# Text frame editor — layout, space below the picture, pinned buttons

Date: 2026-09-11

The text-frame editor popup previously used a fixed two-column layout (a 16:9
preview on the left, a narrow 255 px sidebar on the right). Because the sidebar
holds a lot of controls (text, typography, text animations, colours A/B, the
A→B colour transition, position readout), it grew far taller than the preview,
which left dead space under the picture and — on shorter screens — pushed the
footer (Cancel / Save) out of view. This change addresses the three requested
points.

## 1. Save / Close / Cancel always available

- The dialog is now a flex column with `max-height: calc(100vh - 56px)`.
- The header (title + **Close ✕**) and the footer (**Reset position / Cancel /
  Save**) are `flex: 0 0 auto` — they stay pinned while the middle scrolls.
- The middle (`.frame-editor-body`) is `flex: 1; min-height: 0; overflow: auto`,
  so any amount of controls is reachable without ever losing the buttons.
- Bonus: in the default layout the preview canvas is `position: sticky; top`
  within its column, so the picture stays visible while scrolling the controls.

## 2. GUI option to use the available space better

A **Sidebar | Below** toggle sits in the editor header (next to the close
button, with tooltips). The choice is remembered in `localStorage`
(`textFrameLayout`).

### Sidebar (default)

Controls stay in a right column, widened from 255 px to 300 px, and the dialog
was widened from 1050 px to 1180 px so the preview and controls both gain room.

### Below

- The editor collapses to one column: the **preview stretches across the full
  width** (width capped so it never swallows the whole viewport:
  `min(100% - 36px, (100vh - 340px) * 16/9, 1120px)` — still an exact 16:9
  frame, matching the rendered slide).
- The controls move into the **space beneath the picture**, arranged in a
  responsive multi-column grid (`repeat(auto-fit, minmax(250px, 1fr))`):
  Frame text and typography sit side by side, while Text animation, Colour
  A/B and the A→B transition span the full width.
- The position readout and the help line remain below the grid.

## 3. Behaviour notes

- No data-model or backend change; only `src/App.tsx` (layout state, header
  toggle, a `.below-grid` wrapper around the controls) and `src/styles.css`.
- The drag-to-position interaction is unchanged — the canvas keeps
  `position: relative`-like positioning (sticky is a positioned ancestor), so
  the absolute-positioned caption and `dragOnStage` percentages work as before.
- At ≤760 px the layouts collapse identically (stacked canvas + controls) and
  the toggle shrinks to icons.
