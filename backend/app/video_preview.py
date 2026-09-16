"""Browser-playable proxy for movies the browser cannot decode.

Many consumer formats (WMV, MPEG-PS, AVCHD / MTS, FLV, 3GP, DV-AVI,
Motion-JPEG AVI such as the Casio EX-Z11, …) are valid FFmpeg inputs but
have no HTMLVideoElement decoder.  The timeline and the movie editor still
need to *play* the file for trimming and inspection.  This module renders
a small H.264/AAC MP4 proxy once per source file and caches it under the
config volume, analogous to filmstrips.py.

The proxy is deliberately light: 640 px wide (the same as the slideshow
preview), H.264 yuv420p, AAC stereo, faststart, ~2 Mbps.  It is only used
as a *fallback* when the original stream errors in the browser, so its cost
is paid once per file, not on every timeline render.

Cache location::

    <config>/video-previews/<hash>.mp4

Hash covers root, relative path, size, mtime and a format version.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from .config import Settings
from .media import VIDEO_EXTENSIONS, safe_path

log = logging.getLogger(__name__)

PREVIEW_TIMEOUT = 180
CACHE_VERSION = "v1"
DEFAULT_WIDTH = 640  # matches the low-res slideshow preview


class PreviewUnavailable(RuntimeError):
    """The movie cannot be turned into a preview; message is user-facing."""


def _cache_key(root_name: str, relative: str, size: int, mtime_ns: int, width: int) -> str:
    raw = f"{CACHE_VERSION}:{root_name}:{relative}:{size}:{mtime_ns}:{width}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def build_video_preview(
    settings: Settings,
    root_name: str,
    relative: str,
    width: int = DEFAULT_WIDTH,
) -> Path:
    """Return a cached H.264 proxy for *root/relative*, building it if needed.

    Raises :class:`PreviewUnavailable` when the file is missing, empty,
    unreadable, or FFmpeg fails to transcode it.
    """
    # sanitize width
    width = max(160, min(1280, int(width)))
    # make even for yuv420p
    if width % 2:
        width -= 1
    if root_name not in settings.media_roots:
        raise PreviewUnavailable("Unknown media root")
    try:
        target = safe_path(settings.media_roots[root_name], relative)
    except Exception as exc:
        raise PreviewUnavailable("Invalid media path") from exc
    if not target.is_file():
        raise PreviewUnavailable("Movie file not found")
    if target.suffix.lower() not in VIDEO_EXTENSIONS:
        raise PreviewUnavailable("File is not a supported movie")
    try:
        stat = target.stat()
    except OSError as exc:
        raise PreviewUnavailable(f"Cannot read file: {exc}") from exc
    if stat.st_size == 0:
        raise PreviewUnavailable("File is empty (0 bytes)")

    cache_dir = settings.config_dir / "video-previews"
    cached = cache_dir / f"{_cache_key(root_name, relative, stat.st_size, stat.st_mtime_ns, width)}.mp4"
    if cached.exists() and cached.stat().st_size > 1024:
        return cached

    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".mp4.part")

    # Transcode to a browser-safe MP4:
    # - scale to `width` preserving aspect, force even dimensions, yuv420p
    # - yadif for interlaced sources (640×480 29.97 interlaced is common) – no-op for progressive
    # - aac stereo, 128 kb, faststart
    # - limit to maybe first 5 minutes for preview? No, proxy the whole file but the editor only
    #   needs to seek anywhere, so keep full duration but low bitrate.
    # - Use veryfast preset for speed.
    # Rotation metadata is honoured via autorotate no; we let FFmpeg handle it via transpose if needed,
    # but simple scale handles most.
    vf = f"scale={width}:-2:flags=bicubic,format=yuv420p"
    # Add yadif for interlaced detection: it is harmless on progressive and fixes 29.97i
    # We use yadif=mode=0:parity=-1:deint=interlaced – but to keep simple, use yadif with deint all?
    # Instead, use bwdif which is better and auto. Fallback to yadif if bwdif missing.
    # For now, try scale only; if interlaced, FFmpeg still outputs progressive frames with combing,
    # which is acceptable for a preview. We keep command simple for compatibility.
    command = [
        settings.ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(target),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart",
        "-y",
        str(tmp),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=PREVIEW_TIMEOUT)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        raise PreviewUnavailable("Preview transcode timed out") from None
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PreviewUnavailable(f"Could not run FFmpeg: {exc}") from exc
    if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1][:220] if detail else "FFmpeg failed"
        raise PreviewUnavailable(reason)
    tmp.replace(cached)
    log.info("Built video preview %s (%s px) for %s", cached.name, width, target.name)
    return cached
