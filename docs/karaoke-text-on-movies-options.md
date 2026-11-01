# Karaoke text on a movie — options for the data model and the timing GUI

Date: 2026-10-25 · Status: **decided and built** — what shipped is in [docs/karaoke-timing-editor.md](karaoke-timing-editor.md); the options and the research behind them stay here

Question: *"add new functionality to add karaoke text to a movie. The text is long
text where the sentences need to be timed — which sentence is visible when. If a
sentence is visible, the colouring of the words also needs to be timed. Do a
thorough internet deep search on what kind of interface/GUI works best, so the
user can set up the timing quickly (most flexibility, least clicking)."*

## Short answer

Two numbers describe the work: a 3-minute movie with ~40 sentences has
**~80 sentence edges** and **~600 word boundaries** to place. No click-based UI
survives that. The tools that people actually use for this get it down to one
gesture per *edge* plus an automatic first pass, and then keyboard nudges.

Recommended package (**P1** below + tap pass + onset snapping + ASS/LRC/SRT
import): sentences as blocks on the movie's own strip, **play-and-tap** to lay
the whole skeleton in one listening pass, words auto-filled and editable inside
the selected sentence, everything reachable by keyboard, and an automatic first
guess you correct instead of a blank page you fill in.

## What the good timing editors do (research)

Read from Aegisub's manual and Karaoke Mugen's karaoke guide, Subtitle Edit's
mouse/keyboard reference, SyncFlow's docs, and the sync UIs of EasyKara,
LyricTime, Motion Text and Kapwing/Descript:

| Primitive | Where | Why it is fast |
| --- | --- | --- |
| **Block strip, drag the edges** | Subtitle Edit, Kapwing, VEED, Descript | one drag = one edge; the block *is* the sentence |
| **Modifier-click on the strip sets start / end** (Shift = start, Ctrl = end, Ctrl+Shift = start **and ripple everything after**) | Subtitle Edit | no selection, no tool switching; ripple kills the "fix 39 following lines" problem |
| **Commit + auto-advance** ("Medusa timing", "go to next line on commit") | Aegisub | the cursor moves to the next thing to time after every edit |
| **Tap along** — play, press Space on each word/line, times are stamped live | EasyKara ("hit Space in time with each word"), Aegisub's *Song timer* macro (tap → START, tap → END, next line) | ~1 tap per word, no aiming at all; accuracy of a human ear, not a grid |
| **Karaoke assistant pre-cut** — the line is split into syllables, you only drag the dashed dividers | Aegisub | you correct a guess instead of typing 60 numbers |
| **Nudge by ±100 ms / ±1 frame** and **A–B loop of the current cue** | Subtitle Edit, SyncFlow, MPC | the last 5 % of accuracy without leaving the keyboard |
| **Slow playback (50–300 %)** | Subtitle Edit | fast verses become timeable |
| **Two-pass rule: time the lines first, the words second** | Aegisub manual ("do it in several passes") | never fight two granularities at once |
| **Auto first pass from audio, then correct**: forced alignment / Whisper word timestamps; or a dumb but good default: distribute by character count | Subtitle Edit (WhisperX/MMS aligner), WhisperX ("sub-100 ms, enough for karaoke-style highlighting"), the n8n karaoke workflow (weights words by length) | the manual part drops to fixing the outliers |
| **Import existing timings**: `.srt`/`.vtt`/`.lrc`, ASS with `\k` | LyricTime, EasyKara, most lyric tools | a song's timing already exists somewhere |
| **Lead-in and readability rules** — a line should appear ~1 s before it is sung; use `\kf` (sweep) for syllables longer than ~1.2 s | Karaoke Mugen guide, Aegisub | text that lands *with* the vocal reads late |

The universal shape: **one horizontal strip, one block per line, edges drag,
keys nudge, taps fill, auto-guess first.**

## What this app already has (verified in the code)

This is unusually good news — the *rendering* of karaoke is basically already in
the project:

- `registry/text-motion.json` has **`karaoke-sweep`** ("Not yet sung" colour
  param, `colourWipe: {from, to, dir: ltr}`, unit `char`, source note
  `App v1 · ASS \kf`) and a **`karaoke-line`** preset (fade in → fill letter by
  letter → fade out).
- The colour wipe is implemented on **both** sides of the twin:
  `src/textMotionCore.ts:938` (preview) and `backend/app/text_motion.py:1311`
  (libass render). `backend/app/text_effects.py:2067 _karaoke_sweep_body` even
  emits real ASS karaoke tags — `\kf<n>` per character with `\2c` as the
  highlight colour, with the step derived as `(end-start)*100/chars`, i.e.
  **uniform**.
- Per-item caption window already exists: `textStart` / `textEnd` on the
  storyline item (`src/mediaItem.ts`), clip-local seconds.
- `TextFxLayer` already carries `unit` (char/word/line/text), `duration`,
  `delay`, `stagger`, `order` (forward/reverse/centre/edges/random),
  `range: [from, to]` (**effects can already target a word range**) — so the
  "which words, in what order, how long" plumbing exists; what is missing is
  *non-uniform* per-unit times.
- `MovieEditor.tsx` already builds exactly the strip widget this needs: filmstrip
  frames, ruler, playhead, `trim-handle in/out` drag targets, `cut-shade`,
  transport with jump-to-IN/OUT and a `position - start` clock.
- `/api/media/beats` accepts `videos` and `photos` roots as well as `music`
  (`backend/app/main.py:566`, `renderer.detect_beats`) — onsets of the movie's
  own audio are already available and already cached.
- Project-save/persistence, the "preview = render" twin culture, and the
  libass-absent fallback (effects degrade to plain fades) all exist.

**The gap is therefore only three things:** (1) *sentences* — many windows
instead of one; (2) *uneven word times* inside a window; (3) a fast way to
enter both.

## Data model (proposed, one option)

On the media item, next to `textStart`/`textEnd`:

```ts
karaoke?: {
  text: string                 // the long text; sentences are derived, never typed separately
  style?: 'sweep' | 'word' | 'reveal' | 'pop'   // see "Render styles"
  fill?: string                // "already sung" colour (defaults to the caption highlight colour)
  leadIn?: number              // seconds a sentence shows before its first word (default 0.35)
  sentences: {
    from: number               // word index the sentence starts at (anchor, not a char offset)
    start: number              // clip-local seconds, 0 = the clip's IN point
    end: number
    times?: number[]           // optional per-word starts; absent = distribute by weight
  }[]
  seed?: number                // only for "distribute" jitter, so a save re-renders identically
}
```

Why anchored on **word indices**: the text stays editable. Deleting a word or
retyping a sentence keeps the timing (the anchor is a word count, not a byte
offset); inserting a word inside a sentence makes it take its share of the
sentence window; `Re-time…` re-runs the auto pass when you replace the whole
text. Sentences are **derived from `text` on demand** (split on `. ! ? …` plus
explicit newlines, capped at N words per block, with a `Split at playhead` /
`Merge` pair to override the guess), so a project that only has a long `text`
needs no migration and re-derives cleanly whenever you edit the words.

Two things to know before planning that derivation:

- the vocabulary already exists — the text-motion UI calls the `line` unit
  **"Sentence"** (`src/TextMotionPathEditor.tsx:957`: *Whole caption / Sentence /
  Word / Letter*) and the renderer accepts `unit in ("sentence", "line")`
  (`backend/app/text_motion.py:2242`), so a karaoke layer is *sentences* on the
  `line` unit with word-level colour on the `word` unit;
- there is **no caption-file parser in this repo** (nothing parses `.srt`,
  `.vtt`, `.lrc` or ASS in `src/` or `backend/app/` — verified by search), so
  importing timings is a small new module, not a reuse; the ASS *writer* side
  already exists (`backend/app/text_motion.py` builds the `.ass`).

Everything else stays as today: the caption's font, size, colour, outline,
`textX/textY`, `textFx` stack, and the movie's own audio all keep working, and
the karaoke window is *inside* `textStart…textEnd` if that is narrower than the
clip (so the "text on this picture/movie" switch still wins).

## GUI options

Effort is in UI, not in the renderer. Pick one; the keyboard layer and the auto
pass are shared by all.

**A. Sentence strip on the movie's timeline (recommended spine).**
The kept part of the movie becomes a lane of blocks, one per sentence, drawn on
the existing filmstrip strip.

- drag a block's edge → that sentence's start/end; the neighbour follows
  (no gaps, no overlaps — the Aegisub "make times continuous" rule);
- drag a block's body → move it, duration kept;
- `Shift`-click on the strip = set start, `Ctrl`-click = set end,
  `Ctrl+Shift`-click = set start and ripple all later blocks;
- click a block → it expands below the strip into **word chips**, drag a
  divider between two chips to move that word boundary; `Alt`-drag the last
  divider = fine-tune the end;
- `Enter` commits and jumps to the next block (auto-advance), `Space` plays the
  sentence ± its lead-in (an A–B micro-loop, "play 1 s and back" style),
  `←/→` = ±100 ms, `Shift+←/→` = ±1 frame, `[`/`]` = the whole rest ±100 ms,
  `S` = split the block at the playhead, `M` = merge with the previous,
  `Delete` = drop the block (the words fold into the neighbour),
  `Tab` = next/previous word inside the open sentence.
- Cost: medium (one new component, reuses the strip's geometry and CSS).
- Best flexibility/least clicking balance, and it teaches nothing new: it is
  the same strip the trim editor already uses.

**B. Tap-along pass (recommended *on top of* A, not instead of it).**
A "Time it" button: playback starts, the text scrolls, and **Space stamps the
next word** (or the next sentence in `Coarse` mode — that is the default for the
first pass). Rhythm in, timings out; a bad tap is fixed in A. Add `Backspace` =
undo last tap, `2`/`½` = half/double speed. This is the single biggest click
saver in every tool researched, and the only one that needs no aiming.
- Cost: small, once the strip and the block model exist.

**C. Word-chip transcript (Descript/Kapwing flavour) as the whole editor.**
The long text is laid out as chips with the sentence breaks as gaps; you drag
the gaps along a mini ruler, or select words and press `=` to make a sentence.
- Cheapest to understand for typing; **worst for aiming** (a chip is not a
  time). Good as A's "expanded sentence" row, weak as a spine.

**D. Full waveform/spectrogram editor (Subtitle Edit / Aegisub grade).**
A real waveform of the movie audio under the strip, zoom, keyframe ticks.
- Most precise for speech, most work (a peak-extraction endpoint + a canvas
  component), and the spectrogram only pays off for music with vocals — where
  B (tap) and onset snapping do 90 % of it anyway. Proposed as a later "P3".

**Auto first pass (any option; this is where the clicking really disappears):**

1. **Distribute** — sentences spread over the kept length, weighted by
   character count (the standard cheap guess), then a global
   `Fit to window` / `Space evenly` / `Compress gaps` pair of buttons.
2. **Snap to onsets** — the existing `detect_beats` times, mapped to clip-local
   seconds; each sentence edge and each word boundary jumps to the nearest
   onset within ±0.25 s (an adjustable magnet, `snap` off per drag with `Alt`).
   This is the app's beat-sync machinery reused, no new backend.
3. **Import** — `.lrc`/`.srt`/`.vtt`, and `.ass` with `\k`/`\kf` parsed into
   word times. Importing an existing fansub karaoke file would then reproduce it
   almost exactly, because the render side writes the same tags.
4. **Transcribe + forced alignment** (WhisperX/stable-ts class) — the only true
   "zero clicks", but it needs models + a Python dependency in the container and
   is heavy on a DS918+. Proposed as an explicit **no** for now, with the door
   left open: the importer takes a JSON of `{word,start,end}` so a future
   sidecar (or an outside tool) can feed it.

## Render styles (from the ASS capability set, all four are cheap)

`ffmpeg-word-captions` documents exactly the four looks users ask for, and all
four are ordinary ASS:

| Style | What it looks like | ASS |
| --- | --- | --- |
| **sweep** (default, true karaoke) | the sentence is up; each word *fills* left→right as it is sung | one event per sentence, `\kf` per word, `\2c` = fill colour |
| **word** | only the current word is in the highlight colour (TikTok/Reels look), optionally 110 % | `\kf` with a zero-width sweep, or one event per word with `\1c` + `\fscx` |
| **reveal** | the sentence builds up word by word, unsaid words invisible | `\ko`-style per-word alpha |
| **pop** | one word at a time, big, centre | one event per word with `\t` 70→112→100 % |

Notes that come from the research and apply here: use `WrapStyle: 2` so libass
cannot re-wrap around a scaled word (otherwise the line jumps at every word);
prefer a sweep for words longer than ~1.2 s (an instant flip looks broken);
leave a lead-in (~0.35–1 s) so text is readable *before* it is sung; and a long
gap between sentences should keep the previous sentence on screen or fade it,
not blink it.

Two app-specific ones: on builds **without libass** the karaoke degrades to
per-sentence show/hide (same policy the text effects already use — stated, not
silent), and the movie's kept cut means times are stored from the **IN point**,
so re-trimming never shifts the karaoke.

## Where it lives, and how it persists

- **On the item, not in a parallel store.** The karaoke block sits next to
  `text`/`textStart`/`textEnd` on the storyline item, so it follows the slide
  when the slide is duplicated, moved, or re-ordered, and it goes through the
  same clamp the caption window already uses (`normalizedTextTiming` /
  `normalizeItemTextTiming`, `src/App.tsx:958` and `:971`) so sentence windows
  can never fall outside the slide after a duration change.
- **Persistence is free, verified on both ends:** the backend stores the whole
  project as the canonical lossless `payload_json` envelope
  (`docs/sqlite-persistence.md`), and loading mutates `saved.media` **in place**
  (`src/App.tsx:1667`, `let m: any = m0`) and returns the same object — so a new
  item key like `karaoke` survives save → `POST/PUT /api/projects` (`main.py:172`
  / `:178`) → load with no migration code, and the existing
  `backend/tests/test_output_delivery.py`-style round-trip tests can pin it. (Collages are the opposite: they are rebuilt through the
  `normalizeCollage` allow-list, so any new collage key has to be listed there —
  see `docs/collage-photo-animation-options.md`.)
- **One on/off switch, no second concept:** the caption's own enable flag, which
  the renderer already honours (`backend/app/text_effects.py:2219
  text_enabled = item.get("textEnabled") is not False`; the editor's *Add text*
  chip at `src/App.tsx:2222`, collages at `:2326`). Karaoke text is that same
  text, styled and timed — switching the caption off hides the karaoke too.
- **Editor window:** the karaoke strip lives in a popup layer (`.popup-panel`,
  `z-index: 60`) when opened from a slide, or inside the Movie editor; the
  popup's existing background-blur + scale-in pattern applies. Only the Movie
  editor version has the playing movie (and therefore its audio) next to the
  strip — that is what decision 3 is about.

## Preview (browser side)

The browser preview already animates `colourWipe` per unit
(`src/textMotionCore.ts`); karaoke needs the unit clock to accept an explicit
time list instead of `delay + rank * stagger`. That is a small, contained
change in the same file the twin test already pins — so the popup can keep
showing the real thing while you time it, which is the whole point of the strip.

## Test plan (matching how this repo works)

- extend the **existing text-motion twin** (`backend/tests/test_text_motion_twin.py`
  + `backend/tests/text_motion_twin.mjs`, whose header says it outright: *"The
  live preview and the render must agree"*) — sentence +
  word-time cases, Node runs `textMotionCore.ts`, both must agree on every word's
  colour state at sampled times, and the emitted ASS `\kf` durations must equal
  the windows (centiseconds, summing to the sentence length).
- ASS golden tests: `\kf` sum, `WrapStyle: 2`, lead-in, the no-libass fallback,
  `\N` handling, and empty/short sentences.
- persistence: save → load keeps `karaoke` byte-identical; unknown-key retention
  still works; a project without `karaoke` renders exactly as before.
- import: `.lrc`/`.srt`/`.ass` fixtures, including a karaoke `\k` file.

## Decisions I need from you

1. **Timing surface**: A (strip + keys), A+B (also tap-along), A+C (chips as the
   spine), or D (add a real waveform)?
2. **Auto first pass**: distribute only, + onset snapping, + `.lrc/.srt/.ass`
   import, or attempt Whisper alignment in-container (heavy new dependency)?
3. **Where it lives**: a third tab in the Movie editor (next to the trim strip),
   its own *Karaoke text* popup opened from the slide's text row, or the tab for
   timing plus the popup for text and style?
4. **Scope**: movies only, or the same editor for a photo slide (and a text
   frame) — the engine cost is the same, the UI cost is one more entry point.
5. Default **style**: `sweep` (classic karaoke) or `word` (highlight only)?
