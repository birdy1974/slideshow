/**
 * The pointer ↔ seconds maths of the collage's per-photo timing lane, kept out of
 * the component so it can be measured (`backend/tests/collage_lane_math.mjs` runs
 * it under node, and `backend/tests/test_collage_photo_window.py` compares the
 * answers with what the lane needs them to be).
 *
 * There are two reasons this file exists instead of six lines inside the
 * component. The first is the bug it was written for: a lane's outer box also
 * holds the photo name and the seconds column, so mapping the pointer through it
 * shifted every drag by the width of the name column and scaled it by the ratio of
 * the two widths — the block answered to a click ~230 px away from where the ruler
 * said it should be. The mapping has to be computed from the *track* the pointer is
 * actually over, and a pure function makes that a fact you can assert instead of a
 * habit. The second is that a drag needs the geometry captured when it began: a
 * `getBoundingClientRect()` read on every pointer move jitters when the row under
 * the cursor re-renders, and it goes to zero if a scroll re-parents the element
 * mid-drag.
 */

/** What the lane needs from the track under the pointer: a horizontal ruler, and
 *  nothing about it vertical. */
export interface LaneRect { left: number; width: number }

/** The render is 25 frames a second, so a fortieth-of-a-second grid is the finest
 *  unit that still means something: every value the lane writes is a whole frame of
 *  the MP4 (and the beats it snaps to were found on that same clock). */
export const LANE_FPS = 25
/** The narrowest window worth writing: below it the entrance has no room and the
 *  engine refuses the stop anyway (see `exit_start` in `backend/app/collage.py`). */
export const MIN_WINDOW = 0.12

export function snapSeconds (t: number, fps: number = LANE_FPS): number {
  if (!Number.isFinite(t)) return 0
  return Math.round(t * fps) / fps
}

/** A lane with no usable track (hidden popup, zero width, a row mid-mount) maps
 *  to the start of time rather than to a division by a missing number. A type
 *  predicate, so the callers below stay honest about `rect` without repeating the
 *  whole list of checks. */
function usable (rect: LaneRect | null | undefined, span: number): rect is LaneRect {
  return !!rect && Number.isFinite(rect.left) && Number.isFinite(rect.width) && rect.width > 8 && span > 0
}

/** Seconds at the pointer, measured across this track alone. */
export function secondsAt (rect: LaneRect | null | undefined, span: number, clientX: number): number {
  if (!usable(rect, span)) return 0
  return Math.max(0, Math.min(span, ((clientX - rect.left) / rect.width) * span))
}

/** How far the pointer travelled, in seconds of this track. Independent of
 *  `rect.left` on purpose: a row that scrolls sideways mid-drag must not jump. */
export function deltaSeconds (rect: LaneRect | null | undefined, span: number, fromX: number, toX: number): number {
  if (!usable(rect, span)) return 0
  return ((toX - fromX) / rect.width) * span
}

/** Dragging the middle of a block: the width is the person's, so only the whole
 *  window moves, and it never leaves the lane. */
export function moveWindow (
  rect: LaneRect | null | undefined, span: number, fromX: number, toX: number, at: number, until: number,
): { at: number; until: number } {
  const width = Math.max(MIN_WINDOW, until - at)
  const from = snapSeconds(Math.max(0, Math.min(span - width, at + deltaSeconds(rect, span, fromX, toX))))
  return { at: from, until: from + width }
}

/** Shift-click: pin the arrival where the pointer is. It cannot pass the stop, and
 *  it cannot eat the window the photo needs to be seen arriving. */
export function pinStart (rect: LaneRect | null | undefined, span: number, clientX: number, until: number): number {
  const t = snapSeconds(secondsAt(rect, span, clientX))
  return Math.max(0, Math.min(t, until - MIN_WINDOW))
}

/** Ctrl/⌘-click: pin the *departure* — the instant the photo is fully gone, which
 *  is where the exit's own fly ends, not where it starts. */
export function pinStop (
  rect: LaneRect | null | undefined, span: number, clientX: number, at: number,
): number {
  const t = snapSeconds(secondsAt(rect, span, clientX))
  return Math.min(span, Math.max(t, at + MIN_WINDOW))
}

/** One edge, dragged: the same clamp as the pin, from a delta instead of a
 *  position, because grabbing an edge you cannot see the far end of. */
export function dragEdge (
  rect: LaneRect | null | undefined, span: number, fromX: number, toX: number, at: number, until: number,
  edge: 'start' | 'end',
): number {
  const delta = deltaSeconds(rect, span, fromX, toX)
  if (edge === 'start') return Math.max(0, Math.min(snapSeconds(at + delta), until - MIN_WINDOW))
  return Math.min(span, Math.max(snapSeconds(until + delta), at + MIN_WINDOW))
}
