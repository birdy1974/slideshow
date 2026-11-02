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

/**
 * How a click changes the *selection*. Three verbs, one implementation, because the
 * lane's checkbox, its number cell, the photo strip's number and the preview's
 * picture all have to answer the same question the same way — a UI where one control
 * toggles and another replaces is a riddle. `single` means "this one only", and
 * clicking the photo that is already the only one selected lets it go again; `toggle`
 * is the checkbox and Ctrl/⌘; `range` is Shift, measured from the last thing clicked.
 *
 * It lives here rather than in each component for the same reason the drag maths
 * does: `backend/tests/collage_lane_math.mjs` can run it, so the modifier rules are
 * a fact a test holds rather than a habit four call sites drift apart from. The
 * module imports nothing on purpose, which is what makes it measurable under node.
 */
export type SelectionMode = 'single' | 'toggle' | 'range'

export function nextSelection (current: number[], index: number, mode: SelectionMode, count: number): number[] {
  const n = Math.max(0, Math.floor(Number(count) || 0))
  const list = (current || []).filter(i => Number.isInteger(i) && i >= 0 && i < n)
  if (!Number.isInteger(index) || index < 0 || index >= n) return list
  if (mode === 'single') return list.length === 1 && list[0] === index ? [] : [index]
  if (mode === 'toggle') return list.includes(index) ? list.filter(i => i !== index) : [...list, index].sort((a, b) => a - b)
  const anchor = list.length ? list[list.length - 1] : index
  const lo = Math.min(anchor, index)
  const hi = Math.max(anchor, index)
  const next = [...list]
  for (let i = lo; i <= hi; i++) if (!next.includes(i)) next.push(i)
  return next.sort((a, b) => a - b)
}

/**
 * Every photo of the selection moved by the *same* shift as the one being dragged: a
 * group is set against the ruler together, and each photo keeps its own width. The
 * delta is taken from the dragged row *after* its own clamp, so pushing a group
 * against the start of the lane stops the group — it does not let the other rows slide
 * into negative time while the anchor stands still.
 */
export function groupShift (
  rect: LaneRect | null | undefined, span: number, fromX: number, toX: number,
  anchor: { at: number; until: number }, others: { at: number; until: number }[],
): { at: number; until: number }[] {
  const moved = moveWindow(rect, span, fromX, toX, anchor.at, anchor.until)
  const delta = moved.at - anchor.at
  return (others || []).map(o => {
    const width = Math.max(MIN_WINDOW, o.until - o.at)
    const from = snapSeconds(Math.max(0, Math.min(span - width, o.at + delta)))
    return { at: from, until: from + width }
  })
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
/**
 * One write-ahead slot, for a popup that patches a spec it was *handed*.
 *
 * React does not re-render in the middle of an event handler, so a burst of
 * writes — a group edit that visits every selected photo, a lane drag that
 * writes the entrance and then the exit, a slider that patches on each move —
 * would otherwise compute every one of those patches from the same snapshot. The
 * parent applies them in order, each carrying a whole `photos` array, so the last
 * write wins and every earlier one is silently reverted: "apply to the selection"
 * ends up applying to one photo. A popup that keeps its writes in this slot
 * instead lets the next write build on the previous one, and drops the slot as
 * soon as the item carries what was written (a new `source` identity), which is
 * also what keeps an untouched slide from being rewritten at all.
 */
export interface BurstSlot<T> { source: unknown; spec: T }

/** The spec to edit right now, and the slot to keep for the next write. */
export function beginWrite<T> (
  slot: BurstSlot<T> | null, source: unknown, spec: T,
): { slot: BurstSlot<T>; spec: T } {
  if (slot && slot.source === source) return { slot, spec: slot.spec }
  return { slot: { source, spec }, spec }
}

/** Fold one write in, so the next write in the same burst builds on it. */
export function recordWrite<T> (
  slot: BurstSlot<T>, source: unknown, spec: T,
): { slot: BurstSlot<T>; spec: T } {
  return { slot: { source, spec }, spec }
}
