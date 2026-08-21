"""Safe browsing and path resolution inside configured Docker mounts."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from .config import Settings

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}


class UnsafePath(ValueError):
    pass


def safe_path(root: Path, relative: str = "") -> Path:
    root = root.resolve()
    candidate = (root / relative.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise UnsafePath("Path escapes its configured media root")
    return candidate


def mounted_path(settings: Settings, value: str, name: str = "") -> Path:
    """Resolve UI paths such as /photos/Holiday plus a filename safely."""
    normalized = value.replace("\\", "/")
    for key, root in settings.media_roots.items():
        prefix = f"/{key}"
        if normalized == prefix or normalized.startswith(prefix + "/"):
            relative = normalized[len(prefix):].lstrip("/")
            base = safe_path(root, relative)
            return safe_path(base, name) if name else base
    raise UnsafePath(f"Path must start with one of: {', '.join('/'+x for x in settings.media_roots)}")


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


def browse(settings: Settings, root_name: str, relative: str = "", folders_only: bool = False) -> dict[str, Any]:
    if root_name not in settings.media_roots:
        raise UnsafePath("Unknown or non-browsable media root")
    if root_name == "output" and not folders_only:
        raise UnsafePath("The output root is only browsable when picking a destination folder")
    root = settings.media_roots[root_name]
    folder = safe_path(root, relative)
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(str(folder))
    accepted = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
    entries = []
    for child in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
        if child.name.startswith("."):
            continue
        if folders_only:
            if not child.is_dir():
                continue
        elif not child.is_dir() and child.suffix.lower() not in accepted:
            continue
        stat = child.stat()
        kind = "directory"
        if child.is_file():
            ext = child.suffix.lower()
            kind = "image" if ext in IMAGE_EXTENSIONS else "video" if ext in VIDEO_EXTENSIONS else "audio"
        rel = child.relative_to(root).as_posix()
        entries.append({
            "name": child.name, "path": f"/{root_name}/{rel}", "relativePath": rel,
            "kind": kind, "size": stat.st_size, "empty": stat.st_size == 0,
            "modified": stat.st_mtime,
            "mime": mimetypes.guess_type(child.name)[0],
        })
    parent = Path(relative).parent.as_posix() if relative and Path(relative).parent != Path(".") else ""
    return {"root": root_name, "path": relative, "parent": parent, "entries": entries}
