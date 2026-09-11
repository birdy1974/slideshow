# Detailed slide row — options for better use of the row's space

**Ask (2026-09-11):** the detailed list's slide row should show all information and
settings in the space it has — e.g. the Ken Burns (effect) box should be smaller.
Advisory only; nothing below is implemented yet.

## How the row is built today (desktop > 1100 px)

`.timeline-item` is a 7-track grid feeding **8 cells**, `min-height:82px`, 10 px gaps,
12 px padding:

| # | Track today | Cell | Contents |
| --- | --- | --- | --- |
| 1 | `34px` | row-select | grip + bulk checkbox |
| 2 | `78px` | thumb | 54 px thumbnail + overlays: position badge, video length, Filter/look chip, crop chip, Edit (titles) |
| 3 | `minmax(160px,1.85fr)` | media-info | name, path, size meta, trim badge, **caption bar** (eye · enter symbol · text input · exit symbol · On picture/New frame select), Edit frame (titles) |
| 4 | `90px` | clip-duration | duration stepper + "sec" |
| 5 | `150px` | effect select | "None / Ken Burns · Zoom in / …" (long labels) |
| 6 | `minmax(125px,1.2fr)` | transition cell | chip + time stepper + always-visible ⚙ + non-default summary line |
| 7 | `80px` | movie-audio | audio source select + hint (`min-width:125px` **overflows its 80 px track**) |
| — | *(implicit row 2)* | row-actions | ↑ ↓ remove — **wraps to a second line under the checkbox because the grid has no 8th track** |

Width budget @1680 px container: ~1584 px row inner; fixed tracks 432 px + 70 px gaps
leave ~1082 px for the two fr columns. At 1280 px screen width that shrinks to ~682 px
(media-info ~415 px, transition ~267 px) — the fixed boxes start to dominate, which is
exactly what the "Ken Burns box smaller" remark points at.

**Defect found while measuring:** the 8th cell (row-actions) has no track — it lands on
an implicit second grid row, so every row renders ~2 lines tall and the buttons sit
below the grip; movie-audio's 125 px min-width overflows its 80 px track into the
actions area. Both date back to the base commit. Any density work should fix this
first; it is also the single biggest "wasted space" item.

Two smaller observations: the size meta is a hardcoded placeholder for every picture
("6000 × 4000 · JPG"), and the effect select spends 150 px mostly on the repeated words
"Ken Burns ·".

## Options

### Option 0 — Make the grid fit its cells (baseline, must-do)
Add the missing 8th track and give audio a real one, e.g.
`34px 78px minmax(160px,1.6fr) 84px 132px minmax(125px,1.1fr) minmax(120px,.8fr) 76px`,
align the 8 head labels (the last one is empty today anyway), and drop
`.movie-audio`'s min-width so it fits its track. Rows collapse from ~2 lines to 82 px —
the list gets ~35 % shorter with zero information loss.
*Cost:* CSS only, low risk. *Frees:* one full line per slide.

### Option 1 — Slim the fixed boxes (the literal ask)
* **Effect select 150 → ~96 px:** shorten the *closed* label ("Ken Burns · Zoom in" →
  "Zoom in", "Ken Burns · Pan left" → "Pan left"); the open dropdown keeps full names,
  grouped under a "Ken Burns" optgroup. The words "Ken Burns ·" are the same in every
  non-None option — pure repetition inside a 150 px box.
* **Duration 90 → ~74 px:** tighter stepper, "s" instead of "sec".
* **Thumb chips to icons:** the Filter/look and crop chips keep their icon but lose the
  text label when inactive (active looks keep the label — that state matters); tooltips
  already carry the summaries.
* **Actions:** keep the three buttons but at 20 px hit areas (they wrap today anyway).
*Cost:* CSS + label mapping, low risk. *Frees:* ~110–140 px for name/transition.

### Option 2 — Regroup: fewer columns, wider text (biggest structural win)
* **Movie-audio moves into media-info** as a small inline select on the meta line, only
  rendered for videos. Pictures get the column back entirely; the head loses the AUDIO
  column.
* **Duration moves into the transition cell** ("4.0 s → next · 1.0 s" conceptually
  belong together) or as a mini-stepper under the name — the 90 px column disappears.
* **Effect becomes a chip on the thumbnail** (same pattern as the Filter chip):
  icon + short label, click opens a small popover with the Ken Burns list + intensity.
  The 150 px column disappears; the motion lives visually with the picture it animates.
* Result: 5 tracks — select · thumb · info · transition · actions — and the fr columns
  roughly double at 1280 px.
*Cost:* JSX restructuring + new popover; medium effort, the row's mental model changes.

### Option 3 — Two-tier row with settings chips
Keep one bordered row. Top line: thumb · name+path · duration · effect · transition ·
actions (Option 0's grid). A second line appears **only when a slide has non-default
settings** (caption text, trim, original audio, GL params/easing/reverse, look/crop):
small clickable chips summarizing each setting ("✎ Caption", "✂ 12 s of 3:04",
"♪ Original audio", "GL Cube · bounce · reverse"). Click a chip to edit that setting
inline. Default rows stay 82 px; configured rows grow honestly instead of squeezing
everything horizontally.
*Cost:* medium; pairs well with 0+1.

### Option 4 — Details on demand (slimmest rows)
Row shows: checkbox · thumb · name · duration · effect icon · transition chip · ⚙ ·
actions. The ⚙ opens a full-width drawer inside the row with everything grouped
(motion / caption / transition details / audio) — the per-row sibling of the existing
TransitionInspector bar. All rows uniform 82 px; every setting is ≤2 clicks away but
nothing is visible at a glance.
*Cost:* medium-high; biggest behaviour change (settings hidden behind a click).

### Option 5 — Density toggle
A "Cozy / Compact" switch on the VIEW bar: compact reduces row padding 10→6 px,
control heights 33→27 px, fonts −1 px. Works with any option above; lets each user pick.
*Cost:* low, but two layouts to keep working.

## Suggested package

**0 + 1** first (fix the wrap, slim the boxes — the row fits on one line and the Ken
Burns box shrinks), optionally plus **2's movie-audio-into-info** move, which removes a
whole column for picture rows without changing where transition/duration controls live.
**3** is the natural follow-up if you want trim/audio/params visible per slide without
sacrificing width; **4** only if you prefer strictly minimal rows.

**IMPLEMENTED — the full 0 + 1 + 2 + 3 package (2026-09-11):**

* **0:** the row grid now has the 5 tracks its cells need (`34px 78px minmax(200px,1.85fr)
  minmax(210px,1.2fr) 76px`); the wrapped second line and the movie-audio overflow are
  gone; head labels realigned (MEDIA · SLIDE/CLIP · DURATION · TRANSITION TO NEXT).
* **1:** the effect box is gone from the grid entirely (see 2); duration lives in the
  transition cell at 76 px; inactive look/crop chips show their icon only.
* **2:** movie-audio is an inline select + hint inside the info column (videos only);
  duration is the first control of the transition cell (last slide: duration +
  "End of story"); the Ken Burns effect is a motion chip on the thumbnail's bottom-left
  corner (short labels: "Zoom in", "Pan left", "Original") that opens a small picker
  popover in the info column — full option names kept in the picker and in the bulk tool.
* **3:** a settings-chip line appears only when a slide has non-default state:
  "Text hidden" (click restores) and the trim chip (click opens the lightbox/trim editor).
  GL params/easing/reverse remain in the transition cell's summary line; the fake
  "6000 × 4000 · JPG" meta was replaced by an honest "photo"/"video" label.
* Rows that were ~2 lines tall now fit one 82 px line; the two text columns gained
  ~180 px of width at 1280 px screen size.

The remaining unimplemented option is **4** (details-on-demand drawer) and **5**
(density toggle) — not built, no longer needed for the original complaint.
