# Photo collage: more appear / disappear animations, reuse of the transition
# catalogue, and a GUI for a big list — options

Date: 2026-10-25 · Status: **options, nothing built yet** (awaiting your decisions)

Question: *"do a thorough deep search to see if there are any more photo
animations possible in the photo collage for the photo to appear / disappear
(also check within the existing transitions, if they can be used for individual
photos). If the number of animations becomes too big, propose a new
interface/GUI for the user. Also provide a random option."*

## Where it stands today (verified)

- **Entrances**: 13 + `none` — Drop, Pop, Swing, Flip, Fade, Slide, Rise, Tumble,
  Zoom, Origami, Glitch, Ink, Brush (`COLLAGE_ANIMS_ALL`, `src/collageCore.ts:29`;
  labels/hints at `src/App.tsx:4402`). Each has a **fixed** length
  (`ENTRANCE_LENGTH`) — no speed control.
- **Exits**: 4 + *Stay on* (`none`) — **Sweep out, Deal off, Scatter off
  (`shuffle`), Fade out** (`COLLAGE_EXITS`, `src/collageCore.ts:1085`; labels and
  hints `src/App.tsx:4442`), with `EXIT_LENGTH`, `EXIT_STAGGER` and a **speed
  slider (0.05–2×, `EXIT_SPEED_MIN/MAX` = `:1088`). The `none` choice is stored
  by *deleting* the key (`src/App.tsx:5077` writes `exit: undefined`).
- Model: three lanes per photo (enter / while-motion / exit) + a look animation;
  collage-wide default with a **per-photo override** that inherits
  ("*Drop · collage*" in the per-photo select, `src/App.tsx:4919`, `4982`).
- Both engines are twins: `photoState()` in `src/collageCore.ts` (preview) vs
  `backend/app/collage.py` (FFmpeg), compared value-for-value by
  `backend/tests/test_collage_twin.py`, and the mask machinery already exists on
  both sides (`geq` alpha for mat shapes and for `ink`/`brush`,
  `alphamerge`+`maskedmerge` for cut-out, `frameClipPath` ↔ `frame_clip_path`).
- The transition catalogue: **191** entries = 58 native xfade + 133 ported GL,
  one shared `registry/transitions.json` + `src/transitionCatalog.ts`, with a
  searchable gallery, ★ favourites, recents and cached example clips.
- **Persistence has an allow-list**: `normalizeCollage` (`src/collageCore.ts:1680`)
  rebuilds the spec and *every photo* field by field, so a new key
  (`animSpeed`, `direction`, `locked`, `randomScope`, …) silently disappears on
  the next load unless it is added there and in the backend's twin validator.

> **Bug found while auditing (unrelated to this proposal; one-line fix).**
> `normalizeCollage` (`src/collageCore.ts:1742`) accepts only
> `['sweep','deal','shuffle']` for the **collage-wide** `exit`, but the picker
> chips (`src/App.tsx:5077`) also offer `'fade'` = *Fade out*, and `exitMode`
> (`src/collageCore.ts:1112`) supports it. So: choose *Fade out* for the whole
> collage → save → reload → the key is dropped → `exitMode` falls back to
> `'none'` (*Stay on*) — the exit not only changes, the slide gets **shorter**,
> because `exitTotal` (`:1147`) stops counting it. The photo-level line (`:1710`)
> validates against the full `COLLAGE_EXITS`, so *per-photo* "Fade out" survives.
> Fix: validate the spec-level exit the same way, and add the round-trip case.

So the useful question is not "can we invent animations" but "**how many can the
two engines keep in agreement, and how does a 50-item picker stay usable**".

## Can the existing slide transitions be used for an individual photo? Yes —
### in two grades, one clean and one compromise.

**Grade 1 — mask reveals (recommended).** A reveal transition is just a scalar
field thresholded by progress. xfade's own `wipeleft`/`circleopen`/`sliceh`
family is that field, so the mask can be generated from two *solid* layers and
merged into the photo's alpha — a recipe proven outside this repo (build the
mask with `xfade=transition=wiperight` over a black/white pair, then
`alphamerge` onto the overlay) and already used here for `ink`, `brush` and the
mat shapes. Cost: one `geq`/mask expression per photo, i.e. the same class of
filter the collage already runs, and **the CSS twin is honest**: `mask-image`
with an animated `linear-/conic-/radial-gradient` stop is the documented
browser-side equivalent (wipe, clock, iris, and `mask-composite` combinations
for diamond/mosaic/honeycomb).

Of the 58 native xfade entries, these translate directly into a per-photo
appear/disappear:

| xfade group | usable as a photo reveal | count |
| --- | --- | --- |
| Wipes (left/right/up/down/4 corners) | all, as `linear-gradient` masks | 8 |
| Shapes (circle/rect crop, circle open/close, vertical & horizontal open/close, radial) | all, as `radial-/conic-gradient` or clip-path | 9 |
| Slices (4 diagonals, h/v left-right-up-down slices) | all, as stripe masks | 8 |
| Fades & blends (Fade, Dissolve, Pixelize, H blur, Distance) | all, as noise/tile/blurred-threshold masks | 5 |
| Slides & smooth (Smooth ×4) | the soft-edged ones; the hard `Slide ×4` are `cover` in disguise | 4 |
| Squeeze & wind (wind ×4, squeeze ×2) | wind = bars appearing in order; squeeze is geometry, not a mask | 4 |
| Cover & reveal (Cover ×4, Reveal ×4) | yes, **but** they need a copy of the backdrop behind the photo (that's what slides away / gets uncovered) | 8 |

≈ **46 of the 58** are immediately reusable, ~36 of them with mask-only work.
The `Cover/Reveal` four+four need the app to composite a *backdrop crop* per
photo — doable (the movie frame already builds a blurred-copy backdrop) but a
bigger change, so it is its own tier.

**Grade 2 — the GL catalogue (compromise).** The 133 GL transitions *sample both
images*, so per-photo they can only work as `xfade(backdrop-crop → photo)` inside
the photo's rectangle — which is technically fine, but (a) it costs one extra
filter instance and one extra input per photo, (b) on the DS918+ portability path
GL **falls back to dissolve**, so the animation a user picked would silently be a
fade in the MP4, and (c) the browser preview can only be a CSS *approximation* —
which the slide-transition previews already accept, but the collage engine is
pinned by value-for-value twin tests. Recommendation: expose these as an
**opt-in "Advanced reveals"** list, clearly marked *preview approximate · may
degrade to dissolve on Synology*, not as the main catalogue.

## Proposed catalogue (tiered; every entry = 1 state function × 2 engines + twin cases)

**P1 · Mask & dissolve family — 16 new (the ones that twin cleanly, and the
cheapest per entry because alpha is all they touch):**

Blinds (h), Blinds (v), Checkerboard, Random bars, Strips (corner), Box,
Diamond, Iris (circle open), Clock wipe, Wheel (spokes), Split (h/v),
Crawl in, Pixel dissolve, Grain dissolve, Mosaic tiles, Honeycomb.

**P2 · Geometry & paper family — 12 new (transform/clip work, same class as the
existing fold/flip/swing):** Cube turn, Door swing, Tilt away, Page turn,
Roll down (unfurl), Letterbox (bars retract), Curtain (open sideways),
Squeegee, Stamp (over-scale + settle + shadow squeeze), Hanger drop (bounce on
a nail), Spring in (elastic overshoot), Jelly (squash & stretch settle).

**P3 · Look-family exits/entrances — 8 new (they ride the existing look/filter
animation instead of new geometry):** Develop (Polaroid: blur + warm cast +
contrast resolving), Focus pull, Exposure flash (bloom in), Light leak,
Gate weave + projector flicker, VHS tracking, CRT power-on (line expand),
Photostatic (high-contrast burn-in).

**P4 · The transition bridge — 0–36+:** the mask-relevant native xfade names
exposed as photo animations by *name*, so "the photo wipes in like
`Wipe top-right`" and, if you accept the compromise above, GL names too. This
is the "use the existing transitions" answer: as soon as P1 exists, P4 is
mostly a mapping, not new maths — one mask evaluator, two sources.

**Exits**: every P1–P3 entrance gets the mirrored exit for free if the exit
path is redefined as "the same function played backwards" (alpha/geometry
inverted), which also removes today's asymmetry (4 exits vs 13 entrances).
Optional extras that cost almost nothing and fix real gripes:
**Entrance speed (0.05–2×) like `exitSpeed`**, per-photo **anim length**, and an
**"in and out of the same family"** switch so a photo that wipes in also wipes
out unless you override it.

That is **36 new (P1+P2+P3)** or **~70 with P4** — both too many for the current
chip row (14 already fills it), which is what the rest of this document is about.

## The GUI: one row of chips cannot carry 40–70 items; this app already solved
### that problem twice

The transitions and the text effects both went through the same journey: a long
`<select>` → chips + **a searchable browser with live previews, ★ favourites,
recents, groups and a "Browse all 191" gallery** (`TransitionGallery.tsx`,
`TextEffectBrowser.tsx`, `docs/transition-previews.md`). The collage animators
should reuse that shape rather than invent one, at three sizes:

1. **Chips (today) → 6 + "…"**: the 6 most-used per lane + a *More…* button.
   Default look stays as light as it is now.
2. **Popover picker (~150 ms to open, no popup layer fight)**: the current
   per-photo `<select>` becomes a grouped popover — search field on top, family
   columns, each row a **24-frame micro-preview that loops on hover** (rendered
   once per animation and cached under `/config`, exactly like
   `transition_previews.py` does; `CollageArrangementThumb.tsx` shows the SVG-thumb
   idiom already used for arrangements), ★ toggle, and the *inherit* row at the
   top ("Drop · collage"). Keyboard: type to filter, `1-9` to pick, `Enter`.
3. **Full browser (same window class as the transition gallery)**: opened by
   "Browse all 70…" from the popover. Left: search + families (Reveal /
   Geometry / Paper / Look / Motion / Exit) + ★ + Recent. Middle: a wall of
   live tiles, each playing the animation on the *actual selected photo* (so the
   user sees their photo do it, not a stock frame). Right: length, speed,
   easing, direction (from left/right/top/bottom/centre — one parameter covers
   6 variants of most mask reveals and is the reason the catalogue need not be
   3× bigger), and "Apply to: this photo / selection / all".
4. **A strip, for the timing-in-the-timeline case**: the photo lane the editor
   already has (`text-track`-style) can show per-photo enter/exit blocks, so
   lengths and waits are dragged, not typed. Same idea as the karaoke strip in
   `docs/karaoke-text-on-movies-options.md` — worth building once and sharing the
   widget.

Two side-suggestions that keep the list usable without cutting it: **direction
and speed as parameters** (70 names → ~20 names × a direction control), and
**"Only these" families filter** stored on the collage, so a "Gentle" album can
hide the glitchy ones.

## The random option (and why it must be materialised, not stored as "random")

A saved project has to render identically tomorrow, so `animation: 'random'`
cannot be the stored value — the pick is made **in the editor** and written as a
concrete id, with the draw recorded so a re-roll is reproducible:

```ts
animation?: CollageAnim | 'random'      // 'random' = "roll again on next open" (opt-in only)
animSeed?: number                        // what the shuffle drew from
animLocked?: number[]                    // photo indices a shuffle must not touch
animRandomScope?: 'all' | 'family:reveal' | 'favourites' | 'no-repeat'
```

Controls, all in one place so a whole album can be dressed in three clicks:

- **🎲 Shuffle** on the collage row — draws for every unlocked photo at once,
  honouring `no-repeat` (no two neighbours get the same), the family filter, and
  ★ favourites only if you ask;
- **per-photo 🎲** (reroll just that one) and a **🔒** that keeps it through a
  shuffle — the lock/selection pattern the storyline already uses;
- **scope selector** = the same `RandomScope` idea as the transitions
  (`src/transitionControls.tsx`: 'xfade' | 'gl' | 'both' + labels with counts),
  here: *all · by family · favourites · exclude the plain ones*;
- **paired exits** toggle ("same family out"), **length/speed jitter** ("vary
  0.75–1.25×"), and **"one of each"** (deal distinct animations round-robin
  instead of independent draws — the option that actually looks designed);
- **on beats** (already the app's `slideLocalBeats` machinery): random
  animation, deterministic *times* — arrival on the onset, look random;
- **Undo** by keeping the previous spec (project save already snapshots), plus a
  **history strip** in the browser ("Rollback last shuffle").

Cost note: random is nearly free once `ENTRANCE_LENGTH`/`exitSpeed` are per-photo
and the picker knows its pool — the dice is a loop over `photoAnim` writes.

## Cost, honestly

| Tier | New anims | Render work | Preview work | Twin test |
| --- | --- | --- | --- | --- |
| P1 masks | 16 (× directions) | 1 mask expr per photo (same class as `ink`/`brush`) | CSS `mask-image` per anim | geometry-free, cheap to pin |
| P2 geometry | 12 | extra transform terms (like `fold`/`flip`) | CSS 3D transforms | the fiddly ones (perspective, hinge point) |
| P3 looks | 8 | reuse `picture_filters`/look anims | CSS filters | small |
| P4 xfade/GL | +36…133 | one xfade instance + a backdrop crop per photo | **approximate only** | cannot be pinned value-for-value; document the gap |
| GUI popover + browser | — | — | cached micro-previews (build once per anim, ~1 s each) | source-level + manual |

My recommendation: **P1 + exits-mirrored + entrance speed + the popover picker
+ 🎲 shuffle with scope/lock/one-of-each**, then P2 in a second pass, and P4
behind an "Advanced" switch only if you want the GL names in the list knowing
the DS918+ degrades them.

## Decisions I need from you

1. **How big**: P1 only (16), P1+P2 (28), P1+P2+P3 (36), or +P4 (70–170 names via
   the transition catalogue)?
2. **Directions as a parameter** instead of 4× the entries — yes/no?
3. **Picker shape**: popover-with-mini-previews only, or also the full
   "Browse all" browser (search/★/recents) like the transitions have?
4. **Exits**: mirror every entrance (a big win for 36 anims), or keep a curated
   exit list?
5. **Random**: shuffle-all + per-photo reroll + lock + scope, and should
   "one of each" and "vary length/speed" be on by default?
6. **P4 (GL per photo)**: include with an "approximate preview / may degrade to
   dissolve on Synology" warning, or leave the GL names for slide transitions
   only?
