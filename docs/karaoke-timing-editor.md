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
clip, as it always was) to **6000 %**, by the slider in the transport row, `+`/`-`,
or ⌘/Ctrl+wheel over the strip itself; `0` or *Fit* comes back out, and Alt-click
puts the time you clicked in the middle of the box.

The ceiling was 1200 % and that was a bug of the arithmetic, not of the gesture: the
top of the range has to be set by what the *widest* use needs, which is `3 lines` on a
long clip — "did I steal this syllable from the next sentence" is only answerable when
three sentences of a nine-minute movie fit the box, and at 12× a 540-second clip would
still show twenty sentences. `KARAOKE_ZOOM_MAX = 60` is that number rounded up. What
used to stop anyone from asking for more was the ruler's own label budget, a flat
`KARAOKE_MAX_TICKS` per *strip*: at 30 s × 60 it answered with a rung so coarse that
two ticks stood 350 px apart, i.e. the ceiling was useless because the ruler refused to
follow it up. `karaokeLabelBudget(zoom)` now scales the budget per *screen* of strip
(139 × zoom, capped at 6000 labels), which keeps the pixel target the deciding rule and
still bounds the DOM.

Nothing about the *model* changes, which is the whole design: every lane already
stores its geometry as a percent of the clip, so zooming is drawing that one
mapping wider inside a scroller (`width: zoom × 100 %`). Blocks, handles,
dividers, the playhead and the waveform all follow without a second coordinate
system, and `pointerTime()` keeps inverting the strip's own `getBoundingClientRect()`,
so a drag is as exact at 12× as at 1×. Three things are measured in screen pixels
rather than in time, and only those three had to learn about the zoom — the ruler's
step (a tick every ~55 px, from a 300 s clip down to 0.01 s, which is why one
frame at 25 fps is reachable — then capped at 139 labels however deep the zoom goes,
because a label is a DOM node in a strip that is already twelve screens wide), the
tick's label (written with the step's own
decimals, or a ruler reading `10.3` above the tick at 10.25 would point at a time
that is not there), and the floor under a one-word sentence's block (0.8 % of the
*box*, divided by the zoom, so it stays ~6 px grabbable instead of stretching a
0.2 s line to a quarter of the screen). They are pure functions in
`karaokeCore.ts`, which is what makes them testable at all.

Zooming is deliberately not part of `karaokeData`: it says how wide this person
wants the ruler, and the render reads the data. It rides in `localStorage` under
`slideshow.karaoke.zoom` and never reaches a project file. While the movie runs the
strip scrolls and the playhead does not: each frame drives the scroller so that the
line stands under the *centre* of the box (`karaokeFollowScroll`), which is how a
transport is read — a head that stays put while the film slips under it — instead of
the line flicking from one edge of the box to the other, which is what the earlier
keep-it-in-view rule did. At the top and bottom of the clip the clamp lets the line
walk to the edge, the only honest answer when there is no film left to slide, and
below fit-zoom there is nothing to scroll at all so it rides its own percentage as it
always did. A *scrub* deliberately does not re-centre: the pointer is driving the
position there, and sliding the view under a pointer that is sliding through it is a
feedback loop, so that case keeps its own held-offset rule. Alt-click uses the same
arithmetic, which is what `KaraokeFollowScrollTest` measures. The one thing the zoom
cannot do is sharpen the
waveform: `WAVE_BUCKETS` buckets are fetched once for the kept cut and stay that
many, so at 12× the peaks stretch (they do not lie — they just get soft) while the
onset marks remain exact to the millisecond.

## The clip the strip runs on

A movie can be cut to an IN/OUT pair, and every time in `karaokeData` is a second
*inside the kept region* — never a second of the file. That is what lets a re-cut
leave the words where they are, and it means the popup has to agree with the render
about where its zero is and where its picture ends.

It did not, in two ways. The strip's whole width came from `item.duration`, which for
a cut movie is the *rounded* kept length and for a hand-edited hold is not the clip at
all; and `trimEnd` appeared nowhere in `KaraokeEditor`, so playback ran past the OUT
point — the strip's last seconds showed a movie that is not in the render, which is
what "it still shows the complete movie" was. The movie editor's own karaoke popup
passed an exact `kept`; opened from the timeline, nothing did.

Now the window is computed once, in `karaokeCore.ts`:

```ts
karaokeClipWindow(item, kept) → { start, span, cutAt }
karaokeClipClock(videoTime, win) → { position, ended }
```

Three rules, deliberately the renderer's own:

* `start` is the IN point. The `<video>` is seeked to `start + position`, the waveform
  is fetched for `start … span`, and the beats the magnet offers are shifted by
  `start` and then thrown away if they fall outside the strip.
* `span` is the kept region: `trimEnd - trimStart`, or — when only an IN point was
  ever written, which means "to the end of the file" — the item's length minus it.
  `duration` is *not* the bound, because for a movie it is only a floor: a short hold
  does not shorten the clip, and the render proves it (`hold = max(hold, native)`).
* `cutAt` is `null` unless the item was actually cut. An untrimmed movie has no OUT
  point, so the popup must not stop it; that case behaves exactly as it always did.

`ended` pauses the element and parks the playhead at the end of the strip; pressing
play there means *again*, from the IN point, rather than resuming on a frame that is
not in the movie. Reaching the end of the *strip* is a different thing and only clamps
the playhead — which is why the position clamp and the pause are two rules in one pure
function rather than one condition.

The final result was checked at the level where a mismatch would hurt: the strip's
numbers and the renderer's are the same numbers. `video_trim_window(item, probed)` is
the authority on the segment (`{start, end, kept}`), the segment is cut with `-ss`
before `-i` so its output PTS start at zero, karaoke times are laid out from zero
(`karaoke_document` receives `duration = hold + lead_in`, `textStart/End` shifted by
the same `lead_in`), and `test_karaoke_strip.py`'s `KaraokeClipWindowTest` compares
the TypeScript window against `video_trim_window` for every case it can, so a future
change to one side has to meet the other.


## The box the words are painted in

Two things cut the tops and bottoms off the sung words in the preview, and they
had the same shape: **a rectangle was smaller than the letters, and something was
clipping to it.** Neither touched the MP4, because libass paints a line and its
`\kf` colour with no box around them.

*The borrowed box.* `KaraokeStage` laid the line out in an element with class
`kara-block` — which is also what the timing strip under the stage calls a row,
and the strip's rule came later in the same stylesheet at the same specificity:
`height: 34px`, `display: flex`, `overflow: hidden`, a background, a border, 9 px
of padding and `white-space: nowrap` on the words inside it. The lyric line was
therefore painted on a green plate that never grew with the font, its words
ellipsised instead of wrapped, its ascenders and descenders outside 34 px. "The
background of the karaoke text must fit the height of the text size" was this: not
a background to size, but a background that should not exist. The stage now has a
class of its own (`.kara-lyric`: positioning, the fade, nothing else), and
`StageBoxCollisionTest` keeps the two apart — it asserts the stage never uses the
strip's class, that exactly one rule owns `.kara-block`, and that the lyric rule
carries no height, background, border or clip.

*The space between the words.* Losing that row also lost the one thing that had been
spacing the words for free: its `gap`. `tokenize` matches `\S+`, so a token carries
its letters and never the separator behind them, and the stage was painting
`<span>word</span>` runs with nothing between them — "all the spaces are gone",
reported as a font bug. The gap is now a real space written *between* the spans, not
a `margin-right` on them: `app/karaoke.py` puts a literal `" "` between the `\kf`
runs, so only a space the face itself measures matches the render, scales with the
font, and — the part a margin or a `gap` can never do — is a line-break opportunity.
None in front of the first word of a line, because the block is centred on its own
text and a leading space would slide the line sideways; and after a token flagged
`nl` a `\n`, so a break the render writes as `\N` is visible here too.

*The mask, and the clip that replaced it.* The sweep used to colour a word by
painting a gradient and clipping it to the glyphs (`background-clip: text` with a
transparent fill), which makes the word's box the mask. It is now the same word
painted twice — the unsung colour underneath, the sung one laid over it — with
the top copy revealed by `clip-path: inset(-100% R% -100% 0)`, where `R` is
`1 - u` from the twin. Replacing the mask was not enough by itself, and that took
a second report to see: **a `clip-path` clips to the element's box too**, so with
`inset(0 …)` a face taller than its own line box (one whose metrics could not be
read yet, a fallback substitution, a display cut that overshoots its recorded
ascent) still lost its cap and its descender — the same bite, from a different
direction. Growing the rectangle's vertical insets to `-100%` makes the clip three
times the word's height, which nothing paints outside, and leaves exactly one edge
that can bite: the vertical line the sweep is made of.

The box itself is measured from the font file — `winAscent + winDescent` over
`upm`, floored at 1.02 em — because that is the height `app/text_motion.py` gives
the line in the ASS file, so the change moved the preview *towards* the render
instead of inventing a second guess. The word no longer carries `padding` with a
compensating negative margin: that pair was left over from the mask era, and an
absolutely positioned overlay is placed against its parent's *padding* box, so the
padding was lifting the coloured copy a few pixels above the letters it is
supposed to colour. One box, both copies, no offset — and the twin's arithmetic
does not move, because the box is unchanged in size, only in who owns it.

## Reading the audio: two bands on a dB ruler

The lane under the strip used to be a single dark polygon of peak amplitudes, scaled
against full scale, and it was described in the field as "hard to see whether words are
actually spoken". That is what a peak view *is*: `max(|x|)` per pixel column, which says
"a transient happened somewhere in this column" and says nothing about whether anything
was *said*. Measured on a real voice, a bucket at 5 % of the file's loudest sample — a
syllable, a breath, the tail of a sentence — draws 0.056 of the half-height, i.e. a few
pixels, and silence draws the same. A flat lane and a spoken lane were the same picture.

Three changes, in the order the reading needs them:

* **Peaks *and* RMS.** The backend reduces each bucket to both (`_waveform_buckets`,
  pure, so it is testable without FFmpeg). The dark shape is the peak envelope, the
  lighter band inside it is RMS, and the gap between the two is the crest factor — wide
  for speech and untreated drums, narrow for a compressed music bed. That gap is the
  thing that distinguishes a syllable from a click: measured on a file with one
  full-scale sample dropped in front of a tone, the click's bucket reads `peak 1.0 /
  rms 0.0707` and the tone's reads `1.0 / 1.0`. One value per bucket cannot say that.
* **Measured against the file's own peak, not full scale.** Both series are divided by
  the loudest bucket in *this* movie before they leave the backend, and the API returns
  `reference: 1.0` to say so — the contract the lane draws against. A quietly recorded
  voice then fills the lane instead of hugging the centre line.
* **A dB ruler by default, with *linear* one click away.** Silence to the file's peak
  spread over 48 dB: the same 5 % bucket is 0.05 of the lane linear and 0.89 of it in
  dB, and 2 % is 0.29 instead of invisible. Amplitude is kept as the alternative because
  it is the honest view of clipping, and the choice is remembered in
  `localStorage` (`slideshow.karaoke.waveScale`) rather than in `KaraokeData`: which way
  *you* want to read a level is not a property of the caption, and it must not ride into
  the MP4 or a project file. The scale along the left edge is five mirrored marks
  (`-48 · -24 · 0 dB · -24 · -48`) because the lane itself is mirrored around the centre
  line; a ruler that only counts down from the top describes a shape nobody draws.

What silence draws is the part that earns the height: nothing but the centre line. So
the flat runs *are* the gaps, and a sentence boundary belongs at the edge of a hill.
Marks for both timelines are painted on top of the picture — one tick per sentence
start (`i`, the selected one bright), and one per word of the selected sentence (`u`,
`sung` once the playhead is past it) — because a level you can see is only useful next
to the words you are timing against it. `karaokeWaveLevel` and `karaokeWavePaths` are
pure and measured in `AudioLaneScaleTest`; the three smaller waveform drawings (the
`wave` surface, the word bar's mini lane, the lane) share `karaokeWavePaths`, so a
bucket means one thing in the whole popup.

### Four more things, after the lane was used for real

The first version of the lane was drawn and then judged by eye, and three of the four
changes below are what that judging found — each one is a technique the editors that have
decades of speech-annotation use already have:

* **The buckets are interpolated, not repeated.** A bucket covering thirty columns used to
  paint thirty equal pixels, which reads as hard edges the audio does not have — and it
  made a *taper* (the shape that tells you a word is ending) look like a cliff. A column
  now takes a weighted mix of the two buckets around it, so the slope is a slope. This is
  the same reason min/max is used instead of an average: an average hides a transient, a
  repeat invents one.
* **The envelope gets a stroked silhouette** (`path.edge`) on top of the fill. An aim point
  is an *edge*, and a shape whose outline is the same colour as its body has nothing to put
  a boundary against.
* **Silent runs are shaded** (`path.gap`, behind the wave) and their edges are added to the
  magnet beside the onsets. This is the single idea the annotation manuals agree on — Praat
  guidance is literally "the waveform goes from flat to spiky where the word starts, put
  the boundary there" — so the lane now marks the flat runs instead of leaving you to
  notice them, and *B* will pull a word onto the end of one. Only the edges that face
  speech are offered: a run that touches the start of the file has no boundary to find
  there, and snapping a word to the IN point would be the editor inventing a correction.
  Which runs count as silent is decided at 40 dB under the file's own loudest sample and
  *never* through the display scale — switching between dB and linear must not make the
  lane claim the recording changed — and the level is deliberately conservative, because
  the same list steers the magnet. It is computed once, inside the lane, and handed to the
  popup as `gapRuns`: a second call with its own threshold would be a second opinion about
  the same audio.
* **The five shapes have a fixed order**, because they are drawn over each other: shaded
  silence, peak envelope, RMS band, centre line, silhouette — the outline last so nothing
  ever paints over the edge being aimed at. `WaveLaneShapeTest` pins the order in the
  markup, so the list of `<path>`s cannot be tidied into a different picture.
* **The lane's resolution follows the drawn width** (`karaokeWaveBuckets`, asked for as
  the probe's `buckets=`: 800 / 1600 / 2400 / 4000). What the lane has to say is one value
  per painted column, and the painted width is the strip's 700-odd CSS pixels times the
  zoom — a screenful at fit, ~42 000 px at 60×. The constant it used to be, 1400 buckets,
  is therefore right at fit and one column every thirty pixels at the top of the range,
  which is precisely "the peaks go soft when I zoom in". Quantised onto four rungs instead
  of computed exactly, because a request is an FFmpeg decode: `waveAsked` makes it one
  request per rung, and the backend's probe cache is keyed by bucket count, so pacing back
  across a boundary is free rather than another decode. 4000 is the API's ceiling and the
  honest limit — a bucket is `span / 4000` seconds, 3 ms on a twelve-second cut and 30 ms
  on a two-minute one, while a word boundary is only ever timed to ~50 ms. Below that, the
  right picture is a spectrogram, which is the next point.

**What was deliberately not done: a spectrogram.** The Praat/ELAN literature is blunt that
a waveform cannot distinguish speech from a lip smack, a breath or a door, and that the
spectrogram is the tool for that. It is also blunt that the waveform is the right tool for
"where does this word start and end", which is the question this popup asks — and a
per-column frequency image in an SVG lane, for every clip, in a popup whose job is to drag
four numbers, is a different feature. The onset marks and the shaded gaps are the 90 % answer
here; if a caption is genuinely unreadable from the waveform, the honest advice is that the
audio needs a listen, not another picture.

## Two things the full-width change broke, and how each was fixed

* **The preview vanished.** `display:contents` on `.kara-left` made the stage a grid item,
  and a grid item sized by `max-width` with `justify-self:start` is sized by its *content*
  — whose every child (`video`, the caption overlay, the tap cue) is `position:absolute`.
  Content width zero, `aspect-ratio` on zero, no picture. The box now has a real
  `width:min(100%,520px)`, and `PreviewVisibilityTest` pins the shape of that declaration
  rather than trusting that the layout "should" work: this is the second bug in a row that
  a rule in the file could not prevent.
* **A selection made while paused was selected but off screen.** The playhead follow is
  switched off while paused on purpose (a scrub must not fight the pointer), so nothing
  moved the box: the block got its highlight somewhere the user could not see it, which is
  what "the first timeline is not updated when I click a numbered sentence" looked like.
  A deliberate selection now asks for one centring pass (`reveal`), and only a deliberate
  one — a drag still never yanks the view around.


## Two rulers, one axis, and how the words pane got its controls back

Four layout asks, one of which was a cascade bug worth writing down:

* **The lanes are the popup's width, not the preview's.** They are grid items of
  `.karaoke-body` (`.kara-left` is `display:contents`, so the stage, the transport row,
  both timelines and the controls row are rows of the same grid) and each lane says
  `grid-column:1/-1`. It did not work the first time, for a reason no screenshot
  explains: a second, later `.kara-left{display:flex…}` rule in the same file overrode
  the `display:contents` above it, so the lanes stayed children of a column as wide as
  the video and `grid-column` was never applied. Both rules were in the file; the later
  one won. `PaneLayoutTest.test_the_lanes_span_the_popup_not_the_preview` therefore
  greps for a *second declaration of the class* rather than trusting that the intended
  rule exists.
* **The selected sentence's controls live under the strip again.** `Selected sentence
  x of y` with *Split here*, *Merge* and *Start here* had moved behind *Settings* in the
  previous change, and that turned out to be a bad trade: the line you are timing is the
  line whose boundary you are moving, and a click away is one click too far. They are a
  full-width row of the same grid now. *Settings* keeps the things that are not a
  timeline — style, text look, first pass, the caption window, the numbers — including
  the new bulk action below.
* **A row's × merges it into the row before it.** `glueToPrevious(i)` writes `br: -1` on
  the previous line's last word. That is not "remove the flag", it is "never cut here":
  `-1` beats punctuation *and* a hard break in the text, so the × also fixes the
  sentence a full stop made — while an old project saved with real newlines still splits
  the way it always did. `SentenceBreakIsAFlagTest` runs all four cases through the real
  `planOf`.
* **The rail scrolls under the wheel.** `karaokeRailWheelScroll(deltaY, deltaX,
  deltaMode, scrollHeight, clientHeight)` is the decision, and it is in core because
  each of its four rules is a complaint waiting to happen: a sideways swipe belongs to
  the strip beside it, a Firefox `deltaMode: 1` means lines and must be scaled, a list
  with nothing to scroll must leave the event to the page, and one notch is clamped to
  60 % of the visible height so it cannot throw the list from end to end. The listener
  is native with `{ passive: false }` — React's `onWheel` is passive and cannot
  `preventDefault`, which is exactly the half-scroll that makes a popup feel broken.

## A sentence break is a flag, never an edit

The report was "too many 1-word sentences since the last-word timing change", and the
cause was in the *text*: *New sentence* inserted a `\n` into the caption at the caret.
`tokenize` treats every hard break as a sentence boundary — that is how a lyric line
becomes a line — so one stray keystroke both minted a sentence out of whatever single
word happened to sit beside the caret *and* re-wrapped the rendered caption. Nothing in
the plan's arithmetic was wrong; the plan was reading a real edit as a real sentence.

Now a break is a word flag and the editor never writes to `item.text`: *New sentence*
sets `br: 1` on the word before the caret, the rail's × sets `br: -1`, `hold` rides the
last word of a line, and the same three surfaces — the pane, the numbered rows, the
strip — read that one list of flags, which is what "the sentence in *The words* should
match the numbered sentence" means. A test says it rather than a promise:
`SentenceBreakIsAFlagTest` asserts that neither handler contains `setDraftText`, and
that the four flag combinations produce the four expected `[[from, to], …]` plans.

## Spacing every word evenly, per line

*Space all words evenly* in *Settings* divides each sentence's own window by its own
word count (`karaokeEvenWordLine`, shared with the per-line *Space evenly*). Two details
make it safe to press at any time:

* A line's window is never moved, only divided, so a handoff you already tapped with a
  neighbour survives — the alternative is a bulk action that quietly re-times the whole
  caption.
* The last word's `hold` is retuned to the new slot. A line's window is its last word's
  end plus the tail, so a hold left over from a longer line keeps revealing after the
  boundary — the sung colour would still be crawling into the next sentence. This is the
  same bug class as the typed *Sentence to* field, and it is why `karaokeHoldForEnd` and
  `karaokeEvenWordLine` exist instead of a subtraction at each call site.

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

The `ass=` filter carries **`fontsdir=` too, and that is not decoration**: the TTFs
this app renders with are shipped in their own directory rather than installed into
the image, so libass learns the caption's family name exists only because it is
pointed there (`karaoke_overlay_filter(ass_path, self.settings.fonts_dir)`, the same
`settings.fonts_dir` the caption path has always passed). A missing `fontsdir` is not
an error either, and what it costs is measured rather than assumed (a real FFmpeg 9 with
fontconfig, DejaVu on disk, Montserrat only in the app's own directory): libass falls back
to *some* face, so the line is drawn in the wrong font — different metrics, so a different
width and position, and the sweep colour comes out wrong as well. Only a build with no
usable fallback draws nothing at all. So the symptom of a missing `fontsdir` is a caption
that looks wrong, not a caption that is gone; when the words are *absent*, look at the
window rule below first. `TextStyleTest` pins the wiring on the call site and
scans every `f"ass=filename=` in `text_effects.py`, `text_motion.py` and `renderer.py`
for a `fontsdir` in the same expression: no libass overlay may be built without one.

Two degradations, both deliberate: a caption whose words are all un-timed is
plain text (`has_karaoke()` returns false, and `sanitizeKaraoke()` refuses to
store an empty clock), and on an FFmpeg build without libass the karaoke branch
is not entered at all, so the slide keeps its words, position, style and plain
fades rather than failing the job.

**Neither is silent.** "The MP4 is fine except the sung words are missing" is
otherwise undiagnosable from outside the renderer — no FFmpeg error, no failed
job, no pixel to point at — so `karaoke.degrade_reason(item)` names every state
that keeps a block out of the render (empty caption, no block, times never
tapped, times outside the 0-86399 s a subtitle clock can hold, caption switched
off) and `_text_filter` logs it as a warning naming that slide's
`text-NNNN.ass`. A slide that *does* burn karaoke in logs one INFO line with its
event count and the fonts directory libass was handed, so

    docker compose logs slideshow | grep -i karaoke

answers "did the sung words get a font, and were there any events?" without a
repro. Read the switched-off case first: `textEnabled: false` hides the caption
*and* the karaoke with it, because the sung words are the caption — while the
karaoke editor keeps previewing them, which is exactly how "the preview shows
them, the render does not" comes to be reported.

`/api/health` carries the two numbers the same question needs before any log is
grep'd: `revision` (the commit the image was built from, stamped by CI's
`SLIDESHOW_REVISION` build arg; `untracked` means a build without it, not an old
image) and `fonts` (the directory, whether it exists, how many font files are in
it, and how many registry families have a file there). `familiesWithFile: 0`
means no caption can find its face in any engine, and it is a deployment
question, not a project one.

## The window the line is sung in

A karaoke caption has two clocks, and until now only one of them was honoured.
`textStart`/`textEnd` are the storyline's caption handles; `karaoke.words` is the
line the user tapped by ear against the movie. `plan_of` clamps the words into the
handles, so **a line longer than the caption window had its tail deleted in
silence**: no event, no FFmpeg error, a finished MP4 without the words.

The two windows are not merely different, they are different *in the direction that
guarantees a loss*. The editor bounds the strip by the clip's kept length
(`karaokeClipWindow`); the render bounds the caption by `textEnd`, whose default is
the slide's `duration` — and for a movie `duration` is a floor, never a cap, since
the MP4 plays the file to its end regardless. Tap a whole song onto a 30 s clip
with the default 8 s duration and everything after 8 s was cut, while the popup
that produced those timings kept showing every word. That is the report "the final
result renders okay, except the karaoke text is missing", and it was never a font
or a filter-graph problem.

So: **the sung line widens its own caption window, and stops where the slide ends.**
`karaoke.speech_span()` (twin `karaokeSpeechSpan`) is first tapped time to last
tapped time plus `tail`; `karaoke_document()` takes `min(textStart, span.from)` and
`max(textEnd, span.to)`, clamped to the slide's *visible hold* — which is why
`Renderer._text_filter` gained a `hold=` argument fed by `durations[index]` rather
than reusing `item["duration"]`: the hold is the only number that knows a movie runs
to 12 s while its slide "says" 8. Widening is one-way, so a line that fits inside
its handles keeps the caption's fade timing byte for byte, and `textStart` still
delays the words — it just cannot delete the ones before it.

Verified frame by frame, not by reading: the same slide (`textEnd` 2 s, six words to
6.7 s, a 12 s movie) renders an empty frame at 3.5 s before the change and
`To mountain high now forever always` with `high` mid-sweep after it, the whole line
still sweeping at 5.5 s. `SpeechWindowTest` pins all four sides of it — reaches the
last word, bounded by the hold, no-ops for a short line, and that the render loop is
the one supplying the hold.

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

* **A sentence break is a word flag.** Nothing in this editor writes to
  `item.text`: *New sentence* sets `br: 1` on the word before the caret and the rail's
  × sets `br: -1`, because `tokenize` reads every hard break as a boundary — an edit to
  the text to "fix the timing" re-wraps the rendered caption *and* mints a sentence,
  which is exactly how the one-word sentences appeared.
* **The pane, the rail and the strip read the same flags.** A row of *The words* is a
  row of the plan is a block of the bar, in the same order, because all three come from
  one `planOf` pass over `words[i].br`. A merge that the `maxWords` cap would undo says
  so instead of claiming a row moved.
* **A level is measured against the file, not full scale.** The lane's 0 dB is this
  movie's loudest bucket, the same divisor for peaks and RMS so the gap between the two
  bands stays the crest factor, and the choice of dB or linear is a browser preference —
  never in `KaraokeData`, so it cannot ride into an MP4 or a project file.
* **No button inside a button.** A rail row is a `div[role=button]` with its own
  key handler, so the × in it is a real button with a real click target.
* **The lane's columns match the strip's.** `karaokeWaveBuckets` picks the probe's
  resolution from the drawn width, one request per rung of the ladder; a lane that kept
  its fit-zoom detail at 60× is a lane that blurs exactly when it is being used.
* **The magnet's candidates are onsets *and* silence edges**, sorted into one list and
  limited by the same radius. Adding a source can only improve which candidate is nearest;
  it cannot make the magnet grab further than `snap` says.
* **The stage box has a width of its own.** Its content is entirely absolutely positioned,
  so nothing else can give it one: `width:min(100%,520px)`, not `max-width`.

## Tests

* `backend/tests/test_database.py` — the block, the word times and its text style
  survive the save the app actually performs (`payload_json` in, snapshot out),
  and the `.ass` built from the *loaded* item carries them: `Style: FX,Impact,96`,
  `\pos(960,340)` — the caption's x, the block's y — and the words in order. That
  is the "is my karaoke in the final result?" question, asked where it can be
  answered.
* `backend/tests/test_karaoke.py` — `DegradeReasonTest` pins each silent case to
  one sentence, including the two that must *not* be reported (a `br`-only block
  and a usable one); `backend/tests/test_renderer.py`'s `KaraokeDiagnosticsTest`
  pins that the renderer actually logs them, and that a healthy karaoke slide
  logs its event count and `fontsdir` and warns about nothing.
* `backend/tests/test_font_status.py` — the `/api/health` fonts report: names
  only, no fontconfig and no FFmpeg, and an absent directory is a number rather
  than an exception.
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
  the editor draws it, and the clip window it draws it on (the IN point, the kept
  span, the `null` that means "nothing to stop at", the clock that parks the
  playhead, and `karaokeClipWindow` measured against `renderer.video_trim_window`);
  the clamp (1 is "the clip already fits", nothing narrower;
  12 is twelve screens and the ceiling the ruler can still express; a NaN from a
  hand-written project file is just "fit"), the centre-follow scroll (the middle of
  the box stands on the playhead whenever there is film to scroll, and the scroller
  never goes off its own left edge), the ruler's step (always a rung of the ladder,
  never coarser for a longer clip,
  never finer for a bigger one, 40–140 px of room per tick at every span from 1 s
  to an hour, at most 139 labels, ticks unique and reaching the end, and labels that say the time they
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
* `backend/tests/test_karaoke_wordbar.py` + `karaoke_wordbar.mjs` — the word bar and the
  lane under it. The geometry (bar span, per-word left/width, a divider dragged into its
  neighbours, a typed end, the ruler's label budget at the top of the zoom range) is run
  through `karaokeCore.ts` by Node, so it is measured rather than eyeballed;
  `AudioLaneScaleTest` and `WaveformBucketsTest` pin the audio lane's vertical scale and
  the bucket maths — the latter on hand-built samples, because this repo's `test` job has
  no FFmpeg and a lane that can only be tested by decoding a file cannot be tested there
  at all. `SentenceBreakIsAFlagTest` puts the four `br` combinations through the real
  `planOf` and asserts that neither *New sentence* nor the rail's × contains
  `setDraftText`, which is the whole fix for the one-word sentences.
  `PopupShapeTest` and `PaneLayoutTest` read the two files instead of running them —
  there is no browser here, so what they can honestly pin is the *rules*: that a lane
  claims `grid-column:1/-1`, that no later rule un-claims it, that the × is not a button
  inside a button, that the controls row is under the strip and not in the settings
  layer. They are text assertions, deliberately, and each one says which change would
  have silently undone the feature.
