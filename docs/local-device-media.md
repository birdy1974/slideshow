# Local files from the GUI device (photos & movies) — feasibility & options

Date: 2026-09-11 · Status: **implemented** (photos & movies, dedicated /uploads volume)

Question from improvements.md: "give option if it is possible to use local file
from device that is using the gui as well" — i.e. photos and movies sitting on the
laptop/phone/tablet that opens the GUI, without first copying them into a mounted
folder.

**Short answer: yes, it is possible and the architecture is ready for it.** One
physical requirement is unavoidable: the render happens on the NAS, where FFmpeg
reads source files from disk — so a "local file" must be **uploaded** to the NAS
once. After that it behaves exactly like a file from `/photos`. There is no
browser-only path (a blob URL in the tab cannot be read by a server-side FFmpeg).

## What the current architecture gives us (verified)

- Media mounts are deliberately **read-only** (`:ro` in `compose.yaml`): the app
  never writes into your photo archive. Writable volumes today: `/config`
  (SQLite, caches, work) and `/output` (renders, saved project files).
- **`Settings.media_roots`** (`backend/app/config.py`) is the single choke point
  for *everything*: folder browsing, `/media/{root}/…` streaming (thumbnails,
  lightbox, proxies), render source resolution, `safe_path` traversal protection
  and `validate_mount_references`. Adding one `"uploads": …` entry makes every
  subsystem accept uploaded files with **no other pipeline changes** — the
  renderer, project save/load, thumbnails and proxies all inherit it.
- The media picker (`MediaBrowser`) already has a root-tab system
  ("All media / photos / videos"); an *uploads* tab slots into the same UI.
- `python-multipart` is **already in requirements.txt** — FastAPI's multipart
  upload dependency — so no new backend dependency is needed.
- Uploads are same-origin POSTs (the SPA is served by the same FastAPI app on
  port 8080; in dev, Vite proxies `/api`), so no CORS changes are required.

## The zero-development alternative (worth stating first)

The mounts are read-only *to the app*, not to *you*: the underlying DSM shared
folders can be filled via **File Station, Synology Drive or SMB** from any device.
Copy the files into the photos/videos folder, reopen the picker — done. For "my
holiday photos are on the laptop" this is often good enough. The in-GUI upload
wins on: phones/tablets (no DSM login, camera-roll picker), one uninterrupted
story-building flow, other users/guests on the LAN, and everything happening in
one screen.

## Storage decision — where uploads live

| Option | Compose change | Pros | Cons |
|---|---|---|---|
| **A [recommended] — dedicated `/uploads` volume** | `${UPLOADS_PATH:-./data/uploads}:/uploads` | Sources stay separate from renders *and* from app state; independent backup/wipe; visible as its own DSM folder; mounts rw like `/output` | One new compose line + `.env.example`/docs entry; existing installs add the volume once |
| B — `/config/uploads` | none | Zero compose change; config volume already persists | Config volumes are typically small and included in SQLite-centric backups — GBs of movies there are a trap; mixes sources with app state |
| C — `/output/uploads` | none | Reuses an existing writable volume | **"Clear output" (one click, existing button) would delete source files**; sources and renders mixed — not recommended |

With option A the only backend change is the `media_roots` entry plus a settings
env (`UPLOADS_DIR`, default `/uploads`); in non-Docker/dev setups it can default
to `<config>/uploads` so a bare checkout keeps working.

## Upload design

- `POST /api/media/upload` — multipart, one or many files per request; streamed
  to disk (FastAPI spools large files), never fully buffered in RAM.
- Validation on arrival, reusing existing patterns:
  - filename sanitised with the **same rules as project files**
    (`safeFilename`, mirrored front/back — `project_files.py` /
    `src/projectName.ts`), auto-deduped (`beach.jpg` → `beach-2.jpg`);
  - extension allowlist = `IMAGE_EXTENSIONS | VIDEO_EXTENSIONS` (already defined
    in `media.py`); audio can join later for soundtracks;
  - **ffprobe verification** (the app already shells out to it everywhere):
    confirms the container really decodes — a text file renamed `.mp4` is
    rejected with the reason, and the probe result (duration, codec) feeds the
    picker exactly like `/api/media/probe` does for mounted files;
  - size cap per file via env (`UPLOAD_MAX_GB`, default ~4 GB) and a total
    uploads quota so a phone dump cannot fill the NAS volume silently.
- Progress/cancel: `XMLHttpRequest` (not `fetch`) gives upload progress events —
  per-file bar, MB/s, cancel button, sequential queue so Wi-Fi is not saturated.
- Chunked/resumable upload (tus-style) is a clean phase-2 for multi-GB movies on
  flaky Wi-Fi; the endpoint shape above does not block it.

## GUI integration

- **Media picker**: an "Uploads" location button next to photos/videos, and an
  **Upload from this device** section with **Choose files…** (multi-select with
  Ctrl/Cmd/Shift, camera roll on iOS/Android) and **Choose folder…** (whole
  directory incl. subfolders, via `webkitdirectory`). Picks are *staged* first —
  a review list with size, remove-per-file, and an explicit **Upload N files**
  button — so several picks can be combined and nothing leaves the device
  until the button is pressed. Non-media files a folder pick brings along
  (`.xmp`, `.aae`, `Thumbs.db`, `.DS_Store`) are dropped quietly and counted.
- **Pre-flight check**: `/api/health` reports `uploads: { writable, reason,
  maxMb }`. When the uploads volume is not writable by the app user the picker
  shows the reason (with the uid:gid it runs as) and the Upload button stays
  disabled — the classic first-run failure is a root-owned `UPLOADS_PATH`
  folder that Docker created while the container runs as `PUID:PGID`.
- **Drag & drop**: drop files from the desktop straight onto the storyline /
  compact grid; a drop overlay ("Drop 6 files to upload") starts the same queue,
  and files land in the storyline in drop order.
- **Upload tray**: a small progress strip (per file: name, bar, MB, cancel)
  bottom-right while a batch is in flight; finished files auto-add to the
  storyline like picker selections do. Successful rows fade after a few
  seconds; **failed rows stay, with the rejection reason printed inline**,
  until the tray is cleared.

## Troubleshooting "I pick a file and nothing happens"

1. Open the picker → *Upload from this device*. If an amber box says the
   uploads folder is not writable, fix ownership on the NAS:
   `sudo chown -R PUID:PGID /volume1/docker/slideshow/uploads` (the same
   PUID/PGID as in `.env`), or create the folder before the first
   `docker compose up` so Docker does not create it as root.
2. Look at the upload tray bottom-right: a red row shows the exact reason
   (`not a supported photo or movie type`, `larger than the N MB upload
   limit`, `Not a valid movie: …` from ffprobe, HEIC is not supported).
3. Behind a reverse proxy (DSM Application Portal / nginx) a large movie may
   fail with `Upload failed (413)`: raise `client_max_body_size` on the proxy.
   The app itself accepts up to `UPLOAD_MAX_MB` (default 4096).
- Everything downstream is untouched: thumbnails, lightbox, cut/crop, filters,
  rotation, transitions, render — an uploaded file *is* a normal media item
  (`path: "/uploads/beach.jpg"`).

## Security & operational notes

- This would be the **first endpoint that writes browser data to disk**; the app
  has no authentication (LAN tool by design). Mitigations: uploads are confined
  to their own volume, extension allowlist, size cap, sanitised names, ffprobe
  check; expose nothing beyond the LAN port. A token or DSM-style auth is a
  separate, orthogonal feature if the app is ever exposed beyond the LAN.
- **HEIC caveat**: iPhone photos default to HEIC; browsers cannot thumbnail it
  and FFmpeg cannot decode HEIF/HEIC (patent-encumbered, not in any build).
  Advise "Most Compatible" (JPG) on iOS or convert before upload; the upload
  validation will reject HEIC with a clear reason instead of failing later.
- Projects referencing `/uploads/…` are per-installation, exactly like
  `/photos/…` references — the project-file documentation already words this
  caveat generically ("portable between installations *that mount the same
  media*").

## Implemented (2026-09-11)

- **Storage**: dedicated `/uploads` volume — `compose.yaml` mounts
  `${UPLOADS_PATH:-./data/uploads}:/uploads` and sets `UPLOADS_DIR=/uploads`;
  without the env (bare checkout, dev) `Settings.__post_init__` falls back to
  `<config>/uploads`. Per-file cap `UPLOAD_MAX_MB` (default 4096) is wired
  through compose and `.env.example`.
- **Backend**: `backend/app/uploads.py` — `sanitized_name` (project-file rules,
  photo/movie extension allowlist), `unique_name` (case-insensitive dedupe),
  streaming `store_upload` (size cap while writing, 0-byte reject) and
  `probe_media_file` (ffprobe must see a video stream, so renamed junk and
  truncated downloads are rejected with a reason — rejected files never stay
  on disk). `POST /api/media/upload` in `main.py` returns media-browser-shaped
  entries. The `uploads` root joined `media_roots`, so browsing, `/media/…`
  streaming, thumbnails, cropdetect, probe and render resolution all accept it
  with no pipeline changes.
- **GUI**: an *uploads* location and an **"Upload from this device"** button in
  the media picker (native camera-roll picker on phones; the picker reloads
  the uploads root when a batch finishes), **drag & drop** of files from the
  desktop onto the storyline (overlay hint), and a bottom-right **upload tray**
  with per-file progress, MB counters and cancel. Completed files are added to
  the storyline immediately through the same path as mounted files
  (`addFilesToStoryline`). `src/uploads.ts` does XHR uploads (progress events)
  one file per request.
- **Tests**: `backend/tests/test_uploads.py` (10 cases: sanitising, dedupe,
  kind detection, cap, empty, probe-failure leaves no trace, config-dir
  fallback, browsability) against an ffprobe stub that refuses unknown files
  like the real binary. Full suite 286/286 green.
- **Not yet** (deliberate): audio uploads for soundtracks, chunked/resumable
  movies, zip-batch import, per-file delete from the uploads root (wipe via
  DSM or the volume folder for now).
