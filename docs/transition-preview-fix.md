# "Browse all effects" shows no examples — root cause & fix

Date: 2026-09-11 · Status: **root cause proven, fix ready to apply**

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

## The fix

1. **[must] Make the temp file acceptable to FFmpeg** — one line, either form:
   - keep the name, declare the muxer: add `"-f", "mp4"` to the command's output
     options (the documented FFmpeg remedy, quoted in the error itself); or
   - keep an `.mp4` extension on the temp name
     (`target.with_suffix(".tmp.mp4")` → `fade.tmp.mp4`), so extension-based
     format detection keeps working and the atomic `tmp.replace(target)` is
     preserved. Failure paths already unlink the temp file, and the test that
     asserts "no partial files left behind" still passes.
   Either is safe; the `-f mp4` variant is the smallest diff, the `.tmp.mp4`
   variant keeps "the extension always tells the truth" hygiene.
2. **[must] Bump `CACHE_VERSION` 1 → 2 in the same change.** The manifest loader
   ignores manifests with an older version, so every deployment automatically
   forgets the 191 `failed` records and goes back to *pending* — the gallery
   then offers *"Render all 191 missing"* and one click rebuilds the whole
   cache with the fixed command. Without the bump, existing installations stay
   stuck (see the self-heal trap above) until they manually clear the cache.
3. **[should] Close the test gap**: extend the stub so it *refuses* output names
   whose extension doesn't map to a muxer (mirroring real FFmpeg), or simply add
   an assertion that the render command carries `-f mp4` / the temp name ends in
   `.mp4`. One new test: "a failed render leaves no output and records the
   reason".
4. **[nice] Surface the recorded reason in the GUI**: the manifest already keeps
   the real stderr per slug, but the gallery only prints a generic
   *"Preview could not be rendered"*. Showing `status.items[slug].error` as a
   tooltip / detail line (and a counter for failed tiles) would have made this
   diagnosis instant from the browser.

## Verification

With the fix applied (variant B), the exact production command renders a valid
5.7 KB example clip for `fade.mp4.part` with the same real FFmpeg binary that
failed without it; the full cache pass then renders all native transitions and
records the GL ones per its normal fallback logic on stock builds (the custom
xfade-easing build in the container renders all 191).
