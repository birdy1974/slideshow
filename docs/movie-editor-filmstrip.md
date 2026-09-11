# Movie strip / real-frame timeline in the movie editor — options

Date: 2026-09-11 · Status: **implemented** (Option C + G1, 10 cells)

Question: "is it possible to show a movie strip / timeline example in the movie
editor pop-up? e.g. five different thumbnails of the movie at 5 different
timestamps?"

**Answer: yes.** The editor already has the whole structure for it — a trim strip
with kept/cut shading, playhead, IN/OUT handles and a time ruler — but its
"filmstrip" is currently **48 abstract coloured bars** (`hsl()` swatches), not
frames. The work is replacing those bars with real pictures of the movie.

## Where the frames can come from

Verified facts that shape the options: the editor streams the movie itself via
`/api/media/file` (same-origin, and Starlette 0.47 serves it with HTTP Range
support, so seeking only downloads the needed byte ranges — a handful of seeks
cost a few MB, not the whole file), and `/api/media/probe` already proves the
"browser cannot decode this camera file, FFmpeg can" fallback pattern.

- **Option A — client-side capture (canvas grabs from the stream).**
  Seek a hidden `<video>` (or reuse the stage video) to N timestamps, draw each
  to a canvas, keep the data URLs.
  - Pros: no backend changes at all; instant for the common MP4/H.264 case;
    zero NAS CPU; range requests keep the transfer small.
  - Cons: browsers cannot decode some camera files (the known Casio Motion-JPEG
    AVI case) — capture fails or stays black; long-GOP 4K files can seek slowly
    on weak devices; the failure needs to be detected (video error / identical
    frames) to fall back.
- **Option B — server-side FFmpeg filmstrip.**
  `GET /api/media/filmstrip?root=&path=&count=&width=` renders **one small JPEG
  sprite** (N fast `-ss` frame extracts + `hstack`, or `fps=N/duration + tile`
  for short clips) and caches it on the config volume, keyed by
  path + size + mtime + count — generated once per file, then static.
  - Pros: works for **everything FFmpeg can read**, including the camera AVIs
    the browser cannot touch; one ~50 KB download; identical result for every
    client; fits the app's server-renders-the-truth philosophy.
  - Cons: backend work (endpoint + cache + tests); a brief FFmpeg run the first
    time a movie is opened.
- **Option C — hybrid [recommended].**
  Try A first (instant for normal MP4s, no NAS load); on decoder error or a
  failed/black capture, fall back to B. Exactly mirrors the existing
  `serverVideoDuration` fallback, and the AVI case silently gets the server
  image while everything else stays instant.

## GUI placement variants

- **G1 — real frames as the trim strip background [recommended].**
  The 48 bars are replaced by the frames laid edge-to-edge under the existing
  kept/cut shading, playhead and IN/OUT handles: the strip *becomes* the movie
  timeline you drag the handles on. 5 cells is the asked-for minimum; 8–12
  reads better across a wide popup (count is just a parameter).
- **G2 — separate thumbnail row.**
  A row of 5 labelled thumbs (with timestamps) above or below the strip;
  clicking one seeks the stage. The abstract strip stays as-is — least visual
  change, but two timeline graphics instead of one.
- **G3 — hover-scrub preview (YouTube-style) [later upgrade].**
  A bigger sprite (20–40 cells); moving the pointer over the strip floats the
  frame at that timestamp. The flashiest, and a natural add-on *after* B/C
  exists since it only needs more cells from the same sprite.

In every variant: clicking a thumbnail seeks the stage preview; trim handles,
shading and the ruler are untouched; the strip always shows the **whole file**,
so trimming never reshuffles the frames mid-drag.

## Implemented (2026-09-11): Option C + G1, 10 cells

- **Backend**: `backend/app/filmstrips.py` + `GET /api/media/filmstrip?root=&
  path=&count=&width=` — one FFmpeg run with N fast `-ss` seeks of the same
  file, `hstack` into a single JPEG sprite, written via a declared-muxer temp
  file and atomic replace; cached on the config volume keyed by
  root + path + size + mtime + count + width, so a changed file regenerates.
  `FilmstripUnavailable` reasons surface as 404/422.
- **Frontend**: `src/filmstrip.ts` — `captureFilmstrip` seeks one hidden
  video across the 10 slice centres and composites one canvas sprite (same
  shape as the server's); on any decode/seek failure the editor fetches the
  server sprite instead; only if both fail do the original colour bars stay
  (dimmed). The sprite fills the existing trim strip edge-to-edge
  (`object-fit: fill`) under the kept/cut shading, playhead and IN/OUT
  handles; clicking anywhere still seeks, as before.
- **Tests**: `backend/tests/test_filmstrips.py` (8 cases: command shape with
  10 seeks/hstack/`-f mjpeg`/`.part` temp, seek timestamps spanning the file,
  cache hit, mtime invalidation, per-count sprites, refused files,
  probe-failure leaves nothing cached, uploads root) against ffmpeg/ffprobe
  stubs that keep the muxer guard. Full suite 294/294 green.
