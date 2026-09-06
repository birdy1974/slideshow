"""Project files on the mounted volumes — "Save project" / "Load project" as files.

SQLite (`/config/slideshow.db`) stays the working store: it is what restores the
editor after a refresh, what the render queue hangs off, and what keeps project
revisions. A *project file* is an extra, portable copy of the same snapshot —
something you can back up, copy to another NAS, or hand to somebody else — so
saving writes the file **and** the database row, and loading a file puts it in
the editor and stores it as a fresh row.

Two rules keep this inside the container's security model:

* **Only writable mounts accept a save.** `/photos`, `/videos` and `/music` are
  mounted `:ro` in compose.yaml; `/output` is the one volume the user owns and
  the renderer already writes to. Loading may read from any mount, because
  reading a read-only volume is exactly what the media browser does all day.
* **A filename can never leave its folder.** The name is reduced to its last
  path component, the characters no filesystem accepts become a dash, edge dots
  and dashes are dropped, the length is capped, and the folder itself goes
  through `safe_path()` — the same traversal guard the media browser uses.

Writes are atomic (a temporary file in the destination folder, then
`os.replace`), so a project file on the NAS is never half-written.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings
from .media import PROJECT_EXTENSIONS, UnsafePath, safe_path

# The suffix this app writes. Loading also accepts a plain `.json`, so a file
# somebody renamed by hand still opens.
PROJECT_SUFFIX = ".slideshow.json"

# Roots a project file may be written to. Keep in sync with compose.yaml, where
# every other mount is `:ro`.
WRITABLE_ROOTS = ("output",)

# A project snapshot is JSON with a media list; even a ten-thousand-clip
# storyline is a few MB. Anything bigger is not a project file.
MAX_PROJECT_BYTES = 32 * 1024 * 1024

# Mirrors MAX_NAME_LENGTH in src/projectName.ts.
MAX_NAME_LENGTH = 80

ILLEGAL_CHARACTERS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
EDGE_CHARACTERS = re.compile(r"^[\s.\-]+|[\s.\-]+$")


class ReadOnlyMountError(PermissionError):
    """The chosen volume is mounted read-only, so nothing can be saved there."""


class ProjectFileExistsError(RuntimeError):
    """A file is already there and the caller did not ask to overwrite it."""

    def __init__(self, ui_path: str) -> None:
        super().__init__(ui_path)
        self.ui_path = ui_path


def safe_stem(name: Any) -> str:
    """The filesystem-safe stem of a name — the twin of `safeFilename()` in the
    browser, so both sides agree on what a project file is called."""
    # Anything that is not a string is not a name — no repr() surprises.
    text = name if isinstance(name, str) else ""
    # A pasted path keeps only its last component.
    text = Path(text.replace("\\", "/")).name
    return EDGE_CHARACTERS.sub("", ILLEGAL_CHARACTERS.sub("-", text)[:MAX_NAME_LENGTH * 2])[:MAX_NAME_LENGTH].strip(" .-")


def project_filename(name: Any) -> str:
    """`Portugal summer` → `Portugal summer.slideshow.json` (never empty)."""
    stem = safe_stem(name)
    lowered = stem.lower()
    if lowered.endswith(PROJECT_SUFFIX):
        stem = stem[: -len(PROJECT_SUFFIX)]
    elif lowered.endswith(".json"):
        stem = stem[: -len(".json")]
    return f"{EDGE_CHARACTERS.sub('', stem) or 'project'}{PROJECT_SUFFIX}"


def ui_path(root: str, relative: str | Path) -> str:
    """The path the UI talks in: `/output/trip/holiday.slideshow.json`."""
    text = str(relative).replace(os.sep, "/").lstrip("/")
    return f"/{root}/{text}" if text else f"/{root}"


def _root(settings: Settings, root: str) -> Path:
    if root not in settings.media_roots:
        raise UnsafePath(f"Unknown volume: {root}")
    return settings.media_roots[root]


def resolve_project_file(settings: Settings, root: str, relative: str) -> Path:
    """Absolute path of a project file, inside its mount or not at all."""
    folder = safe_path(_root(settings, root), str(Path(relative).parent if Path(relative).name else relative))
    name = Path(str(relative).replace("\\", "/")).name
    if not name:
        raise UnsafePath("No project file was given")
    return safe_path(folder, name)


def read_project_file(settings: Settings, root: str, relative: str) -> dict[str, Any]:
    """Load one project file. Returns the snapshot exactly as it was saved."""
    target = resolve_project_file(settings, root, relative)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(ui_path(root, relative))
    if target.suffix.lower() not in PROJECT_EXTENSIONS:
        raise ValueError("That is not a project file — look for a .slideshow.json")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise PermissionError(f"Cannot read {target.name}: {exc}") from exc
    if size == 0:
        raise ValueError("That project file is empty (0 bytes)")
    if size > MAX_PROJECT_BYTES:
        raise ValueError(f"That file is {size // (1024 * 1024)} MB — too big to be a project")
    try:
        snapshot = json.loads(target.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"That file is not readable project JSON: {exc}") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("A project file holds an object, not a list or a value")
    if not isinstance(snapshot.get("media", []), list) or not isinstance(snapshot.get("project", {}), dict):
        raise ValueError("That JSON is not a slideshow project (no media list)")
    if not any(key in snapshot for key in ("media", "project", "schemaVersion", "output")):
        raise ValueError("That JSON is not a slideshow project")
    return snapshot


def project_file_info(settings: Settings, root: str, relative: str) -> dict[str, Any]:
    """What the picker shows about a saved project file, plus its snapshot."""
    target = resolve_project_file(settings, root, relative)
    snapshot = read_project_file(settings, root, relative)
    stat = target.stat()
    name = str(snapshot.get("project", {}).get("name") or "")
    return {
        "root": root,
        "path": ui_path(root, target.relative_to(_root(settings, root)).as_posix()),
        "name": target.name,
        "projectName": name,
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "items": len(snapshot.get("media") or []),
        "project": snapshot,
    }


def write_project_file(
    settings: Settings,
    root: str,
    folder: str,
    filename: Any,
    snapshot: dict[str, Any],
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save a snapshot as a project file. Atomic, and only onto a writable mount."""
    base = _root(settings, root)
    if root not in WRITABLE_ROOTS:
        raise ReadOnlyMountError(
            f"/{root} is mounted read-only — project files are saved on /output"
        )
    destination = safe_path(base, folder)
    name = project_filename(filename)
    target = safe_path(destination, name)
    shown = ui_path(root, target.relative_to(safe_path(base, "")).as_posix())
    if target.exists() and not overwrite:
        raise ProjectFileExistsError(shown)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
    if len(payload) > MAX_PROJECT_BYTES:
        raise ValueError("This project is too large to save as a file")
    try:
        destination.mkdir(parents=True, exist_ok=True)
        # Same folder, so os.replace() is a rename on the same filesystem even
        # when /output is a network mount.
        handle, temporary = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".part", dir=str(destination))
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise PermissionError(f"Could not write {shown}: {exc}") from exc
    return {
        "root": root,
        "path": shown,
        "name": name,
        "folder": ui_path(root, destination.relative_to(safe_path(base, "")).as_posix()),
        "size": len(payload),
        "overwritten": bool(overwrite),
        "items": len(snapshot.get("media") or []),
    }

