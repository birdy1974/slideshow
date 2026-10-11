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
| `wait` | seconds of pause before this photo lands |
| `size` | scale factor of its mat, 0.5–3.0 (100 % = the arrangement's own size) |
| `crop`, `filter` | the same look the single-photo editor uses |
| `shape` | its own mat shape/aspect, overriding the collage frame |
| `slot` | its place in the arrangement (order ≠ position) |
| `cx`, `cy` | Free arrangement only: where it sits, in % of the frame |
| `rot` | Free arrangement only: its resting tilt in degrees, clockwise, −180…180 |

`rot` is written only when it is not zero (`setPhotoRot` drops it at 0°), so a
project nobody ever turned stays byte-identical through a save.

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
selected mat's exact box, corner radius and transform every frame: it turns,
scales, fades and flies out with the photo. Its opacity never drops below 0.45,
so a photo picked from the strip before it has landed still shows where it will
appear. The chip's dotted outline is a pseudo element for the same reason — its
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

## Tests

- `backend/tests/test_collage_twin.py` — 37 twin cases: placements (incl. Free
  `rot`), per-photo motion, camera, duration, `free_from_displayed`,
  normalization, and the generated filtergraph (the flip expression must contain
  the resting tilt; `_n()` writes 4 decimals, so command comparisons use 1e-4).
- `backend/tests/test_collage_animation_ffmpeg.py` — real FFmpeg renders, pixels
  measured. `CollageFreeTiltRenderTest` renders one red photo on a grey bed at
  0°, ±45°, ±90°, 135°, 180° and checks the bounding box turns the right way and
  that no red is lost: a 90° turn comes back 72×96 where the straight one is
  96×72, with the same amount of red inside.
- `backend/tests/test_collage_tilt_handles.py` — the turn-a-photo controls,
  which are pointer maths with no render counterpart: Python writes the cases
  and an independent computation, Node evaluates the TypeScript helpers
  (`tests/tilt_handles.mjs`), including which side of the mat the knob goes on
  and where the selection ends up when the strip reorders or removes a photo;
  the last case is a real twin — the `rot` a stored tilt ends up as in the Free
  placements must match `collage.py`.
- `backend/tests/test_collage_camera_ffmpeg.py`, `test_collage_orientation.py` —
  camera moves and EXIF orientation through the same chain.
