"""Files uploaded from the GUI device (photos & movies).

The media mounts (/photos, /videos, /music) are read-only security
boundaries: the app never writes into a user's photo archive. The uploads
root is the one place browser files may land — a dedicated volume in
Docker (``UPLOADS_DIR=/uploads``), below the config directory elsewhere.
Everything downstream (browsing, streaming, thumbnails, rendering) treats
an uploaded file exactly like a mounted one, because it simply lives
inside a media root.

Every file is validated before it is kept:

- the name is sanitised with the same rules as project files
  (``safe_stem``) and de-duplicated (``beach.jpg`` -> ``beach-2.jpg``);
- the extension must be a known photo or movie type;
- the size is capped (``UPLOAD_MAX_MB``, default 4096) while streaming to
  disk, so a phone dump cannot fill the NAS volume silently;
- ffprobe must be able to read the result — a text file renamed ``.mp4``
  is rejected with a clear reason instead of failing a render later.

Rejected files never stay on disk. Accepted files are returned in the
same entry shape the media browser uses, so the frontend can add them to
the storyline through its normal path.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, BinaryIO

from .config import Settings
from .media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, safe_path
from .project_files import safe_stem

log = logging.getLogger(__name__)

ACCEPTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# Marker written next to a file that ffprobe could not read, purely for the
# error path (the file itself is deleted immediately after).
_CHUNK = 1024 * 1024


class UploadRejected(ValueError):
    """The file cannot be accepted; the message is user-facing."""


def unique_name(directory: Path, original: str) -> str:
    """A free filename in ``directory`` based on ``original``, never clashing.

    ``beach.jpg`` -> ``beach.jpg``, then ``beach-2.jpg``, ``beach-3.jpg``, …
    Keeps the (already validated) extension and is case-insensitive, because
    the NAS volumes are typically case-insensitive too.
    """
    stem = Path(original).stem
    suffix = Path(original).suffix.lower()
    candidate = f"{stem}{suffix}"
    taken = {entry.name.lower() for entry in directory.iterdir()} if directory.exists() else set()
    counter = 2
    while candidate.lower() in taken:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def sanitized_name(original: str) -> str:
    """Keep only the last path component, filesystem-safe, photo/movie only.

    Mirrors the project-file rules: ``../../evil.jpg`` -> ``evil.jpg``,
    illegal characters become a dash, control characters disappear. The
    extension must be an accepted photo/movie type — audio uploads are out
    of scope for now.
    """
    text = str(original or "").replace("\\", "/")
    name = Path(text).name
    stem = safe_stem(Path(name).stem) or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in ACCEPTED_EXTENSIONS:
        raise UploadRejected(f"'{name}' is not a supported photo or movie type")
    return f"{stem}{suffix}"


def probe_media_file(ffprobe_bin: str, target: Path, image: bool) -> None:
    """Raise :class:`UploadRejected` unless ffprobe reads a real media file.

    Any decodable input has a video stream; that single check rejects empty
    payloads, renamed text files and truncated downloads alike, for photos
    and movies both.
    """
    command = [
        ffprobe_bin, "-v", "error", "-show_entries",
        "stream=codec_type", "-of", "json", str(target),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise UploadRejected("Could not verify the file (ffprobe unavailable)") from exc
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = detail[-1][:200] if detail else "FFmpeg could not read this file"
        raise UploadRejected(f"Not a valid {'photo' if image else 'movie'}: {reason}")
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except ValueError as exc:
        raise UploadRejected("Not a valid media file") from exc
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise UploadRejected(f"Not a valid {'photo' if image else 'movie'}: no picture data")


def store_upload(settings: Settings, original_name: str, source: BinaryIO) -> dict[str, Any]:
    """Validate, write and probe one uploaded file. Returns a browse-style entry.

    ``source`` is any readable binary stream (FastAPI's spooled upload file in
    production, BytesIO in tests). Raises :class:`UploadRejected` — with the
    reason — and leaves nothing behind when the file cannot be accepted.
    """
    name = sanitized_name(original_name)
    image = Path(name).suffix.lower() in IMAGE_EXTENSIONS
    cap = max(1, int(getattr(settings, "upload_max_mb", 4096))) * 1024 * 1024
    root = settings.uploads_dir
    root.mkdir(parents=True, exist_ok=True)
    # safe_path guards the (already sanitised) name against any surprise.
    target = safe_path(root, name)
    final = root / unique_name(root, name)
    written = 0
    try:
        with final.open("wb") as handle:
            while True:
                chunk = source.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > cap:
                    raise UploadRejected(
                        f"'{Path(original_name).name}' is larger than the {cap // (1024 * 1024)} MB upload limit")
                handle.write(chunk)
    except UploadRejected:
        final.unlink(missing_ok=True)
        raise
    except OSError as exc:
        final.unlink(missing_ok=True)
        raise UploadRejected(f"Could not write the upload: {exc}") from exc
    if written == 0:
        final.unlink(missing_ok=True)
        raise UploadRejected(f"'{Path(original_name).name}' is empty (0 bytes)")
    try:
        probe_media_file(settings.ffprobe_bin, final, image)
    except UploadRejected:
        final.unlink(missing_ok=True)
        raise
    kind = "image" if image else "video"
    relative = f"/uploads/{final.name}"
    log.info("Stored upload %s (%.1f MB)", final.name, written / (1024 * 1024))
    return {"name": final.name, "path": relative, "kind": kind, "size": written}
