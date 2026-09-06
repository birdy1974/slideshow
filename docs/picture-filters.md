# Picture filters & effects — advice, catalogue and GUI options

Date: 2026-09-06 · Requested in `improvements.md`: *"on the picture preview popup add
options to edit / change the pictures by adding effect and filters
(photofilters.com), for example: make picture black and white."*

This is the advice half of that item: which filters are worth implementing, how to
implement them so the editor and the MP4 agree, and four GUI placements to choose
from. Nothing here is implemented yet — pick and it gets built.

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
| Pixelate | FFmpeg has `pixelize`, CSS has nothing (canvas/SVG hack only). Preview would lie. |
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

**Recommendation: A**, because the request says *"on the picture preview popup"*
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

- `backend/tests/test_renderer.py`: the chain builder (identity → no filters,
  each parameter → expected filter, ordering vs. zoompan/drawtext, amount scaling,
  videos vs. pictures).
- Frontend: a small parity page or a vitest-style snapshot of the CSS string per
  preset (there is no frontend test runner in the repo yet — a plain node script
  that prints both sides of the catalogue is enough to eyeball drift).
