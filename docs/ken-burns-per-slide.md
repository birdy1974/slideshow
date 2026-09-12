# Per-slide Ken Burns settings (detailed slide rows)

Date: 2026-09-12 · Status: **implemented**

Until now every photo with a "Ken Burns · …" effect used one hard-coded motion:
a fixed 1.12× zoom anchored in the centre, advancing by a fixed per-frame step
(so short slides never completed the motion and long slides stopped moving
early). The only per-slide choice was the direction.

## What a slide can set now

Open the **motion chip** on a detailed row's thumbnail (bottom-left; photos
without motion show a small dimmed chip so they can get one). The panel has:

| Setting | Field | Default | Notes |
| --- | --- | --- | --- |
| Motion | `effect` | — | Zoom in / Zoom out / Pan left / right / **up / down** (up/down are new) |
| Strength | `kenBurnsZoom` | `1.12` | Target zoom factor, 1.02 – 1.35. Presets Subtle 6 % · Normal 12 % · Strong 20 % · Dramatic 30 % plus a 1 % slider. For pans it is the constant zoom the picture glides at. |
| Focus | `kenBurnsX`, `kenBurnsY` | `50 / 50` | Zooms only: click on the small preview to choose the point the zoom moves towards (in) or away from (out), in percent of the picture. |

The preview in the panel animates the real values (CSS, same geometry as the
renderer) and can be replayed by clicking it. **Reset** returns the slide to the
project-wide defaults; a slide with custom values shows a green chip.

## Renderer

`ken_burns_settings(item)` in `backend/app/renderer.py` normalises the fields
(clamped, malformed → default) and `ken_burns_filter()` builds the `zoompan`
expression:

- progress `p = min(1, on / frames)` with `frames = hold × fps`, so the motion
  completes exactly at the end of the slide's hold regardless of its length and
  then holds still through the outgoing transition handle;
- zooms: `z = 1 + (S−1)·p` (or reversed for zoom out), window origin
  `max(0, min(iw−iw/zoom, iw·fx − iw/zoom·fx))` keeps the focus point fixed;
- pans: `z = S` constant, origin slides `(iw−iw/zoom)·p` (or `1−p`) edge to edge;
- the letterbox fit gets headroom `S` instead of the fixed 1.12, so a strong
  zoom still never reaches the picture edges.

Unset fields produce the historical 1.12× centred motion, so existing projects
render as before (bar the pacing fix). Bulk "Apply effect" / "Random" change the
motion only and leave per-slide strength/focus alone.
