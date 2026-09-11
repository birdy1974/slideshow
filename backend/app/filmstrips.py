"""Cached movie filmstrips: one wide JPEG of N frames from a movie file.

The movie editor's trim strip shows real frames of the movie instead of
abstract colour bars. The browser usually grabs those frames itself from the
streamed file; this module is the fallback for containers browsers cannot
decode (camera Motion-JPEG AVIs and friends) — the same division of labour
as the duration probe: the browser first, FFmpeg when it must.

One sprite is generated **once per file** and cached on the config volume::

    <config>/filmstrips/<hash>.jpg

The hash covers the root, relative path, size, mtime, cell count, cell width
and a format version, so any change to the file (or to the strip settings)
silently produces a new sprite.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path

from .config import Settings
from .media import VIDEO_EXTENSIONS, safe_path

log = logging.getLogger(__name__)

# Cell geometry: the editor stretches the sprite edge-to-edge, so cells are
# small on purpose (10 cells at 160 px = a ~1600x90 JPEG, tens of KB).
DEFAULT_COUNT = 10
DEFAULT_WIDTH = 160
MIN_COUNT, MAX_COUNT = 4, 16
MIN_WIDTH, MAX_WIDTH = 80, 480
PROBE_TIMEOUT = 30
RENDER_TIMEOUT = 120


class FilmstripUnavailable(RuntimeError):
    """The movie cannot be turned into a filmstrip; the message is user-facing."""


def _cache_key(root_name: str, relative: str, size: int, mtime_ns: int, count: int, width: int) -> str:
    raw = f"v1:{root_name}:{relative}:{size}:{mtime_ns}:{count}:{width}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def _probe_duration(ffprobe_bin: str, target: Path) -> float:
    try:
        result = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(target)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise FilmstripUnavailable(f"Could not inspect the movie: {exc}") from exc
    if result.returncode:
        raise FilmstripUnavailable("FFmpeg could not read this movie")
    try:
        duration = float(json.loads(result.stdout).get("format", {}).get("duration", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        duration = 0
    if duration <= 0:
        raise FilmstripUnavailable("Movie duration is unavailable")
    return duration


def build_filmstrip(settings: Settings, root_name: str, relative: str, count: int = DEFAULT_COUNT,
                    width: int = DEFAULT_WIDTH) -> Path:
    """Render (or return from cache) the sprite for one movie on a media root.

    Raises :class:`FilmstripUnavailable` with a user-facing reason when the
    file is missing, unreadable or empty; nothing is cached then.
    """
    count = max(MIN_COUNT, min(MAX_COUNT, int(count)))
    width = max(MIN_WIDTH, min(MAX_WIDTH, int(width)))
    if root_name not in settings.media_roots:
        raise FilmstripUnavailable("Unknown media root")
    try:
        target = safe_path(settings.media_roots[root_name], relative)
    except Exception as exc:  # UnsafePath — never leak the real path anyway
        raise FilmstripUnavailable("Invalid media path") from exc
    if not target.is_file():
        raise FilmstripUnavailable("Movie file not found")
    if target.suffix.lower() not in VIDEO_EXTENSIONS:
        raise FilmstripUnavailable("File is not a supported movie")
    stat = target.stat()
    if stat.st_size == 0:
        raise FilmstripUnavailable("File is empty (0 bytes)")

    cache_dir = settings.config_dir / "filmstrips"
    cached = cache_dir / f"{_cache_key(root_name, relative, stat.st_size, stat.st_mtime_ns, count, width)}.jpg"
    if cached.exists():
        return cached

    duration = _probe_duration(settings.ffprobe_bin, target)
    # One FFmpeg run: N inputs of the same file, each fast-seeked (-ss before
    # -i is frame-accurate since FFmpeg 2.1 while skipping the decode lead-in).
    # Frames land at the centre of N equal slices; every chain is scaled to
    # the cell width (same source, so heights match) and hstacked into a strip.
    command: list[str] = [settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]
    chains: list[str] = []
    for index in range(count):
        at = (index + 0.5) / count * duration
        command += ["-ss", f"{at:.3f}", "-i", str(target)]
        chains.append(f"[{index}:v]scale={width}:-2,setsar=1[c{index}]")
    graph = ";".join(chains) + ";" + "".join(f"[c{i}]" for i in range(count)) + f"hstack=inputs={count}[v]"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".jpg.part")
    command += [
        "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-q:v", "5",
        # The temp name ends in .part, from which FFmpeg cannot infer an output
        # muxer — declare it explicitly (docs/transition-preview-fix.md).
        "-f", "mjpeg", str(tmp),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        raise FilmstripUnavailable("Filmstrip render timed out") from None
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise FilmstripUnavailable(f"Could not run FFmpeg: {exc}") from exc
    if result.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1][:200] if detail else "FFmpeg failed"
        raise FilmstripUnavailable(reason)
    tmp.replace(cached)
    log.info("Rendered filmstrip for %s (%s cells)", target.name, count)
    return cached
