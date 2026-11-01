// One line per photo: when it appears, when it goes.
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
import { entranceLength, exitFlyLength, exitStart, photoAt, photoDelay, photoStart, photoUntil } from './collageCore'

/** The render is 25 fps, so a twentieth-of-a-second grid is the finest unit that
 *  still means something: every snapped value is a whole frame of the MP4. */
const snap = (v: number) => Math.round(v * 25) / 25
/** The narrowest window worth writing — below it the entrance has no room and the
 *  engine would refuse the stop anyway (see `exit_start` in collage.py). */
const MIN_WINDOW = 0.12

export interface CollageTimingLanesProps {
  spec: CollageSpec
  /** Seconds the ruler spans: the slide's own duration when that is longer than
   *  what the timings imply, so a stop written past the end is still on screen. */
  duration: number
  selected: number | null
  onSelect: (index: number) => void
  /** '' clears the field, which is the same thing as "the schedule owns it". */
  onChange: (index: number, lane: 'at' | 'until', seconds: string) => void
}

interface Drag {
  index: number
  /** Which edge, or the body (both edges together). */
  kind: 'start' | 'end' | 'both'
  /** Pointer position and both times when the drag began, so the maths is always
   *  measured from a snapshot and cannot drift across frames. */
  x0: number
  at: number
  until: number
}

/** The photo's visible window as the engines see it, authored or not. */
export function photoWindow (spec: CollageSpec, i: number): { from: number; to: number } {
  const from = photoStart(spec, i, 0)
  const to = exitStart(spec, i, 0) + exitFlyLength(spec, i)
  return { from, to: Math.max(to, from + MIN_WINDOW) }
}

export function CollageTimingLanes({ spec, duration, selected, onSelect, onChange }: CollageTimingLanesProps) {
  const photos = spec.photos ?? []
  const span = Math.max(0.5, Number(duration) || 0.5)
  const lanes = useRef<HTMLDivElement | null>(null)
  const drag = useRef<Drag | null>(null)
  const [moving, setMoving] = useState(false)

  const timeAt = useCallback((clientX: number) => {
    const rect = lanes.current?.getBoundingClientRect()
    if (!rect || rect.width < 8) return 0
    return Math.max(0, Math.min(span, ((clientX - rect.left) / rect.width) * span))
  }, [span])
  const percent = useCallback((t: number) => `${Math.max(0, Math.min(100, (t / span) * 100))}%`, [span])

  const begin = (index: number, kind: Drag['kind']) => (event: React.PointerEvent) => {
    event.stopPropagation()
    event.preventDefault()
    const win = photoWindow(spec, index)
    drag.current = {
      index, kind, x0: event.clientX,
      at: photoAt(spec, index) ?? win.from,
      until: photoUntil(spec, index) ?? win.to,
    }
    setMoving(true)
    onSelect(index)
    ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  }
  const move = (event: React.PointerEvent) => {
    const held = drag.current
    if (!held) return
    const rect = lanes.current?.getBoundingClientRect()
    if (!rect || rect.width < 8) return
    const delta = ((event.clientX - held.x0) / rect.width) * span
    if (held.kind === 'both') {
      const width = held.until - held.at
      const from = snap(Math.max(0, Math.min(span - width, held.at + delta)))
      onChange(held.index, 'at', String(from))
      onChange(held.index, 'until', String(from + width))
      return
    }
    const at = snap(timeAt(event.clientX))
    if (held.kind === 'start') onChange(held.index, 'at', String(Math.max(0, Math.min(at, held.until - MIN_WINDOW))))
    else onChange(held.index, 'until', String(Math.min(span, Math.max(at, held.at + MIN_WINDOW))))
  }
  const end = useCallback(() => {
    if (!drag.current) return
    drag.current = null
    setMoving(false)
  }, [])
  // A pointerup on a handle that was captured lands on the handle, not the lane,
  // so the drag is also ended on the window: otherwise a fast release outside the
  // popup leaves the block welded to the cursor.
  useEffect(() => {
    if (!moving) return
    const off = () => end()
    window.addEventListener('pointerup', off)
    window.addEventListener('pointercancel', off)
    return () => {
      window.removeEventListener('pointerup', off)
      window.removeEventListener('pointercancel', off)
    }
  }, [moving, end])

  if (!photos.length) return null
  const ticks: number[] = []
  const step = span > 240 ? 30 : span > 120 ? 15 : span > 45 ? 5 : span > 15 ? 2 : 1
  for (let t = 0; t <= span + 0.001; t += step) ticks.push(Math.round(t * 100) / 100)

  return (
    <div className="collage-window" ref={el => { lanes.current = el }}>
      <div className="collage-window-ruler">
        <span className="collage-window-key">photo</span>
        <div className="collage-window-track ruler">{ticks.map(t => <b key={t} style={{ left: percent(t) }}><s>{Math.round(t)}</s></b>)}</div>
        <span className="collage-window-values">window</span>
      </div>
      {photos.map((photo, i) => {
        const win = photoWindow(spec, i)
        const at = photoAt(spec, i)
        const until = photoUntil(spec, i)
        const authored = at !== undefined || until !== undefined
        const ent = entranceLength(spec, i)
        return (
          <div key={`${photo.path}-${i}`} className={`collage-window-row${selected === i ? ' active' : ''}${authored ? ' pinned' : ''}`}
               onDoubleClick={() => { onChange(i, 'at', ''); onChange(i, 'until', '') }}
               title={`Photo ${i + 1} — ${authored ? 'its own window; double-click to hand it back to the schedule' : 'the stagger schedule owns this one; drag the block or its edges to pin a window'}`}>
            <span className="collage-window-key">
              <i>{i + 1}</i>
              <span className="collage-window-name">{photo.name || photo.path.split('/').pop() || 'photo'}</span>
            </span>
            <div className="collage-window-track"
                 onPointerDown={event => {
                   // The lane's own empty ground: Shift-click pins the arrival there,
                   // Ctrl-click the stop — the same mouse map the caption strip uses.
                   const win2 = photoWindow(spec, i)
                   const at2 = at ?? win2.from
                   const until2 = until ?? win2.to
                   const t = snap(timeAt(event.clientX))
                   if (event.shiftKey) onChange(i, 'at', String(Math.max(0, Math.min(t, until2 - MIN_WINDOW))))
                   else if (event.ctrlKey || event.metaKey) onChange(i, 'until', String(Math.min(span, Math.max(t, at2 + MIN_WINDOW))))
                   else begin(i, 'both')(event)
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
                        title={`Drag: when this photo is gone · now ${win.to.toFixed(2)}s`}/>
              </div>
            </div>
            <span className="collage-window-values">
              <em title={authored ? `Pinned: appears at ${win.from.toFixed(2)}s, gone at ${win.to.toFixed(2)}s` : `The schedule's own: lands ${win.from.toFixed(2)}s, ${win.to.toFixed(2)}s total · ${photoDelay(spec, i).toFixed(2)}s wait`}>
                {win.from.toFixed(2)}→{win.to.toFixed(2)}
              </em>
              {authored ? <button type="button" className="collage-window-auto" title="Give this photo back to the stagger schedule"
                                  onPointerDown={event => event.stopPropagation()} onClick={() => { onChange(i, 'at', ''); onChange(i, 'until', '') }}>auto</button> : null}
            </span>
          </div>
        )
      })}
    </div>
  )
}
