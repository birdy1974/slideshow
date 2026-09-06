"""Safe browsing and path resolution inside configured Docker mounts."""
from __future__ import annotations

import errno
import mimetypes
import os
from pathlib import Path
from typing import Any

from .config import Settings

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
# Saved project files (`holiday.slideshow.json`). Listed by the project picker
# and read/written by app.project_files; never treated as media.
PROJECT_EXTENSIONS = {".json"}


def is_project_file(path: Path) -> bool:
    """True for a file the project picker should list."""
    return path.suffix.lower() in PROJECT_EXTENSIONS


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


def _is_denied(exc: BaseException) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM}


def _folder_denied(folder: Path, root_name: str) -> PermissionError:
    label = folder.name or root_name
    return PermissionError(
        f"No permission to open “{label}”. "
        "The container user cannot read this folder — check DSM share/ACL permissions "
        "and the PUID/PGID in your compose file."
    )


def _sort_key(path: Path) -> tuple[bool, str]:
    try:
        is_directory = path.is_dir()
    except OSError:
        is_directory = False
    return (not is_directory, path.name.casefold())


def _entry_meta(path: Path) -> tuple[bool, bool, os.stat_result] | None:
    """Stat a child. None means it cannot be inspected and should be skipped."""
    try:
        stat = path.stat()
        return path.is_dir(), path.is_file(), stat
    except OSError:
        return None


def _dir_accessible(path: Path) -> bool:
    try:
        return os.access(path, os.R_OK)
    except OSError:
        return False


def browse(settings: Settings, root_name: str, relative: str = "", folders_only: bool = False,
           project_files: bool = False) -> dict[str, Any]:
    """List one folder of a mount.

    `folders_only` is the output-destination picker; `project_files` is the
    project picker, which needs folders *and* saved `.json` projects in one list
    (and is the only mode that may list files on the output root).
    """
    if root_name not in settings.media_roots:
        raise UnsafePath("Unknown or non-browsable media root")
    if root_name == "output" and not (folders_only or project_files):
        raise UnsafePath("The output root is only browsable when picking a destination folder")
    root = settings.media_roots[root_name]
    folder = safe_path(root, relative)
    try:
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(str(folder))
        children = list(folder.iterdir())
    except OSError as exc:
        if _is_denied(exc):
            raise _folder_denied(folder, root_name) from exc
        raise
    accepted = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
    entries = []
    for child in sorted(children, key=_sort_key):
        if child.name.startswith("."):
            continue
        meta = _entry_meta(child)
        if meta is None:
            # Dangling symlink, ACL hole, flaky NAS entry — skip rather than 500.
            continue
        is_directory, is_file, stat = meta
        if folders_only:
            if not is_directory:
                continue
        elif project_files:
            if not is_directory and not is_project_file(child):
                continue
        elif not is_directory and child.suffix.lower() not in accepted:
            continue
        kind = "directory"
        if is_file:
            ext = child.suffix.lower()
            kind = "project" if project_files and is_project_file(child) \
                else "image" if ext in IMAGE_EXTENSIONS else "video" if ext in VIDEO_EXTENSIONS else "audio"
        rel = child.relative_to(root).as_posix()
        accessible = _dir_accessible(child) if is_directory else True
        entries.append({
            "name": child.name, "path": f"/{root_name}/{rel}", "relativePath": rel,
            "kind": kind, "size": stat.st_size, "empty": stat.st_size == 0,
            "modified": stat.st_mtime,
            "mime": mimetypes.guess_type(child.name)[0],
            "accessible": accessible,
        })
    parent = Path(relative).parent.as_posix() if relative and Path(relative).parent != Path(".") else ""
    return {"root": root_name, "path": relative, "parent": parent, "entries": entries}
