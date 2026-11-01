# Karaoke text on a movie — the timing editor

Status: **built**. This is the shipped behaviour and the code behind it; the
options it was chosen from (and the research they came out of) are in
[docs/karaoke-text-on-movies-options.md](karaoke-text-on-movies-options.md).

## What it is

A slide's caption can be *timed word by word*: which sentence is on screen when,
and inside a sentence which word is being sung at this instant — coloured from
left to right like a karaoke track. It is not a second block of text. The words
stay in `item.text`, so the caption, its font, its position, the storyboard
thumbnail, the collage caption and the render all keep reading the same field;
`item.karaoke` only adds a clock to the words that are already there.

```ts
karaoke?: {
  editor: 'strip' | 'tap' | 'chips' | 'wave'   // which timing surface this movie uses
  style:  'sweep' | 'word' | 'reveal' | 'pop'  // how a word changes colour
  fill:   '#ffd166'                            // the colour a word turns into
  leadIn: 0.35                                 // seconds the line appears before its first word
  tail:   0.3                                  // seconds it holds after its last one
  snap:   0.25                                 // how far a tap may move to reach an onset
  maxWords: 12                                 // words per line before the text is cut
  text?: { … }                                 // how the sung words look; unset = the caption's style
  words: [{ t?: number, br?: -1 | 0 | 1 }, …]   // one entry per word, in clip-local seconds
}
```

Two rules make the whole thing survivable:

* **One entry per word, addressed by position.** `words[i]` belongs to the *i-th
  whitespace token of `text`*, so a time is attached to a word and not to a byte
  offset. Insert, delete or retype anything and the timing rides along:
  `retarget()` aligns the old token list with the new one (LCS on the word
  strings) and carries each time to the word it was written for. This is the
  single most important difference from a cue list — a `.srt` import has to be
  re-synced after every edit, and this one does not.
* **Times are clip-local seconds**, counted from the movie's own trim IN point,
  exactly like `textStart`/`textEnd`. Re-trim the movie and the words go with the
  picture instead of sliding off it.

Everything else — the sentence windows, which word is current, how far the sweep
has got — is *derived* from those two fields by `planOf()`. Nothing is stored
twice, so nothing can disagree with itself.

## The four surfaces

Every timing tool people actually use for this picks one of four input models, and
they disagree hard about which one is fastest. So all four are there, and the
choice is stored **per movie** inside this popup — never in general settings,
because a two-line quote and a whole song lyric want different ones.

| Surface | What you do | Steals from |
| --- | --- | --- |
| **Sentence strip** | drag blocks and their edges on the clip's own timeline | CapCut / captions UIs |
| **Tap along** | play the movie, press `Space` in time with each word or line | EasyKara, Aegisub's song-timer macro |
| **Word chips** | the text itself is the editor: click a word to start it at the playhead, click the gap after it to break or glue | Descript's transcript |
| **Waveform** | the strip again, over the movie's own audio peaks and onsets, so a consonant you can hear is a line you can put a mouse on | Aegisub's Karaoke Assistant, Subtitle Edit's visual sync |

Keys are shared: `Space` tap, `Enter` next line, `Shift+←/→` ±100 ms,
`Alt+←/→` ±1 frame, `I`/`O` set the selected line's start/end at the playhead,
`S` split, `M` merge, `F` 50/75/100/150 % rate, `L` loop the line, `A` auto
first pass, `B` snap to onsets, `1`–`4` switch surface, `+`/`-` zoom the strip and
`0` fit it back. The mouse map on the
strip is Subtitle Edit's: Shift-click sets the start, Ctrl-click the end, and
dragging a block ripples every later word by the same amount so a line never
lands on top of the next.

Nothing here is a "generate" button pretending to be exact: the first pass
distributes the words by character weight across the caption window, onset
snapping pulls each one onto the nearest attack in the movie's own audio, and
`.lrc` / `.srt` / `.vtt` / `.ass` import takes times from a file you already
have — and the popup shows a worked example of every one of them, in a tab per
format, because a parser that is described to you in one line is a parser you
feed a file you invented. (Per-word *forced alignment* would be better still and
is deliberately not in the container: it needs a model of a few hundred MB, and
the plan above is what it would have to write anyway.)

## The text style: what the sung words look like

Timing and typography are separate lanes — the lesson of every editor in the
research notes (`docs/karaoke-text-on-movies-options.md`): Descript, Kapwing and
Aegisub all keep style out of the timing surface, and this app had to *inherit*
it: karaoke wore the caption's font, size, colour, outline and position and
nothing else, because `KaraokeStage` is handed the caption's style as props.

So the block is an **override**, not a replacement. `karaoke.text` carries nine
optional fields and every one of them means "instead of the caption's value":

```ts
text?: {
  family?: string    // font face
  size?: number      // px on a 1920-wide frame, exactly like the caption's
  bold?: boolean
  italic?: boolean
  underline?: boolean
  colour?: string    // the *unsung* colour — `fill` stays the sung one
  outline?: boolean  // off is what most lyric videos do
  x?: number         // % of the frame
  y?: number
}
```

The popup builds that lane **from the table the engines use**, so a control cannot
exist that nothing honours: `KARAOKE_TEXT_FIELDS` in `src/karaokeCore.ts` lists the
nine, `TEXT_STYLE` in `backend/app/karaoke.py` maps each one to the caption key the
render reads, and a test asserts the two lists are the same names in the same
order. The chips for the four on/off fields have three states — the caption's
value, on, off — because *off* is a look people want (no outline) and inheriting
it back has to stay one click away.

What is deliberately **not** on the list: outline colour, shadow depth, letter
spacing and text alignment. Those are fixed for every text on this path by the one
`Style: FX` line libass reads (`_header` in `backend/app/text_effects.py`:
`Spacing 0`, `Shadow 2`, `Alignment 5` with `\pos`, a constant outline colour), so
offering them for karaoke alone would put a promise in the preview that the MP4
contradicts. If that line ever grows per-item control, the fields belong in the
same two tables and the tests will not let them drift.

Merging is one function per engine, and both are trivial:

* preview — `karaokePaintStyle(caption, karaoke)` in `src/karaokeCore.ts`, applied
  inside `KaraokeStage`, so *every* mount (storyboard frame, preview modal, the
  popup's own video overlay) restyles together. It validates while merging, which
  is what stops a hand-edited project file putting a bad colour into CSS.
* render — `style_overrides(data)` in `backend/app/karaoke.py`, spliced over the
  item in `karaoke_document()` *before* `_geometry()`. The overrides therefore
  travel through the ordinary caption path: no ASS override tags, no second style,
  and an absent or unusable block leaves the document byte for byte what it was.

`size` is the only field spelled differently in the two models (the stage prop is
`fontSize`, like every other caption path) and that single rename lives inside
`karaokePaintStyle` rather than in a caller.

## The strip's zoom

A three-minute song squeezed into a 700 px box is 0.26 seconds per pixel: the
ruler is honest and a divider is un-hittable. So the strip zooms, 100 % (the whole
clip, as it always was) to 800 %, by the slider in the transport row, `+`/`-`, or
⌘/Ctrl+wheel over the strip itself; `0` or *Fit* comes back out, and Alt-click
puts the time you clicked in the middle of the box.

Nothing about the *model* changes, which is the whole design: every lane already
stores its geometry as a percent of the clip, so zooming is drawing that one
mapping wider inside a scroller (`width: zoom × 100 %`). Blocks, handles,
dividers, the playhead and the waveform all follow without a second coordinate
system, and `pointerTime()` keeps inverting the strip's own `getBoundingClientRect()`,
so a drag is as exact at 8× as at 1×. Three things are measured in screen pixels
rather than in time, and only those three had to learn about the zoom — the ruler's
step (a tick every ~55 px, from a 300 s clip down to 0.01 s, which is why one
frame at 25 fps is reachable), the tick's label (written with the step's own
decimals, or a ruler reading `10.3` above the tick at 10.25 would point at a time
that is not there), and the floor under a one-word sentence's block (0.8 % of the
*box*, divided by the zoom, so it stays ~6 px grabbable instead of stretching a
0.2 s line to a quarter of the screen). They are pure functions in
`karaokeCore.ts`, which is what makes them testable at all.

Zooming is deliberately not part of `karaokeData`: it says how wide this person
wants the ruler, and the render reads the data. It rides in `localStorage` under
`slideshow.karaoke.zoom` and never reaches a project file. While the movie plays
the scroller keeps the playhead inside itself, so zoomed-in playback is not a
strip you watch disappear. The one thing the zoom cannot do is sharpen the
waveform: 700 buckets are fetched for the kept cut and stay 700 buckets, so at 8×
the peaks stretch (they do not lie — they just get soft) while the onset marks
remain exact to the millisecond.

## The box the words are painted in

`sweep` colours a word by painting it twice: the word in the look's unsung
`colour`, then the same text again in the `fill`, laid over it and cut on one
vertical line (`clip-path: inset(0 40% 0 0)` — the right edge is the only moving
number, driven by the twin's `u`).

Before that it was a gradient behind the glyphs, clipped to them
(`background-clip:text` with a transparent text fill), which makes the word's own
box the *mask*: anything the font draws outside that box is transparent. The
preview used a constant line box of 1.28 em, and 76 of the 93 styles in the font
registry want a taller line than that — Anton measures 1.73 em, so at a karaoke
size of 120 px roughly 27 px of cap and 27 px of descender fell outside the mask
and vanished. That is the whole "increase the font size and the letters get cut
off top and bottom", and it was preview-only: libass paints the glyphs and the
`\kf` colour, so the MP4 was never losing anything. Measuring the box from the
font file (`winAscent + winDescent` over `upm`, floored at 1.02 em — the same
numbers `app/text_motion.py` uses to place a line) moved the preview *towards* the
render, but it could not have stopped the clipping: a rectangular mask clips
whatever rectangle it is given.

So there is no mask any more, only a reveal, and a horizontal inset cannot touch a
glyph's top or bottom at any size or any face. The two copies are `.kara-word` and
`.kara-word-sung` in `src/styles.css`; the overlay copy keeps 0.04 em of padding
above and below the line box, paid for by an equal negative margin, for the faces
whose drawings overshoot the metrics recorded in their own table — the margin means
the block's position, and so the twin's arithmetic, does not move. It also needs no
`-webkit-` prefix, no `background-clip`, and no `color: transparent`, which is the
combination that silently loses its paint in a browser that decides not to support
it.

## Where the code is

| File | Role |
| --- | --- |
| `src/karaokeCore.ts` | the model, once: tokenise → break → distribute → snap → plan → frame, plus the `KARAOKE_TEXT_FIELDS` table, the style sanitizer and `karaokePaintStyle` |
| `src/KaraokeEditor.tsx` | the popup: four surfaces, the style lane, the text-style lane, the first-pass actions |
| `src/KaraokeStage.tsx` | the layer that paints a frame of it (and merges `karaoke.text` over the caption's style), plus `useKaraokeClockTime` for the preview clock |
| `src/App.tsx` | `CaptionStage` is the one karaoke swap — `FrameMotionPreview`, both text popups' canvases and the collage caption all render through it, so no preview can miss a saved timing; the text popups also adopt `karaoke` from the item while they are open |
| `backend/app/karaoke.py` | the twin of the model, plus the ASS emitter and `TEXT_STYLE` / `style_overrides()` |
| `backend/app/text_effects.py` | `karaoke_document()` (the `.ass`) and `karaoke_overlay_filter()` |
| `backend/app/renderer.py` | `_text_filter()` takes the karaoke branch when an item has timing **and** the FFmpeg build has libass |
| `backend/app/main.py` | `GET /api/media/beats` (onsets) and `GET /api/media/waveform` (peaks) feed the wave surface |

`src/karaokeCore.ts` and `backend/app/karaoke.py` are mirrors, statement for
statement, for one reason: the popup has to be unable to show a timing the MP4
will not do.

## How it renders

One `Dialogue` event per sentence with a `\kf` tag run per **character**, whose
durations come from that word's own window — so a gap you tapped stays a gap in
the file instead of being smoothed back into a linear crawl. `word` style uses
one `\k` per word (an instant flip), `pop` uses one event per word because
per-word scale cannot live inside a `\k` run, and `reveal` keeps `\1c` as the
caption colour while making the *not yet sung* side transparent.

The colour semantics are the part worth writing down, because libass
interpolates **secondary → primary** across a `\k` run:

* `\2c` = the caption's own colour — what a word looks like *before* it is sung;
* `\1c` = `fill` — what it turns into.

That is the opposite of the legacy sweep helper's convention in the same file,
where `&H40C8FF&` sat in `\2c` as the highlight; a karaoke caption inverts it on
purpose, so an untimed word never changes hue.

The document uses `WrapStyle: 2` and `\an*\pos()`, so a line cannot re-wrap when
a word's colour changes underneath it — the same reason the per-word styles write
one event per word instead of scaling inside `\k`.

Two degradations, both deliberate: a caption whose words are all un-timed is
plain text (`has_karaoke()` returns false, and `sanitizeKaraoke()` refuses to
store an empty clock), and on an FFmpeg build without libass the karaoke branch
is not entered at all, so the slide keeps its words, position, style and plain
fades rather than failing the job.

## Rules the editor guarantees

* A line appears `leadIn` before its first word and holds `tail` after its last,
  but two sentences are **never** on screen at once: they share one position, so
  an overlap would print one caption on top of the other. The handoff point is
  the next line's first word — the earlier line may hold until that word and
  never past it. When a user taps two sentences on top of each other, the
  earlier one is cut short instead: a sweep that stops mid-word is the visible,
  honest signal that these two need a gap, and no word's time is ever moved to
  hide the fact.
* A word is sung until the next one starts. No minimum-length floor on that
  window, or two colours would overlap; a word that ends up thinner than 50 ms
  is a timing problem the strip shows as a too-thin block, and the editor's own
  drags keep 60 ms between word starts.
* The `maxWords` cap is a legibility promise: it always cuts, and a comma at the
  end of the run only moves the cut *inside* the cap. (Deferring the cut to "the
  next comma" instead is how a 4-word setting once produced an eight-word
  caption and a one-word orphan.) A handful of words left over at the end folds
  back into the line before it rather than being stranded as its own caption.
* Everything read back from a project goes through `sanitizeKaraoke()`
  (`src/karaokeCore.ts` ↔ `karaoke.py`): unknown styles and editors fall back to
  the default, times outside `[0, 86400)` are dropped, and a data block in which
  nothing is timed is dropped entirely.
* **The panel follows the playhead while the movie plays.** `followSentence()`
  picks the sentence that is showing, or the *next* one during a gap, so
  "Selected sentence 3 of 15" names the line you are hearing and the line you are
  about to hear is already selected — Aegisub's cursor. Paused it stays where it
  was; clicking a block, a keyboard step or a drag **pins** the line
  (`selectLine`) and pressing play picks the cursor back up. The rule is one loop
  in each engine (`followSentence` ↔ `follow_sentence`) and the twin test compares
  it at every sentence and word boundary of the plan, so the strip highlight, the
  side panel and the painted caption cannot disagree.
* **The timing popup is not the only place it shows.** `CaptionStage` in
  `src/App.tsx` owns the karaoke swap once: the storyboard frame, the preview
  modal, the "Edit picture text" and text-frame canvases and the collage caption
  all go through it. The swap used to live in `FrameMotionPreview` alone, which is
  how a saved timing could be visible on the storyboard and absent from the popup
  that made it. The text popups also *adopt* `karaoke` from the item while they
  are open (and the caption text and window, unless they hold an unsaved edit of
  their own), because the karaoke popup writes straight to the item and the popup's
  Save must not write the opening snapshot back over it — that clobber is how
  "save, close, reopen" lost both the text and the timing.

## Tests

* `backend/tests/test_database.py` — the block, the word times and its text style
  survive the save the app actually performs (`payload_json` in, snapshot out),
  and the `.ass` built from the *loaded* item carries them: `Style: FX,Impact,96`,
  `\pos(960,340)` — the caption's x, the block's y — and the words in order. That
  is the "is my karaoke in the final result?" question, asked where it can be
  answered.
* `backend/tests/test_karaoke.py` — the rules above against the Python engine,
  the ASS emission (tag spelling, colour direction, per-word `\kf` sums, brace
  escaping, `WrapStyle: 2`), the style block (clamps, "a boolean is not a size",
  "an empty block is no block", the `Style:` line and `\2c` an override produces,
  and that an unset one changes not a byte), and the joints neither engine can
  see: the renderer branch and its degradation, the persisted field, both entry
  points, the four surfaces, the strip's CSS, the two style tables agreeing, the
  single `CaptionStage` branch every preview shares, the popup's adopt-not-overwrite
  merge, and the panel following the playhead.
* `backend/tests/test_karaoke_strip.py` + `karaoke_strip.mjs` — the strip's zoom as
  the editor draws it: the clamp (1 is "the clip already fits", nothing narrower;
  8 is eight screens; a NaN from a hand-written project file is just "fit"), the
  ruler's step (always a rung of the ladder, never coarser for a longer clip,
  never finer for a bigger one, 40–140 px of room per tick at every span from 1 s
  to an hour, ticks unique and reaching the end, and labels that say the time they
  stand on), the block's floor in box-percent, and — with no Node required, because
  they are the parts a CSS tidy-up could silently undo — that the sung words' box
  is measured from the font rather than assumed, that `.kara-word` still carries its
  padding and its negative margin, that no bundled face is given a box shorter than
  its own line, and that the zoom never leaked into `KaraokeData`.
* `backend/tests/test_karaoke_twin.py` + `karaoke_twin.mjs` — 22 cases run through
  **both** engines: timing shapes (coarse passes, holes, mistimed taps, emoji,
  forced breaks, a window that cuts the text off) plus the style block (nothing
  set, a display face, no outline, moved down the frame, garbage that must be
  dropped, out-of-range numbers that must clamp). Compared value for value:
  sentence windows, word windows, per-instant preview frames, word colours, the
  sanitized style block and the nine effective paint values — the last derived
  from `TEXT_STYLE` rather than repeated as a literal, so an engine that renames
  or drops a field fails here. This is the file that decides whether "what you
  time is what the MP4 does" is a sentence rather than a hope.
