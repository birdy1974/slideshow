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
first pass, `B` snap to onsets, `1`–`4` switch surface. The mouse map on the
strip is Subtitle Edit's: Shift-click sets the start, Ctrl-click the end, and
dragging a block ripples every later word by the same amount so a line never
lands on top of the next.

Nothing here is a "generate" button pretending to be exact: the first pass
distributes the words by character weight across the caption window, onset
snapping pulls each one onto the nearest attack in the movie's own audio, and
`.lrc` / `.srt` / `.vtt` / `.ass` import takes times from a file you already
have. (Per-word *forced alignment* would be better still and is deliberately not
in the container: it needs a model of a few hundred MB, and the plan above is
what it would have to write anyway.)

## Where the code is

| File | Role |
| --- | --- |
| `src/karaokeCore.ts` | the model, once: tokenise → break → distribute → snap → plan → frame |
| `src/KaraokeEditor.tsx` | the popup: four surfaces, the style lane, the first-pass actions |
| `src/KaraokeStage.tsx` | the layer that paints a frame of it, and `useKaraokeClockTime` for the preview clock |
| `src/App.tsx` | `FrameMotionPreview` swaps to `KaraokeStage` for any item that has real timing, so the storyboard frame, the preview modal and the collage caption all inherit it from one place |
| `backend/app/karaoke.py` | the twin of the model, plus the ASS emitter |
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

## Tests

* `backend/tests/test_karaoke.py` — the rules above against the Python engine,
  the ASS emission (tag spelling, colour direction, per-word `\kf` sums, brace
  escaping, `WrapStyle: 2`) and the joints neither engine can see: the renderer
  branch and its degradation, the persisted field, both entry points, the four
  surfaces, the strip's CSS.
* `backend/tests/test_karaoke_twin.py` + `karaoke_twin.mjs` — 17 timing shapes
  (coarse passes, holes, mistimed taps, emoji, forced breaks, a window that cuts
  the text off) run through **both** engines, comparing sentence windows, word
  windows, per-instant preview frames and word colours value for value. This is
  the file that decides whether "what you time is what the MP4 does" is a
  sentence rather than a hope.
