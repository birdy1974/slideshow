# Photo collage

A *collage* is a photo slide that holds a **photo set** instead of one photo:
several pictures land one after another on a background, inside framed mats,
with an entrance, an exit and a camera move of their own. It is edited in the
**Photo collage** popup (open a photo slide → Photo collage → Add photos…) and
rendered by FFmpeg into the same MP4 as everything else.

The popup's preview is not a movie: it is a live engine driven by a clock, and
the render is a second implementation of the same maths. Keeping those two in
step is what this page is about.

## The data

`MediaItem.type === 'collage'` and `item.collage` holds the spec. It is saved
verbatim in `media_items.item_json` (SQLite) and normalized on both sides
(`normalizeCollage` in `src/collageCore.ts`, `normalize_collage` in
`backend/app/collage.py`), so a hand-edited or older project can never feed the
renderer an out-of-range number.

Per photo (`spec.photos[i]`):

| field | meaning |
| --- | --- |
| `path`, `name` | the media file (photo or movie) |
| `delay` | seconds of pause before this photo lands |
| `size` | scale factor of its mat, 0.5–3.0 (100 % = the arrangement's own size) |
| `crop`, `filter` | the same look the single-photo editor uses |
| `shape` | its own mat shape/aspect, overriding the collage frame |
| `slot` | its place in the arrangement (order ≠ position) |
| `cx`, `cy` | Free arrangement only: where it sits, in % of the frame |
| `rot` | Free arrangement only: its resting tilt in degrees, clockwise, −180…180 |
| `anim` | its own entrance, overriding `spec.animation` (missing = inherit) |
| `motion`, `motionAmount` | what it keeps doing once it has landed, and how strongly (0.25–2) |
| `motionDuration` | seconds the motion plays, from the photo's landing; missing/0 = the rest of the slide |
| `moveX`, `moveY` | where a `move` motion travels to, in % of the frame from its resting spot (±60) |
| `turnTo` | the angle a `turn` motion reaches, degrees clockwise from its resting tilt (±180) |
| `lookAnim` | an animated look: its colour changes over time (`toColor`, `toBw`, `flicker`, `invert`, `hueFlip`) |
| `exit` | how it leaves, overriding `spec.exit`; `none` keeps it on screen |

`rot` is written only when it is not zero (`setPhotoRot` drops it at 0°), and the
three lanes only when they differ from the collage's, so a project nobody ever
touched stays byte-identical through a save.

## The two engines

- `src/collageCore.ts` — placements, per-photo motion, camera, duration. Runs in
  the editor preview and in the timeline's estimate.
- `backend/app/collage.py` — the same numbers, emitted as one FFmpeg filtergraph
  (`collage_graph`) plus a per-photo sprite chain.

The house rule: **a visual change is data + both engines + a twin case.**
`backend/tests/test_collage_twin.py` builds specs × aspects × times, hands them
to Node (`backend/tests/collage_twin.mjs`) which evaluates the TypeScript twin on
exactly the same input, and compares every number (`TOL = 1e-9`). `rot` is
compared like any other placement field, so a preview-only tilt cannot slip in.

## A preview that keeps its place

Editing a collage parameter used to restart the preview: the clock rewound to
zero, so moving the *size* slider by one pixel replayed the whole slide and the
moment you were looking at was gone. It now repaints **in place**:

- `CollagePhotos` paints every mat imperatively from a `MotionClock`
  subscription (no React re-render per frame) and re-subscribes whenever the
  spec, geometry, media ratios or the selection change. `MotionClock.subscribe`
  fires immediately with the current time, so a re-subscribe *is* the repaint of
  the new parameters at the running playhead — paused stays paused.
- `MotionClock.setDuration` no longer rewinds; it clamps `t` into
  `[0, duration + tail]` and emits. A slide that got shorter therefore lands on
  its own end instead of jumping back to the start. A component that *wants* a
  restart seeks itself (the text-effect browser does).
- The media measurement effect is keyed on `measureKey` (the file paths plus the
  shapes that need a native aspect), not on the spec object, so resizing,
  turning, retiming or dragging never re-probes the photos.
- The layer scale lives in `layerScaleRef`, not in state: a 1920×1080 layer is
  scaled down to the popup, and reading that scale must not re-render anything.

Rewinding is still available, and only from explicit controls: the **Replay**
button in the transport row, **Stop** (rewind + pause), the timeline bar, and
opening the popup.

## Selecting a photo

One state (`selectedPhoto` in `CollageEditor`), shown in two places, so it works
in both directions: clicking a photo on the preview rings it *and* highlights its
chip in the strip below (scrolling the strip to it, which can hold a dozen
photos), and clicking a chip — or its number badge, a real button so the keyboard
can do it too — rings that photo on the preview. The selected photo's size
slider, and in Free its tilt, appear under the preview.

The ring is deliberately not the mat's own outline. Mats are painted in DOM
order, so the photos stacked on top of the selected one would cover it — which is
why the marker used to disappear in exactly the arrangements where photos
overlap. `.collage-select-ring` is a sibling *after* all the mats,
`pointer-events: none` (it must never steal a drag), and the preview hands it the
selected mat's box, corner radius and transform every frame: it turns, scales,
fades and flies out with the photo. Its opacity never drops below 0.5, so a
photo picked from the strip before it has landed still shows where it will
appear.

The line itself is **screen-constant**, which is the part that is easy to get
wrong: the ring lives in the 1920×1080 layer that the stage scales down to a
quarter of that or less, and the camera zoom and the entrance's own scale shrink
it further, so a border written in layer units arrives on screen a quarter as
thick — a 2px outline measured under half a pixel and was simply invisible. Like
the rotate knob, the ring divides its line width, its gap and its dark halo by
`layerScale × cameraZoom × photoScale` every frame (`SELECT_RING_LINE`,
`SELECT_RING_GAP`), so it is 3.5 px of dotted lime at any popup width, with the
border capped at a third of the mat so a tiny photo mid-entrance cannot collapse
into a blob. A pin-hung mat grows the box upwards instead of both ways and turns
around the pin itself (`transform-origin: 50% <gap>`). The chip's dotted outline is a pseudo element for the same reason — its
hover actions bar and its *pos* badge are positioned children and would paint
over an outline on the chip itself.

Photos are identified by their index in the strip, so the selection has to move
with them: `selectionAfterMove` (drag to reorder, or the ‹ › buttons) and
`selectionAfterRemove` keep the ring and the highlighted chip on the same photo
instead of letting a neighbour slide into the marked place, and removing the
selected photo deselects rather than marking whatever took its slot. Clicking
the chip's own buttons (replace, ‹ ›, remove) does not select as a side effect.

## Turning a photo (Free arrangement)

Select a photo on the preview (or in the strip) and a round **knob** appears on
its top edge. Drag it around the photo to turn it; **Shift** snaps to 15°, and
**← / →** nudge 1° (15° with Shift). Under the preview, the selected-photo bar
carries the same thing as a *tilt* slider (−180…180) with ⟲ 90°, ⟳ 90° and 0°
buttons. Other arrangements turn their photos themselves (scatter, fan, arc,
zigzag…), so there the control reads *tilt · Free only* and points at the
Arrangement picker.

Implementation notes, for the next person:

- The knob is a **sibling after all the mats**, positioned imperatively each
  frame. Inside a mat it is unusable: the mat's `clip-path` cuts it away and the
  later siblings paint over it.
- Its place is the mat's *pivot*, mapped analytically (`matPivotClient`) through
  the layer scale and the camera transform — not `getBoundingClientRect()` of
  the mat, whose centre only coincides with the pivot for `transform-origin:
  50% 50%` and not for the pin/swing anchor.
- `k = layerScale × cameraZoom` and the knob is counter-scaled by `1/k`, so it
  stays the same size on screen whatever the zoom, and lifts `matH·scale/2`
  camera pixels plus a fixed screen gap above the pivot.
- The maths lives in `collageCore.ts`: `photoTilt` (read), `setPhotoRot` (write,
  drops 0), `normalizeTilt` (fold into −180…180, round to 0.1°), `pointerAngle`
  (screen y points down ⇒ clockwise positive, like CSS), `rotateDragTilt`
  (start tilt + pointer delta, optionally snapped) and `rotateKnobLift` (which
  side of the mat the knob goes on: the host clips, so a knob above a photo that
  hangs over the top edge would be cut off — it moves below the mat, or onto the
  pivot when the photo spans the whole frame).
- The mat transform is `rotate(g.rot + st.rot)deg`: `g.rot` is the resting tilt
  from the placement, `st.rot` the animation's own rotation. That sum is exactly
  what the render draws.

## How the turn reaches the MP4

Each photo is a sprite: scaled and cropped to the mat, padded with the border,
masked to the frame shape, padded again onto a transparent canvas, optionally
given a shadow, faded, split per size level, scaled to output resolution,
**rotated**, and overlaid at its position:

```
scale → pad (border) → [format=rgba + shape mask] → pad=cw:ch:mat_x:mat_y
      → [shadow] → fade → split per size level → scale=W:H,rotate='…' → overlay
```

The second `pad` is what gives the turn its room, and its size is the subtle
part. The widest a `w × h` box ever gets while turning is
`f(θ) = w·|cos θ| + h·|sin θ|`, which peaks at `atan2(h, w)`. So the canvas is
sized for `reach = max(|rest| + spin, MIN_TILT_HEADROOM)` with the angle used
capped at that peak:

```python
reach  = max(abs(radians(pl["rot"])) + radians(choreography_spin(anim, exit, sway)), 0.18)
wide_a = min(reach, atan2(mat_h, mat_w))      # horizontal worst case
tall_a = min(reach, atan2(mat_w, mat_h))      # vertical worst case
cw     = max(mat_w, ceil(mat_w·cos(wide_a) + mat_h·sin(wide_a))) + 2·(border + 4)
```

`choreography_spin` is the largest turn the *animation itself* adds —
`ENTRANCE_SPIN` (tumble 28°, swing 16°, drop 7°…), `EXIT_SPIN` (deal 25°,
shuffle 20°, sweep 10°), a 7° base and +1.5° for sway — so a photo that rests at
0° still gets a canvas big enough for its entrance, and a turned photo gets one
big enough for the turn *and* the entrance. The pin/swing variant grows the
canvas around the pin (`mat_y = above = half`) instead of around the centre.

Before this, the headroom was `|rest| + 7°` with an unconditional cosine term:
past ~45° that term went negative and the canvas came out **narrower than the
mat**, which is why a 90° turn was effectively impossible. With `rest = 0` the
new formula is byte-identical to the old one.

The angle itself carries the resting tilt as a prefix, e.g. for the flip
entrance:

```
rotate='(-12.5-4*(1-q_flip))*PI/180':c=black@0.0
```

and both twins clamp `rot` to ±180°, so a knob dragged past a full turn lands on
the same angle the renderer will use.

## The size ladder (anything that keeps scaling after it lands)

FFmpeg's `scale` freezes its output dimensions at init — even with `eval=frame`,
verified on the render binary — so a photo whose size changes over time cannot
be scaled per frame. It is rendered as a **ladder**: one static `scale=W:H` per
distinct size, each `overlay` enabled only for the frame windows where that size
is on screen (`enable='between(t,…)+…'`). The sizes are sampled from the twin's
`photoState()`, so the MP4 shows what the preview shows.

The ladder samples in two phases, because they have very different needs:

- **The entrance and the depth push** are fast and cross many distinct sizes (a
  spring overshoots), so they are sampled at frame resolution and left exact.
- **The tail** — everything after the photo has landed — is slow by nature: Ken
  Burns ramps the scale to 1.08 over most of the slide, and a per-photo motion
  can breathe or pulse it. Sampled per frame that would be one level per frame,
  i.e. hundreds of `scale`+`overlay` chains for one photo, so the tail is
  **quantised**: `ladder_buckets(cw, ch)` (8…64, coarse for a small sprite, fine
  for a big one) steps of the resting scale, and the scale is rounded to a step
  before it is turned into pixels. 1.0 is a fixed point of that rounding, so a
  photo that does not scale after its entrance emits exactly the sizes it always
  did — the ladder stays byte-identical for every project without Ken Burns or a
  scaling motion (verified over 1792 generated graphs against the previous
  output).

Sampling the tail at all is the fix for a real divergence: the ladder used to
stop at the entrance and leave the last level open-ended, which held the size the
photo had when it landed for the rest of the slide. The Ken Burns checkbox
therefore zoomed in the preview and **not at all in the MP4** (only its drift
survived, since position rides the overlay expression). `test_the_size_ladder_
follows_the_twin_past_the_entrance` pins both halves down — the ladder must
reach the end of the photo's time on screen, and every held size must stay
within one bucket of the twin — and `CollageKenBurnsRenderTest` measures real
pixels: the rendered photo has to grow by the factor the twin says it grows by.

## One photo's own three lanes

The collage-wide choreography (`animation`, `exit`, `sway`, `kenBurns`) is the
default for every photo; a single photo can carry its own. The model is the same
three-lane split the text layers use — **enter / while / exit** — and each lane
inherits when it is not written, so an untouched project is untouched.

| lane | spec field | accessor (TS / Python) | falls back to |
| --- | --- | --- | --- |
| enter | `photo.anim` | `photoAnim()` / `photo_anim()` | `spec.animation` |
| while | `photo.motion` + `motionAmount` | `photoMotion()` / `photo_motion()` | nothing — the default is `none` |
| look | `photo.lookAnim` | `photoLookAnim()` / `photo_look_anim()` | nothing — the default is `none` |
| exit | `photo.exit` | `exitMode(spec, i)` / `exit_mode(spec, i)` | `spec.exit` |

What follows from a per-photo lane:

- **The slide waits for the slowest arrival.** `baseDuration` is
  `max_i(photoStart(i) + ENTRANCE_LENGTH[anim_i]) + hold`, not "the last photo's
  start plus the collage's entrance". A `swing` in the middle of a `fade`
  collage holds the slide up by its own two seconds. A collage whose photos are
  all `none` is still just the hold.
- **A swing pins only its own photo.** `pinAnchor(spec, i)` decides where the
  mat's anchor sits — hanging from its top edge instead of centred — in the
  preview (`transform-origin: 50% 0`) *and* in the render, where the sprite
  canvas is built symmetrically about the pin. One photo can hang and swing
  while its neighbours fade in place; the render's canvas for it is byte-for-byte
  the one a whole-collage swing produces.
- **The exit order stays a group decision.** How a photo leaves is its own
  (`exitMode(spec, i)` sets its fly, its spin and its direction); *when* it
  leaves comes from one shared schedule, `exitScheduleMode()` — the collage's
  exit when it has one, otherwise the first photo that leaves on its own.
  `exitTotal` is simply when the last photo to go is gone,
  `max_i(exitOffset(i) + exitFlyLength(i))`, which is the old
  "fly + longest offset" for a uniform exit. `photo.exit = 'none'` is a real
  choice: that photo stays on screen while the others sweep out.
- **`fade` is an exit too.** It travels nowhere and turns nothing: the photo fades
  out where it stands. The movers spend their last quarter fading on the way out
  (`alpha *= 1 - clamp01((qe - 0.75) / 0.25)`), while a fade spends its *whole*
  fly fading (`alpha *= 1 - qe`, linear, because that is what the render's
  `fade=t=out:st=…:d=…:alpha=1` does). It staggers like any other exit (0.08 s
  between photos) and can be set per photo or for the whole collage.
- **Depth push-back stays collage-wide.** Whether the push is on at all follows
  `spec.animation` (drop / pop / flip), because it is a property of the pile;
  but each push follows the *arriving* photo's own entrance length, so a photo
  that arrives on its own clock pushes its neighbours back on that clock.

### The while lane

`MOTION` in `collageCore.ts` and `MOTION` in `collage.py` hold the amplitudes —
dx / dy in % of the frame, rot in degrees, speed in radians per second, decay in
1/s — and both engines derive their numbers from that one table. A motion starts
when the photo has landed, eases in from the resting pose over `MOTION_RAMP`
(0.35 s) so it cannot jump, and is added on top of the entrance, Ken Burns and
the collage-wide sway (`addMotion()` / `add_motion()`). Directions and phases are
seeded per photo with `hash01(seed, i, 81/83/85)`, so a grid of drifting photos
does not slide as one block.

| motion | what it does |
| --- | --- |
| `drift` | a slow one-way pan that ramps in across the hold |
| `float` | a bob with a little lean, three unrelated slow frequencies |
| `orbit` | a slow ellipse around the resting spot |
| `shake` | a jolt as it lands, dying away — picks one photo out of a group |
| `sway` | the corkboard pendulum, for this photo alone, at its own strength |
| `breathe` | a slow zoom of its own — Ken Burns for this photo, without the pan |
| `pulse` | a rhythmic swell and settle around its resting size |
| `beat` | a punch on every beat of the soundtrack, dying away between them |
| `move` | travels to an end position you pick, and stays there |
| `turn` | turns to an end angle you pick, from its resting tilt, and stays there |
| `spin` | keeps turning at a steady rate (MOTION's `rot` is degrees per second here) |

The first five move and turn a photo only, which is exactly what FFmpeg can do
per frame: the render adds the terms to the `overlay` x/y and to the `rotate`
angle (`motion_terms()`), with the factors left in the same order as the twin so
both round the same way. Nothing is emitted for a photo without a motion, so an
untouched graph is unchanged. `MOTION_SPIN` feeds the sprite-canvas headroom
through `choreography_spin()`, which is why a shaking photo keeps its corners.

The last three change *size*, and `scale` is the one thing FFmpeg will not
animate per frame — `scale` freezes its output dimensions at init even with
`eval=frame`. So they write to `out.scale` in the twin and ride the **size
ladder**: `size_levels()` treats a scale motion the way it treats Ken Burns
(`varies` and `tail` both come out true) and the sprite is re-scaled once per
quantisation level, each level carrying the `enable` window it is true for.
Nothing else in the graph has to know.

- `breathe` ramps its zoom across the slide at Ken Burns' pace, so a lone photo
  can breathe without the whole collage panning.
- `pulse` swells on a slow sine of its own phase, seeded per photo.
- `beat` reads the beats the Beat sync section stored in `spec.beats` — they live
  in the slide's own hold clock, so the twin compares against `t - leadIn` — and
  sums a decaying punch for every beat that has already sounded. A beat still
  ahead contributes nothing, and the punch eases in with `MOTION_RAMP` so a photo
  that lands mid-beat cannot pop. `beatPunches()` / `beat_punches()` thin a long
  list to at most `BEAT_PUNCH_MAX` (24) evenly spaced beats, because each one adds
  two `enable` windows per ladder level to the graph.

`move`, `turn` and `spin` write dx / dy / rot like the first five, so they need no
new render machinery at all — only headroom: `motionSpin()` returns a `turn`'s own
target angle (not a table amplitude) and 180° for a `spin`, which
`choreography_spin()` caps at the mat's diagonal as it already did.

#### Targets and durations

Two of the motions have an **end state you pick** rather than an amplitude, so a
strength slider would only scale a target you chose explicitly:
`COLLAGE_TARGET_MOTIONS` (`move`, `turn`) ignore `motionAmount`, and the editor
hides the slider and shows the target instead. On the preview the target is a
dashed **ghost** of the photo at its end place, drawn from the photo's *resting*
geometry — never from the playhead, because the end state does not depend on when
you look — and that ghost carries a handle you can drag:

- `move` puts the handle at the ghost's centre. Dragging it writes `moveX` /
  `moveY` (% of the frame, ±60); **Shift** snaps to 5 %, **arrows** step 1 %.
- `turn` puts a **knob on the ghost's top edge** — the same knob that turns a
  photo's resting tilt — and dragging it around the pivot writes `turnTo` (±180,
  clockwise from that resting tilt); **Shift** snaps to 15°, **arrows** step 1°.
  The knob hangs off the ghost's *resting* pivot (`restPivotClient`: the
  placement's `ax`/`ay`, with none of the animation's `dx`/`dy`), turns with the
  ghost, and lifts `matH/2` above that pivot — nothing at all when the photo is
  pinned, since then the pivot is already its top edge — plus a fixed screen gap,
  counter-scaled by `1/k` so it stays the same size at any zoom.

So both target motions are aimed by pointing at the preview, and the number
fields in the selected-photo bar show the same numbers the handles write.

`motionDuration` says how long a motion plays, counted from the photo's landing
(0 or missing = the rest of the slide, which is what every existing project does,
so those graphs are unchanged). In the editor it is a **slider**, not a number
field: 0 … min(`MOTION_DURATION_MAX`, the slide's own length rounded up), reading
`all` at 0 and `1.4s` otherwise, so the longest a motion can be asked to run is
never longer than the slide. `setPhotoMotionField` snaps a value between 0 and
`MOTION_DURATION_MIN` up to that minimum, so a stored spec cannot disagree with
what both engines clamp it to. What happens when that time is up depends on the
motion, and `COLLAGE_ONESHOT_MOTIONS` is the list:

| kind | motions | when its time is up |
| --- | --- | --- |
| one-shot | `move`, `turn`, `spin`, `drift`, `breathe` | it holds the end state it travelled to |
| looping | `float`, `orbit`, `shake`, `sway`, `pulse`, `beat` | it settles back to rest over the last `min(0.4, D·0.3)` s |

A loop that simply stopped would freeze mid-bob — or mid-orbit, off-centre — which
looks broken, so the settle envelope multiplies its amplitude back to zero. A
one-shot's `span` *is* the duration (`move` and `turn` ease with a smoothstep, so
neither end jolts); a `spin`'s angle is `rate · min(u, D)`, which is why it holds
the angle it reached instead of unwinding.

### The look lane

A look changes the photo's *colour* while it is on screen, and it is its own lane
because it is not part of `PhotoState`: adding a field there would mean fourteen
literal edits per engine in the twin comparator, so `photoLookState(spec, i, t,
leadIn)` returns its own `{sat, bright}` where 1 means "untouched".

| look | what it does | how it reaches the MP4 |
| --- | --- | --- |
| `toColor` | arrives in black & white, blooms into colour as it lands | `hue=s='…'` |
| `toBw` | the colour drains away across the slide | `hue=s='…'` |
| `flicker` | an old-projector brightness jitter as it lands, dying away | `sendcmd` + `eq@fx{i}` |
| `invert` | arrives as a negative, flips to its real colours once it has landed | `negate=enable='lt(t,…)'` |
| `hueFlip` | the colour wheel turns half way round, ending in opposite colours | `hue=H='…'` |

The split is FFmpeg's, not ours. `hue` re-evaluates its expressions on **every
frame**, so a saturation ramp is one exact expression; `eq` evaluates them once at
init (both measured on the render binary), so brightness needs the same quantised
`sendcmd` staircase the depth dim uses — into its *own* `eq@fx{i}` instance,
because the dim's `eq@dim{i}` is already using contrast, saturation and
brightness together to stay multiplicative. `eq` with `contrast=B` and
`brightness=128*(B-1)/255` is multiplicative to within half a unit, which is what
CSS `brightness(B)` does.

All of them sit exactly where the dim sits — after the mat is padded, before
`format=rgba` and the shape mask — so the frame border and a `native` letterbox
react the way the CSS filter on the preview's mat does, and no alpha is lost. The
preview builds the same string in the same order (`matFilterCss`):
`brightness(1 - dim) hue-rotate(hue) saturate(sat) brightness(bright) invert(inv)`.
`photoLookState()` returns all four numbers, and 1 / 0 means "untouched".

Two traps, both measured on the render binary:

- **`hue=H=` takes radians.** The filter multiplies the expression by 180/π
  itself, so a half turn of the colour wheel is `H='3.1416*…'`, not `H='180*…'`.
  The twin's `hue` stays in degrees because that is what CSS `hue-rotate()` wants;
  the render converts once, in `look_segment()`.
- **`negate` has no amount.** It is on or off, so `invert` is a hard flip at the
  moment the photo has landed (`t0 + max(entrance, INVERT_MIN)`, so a photo with
  no entrance of its own still gets 0.45 s of negative to develop out of). CSS
  `invert(1)` is the same hard flip, which is why this look matches exactly while
  `hueFlip` only matches approximately: CSS `hue-rotate()` is a matrix
  approximation of a real rotation, so on a saturated primary the preview can read
  a shade lighter than the MP4. On photographs the two are indistinguishable.

### Where it is in the editor

Selecting a photo — on the preview or on its chip — opens the per-photo panel
under the stage: size and tilt, and a second row with **entrance / motion / look
/ exit**, plus — when the motion has one — its **duration** and its **target**. Each select's first entry is the collage's own setting ("Drop ·
collage"), which is also what picking it again does: the override is removed, so
the photo follows later changes to the collage-wide choreography. A motion brings
a strength slider (25–200 %, hidden for `move` and `turn`, whose strength is the
target you picked), an **over …s** duration field, `end x` / `end y` for a move
and `end angle` for a turn, and an **Inherit** button drops every lane at once.
The motion select is grouped by what a motion needs: *keeps moving*, *goes
somewhere — you pick the end*, and *changes its size — a little slower to render*,
because the last group is the one that makes the render build a ladder. Picking a
different motion drops the targets that no longer belong to it, so a `moveX` never
survives a switch to `turn`. A chip whose photo has any lane of its own carries a lime dot on its
number. Changing a lane never restarts the preview — the clock keeps running and
only clamps when the slide's derived duration changes.

## Preview and MP4: what makes them agree

The two engines are written to say the same thing, and three kinds of test hold
them to it. Each compares against *numbers*, never against the other engine's
opinion of itself:

1. **Twin tests** (`test_collage_twin.py`) walk the same case list through
   `src/collageCore.ts` in Node and `backend/app/collage.py` in Python and compare
   placements, per-photo states, look states, exits, cameras, durations,
   normalization and the generated filtergraph — value by value. That includes the
   **CSS filter string the preview paints**: `matFilterCss` lives in the core so
   the driver can emit it, and Python re-derives it from `photo_state().dim` +
   `photo_look_state()` and checks the parts, their order and their values
   (22 790 strings across the cases). If the preview dims a photo by 0.791, the
   MP4 dims it by 0.791.
2. **Pixel tests** (`test_collage_animation_ffmpeg.py`) render the Python engine's
   graph with the real FFmpeg binary and measure the pixels back: where a lane is,
   how big, what colour, how opaque, at which frame. `CollagePreviewParityRenderTest`
   is the closing link — one render holding a `move`, a `turn`, a `spin` and a
   `drift`, every look and a `fade` exit, each measured against the twin at exact
   frame times.
3. **Byte identity** for what has not changed: a matrix of 3584 specs is dumped to
   filtergraph strings and diffed against the previous commit, so a feature that
   touches one motion cannot silently move 3583 other graphs.

Where the MP4 cannot be *exactly* the preview, the reason is FFmpeg's, and it is
measured rather than assumed:

- **Position snaps to the even chroma grid.** `overlay` composites in yuv420p, so
  its `x`/`y` land on even pixels (measured: an x of 81.6 lands on 80), and a flat
  colour carries a chroma halo at its edges. The preview positions a mat to
  sub-pixel accuracy; the render cannot. Worst residual measured, against the
  photo's own offset at rest: **1.5 px** on a photo that travels 66.6 px, **1.1 px**
  on one that spins, **0.0 px** on one that only turns — at 320×180, i.e. ~0.5 % of
  the frame, and a sixth of that at 1920×1080.
- **CSS `hue-rotate()` is a matrix approximation** of a true rotation, so for a
  saturated primary the preview reads a shade lighter than the yuv-exact MP4
  (green → CSS (255,110,255) vs ffmpeg (255,76,255)). The render is the accurate
  one; the difference is documented, not "fixed".
- **`negate=enable=…` is binary**, so the `invert` look is a hard flip at its
  moment (alpha preserved), not a crossfade — which is what the preview does too.
- **`eq` evaluates its expressions once at init**, so brightness looks ride a
  quantized `sendcmd` staircase and only saturation/hue (`hue=s=`, `hue=H=` in
  *radians*) and position/rotation/opacity are truly per-frame. Scale needs the
  same treatment as brightness, which is what the size ladder is for.

## Tests

- `backend/tests/test_collage_twin.py` — the case list spans layouts ×
  entrances × shapes × photo counts × aspects × timings, plus dedicated cases
  for exits, cameras, per-photo shapes, beat snapping and the four per-photo
  lanes. Every case compares placements (incl. Free `rot`), per-photo motion,
  camera, duration, `free_from_displayed`, normalization, the lane accessors
  (`photoAnim` / `photoMotion` / `photoLookAnim` / `exitMode(i)` / `exitOffset`
  / `exitFlyLength(i)` / `exitTotal` / `baseDuration` / `pinAnchor(i)`) and the
  generated filtergraph
  (the flip expression must contain the resting tilt; `_n()` writes 4 decimals,
  so command comparisons use 1e-4). `test_the_size_ladder_follows_the_twin_past_
  the_entrance` parses the ladder's sizes and enable windows back out of the
  graph and checks them against `photoState()`. The lane cases include the scale
  motions (`breathe` / `pulse` / `beat`, with a stored beat list), a beat list
  long enough that both engines have to thin it to the same subset, every look
  animation sampled across the slide, and a junk look / junk motion that both
  sides must drop the same way. The lane cases also compare the **CSS filter
  string** each engine paints at each sampled moment (`matFilterCss`, emitted by
  the driver as `filters`): same parts, same order, same values.
- `backend/tests/test_collage_animation_ffmpeg.py` — real FFmpeg renders, pixels
  measured. `CollageFreeTiltRenderTest` renders one red photo on a grey bed at
  0°, ±45°, ±90°, 135°, 180° and checks the bounding box turns the right way and
  that no red is lost: a 90° turn comes back 72×96 where the straight one is
  96×72, with the same amount of red inside. `CollageKenBurnsRenderTest` checks
  the zoom actually happens (the photo's bounding box on the last frame against
  an early one, by the factor the twin predicts). `CollagePhotoLanesRenderTest`
  renders collages of flat primary colours — one colour per photo — so a frame
  can be split back into "which photo is where": a drifting photo has to travel
  by the twin's own amounts while its neighbour does not move at all, a photo
  whose exit is `none` has to still be on screen at the end while the others have
  swept out, and a per-photo swing has to hang from a canvas built symmetrically
  about its pin and swing while its neighbour holds still.
  `CollagePhotoLooksRenderTest` measures the lanes that change size and colour: a
  photo with `toBw` has to end grey while its neighbour keeps its colour and a
  `toColor` photo has to start grey (found by luma band rather than by colour,
  since a drained photo has none), `breathe` has to grow the bounding box by the
  factor the twin predicts through the ladder, a `beat` punch has to be widest
  just after a beat and back to its resting size after the last one, and
  `flicker` has to jitter the brightness while it lands and be quiet by the end —
  with the loudest rendered frame being the twin's loudest ask — and the two
  flipping looks have to arrive as a negative / in their own colour and end in
  their opposite, with a part-way frame that is neither.
  `CollagePreviewParityRenderTest` is the end of the chain: one render holding a
  `move`, a `turn`, a `spin` and a `drift` — spaced so no photo ever travels into
  another, since a covered lane's centroid is pulled towards whatever covers it —
  measured at exact frame times against the twin's own centre, plus a second
  render in which every look animation and the `fade` exit run together and each
  pixel is compared with `photoLookState` / the twin's alpha. The positional slack
  is the render's even-chroma snap (see above), and the test also demands the
  travel actually happened rather than sitting still inside the slack.
  `CollageMotionTargetRenderTest` measures the motions that go somewhere: a
  `move` has to travel exactly the picked % of the frame and stay there, a `turn`
  has to end standing up with the same amount of photo inside (its headroom comes
  from the target angle), a `spin` has to hold the angle it reached when its time
  is up, a looping `float` with a duration has to be back at its resting pixel by
  the end rather than frozen mid-bob, and a `fade` exit has to leave the photo
  exactly where it stood while its opacity follows the twin's ramp at three
  moments.
- `backend/tests/test_collage_tilt_handles.py` — the turn-a-photo controls,
  which are pointer maths with no render counterpart: Python writes the cases
  and an independent computation, Node evaluates the TypeScript helpers
  (`tests/tilt_handles.mjs`), including which side of the mat the knob goes on
  and where the selection ends up when the strip reorders or removes a photo;
  the last case is a real twin — the `rot` a stored tilt ends up as in the Free
  placements must match `collage.py`.
- `backend/tests/test_collage_camera_ffmpeg.py`, `test_collage_orientation.py` —
  camera moves and EXIF orientation through the same chain.
