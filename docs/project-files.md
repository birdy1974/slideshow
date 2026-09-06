# Project files — "Save project" / "Load project" as a file you can pick

Date: 2026-09-06 · Requested in `improvements.md`: *"\"load project\" and \"save project\"
button should call up browse popup so user can select path and filename where to
save/load."*

Built as chosen: **Load** gained a second tab — *Saved in SQLite* (unchanged) next to
*Browse files* — and **Save** opens a picker for the volume, the folder and the filename,
writing the file **and** the SQLite row. Loading may browse **all four** mounts (reading a
read-only volume is safe and is what the media browser does all day); saving is only
allowed on the **writable** `/output`.

SQLite (`/config/slideshow.db`) stays the working store: it restores the editor after a
refresh, feeds autosave, keeps project revisions and drives the render queue. A project
file is an **extra portable copy** of the same snapshot — something you can back up, copy
to another NAS, or hand to somebody else. That is why saving does both and why loading a
file stores it as a *fresh* row instead of overwriting the row you were editing.

## Save project

`Save project` → a browse popup built on the same dialog the Output pane already uses.

1. **Volume** — `/output` is offered; `/photos`, `/videos` and `/music` are listed but
   disabled and marked *read-only*, so it is obvious why they cannot be chosen instead of
   silently failing. `compose.yaml` mounts those three `:ro`.
2. **Folder** — breadcrumbs plus a grid of folders. The `/output` root became browsable
   for this (previously only `/photos` and `/videos` could be opened); missing folders are
   created when the file is written.
3. **Filename** — the project name, shown as `Portugal summer` + `.slideshow.json`, so the
   name comes from the project and the type stays fixed. The destination line under the
   field reads `→ /output/trip/Portugal summer.slideshow.json` while you type.

If that file already exists the row turns into **File exists — replace it?** with a
*Replace file* button, rather than overwriting quietly. That is the same handshake the
render uses for an existing MP4: the backend answers `409` with
`{"code": "project_exists", "path": "/output/trip/…"}`.

Afterwards the editor notifies what actually happened — `Project saved to /output/trip/… ·
and to SQLite`, or, if the database write failed, that the file is there but SQLite needs
another save. The last destination is remembered, so saving twice in a row is one click.

## Load project

Two tabs:

* **Saved in SQLite** — the existing list, with a per-row trash can and *Delete all*.
* **Browse files** — the four volumes, folders and project files (`.slideshow.json`, and a
  plain `.json` so a file renamed by hand still opens) as cards showing the project name,
  the size and the modification date. Clicking a card reads it and applies the snapshot
  through exactly the code path a database row uses, then stores it as a new row (`project
  #12`), because the file is a copy rather than the project being edited. If SQLite is
  offline the load still happens and the notification says to save once the backend is
  back.

A file that is not a project is reported as such (*"not a project file"*) instead of
opened: wrong extension, empty, invalid JSON, JSON that is not an object, an object with no
`media` / `project` / `output` / `schemaVersion` key, or larger than 32 MB.

## What lands on disk

The snapshot the editor already keeps — the same JSON a SQLite row stores — pretty-printed
so it can be diffed and hand-edited:

```jsonc
{
  "schemaVersion": 1,
  "project": { "name": "Portugal summer", "randomOrder": false },
  "media":   [ { "id": 1, "type": "image", "path": "/photos/trip/a.jpg",
                 "duration": 4, "crop": { /* … */ }, "filter": "mono" } ],
  "output":  { "path": "/output", "filename": "Portugal summer" },
  "soundtrack": {}, "textDefaults": {}, "timeline": {}
}
```

Media paths stay **UI paths** (`/photos/…`), so a project file is portable between
installations that mount the same volumes — no absolute host paths are baked in.

Writes are **atomic**: a temporary file in the destination folder, then `os.replace`. A NAS
never holds a half-written project, and no `.part` leftovers survive.

## API

| Call | Purpose |
| --- | --- |
| `GET /api/media/browse?root=&path=&projects=true` | folders + project files for the picker (`kind: "directory"` / `"project"`); with `folders=true` the same endpoint keeps listing folders only, which is what the Output destination picker uses |
| `GET /api/project-files?root=&path=` | one file: `{root, path, name, projectName, size, modified, items, project}` |
| `POST /api/project-files` | `{root, folder, filename, overwrite, project}` → `201 {root, path, name, folder, size, overwritten, items}` |

| Situation | Answer |
| --- | --- |
| folder contains `..`, or an unknown volume | `400` |
| saving to `/photos`, `/videos`, `/music` | `403` (read-only mount) |
| the file exists and `overwrite` is false | `409 {"code": "project_exists", "path": …}` |
| missing file | `404` |
| the file is not a project | `422` |
| a media path outside the mounts | `422` (the same `validate_mount_references` the project endpoints use) — and nothing is written |

## Staying inside the container

* **Only `/output` accepts a save.** `WRITABLE_ROOTS = ("output",)` in
  `backend/app/project_files.py`, mirrored by `src/projectFiles.ts` for the picker.
* **A filename can never leave its folder.** Only the last path component is kept
  (`../../evil` → `evil.slideshow.json`, `/etc/passwd` → `passwd.slideshow.json`), the
  characters no filesystem accepts (`\ / : * ? " < > |` and control characters) become a
  dash, edge dots/spaces/dashes are trimmed (Windows drops them silently, which would make
  the saved file impossible to find), the stem is capped at 80 characters, and the folder
  goes through the media browser's `safe_path()` traversal guard.
* **Nothing is read or written outside the mounts** — every path is resolved against a
  configured root and re-checked with `is_relative_to()` afterwards.

## Both halves agree

The filename rules exist twice (the picker promises a name live, the backend enforces it),
so `src/projectFiles.ts` mirrors `backend/app/project_files.py` statement for statement —
`projectFileStem()` / `safe_stem()` — and one of the new tests reads the TypeScript file and
fails if `PROJECT_SUFFIX`, `WRITABLE_ROOTS`, `MAX_NAME_LENGTH`, the illegal-character class
or the edge-trimming pattern ever drift apart.

Files: `backend/app/project_files.py`, `backend/app/media.py` (`projects` flag on
`browse()`), `backend/app/main.py` (the two endpoints), `src/projectFiles.ts`,
`src/ProjectFileBrowser.tsx`, `src/App.tsx` (Save button + the tabbed loader),
`backend/tests/test_project_files.py` (34 tests: round trip, overwrite handshake,
read-only mounts, traversal, atomicity, error taxonomy, endpoints, TypeScript parity).
