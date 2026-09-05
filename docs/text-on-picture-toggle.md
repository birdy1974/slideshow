# Enable/disable text on picture — feature & GUI placement options

Date: 2026-09-05

Each slide now carries a `textEnabled` flag (default: on). Turning it off keeps the
caption text and its timing, but the text is not drawn on the picture — in the
preview, in the editor, and in the final FFmpeg render. Title frames are unaffected:
a text frame *is* its text, so the flag never applies to `type === "title"` items.

Implemented controls (the recommended placement) are marked **[implemented]**.
Alternatives are listed so we can switch cheaply if a different spot is preferred.

## Storyline (overall timeline)

- **S1 [implemented] — per-clip eye badge (top-left corner, next to the select checkbox).**
  Shown only when the clip actually has text. Click toggles on/off; the badge is green-eye
  when shown and amber eye-off when hidden. When hidden, the caption box in the text lane
  above the clip renders dashed + struck-through so the state is visible at a glance.
  - Pros: one click, state always visible exactly where the clip lives, no new toolbar.
  - Cons: one more corner control on a clip that already has select/zoom/duration chips.
- **S2 — toggle inside the text-lane box** (eye at the box's right edge).
  - Pros: sits where text is edited/timed.
  - Cons: the box can be very narrow (min 3% of the clip), so a button overflows and is a
    bad click target; the lane already hosts enter/exit + two timing handles.
- **S3 — selection-driven button** in the existing bulk bar / inspector ("Text on/off").
  - Pros: zero per-clip clutter and free bulk support for many clips.
  - Cons: two steps (select, then toggle) and the state is not visible without selecting.
  - Recommended later as an *addition* to S1 for bulk edits, not a replacement.

## Detailed slide list

- **L1 [implemented] — eye button at the start of the caption controls row**
  (before appear / text input / disappear / On-picture-New-frame). Amber when hidden; the
  text input renders struck-through while hidden.
  - Pros: directly next to the text it hides; always present; obvious.
  - Cons: one more small button in an already dense row.
- **L2 — eye overlay on the row thumbnail** (like the duration badge).
  - Pros: visually ties the flag to the picture.
  - Cons: thumbnail already carries badges; tiny target; conflates "view" with "hide text".
- **L3 — a dedicated TEXT column / checkbox in the timeline head grid.**
  - Pros: scannable down the list.
  - Cons: widens the grid for a rarely-used flag; most rows would show an empty cell.

## Optional global additions (not implemented)

- A master "Text on picture" switch in the *Default text style* modal, with per-slide
  override (per-slide flag wins).
- Bulk "Hide text / Show text" buttons in the bulk bar for the current selection.

## Behaviour notes

- Preview stage hides the caption when disabled; the timeline text lane keeps showing the
  (struck-through) box so timing is not lost.
- Backend `Renderer._text_filter` returns no drawtext filter for a disabled caption, so the
  final render matches the editor. Covered by `TextOnPictureToggleTests`.
