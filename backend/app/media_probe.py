"""What a video file actually is — and whether it is whole.

Three caches hold MP4s and JPEGs the browser plays directly: the movie proxy
(``video_preview``), the transition gallery and the text-effect cards. All of them
trusted a cache entry by *existence*, and the proxy additionally by "bigger than a
kilobyte". That is not a check: a write cut short — the container stopped mid
transcode, a rename across a network volume where rename is not atomic, a NAS
volume that ran out of space — leaves a small file that satisfies it, and then it is
served forever. The report that follows is the most confusing one there is: *the
same broken picture on every render, no matter how often the backend is fixed*,
because nothing ever looked at the bytes again.

So: ask FFmpeg, not the filesystem. A whole file has a ``moov`` box and decodes at
both ends. ``summary`` is the same look, described, for ``/api/diagnose`` — the one
glance that says what a deployment actually produced, which is what a
"renders okay but looks wrong" report turns on.

FFmpeg only, never ffprobe: the version of the container that has to answer this
question on a user's NAS is the version that may not ship ffprobe at all.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

# Each check below decodes at most a frame or two of a 640 px file, so a cache hit
# stays in the tens of milliseconds. A render spawns hundreds of FFmpeg processes;
# three tiny ones are not the cost the user notices.
TIMEOUT = 20
HEAD_WINDOW = 256 * 1024
TAIL_WINDOW = 64 * 1024


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)


def stream_lines(ffmpeg_bin: str, path: Path) -> list[str]:
    """The `Stream #…` lines FFmpeg prints for a file, with the banner noise gone."""
    try:
        result = subprocess.run([ffmpeg_bin, "-hide_banner", "-i", str(path)],
                                capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in (result.stderr or "").splitlines():
        if "Stream #" in line:
            out.append(" ".join(line.strip().split()))
    return out


def _has_moov(path: Path) -> bool:
    """A playable MP4 must carry an index. A file cut short during the write has the
    media data and no `moov`, and no player can tell the user why it shows garbage."""
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            head = handle.read(min(HEAD_WINDOW, size))
            tail = b""
            if size > HEAD_WINDOW * 2:
                handle.seek(max(0, size - TAIL_WINDOW))
                tail = handle.read(TAIL_WINDOW)
    except OSError:
        return False
    return b"moov" in head or b"moov" in tail


def decodes_at_both_ends(ffmpeg_bin: str, path: Path) -> bool:
    """One frame from the head, one from the tail. A truncated tail is the failure
    mode that matters here, and it is invisible to every other check."""
    for probe in (["-i", str(path), "-frames:v", "1", "-f", "null", "-"],
                  ["-sseof", "-0.5", "-i", str(path), "-frames:v", "1", "-f", "null", "-"]):
        try:
            if _run([ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y", *probe]).returncode != 0:
                return False
        except (OSError, subprocess.SubprocessError):
            return False
    return True


_VERIFIED: dict[tuple, bool] = {}
_VERIFIED_LIMIT = 512


def verified_whole(settings, path: Path, *, image: bool = False) -> bool:
    """`whole_video` / `whole_image`, remembered per (path, size, mtime).

    A gallery page asks this for two dozen clips on every open, so the answer is
    kept for as long as the bytes are the same: a rebuild changes the mtime, and a
    changed file is examined again. This is the difference between "we look at the
    cache" and "we decode a 4K filmstrip every time a dialog opens".
    """
    try:
        stat = path.stat()
    except OSError:
        return False
    key = (str(path), stat.st_size, int(stat.st_mtime_ns), image)
    remembered = _VERIFIED.get(key)
    if remembered is not None:
        return remembered
    verdict = whole_image(settings, path) if image else whole_video(settings, path)
    if len(_VERIFIED) >= _VERIFIED_LIMIT:
        _VERIFIED.clear()
    _VERIFIED[key] = verdict
    return verdict


def structurally_whole(path: Path, *, minimum: int = 1024, index: bytes = b"moov") -> bool:
    """A file that has an index and plausible bulk, without asking FFmpeg.

    For the gallery clips, where a page opens two hundred cache entries and spawning
    a decoder per entry would cost more than rendering them. A truncated write has no
    `moov`: that is the whole test, and it is the difference between a broken chip
    being replaced and being served to every visitor forever.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < minimum:
        return False
    return _has_moov(path) if index == b"moov" else index in path.read_bytes()[:minimum]


def whole_video(settings, path: Path) -> bool:
    """Can this cached file be trusted to be what a render wrote?"""
    try:
        if not path.is_file() or path.stat().st_size < 2048:
            return False
    except OSError:
        return False
    return _has_moov(path) and decodes_at_both_ends(settings.ffmpeg_bin, path)


def whole_image(settings, path: Path) -> bool:
    """The filmstrip sprite is a JPEG; for a JPEG "whole" means "decodes"."""
    try:
        if not path.is_file() or path.stat().st_size < 512:
            return False
    except OSError:
        return False
    try:
        return _run([settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-v", "error",
                     "-i", str(path), "-frames:v", "1", "-f", "null", "-"]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_COLOUR = re.compile(r"(?P<pix>yuv[a-z0-9]+|rgb[0-9]+|bgr[0-9]+|nv12|p010|gbrp)\s*(\((?P<tags>[^)]*)\))?")


def describe(lines: list[str]) -> dict[str, Any]:
    """Pull the fields a wrong-colour report hinges on out of FFmpeg's stream lines.

    `yuvj420p(pc, bt470bg/unknown/unknown)` and `yuv420p(tv, bt709/bt709/bt709)` are
    the two descriptions a slideshow must not mix inside one file — see
    docs/timeline-colour-normalisation.md — so a user can read their own answer here
    instead of describing the damage in words.
    """
    video = next((line for line in lines if "Video:" in line), "")
    audio = next((line for line in lines if "Audio:" in line), "")
    match = _COLOUR.search(video)
    tags = [piece.strip() for piece in (match.group("tags") or "").split(",")] if match and match.group("tags") else []
    size = re.search(r"(\d{2,5})x(\d{2,5})", video)
    sar = re.search(r"SAR\s+(\d+:\d+)", video)
    interlaced = "interlaced" in video or "tff" in video or "bff" in video
    return {
        "video": video or None,
        "audio": audio or None,
        "pixFmt": match.group("pix") if match else None,
        "range": tags[0] if tags and tags[0] in ("pc", "tv") else None,
        "colourTags": "/".join(tag for tag in tags[1:] if tag and tag != "progressive") or None,
        "size": f"{size.group(1)}x{size.group(2)}" if size else None,
        "sar": sar.group(1) if sar else None,
        "interlaced": interlaced,
    }


def summary(settings, path: Path) -> dict[str, Any]:
    """Everything /api/diagnose reports about one file."""
    lines = stream_lines(settings.ffmpeg_bin, path)
    try:
        stat = path.stat()
        exists, size = True, stat.st_size
    except OSError:
        exists, size = False, 0
    out: dict[str, Any] = {"path": str(path), "exists": exists, "sizeBytes": size}
    if not exists or not lines:
        out.update(readable=False, complete=False)
        return out
    out.update(describe(lines))
    out["readable"] = True
    out["complete"] = _has_moov(path) and decodes_at_both_ends(settings.ffmpeg_bin, path) if path.suffix.lower() in {".mp4", ".m4v", ".mov"} else True
    return out
