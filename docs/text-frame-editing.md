# Editing existing text frames — implementation & GUI access

Date: 2026-09-11

The text frame editor itself always supported editing existing frames: it opens with
that frame's own text, font (family/size/bold/italic/underline), background colours
(A and the optional B + colour-change transition), caption position and duration, it
updates the item live while you edit, and **Cancel** restores the values from before
the editor opened (only a brand-new frame is discarded on cancel). What was missing
was *discoverable* access — the editor could only be reached through a small button
in the detailed list, an unlabelled click on the compact card, and a hidden
double-click on the filmstrip. This document describes the implemented access
points (marked **[implemented]**) and the alternatives that were considered.

## Access points

- **A1 [implemented] — ✎ symbol, bottom-right corner of every text-frame card.**
  In the compact grid (round pencil button), in the detailed slide list (green
  "✎ Edit" badge on the thumb, mirroring the Filter/Crop badges photos wear on the
  left) and on the overall timeline filmstrip (same spot as the 🔍 on clips). The
  tooltip always shows the frame's real data: `Edit text frame · "…" · 6s`.
  - Pros: one consistent, discoverable affordance in all three views; the card body
    stays drag-to-move.
  - Cons: one more corner control on small cards.
- **A2 [implemented] — ✎ inside the caption bar on the text timing strip.**
  Title lanes on the text track (the strip with appear/disappear transitions and
  timing handles) carry a small pencil at the right edge of the caption input that
  opens the full editor.
  - Pros: sits exactly where text and timing are already edited.
  - Cons: the lane can be narrow; the button is small (its tooltip explains it).
- **A3 [implemented] — "Edit frame" button in the story preview popup.**
  Text frames are no longer skipped by the preview: they render as the frame itself
  (background colours with the A→B change looping, caption at its position, real
  font/weight/colour), the ←/→ keys and the left/right halves now step through them
  in storyline order, and the header shows `TEXT FRAME · position 3 / 12` with an
  **Edit frame** button next to the movie's **Cut** button. The editor opens stacked
  on top of the preview (same z-index treatment as the movie/look editors); the
  preview underneath updates live while editing, and Save/Cancel/Escape returns to
  the preview.
- **A4 [implemented, pre-existing] — labelled "Edit frame" button** in the detailed
  list's info column (kept, now with a summarising tooltip), and the hidden
  **double-click** on a filmstrip title clip (kept as a bonus shortcut).
- **A5 — right-click context menu** (Edit frame / Duplicate / Delete): considered
  and skipped for now — a new interaction pattern for the app; revisit if more
  per-card actions pile up.

## Behaviour notes

- Clicking a title card's body (compact grid, detailed list, filmstrip) now opens
  the **preview popup** exactly like photos and movies do — the ✎ symbol is the way
  into the editor. Before, clicking the card silently opened the editor, which
  clashed with drag-to-move and was undiscoverable.
- The editor always shows the *current* values of that exact frame because it
  renders from the live storyline item (`media.find(id)`), so edits made elsewhere
  (inline caption inputs, timing handles, duration steppers) are reflected.
- No backend or data-model changes: text frames keep living entirely in the
  `MediaItem` (`text`, `frameBackground`, `frameBackground2`, `frameTransition*`,
  `font*`, `textX/Y`, duration …); all access points funnel through the one
  existing `setEditingTextFrame(id)` entry point.
