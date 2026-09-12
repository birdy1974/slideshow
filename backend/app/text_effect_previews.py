"""Cached, real FFmpeg examples for the dynamic text-effect catalogue.

Exactly the pattern of ``transition_previews.py``: a name like "Decrypted
scramble" tells you nothing about what it looks like, so every effect is
rendered **once** — the actual engine the renderer uses (animated drawtext or
libass, per its registry entry) onto a synthetic title card — and the MP4 is
stored on the config volume next to the database. The GUI then streams static
files.

Storage layout (all below ``Settings.config_dir``)::

    <config>/text-effect-previews/
        manifest.json          status per effect slug
        <slug>.mp4             one clip per effect (enter/while/exit slot)

The slug is a slug of the friendly label ("Split fade · chars" ->
``split-fade-chars``), the same rule the frontend applies.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .renderer import Renderer, format_ffmpeg_number
from .text_effects import build_text_overlay, effect_for, overlay_plan
from .transition_previews import PreviewUnavailable, slugify

log = logging.getLogger(__name__)

CACHE_VERSION = 1

# Example geometry: small like the transition previews (65 clips must fit on a
# NAS volume), large enough to read 40 px text.
WIDTH, HEIGHT, FPS = 640, 360, 25
DURATION = 3.0
RENDER_TIMEOUT = 60

EXAMPLE_TEXT = "Summer, slowly.\nEvery word matters"
EXAMPLE_BACKGROUND = "0x242B21"   # matches the app's #242B21 title-card green
CARD_FONT_SIZE = 44               # in 640x360 playres
CARD_TEXT_X, CARD_TEXT_Y = 50.0, 50.0


class TextEffectPreviewCache:
    """Renders and caches one short MP4 per text effect."""

    def __init__(self, settings: Settings, renderer: Renderer) -> None:
        self.settings = settings
        self.renderer = renderer
        self.root = settings.config_dir / "text-effect-previews"
        self.manifest_path = self.root / "manifest.json"
        self._lock = threading.Lock()
        self._slug_locks: dict[str, threading.Lock] = {}
        self._manifest: dict[str, Any] | None = None
        self._build_thread: threading.Thread | None = None
        self._build_stop = threading.Event()
        self._build_progress: dict[str, int] = {"done": 0, "total": 0}

    # ---------------------------------------------------------------- catalogue

    def catalogue(self) -> list[dict[str, Any]]:
        """Every effect the UI offers, in registry order."""
        from .text_effects import text_effect_catalog
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in text_effect_catalog():
            slug = slugify(str(entry["label"]))
            if slug in seen:
                continue
            seen.add(slug)
            items.append({
                "label": str(entry["label"]),
                "slug": slug,
                "slot": str(entry["slot"]),
                "kind": str(entry.get("engine", "ass")),
                "params": {str(p.get("name")): str(p.get("default")) for p in (entry.get("params") or []) if p.get("name")},
            })
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

    def _record(self, slug: str, status: str, error: str = "") -> None:
        manifest = self._read_manifest()
        with self._lock:
            manifest["items"][slug] = {
                "status": status,
                "error": error,
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        self._write_manifest()

    # ------------------------------------------------------------------- status

    def status(self) -> dict[str, Any]:
        manifest = self._read_manifest()
        items = manifest["items"]
        ready = failed = 0
        for entry in self.catalogue():
            slug = entry["slug"]
            if (self.root / f"{slug}.mp4").exists():
                ready += 1
            elif str(items.get(slug, {}).get("status") or "") == "failed":
                failed += 1
        total = len(self.catalogue())
        building = bool(self._build_thread and self._build_thread.is_alive())
        return {
            "version": CACHE_VERSION,
            "dir": str(self.root),
            "total": total,
            "ready": ready,
            "failed": failed,
            "pending": max(0, total - ready - failed),
            "building": building,
            "buildDone": self._build_progress["done"],
            "buildTotal": self._build_progress["total"],
            "hasFfmpeg": bool(self.renderer.capabilities().get("ffmpeg")),
            "items": {
                entry["slug"]: {
                    "label": entry["label"],
                    "slot": entry["slot"],
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

    # ------------------------------------------------------------------ render

    def _lock_for(self, slug: str) -> threading.Lock:
        with self._lock:
            return self._slug_locks.setdefault(slug, threading.Lock())

    def path_for(self, slug: str) -> Path:
        return self.root / f"{slug}.mp4"

    def _item_for(self, entry: dict[str, Any]) -> dict[str, Any]:
        """A synthetic title-frame item that shows exactly this effect."""
        slot, label = entry["slot"], entry["label"]
        params = entry.get("params") or {}
        speed = 1.6
        item: dict[str, Any] = {
            "type": "title",
            "text": EXAMPLE_TEXT,
            "duration": DURATION,
            "textStart": 0.0,
            "textEnd": DURATION,
            "textX": CARD_TEXT_X,
            "textY": CARD_TEXT_Y,
            "fontSize": CARD_FONT_SIZE * (1920 / WIDTH),  # renderer scales by width/1920
            "fontColor": "#F4F6F0",
            "fontFamily": "Montserrat",
            "textBold": True,
            "textItalic": False,
            "textFxWhileSpeed": speed,
        }
        enter_default = effect_for("Fade", "enter")
        exit_default = effect_for("Fade out", "exit")
        while_default = effect_for("None (static)", "while")
        if slot == "enter":
            item["textFxEnter"] = label
            item["textEnterDuration"] = 1.0
            item["textFxExit"] = exit_default.get("label")
            item["textExitDuration"] = 0.4
            item["textFxWhile"] = while_default.get("label")
        elif slot == "while":
            item["textFxEnter"] = enter_default.get("label")
            item["textEnterDuration"] = 0.3
            item["textFxExit"] = exit_default.get("label")
            item["textExitDuration"] = 0.4
            item["textFxWhile"] = label
            item["textFxWhileSpeed"] = float(entry["params"].get("speed", 1.6)) if entry["params"].get("speed") else 1.6
        else:
            item["textFxEnter"] = enter_default.get("label")
            item["textEnterDuration"] = 0.3
            item["textFxExit"] = label
            item["textExitDuration"] = 0.9
            item["textFxWhile"] = while_default.get("label")
        if params:
            item["textFxParams"] = params
        # Count up reads nicer with a year range on the example card.
        if str(entry["label"]) == "Count up":
            item["textFxParams"] = {"from": "0", "to": "100"}
        return item

    def _render(self, entry: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.renderer.capabilities().get("ffmpeg"):
            raise PreviewUnavailable("FFmpeg is not installed")
        item = self._item_for(entry)
        plan = overlay_plan(item)
        if plan is None:  # pragma: no cover - the example text is a constant
            raise PreviewUnavailable("No example text")
        # The overlay engine writes the .ass document itself; give it the job
        # file layout used by real renders.
        ass_path = self.root / f"{entry['slug']}.ass"
        overlay = build_text_overlay(
            item, {}, WIDTH, HEIGHT, self.settings.fonts_dir, ass_path, self.renderer_font,
            force_ass=self.force_ass(),
        )
        if not overlay:
            raise PreviewUnavailable("Could not build the text overlay")
        target = self.path_for(entry["slug"])
        tmp = target.with_suffix(".mp4.part")
        command = [
            self.settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"color=c={EXAMPLE_BACKGROUND}:s={WIDTH}x{HEIGHT}:r={FPS}:d={format_ffmpeg_number(DURATION)}",
            "-vf", f"{overlay},format=yuv420p",
            "-t", format_ffmpeg_number(DURATION),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            "-f", "mp4", str(tmp),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
        except subprocess.TimeoutExpired:
            tmp.unlink(missing_ok=True)
            self._record(entry["slug"], "failed", "Preview render timed out")
            raise PreviewUnavailable("Preview render timed out") from None
        finally:
            ass_path.unlink(missing_ok=True)
        if result.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            detail = (result.stderr or "").strip().splitlines()
            reason = detail[-1][:300] if detail else "FFmpeg failed"
            self._record(entry["slug"], "failed", reason)
            raise PreviewUnavailable(reason)
        tmp.replace(target)
        self._record(entry["slug"], "ready")
        return target

    def force_ass(self) -> bool:
        """Mirror the renderer: no drawtext in this FFmpeg → examples via libass."""
        try:
            return self.renderer.ass_filter_supported() and not self.renderer.drawtext_filter_supported()
        except Exception:
            return False

    def renderer_font(self, family: str, bold: bool, italic: bool, fonts_dir: Path) -> str:
        from .renderer import font_file
        return font_file(family, bold, italic, fonts_dir)

    def ensure(self, label: str) -> Path:
        """Return the cached clip for ``label``, rendering it on first use."""
        slug = slugify(label)
        cached = self.path_for(slug)
        if cached.exists():
            return cached
        recorded = self._read_manifest()["items"].get(slug) or {}
        if recorded.get("status") == "failed":
            raise PreviewUnavailable(str(recorded.get("error") or "Preview unavailable"))
        entry = next((x for x in self.catalogue() if x["slug"] == slug), None)
        if not entry:
            raise PreviewUnavailable("Unknown text effect")
        lock = self._lock_for(slug)
        if not lock.acquire(timeout=RENDER_TIMEOUT + 10):
            raise PreviewUnavailable("Another preview is still rendering")
        try:
            if cached.exists():
                return cached
            return self._render(entry)
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
                    self.ensure(entry["label"])
                except PreviewUnavailable:
                    pass
                except Exception:  # noqa: BLE001 - one bad clip must not stop the pass
                    log.exception("Text-effect preview failed for %s", entry["label"])
                self._build_progress["done"] += 1
            self._build_progress["total"] = self._build_progress["done"]

        self._build_thread = threading.Thread(target=_run, name="text-effect-previews", daemon=True)
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
