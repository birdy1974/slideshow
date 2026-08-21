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
