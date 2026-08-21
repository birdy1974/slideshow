# Slideshow Application - Changes Summary

## Date: 2026-08-21

This document summarizes all the fixes and enhancements made to the slideshow application based on user feedback.

---

## ✅ Fixed Issues & Enhancements

### 1. **Fixed NameError: name 'source_path' is not defined during generation / project creation**
**Problem**: When saving projects, generating previews, or starting renders, the FastAPI backend threw an HTTP 500 error: `NameError: name 'source_path' is not defined` at `validate_mount_references` in `backend/app/main.py`.
**Fix**:
- Defined and exported `source_path` in `backend/app/media.py`.
- Imported `source_path` in `backend/app/main.py` and `backend/app/renderer.py`.
- Added unit tests in `backend/tests/test_media.py` to verify `validate_mount_references`.

**Files modified**:
- `backend/app/media.py`
- `backend/app/main.py`
- `backend/app/renderer.py`
- `backend/tests/test_media.py`

---

### 2. **Photo thumbnails and popup preview not showing anything**
**Problem**:
- The popup preview modal checked `item.src`, which is empty when projects are loaded from SQLite or refreshed, resulting in a blank modal.
- Video clips in the timeline and preview only attempted to render `<img>` tags instead of `<video>` tags.
- Direct paths and media root URLs were not properly resolved in all cases.
**Fix**:
- Updated `itemThumbUrl` in `src/App.tsx` to handle all path formats (full paths, relative paths, URLs, and static media).
- Updated the `Preview` popup component to use `itemThumbUrl`, render `<video>` elements for video clips, and support animated auto-advancing slides when playback is active.
- Updated filmstrip and timeline thumbnails (overview and list) to support both image and video elements.
- Updated CSS in `src/styles.css` for `.thumb video`, `.overview-clip video`, and `.preview-filmstrip video`.

**Files modified**:
- `src/App.tsx`
- `src/styles.css`

---

### 3. **Move "New project" button inside storyline pane (next to "Delete selected" button)**
**Problem**: The "New project" button was located in the top project heading.
**Fix**:
- Moved the "New project" button into the Storyline panel toolbar (Section 01 Storyline) right next to the "Delete selected" button.
- Retained the confirmation dialog when starting a new project if the storyline contains items.

**Files modified**:
- `src/App.tsx`

---

### 4. **Move "Clear all" button next to "Load project" button**
**Problem**: The "Clear all" button was located in the top eyebrow line.
**Fix**:
- Moved the "Clear all" button to the main action buttons in the top heading right next to the "Load project" button.
- Retained the confirmation dialog to safely wipe all projects and temporary files.

**Files modified**:
- `src/App.tsx`

---

### 5. **Add "Clear output" button in output pane with backend directory cleanup**
**Problem**: No way to clear rendered files in the output directory from the UI.
**Fix**:
- Added new backend endpoints `POST /api/output/clear` and `DELETE /api/output` in `backend/app/main.py` that securely wipe files/folders inside the output mount without escaping the directory boundary.
- Added a "Clear output" button in the header of the Output panel.
- Added a confirmation dialog to confirm before clearing the output folder.
- Shows notification toast with counts of deleted files and folders.

**Files modified**:
- `backend/app/main.py`
- `src/App.tsx`
- `backend/tests/test_media.py`

---

## 🧪 Testing

All changes have been verified to:
- Pass all 37 Python unit tests (`PYTHONPATH=backend pytest backend/tests`).
- Build frontend production assets cleanly with TypeScript check (`npm run build`).

---

## Date: 2026-08-21 (second round)

### 6. **Render no longer fails on temporarily empty files / slow ffprobe**
**Problem**: Rendering MP4 failed with `file is empty (0 bytes)` for photos and
`ffprobe timed out` for the MP3 soundtrack. On Synology these are usually
cloud-synced placeholders (on-demand sync reports 0 bytes while the real
content hydrates) and slow/networked volumes that exceed a short probe timeout.
**Fix**:
- `_probe_readable` now re-stats a 0-byte file up to `MEDIA_PROBE_RETRIES`
  times (default 2, 0.75 s apart) before declaring it empty, giving hydrating
  files time to fill in.
- The ffprobe timeout was raised from 15 s to 30 s (configurable via
  `FFPROBE_TIMEOUT`) and a timed-out probe is retried once before failing.
- Empty files are still reported clearly if they never fill, so the user knows
  to remove or replace them.

**Files modified**:
- `backend/app/renderer.py`
- `backend/app/config.py`
- `backend/tests/test_renderer.py`

---

### 7. **Photo thumbnails and picture popup now visible (and empty files visible as such)**
**Problem**: Thumbnails and the picture popup were blank — a 0-byte file streams
an empty body, which browsers render as a silently broken image.
**Fix**:
- `/api/media/file` now answers `422` for empty files instead of streaming an
  empty body, so the UI can react.
- The media browser marks empty files with an `EMPTY · 0 B` badge, excludes
  them from "Select visible files", and skips them (with a notice) when adding
  to the storyline.
- Thumbnails in the overview timeline, list, and preview filmstrip show a
  clear "unavailable" placeholder instead of a broken image.
- The picture popup (lightbox) and preview stage show a readable error message
  for empty/unreadable files.

**Files modified**:
- `backend/app/main.py`
- `backend/app/media.py`
- `src/App.tsx`
- `src/styles.css`
- `backend/tests/test_media.py`

---

### 8. **Default transition time is now 5 seconds**
**Problem**: New transitions defaulted to 1–1.2 s.
**Fix**:
- The "Transition default" control, newly added media, text frames, and legacy
  items without an explicit `transitionTime` all use **5 seconds** now.
- The renderer's fallback for missing `transitionTime` was aligned to the same
  5 s value so the on-screen estimate always matches the rendered MP4.
- The bulk "Apply to all" upper bound was raised from 5 s to 30 s.

**Files modified**:
- `src/App.tsx`
- `backend/app/renderer.py`
- `backend/tests/test_renderer.py`

---

## 🧪 Testing

- All 44 Python unit tests pass (`cd backend && python3 -m unittest discover -s tests`).
- `npm run build` (TypeScript check + Vite production build) passes.
- End-to-end preview and MP4 renders (10 photos, 5 s transitions, MP3
  soundtrack) complete successfully; the 480p preview is exactly 95 s
  (10 × 5 s holds + 9 × 5 s transitions).

---

## Date: 2026-08-21 (third round)

### 9. **Complete movie plays before the transition to the next picture**
**Problem**: Video clips were opened with `-stream_loop -1 -t <clip>`, so a
long movie was either cut short at the UI duration (default 10 s) or restarted
mid-hold. Transitions could fire before the story finished.
**Fix**:
- The renderer probes each video's native duration and expands the timeline
  hold to at least that length.
- Videos are played once (no `-stream_loop`); first/last frames are frozen with
  `tpad` only for the incoming/outgoing xfade handles.
- Adding a video in the UI probes its length via a metadata `<video>` element
  so the storyline estimate matches the rendered MP4.
- The lightweight client preview advances videos on `ended` instead of a fixed
  timer, so movies are not cut short there either.

**Files modified**:
- `backend/app/renderer.py`
- `backend/tests/test_renderer.py`
- `src/App.tsx`

---

### 10. **Internal Server Error on job status for some directories ("database is locked")**
**Problem**: Polling `GET /api/jobs/{id}` while a render wrote progress raised
`sqlite3.OperationalError: database is locked` → HTTP 500, especially on
network volumes where WAL mode cannot be enabled.
**Fix**:
- WAL is still enabled once at startup; when it cannot be enabled every
  connection is serialised through the in-process write lock so readers never
  race writers under rollback-journal rules.
- All public DB methods retry transient `database is locked` / `busy` errors
  with exponential backoff.
- `GET /api/jobs/{id}` returns 503 (not 500) on residual lock failures; the UI
  progress poller treats 503 as "try again" instead of aborting the render.

**Files modified**:
- `backend/app/database.py`
- `backend/app/main.py`
- `backend/tests/test_database.py`
- `src/App.tsx`
