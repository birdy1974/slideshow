# Stacked text effects ("text motion"): implemented

Date: 2026-09-25 · Status: **implemented**. This covers phases P1–P4 of
[text-motion-references.md](text-motion-references.md), using the recommended
option for every decision in its §9. It also adds 29 new fonts (mostly
handwriting and script) and links text effects to the text frame's colour A → B
background.

Request (2026-09-25, after the advice): *"implement everything, add new font
(especially handwritten fonts) and integrate with the color transitions
background"*. How it was read:

* **"implement everything"**: the whole proposal (P1–P4) with the §9
  recommendations:
  * the stack + channel compositor + declarative registry, with effect ids
    stored in the project;
  * `uharfbuzz` + `fontTools`, and `Kerning: yes`;
  * migrate on load and write `textFx` only;
  * a TypeScript twin as the preview;
  * lanes + a browser aligned with the transition browser, then the full
    gallery and mini timeline;
  * the toggles become While layers, which removes the duplicate *Bouncy text*;
  * the Jitter batch first, then Prismic/copies, plus word-range targeting;
  * presets saved per browser.
* **"color transitions background"**: the text frame's second colour
  (Colour A → Colour B through any transition from the catalogue). Text effects
  can follow that change with its exact shape and timing, any layer can be timed
  to it, and the text colour can switch to a readable colour on B.
* **Fonts**: 29 new families (14 handwriting, 13 script, 2 typewriter), 49
  families in total, all drawn in their own face in a new font picker.

Examples: [demo/text-motion-production.mp4](demo/text-motion-production.mp4)
and its [poster](demo/text-motion-production-poster.png). Both are rendered by
the app's real pipeline (`Renderer.render`, exactly what "Render MP4" runs) by
[demo/make_text_motion_production_demo.py](demo/make_text_motion_production_demo.py).
The eight scenes use presets, handwriting / script / typewriter fonts and
colour A → B frames:

| Tile | Scene | Shows |
|---|---|---|
| 1 | Handwritten note (Kalam) | written letter by letter, hand-drawn boil |
| 2 | Sunrise card | circle open navy → coral; the letters inside the circle turn dark |
| 3 | Great Vibes + wipe right | script letters change colour exactly at the wipe edge; the joins stay intact |
| 4 | Ink swap (Caveat) | wipe up; the handwriting takes colour A where B has arrived |
| 5 | Memo typewriter (Special Elite) | typed with a caret |
| 6 | Reveal with colour | text exists only inside the arriving colour |
| 7 | Kinetic pop (Permanent Marker) | words pop in one by one and pulse |
| 8 | Signature (Mr Dafoe) | signed in one flowing stroke |

## 1. Data model: `MediaItem.textFx`

Each caption (text frame or picture caption) stores an ordered list of layers:

```json
"textFx": [
  {"id": "l1", "effect": "fade-up-words"},
  {"id": "l2", "effect": "bg-follow", "params": {"to": "contrastB"}},
  {"id": "l3", "effect": "pulse", "range": [1, 1], "sync": "bg"},
  {"id": "l4", "effect": "fade-out", "duration": 0.7}
]
```

| Field | Meaning | Default / limits |
|---|---|---|
| `effect` | registry id (never a label, so renames are safe) | required; unknown ids are dropped with a log line |
| `unit` | `char` / `word` / `line` / `text` | the effect's unit (only units the effect allows) |
| `duration` | seconds of an enter/exit or of one loop period | the effect's default; 0.02–120 s |
| `delay` | seconds after the window start (before the end for exits) | 0–120 s |
| `stagger` | 0–1: how much the units overlap (0 = together, 1 = one after the other) | the effect's default |
| `order` | `forward` / `reverse` / `center` / `edges` / `random` | `forward` |
| `loop` | While lane: `loop` / `pingpong` / `once` | the effect's default |
| `intensity` | scales the effect's amplitude | 1 (0–3) |
| `params` | effect parameters (colours, amounts, from/to …) | the registry defaults |
| `range` | `[first, last]` word indices the layer applies to | whole text |
| `sync` | `"bg"`: run during the frame's colour change | – |
| `muted` | kept in the stack but not drawn | false |

The browser (`normalizeTextFx`, `src/textFx.ts`) and the backend
(`parse_stack`, `backend/app/text_motion.py`) apply the same clamps.

## 2. Registry v2: `registry/text-motion.json`

It holds 15 categories, **129 effects** (59 enter, 37 while shown, 33 exit) and
**19 presets**. Both engines read it, so effects are data, not code. Adding an
effect means adding JSON; `tests/test_text_motion_twin.py` then checks it
automatically in both engines.

* **Effect**:
  * `id`, `label`, `phase` (`in` / `hold` / `out`), `category`, `symbol`, `unit`;
  * `tracks`: keyframes per channel over the unit's local time `u` ∈ [0, 1],
    with easing names and `{"rand": [a, b]}` jitter;
  * `params`: referenced as `"$name"`;
  * optional:
    * `content` (scramble, split-flap, count up/down);
    * `caret`;
    * `copies` (glow, trail, shadow, extrusion, mirror, RGB split, box);
    * `colourWipe`;
    * `bg` (follow / arrive / leave the colour change);
    * `needsBg`, `sync`;
  * `legacy`: the v1 slot and label this effect replaces;
  * `source`, `notes`, `approx`.
* **Preset**: `layers`, plus an optional `font` (family / bold / italic) and,
  for colour-change looks, a `frame` (colour A, colour B, transition, time,
  start). Applying a preset in the editor sets all of them.
* All 69 v1 effects, and the six former toggles (Grow / shrink, Rotate, Squash,
  Colour morph, Bouncy, Motion path), are v2 effects.
* New effects include:
  * **Jitter**: Tracking in, Blurry spin, Sliding reveal, Snappy stretch, 3D
    flip · letters, Cascade drop, Drop & bounce · words, Tilted snap, Elastic
    letters, Squeeze, Swoosh in, Jello, Swing, Heartbeat, …
  * **Prismic / copies**: Letter burst, Dancing shadow, Text extrusion, Mirror
    reflection, CRT / RGB split, Rainbow wave, Breathe (weight), Type trail,
    Glitch reveal, Departures board, Countdown, …
  * **Handwriting**: Outline → fill, Write on, Ink bleed, Hand-drawn boil,
    Write off.
  * **Emphasis & bars**: Marker highlight, Underline draw.
  * **Background sync**: see §5.

## 3. How layers combine

Effects never overwrite each other. Every channel is composed across the stack,
at every video frame:

| Channels | Rule |
|---|---|
| opacity, scale, sx, sy, fill, squash | multiply |
| x, y, vx, vy, rotation (rz, rx, ry), skew, blur, spacing, line, gather | add |
| glow, depth, rgb split, orbit | maximum |
| colour | folded bottom → top (`base` = the colour below) |
| clip (masks, wipes) | intersected, only while the layer runs |
| content (text-rewriting effects) | one per lane; the top one wins and the other gets a badge |

* **Units nest**: letter < word < [word range] < line < text. A coarser layer
  moves and turns its finer children around its own centre, so *Pop in · words*
  + *Wave · letters* really compose.
* **Adaptive granularity**: the engine splits the caption only as finely as the
  finest layer needs. Letters and words are drawn **in line**: each event holds
  the whole line with the other units transparent. libass therefore shapes the
  line exactly as it would without effects, so kerning, ligatures and the joins
  of script fonts survive letter effects.
* **Conflicts** are reported, never silently wrong. The lanes show badges and
  the stage warns for:
  * *replaced by X*: two text-rewriting effects in one lane;
  * *needs a colour change*: a background effect on a frame without colour B;
  * *whole text*: a counter forces the other layers onto the whole text;
  * *no colour change to sync to*.

## 4. The engines

| Where | File | Role |
|---|---|---|
| MP4 | `backend/app/text_motion.py` | compiles a stack to an `.ass` file |
| MP4 | `backend/app/renderer.py` (`_text_filter`) | picks the stack engine when an item has `textFx` |
| Browser | `src/textMotionCore.ts` | the **twin** of the compiler/evaluator (same maths, same registry) |
| Browser | `src/textMotionScene.ts`, `src/TextMotionStage.tsx` | the DOM scene, background painter, shared clock, `MotionStage` / `MotionTile` |
| Fonts | `backend/app/font_registry.py`, `src/fonts.ts` | family → files, styles, metrics (`registry/fonts.json`) |

**The compiler (`text_motion.py`)**

* It samples the composed state per frame and writes piecewise-linear `\t`
  chains. Any easing survives: overshoot, elastic and bounce are sampled
  curves.
* It splits events only where libass requires it:
  * `\move` is linear;
  * glyphs change;
  * a vector clip changes.
* HarfBuzz (`uharfbuzz`, the same shaper libass uses) supplies the layout for
  pivots, clips and the browser. `Kerning: yes` makes libass kern like HarfBuzz
  and the browser.
* fontTools reads metrics for fonts that are not in the registry.

**Renderer**

* Title frames with colour B build
  `[0:v][1:v]xfade=…:offset=lead_in+start[bg];[bg]…,ass=…[v]`, so the caption
  is burnt in on top of the moving background.
* Captions are shifted by the incoming transition handle (`lead_in`), like
  every caption.

**Fallbacks**

* **No libass** in the FFmpeg build (stock NAS binaries): the caption keeps its
  words, style and position, with plain fades as long as its enter and exit
  lanes (`stack_fallback_fields`).
* **Items without `textFx`**: projects that were never opened in the new GUI
  still render with the v1 engine (`backend/app/text_effects.py`, unchanged).

**Preview = render**

* The editor stage, the browser tiles, the gallery and the lightbox all run the
  twin on the layout Python computes.
* `tests/test_text_motion_twin.py` checks this for every effect plus 20 stack
  combinations:
  * it evaluates both engines on the same input (more than 20,000
    unit-frames);
  * it compares every channel, the warnings and the conflicts.
* The server-side example clips (`/api/text-effects/*.mp4`) come from the same
  compiler.

## 5. Colour A → B background

A text frame with a second colour (`frameBackground2`) changes colour through a
transition from the catalogue. The settings are `frameTransition`,
`frameTransitionTime` and `frameTransitionStart`. The renderer turns this into
a `BgChange`, and **the stack can see it**:

* **Exact geometry.** `bg_region()` reproduces FFmpeg's xfade maths:
  * rectangles for wipes, slides, covers, reveals, slices, open/close and
    squeeze;
  * a circle for circle open/close;
  * polygons for radial and the diagonals;
  * a crossfade for fades, dissolve, pixelize, zoom and the GL transitions.

  Soft edges (smooth*, circles, open/close) are split at their midpoint.
  `src/textMotionScene.ts` paints the editor's background with the same
  geometry on the same clock, so the preview shows the change exactly as the
  MP4 does.
* **Background effects** (category *Background sync*):

  | Effect | Lane | What it does |
  |---|---|---|
  | Follow background | While shown | text colour changes where B has arrived (default: readable on B) |
  | Swap with background | While shown | the text takes colour A as B arrives (ink-swap look) |
  | Arrive with background | Enter | the text appears only where B has arrived |
  | Leave with background | Exit | the text disappears where B arrives |
  | Pulse on colour change | While shown | one pulse timed to the change |

  They are drawn as two clipped copies (`\clip` / `\iclip` of the region), or
  crossfaded for mix-type transitions.
* **Any layer can be timed to the change.** Tick *Timed to colour change* in
  the layer's parameters (`sync: "bg"`). An enter or exit then runs over the
  transition window, and a loop runs only during it.
* **Colour tokens** in colour parameters:
  * `base`;
  * `bgA`, `bgB`;
  * `contrastA`, `contrastB`: a readable colour on A or B. This is the text
    colour if it already reaches 4.5:1 (WCAG AA), otherwise near-white or
    near-black.
* **GUI**:
  * the A → B block's hint offers *Text follows colour* (it adds the Follow
    layer);
  * the lanes show A→B badges;
  * the mini timeline has a **BACKGROUND** lane (A · transition · B);
  * *Same as A* unticked now starts colour B as a copy of A;
  * four presets bring their own colours and transition: **Sunrise card**,
    **Colour wipe**, **Ink swap**, **Reveal with colour**.
* **Randomize** only picks colour-change presets for frames that have a
  colour B.

## 6. Fonts

49 families in `public/fonts` (the browser loads them through
`src/fonts.css`; the Docker image copies them to `/app/fonts`):

| Group | Families | New |
|---|---|---|
| Sans (10) | Montserrat, Open Sans, Roboto, Lato, Poppins, Raleway, Nunito, Source Sans 3, Oswald, DejaVu Sans (system) | – |
| Serif (4) | Playfair Display, Merriweather, Lora, Cormorant Garamond | – |
| Display (2) | Bebas Neue, Anton | – |
| Handwriting (15) | Caveat, **Caveat Brush, Kalam, Patrick Hand, Indie Flower, Shadows Into Light, Amatic SC, Permanent Marker, Gloria Hallelujah, Architects Daughter, Gochi Hand, Handlee, Reenie Beanie, Nothing You Could Do, Homemade Apple** | 14 |
| Script (16) | Pacifico, Dancing Script, Great Vibes, **Sacramento, Satisfy, Allura, Parisienne, Alex Brush, Kaushan Script, Yellowtail, Cookie, Courgette, Tangerine, Lobster, Mr Dafoe, Pinyon Script** | 13 |
| Typewriter (2) | **Special Elite, Courier Prime** (4 styles) | 2 |

* `scripts/build_fonts.py`:
  * downloads from google/fonts;
  * instances variable fonts to static weights;
  * subsets to Latin while keeping every OpenType feature, so script joins and
    ligatures survive;
  * writes `registry/fonts.json` (group, files per style, licence, vertical
    metrics) and `src/fonts.css`.

  Licences are in `public/fonts/licenses/` (SIL OFL 1.1, Apache 2.0).
* `available_style()` never fakes a bold or italic that a family does not ship.
  The editor disables the toggle, and libass and drawtext draw the real
  upright/regular cut, so preview and MP4 agree.
* **Font picker** (`src/FontPicker.tsx`): grouped tabs with counts, a search
  field, and every family drawn in its own face with the caption text.
* Handwriting effects pair with the new fonts, and presets set them:
  *Handwritten note* (Kalam), *Wedding script* (Great Vibes), *Chalkboard*
  (Permanent Marker), *Signature* (Mr Dafoe), *Ink swap* (Caveat), *Memo
  typewriter* (Special Elite), *Departures* (Courier Prime).

## 7. GUI

The *Text animation* section is the same in the text frame editor, the picture
caption editor and the *Default text style* popup (`src/TextMotionEditor.tsx`).

**Lanes and layers**

* Three lanes (**Enter / While shown / Exit**). Each row shows symbol, name,
  category, unit badge, duration, ⚙ parameters, 👁 mute and 🗑. Drag rows to
  reorder them.
* Parameters per layer: unit, duration, delay, stagger, order, intensity, loop,
  *Timed to colour change*, *Words* (range), and the effect's own parameters
  (colours accept the tokens above).
* **Preset chip**: the current look (or *Custom stack*). **Save** stores the
  stack as a named preset in this browser.

**Effect browser** (`src/TextEffectBrowser.tsx`), aligned with the transition
browser:

* search;
* tabs *All / Enter / While shown / Exit / ★ / Recent / Presets*, with counts;
* a category rail with counts;
* live tiles in your own font, animated on hover (or with *Autoplay tiles*).
  **Hover previews the effect on top of your stack** on the big stage;
* conflict badges on tiles (*replaces X*, *needs colour B*, *whole text*);
* ★ favourites and Recent (localStorage `slideshow.textFx.favourites` /
  `.recent` / `.presets`).

**Full gallery**: a big stage with your caption and stack, plus a detail panel
(channels, exact vs ≈ in the render, source, how it combines).

**Mini timeline** under the stage: BACKGROUND, ENTER, WHILE SHOWN and EXIT
lanes. Drag a bar's inner edge to change its duration, click the ruler to scrub,
play/pause. It shares one clock with the stage and the background.

**Elsewhere**

* **Storyline and detail list**: the enter/exit chips show the first layer's
  symbol plus *+N*; the tooltip lists the whole stack.
* **Bulk "Text effects"** gives each photo (or the selection) a random curated
  preset.
* **Motion path**: the section's own toggle adds or removes the *Motion path*
  layer.
* **Removed**:
  * the grow/shrink, rotate, squash, colour change and bouncy toggles, which
    are now While effects;
  * the duplicate *Bouncy text* option.

## 8. Migration and compatibility

* **On load**, every item and the saved default style are converted:
  * old fields → `textFx` (`migrateLegacyTextFx`, `src/textFx.ts`);
  * v1 labels → registry ids, including the old xfade names (*Wipe left* →
    Wipe from left);
  * each enabled toggle → a While layer;
  * Bouncy effect + Bouncy toggle → one layer;
  * the untouched colour toggle (`#ffcc33`) keeps the text colour.

  From then on only `textFx` is written.
* `legacy_to_stack()` in the backend mirrors the browser's migration.
  `tests/test_text_motion.py` runs the browser code under Node and checks that
  both give the same result for every v1 label, alias and toggle.
* `Kerning: yes` slightly changes the letter spacing of libass captions compared
  with older renders. It now matches the browser and drawtext.
* Requirements: `uharfbuzz==0.56.2` and `fonttools==4.66.0` in
  `backend/requirements.txt` (manylinux wheels for CPython 3.13, x86_64 and
  aarch64). Without uharfbuzz a width estimate keeps the engine working; only
  pivots and clips become approximate.

## 9. Verification

* `backend/tests/test_text_motion.py` (30 tests):
  * registry integrity;
  * migration, pure and against the browser code;
  * composition: multiply/add, word ranges, content conflicts, sync windows;
  * xfade region geometry, contrast colours, follow/swap splits;
  * fonts: files, licences, no faux bold, HarfBuzz kerning;
  * the renderer's textFx graphs: compositor overlay, xfade before ass, empty
    stack, no-libass fallback;
  * **burnt-in pixels**: FFmpeg + libass render a wipe with *Follow
    background*, and the letters are white before the change, dark after, and
    split within ±4 px of the wipe edge halfway through.
* `backend/tests/test_text_motion_twin.py`: every effect and 20 combinations,
  Python vs TypeScript, channel by channel.
* Full suite: 444 tests OK. It passes with or without an FFmpeg on PATH; the
  renderer tests now pin the container build's capabilities.
* Headless Chromium walk-through of the editor: lanes, the browser with hover
  preview, presets, colour-change presets with the background lane, the font
  picker, the gallery, the parameters and the storyline chips.

## 10. Limits and later

* Soft-edged transitions (circle open/close, smooth wipes) switch the text
  colour at the midpoint of the soft edge. libass cannot fill a glyph with a
  per-pixel gradient. GL transitions are followed with a crossfade.
* Not possible in libass (see the references doc): image-filled text,
  holographic / colour fonts, hover effects.
* Presets are stored per browser. Project-wide or global presets would need a
  small API.
