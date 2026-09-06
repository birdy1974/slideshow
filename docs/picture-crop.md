# Picture cut & crop — the second tab of the *Edit picture* popup

Date: 2026-09-06 · Requested in `improvements.md`: *"on the picture preview popup add
options to cut and crop parts of the picture."*

Built as chosen: **all four** cutting tools, user-selectable, in a **second tab** of the
existing *Edit picture* popup, for **photos and movies**, and the cropped picture shows
**everywhere** — storyline, compact grid, detailed slide list, filmstrip, lightbox,
Preview and the MP4.

The one rule this feature had to keep is the same rule the picture filters keep: the
files on `/photos` and `/videos` are read-only mounts and are **never** rewritten. A cut
is a handful of numbers on the media item; `src/pictureCrop.ts` turns them into canvas
and CSS for the browser, `backend/app/picture_crop.py` turns the *same* numbers into
FFmpeg filters for the render. Where the browser can only approximate, it says so in the
editor.

## What is stored

One optional field on the media item (`src/mediaItem.ts`), saved with the project and in
SQLite like every other per-item setting:

```jsonc
"crop": {
  "rect":    { "x": 0.12, "y": 0.05, "w": 0.8, "h": 0.72 },  // fractions, top-left + size
  "degrees": -2.5,                                            // straighten, −15 … +15
  "lasso":   [[0.21, 0.33], [0.62, 0.28], [0.48, 0.71]],       // fractions of the *cropped* view
  "feather": 0.35                                              // softness of the cut-out edge, 0…1
}
```

Every part is optional. A missing, malformed or out-of-range value falls back to "no
crop" on both sides, and a full frame with nothing to straighten resolves to `undefined`
— so a project saved before this feature renders byte-identically, and "Reset crop"
really does put the item back to the untouched file.

**Coordinate space** (identical in the browser, the editor and FFmpeg):

1. the picture as stored, turned by the item's whole-quarter `rotation` (`transpose=1`
   …) — a portrait photo is cropped the way it is shown;
2. straightened by `degrees` and zoomed to the largest **inscribed** rectangle, so a
   turned corner is never visible;
3. `rect` — fractions of that straightened view;
4. `lasso` — a polygon in fractions of the *cropped* view; its interior is cut away and
   filled with a blurred copy of the same picture.

Constants shared by both implementations (the test suite reads them out of the
TypeScript source and compares):

| Constant | Value | Meaning |
|---|---|---|
| `MAX_STRAIGHTEN` | 15° | levelling, not rotating — the quarter turns live in `rotation` |
| `MIN_CROP` | 0.05 | a crop smaller than 5 % is a mistake, not a composition |
| `MIN_LASSO_POINTS` / `MAX_LASSO_POINTS` | 3 / 24 | a polygon needs corners, and stays draggable |
| `DEFAULT_FEATHER` | 0.35 | the hole edge is soft by design |
| `LASSO_MASK_SIZE` | 512 px | the PGM mask the render stretches to the frame |

## Files

| Path | Role |
|---|---|
| `backend/app/picture_crop.py` | normalisation, the inscribed-rectangle maths, the `crop=`/`rotate=` filters, the PGM mask writer, the lasso `filter_complex`, the cropdetect parser and probe command |
| `backend/app/renderer.py` | runs the crop **before** the frame fit (line ~1436 prefix list) and switches to `-filter_complex` + a mask input when a lasso is present (line ~1497) |
| `backend/app/main.py` | `GET /api/media/cropdetect` — the "Black bars" tool |
| `src/pictureCrop.ts` | the browser twin: `normalizeCrop`, `cropGeometry`, `inscribedZoom`, `cropPaintPlan` (canvas), `cropSpriteStyle` (live video), `lassoBoxPolygon`, `cropSummary` / `cropLabel` |
| `src/usePictureCrop.ts` | `useCroppedSource()` — one cached canvas copy per clip per tier |
| `src/PictureCropEditor.tsx` | `PictureCropPanel` (the tab itself) and `CropSpriteVideo` (a playing movie wearing its crop) |
| `src/PictureLookEditor.tsx` | now a two-tab shell: *Filters & effects* \| *Cut & crop*, one Cancel for both |
| `src/App.tsx` | lightbox **Crop** button, detailed-list crop chip, crop proxies on every thumbnail, Preview stage, the cropdetect client |
| `src/styles.css` | `.editor-tabs`, `.crop-*` chrome, `.crop-window` |
| `backend/tests/test_picture_crop.py` | 69 tests: parity, geometry, masks, graphs, cropdetect, segment ordering, endpoint |

## The four tools

**Crop frame.** A rectangle with eight handles, draggable anywhere on the picture;
clicking outside the frame recentres it on the click. Aspect presets — Free · Original ·
Frame (16:9) · 16:9 · 4:3 · 3:2 · 1:1 · 9:16 — keep the centre and clamp to `MIN_CROP`,
and a corner drag with a preset locked grows from the opposite corner. The readout says
what stays: `1536 × 864 px of the picture · 62 % of its area`.

**Straighten.** A −15…+15° slider (0.1° steps, published on release so the drag stays
smooth). The stage zooms by exactly the factor the render zooms, and the panel prints it:
*"The picture zooms to 108 % so the turned corners never show."* A rule-of-thirds grid
appears inside the frame while this tool is active.

**Cut out.** Click to place polygon points, drag a point to reshape it, Backspace (or
*Undo point*) removes the last one, *Clear cut* drops the polygon. Up to 24 points. The
hole is filled live on the stage with a blurred copy of the same picture (pictures only),
and the **Feather** slider softens the edge — in the browser by how hard the copy is
downscaled before it is grown back, in the render by the mask's `gblur` sigma.

**Black bars.** One click asks the backend to run FFmpeg's `cropdetect` on the real file
(`GET /api/media/cropdetect?root=…&path=…&rotation=…&seconds=…`), turning the picture
the way the user sees it first. The proposal comes back as fractions in *rotated* space
and can be dropped straight into `rect` with **Use this crop**; a result that covers
≥ 98.5 % of the frame is reported as "no bars" rather than stored as a crop.

A fifth element sits under the tools: **In the slideshow frame**, a 16:9 preview of the
result — the cropped picture contained over a blurred, darkened copy of itself, which is
what `fit_frame_filter` does in the render. It is rebuilt from a canvas copy 220 ms after
the last drag, so it trails the pointer by a beat instead of repainting on every move.

## The render

Per segment, the crop is a prefix that runs **before** the frame fit, so the blurred
letterbox backdrop, the Ken Burns zoom, the picture look and the caption all see the
picture exactly the way the editor shows it:

```
transpose=<rotation>?,rotate=a=<rad>:ow=iw:oh=ih:fillcolor=black:bilinear=1?,
crop=w=trunc((…)/2)*2:h=trunc((…)/2)*2:x=trunc((…)/2)*2:y=trunc((…)/2)*2?,
<fit/fill to 1920×1080>, zoompan?, tpad?, <picture look>, drawtext?, format=yuv420p, …
```

Two details that matter:

* **Even truncation.** `w`/`h`/`x`/`y` are wrapped in `trunc((…)/2)*2` so every value is
  an even pixel count — `crop` on yuv420p refuses odd sizes, and a 24 MP photo and a 480p
  movie both have to work without probing. The expressions use only `iw`/`ih`, so no
  extra FFmpeg run per clip is needed.
* **Straightening and cropping are one resample.** `rotate` keeps the canvas
  (`ow=iw:oh=ih`) and the crop takes the inscribed rectangle out of it, so a straightened
  picture is never scaled twice:

  ```
  w = min(iw²/(iw·C+ih·S), iw·ih/(iw·S+ih·C))      C = cos θ, S = sin |θ|
  h = min(iw·ih/(iw·C+ih·S), ih²/(iw·S+ih·C))
  x = (iw − w)/2 + w·rect.x        y = (ih − h)/2 + h·rect.y
  ```

  with `zoom = max(C + S/a, a·S + C)`, `a = iw/ih` — the formula `inscribedZoom()` in the
  browser carries, checked in the tests against a brute-force corner search *and* against
  these emitted expressions.

**The cut-out** needs a second input, which `-vf` cannot express, so a lasso switches the
segment to `-filter_complex`. The polygon is written as a greyscale PGM next to the
segments (`mask-0000.pgm`, white = the hole, edges covered analytically per row so a
512² mask costs tens of milliseconds) and fed in as a bounded still stream
(`-loop 1 -framerate <fps> -t <duration> -i mask.pgm`, frame-aligned with the picture):

```
[0:v]<crop prefix>[cutpre];
[1:v]format=gray,gblur=sigma=<feather·512/42>[cutmask0];
[cutmask0][cutpre]scale2ref=flags=bicubic[cutmask][cutpic];
[cutpic]split=2[cutkeep][cutblur];
[cutblur]scale=640:-2,gblur=sigma=16[cutsoft0];
[cutsoft0][cutkeep]scale2ref=flags=bicubic[cutsoft][cutbase];
[cutsoft][cutmask]alphamerge[cutsoftalpha];
[cutbase][cutsoftalpha]overlay=0:0:format=auto[cutfilled];
[cutfilled]<fit, zoom, look, caption>[v]
```

`scale2ref` stretches the 512 px mask to whatever size the stream has at that point, so
the renderer never needs the picture's pixel dimensions. The blurred fill is made on a
640 px copy and scaled back — visually identical to a full-frame blur, far cheaper. The
hole is composited with **alphamerge + overlay** rather than `maskedmerge`: the mask's
greyscale becomes the alpha of the blurred copy, so only that copy changes format and the
kept picture is never round-tripped through RGB (`maskedmerge` on yuv420p corrupts
chroma — this was tried and rejected).

## The browser

A crop cannot be expressed as a CSS filter, so the browser pays for it the way it pays
for the Pixelate look: **one small canvas copy per clip**, drawn with the exact geometry
`cropPaintPlan()` computes (quarter turn → straighten → rectangle → cut-out fill) and
cached. The copy becomes the element's `src`, which is why every surface shows the crop
with no extra CSS. Three tiers keep memory bounded:

| Tier | Width | Cache budget | Feeds |
|---|---|---|---|
| `thumb` | 360 px | 12 MB | storyline cells, compact grid, detailed list, filmstrip, media browser |
| `result` | 720 px | 8 MB | the editor's "in the slideshow frame" preview, a cropped movie's filter chips |
| `stage` | 1600 px | 24 MB | lightbox, Preview stage, the editor's filter chips for pictures |

Oldest entries go first; the cache key is `src | rotation | crop JSON` per tier, so a drag
that changes the crop paints a new copy and leaves the old one to age out. Movies sample
one frame (a seek to 10 % of the runtime) instead of decoding a picture.

Two things never go through a proxy:

* **A playing movie** — a JPEG would replace the recording with one still. Those wear a
  CSS sprite instead (`CropSpriteVideo`): the window box gets the crop's aspect —
  measured against its parent, because a non-replaced element cannot contain-fit itself —
  and inside it the video is oversized and offset so the crop rectangle lands exactly on
  the window, with the straightening as a `rotate()` on the same element. Live, full
  resolution, no re-encode.
* **The crop editor's own stage** — it has to show the *whole* picture so the handles can
  be dragged. The stage box is sized in JS (ResizeObserver) to the exact aspect of the
  turned picture, so every percentage in the overlay is a fraction of the picture itself;
  the media element is `translate(-50%,-50%) rotate(turn + degrees) scale(zoom)` with its
  width and height swapped for 90/270, which displays the inscribed view exactly.

The order everywhere is **crop first, look second** — the renderer's order — so a filtered
*and* cropped clip previews the filter on the cropped copy, and its preset chips are
painted from that copy too.

## Honest limits

| Limit | Stated where |
|---|---|
| A cut-out cannot be shown on a *playing* movie (a live video cannot be repainted per frame). The frame and the straightening are live; the hole appears in the movie's thumbnail, in the editor's result preview and in the render. | the editor's note, and the lightbox badge |
| The browser's hole fill is a downscaled-and-grown copy, not a real Gaussian blur; `ctx.filter` is not available in Safari. The render uses `gblur`. | the editor explains the fill, not the algorithm |
| Feather maps to `sigma = feather · 512 / 42` on the mask in FFmpeg and to the shrink factor in the browser — the same *feel*, not the same number. | — |
| Pixelate stays approximate (from the filters feature) and now also previews on the cropped copy. | the filters tab's note |
| `cropdetect` measures near-black borders only; a coloured or noisy frame edge is not detected. The proposal is never applied without the user clicking **Use this crop**. | the tool's own text |

## Tests

`backend/tests/test_picture_crop.py` — 69 tests, all offline (no FFmpeg needed):

* **Parity**: the shared constants read out of `src/pictureCrop.ts`, and the inscribed
  formula matched between the TypeScript source, the Python helper and a brute-force
  geometric search.
* **Geometry**: `inscribed_zoom` vs. brute force over five aspects × seven angles; the
  emitted FFmpeg expressions evaluated numerically at four source sizes (1920×1080,
  1080×1920, 4000×3000, 640×480) against the same formula, within 2 px, always even,
  always inside the frame, using only `iw`/`ih`.
* **Normalisation**: clamping, garbage tolerance (`NaN`, strings, `{}`, `[]`), "full
  frame = no crop", degenerate polygons, the 24-point cap.
* **Masks**: PGM header and byte length, white inside / black outside, greyscale mass vs.
  the shoelace area, concave polygons, polygons touching the edge.
* **Graph**: pre/post chain order, `null` for an empty prefix, mask index, alpha
  compositing, no `maskedmerge`, feather → sigma, the bounded mask input.
* **cropdetect**: fractions from real stderr, the last proposal wins, full frame ⇒ no
  bars, the 90/270 source swap, unusable output, the probe command's `-vf` chain and
  `-t` floor.
* **Renderer**: crop before fit and before zoompan, rotation before crop, untouched items
  byte-identical to the baseline, title frames never cropped, movies cropped the same way,
  crop before the picture look, and the lasso switching the segment to
  `-filter_complex` with a real `mask-0000.pgm` written to the work directory.
* **Endpoint**: happy path, rotation forwarded to the probe, and 404 / 415 / 422 / 400 /
  503 rejections.
