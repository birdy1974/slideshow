# A slide's filter graph is one argument, and one argument has a ceiling

Reported as `Failed — [Errno 7] Argument list too long: 'ffmpeg' · 23%`.

That error is not FFmpeg's. It comes from `execve()`, before FFmpeg is running, so
nothing in the graph was parsed and no frame was decoded: the command line could not be
handed to a process at all. The percentage is how far the job got, i.e. how many slides
happened to fit.

## What the limit actually is

Linux caps **one single argument** at `MAX_ARG_STRLEN` = 32 pages = 131 072 bytes.
`ARG_MAX` (~2 MB) is the budget for the whole vector and is almost never the binding
constraint here. A render builds one slide's compositing as a single `-filter_complex`
value, so a busy slide passes 131 072 bytes while the command line as a whole is nowhere
near 2 MB — which is why the same project renders its first slides and dies on a later
one, and why "shorten the graph" is not the same fix as "raise the limit": there is no
limit left to raise.

## Where the bytes come from, measured

`collage_graph()` composites every photo through a *size ladder*: the preview quantises
zoom into buckets and the render bakes one still per (size, reveal-step, reveal-kind) the
photo passes through, so FFmpeg never has to evaluate a `geq` mask for a frame that shows
nothing. The baking is deliberate — it is the difference between a NAS exporting a slide
in seconds and in minutes — but it means the graph grows with the slide, not with its
photo count alone.

It grows less than it looks, though. Counting the emitted lines of a 1920×1080, 25 fps,
one-pad-per-photo slide with 12 photos, `zoom` entrance and camera, `curtain` reveal and a
12 s hold: 56 834 bytes in 429 lines — **178 level lines at 192 B, 190 overlay lines at
76 B, 12 `split` fan-outs at 112 B, 49 other lines at 141 B**. Each level line carries that
level's `select` window, its `scale`, its mask and the photo's `rotate=` curve; each
overlay line carries the x/y expressions and the `enable` windows. An earlier draft of this
page said "about 2 kB per level line", which is off by an order of magnitude and was not
measured; the 679 lines the same draft mentions were a different probe's line count.

## The transport fix

`app/ffmpeg_argv.py` + `Renderer._run_ffmpeg`: count the graph in **bytes**, and above
96 KiB write the same text to `work/<job>/<output-stem>-<position>.filter` and exec
`-/filter_complex <path>` instead. 96 KiB, not 128: the kernel counts the NUL, a caption's
worth of CJK is 3 bytes a character, and a threshold that decides *how* to pass a graph
should err early. The graph itself is not shortened, so the render is unchanged — see the
framemd5 test, which encodes one real collage slide both ways and requires the frames to
be identical.

Which option reads a graph from a file is a property of the binary, not of the version
string: `-filter_complex_script` was dropped by FFmpeg 9, and the generic `-/option file`
form works on the reachable builds but is documented nowhere (`-h full` lists zero `-/`
options). So the renderer asks the binary once per process, and if it answers "unrecognized
option" to both names, the command is left exactly as the builder wrote it — the render
then still fails on Linux with E2BIG, which is honest, rather than failing everywhere with
an invented option.

Covered: every render command (`_run_ffmpeg` is the one choke point, including the
hardware→CPU `cpu_variant` retries and the preview render). Not covered, because none of
them can reach the ceiling: the filmstrip, crop, text-effect and transition-preview
builders, which cap their own element counts.

## The mask ladder is now computed, not baked

The count of levels was the only thing worth cutting, and the reveal sweep held most of
them: an uncovered photo needs one still per step of its sweep — up to 32 per photo, each
with its own `select`, `geq`, `rotate`, `overlay` and `enable`, and each an arm of the
photo's `split`. But the sweep's step is a *pure function of time*: the photo's arrival
window and the family's easing are both known when the graph is written, and `geq` exposes
the frame timestamp as `T`. So `reveal_step_expr()` writes that function once and the level
carries every step its size was holding still for (`app/collage_reveal.py`,
`size_levels()` in `app/collage.py`).

What that removes, per revealing photo: the mask levels, their share of the `split`
fan-out, and one `enable` window list per step. What it leaves alone: the *size* half of
the ladder, and the steps of a departure that the arrival's clock does not describe — a
mirrored (`reverse`) or replacing exit re-runs or replaces the state, so its frames get
baked stills and `merge_upto` is the last frame the arrival's own formula covers. A size
with a single step to show keeps its short literal too: there is no level to remove, and
the expression is 400 bytes where the literal is 8.

At 1920×1080, 25 fps, one pad per photo, rounded frame with shadow:

| slide | levels per photo | graph before | graph now |
|---|---|---|---|
| 6 photos, no camera, `curtain` | 15 → 2 | 28 488 B | 10 848 B (62% smaller) |
| 6 photos, `zoom` camera, `curtain` | 15 → 2 | 28 742 B | 11 102 B (62%) |
| 6 photos, `telescope`, `iris` | 15 → 2 | 31 432 B | 11 296 B (65%) |
| 12 photos, `zoom` camera, `curtain`, 12 s hold | 14–15 → 2 | 56 834 B | 22 125 B (62%) |
| 12 photos, `zoom` + Ken Burns + `breathe`, `curtain` | 20+ → 6–7 | 107 469 B | 43 536 B (60%) |
| 6 photos, `zoom` + Ken Burns + `breathe` + `mosaic`, 20 s hold, `reverse` exit | 40 → 25 | 139 838 B | 87 410 B (38%) |
| 9 photos, `droste`, `tumble` (no reveal) | 13–14 | 38 536 B | 38 536 B (0%) |

The last two rows are the point and the limit of it: the shape that was reported — a slide
whose graph was over the 131 072-byte ceiling at 6 photos — comes in under the ceiling, and
under the 96 KiB threshold too, so it stops taking the file route at all. A slide with no
mask reveal to compute gains nothing, because nothing about a size ladder was redundant. (`normalize_collage` keeps at most `MAX_COLLAGE_PHOTOS` = 12 photos
per collage, measured by normalising a 20-photo spec, so "more photos" cannot make a graph
grow past that either way.)

The reported failure had `split=82` and `split=86`: 82 levels on one photo. A photo's
*mask* levels are now one, but its *size* levels are still one per bucket crossing, so a
busy slide can still pass 131 072 bytes and the transport fix above stays load-bearing.

### Why the arithmetic is exact rather than nearly so

Both are measured claims in `app/collage_reveal.py`'s docstring, and each has a test:

* **The clock.** `geq`'s `T` is `round(pts * 1e6 / tb_den) / 1e6` — microseconds, rounded —
  while the ladder samples at `k / fps` and the preview divides the same way. At 25 and 50
  fps the two are the same number; at 24, 30 and 60 they differ by 3.3e-7 s, which is
  enough to move a `floor` across a boundary and hand a frame a whole step of mask the
  preview does not show. So the expression recovers the frame index (`floor(T*fps+0.5)`)
  and divides again. `test_the_expression_agrees_with_the_sampler_at_ffmpeg_clock_rates`
  walks every frame of a sweep at each rate the renderer can produce.
* **The completion clamp.** `reveal_quantise` returns exactly 1.0 once the sweep is over,
  and it decides that on the *eased* progress — a frame can sit a hair below 1 before the
  curve and land on 1 after it. Clamping on the raw progress instead left such a frame at
  31.5/32, a sliver of mask for the rest of the slide (measured: `mosaic`, 25 fps, a
  19-frame sweep, frame 69). `min(1, …)` over the quantised value is the correction, and
  shorter than the `if(gte(…))` it replaced.
* **The shape of the arithmetic.** The easing is multiplied out — `q*q*(3-2*q)` — never
  `pow(q, 2)`, because the preview evaluates that same polynomial in JavaScript and both
  engines must reach the same double before a `floor` decides a step.
  `test_the_expression_easing_is_the_python_easing` checks the three curves against the
  Python they mirror at 201 points.

### What "unchanged pixels" means here

The claim is about the render, and the render feeds each photo as one still looped at the
slide's frame rate — so the only thing a level's shape can change is which pixels are
uncovered on which frame. That is what is compared: `MergedLadderFramesTest` in
`backend/tests/test_collage_reveal_render.py` builds each graph twice, with and without the
expression, renders both, and requires the raw frames to be equal byte for byte (it needs an
FFmpeg binary and self-skips without one). Four slide shapes are in that test, and 12 more
were measured while writing this: flat-colour photos at 320×180, 640×360 and 1920×1080,
with camera moves, Ken Burns, motions, and `none` / `zoom` / `swing` / `reverse` exits.

One caveat, recorded because it was chased for a day and is not a defect: with
`testsrc2` — a source whose every frame is different noise, which no photo input ever is —
the two graphs disagree on up to 12 frames of 235. Nothing about the mask differs (the same
graphs on flat sources agree on every byte of every frame); what differs is which source
frame a level's stream hands the `overlay`, since the merged level is asked for every frame
of its window and a baked step for one. On a slide made of still images that is invisible by
construction, which is why the test feeds flat colours rather than `testsrc2`.

## Still open: the size ladder

`geq` computing a step removed the mask's redundancy. The *size* half is still one still
per bucket crossing, and it is what a heavy slide's graph is now made of: the `zoom` +
Ken Burns + `breathe` row above is 43 536 bytes with 6–7 levels per photo, and every level
after the first is a size change. Two options, unchanged from the earlier draft:

1. **Thin the ladder under pressure** — when the graph passes the ceiling, rebuild it with
   coarser buckets (`ladder_buckets`) for the levels whose windows are one or two frames.
   A short-lived size step is invisible; ~190 B of graph per level is not.
2. **Move the ladder into `scale`** — `scale` supports `eval=frame`, so one chain could
   carry the whole size step function as a `t` expression: ~20 characters per step instead
   of a line each, and the `split` fan-out disappears with them. Now that the mask is
   computed, this is the *only* remaining reason a still photo needs more than two levels.

Neither is a substitute for the transport fix: the 12-photo heavy slide in the table is
170 383 bytes with its mask levels merged, which is over the ceiling by itself, and a
slide's captions and per-photo curves are counted in the same argument.
