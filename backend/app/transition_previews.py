"""Cached, generic FFmpeg previews for the whole transition catalogue.

The editor offers 191 transitions (58 native xfade + 133 ported GL ones) and a
name like "GL · Angular" tells you nothing about what it looks like. Rendering
a real FFmpeg sample for each one while the user scrolls the picker would be
far too slow, so every transition is rendered **exactly once** — between two
tiny synthetic images — and the MP4 is stored on the config volume next to the
database. After the first pass browsing the catalogue costs nothing: the
frontend just streams static files.

Storage layout (all below ``Settings.config_dir``, so it survives container
restarts and is included in the documented backup of /config)::

    <config>/transition-previews/
        manifest.json          status per transition slug
        src/a.png, src/b.png   the two synthetic example frames
        fade.mp4               one clip per transition, named by slug
        gl_cube.mp4

The cache is keyed by a slug of the *friendly* UI label (``"GL · Angular"`` ->
``gl-angular``) rather than by the ffmpeg id, because the frontend only ever
knows labels and the two must never drift apart.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .renderer import (
    Renderer,
    XFADE,
    _registry_candidates,  # shared list of registry/transitions.json locations
    format_ffmpeg_number,
    parse_transition_label,
    xfade_name,
)

log = logging.getLogger(__name__)

# Bump to invalidate every cached clip after a change to the example frames or
# to the encoding settings below.
CACHE_VERSION = 1

# Preview geometry. Deliberately small: 191 clips have to fit on a NAS volume.
WIDTH, HEIGHT, FPS = 640, 360, 25
# Each example frame is held this long; the transition eats most of it so the
# motion is the biggest part of the clip.
HOLD_SECONDS = 1.2
TRANSITION_SECONDS = 0.8
# Safety net for a single xfade encode; GL transitions are cheap but a wedged
# ffmpeg must never hold the build thread forever.
RENDER_TIMEOUT = 90

# The two example frames: cool outgoing, warm incoming, each labelled so a
# wipe/slide/flip is unmistakable even at thumbnail size.
EXAMPLE_A = {"name": "a", "colour": "0x2E5E4E", "label": "A"}
EXAMPLE_B = {"name": "b", "colour": "0xB4552D", "label": "B"}


class PreviewUnavailable(RuntimeError):
    """The transition cannot be previewed (unsupported by this ffmpeg, or it failed."""


def slugify(label: str) -> str:
    """Filesystem-safe, stable key for a transition label."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")
    return slug or "transition"


def _gl_entries() -> list[dict[str, Any]]:
    """GL catalogue straight from the shared registry (id, label, params)."""
    for path in _registry_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = data.get("gl") if isinstance(data, dict) else None
        if not entries:
            continue
        cleaned: list[dict[str, Any]] = []
        for entry in entries:
            transition_id = str(entry.get("id", "")).strip()
            label = str(entry.get("label", "")).strip()
            if not transition_id.startswith("gl_") or not label:
                continue
            params = {
                str(p.get("name")): str(p.get("default"))
                for p in (entry.get("params") or [])
                if p.get("name") is not None
            }
            cleaned.append({"id": transition_id, "label": label, "params": params})
        if cleaned:
            return cleaned
    log.warning("registry/transitions.json not found; GL previews unavailable")
    return []


class TransitionPreviewCache:
    """Renders and caches one short MP4 per transition."""

    def __init__(self, settings: Settings, renderer: Renderer) -> None:
        self.settings = settings
        self.renderer = renderer
        self.root = settings.config_dir / "transition-previews"
        self.src_dir = self.root / "src"
        self.manifest_path = self.root / "manifest.json"
        self._lock = threading.Lock()
        self._slug_locks: dict[str, threading.Lock] = {}
        self._manifest: dict[str, Any] | None = None
        self._build_thread: threading.Thread | None = None
        self._build_stop = threading.Event()
        self._build_progress: dict[str, int] = {"done": 0, "total": 0}

    # ---------------------------------------------------------------- catalogue

    def catalogue(self) -> list[dict[str, Any]]:
        """Every transition the UI offers, in the order the UI lists them."""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for label in XFADE:
            slug = slugify(label)
            if slug in seen:
                continue
            seen.add(slug)
            items.append({"label": label, "slug": slug, "kind": "xfade", "params": {}})
        for entry in _gl_entries():
            slug = slugify(entry["label"])
            if slug in seen:
                continue
            seen.add(slug)
            items.append({"label": entry["label"], "slug": slug, "kind": "gl", "params": entry["params"]})
        return items

    # ----------------------------------------------------------------- manifest

    def _read_manifest(self) -> dict[str, Any]:
        with self._lock:
            if self._manifest is not None:
                return self._manifest
            manifest: dict[str, Any] = {}
            try:
                loaded = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("version") == CACHE_VERSION:
                    manifest = loaded
            except (OSError, ValueError):
                manifest = {}
            manifest.setdefault("version", CACHE_VERSION)
            manifest.setdefault("items", {})
            self._manifest = manifest
            return manifest

    def _write_manifest(self) -> None:
        with self._lock:
            if self._manifest is None:
                return
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.manifest_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._manifest, indent=1, sort_keys=True), encoding="utf-8")
            tmp.replace(self.manifest_path)

    def _record(self, slug: str, status: str, error: str = "", resolved: str = "") -> None:
        manifest = self._read_manifest()
        with self._lock:
            manifest["items"][slug] = {
                "status": status,
                "error": error,
                "resolved": resolved,
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        self._write_manifest()

    # ------------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        manifest = self._read_manifest()
        items = manifest["items"]
        ready = unsupported = failed = 0
        for entry in self.catalogue():
            slug = entry["slug"]
            if (self.root / f"{slug}.mp4").exists():
                ready += 1
                continue
            recorded = str(items.get(slug, {}).get("status") or "")
            if recorded == "unsupported":
                unsupported += 1
            elif recorded == "failed":
                failed += 1
        total = len(self.catalogue())
        building = bool(self._build_thread and self._build_thread.is_alive())
        return {
            "version": CACHE_VERSION,
            "dir": str(self.root),
            "total": total,
            "ready": ready,
            "failed": failed,
            "unsupported": unsupported,
            "pending": max(0, total - ready - failed - unsupported),
            "building": building,
            "buildDone": self._build_progress["done"],
            "buildTotal": self._build_progress["total"],
            "hasFfmpeg": bool(self.renderer.capabilities().get("ffmpeg")),
            "items": {
                entry["slug"]: {
                    "label": entry["label"],
                    "kind": entry["kind"],
                    "status": (
                        "ready" if (self.root / f"{entry['slug']}.mp4").exists()
                        else str(items.get(entry["slug"], {}).get("status") or "pending")
                    ),
                    "error": str(items.get(entry["slug"], {}).get("error") or ""),
                }
                for entry in self.catalogue()
            },
        }

    # --------------------------------------------------------- example sources

    def _font_file(self) -> str | None:
        fonts_dir = self.settings.fonts_dir
        try:
            candidates = sorted(fonts_dir.glob("*.ttf"))
        except OSError:
            return None
        for path in candidates:
            if "Bold" in path.name or "Regular" in path.name:
                return str(path)
        return str(candidates[0]) if candidates else None

    def _render_example(self, spec: dict[str, str]) -> Path:
        """Draw one synthetic example frame (solid colour + a big letter)."""
        target = self.src_dir / f"{spec['name']}.png"
        font = self._font_file()
        filters = [f"scale={WIDTH}:{HEIGHT}"]
        if font:
            escaped = font.replace("\\", "/").replace(":", r"\:")
            filters.append(
                "drawtext=fontfile="
                + escaped.replace("=", r"\=").replace(",", r"\,")
                + f":text={spec['label']}:fontsize=200:fontcolor=0xFFFFFF"
                ":x=(w-text_w)/2:y=(h-text_h)/2"
            )
        command = [
            self.settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={spec['colour']}:s={WIDTH}x{HEIGHT}:d=1",
            "-frames:v", "1", "-vf", ",".join(filters), str(target),
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            # No usable drawtext/font (or no ffmpeg): fall back to a plain
            # colour field. The transition is still perfectly readable because
            # the two examples are strongly contrasting colours.
            log.warning("Could not draw the labelled example frame; using a plain colour for %s", spec["name"])
            command = [
                self.settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c={spec['colour']}:s={WIDTH}x{HEIGHT}:d=1",
                "-frames:v", "1", str(target),
            ]
            subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
        return target

    def ensure_sources(self) -> tuple[Path, Path]:
        self.src_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.src_dir / f".v{CACHE_VERSION}"
        a, b = self.src_dir / "a.png", self.src_dir / "b.png"
        if a.exists() and b.exists() and stamp.exists():
            return a, b
        self._render_example(EXAMPLE_A)
        self._render_example(EXAMPLE_B)
        stamp.touch()
        return a, b

    # ------------------------------------------------------------------ render

    def _lock_for(self, slug: str) -> threading.Lock:
        with self._lock:
            return self._slug_locks.setdefault(slug, threading.Lock())

    def path_for(self, slug: str) -> Path:
        return self.root / f"{slug}.mp4"

    def _render(self, label: str, slug: str, params: dict[str, str]) -> Path:
        a, b = self.ensure_sources()
        self.root.mkdir(parents=True, exist_ok=True)

        # Ask the renderer for the exact filter it would use in a real render,
        # including GL defaults and its stock-ffmpeg fallback logic. If it has
        # to downgrade the transition we mark the preview unsupported instead
        # of storing 133 identical dissolve clips.
        requested = xfade_name(label)
        resolved = self.renderer.resolve_xfade(label)
        if parse_transition_label(resolved)[0] != parse_transition_label(requested)[0]:
            self._record(slug, "unsupported", "Falls back on this FFmpeg build", resolved)
            raise PreviewUnavailable("Falls back on this FFmpeg build")

        xfade = self.renderer.build_transition_xfade(
            {"transition": label, "transitionParams": params, "transitionEasing": "linear", "transitionReverse": 0},
            duration=TRANSITION_SECONDS,
            offset=max(0.01, HOLD_SECONDS - TRANSITION_SECONDS),
        )
        fit = f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},setsar=1"
        graph = f"[0:v]{fit}[a];[1:v]{fit}[b];[a][b]{xfade}[v]"
        target = self.path_for(slug)
        tmp = target.with_suffix(".mp4.part")
        command = [
            self.settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-t", format_ffmpeg_number(HOLD_SECONDS), "-i", str(a),
            "-loop", "1", "-t", format_ffmpeg_number(HOLD_SECONDS), "-i", str(b),
            "-filter_complex", graph, "-map", "[v]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(tmp),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
        except subprocess.TimeoutExpired:
            tmp.unlink(missing_ok=True)
            self._record(slug, "failed", "Preview render timed out")
            raise PreviewUnavailable("Preview render timed out") from None
        if result.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            detail = (result.stderr or "").strip().splitlines()
            reason = detail[-1][:300] if detail else "FFmpeg failed"
            self._record(slug, "failed", reason)
            raise PreviewUnavailable(reason)
        tmp.replace(target)
        self._record(slug, "ready", "", resolved)
        return target

    def ensure(self, label: str, params: dict[str, str] | None = None) -> Path:
        """Return the cached clip for ``label``, rendering it on first use."""
        slug = slugify(label)
        cached = self.path_for(slug)
        if cached.exists():
            return cached
        recorded = self._read_manifest()["items"].get(slug) or {}
        if recorded.get("status") in ("failed", "unsupported"):
            raise PreviewUnavailable(str(recorded.get("error") or "Preview unavailable"))
        entry = next((x for x in self.catalogue() if x["slug"] == slug), None)
        lock = self._lock_for(slug)
        if not lock.acquire(timeout=RENDER_TIMEOUT + 10):
            raise PreviewUnavailable("Another preview is still rendering")
        try:
            if cached.exists():
                return cached
            return self._render(label, slug, (entry or {}).get("params") or (params or {}))
        finally:
            lock.release()

    # --------------------------------------------------------------- build all

    def build_all(self) -> dict[str, Any]:
        """Kick off a background pass over the whole catalogue (idempotent)."""
        if self._build_thread and self._build_thread.is_alive():
            return self.status()
        self._build_stop.clear()

        def _run() -> None:
            pending = [x for x in self.catalogue() if not self.path_for(x["slug"]).exists()]
            self._build_progress = {"done": 0, "total": len(pending)}
            for entry in pending:
                if self._build_stop.is_set():
                    break
                try:
                    self.ensure(entry["label"], entry["params"])
                except PreviewUnavailable:
                    pass
                except Exception:  # noqa: BLE001 - one bad clip must not stop the pass
                    log.exception("Transition preview failed for %s", entry["label"])
                self._build_progress["done"] += 1
            self._build_progress["total"] = self._build_progress["done"]

        self._build_thread = threading.Thread(target=_run, name="transition-previews", daemon=True)
        self._build_thread.start()
        return self.status()

    def stop_build(self) -> None:
        self._build_stop.set()

    def clear(self) -> None:
        self.stop_build()
        for path in self.root.glob("*.mp4"):
            path.unlink(missing_ok=True)
        with self._lock:
            self._manifest = {"version": CACHE_VERSION, "items": {}}
        self._write_manifest()

    def wait_for(self, slug: str, timeout: float = RENDER_TIMEOUT) -> Path | None:
        """Used by tests/CLI: block until a clip is cached (or give up)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cached = self.path_for(slug)
            if cached.exists():
                return cached
            time.sleep(0.2)
        return None
