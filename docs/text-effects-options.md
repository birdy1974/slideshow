# Dynamic text effects — options & GUI integration

Date: 2026-09-11 · Status: **implemented as Option 1** — see [text-effects.md](text-effects.md) for what shipped. This document stays as the advisory record.

Advisory for "give options how to add dynamic text effects for the text in the text
frames (e.g. https://reactbits.dev/), advise on more dynamic text effects, and how
to integrate this in the current GUI". Nothing is implemented yet; options are
marked like the other docs (**[recommended]** / alternative) so we can choose and
implement cheaply afterwards.

**Decisions from the review (2026-09-11):**

1. **The hard-coded fade goes.** Text enter/exit transitions must be *rendered*
   (today `renderer.py` fades every label regardless of what the GUI shows) —
   making the existing labels real is a headline requirement, not a bonus.
2. **As many text effects as possible.** The catalogue below is deliberately
   large (~45 effects) and grouped by render engine, so it can grow like the
   191-entry transition catalog did.
3. **Three slots including "While shown"** (loop effects: shimmer, pulse,
   karaoke, wave, …) — the third slot is confirmed, not a maybe.
4. Implementation itself is **deferred**; this document is the reference.

## Rendered proof-of-concept demo (2026-09-11)

`docs/demo/` contains a **real rendered demo** — not CSS, the exact engine the final
result would use (`ass` filter / libass, already compiled into the custom FFmpeg
build):

* `text-effects-demo.mp4` — 27 s, 9 effects, one per 3 s segment, each labelled with
  the ASS tags that build it: Pop (enter, `\fscx/\fscy` overshoot), Bounce (enter,
  per-frame `\pos` damped bounce), Glow (while shown, coloured `\3c` border breathing
  `\blur`), Typewriter (enter, karaoke `\k`), Blur-in (enter, `\blur`+`\alpha` via
  `\t`), Pulse (while shown, chained `\t` scale loop), Split stagger (enter, one event
  per character with `\move`), Shake (while shown, decaying per-frame `\pos` jitter),
  Pop-out (exit, delayed `\t` scale + `\alpha` out).
* `text-effects-poster.png` — 3×3 contact sheet of all nine.
* `make_text_effects_demo.py` — the generator (runs with any libass-enabled ffmpeg,
  e.g. the container build or Debian's `ffmpeg`; recreates the .ass and the MP4).
* `text-effects-demo.ass` — the generated subtitle file, handy as an implementation
  reference for the future `backend/app/text_effects.py`.

This closes the "is it really renderable?" question empirically: everything the GUI
would promise, libass burns into the MP4 with the fonts already in the image.

## The one hard constraint: the MP4 must match the GUI

React Bits components (SplitText, BlurText, ShinyText, DecryptedText, …) are
React/Canvas/WebGL animations. They only run in a browser — the slideshow is
rendered by FFmpeg on the NAS. Using them as-is would give a beautiful editor
preview and a completely different MP4. So every effect we adopt must exist twice,
which is exactly the architecture the app already uses everywhere else:

- Transitions: CSS approximation in the editor (`ColourChangePreview`,
  `quickTransitionClass`) vs the real xfade in `renderer.py`.
- Picture looks: CSS in `src/pictureFilters.ts` vs FFmpeg in
  `backend/app/picture_filters.py`.
- Cut & crop: canvas/CSS in `src/pictureCrop.ts` vs FFmpeg in
  `backend/app/picture_crop.py`.

Dynamic text effects follow the same split: **effect = data on the MediaItem**
(a label + parameters, saved with the project), **GUI shows a CSS/JS approximation**,
**renderer produces the real thing**.

## What the render pipeline can already do (verified)

- Text today is **one `drawtext`** per clip (`_text_filter` in `renderer.py`):
  fixed position, shadow, and a hardcoded **alpha fade** in/out. Notably the
  enter/exit *labels* the GUI shows (Wipe left, Circle open, … from the full
  transition catalog) are **not rendered** — every label fades. Any text-effect
  work should close that gap at the same time.
- FFmpeg 8 `drawtext` animates **`alpha`, `x`, `y` per frame** (expressions with
  `t`), and **`fontcolor_expr`** gives per-frame colour. `fontsize` is *not* an
  expression in FFmpeg 8, so true text zoom/pop needs the libass route below.
- **libass is already compiled into the custom FFmpeg build** (`--enable-libass`,
  `CONFIG_LIBASS=yes` is a hard build check, `libass9` ships in the runtime
  image) — zero Dockerfile changes needed. The `ass` filter accepts a `fontsdir`
  option, and the image already carries the same TTFs the GUI uses, so
  typography parity is preserved. libass (the engine behind .ASS subtitles)
  is the industry-standard way to do animated text in FFmpeg: `\fad`, `\move`,
  `\t(...)` transforms (scale/rotation/colour/blur), karaoke `\k` tags,
  per-character overrides, even vector shapes — everything React Bits does
  stylistically, but renderable.
- Title frames are rendered as isolated, time-base-reset segments with the text
  drawn per clip — a per-clip .ass file with clip-relative times drops straight
  into the existing filter chain.

## Effect catalogue proposal (~45 effects, three slots)

Modelled like the transition catalog: a shared `registry/text-effects.json`
(id, label, group, engine, params, symbol) read by both sides — the same
drift-proof pattern `registry/transitions.json` uses today.

**Slot 1 — Enter** (renders the existing `textEnter` label + `textEnterDuration`)
**Slot 2 — While shown** (new: `textFxWhile` + speed, loops for the hold)
**Slot 3 — Exit** (renders the existing `textExit` label + `textExitDuration`)

Engines: **[dt]** = drawtext expressions (alpha/x/y/fontcolor per frame) — the
same filter the renderer already uses, just animated; **[ass]** = per-clip .ass
file rendered by libass (already compiled into the custom FFmpeg build).

### Enter (≈ 22)

| Effect | Group | React Bits / classic source | FFmpeg render | Fidelity |
|---|---|---|---|---|
| Fade | Fades | — (today) | [dt] alpha (existing) | exact |
| Flicker in | Fades | film leader | [dt] stepped alpha | exact |
| Blur-in | Fades | **BlurText** | [ass] `\blur`→0 | exact |
| Slide from left/right/up/down | Slides (4) | SplitText direction | [dt] `x`/`y` + alpha | exact |
| Rise + settle | Slides | GSAP classics | [dt] `y` overshoot curve + alpha | exact |
| Drop + bounce | Slides | physics entrances | [dt] bounce `y` curve | exact |
| Slide + overshoot (back ease) | Slides | elastic entrances | [dt] back-ease `x`/`y` | exact |
| Split fade per char | Split | **SplitText** | [ass] per-char `\t` stagger | exact |
| Split fade per word | Split | **SplitText** | [ass] per-word stagger | exact |
| Split slide-up per char | Split | SplitText | [ass] per-char `\move`+`\t` | exact |
| Split from centre | Split | kinetic typography | [ass] per-char `\move` outward | exact |
| Converge from edges | Split | kinetic typography | [ass] per-char `\move` inward | exact |
| Typewriter | Typed | typed.js | [ass] per-frame substrings | exact |
| Typewriter + caret | Typed | typed.js | [ass] per-frame + `\p1` caret | exact |
| Word-by-word type | Typed | terminal style | [ass] per-frame substrings | exact |
| Decrypted / scramble | Typed | **DecryptedText** | [ass] per-frame random glyphs | exact |
| Pop in (zoom, elastic) | Zoom | elastic entrances | [ass] `\t(\fscx\fscy)` accel | near-exact |
| Zoom down (big → fit) | Zoom | film titles | [ass] `\t(\fscx\fscy)` | near-exact |
| Flip in (vertical unfold) | Zoom | FoldText-ish | [ass] `\t(\fscy 0→100)` | near-exact |
| Rotate in | Zoom | poster swing | [ass] `\t(\frz −90→0)` + alpha | near-exact |
| Wipe reveal (8 directions) | Reveal | matches xfade names | [ass] animated `clip()` | exact |
| Lower-third bar + text | Lower third | news style | [ass] `\p1` shape + `\move` | exact |

### While shown — loops for the hold (≈ 13)

| Effect | Group | React Bits / classic source | FFmpeg render | Fidelity |
|---|---|---|---|---|
| None (static) | — | — | — | exact |
| Gentle float | Motion | ambient loops | [dt] `y` sine | exact |
| Horizontal drift / marquee | Motion | tickers | [dt] `x` linear | exact |
| Slow zoom on text | Motion | Ken Burns for titles | [ass] long `\t(\fscx\fscy)` | near-exact |
| Pulse (heartbeat scale) | Motion | motion posters | [ass] `\t` scale loop | near-exact |
| Wave (per-char bob) | Motion | kinetic typography | [ass] per-char `\fscy` stagger | good |
| Shake / jitter | Motion | GlitchText energy | [ass] per-frame small `\pos` | good |
| Karaoke highlight | Highlight | AMV/ASS staple | [ass] `\k` tags | exact |
| Shimmer sweep | Light | **ShinyText** | [ass] per-char brightness stagger | approximate |
| Neon glow breathing | Light | GlowText | [ass] `\bord`+`\blur` loop | good |
| Colour cycle | Light | **GradientText** | [dt] `fontcolor_expr` sine | approximate |
| Count up / down (numeric) | Numbers | **CountUp** | [ass] per-frame events | exact |
| Glitch flicker (RGB split) | Light | **GlitchText** | [ass] 3 layered offset copies | approximate |

### Exit (≈ 14)

| Effect | Group | FFmpeg render | Fidelity |
|---|---|---|---|
| Fade out (today) | Fades | [dt] alpha (existing) | exact |
| Blur-out | Fades | [ass] `\blur` 0→8 | exact |
| Slide out left/right/up/down | Slides (4) | [dt] `x`/`y` + alpha | exact |
| Sink + fade | Slides | [dt] `y` ease-in + alpha | exact |
| Split out per char/word | Split | [ass] reverse stagger | exact |
| Typewriter delete (backspace) | Typed | [ass] per-frame substrings | exact |
| Scramble out | Typed | [ass] per-frame glyphs | exact |
| Pop out / collapse | Zoom | [ass] `\t(\fscx\fscy →0)` | near-exact |
| Rotate out | Zoom | [ass] `\t(\frz)` + alpha | near-exact |
| Wipe out (8 directions) | Reveal | [ass] animated `clip()` | exact |
| Lower-third retract | Lower third | [ass] `\p1` + `\move` | exact |

That is **≈ 49 entries**; every one renders in the container as-is. Point/scroll
reactbits components (TextPressure, VariableProximity, FuzzyText-hover,
ScrollReveal/Velocity, CurvedLoop-drag) have no time-based meaning in a rendered
video and stay out; gradient *fill* text is not reachable in FFmpeg (solid colours
only) — Shimmer/Colour-cycle are the honest approximations. Falling letters
(**FallingText**) is a good later addition: pre-computed gravity arcs become
per-char `\move` events.

## Implementation strategies

- **Option 1 [agreed direction, IMPLEMENTED] — dual-engine (drawtext expressions + libass).**
  A new `backend/app/text_effects.py` builds either extended drawtext expressions
  (slide/rise/flicker/colour) or a per-clip `.ass` file (typewriter/split/blur/pop/
  karaoke/scramble/count-up/wipes/lower-third/…) applied with
  `ass=...:fontsdir=/app/fonts` after the frame fit; `_text_filter` becomes an
  effect dispatcher. The first slice — the **non-negotiable fix** — makes
  `textEnter`/`textExit` labels render for real instead of the current hard-coded
  fade. GUI previews are small CSS/JS animations on the existing frame canvas
  (same approach as `ColourChangePreview`). Medium effort; no infra changes;
  every effect lands exact or near-exact.
- **Option 2 — drawtext-expressions only.** Smallest change (alpha/x/y/fontcolor
  maths), and it fixes the hard-coded fade, but it cannot do typewriter/split/
  blur/pop/karaoke — the React Bits showpieces are all missing. Only worth it as
  the first slice of Option 1.
- **Option 3 — real React Bits in the GUI only.** Embed the actual components for
  preview; render falls back to the old fade. Explicitly **not recommended**:
  breaks the WYSIWYG promise the app has kept everywhere else (what the preview
  shows is what the MP4 contains).
- **Option 4 — headless-browser/Python frame baking.** Frame-exact for anything,
  but heavy on a DS918+ and a big new dependency chain. Keep as last resort.

## GUI integration

- **TextFrameEditor sidebar — new "Text animation" section [recommended home].**
  Three compact rows: *Enter / While shown / Exit*, each a chip-picker from the
  curated text-effect catalog (with glyphs, like `transitionSymbol`), a duration
  stepper, and per-effect parameters (direction, per-char delay, target for
  count-up). The preview canvas loops the animation live (paused while the
  colour-change preview is paused — same toggle).
- **Storyline & detailed list** keep the existing enter/exit chips and popover —
  the recommended picker source is the **curated text-effect catalog** (large, but
  every entry means something for text), replacing the current full transition
  list whose 191 xfade/GL labels mostly make no sense on text; symbols included.
  Alternative: keep the full transition list and append the text effects to it.
  Labels from older saved projects that the catalog doesn't know degrade to Fade.
- **Effect gallery** (same UX as the transition gallery): a popup grid where each
  effect shows a **real rendered example clip**, generated once by the backend and
  cached — the exact pattern of `transition_previews.py` ("x/y cached"). This is
  the strongest WYSIWYG selling point and reuses existing infra.
- **Randomize & bulk**: the existing "Random text transition" button and the bulk
  bar gain "random/assign text effects" over the new catalog (enter+exit random,
  while-shown optional).
- **Default text style popup**: gains default enter/while/exit effects for new
  frames, saved with the project like the other text defaults.
- **Story preview popup**: the title view added for frame editing plays the same
  CSS approximation, so effects are visible without rendering.
- **Scope**: title frames first; the exact same machinery applies later to
  captions on pictures (both go through `_text_filter`).

## Suggested phasing

1. **Fix the hard-coded fade first** (the agreed must-have): the renderer
   honours `textEnter`/`textExit` labels — slides/rises/flicker via drawtext
   expressions, the wipe-reveal family via the first small .ass subset.
2. Catalog + data model + "Text animation" section in the editor (Enter /
   While shown / Exit) + looping CSS previews on the frame canvas.
3. libass engine full set: typewriter, split stagger, blur-in, pop/flip/rotate,
   karaoke, count-up, decrypted, shake, lower-third, glow.
4. Effect gallery with cached rendered examples + randomize/bulk + defaults in
   the Default text style popup.
5. (Later) apply the same effects to picture captions.
