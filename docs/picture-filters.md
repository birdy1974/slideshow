# Picture filters & effects — advice, catalogue and GUI options

Date: 2026-09-06 · Requested in `improvements.md`: *"on the picture preview popup add
options to edit / change the pictures by adding effect and filters
(photofilters.com), for example: make picture black and white."*

This started as the advice half of that item: which filters are worth implementing,
how to implement them so the editor and the MP4 agree, and four GUI placements to
choose from.

**Status: implemented 2026-09-06.** Chosen and built: GUI option **C** (a stacked
*Edit picture* popup), the full catalogue (18 presets + Sharpen + Pixelate), a
per-preset intensity slider, six manual sliders, filters on photos **and** movies,
bulk apply in the PHOTO SELECTION pane, and a per-picture button in the detailed
slide list. [As built](#as-built--what-shipped) below records the files and every
place the build deviates from the advice; the advice sections are kept as written so
the reasoning stays readable, with a *→ shipped* note where reality went elsewhere.

## As built — what shipped

**Storage.** Three optional fields on the media item (`src/mediaItem.ts`), saved with
the project and in SQLite like every other per-item setting:

| Field | Type | Meaning |
|---|---|---|
| `filter` | preset id (`"none"`, `"mono"`, …) | which look |
| `filterAmount` | 0…1, default 1 | how far the preset moves away from the original |
| `filterAdjust` | `{brightness, contrast, saturation, warmth, vignette, softness}` | the manual sliders, applied on top |

Missing, unknown or malformed values resolve to the identity, so a project saved by
an older version renders exactly as before.

**Files.**

| Path | Role |
|---|---|
| `registry/picture-filters.json` | single source of truth: 20 preset entries (Original + 19 looks) with their params, groups, slider ranges and steps |
| `backend/app/picture_filters.py` | resolves a look → numbers → FFmpeg chain (`picture_look`), plus the 3×3 colour matrix for warmth/grayscale/sepia/hue-rotate |
| `backend/app/renderer.py` | inserts that chain per segment, after zoompan/tpad and before `drawtext` (line ~1481) |
| `src/pictureFilters.ts` | the browser twin: same registry, `resolveLook`, `cssFilter`, `pictureFilterStyle`, `lookLabel` / `lookSummary` / `hasLook` |
| `src/usePictureLook.ts` | one canvas copy per clip (pictures decoded, movies sampled with a single seek) → CSS-filtered previews + the small pixelate proxy |
| `src/PictureLookEditor.tsx` | `PictureLookDefs` (the warmth SVG filters) and the stacked editor popup |
| `src/App.tsx` | lightbox **Filters** button + badge, editor mount, thumbnail styles, bulk apply, detailed-list button, Preview-modal stage |
| `src/styles.css` | `.look-*` editor chrome, `.thumb-look` chip, `.look-vignette` overlay |
| `backend/tests/test_picture_filters.py` | 35 tests: registry loading, look resolution, matrix identities, chain building, segment ordering |

**Deviations from the advice above** — all deliberate:

| Advice said | Shipped instead | Why |
|---|---|---|
| `grayscale` → `hue=s=`, `sepia` / `hueRotate` → `hue=h=` | one `colorchannelmixer` built by multiplying the **exact CSS spec matrices** (grayscale × sepia × hue-rotate × warmth) | FFmpeg's `hue` uses different luma weights, so Duotone would preview one way and render another. Multiplying the four spec matrices gives a single cheap filter that agrees with the browser to rounding |
| `warmth` → `colortemperature` | plain RGB gains in the same `colorchannelmixer`: R `1+0.045w`, G `1+0.012w`, B `1−0.045w`, and the identical numbers as an SVG `feColorMatrix` in the browser | `colortemperature` has no CSS twin. Warmth is quantised to the registry step (0.25), so 25 fixed `look-warmth-…` defs mounted once at app root cover the whole slider — no per-thumbnail filter explosion |
| "Pixelate — not recommended, CSS has nothing" | shipped: `scale=w/n:h/n:flags=neighbor,scale=w:h:flags=neighbor`, n = frame width / 120 blocks | the preview downsamples the clip to 120 px and shows it with `image-rendering: pixelated` — the same block size, honest on both sides. A movie's *stage* keeps playing the real file (swapping in a JPEG proxy would replace the recording with one still), so there the Pixelate chip shows the blocks and the stage shows the colour half of the look; the render has both |
| "Sharpen — drop it" | shipped as preset + slider: `unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.3+0.7k` | the render is real; the editor approximates it with a small contrast bump and says so in its note. `cas` was rejected (needs the custom FFmpeg 8.1 build), `unsharp` is universal |
| `invert` → `negate` | `format=rgb24,negate,format=yuv420p` | `negate` is an RGB filter; the explicit round trip keeps the segment's yuv420p contract |
| GUI **A** recommended | GUI **C**, with A's live-chip strip inside it | the user picked C so the pending *"cut and crop parts of the picture"* item can move into the same popup as a second tab instead of getting its own window later |

**Chain order in the render** (only the parts that differ from identity are emitted):

```
pixelate → eq (brightness/contrast/saturation) → colorchannelmixer (warmth·hue·sepia·gray)
        → negate (via RGB) → gblur (softness) → unsharp (sharpen) → vignette
```

**The editor itself.** Left: the clip wearing the look (a real `<video>` with its own
controls for movies), a vignette overlay, an `ORIGINAL` badge while comparing and a
**Show original** button — plus **hold Space** to compare on pictures, because Space
belongs to the player on movies. Right: the preset chips grouped as `Basic · Black &
white · Colour · Retro & film · Effects`, each one a live thumbnail of *this* clip,
then the Intensity slider and the six adjustments (double-click a slider to reset it).
Footer: Reset / Cancel (puts back what the clip had when the editor opened) / Done.

**Consistency checklist: all six done.** Lightbox stage (the **Filters** button turns
lime and reads `Mono · 80 % · adjusted` when a look is active); storyline clips,
compact grid and detailed-list thumbnails via one shared
`pictureFilterStyle(item)` on `MediaThumb`; the slideshow **Preview** modal stage;
and `Renderer.render()` — so the low-res preview and the accurate transition preview
inherit the look for free because they call the same renderer. Plus bulk apply in the
PHOTO SELECTION pane (the selection, else every photo and movie; text frames
excluded) and a per-picture **Filters** chip in the detailed slide list.

**Adding a preset** costs one entry in `registry/picture-filters.json` — browser and
renderer both read it, and the editor chips, badge, bulk `<select>` and the parity
tests pick it up automatically. `SLIDESHOW_FILTER_REGISTRY` points the backend at
another registry file (Docker: `/app/registry`), same convention as
`registry/transitions.json`.

**Parity check.** `resolveLook()` (TS) and `resolve_look()` (Python) were run over
131 looks — every preset at four intensities, every warmth step, each slider alone
and all six combined, plus malformed input (`null`, `[]`, `"0.5"`, out-of-range
numbers, unknown preset id). All 131 resolved to identical values. The unit test
suite additionally reads `src/pictureFilters.ts` and compares the shared magic
numbers, so editing one side of a constant fails the tests.

## The one constraint that decides everything

A filter chosen in the browser must also appear in the rendered MP4, the low-res
preview and the accurate transition preview. Those are made by FFmpeg
(`Renderer.render()`), not by the browser, so **every filter needs two
implementations that look the same**: a CSS one for the editor and an FFmpeg one
for the render. Filters that only exist on one side (oil painting, tilt-shift,
pixelate, bokeh) would make the editor lie, which is worse than not offering them.

Two consequences:

1. **Nothing is baked into the files.** `/photos` and `/videos` are read-only
   mounts, so — exactly like the existing `rotation` field — a filter is stored as
   a small set of numbers on the media item and applied on the fly. Originals are
   never touched, and a filter can always be reset.
2. **One parameter model, two renderers.** Presets are not hand-written CSS
   strings plus hand-written FFmpeg strings (those drift apart). A preset is a set
   of numbers; `pictureFilters.ts` turns them into a CSS `filter:` string and
   `renderer.py` turns the same numbers into a filter chain. Adding a preset then
   costs one line, and the manual sliders fall out for free.

## Parameter model (the whole vocabulary)

| Parameter | Identity | CSS (editor) | FFmpeg (render) | Parity |
|---|---|---|---|---|
| `brightness` | 1 | `brightness(b)` | `eq=brightness=(b-1)*0.5` | good, calibrate the 0.5 factor once |
| `contrast` | 1 | `contrast(c)` | `eq=contrast=c` | very good (both pivot around mid-grey) |
| `saturation` | 1 | `saturate(s)` | `eq=saturation=s` | very good |
| `grayscale` | 0 | `grayscale(g)` | `hue=s=1-g` | very good (luma weights differ slightly) |
| `sepia` | 0 | `sepia(p)` | `colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131`, blended at `all_opacity=p` | very good |
| `hueRotate` | 0° | `hue-rotate(d)` | `hue=h=d` | good (CSS uses a matrix approximation) |
| `warmth` | 0 (6500 K) | warm: `sepia(.12w) saturate(1+.08w)`; cool: `hue-rotate(-9w deg) saturate(1+.05w)` | `colortemperature=6500-1200w` | acceptable — the only approximate one |
| `vignette` | 0 | overlay div, `radial-gradient(ellipse at 50% 50%, transparent 42%, #000 100%)` at `opacity=.6v` | `vignette=PI/4*v` | good; in the render it also darkens the blurred letterbox bars, which is what you want |
| `softness` | 0 | `blur(r)` | `gblur=sigma=r/2` | good after one calibration (CSS px ≠ FFmpeg sigma) |
| `sharpen` | 0 | *none* — see below | `cas=0.4s` (or `unsharp=5:5:.8s`) | poor: CSS has no sharpen |
| `invert` | off | `invert(1)` | `negate` | exact |

`sharpen` is the only parameter without a CSS twin. Either drop it, accept a
`contrast(1.05)` stand-in in the editor, or match it properly with an inline SVG
`feConvolveMatrix` kernel referenced as `filter:url(#sharpen)` (works in all three
browsers, costs some CPU on 24 MP pictures). Recommendation: drop it from the
preset list and keep `cas` as a render-time "extra sharpness" only if you ask for it.
*→ shipped as the middle option: `unsharp` in the render, a labelled contrast bump in
the editor (see As built).*

Everything is scaled by one **`amount`** (0–100 %): every parameter moves from its
identity value toward the preset value by that factor, on both sides. That gives a
per-filter intensity slider for free.

## Recommended catalogue

### Tier 1 — core (10, always visible)

| Preset | Recipe |
|---|---|
| Original | — |
| **Mono** (black & white) | `grayscale 1` |
| **Noir** | `grayscale 1, contrast 1.4, brightness .93, vignette .55` |
| **Silver** (soft B&W) | `grayscale 1, brightness 1.07, contrast .94` |
| **Sepia** | `sepia .9` |
| **Vintage** | `sepia .35, saturation .82, contrast .93, brightness 1.06, vignette .3` |
| **Warm** | `warmth +2, saturation 1.08` |
| **Cool** | `warmth −2, saturation 1.05` |
| **Vivid** | `saturation 1.5, contrast 1.1` |
| **Faded** | `saturation .72, contrast .84, brightness 1.1` |

These cover the example in the request (black & white), the four looks people
actually use on holiday slideshows (warm / cool / vivid / faded), and the three
retro ones (sepia / vintage / noir). All ten are per-pixel operations: negligible
extra render time.

### Tier 2 — character (+8, behind a "More" tab or further along the strip)

| Preset | Recipe | Note |
|---|---|---|
| Polaroid | `sepia .15, brightness 1.1, contrast .88, saturation 1.05, vignette .22` | lifted, warm, soft corners |
| Duotone (blue/gold) | `grayscale 1, sepia 1, hueRotate 165, saturation 2` | strong graphic look |
| Infrared | `hueRotate 180, saturation 1.7, contrast 1.1` | surreal foliage |
| Sunset | `warmth +3, saturation 1.35, brightness 1.03, vignette .2` | golden hour rescue |
| Cinema | `warmth +1, contrast 1.14, saturation 1.12, brightness .97, vignette .25` | teal-and-orange-ish |
| Soft focus / Dream | `softness 1.6, brightness 1.05, saturation 1.12` | the only `gblur` preset — the expensive one |
| Vignette | `vignette .8` | just the corners |
| Negative | `invert 1` | cheap and occasionally fun |

### Tier 3 — manual adjustments (sliders, on top of any preset)

`brightness`, `contrast`, `saturation`, `warmth`, `vignette`, `softness`, plus
`amount` for the preset itself. All map to the table above, so they render exactly
as they preview.

### Not recommended

| Effect (photofilters.com) | Why not |
|---|---|
| Oil paint, Pointillism, Smear, Ripples, Bokeh | No FFmpeg equivalent that is both faithful and affordable per frame; no CSS equivalent at all — the editor could not show it. |
| Tilt-shift | Needs a gradient-masked blur: `gblur` + `gradients` mask + `blend` per frame. Expensive on a DS918+, and CSS can only fake it with an SVG mask. |
| Pixelate | FFmpeg has `pixelize`, CSS has nothing (canvas/SVG hack only). Preview would lie. *→ shipped anyway: the scale-down/scale-up trick previews honestly on pictures, see As built.* |
| Red-eye removal, lens distortion | Needs faces/lens profiles; not a slideshow feature. |
| RGB curves / levels / channel mixer UI | Big UI, and per-item `curves` strings are hard to keep in sync with a CSS preview. The warmth + contrast + saturation sliders cover 95 % of the use. |
| Posterize / colour quantization | Per-item `palettegen`+`paletteuse` is slow; CSS has no twin. |
| Local edits (brush, masks, crop-as-filter) | Different feature — that is the separate "cut and crop parts of the picture" item. |

## GUI options

**A — Live thumbnail strip inside the lightbox (recommended).**
A horizontal strip under the picture: `Original · Mono · Noir · Sepia · Vintage ·
Warm · Cool · Vivid · …`, each chip a 56×38 thumbnail of *this* picture with the
filter applied and its name underneath; the active chip gets the lime outline. A
trailing **Adjust** chip unfolds one slim row of sliders (Tier 3) + `amount`.
Hold **Space** to see the original (photofilters.com does this too).
- Pros: you see the result on your own picture before committing; one click; no new
  window; the strip is also the natural home for the future crop button.
- Cons: costs ~78 px of height (the stage drops from 640 px to ~560 px, still well
  inside `calc(100vh - 120px)`). Perf: the chips must **not** be 18 copies of a
  24 MP JPEG — draw the picture once into a small offscreen canvas (~240×160) and
  use that data-URL for every chip, so the CSS filters run on tiny bitmaps.

**B — Docked right-hand panel.** Widen the lightbox to `min(1280px, 96vw)`,
picture left, 240 px panel right with grouped filter names and always-visible
sliders.
- Pros: everything visible at once, most room to grow (tabs for Adjust / Crop).
- Cons: real rework of the modal; on a 1366×768 laptop the picture gets narrow;
  name-only browsing unless the panel also carries thumbnails (back to A's perf note).

**C — Stacked "Edit picture" popup** (the pattern the movie Cut/Crop editor already
uses: it opens on top of the lightbox and returns to it).
Big before/after stage, the strip from A, sliders, Reset / Done. A **Filters**
button joins Rotate and Cut in the lightbox header.
- Pros: most room, keeps the lightbox clean, consistent with the movie editor, and
  the pending *"cut and crop parts of the picture"* item can move in as a second tab
  instead of getting its own window later.
- Cons: one extra click; two modals deep.

**D — Select in the lightbox header.** A `<select>` next to the rotate buttons,
applied live on the big picture.
- Pros: ~30 lines, zero layout risk.
- Cons: no browsing by eye, no sliders — it would not feel like photofilters.com.

**Chosen: C** (with A's live-chip strip inside it). **Recommendation had been A**,
because the request says *"on the picture preview popup"*
and because seeing the filter on your own picture is the whole point. If the
cut/crop item is next on your list, choose **C** and put A's strip inside it — that
builds the container once for both features. A and D combine well later (a filter
`<select>` in the detailed slide list for quick per-row changes).

Either way the header gets a small badge when a picture is filtered (e.g. `Mono ·
80 %`) so the storyline shows which frames are edited, and the **PHOTO SELECTION**
bulk pane can gain an "apply filter to selection" control — the same pattern as the
existing bulk Ken Burns / transition applies.

## Where the look has to show up (consistency checklist)

1. Lightbox stage — CSS filter on the `<img>` + vignette overlay.
2. Storyline clips, compact grid and detailed-list thumbnails — one shared
   `pictureFilterStyle(item)` helper on `MediaThumb`.
3. Slideshow **Preview** modal (`img.slow-zoom`).
4. `Renderer.render()` — insert the chain **after** zoompan and **before** drawtext:
   the caption stays crisp and white, the blurred letterbox backdrop picks up the
   same look, and the low-res preview and the accurate transition preview inherit
   it automatically because they call the same renderer.
5. Project save/load and SQLite — free, the numbers live on the media item.
6. Optional: bulk apply, optional filter column in the detailed list.

## Cost on the NAS

Filters run on 1080p frames after zoompan, once per segment. `eq`, `hue`,
`colorchannelmixer`, `negate` are cheap per-pixel operations (≈1–2 % each on a
DS918+); `vignette` is moderate; `gblur` (the Soft focus preset and the `softness`
slider) is the only real cost — worth a line in `renderEstimate.ts` if it gets used
a lot. No filter needs a second input or a second pass, so nothing changes about
segment caching or the transition compose step.

## Testing

`backend/tests/test_picture_filters.py` (35 tests, `.venv/bin/python -m pytest
backend/tests/`):

- **Registry** — every preset resolves, ids are unique, `none` is inert, and the
  TS↔Python magic numbers (divisor, warmth gains, blur factor, vignette opacity,
  warmth step) match by reading `src/pictureFilters.ts`; both sides must load the
  same registry file.
- **Resolution** — amount scaling, adjustment multiplication vs. shifting, clamping,
  and malformed input falling back to the identity.
- **Colour matrix** — grayscale/sepia/hue-rotate reproduce the CSS spec matrices
  element by element, warmth is the diagonal gain, no preset produces runaway
  coefficients.
- **Chain** — one filter per parameter, nothing emitted at identity, `eq` only lists
  sub-values that actually differ, and the documented order holds.
- **Segment integration** — the look lands after zoompan/tpad and before `drawtext`,
  text frames are skipped, movies are filtered, and an item with `filter:"none"` /
  `filterAmount:0` produces a byte-identical FFmpeg command to an untouched one.

There is still no frontend test runner in the repo, so the browser side is covered by
the shared-registry + shared-constant tests and by the one-off 131-look parity run
described above (reproduce it by compiling `src/pictureFilters.ts` with the repo's
`tsc` and diffing `resolveLook` against `resolve_look` for the same input list).
