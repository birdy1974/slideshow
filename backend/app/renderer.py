"""FFmpeg slideshow renderer.

The renderer deliberately normalizes every source and xfade result to an
identical frame rate, time base and timestamp origin before chaining the next
transition. This avoids xfade failures with mixed media and FFmpeg builds that
lose frame-rate metadata on intermediate filter outputs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .database import Database, utcnow
from .media import UnsafePath, mounted_path, source_path

log = logging.getLogger(__name__)

KEN_BURNS_MAX_ZOOM = 1.12
# A single filter_complex of ~90 xfades at 1080p OOMs or appears hung on NAS
# boxes (every xfade keeps both inputs decoded). Compose in small groups.
COMPOSE_BATCH_SIZE = 8
RESOLUTIONS = {
    "4K UHD · 2160p": (3840, 2160), "Full HD · 1080p": (1920, 1080),
    "HD · 720p": (1280, 720), "SD · 480p": (854, 480),
}
XFADE = {
    "Fade":"fade", "Fade black":"fadeblack", "Fade white":"fadewhite", "Fade grays":"fadegrays", "Fade fast":"fadefast", "Fade slow":"fadeslow",
    "Dissolve":"dissolve", "Distance":"distance", "Pixelize":"pixelize", "H blur":"hblur",
    "Wipe left":"wipeleft", "Wipe right":"wiperight", "Wipe up":"wipeup", "Wipe down":"wipedown", "Wipe top-left":"wipetl", "Wipe top-right":"wipetr", "Wipe bottom-left":"wipebl", "Wipe bottom-right":"wipebr",
    "Slide left":"slideleft", "Slide right":"slideright", "Slide up":"slideup", "Slide down":"slidedown", "Smooth left":"smoothleft", "Smooth right":"smoothright", "Smooth up":"smoothup", "Smooth down":"smoothdown",
    "Circle crop":"circlecrop", "Rectangle crop":"rectcrop", "Circle open":"circleopen", "Circle close":"circleclose", "Vertical open":"vertopen", "Vertical close":"vertclose", "Horizontal open":"horzopen", "Horizontal close":"horzclose", "Radial":"radial",
    "Diagonal top-left":"diagtl", "Diagonal top-right":"diagtr", "Diagonal bottom-left":"diagbl", "Diagonal bottom-right":"diagbr", "Horizontal left slice":"hlslice", "Horizontal right slice":"hrslice", "Vertical up slice":"vuslice", "Vertical down slice":"vdslice",
    "Squeeze horizontal":"squeezeh", "Squeeze vertical":"squeezev", "Zoom in":"zoomin", "Horizontal left wind":"hlwind", "Horizontal right wind":"hrwind", "Vertical up wind":"vuwind", "Vertical down wind":"vdwind",
    "Cover left":"coverleft", "Cover right":"coverright", "Cover up":"coverup", "Cover down":"coverdown", "Reveal left":"revealleft", "Reveal right":"revealright", "Reveal up":"revealup", "Reveal down":"revealdown",
}


def parse_number(label: str, fallback: float) -> float:
    match = re.search(r"([\d.]+)", label or "")
    return float(match.group(1)) if match else fallback


def ff_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%").replace("[", r"\[").replace("]", r"\]")


def source_path(settings: Settings, item: dict[str, Any]) -> Path:
    """Resolve a media/soundtrack item to a file on a mounted root.

    The UI used to store `path` as the parent folder and `name` as the
    filename. Newer snapshots store the full file path in `path`. Both work.
    Folder names that contain a dot (e.g. ``holiday.2024``) must not be treated
    as files just because ``Path.suffix`` is non-empty — if `path` is a
    directory, `name` is always joined.
    """
    path = str(item.get("path", "") or "").replace("\\", "/")
    name = str(item.get("name", "") or "")
    filename = Path(name).name
    if filename and Path(path.rstrip("/")).name == filename:
        return mounted_path(settings, path)
    base = mounted_path(settings, path)
    if not filename or base.is_file():
        return base
    return mounted_path(settings, path, filename)


def xfade_name(label: str) -> str:
    # GLSL shaders are not portable on the DS918+ software path; use a safe
    # dissolve fallback while retaining the exact requested value in SQLite.
    return XFADE.get(label, "dissolve" if label.startswith("GLSL") else "fade")


def _parse_xfade_help(text: str) -> set[str]:
    """Transition constant names from `ffmpeg -h filter=xfade` output."""
    block = re.search(r"^\s*transition\s+<int>[^\n]*\n(.*?)^\s*duration\s+<duration>", text, re.S | re.M)
    if not block:
        return set()
    return {name for name in re.findall(r"^\s+(\w+)\s+-?\d+\s+\.\.", block.group(1), re.M) if name != "custom"}


def probe_xfade_transitions(ffmpeg_bin: str) -> set[str]:
    """Transitions the installed FFmpeg build actually supports.

    Many NAS packages ship FFmpeg 5.x, whose xfade lacks the wind/cover/reveal
    catalogue. Returns an empty set when detection is impossible.
    """
    try:
        result = subprocess.run([ffmpeg_bin, "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True, timeout=10)
    except Exception:
        return set()
    return _parse_xfade_help(f"{result.stdout or ''}\n{result.stderr or ''}")


class RenderError(RuntimeError):
    pass


class OutputExistsError(RuntimeError):
    """A render target already exists and the user has not acknowledged overwriting it."""


_FFMPEG_ERROR_RE = re.compile(
    r"(error|invalid|failed|no such file|permission denied|does not contain any|"
    r"unrecognized|not found|could not|unable to|no such filter|unknown encoder|conversion failed)",
    re.I,
)


def _short_label(name: str, limit: int = 42) -> str:
    name = name.strip() or "unnamed"
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _ui_path(item: dict[str, Any]) -> str:
    """Project-facing path (`/photos/...`) rather than the host mount path."""
    path = str(item.get("path", "")).replace("\\", "/")
    name = str(item.get("name", ""))
    if name and Path(path.rstrip("/")).name == name:
        return path
    if path and name:
        return f"{path.rstrip('/')}/{name}"
    return path or name


def _probe_reason_from_ffprobe(output: str, path: Path) -> str:
    """Pick a short, human-readable reason out of ffprobe's stderr/stdout."""
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        for prefix in (f"{path}: ", f"{path.name}: "):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        match = re.search(r"Error opening input:\s*(.+)$", line)
        if match:
            return match.group(1).strip()
        return line
    return "unreadable media"


def _probe_readable(path: Path, ffprobe_bin: str, timeout: float = 30.0, retries: int = 2, retry_delay: float = 0.75) -> str | None:
    """Return a short reason if `path` cannot be read as media, else None.

    Missing and empty (0-byte) files are rejected without spawning ffprobe.
    Cloud-synced mounts (Synology on-demand sync, etc.) can briefly report a
    0-byte placeholder while the real content hydrates, so an empty file is
    re-stat'ed a few times before being declared unreadable.  If ffprobe is
    not installed the content probe is skipped so a render can still start;
    FFmpeg remains the final judge.
    """
    if not path.exists():
        return "file is missing"
    if path.is_dir():
        return "is a folder, not a media file"
    if not path.is_file():
        return "not a file"
    for attempt in range(retries + 1):
        try:
            size = path.stat().st_size
        except OSError as exc:
            return f"cannot stat file ({exc})"
        if size != 0:
            break
        if attempt < retries:
            time.sleep(retry_delay)
    else:
        return "file is empty (0 bytes)"
    probe = shutil.which(ffprobe_bin)
    if not probe:
        return None
    try:
        result = subprocess.run(
            [probe, "-hide_banner", "-v", "error",
             "-show_entries", "format=format_name",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # A slow/networked volume can exceed even a generous timeout while
        # the disk spins up. Give the probe one more chance before failing.
        try:
            result = subprocess.run(
                [probe, "-hide_banner", "-v", "error",
                 "-show_entries", "format=format_name",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return "ffprobe timed out"
        except OSError as exc:
            return f"ffprobe could not start ({exc})"
    except OSError as exc:
        return f"ffprobe could not start ({exc})"
    if result.returncode == 0:
        return None
    return _probe_reason_from_ffprobe(f"{result.stderr or ''}\n{result.stdout or ''}", path)


def _summarize_ffmpeg_log(text: str, *, max_lines: int = 8) -> str:
    """Return the actionable FFmpeg error lines instead of a raw 5k-char tail."""
    tail = text[-12_000:] if len(text) > 12_000 else text
    picked: list[str] = []
    seen: set[str] = set()
    for raw in tail.splitlines():
        line = raw.strip()
        if not line or line.startswith("$ "):
            continue
        if not _FFMPEG_ERROR_RE.search(line):
            continue
        key = line[:200]
        if key in seen:
            continue
        seen.add(key)
        picked.append(line if len(line) <= 240 else line[:237] + "...")
        if len(picked) >= max_lines:
            break
    if picked:
        return "\n".join(picked)
    fallback = [ln.strip() for ln in tail.splitlines() if ln.strip() and not ln.startswith("$ ")]
    return "\n".join(fallback[-6:]) if fallback else "No FFmpeg error details were captured."


def format_ffmpeg_number(value: float) -> str:
    """Stable FFmpeg numeric literal — strips binary float noise (4.999999 → 5)."""
    return f"{round(float(value), 6):g}"


def _even(value: float) -> int:
    """Nearest even pixel count — libx264/yuv420p rejects odd dimensions."""
    return max(2, int(value) // 2 * 2)


def fill_frame_filter(width: int, height: int, fps: int) -> str:
    """Cover the frame, cropping the overflowing edges (videos, title cards)."""
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}"


def fit_frame_filter(width: int, height: int, fps: int, zoom_headroom: float = 1.0) -> str:
    """Show the *whole* picture, letterboxed over a blurred copy of itself.

    `scale=...:force_original_aspect_ratio=decrease` never crops, and the bars
    it leaves are filled by the same image scaled to cover, blurred and dimmed.
    `zoom_headroom` shrinks the visible picture by that factor so a later
    zoompan (Ken Burns) can zoom in that far without cutting anything off.
    """
    inner_w, inner_h = _even(width / zoom_headroom), _even(height / zoom_headroom)
    # Blur on a downscaled copy: visually identical after upscaling, far cheaper.
    blur_w, blur_h = _even(max(32, width / 8)), _even(max(32, height / 8))
    sigma = format_ffmpeg_number(max(3.0, blur_w / 32))
    return (
        "split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={blur_w}:{blur_h}:force_original_aspect_ratio=increase,crop={blur_w}:{blur_h},"
        f"gblur=sigma={sigma},scale={width}:{height},eq=brightness=-0.12:saturation=1.2,setsar=1[bgblur];"
        f"[fgsrc]scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease,setsar=1[fgfit];"
        f"[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}"
    )


def build_filter_graph(durations: list[float], transitions: list[float], xfade_names: list[str], fps: int | None = None) -> str:
    """Compose transitions after each clip, rather than subtracting them.

    A clip's configured duration is its visible hold time.  Each transition is
    additional timeline time, so four 5-second clips with three 3-second
    transitions produces 29 seconds.  The caller supplies normalized segment
    files with lead-in/lead-out handles for xfade; offsets are calculated from
    the user-facing (hold) durations and the preceding transitions.

    ``fps`` is repeated before every xfade and after every xfade result as a
    CFR guard.  ``setpts=PTS-STARTPTS`` discards frame-rate metadata (FFmpeg
    6+/7+ then reports the link as 1/0), so ``fps`` must come *after* it —
    the last filter before each xfade — to reimpose a constant rate.  Placing
    it first lets ``setpts`` clobber it and every following xfade fails.
    """
    if not durations:
        raise ValueError("build_filter_graph requires at least one clip")
    normalize = f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps}" if fps else "settb=AVTB,setpts=PTS-STARTPTS"
    if len(durations) == 1:
        return f"[0:v]{normalize}[vout]"
    expected = len(durations) - 1
    if len(transitions) != expected or len(xfade_names) != expected:
        raise ValueError("transitions and xfade names must cover every clip pair")
    prepared = [f"[{index}:v]{normalize}[s{index}]" for index in range(len(durations))]
    chains: list[str] = []
    # xfade's offset is measured on its first input.  At each boundary the
    # prior clip has held for its configured duration and all earlier
    # transitions have completed.
    offset = durations[0]
    previous = "[s0]"
    for index in range(1, len(durations)):
        transition = transitions[index - 1]
        out = f"[x{index}]" if index < len(durations) - 1 else "[vout]"
        chains.append(
            f"{previous}[s{index}]xfade=transition={xfade_names[index - 1]}"
            f":duration={format_ffmpeg_number(transition)}"
            f":offset={format_ffmpeg_number(offset)},{normalize}{out}"
        )
        previous = out
        offset += durations[index] + transition
    return ";".join(prepared + chains)


def chunk_indices(count: int, batch: int) -> list[tuple[int, int]]:
    """Inclusive-exclusive [start, end) windows covering 0..count."""
    if count <= 0:
        return []
    batch = max(2, batch)
    if count <= batch:
        return [(0, count)]
    windows: list[tuple[int, int]] = []
    start = 0
    while start < count:
        remaining = count - start
        # Avoid a leftover singleton: fold the last two groups if needed.
        take = remaining if remaining <= batch else batch
        if remaining > batch and remaining - batch == 1:
            take = remaining - 2
        windows.append((start, start + take))
        start += take
    return windows


class Renderer:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings
        self.pool = ThreadPoolExecutor(max_workers=settings.render_workers, thread_name_prefix="render")
        self.cancel_events: dict[str, threading.Event] = {}
        self._xfade_supported: set[str] | None = None
        self._xfade_lock = threading.Lock()
        self._qsv_encodable: bool | None = None
        self._qsv_lock = threading.Lock()
        self._ffmpeg_version: str | None = None
        self._version_probed = False
        self._version_lock = threading.Lock()

    def warm_capabilities(self) -> None:
        """Probe ffmpeg version, the xfade catalogue and Quick Sync once, in a
        background thread, right after startup.

        Container health checks poll this app every 30 s. They used to hit
        /api/health, which ran ``ffmpeg -version`` on every call and could
        queue behind the one-time Quick Sync test encode (up to 30 s under a
        lock) — so a busy NAS blew the probe's time budget three times in a
        row and Docker/Portainer flagged a perfectly working container as
        unhealthy. Warming the caches up front keeps every later capabilities
        read subprocess-free and instant.
        """
        def _warm() -> None:
            try:
                self.ffmpeg_version()
                self.xfade_supported()
                self.qsv_encodable()
            except Exception:
                log.exception("Capability warm-up failed; capabilities will re-probe lazily")
        threading.Thread(target=_warm, name="capability-warmup", daemon=True).start()

    def _probe_ffmpeg_version(self) -> None:
        with self._version_lock:
            if self._version_probed:
                return
            self._version_probed = True
            path = shutil.which(self.settings.ffmpeg_bin)
            if not path:
                return
            try:
                self._ffmpeg_version = subprocess.run(
                    [path, "-version"], capture_output=True, text=True, timeout=3
                ).stdout.splitlines()[0]
            except Exception:
                self._ffmpeg_version = None

    def ffmpeg_version(self) -> str | None:
        """First line of ``ffmpeg -version``, probed at most once per process."""
        if not self._version_probed:
            self._probe_ffmpeg_version()
        return self._ffmpeg_version

    def xfade_supported(self) -> set[str]:
        """Lazily probe (once) which xfade transitions this FFmpeg build has."""
        with self._xfade_lock:
            if self._xfade_supported is None:
                self._xfade_supported = probe_xfade_transitions(self.settings.ffmpeg_bin)
        return self._xfade_supported

    def resolve_xfade(self, label: str) -> str:
        """Map a UI transition to one this FFmpeg can run, degrading safely.

        Older FFmpeg builds (e.g. 5.x on the DS918+) lack wind/cover/reveal
        constants; rather than failing the whole render, fall back to dissolve.
        An empty probe result means detection failed: keep the mapped name.
        """
        name = xfade_name(label)
        supported = self.xfade_supported()
        if not supported or name in supported:
            return name
        fallback = "dissolve" if "dissolve" in supported else sorted(supported)[0]
        log.warning("Transition '%s' (%s) is not supported by this FFmpeg build; falling back to '%s'", label, name, fallback)
        return fallback

    def capabilities(self) -> dict[str, Any]:
        # Feeds /api/health, so it must answer instantly even mid-render: the
        # version comes from the once-per-process cache and Quick Sync from a
        # non-blocking read of the background probe's result.
        ffmpeg = shutil.which(self.settings.ffmpeg_bin)
        return {"ffmpeg": bool(ffmpeg), "ffmpegVersion": self.ffmpeg_version(), "quickSync": self.qsv_encodable_cached(), "cpuEncoding": bool(ffmpeg)}

    def qsv_encodable_cached(self) -> bool:
        """Non-blocking view of the Quick Sync probe (False until it finishes).

        Meant for status reporting. Render-time encoder selection keeps using
        the blocking qsv_encodable() so "Auto" always decides on the verified
        answer instead of a probe that is still running.
        """
        return bool(self._qsv_encodable)

    def qsv_encodable(self) -> bool:
        """Whether h264_qsv actually encodes on this host, verified by a probe.

        Merely having /dev/dri/renderD128 is not enough: runtimes differ in the
        rate control modes, pixel formats and resolutions they accept (the
        DS918+ in particular). A tiny test encode mirrors the renderer's
        bitrate-based settings, so "Auto" can pick the working encoder up front.
        """
        with self._qsv_lock:
            if self._qsv_encodable is None:
                self._qsv_encodable = self._probe_qsv()
        return self._qsv_encodable

    def _probe_qsv(self) -> bool:
        if not Path("/dev/dri/renderD128").exists():
            return False
        try:
            result = subprocess.run(
                [self.settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25",
                 "-frames:v", "12", "-c:v", "h264_qsv", "-b:v", "1M", "-maxrate", "1M", "-bufsize", "2M",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def effective_transitions(media: list[dict[str, Any]]) -> list[float]:
        """Transition durations in timeline order.

        Transitions are additional time between clips, not an overlap deducted
        from either configured clip duration.  A small lower bound keeps xfade
        valid while allowing a long transition for a short still image.
        """
        return [max(.05, float(item.get("transitionTime", 5))) for item in media[:-1]]

    def _probe_readable(self, path: Path) -> str | None:
        """Check a single file with ffprobe; see module-level `_probe_readable`."""
        return _probe_readable(
            path,
            self.settings.ffprobe_bin,
            timeout=self.settings.ffprobe_timeout,
            retries=self.settings.media_probe_retries,
            retry_delay=self.settings.media_probe_retry_delay,
        )

    def _validate_media(self, project: dict[str, Any]) -> None:
        """Fail fast with one actionable message naming every unreadable input.

        Runs before any encoding so a 0-byte JPEG or a corrupt MP3 does not
        surface as a mid-render crash dumping thousands of FFmpeg log characters.
        Title frames have no file and are skipped.
        """
        problems: list[str] = []
        for item in project.get("media", []):
            if item.get("type") == "title":
                continue
            label = _short_label(str(item.get("name") or "media"))
            try:
                path = source_path(self.settings, item)
            except UnsafePath as exc:
                problems.append(f"{label} — invalid path ({exc})\n    {_ui_path(item)}")
                continue
            reason = self._probe_readable(path)
            if reason:
                problems.append(f"{label} — {reason}\n    {_ui_path(item) or path}")
        for track in project.get("soundtrack", {}).get("tracks", []):
            label = _short_label(str(track.get("name") or "track"))
            try:
                path = source_path(self.settings, track)
            except UnsafePath as exc:
                problems.append(f"soundtrack '{label}' — invalid path ({exc})\n    {_ui_path(track)}")
                continue
            reason = self._probe_readable(path)
            if reason:
                problems.append(f"soundtrack '{label}' — {reason}\n    {_ui_path(track) or path}")
        if not problems:
            return
        listed = "\n\n".join(f"  {line}" for line in problems)
        raise RenderError(
            "Cannot render — these media files could not be read:\n\n"
            f"{listed}\n\n"
            "Remove or replace them, then try again."
        )

    def render_output_path(self, project: dict[str, Any]) -> Path:
        """Destination MP4 for a final render, shared by submit and render."""
        output_settings = project.get("output", {})
        target_dir = mounted_path(self.settings, str(output_settings.get("path", "/output")))
        stem = Path(str(output_settings.get("filename", "slideshow"))).stem or "slideshow"
        return target_dir / f"{stem}.mp4"

    def render_output_ui_path(self, project: dict[str, Any]) -> str:
        """Project-facing destination (`/output/movie.mp4`) for user messages.

        The host mount path may be a NAS volume like `/volume1/output`; the UI
        always talks in terms of the `/output` mount, so the overwrite prompt
        should echo the path the user actually typed, not the host path.
        """
        output_settings = project.get("output", {})
        folder = str(output_settings.get("path", "/output")).replace("\\", "/").rstrip("/") or "/output"
        stem = Path(str(output_settings.get("filename", "slideshow"))).stem or "slideshow"
        return f"{folder}/{stem}.mp4"

    def submit(self, project_id: int, kind: str, overwrite: bool = False) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        if kind == "render":
            output = self.render_output_path(project)
            if output.exists() and not overwrite:
                raise OutputExistsError(self.render_output_ui_path(project))
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "project_id": project_id, "kind": kind, "settings": project.get("output", {})}
        self.db.create_job(job)
        event = threading.Event(); self.cancel_events[job_id] = event
        self.pool.submit(self._run, job_id, project, kind, event)
        return self.db.get_job(job_id) or job

    def cancel(self, job_id: str) -> bool:
        event = self.cancel_events.get(job_id)
        if not event:
            return False
        event.set(); self.db.update_job(job_id, status="cancelling", stage="Stopping FFmpeg")
        return True

    def _run(self, job_id: str, project: dict[str, Any], kind: str, cancelled: threading.Event) -> None:
        work = self.settings.work_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        self.db.update_job(job_id, status="running", stage="Validating media", started_at=utcnow())
        try:
            if not shutil.which(self.settings.ffmpeg_bin):
                raise RenderError("FFmpeg is not installed or is not available on PATH")
            output = self.render(project, kind, work, cancelled, lambda p,s: self.db.update_job(job_id, progress=p, stage=s))
            self.db.update_job(job_id, status="complete", progress=100, stage="Complete", output_path=str(output), finished_at=utcnow())
        except Exception as exc:
            status = "cancelled" if cancelled.is_set() else "failed"
            self.db.update_job(job_id, status=status, stage="Cancelled" if cancelled.is_set() else "Failed", error_message=str(exc), finished_at=utcnow())
            log.exception("Render job %s failed", job_id)
        finally:
            self.cancel_events.pop(job_id, None)

    def _run_ffmpeg(self, command: list[str], cancelled: threading.Event, log_file: Path) -> None:
        with log_file.open("a", encoding="utf-8") as logs:
            logs.write("\n$ " + " ".join(command) + "\n")
            process = subprocess.Popen(command, stdout=logs, stderr=subprocess.STDOUT, text=True)
            while process.poll() is None:
                if cancelled.wait(.2):
                    process.terminate()
                    try: process.wait(5)
                    except subprocess.TimeoutExpired: process.kill()
                    raise RenderError("Render cancelled by user")
            if process.returncode:
                text = log_file.read_text(encoding="utf-8", errors="replace")
                summary = _summarize_ffmpeg_log(text)
                raise RenderError(f"FFmpeg exited with status {process.returncode}.\n{summary}")

    def _text_filter(self, item: dict[str, Any], defaults: dict[str, Any], width: int, height: int) -> str | None:
        text = str(item.get("text", "")).strip()
        if not text:
            return None
        start, end = float(item.get("textStart", 0)), float(item.get("textEnd", item.get("duration", 5)))
        fade_in = max(.01, float(item.get("textEnterDuration", .5))); fade_out = max(.01, float(item.get("textExitDuration", .5)))
        x, y = float(item.get("textX", 50)), float(item.get("textY", 72))
        # Title frames carry their own type settings. Picture captions use the
        # project-wide defaults so changing “Default text style” never restyles
        # a standalone text card.
        if item.get("type") == "title":
            size_pt = item.get("fontSize", 48)
            colour_raw = item.get("fontColor") or "#ffffff"
            bold = item.get("textBold", True)
            italic = item.get("textItalic", False)
        else:
            size_pt = defaults.get("fontSize", 48)
            colour_raw = defaults.get("fontColor", "#ffffff")
            bold = defaults.get("bold", True)
            italic = defaults.get("italic", False)
        size = max(8, int(float(size_pt) * width / 1920))
        colour = str(colour_raw).replace("#", "0x")
        font_dir = "/usr/share/fonts/truetype/dejavu"
        if bold and italic:
            font = f"{font_dir}/DejaVuSans-BoldOblique.ttf"
        elif italic:
            font = f"{font_dir}/DejaVuSans-Oblique.ttf"
        elif bold:
            font = f"{font_dir}/DejaVuSans-Bold.ttf"
        else:
            font = f"{font_dir}/DejaVuSans.ttf"
        alpha = f"if(lt(t,{start}),0,if(lt(t,{start+fade_in}),(t-{start})/{fade_in},if(lt(t,{end-fade_out}),1,if(lt(t,{end}),({end}-t)/{fade_out},0))))"
        return f"drawtext=fontfile='{font}':text='{ff_escape(text)}':fontsize={size}:fontcolor={colour}:alpha='{alpha}':x=(w-text_w)*{x/100}:y=(h-text_h)*{y/100}:shadowcolor=black@0.55:shadowx=2:shadowy=2:enable='between(t,{start},{end})'"

    def render(self, project: dict[str, Any], kind: str, work: Path, cancelled: threading.Event, progress: Callable[[float,str],None]) -> Path:
        media = list(project.get("media", []))
        if project.get("project", {}).get("randomOrder"):
            import random
            random.shuffle(media)
        if not media:
            raise RenderError("The project contains no media")
        self._validate_media({**project, "media": media})
        output_settings = project.get("output", {})
        if kind == "preview":
            width, height, fps, bitrate = 854, 480, 24, "2M"
        else:
            width, height = RESOLUTIONS.get(output_settings.get("resolution"), (1920, 1080))
            fps = int(parse_number(output_settings.get("frameRate", "30"), 30))
            bitrate = f"{parse_number(output_settings.get('bitrate', '8'), 8):g}M"
        defaults = project.get("textDefaults", {})
        log_file = work / "ffmpeg.log"
        progress(1, "Preparing soundtrack")
        soundtrack = self._make_soundtrack(project, work, cancelled, log_file)
        if soundtrack and project.get("soundtrack",{}).get("policy") == "Fit slideshow to audio":
            audio_duration = self._probe_duration(soundtrack)
            transition_total = sum(self.effective_transitions(media))
            duration_total = sum(max(.2, float(x.get("duration",5))) for x in media)
            if audio_duration > transition_total and duration_total > 0:
                # Holds scale to fill the audio; transitions retain their set duration.
                # Videos are excluded from scaling so a full movie is never shortened.
                factor = (audio_duration - transition_total) / duration_total
                scaled: list[dict[str, Any]] = []
                for item in media:
                    if item.get("type") == "video":
                        scaled.append(item)
                        continue
                    hold = max(.2, float(item.get("duration", 5)) * factor)
                    text_end = min(float(item.get("textEnd", item.get("duration", 5))) * factor, hold)
                    scaled.append({**item, "duration": hold, "textEnd": text_end})
                media = scaled
        segments: list[Path] = []
        transitions = self.effective_transitions(media)
        # Resolve hold durations. Videos always play through to the end of the
        # source file — the configured duration is only a floor, never a ceiling
        # that would cut the movie short before the next transition.
        durations: list[float] = []
        native_video_durations: list[float | None] = []
        for item in media:
            hold = max(.2, float(item.get("duration", 5)))
            native: float | None = None
            if item.get("type") == "video":
                try:
                    native = self._probe_duration(source_path(self.settings, item))
                except Exception as exc:
                    log.warning("Could not probe video duration for %s: %s", item.get("name"), exc)
                    native = None
                if native is not None and native > 0:
                    hold = max(hold, native)
            durations.append(hold)
            native_video_durations.append(native)
        # Give xfade incoming/outgoing handles while preserving every configured
        # clip hold in full.  These handles make transition time additive.
        segment_durations = [
            duration + (transitions[index - 1] if index else 0) + (transitions[index] if index < len(transitions) else 0)
            for index, duration in enumerate(durations)
        ]
        progress(2, "Normalizing media")
        for index, item in enumerate(media):
            if cancelled.is_set(): raise RenderError("Render cancelled by user")
            duration = segment_durations[index]
            segment = work / f"segment-{index:04d}.mp4"
            kind_name = item.get("type", "image")
            effect = str(item.get("effect", ""))
            ken_burns = kind_name == "image" and effect.startswith("Ken Burns")
            if kind_name == "image":
                # Photos are never cropped: fit the whole picture in the frame and
                # fill the letterbox bars with a blurred copy. Ken Burns clips are
                # fitted smaller so the zoom still cannot reach the picture edges.
                base_filter = fit_frame_filter(width, height, fps, KEN_BURNS_MAX_ZOOM if ken_burns else 1.0)
            else:
                base_filter = fill_frame_filter(width, height, fps)
            command = [self.settings.ffmpeg_bin, "-hide_banner", "-y"]
            clip_t = format_ffmpeg_number(duration)
            lead_in = transitions[index - 1] if index else 0.0
            lead_out = transitions[index] if index < len(transitions) else 0.0
            if kind_name == "title":
                background = str(item.get("frameBackground", "#202020"))
                if not background.startswith("#"): background = "#30382a"
                command += ["-f", "lavfi", "-i", f"color=c={background}:s={width}x{height}:r={fps}:d={clip_t}"]
            else:
                source = source_path(self.settings, item)
                if not source.exists(): raise RenderError(f"Media file is missing: {source}")
                # Still images default to 25 fps. Without an explicit -framerate
                # matching the output, `-t 5` yields 125 frames which fps=30
                # then shortens to ~4.17s and every xfade offset is late.
                if kind_name == "image":
                    command += ["-loop", "1", "-framerate", str(fps), "-t", clip_t, "-i", str(source)]
                else:
                    # Play the movie once end-to-end. Never stream_loop a video:
                    # looping would restart the clip mid-hold or during the
                    # transition handle, cutting the story short visually.
                    # Transition handles and any hold beyond the native length
                    # are filled by freezing the first/last frame via tpad.
                    command += ["-i", str(source)]
            filters = [base_filter]
            if ken_burns:
                delta = "0.0008" if "Zoom in" in effect else "-0.0008" if "Zoom out" in effect else "0.0003"
                start_zoom = "1" if delta.startswith("0") else format_ffmpeg_number(KEN_BURNS_MAX_ZOOM)
                zoom = f"max(1,min({format_ffmpeg_number(KEN_BURNS_MAX_ZOOM)},{start_zoom}+on*{delta}))"
                # Anchor the zoom in the centre; zoompan otherwise defaults to the
                # top-left corner, which would push the picture out of frame.
                filters.append(
                    f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}"
                )
            if kind_name == "video":
                # Freeze the opening frame for the incoming xfade handle and the
                # closing frame for the outgoing handle (plus any extra hold the
                # user configured beyond the native runtime). The full movie
                # plays uninterrupted between those pads.
                native = native_video_durations[index]
                if native is not None and native > 0:
                    pad_start = lead_in
                    # After the native movie finishes we still need: remaining
                    # hold past native (if user extended duration) + outgoing
                    # transition.
                    pad_end = max(0.0, durations[index] - native) + lead_out
                    if pad_start > 0.0005 or pad_end > 0.0005:
                        filters.append(
                            f"tpad=start_mode=clone:start_duration={format_ffmpeg_number(pad_start)}"
                            f":stop_mode=clone:stop_duration={format_ffmpeg_number(pad_end)}"
                        )
                elif lead_in > 0.0005 or lead_out > 0.0005:
                    # Probe failed: still pad the transition handles by cloning
                    # edge frames, but do not invent a "native" length of 0
                    # which would turn the whole segment into frozen frames.
                    filters.append(
                        f"tpad=start_mode=clone:start_duration={format_ffmpeg_number(lead_in)}"
                        f":stop_mode=clone:stop_duration={format_ffmpeg_number(lead_out)}"
                    )
            text_filter = self._text_filter(item, defaults, width, height)
            if text_filter: filters.append(text_filter)
            filters += ["format=yuv420p", "settb=AVTB", "setpts=PTS-STARTPTS"]
            command += ["-vf", ",".join(filters), "-an", "-t", clip_t, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(segment)]
            self._run_ffmpeg(command, cancelled, log_file)
            segments.append(segment)
            progress(5 + 45 * (index + 1) / len(media), f"Prepared item {index+1} of {len(media)}")

        xfade_names = [self.resolve_xfade(str(media[index].get("transition", "Fade"))) for index in range(len(media) - 1)]
        total_duration = sum(durations) + sum(transitions)
        progress(55, "Composing transitions and soundtrack")
        if kind == "preview":
            target_dir = self.settings.preview_dir
            filename = f"project-{project.get('id','new')}-preview-{uuid.uuid4().hex[:8]}.mp4"
        else:
            output = self.render_output_path(project)
            target_dir = output.parent
            filename = output.name
        target_dir.mkdir(parents=True, exist_ok=True)
        output = target_dir / filename
        encoder_label = str(output_settings.get("encoder", "Auto"))
        bitrate_value = parse_number(bitrate, 2)
        encoder = "h264_qsv" if "Quick Sync" in encoder_label and self.qsv_encodable() else "libx264"

        def encode_args_for(codec: str, *, intermediate: bool) -> list[str]:
            if codec == "h264_qsv":
                args = ["-c:v", codec, "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_value * 2:g}M", "-pix_fmt", "nv12"]
            else:
                preset = "veryfast" if intermediate else "medium"
                args = ["-c:v", codec, "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_value * 2:g}M", "-preset", preset, "-pix_fmt", "yuv420p"]
            if not intermediate:
                args += ["-movflags", "+faststart"]
            return args

        def run_compose(command: list[str], *, allow_qsv_fallback: bool) -> None:
            try:
                self._run_ffmpeg(command, cancelled, log_file)
            except RenderError:
                if not allow_qsv_fallback or encoder != "h264_qsv":
                    raise
                progress(70, "Quick Sync unavailable; retrying on CPU")
                log.warning("h264_qsv failed; falling back to libx264")
                patched = list(command)
                try:
                    idx = patched.index("h264_qsv")
                    patched[idx] = "libx264"
                    if "-pix_fmt" in patched:
                        pix = patched.index("-pix_fmt")
                        patched[pix + 1] = "yuv420p"
                    if "-preset" not in patched:
                        patched[idx + 1:idx + 1] = ["-preset", "medium"]
                except ValueError:
                    raise
                self._run_ffmpeg(patched, cancelled, log_file)

        # Reduce long stories in batches so FFmpeg never opens 80+ 1080p
        # streams in one filter graph (that stalls after "Press [q]").
        level = 0
        cur_segs = list(segments)
        cur_durs = list(durations)
        cur_trans = list(transitions)
        cur_xfade = list(xfade_names)
        while len(cur_segs) > COMPOSE_BATCH_SIZE:
            windows = chunk_indices(len(cur_segs), COMPOSE_BATCH_SIZE)
            nxt_segs: list[Path] = []
            nxt_durs: list[float] = []
            nxt_trans: list[float] = []
            nxt_xfade: list[str] = []
            for w_i, (start, end) in enumerate(windows):
                if cancelled.is_set():
                    raise RenderError("Render cancelled by user")
                group = cur_segs[start:end]
                g_durs = cur_durs[start:end]
                g_trans = cur_trans[start:end - 1]
                g_xfade = cur_xfade[start:end - 1]
                trail = cur_trans[end - 1] if end < len(cur_segs) else 0.0
                if len(group) == 1:
                    nxt_segs.append(group[0])
                    nxt_durs.append(g_durs[0])
                else:
                    mid = work / f"compose-L{level}-{w_i:03d}.mp4"
                    graph = build_filter_graph(g_durs, g_trans, g_xfade, fps)
                    cut = sum(g_durs) + sum(g_trans) + trail
                    inputs: list[str] = []
                    for path in group:
                        inputs += ["-i", str(path)]
                    command = [
                        self.settings.ffmpeg_bin, "-hide_banner", "-y", *inputs,
                        "-filter_complex", graph, "-map", "[vout]", "-an",
                        *encode_args_for(encoder, intermediate=True),
                        "-r", str(fps), "-t", format_ffmpeg_number(cut), str(mid),
                    ]
                    progress(55 + 20 * (w_i + 1) / max(1, len(windows)), f"Composing group {w_i + 1} of {len(windows)}")
                    run_compose(command, allow_qsv_fallback=True)
                    nxt_segs.append(mid)
                    nxt_durs.append(sum(g_durs) + sum(g_trans))
                if end < len(cur_segs):
                    nxt_trans.append(cur_trans[end - 1])
                    nxt_xfade.append(cur_xfade[end - 1])
            cur_segs, cur_durs, cur_trans, cur_xfade = nxt_segs, nxt_durs, nxt_trans, nxt_xfade
            level += 1

        filter_graph = build_filter_graph(cur_durs, cur_trans, cur_xfade, fps)
        audio_args: list[str] = []
        audio_map: list[str] = []
        if soundtrack:
            audio_index = len(cur_segs)
            policy = project.get("soundtrack", {}).get("policy", "Loop & trim")
            if policy == "Loop & trim":
                audio_args += ["-stream_loop", "-1"]
            audio_args += ["-i", str(soundtrack)]
            volume = max(0, min(1, float(project.get("soundtrack", {}).get("volume", 100)) / 100))
            fade = project.get("soundtrack", {}).get("fadeOut", True)
            af = f"volume={volume}"
            if fade:
                af += f",afade=t=out:st={format_ffmpeg_number(max(0, total_duration - 2))}:d=2"
            filter_graph += f";[{audio_index}:a]{af}[aout]"
            audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        inputs = []
        for path in cur_segs:
            inputs += ["-i", str(path)]
        command = [
            self.settings.ffmpeg_bin, "-hide_banner", "-y", *inputs, *audio_args,
            "-filter_complex", filter_graph, "-map", "[vout]", *audio_map,
            *encode_args_for(encoder, intermediate=False),
            "-r", str(fps), "-t", str(total_duration), str(output),
        ]
        run_compose(command, allow_qsv_fallback=True)
        progress(98, "Finalizing MP4")
        return output

    def _probe_duration(self, path: Path) -> float:
        result=subprocess.run([self.settings.ffprobe_bin,"-v","error","-show_entries","format=duration","-of","json",str(path)],capture_output=True,text=True,timeout=30)
        if result.returncode: raise RenderError(f"Could not probe duration of {path.name}: {result.stderr}")
        return float(json.loads(result.stdout)["format"]["duration"])

    def _make_soundtrack(self, project: dict[str,Any], work: Path, cancelled: threading.Event, log_file: Path) -> Path | None:
        tracks=project.get("soundtrack",{}).get("tracks",[])
        if not tracks: return None
        sources=[]
        for track in tracks:
            source=source_path(self.settings, track)
            if not source.exists(): raise RenderError(f"Soundtrack is missing: {source}")
            sources.append(source)
        output=work/"soundtrack.m4a"
        inputs=[]
        for source in sources: inputs += ["-i",str(source)]
        normalized=";".join(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]" for i in range(len(sources)))
        concat="".join(f"[a{i}]" for i in range(len(sources)))+f"concat=n={len(sources)}:v=0:a=1[aout]"
        command=[self.settings.ffmpeg_bin,"-hide_banner","-y",*inputs,"-filter_complex",normalized+";"+concat,"-map","[aout]","-vn","-c:a","aac","-b:a","192k",str(output)]
        self._run_ffmpeg(command,cancelled,log_file)
        return output
