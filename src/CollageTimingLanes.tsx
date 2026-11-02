// One line per photo: when it appears, when it goes. Both ends of a block are the
// photo's own — the left edge is its entrance, the right edge is the end of its
// exit fly — so the lane under *Photo size & timing* is the exit timeline too, and
// the only place where appear and disappear can be set against each other.
//
// The collage's own model is a *sequence* — photo i arrives after the sum of the
// delays in front of it, then stays until the end of the hold and leaves on its
// exit offset — which is a good default and a bad way to say "this one only
// during the second line". This is the lane that says it: the block is the photo's
// real visible window (`photoStart` → `exitStart` + fly, the same two functions
// the render clips its overlays to ✓ so a row can only ever show what the MP4
// will do), and its two edges can be pulled to any frame on the slide.
//
// Leaving a row alone writes nothing at all: no `at`, no `until`, and the pile's
// schedule owns the photo exactly as before. That is what makes the feature safe
// on a slide that was never touched.
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CollageSpec } from './collageCore'
import { entranceLength, exitChoice, exitFlyLength, exitMode, exitStart, photoAt, photoDelay, photoStart, photoUntil } from './collageCore'
import { MIN_WINDOW, dragEdge, groupShift, moveWindow, nextSelection, pinStart, pinStop } from './CollageLaneMath'
import type { LaneRect } from './CollageLaneMath'


export interface CollageTimingLanesProps {
  spec: CollageSpec
  /** Seconds the ruler spans: the slide's own duration when that is longer than
   *  what the timings imply, so a stop written past the end is still on screen. */
  duration: number
  /** Every photo the popup is currently editing, in index order. One row is the
   *  ordinary case; more than one is how a group gets the same entrance, the same
   *  exit, and the same shift along the ruler. */
  selected: number[]
  /** The lane computes the next set (checkbox, number, Shift-range) and hands it
   *  over whole, so the modifier rules live in one tested function. */
  onSelected: (next: number[]) => void
  /** '' clears the field, which is the same thing as "the schedule owns it". */
  onChange: (index: number, lane: 'at' | 'until', seconds: string) => void
}

interface Drag {
  index: number
  /** The rest of the selection, snapshotted the same way as the row being held —
   *  `index` plus its own `at`/`until` — so a body drag shifts the group by the
   *  distance the anchor actually moved instead of re-reading rows mid-flight. */
  group: { index: number; at: number; until: number }[]
  /** Which edge, or the body (both edges together). */
  kind: 'start' | 'end' | 'both'
  /** Pointer position and both times when the drag began, so the maths is always
   *  measured from a snapshot and cannot drift across frames. */
  x0: number
  at: number
  until: number
  /** The track this row's block lives in, grabbed once at the start. Not the
   *  lane's outer box — that one also holds the name and the seconds columns, and
   *  measuring across it was the whole of "the cursor changed but nothing moved". */
  rect: LaneRect | null
}

/** What ends this photo, named the way the exit group names it — the right edge of
 *  a lane is the end of a *departure*, and saying which one is what makes the same
 *  lane read as an exit editor instead of a mysterious second handle. */
function exitLabel (spec: CollageSpec, i: number): string {
  const mode = exitMode(spec, i)
  const fly = exitFlyLength(spec, i)
  if (mode === 'none') return 'no exit: stays until the slide ends, unless you stop it here'
  const choice = exitChoice(spec, i)
  // A departure that names an arrival is that arrival running backwards, and the
  // id the user picked is the only name worth showing — `reverse` is what it
  // *behaves* like, not what they called it.
  const named = choice === 'reverse' || choice === mode ? mode : `${choice} (its arrival, backwards)`
  return `${named} out · ${fly.toFixed(2)}s fly`
}

/** The photo's visible window as the engines see it, authored or not. */
export function photoWindow (spec: CollageSpec, i: number): { from: number; to: number } {
  const from = photoStart(spec, i, 0)
  const to = exitStart(spec, i, 0) + exitFlyLength(spec, i)
  return { from, to: Math.max(to, from + MIN_WINDOW) }
}

export function CollageTimingLanes({ spec, duration, selected, onSelected, onChange }: CollageTimingLanesProps) {
  const photos = spec.photos ?? []
  const span = Math.max(0.5, Number(duration) || 0.5)
  const lanes = useRef<HTMLDivElement | null>(null)
  const drag = useRef<Drag | null>(null)
  const [moving, setMoving] = useState(false)

  const percent = useCallback((t: number) => `${Math.max(0, Math.min(100, (t / span) * 100))}%`, [span])
  /** The track of the row a pointer event came from. Every number the lane writes
   *  is measured across this box, because this box is what the ruler and the block
   *  are drawn inside. */
  const trackOf = (node: EventTarget | null): LaneRect | null => {
    const el = node instanceof Element ? node.closest<HTMLElement>('.collage-window-track') : null
    if (!el) return null
    const rect = el.getBoundingClientRect()
    return { left: rect.left, width: rect.width }
  }

  /** One click's worth of selection: the pure rules live in `nextSelection`, and the
   *  number cell is the affordance the row is clicked on. */
  const click = (index: number) => (event: React.MouseEvent) => {
    event.stopPropagation()
    event.preventDefault()
    const mode = event.shiftKey ? 'range' : event.ctrlKey || event.metaKey ? 'toggle' : 'single'
    onSelected(nextSelection(selected, index, mode, photos.length))
  }
  const check = (index: number) => (event: React.ChangeEvent<HTMLInputElement>) => {
    event.stopPropagation()
    onSelected(nextSelection(selected, index, 'toggle', photos.length))
  }
  /** Handing photos back to the schedule is a group act when the row you touched is
   *  part of a group: five photos pinned to one window are undone together. */
  const clearWindows = (index: number) => {
    const list = selected.includes(index) && selected.length > 1 ? selected : [index]
    for (const i of list) { onChange(i, 'at', ''); onChange(i, 'until', '') }
  }

  const begin = (index: number, kind: Drag['kind']) => (event: React.PointerEvent) => {
    // The edge handles are inside the track, and a pointerdown there would also
    // start a body drag; the first handler wins and the track's guard below keeps
    // the two apart.
    if (drag.current) return
    event.preventDefault()
    event.stopPropagation()
    const win = photoWindow(spec, index)
    // A group drag is only for the body of a block: two edges have nothing sensible
    // to share, since every photo's window ends where its own exit does.
    const group = kind === 'both' && selected.length > 1 && selected.includes(index)
      ? selected.filter(i => i !== index).map(i => {
          const w = photoWindow(spec, i)
          return { index: i, at: photoAt(spec, i) ?? w.from, until: photoUntil(spec, i) ?? w.to }
        })
      : []
    drag.current = {
      index, kind, x0: event.clientX, rect: trackOf(event.currentTarget),
      at: photoAt(spec, index) ?? win.from,
      until: photoUntil(spec, index) ?? win.to,
      group,
    }
    setMoving(true)
    // Touching a row that is already in the group leaves the group alone; touching
    // one outside it makes it the new subject. Clearing a selection by dragging a
    // handle nobody selected is how a group edit silently becomes a single edit.
    if (!selected.includes(index)) onSelected([index])
  }

  const end = useCallback(() => {
    if (!drag.current) return
    drag.current = null
    setMoving(false)
  }, [])

  // `pointermove` on the window, not on the block: the pointer leaves a 22 px lane
  // within a frame, and a handler that only fires while it is inside would drop the
  // drag exactly when someone drags far enough to matter. The listeners live in an
  // effect keyed on `moving`, so a release outside the popup — or a second popup
  // opened over it — still ends the drag cleanly.
  useEffect(() => {
    if (!moving) return
    const onMove = (event: PointerEvent) => {
      const held = drag.current
      if (!held) return
      const to = event.clientX
      if (held.kind === 'both') {
        const next = moveWindow(held.rect, span, held.x0, to, held.at, held.until)
        onChange(held.index, 'at', String(next.at))
        onChange(held.index, 'until', String(next.until))
        if (held.group.length) {
          // Same shift, own width, own clamp — see `groupShift`.
          const moved = groupShift(held.rect, span, held.x0, to, { at: held.at, until: held.until }, held.group)
          held.group.forEach((g, k) => {
            onChange(g.index, 'at', String(moved[k].at))
            onChange(g.index, 'until', String(moved[k].until))
          })
        }
        return
      }
      // One edge at a time: the other end is left exactly where it is, which is
      // what makes the far edge a *start* or a *stop* and not a duration.
      const value = dragEdge(held.rect, span, held.x0, to, held.at, held.until, held.kind)
      onChange(held.index, held.kind === 'start' ? 'at' : 'until', String(value))
    }
    const off = () => end()
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', off)
    window.addEventListener('pointercancel', off)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', off)
      window.removeEventListener('pointercancel', off)
    }
  }, [moving, span, onChange, end])

  /** Shift/Ctrl-click on the track itself: pin one end at the pointer, leaving the
   *  other to the schedule or to whatever is already written. */
  const pinFromTrack = (index: number, which: 'at' | 'until') => (event: React.PointerEvent) => {
    if (!(event.shiftKey || event.ctrlKey || event.metaKey)) return false
    const win = photoWindow(spec, index)
    const rect = trackOf(event.currentTarget)
    if (which === 'at') {
      onChange(index, 'at', String(pinStart(rect, span, event.clientX, win.to)))
    } else {
      onChange(index, 'until', String(pinStop(rect, span, event.clientX, win.from)))
    }
    if (!selected.includes(index)) onSelected([index])
    event.preventDefault()
    return true
  }

  if (!photos.length) return null
  const ticks: number[] = []
  const step = span > 240 ? 30 : span > 120 ? 15 : span > 45 ? 5 : span > 15 ? 2 : 1
  for (let t = 0; t <= span + 0.001; t += step) ticks.push(Math.round(t * 100) / 100)

  return (
    <div className={`collage-window${moving ? ' moving' : ''}`} ref={el => { lanes.current = el }}>
      <div className="collage-window-ruler">
        {/* The lane is the only place a photo's whole window is set, so it is also
            the natural place to say *which* photos you are setting: the box on the
            left of each number is the selection, and the number itself picks just
            that one. Everything the popup edits — size, tilt, shape, frame,
            entrance, motion, exit, and a body drag along the ruler — goes to every
            checked row at once. */}
        <span className="collage-window-key collage-window-all">
          <input type="checkbox" checked={selected.length >= photos.length}
                 ref={el => { if (el) el.indeterminate = selected.length > 0 && selected.length < photos.length }}
                 aria-label="Select every photo"
                 title={selected.length >= photos.length ? 'Take the selection off all the photos' : `Select all ${photos.length} photos`}
                 onChange={() => onSelected(selected.length >= photos.length ? [] : photos.map((_, i) => i))}/>
          <em>photo</em>
        </span>
        <div className="collage-window-track ruler">{ticks.map(t => <b key={t} style={{ left: percent(t) }}><s>{Math.round(t)}</s></b>)}</div>
        <span className="collage-window-values">window</span>
      </div>
      {photos.map((photo, i) => {
        const win = photoWindow(spec, i)
        const at = photoAt(spec, i)
        const until = photoUntil(spec, i)
        const authored = at !== undefined || until !== undefined
        const ent = entranceLength(spec, i)
        const on = selected.includes(i)
        const lead = selected[0] === i && selected.length > 1
        return (
          <div key={`${photo.path}-${i}`} className={`collage-window-row${on ? ' active' : ''}${lead ? ' lead' : ''}${authored ? ' pinned' : ''}`}
               onDoubleClick={() => clearWindows(i)}
               title={`Photo ${i + 1} — ${authored ? 'its own window; double-click to hand it back to the schedule' : 'the stagger schedule owns this one; drag the block or its edges to pin a window'}`}>
            <span className="collage-window-key">
              <input type="checkbox" checked={on} onChange={check(i)} onClick={e => e.stopPropagation()}
                     aria-label={`Select photo ${i + 1} to edit it with the others`}
                     title={on ? `Photo ${i + 1} is in the selection (${selected.length} photo${selected.length > 1 ? 's' : ''}) — uncheck to leave it out` : 'Check to edit this photo together with the others'} />
              <i onClick={click(i)} title="Click to select only this photo · Ctrl/⌘ adds it to the selection · Shift extends from the last one">{i + 1}</i>
              <span className="collage-window-name">{photo.name || photo.path.split('/').pop() || 'photo'}</span>
            </span>
            <div className="collage-window-track"
                 onPointerDown={event => {
                   // The lane's own empty ground: Shift-click pins the arrival there,
                   // Ctrl-click the stop — the same mouse map the caption strip uses.
                   if (!pinFromTrack(i, event.shiftKey ? 'at' : 'until')(event)) begin(i, 'both')(event)
                 }}>
              {/* What the schedule would do anyway, drawn faintly, so a pinned photo
                  still shows what the photo is *not* doing. */}
              {authored ? <i className="collage-window-ghost" style={{ left: percent(win.from), width: `${Math.max(1, ((win.to - win.from) / span) * 100)}%` }}/> : null}
              <div className="collage-window-block" style={{ left: percent(win.from), width: `${Math.max(0.6, ((win.to - win.from) / span) * 100)}%` }}>
                {/* The entrance is part of the window, not extra: hatched so a short
                    block reads as "arriving", the way the caption strip shows a word. */}
                {ent > 0.001 ? <i className="collage-window-arrive" style={{ width: `${Math.min(100, (ent / Math.max(0.001, win.to - win.from)) * 100)}%` }} title={`Arriving for ${ent.toFixed(2)} s of it`}/> : null}
                <button type="button" className="collage-window-edge in" aria-label={`Start of photo ${i + 1}`} onPointerDown={begin(i, 'start')}
                        title={`Drag: when this photo appears · now ${win.from.toFixed(2)}s`}/>
                <button type="button" className="collage-window-edge out" aria-label={`End of photo ${i + 1}`} onPointerDown={begin(i, 'end')}
                        title={`Drag: when this photo is gone · now ${win.to.toFixed(2)}s · ${exitLabel(spec, i)}`}/>
              </div>
            </div>
            <span className="collage-window-values">
              <em title={authored ? `Pinned: appears at ${win.from.toFixed(2)}s, gone at ${win.to.toFixed(2)}s` : `The schedule's own: lands ${win.from.toFixed(2)}s, ${win.to.toFixed(2)}s total · ${photoDelay(spec, i).toFixed(2)}s wait`}>
                {win.from.toFixed(2)}→{win.to.toFixed(2)}
              </em>
              {authored ? <button type="button" className="collage-window-auto" title={selected.length > 1 && on ? 'Give every selected photo back to the stagger schedule' : 'Give this photo back to the stagger schedule'}
                                  onPointerDown={event => event.stopPropagation()} onClick={() => clearWindows(i)}>auto</button> : null}
            </span>
          </div>
        )
      })}
    </div>
  )
}
