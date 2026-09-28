# Pop-ups always open on top — audit & fix

Date: 2026-09-28 · Status: **implemented** (`src/layers.tsx`, all popup roots)

Request: *check the whole application — whenever a pop-up window is opened it
must be on top.*

## How stacking used to work

Every popup root was a `position: fixed` element whose z-index came from a
hand-kept ladder of CSS classes:

| rung | class | used by |
|---|---|---|
| 50 | `.modal-backdrop` | lightbox, browsers, pickers, confirms, previews, editors |
| 60 | `.modal-backdrop.stacked`, `.movie-backdrop`, `.look-backdrop`, `.transition-browser` | editors opened from the lightbox, chip popovers |
| 70 | `.modal-backdrop.over-editor`, `.font-picker`, `.upload-tray` | media browser opened from an editor |
| 90 / 100 | `.story-context-menu`, `.toast` | |

Two popups on the same rung are ordered by **DOM order** (the later sibling
paints on top), and all app-level popups are siblings at the end of `App`.
Whether a popup landed above its opener therefore depended on where it was
mounted in the JSX and on which class it happened to carry — not on the fact
that it was opened later.

## What was broken

Reproducible cases where the new window opened *behind* the one it was opened
from (all fixed by this change):

1. **Transition gallery from the transition preview** — click a transition
   marker in the storyline, open the chip, *Browse all* → the gallery (rung 50,
   mounted earlier) rendered under the preview modal (rung 50, mounted later).
2. **Transition gallery from the text frame editor** ("Transition A → B" chip)
   — same, and when the editor was opened from the story preview it was on
   rung 60, so the gallery lost outright.
3. **Per-photo look editor from a collage editor opened via the story preview**
   — preview → *Edit frame* on a collage → edit a photo's look: the look editor
   (rung 60) sat below the stacked collage editor (also rung 60, mounted later).
4. **Escape leaked through popups** — the gallery opened from the text frame
   editor closed *both* on Escape (and reverted the edit); with the background
   picker open above the text frame editor, Escape closed the editor underneath
   the picker instead of the picker.
5. **Nested dialogs closed their parent on a backdrop click** — the media
   browser's preview lightbox and its *Delete from /uploads?* confirm, and the
   project loader's delete confirms, are rendered inside their parent's
   backdrop; the mousedown that closed them bubbled on to the parent backdrop
   and closed the whole browser/loader as well.

Verified fine before and after: editors opened from the lightbox, media
browsers opened from editors, portal popovers (transition / text-effect chips)
over editors, the font picker and the text-effect browser/gallery inside the
frame editor, the text-motion editor closing its browser before opening the
gallery.

## The fix: newest popup on top, by construction

`src/layers.tsx` keeps a tiny registry of open popup layers.

- `useLayer()` — called by every popup root; returns the z-index to render
  with. A new layer is always one step (`10`) above the highest layer still
  open, starting at `50`, and is released on unmount. Order of opening — not
  DOM order, not a class — decides who is on top. Freed rungs are reused, so
  the numbers stay bounded by the nesting depth. `useLayer(open)` is used by
  the chip popovers that render conditionally; `useLayer(true, bump)`
  re-registers on top when `bump` changes (the upload tray uses the newest
  upload id, so a new batch surfaces above the browser that started it).
- `useEscapeToClose(z, onClose)` — Escape closes a popup **only while it is
  the top-most layer**. Handlers of every layer run in the same keydown
  dispatch, and the registry changes only when React commits, so a parent
  never reacts to the Escape that closed its child.
- `PopupBackdrop` — a `.modal-backdrop` that is a layer, closes on Escape and
  on a backdrop mousedown, and **stops that mousedown** so nested dialogs do
  not take their parent down with them. Used by the confirm dialogs, the
  folder picker, project loader, project file browser, transition preview and
  the preview player.

Roots that now carry `style={{ zIndex: layer }}`: media lightbox (storyline
and browser), story context menu, soundtrack editor, default-text-style
dialog, text/frame editor, collage editor, media browser (+ nested confirm),
folder picker, project loader (+ confirms), project file browser, confirm
dialog, transition preview, preview player, transition gallery, movie editor,
picture look editor, font picker, transition chip popover, text-effect chip
popover, text-effect browser popover, text-effect gallery, upload tray.

Nested popups rendered inside another backdrop (the font picker inside the
frame editor, the browser's own lightbox) stay inside their parent's stacking
context — `backdrop-filter` makes every backdrop one — which is fine: they
only have to beat their parent's content, and their parent is already the
top-most full-screen layer when they open.

Toasts are not layers; `.toast` moved to `z-index: 1000` so it stays above
any depth of popups. The class ladder in `styles.css` is kept only as a
fallback for a root without a layer.

## Checking it

`npm run build` (tsc + vite) passes. The registry was exercised with a
jsdom + `react-dom/client` script (not committed — the repo has no frontend
test runner): later-opened popups always get the higher z-index regardless of
DOM order, Escape closes only the top layer, a nested `PopupBackdrop` click
leaves its parent open, freed rungs are reused and the registry is empty once
everything is closed.
