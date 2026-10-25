# Media browser: thumbnails that never block you, plus a names-only list

Date: 2026-10-25 · Status: **implemented** (`src/mediaThumbs.ts`, `MediaBrowser` in `src/App.tsx`)

Question this closes: *"the user should be able to select a file before all thumbnails
are loaded"*, and *"in the **Choose background picture** or **Select photos & videos**
popup provide an option to show files as a list (file names only), default stays
thumbnails"*.

## The problem

Every popup that adds media to a project is the same component: `MediaBrowser`
(**Select photos & videos**, **Select collage photos**, **Choose background picture**,
**Select MP3 soundtracks**). Its grid asked each card for the **full-size original**
(`/api/media/file`) the moment the folder listing arrived. A phone-dump folder of 400
JPEGs is several gigabytes of picture traffic:

- browsers keep only a handful of connections per host, so the folder listing, the
  preview lightbox and every other API call queued **behind** the thumbnails;
- decoding megapixels on the main thread froze the popup while the pictures arrived.

The popup was therefore effectively unusable until the whole folder had loaded — even
though selecting a file is a pure state update that needs no picture at all.

## Fix 1 — a card loads its picture when it is near, and only four at a time

`src/mediaThumbs.ts` holds the two rules; `BrowserThumb` is the only consumer:

1. **Viewport first.** One shared, one-shot `IntersectionObserver`
   (`watchViewportOnce`, `THUMB_MARGIN = 240` px) wakes a card when it is about to be
   seen. Opening a 400-file folder requests the ~12 pictures on screen, not 400.
2. **Then a shared slot.** `reserveThumbnailSlot` lets at most `THUMB_SLOTS = 4`
   pictures be in flight, FIFO, so four browser connections always stay free for the
   API calls the popup itself makes. A card hands its slot back on `load`/`error`, when
   it unmounts, or after `THUMB_STALL_MS` (20 s) if the transfer stalls — a dead request
   on a sleeping NAS cannot freeze the wall.

While a card waits it renders `.thumb-pending` — a placeholder in the exact size of the
thumbnail, so nothing shifts and the caption is clickable from the first paint. Pictures
also carry `loading="lazy"` and `decoding="async"` (no main-thread decode). Folders,
music and 0-byte files are icons and never enter the queue at all
(`isThumbnailFile`), which is also what keeps the queue free of requests that could only
answer the backend's `422 File is empty`.

Nothing about *choosing* depends on any of this: selection, the preview lightbox and
**Add to storyline** work on the first frame. `backend/tests/test_media_thumbs.py` pins
the queue (a Node driver replays request/release scripts on the real `src/mediaThumbs.ts`
and Python models it independently) and the wiring in `src/App.tsx`.

## Fix 2 — a Thumbnails / List switch in the file area

The row above the listing (`.file-toolbar`) holds a `mode-toggle`:

| View | What it does |
| --- | --- |
| **Thumbnails** (default) | the familiar card grid, now loading per viewport through the queue above |
| **List** | one row per entry, **file names only** — no `<img>`/`<video>` is mounted at all, so a huge folder is complete and selectable immediately |

The default stays thumbnails; the choice is per popup session (not stored), and it
survives navigating into subfolders. The toggle sits in `MediaBrowser`, so all four
places that use it (photos & videos, collage, background picture, soundtracks) get it.

A list row keeps everything a card can do, as buttons on the row instead of as a
picture: `Select`/`Selected` (and clicking the row itself), a preview (▶ play/pause for
MP3s), the `EMPTY · 0 B` / `NO ACCESS` tags, and the uploads delete checkbox and trash
button. A movie in a picture-only popup (**Choose background picture**, **Select collage
photos**) is *not* selectable there — the row says so and previews on click instead of
selecting something **Add** would refuse.

## Tuning

`src/mediaThumbs.ts` is the only place to touch:

- `THUMB_SLOTS` — keep it between 2 and 5: more starves the API calls the popup makes
  from the same host, fewer makes the wall crawl (the tests assert that bound).
- `THUMB_MARGIN` — raise it if scrolling shows placeholders too often.
- `THUMB_STALL_MS` — how long one slow file may hold a slot.

A next step, if the grid is still heavy on a big archive: a backend image-thumbnail
endpoint (a resized JPEG next to the file, cached under `/config` like the filmstrips),
which would replace `mediaFileUrl` with `?width=480` thumbnails in `BrowserThumb`. The
queue and both views are already in place for it — only the `src` of one element changes.
