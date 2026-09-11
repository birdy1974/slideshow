# "Browse all effects" shows no examples — root cause & fix

Date: 2026-09-11 · Status: **implemented** (fix + CACHE_VERSION bump + stub guard + GUI error surfacing)

Symptom from improvements.md: the transition gallery shows *"Preview could not be
rendered"* on the tiles and *"0/191 cached"* in the footer — not a single example
clip ever renders.

## What the gallery messages actually mean

- *"0/191 cached"* → no MP4 exists in `<config>/transition-previews/` (`ready`
  counts files on disk).
- *"Preview could not be rendered"* → the tile's state is `failed`, i.e. the
  backend **ran** FFmpeg and FFmpeg returned an error (a transition the installed
  build can't do would instead say *"Falls back on this FFmpeg build"*).
- So: FFmpeg is present, the cache directory works, and **every single render
  command fails before it produces a file**.

## Root cause (proven, reproduced end-to-end)

`TransitionPreviewCache._render()` writes its FFmpeg output to a temporary name so
the cache only ever contains complete files:

```python
tmp = target.with_suffix(".mp4.part")   # e.g. fade.mp4.part
```

FFmpeg **chooses the output muxer from the file extension**. `fade.mp4.part` ends
in `.part`, which matches no muxer, so FFmpeg refuses to start:

```
[AVFormatContext @ ...] Unable to choose an output format for 'fade.mp4.part';
use a standard extension for the filename or specify the format manually.
[out#0 @ ...] Error initializing the muxer for fade.mp4.part: Invalid argument
Error opening output file fade.mp4.part.
```

That stderr line is exactly what `_record(slug, "failed", reason)` stores in
`manifest.json` for every entry. Reproduced in the sandbox by running the real
cache code against a real FFmpeg binary: `ensure("Fade")` → `PreviewUnavailable:
Error opening output files: Invalid argument`, status `ready=0`. Every one of the
191 transitions fails identically at the muxer-selection stage — before any
encoding — hence *"0/191 cached"* everywhere, while the main renderer (which
always names its outputs `.mp4`) and the per-slide quick preview keep working.

## Why the tests never caught it

`backend/tests/test_transition_previews.py` runs against a **stub FFmpeg** that
fabricates the output file for every invocation *whatever its name is*
(`out.write_bytes(b"STUBMP4")`). A real FFmpeg's "unknown extension" error can
never occur there, so the suite stays green while production fails on every clip.

## Why it does not self-heal

Two traps in the current code:

1. `ensure()` short-circuits on a recorded status: if the manifest says
   `failed`, it raises immediately **without retrying**.
2. Therefore even the gallery's *"Render all N missing"* button does nothing for
   the broken entries — it calls the same `ensure()`; the pass completes
   "instantly" without rendering anything. Only *Clear cache* (the DELETE
   endpoint) resets the manifest.

## The fix (all four items implemented)

1. **[must, done] The temp file is now acceptable to FFmpeg** — the render
   command declares the muxer explicitly: `"-f", "mp4"` before the output path
   (the remedy the FFmpeg error itself recommends). The atomic
   `tmp.replace(target)` and the failure clean-ups are unchanged.
2. **[must, done] `CACHE_VERSION` bumped 1 → 2.** The manifest loader ignores
   older manifests, so every deployment automatically forgets the 191 `failed`
   records and returns to *pending* — the gallery offers *"Render all N
   missing"* and one click rebuilds the whole cache with the fixed command.
   Verified: a poisoned v1 manifest + the new code reports `failed=0,
   pending=191`.
3. **[should, done] Test gap closed.** The test stub now behaves like real
   FFmpeg at the muxer stage: it refuses output names whose extension maps to
   no muxer unless the command declares `-f mp4` after the inputs. New tests:
   the render command must carry `-f mp4` (asserted, including that it applies
   to the `.part` temp file); a v1 manifest must be forgotten; and a guard test
   proves the stub itself rejects `x.mp4.part` — if that guard ever goes green
   again the suite would be blind to the real failure mode.
4. **[nice, done] The recorded reason is surfaced in the GUI.** Failed tiles in
   the picker and the gallery carry a red `failed` flag whose tooltip shows the
   exact FFmpeg error; the gallery's detail panel prints
   *"Preview could not be rendered: <reason>"* for the focused tile; both
   footers show an `⚠ N failed` counter whose tooltip carries the last recorded
   error.

## Deployment note

After pulling this change and restarting the container, open the transition
gallery once: the old `failed` records are gone (v2 manifest), and
*"Render all 191 missing"* rebuilds the whole catalogue with the fixed command —
rendered once, then cached on the config volume as before.

## Verification

Verified against a real FFmpeg binary: the exact production command that failed
now renders valid example clips (Fade, Dissolve and Wipe Up all READY,
`failed` 191 → 0), the backend suite passes 276/276, and the frontend build is
clean.
