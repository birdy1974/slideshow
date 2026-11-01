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

---

## Decisions taken (2026‑11‑01)

All six asked above were answered "the biggest option": **P1+P2+P3+P4**,
directions as a parameter, the full Browse‑all picker, mirrored exits, the
shuffle with scope/lock/one‑of‑each, and the GL names included — marked
approximate, degrading to dissolve where the hardware cannot run them.
The `normalizeCollage` exit bug found while writing this plan was fixed first
(`8f466ab`).

## P1 — the 16 mask reveals (shipped)

`src/collageCore.ts` owns the catalogue: `COLLAGE_REVEALS` with one
`COLLAGE_REVEAL_GEOM` row each (`fam / label / hint / mirror / n / out / angle`),
`COLLAGE_REVEAL_LENGTH` for how long each sweep takes, and one scalar

```ts
q <= p            // uncovered, for an `out` kind: q >= 1 - p
```

per family — `q` being the geometry measured at a pixel (distance along the wipe
axis, from the centre, round the clock, inside which tile). Both the preview's
CSS mask and the export's `geq` alpha expression are *generated from that one
comparison*, which is what keeps them showing the same photo at the same moment;
describing a shape twice is the way this kind of table rots.

| family | ids | `q` at a pixel |
|---|---|---|
| linear | `wipeUp` `wipeDown` `wipeLeft` `wipeRight` `diagDownRight` `diagDownLeft` | projection on the axis, `angle` clockwise from up |
| centre | `curtain` `diamond` | `|x-½|·2`, `|x-½|+|y-½|` |
| radial | `iris` `irisOut` | distance from the centre, `out` inverts |
| wedge | `clock` `pinwheel` | angle round the clock, `n` wedges |
| repeat | `blindsDown` `blindsRight` | inside its own slat, slats led across |
| mosaic | `mosaic` `mosaicOut` | inside its own tile, tiles led diagonally |

`animDir` (spec *and* per photo) mirrors a kind on the axis its `mirror` field
names; the round kinds have no axis, so the UI shows them no direction control
rather than a checkbox that does nothing.

**Render.** A per‑frame `geq` over a full‑size photo is what a NAS cannot afford,
so the sweep rides the sprite ladder that already exists for sizes: each photo's
stream is cut into runs of *(size, mask step)*, one masked still per run, each
`overlay`‑ed with `enable='between(t,…)'`. Two details make it honest rather than
merely fast:

* the step is quantised by `REVEAL_STEPS = 32` in **both** engines — the preview
  caches its mask image per step, the export bakes a sprite per step, and one
  shared number is why they agree;
* a masked run is `select`‑ed to its own window before the `geq`, because a step
  is a still and the frames outside its window are worth nothing. Without that
  the mask would be recomputed for every level for every frame of the slide.

**Verification.** `backend/tests/test_collage_reveal.py` (16 tests) compares the
engines point by point on a 12×12 grid, re‑evaluates the generated `geq`
expression with a miniature FFmpeg‑expression evaluator, and checks that
coverage is monotone, that every kind is a different shape, and that `p=1` is
reachable. `test_collage_reveal_render.py` reads the filter graph: the number of
masked runs must equal the quantised steps `photo_state` walks through, each
bounded to the same window its overlay is enabled for, with no mask left on the
resting level. The 33 reveal specs are in the animation twin too, so
`revealP`/`revealDir`/`revealKind` are compared at every sampled time of every
entrance.

## P2 — the twelve geometry/paper entrances (shipped)

`cube` `book` `door` `unfold` `rollover` `spiral` `swish` `glide` `peek`
`boomerang` `sling` `thread`, one row each in `COLLAGE_GEOM` (TypeScript) and
`GEOM` (Python). Every row is a set of *curves* — `settle3`, `settle2`,
`smooth`, `one‑smooth`, `one‑back`, `bow`, plus the two hinge shapes `hinge` and
`recip` — and a value is `base + amp × curve(q)`. `photoState` evaluates the row
to numbers; `photo_state` evaluates the same row, and the filter graph builds its
`overlay` x/y and `rotate` terms out of the same names as FFmpeg text. Which rows
need the size ladder is *derived* from whether they touch a scale, and the
render's `fade=t=in:` duration is the row's `fade`, so nothing about a new
entrance has to be remembered in a second place.

They stay inside `dx, dy, rot, scale, scaleX, alpha, dim` on purpose: FFmpeg's
collage graph has `scale`, `rotate` and `pad`, no perspective warp, and
`unfold`'s height‑only squash is reachable as scale *k* with `scaleX` *1/k*
because width is scale × scaleX. A `rotateX` cube would preview beautifully and
render as a still, which is the failure mode this family was designed around.

## Still open

Only the mask builder that would make ten bridged names exact. `smoothleft`,
`smoothright`, `smoothup`, `smoothdown`, the four `*slice` names, `dissolve` and
`pixelize` have no equivalent among the shapes this app draws, so their rows are
marked `via: 'bridge'`: the mask they should carry is FFmpeg's own `xfade` between
two solid layers, `alphamerge`d onto the photo (the single-image-reveal-without-a-
second-clip trick). They currently run the nearest animation instead — a hard-edged
wipe, a strip mask, a mosaic — and their hints say which, so the tile, the preview
and the MP4 all describe the same file. What the builder has to solve before it is
worth its graph lines: the photo's entrance alpha is an expression (`geq a=` per
ladder level) and `alphamerge` *replaces* alpha rather than multiplying it, so the
two have to be composed in one step (a mask stream that already carries the fade,
or `fade=t=in:alpha=1` after the merge) in both engines, in the same order, or the
preview and the MP4 disagree about the first 0.2 s of every photo.

Not planned, and recorded so the reason survives: the 133 GL transition names stay
slide-level only (a per-photo GL name would need the whole scene as a backdrop
crop, and on a DS918+ they degrade to a dissolve anyway); a *blur* entrance needs a
second sprite ladder, which is a feature of its own rather than a row in a table.

## Mirrored exits (shipped)

A new exit mode — **"Back the way it came"** (`exit: 'reverse'`, and per photo
`photos[].exit`) — is not a 42nd motion but the photo's *own entrance played
backwards* over its exit window. Both engines do it by choosing the time the
entrance curves are sampled at:

```ts
ti = mode === 'reverse' && qe > 0 ? t0 + ent * (1 - qe) : t
```

so every field — offsets, rotation, the two scales, the alpha ramp, the pendulum's
clock and the mask's `revealP` — returns along the path it came. In the graph the
same idea is one expression: the entrance progress becomes
`min(clamp((t-t0)/ent), 1-clamp((t-te0)/fly))`, the smaller of "how arrived" and
"how leaving", which is the arrival before the fly and the mirror after it.

Two consequences worth knowing:

* **A mask reveal closes instead of fading.** Its `revealP` mirrors, and the
  closing sweep reuses the *same* baked sprites as the opening — a step of a mask
  is a still — so those ladder levels simply carry a second `enable` window. The
  level that holds the end of the slide is therefore a *masked* one: the photo
  ends covered rather than reappearing for the cut.
* **The alpha out is the alpha in, reversed.** A photo that spent the first 0.16 s
  of a 0.6 s arrival becoming visible spends the last 27 % of its fly
  disappearing, via `fade=t=out:st=…:d=…` sized from the same `ENTRANCE_FADE`
  table. An entrance that never touched its alpha (`none`) leaves over the whole
  fly, because an instant arrival has nothing to reverse.

`Entrance speed` now sits under the entrance chips as the twin of `Exit speed`:
one multiplier (0.05–2×) for the whole arrival — the sweep, its fade, a swing's
clock and the export's mask ladder together — on the collage or on one photo.

## P3 — the eight look-based entrances (shipped)

`develop` `flashIn` `colourFlood` `hueSwing` `negative` `shadowLift` `glowIn`
`xerox` — the photo sits still and its *colour* arrives. Table rows in
`COLLAGE_LOOK_ENTRANCE` (TypeScript) and `LOOK_ENTRANCE` (Python), each channel a
curve from the same vocabulary the paper family uses, on the look's own scale
(1 = untouched for `sat`/`bright`, degrees for `hue`).

They are animations, not look animations, which is why the two lanes have to
compose: `photo_look_state` multiplies saturation and brightness, adds hue and
ORs the invert, and the render merges them into **one** `hue=s='…':H='…'` instance
plus one sendcmd `eq` ladder, because the preview applies a single CSS filter
string and CSS evaluates left to right. Two filters in the other order would be a
different photo.

Why each channel goes the way it does (measured on the render binary, and the
reason the split exists at all):

| channel | filter | why |
|---|---|---|
| saturation, hue | `hue=s=`/`hue=H=` | `hue` re-evaluates its expressions per frame → an exact ramp |
| brightness | `sendcmd` → `eq` | `eq` evaluates once at init, so the ramp is stepped per frame into it |
| invert | `negate=enable='lt(t,land)'` | `negate` has no amount — a hard flip, which is also what `invert(1)` is |

The brightness ladder stops at the end of the entrance (a test asserts it), so a
long slide does not carry thousands of commands for a colour that settled in 0.75 s.
`hue`'s `H=` takes radians while the state speaks degrees, so the conversion
happens exactly once, in the graph.

The twin grew 9 cases (all eight, plus one photo carrying `flicker`, `toColor` and
`invert` *while* an entrance develops, where composition is the whole point); the
graph tests pin which filter carries which channel. 125 collage tests pass.

## P4 — the transition bridge (shipped, 41 names)

The user asked whether the existing *slide transition* set could be reused per
photo. It can, mostly by aliasing: 31 of FFmpeg's `xfade` names are already
something the collage builds more cheaply than a mask, because a wipe is the mask
the `geq` ladder bakes, a slide is a translate and `fadewhite` is a brightness
ramp. So the catalogue grew 41 rows — `COLLAGE_BRIDGE` in TypeScript, `BRIDGE` in
Python — where the *id is FFmpeg's own name* (`wiperight`, `circleopen`,
`slideup`, `fadegrays`, `zoomin`, …), which is both what a viewer searches for and
what a hand-written spec would write.

Each row says which animation it runs, and one function resolves it:
`resolveAnim` / `resolve_anim`. That is the whole design: the picker, the
validator, `ENTRANCE_LENGTH`, the state and the graph all go through that one
lookup, so a bridge cannot mean a wipe to the preview and a colour ramp to the
export. Three rows also carry a `dir`, because FFmpeg's names state a direction —
`slideleft` pushes the new frame in *from the right* — and a mirrored alias would
be a lie about the MP4. A name with a direction of its own shows no direction
toggle at all (`animHasDirection` returns false), because the only thing it could
change is the label.

Two smaller consequences:

* `photo_anim` (Python) returns the *resolved* animation while `photo_anim_name`
  returns the stored name, because the direction rules and the picker need the
  name and everything downstream needs the animation. The TypeScript twin keeps
  them apart the other way round — `photoAnim` raw, `resolveAnim` at each use —
  which is why the twin driver reports `resolveAnim(photoAnim(…))` in its `anim`
  lane: without that, 41 cases failed on names doing nothing wrong.
* `fadegrays`, `fadewhite`, `fadeblack` land in the colour family, so the bridge
  also proved the P3 composer out: `fadewhite` is `flashIn`, and both engines had
  to agree about a brightness curve reaching 1.0 exactly at the landing. They now
  do — the twin found a real `geomCurve` base bug on the way (see P3).

41 names × six sampled times are compared in the twin, which is what a per-photo
transition catalogue needs to be worth anything. 133 collage tests pass.

## B5 — the entrance browser (shipped)

90 entrance ids do not fit a row of chips, so the picker took the shape the
transition browser already uses: a chip that opens a portalled popover with a
search box, a family rail (★ favourites, Recent, All, Classic, Paper, Colour,
Reveals, FFmpeg), and a tile grid with a star on each tile. New CSS only where the
shell did not already reach: the tile's square needs a *photo* to move, so
`.anim-tile-photo` is a flat sky/hill/field gradient — readable at 104 px, no
network, and it makes a wipe, a rotation or a colour ramp unmistakable.

The tiles are not icons and not a second implementation: `tileStyle` builds a
one-photo spec and asks `photoState`, `photoLookState` and `revealMaskStyle` —
the same three functions the stage and the export sample — for the transform, the
filter and the mask at the hovered time. A tile therefore cannot promise a move the
render will not make, which is the failure mode every catalogue this size grows
into. The animation runs while a tile is hovered (one entrance, a beat of hold,
repeat), and rests at its end state otherwise, so opening the browser is not 90
`requestAnimationFrame` loops.

One catalogue feeds it: `collageAnimRows()` builds the rows from the four choice
lists, and `COLLAGE_ANIMS` in `App.tsx` is that same list rather than a hand-typed
copy of labels and hints — the picker, the per-photo select, the shuffle's pools
and both validators now read one table, and `animLabelOf`/`animHintOf`/
`animFamily`/`animHasDirection` are its only accessors. The catalogue is built on
first call rather than at module load, because it reads constants declared in four
later sections and a `const` here would depend on where they happen to sit.

The same picker serves the collage *and* one photo (the per-photo lane gained
direction, speed and a lock checkbox next to it), with an "inherit" row that
deletes the photo's own `anim` again.

## B6 — the random roll (shipped)

`Random` gives every photo its own entrance, drawn from the ticked families, and
**writes the chosen ids onto the photos**. That materialisation is the point: the
engines see ordinary ids, so nothing has to agree about a random number generator
across two languages, and reopening a project shows and renders exactly the file
you previewed rather than a new lottery. `spec.animSeed` is stored next to them —
enough to replay the same roll with the *Replay seed* button, and enough for a
screenshot of the panel to describe the MP4.

Three rules the UI exposes and a test locks: `no repeats` samples without
replacement and wraps only when the pool runs out (more photos than names), the
family checkboxes are the pool (`classic/paper/colour/mask/ffmpeg`, all ticked =
the field is not written at all), and a photo with `lock` keeps its entrance
through every roll — "one of each" additionally refuses to hand that same name to
a second photo, which is the only way a lock and a no-repeat rule can coexist.
`collageShuffle` is a pure function in `collageCore` with a 12-line mulberry32
inside it, so the roll is reproducible from its seed rather than merely stable.

Not built: beat-snapped entrance *times*. The collage has no beat clock of its
own — `spec.beats` drives motion, not arrivals — so snapping a shuffle to the
music would mean reusing the karaoke feature's onset data, which is a separate
piece of work.
