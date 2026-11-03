# One colour description for the whole timeline

Date: 2026-11-03

Symptom reported: *"the PREVIEW DETAIL button still gives a scrambled result … if I
disable 'text is shown on this picture' I can see the movie in the final output but
still without the karaoke text; if I enable it I get the scrambled movie."* A frame
of that preview looked like chroma confetti over the whole picture. The caption is
not what damages the picture — the caption decides **which parts enter the
timeline**, and the parts disagreed about what their pixels mean.

## The mechanism

Every slide is rendered to its own file, then the holds and crossfades are stitched
(`timeline.ffconcat`). The stitch may copy streams (`-c:v copy`) or, if it cannot
prove the parts are compatible, re-encode. Either way **an MP4 carries one colour
description for the whole file — the first part's.**

A photo and a movie do not arrive with the same one:

| part | what FFmpeg reports |
| --- | --- |
| hold cut from a JPEG | `yuvj420p(pc, bt470bg/unknown/unknown)` — full range, 601 matrix |
| hold cut from a movie | `yuv420p(progressive)` — limited range, and no matrix stated |

`format=yuv420p` in the normalizer changes the *pixel format*, not the range or the
tags, so the photo part stayed full-range. Measured with a real FFmpeg, on two
2-second parts stitched with `-c:v copy`: a frame from the movie part reads back
`0 0 0` on its own and **`16 16 16`** once it follows the photo part — every black
in the movie lifted to grey, because the file now promises full range and the
player expands limited-range data with it. The same experiment on a saturated bar
gives `70 0 0` → `78 15 16`: hue rotated *and* range stretched, by the matrix swap.
How badly that reads depends on the player and on the encoder that wrote the parts;
a hardware encoder that also disagrees on the parameter sets turns it into the
undecodable garbage in the screenshot.

**Why the text flag seemed to cause it:** the fast preview skips holds with no text
(`#76`'s area, and `docs/text-on-picture-toggle.md`). Switch the caption on and the
JPEG-derived slide joins the timeline — usually as the *first* part — so from then
on the file speaks the photo's colour language while the movies do not.

## The rule

**One canvas, one range, one matrix, one declaration — and the stitch verifies it.**

1. `timeline_frame_filter()` (the filter every stitched part passes through, holds
   and both `xfade` inputs) scales with
   `out_range=limited:out_color_matrix=bt709`. That *converts*: a full-range photo is
   brought into limited range, a 601 photo into the 709 matrix, and a frame that is
   already right passes through untouched.
2. `TIMELINE_COLOUR_ARGS` (`-color_range tv -colorspace bt709
   -color_primaries bt709 -color_trc bt709`) is stamped by `encode_args_for()` on
   **every** part and by the join's software fallback, so each file says what its
   pixels now are. Measured outcome: the file reports `yuv420p(tv, bt709/unknown/
   unknown)` — range and matrix, the two fields that move pixels, are declared;
   primaries/transfer stay unspecified, which is what an SDR file without them
   means to a browser. Passing them anyway is what the *frame* properties need on
   the paths that do honour them, and `-x264-params colormatrix=…` is not an option
   here: `encode_args_for` is shared with QSV and VA-API, which have no such
   parameter. Stamping without converting would be a lie; converting without
   stamping leaves the file declaring the first part's old values. Both are needed.
3. `_stream_signature()` now probes `color_range, color_space, color_primaries,
   color_transfer` alongside codec/profile/level/size/`extradata`, so a part that
   somehow disagrees cannot be copy-joined — and `_parts_share_parameter_set()` logs
   *which part* and *which parameter*, because that decision is invisible in the
   output and costs a full re-encode.

After the fix the same experiment reads back `0 0 0`, and every part of a real mixed
timeline reports `yuv420p(tv, bt709/…)` — including the stitched file, whose tags used
to be `yuvj420p(pc, bt470bg)`.

## What it does *not* do

It does not tone-map HDR. A `bt2020`/PQ source is still reduced to 8-bit 4:2:0 by
the timeline canvas; the difference is that the result is now described
consistently instead of per part. Nor does it touch the *per-slide* segment files
(`segment-0000.mp4` may still be `yuvj420p`), which are never stitched: only holds
and transition handles reach the concat, which is why one filter is enough.

## Checking a file

```bash
ffmpeg -hide_banner -i output/movie.mp4 2>&1 | grep Video:
# want: yuv420p(tv, bt709/bt709/bt709, progressive)
# not:  yuvj420p(pc, bt470bg/unknown/unknown, progressive)
docker compose logs slideshow | grep "cannot be copy-joined"
```

## Where the code is

- `backend/app/renderer.py` — `timeline_frame_filter`, `TIMELINE_COLOURSPACE`,
  `TIMELINE_COLOUR_ARGS`, `SIGNATURE_FIELDS`, `encode_args_for`,
  `_stream_signature`, `_parts_share_parameter_set`.
- `backend/tests/test_renderer.py` — `test_stitched_parts_carry_one_colour_description`,
  `test_a_colour_mismatch_alone_refuses_the_copy_stitch`,
  `test_the_refused_stitch_names_the_differing_parameter`, and the retargeted
  `test_holds_and_xfades_normalize_to_the_configured_canvas`, which now derives the
  expected filter from `timeline_frame_filter` instead of retyping it.
- `docs/hardware-transcoding-ds918plus.md` — the neighbouring problem (parts from
  two encoders must not meet in one stitch); same machinery, same reasoning.
