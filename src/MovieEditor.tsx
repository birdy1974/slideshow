// Popup editor for one movie in the storyline: drag IN/OUT handles to select
// the section of the recording that actually goes into the slideshow.
//
// It mirrors the soundtrack editor (same strip, same handles, same time
// fields) but without fades, and it drives the clip's place on the timeline:
// the kept section becomes the movie's length, so trimming a 3-minute file
// down to 20 seconds really does shorten the slideshow by 2:40.
import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Info, Pause, Play, Scissors, X } from 'lucide-react'
import { TimeField } from './ui'
import { formatClockPrecise } from './time'
import type { MediaItem } from './mediaItem'

// Shortest section a movie can be cut down to.
const MIN_KEEP = 0.5
const round = (v: number) => Math.round(v * 10) / 10

export function MovieEditor({ item, src, onChange, onClose }: {
  item: MediaItem
  src: string
  onChange: (patch: Partial<MediaItem>) => void
  onClose: () => void
}) {
  const [fileSeconds, setFileSeconds] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [drag, setDrag] = useState<null | 'in' | 'out' | 'seek'>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const stripRef = useRef<HTMLDivElement | null>(null)
  const original = useRef({ trimStart: item.trimStart, trimEnd: item.trimEnd, duration: item.duration })

  const total = fileSeconds
  const start = Math.max(0, Math.min(Number(item.trimStart) || 0, total))
  const end = Math.min(total, (Number(item.trimEnd) || 0) > 0 ? Number(item.trimEnd) : total)
  const kept = Math.max(0, end - start)

  // Read the real file length from the browser's decoder. The row's stored
  // duration is only a floor — movies always played to the end — so it cannot
  // be used to bound the handles.
  useEffect(() => {
    setFileSeconds(0); setPosition(0); setPlaying(false)
  }, [src])

  // Keep playback inside the kept section and stop at OUT.
  useEffect(() => {
    const video = videoRef.current
    if (!video || !total) return
    if (playing && position >= end - 0.02) { video.pause(); setPlaying(false); video.currentTime = start; setPosition(start) }
  }, [position, start, end, playing, total])

  // Space = play/pause, Escape = close (and discard, like the soundtracks).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); cancel() }
      else if (e.key === ' ' && (e.target as HTMLElement)?.tagName !== 'INPUT') { e.preventDefault(); togglePlay() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const applyTrim = (nextStart: number, nextEnd: number) => {
    const patch: Partial<MediaItem> = { trimStart: round(nextStart), trimEnd: round(nextEnd) }
    // The clip's place on the timeline follows the kept section. A manually
    // longer hold (which freezes the last frame) is pulled down with it,
    // otherwise trimming would appear to do nothing.
    if (Number.isFinite(item.duration) && item.duration > nextEnd - nextStart) patch.duration = round(nextEnd - nextStart)
    onChange(patch)
  }
  const setIn = (v: number) => applyTrim(Math.min(Math.max(0, v), end - MIN_KEEP), end)
  const setOut = (v: number) => applyTrim(start, Math.max(Math.min(total, v), start + MIN_KEEP))

  const togglePlay = () => {
    const video = videoRef.current; if (!video) return
    if (playing) { video.pause(); setPlaying(false); return }
    if (position < start || position >= end - 0.05) { video.currentTime = start; setPosition(start) }
    video.play().then(() => setPlaying(true)).catch(() => setPlaying(false))
  }
  const seekTo = (seconds: number) => {
    const video = videoRef.current
    const v = Math.min(Math.max(seconds, start), Math.max(start, end - 0.05))
    if (video) video.currentTime = v
    setPosition(v)
  }
  const secondsFromEvent = (event: React.PointerEvent | PointerEvent) => {
    const rect = stripRef.current?.getBoundingClientRect(); if (!rect || !total) return 0
    return Math.min(total, Math.max(0, (event.clientX - rect.left) / rect.width * total))
  }
  const onStripDown = (kind: NonNullable<typeof drag>) => (e: React.PointerEvent) => {
    e.preventDefault(); e.stopPropagation()
    // Optional: pointer capture keeps the drag alive over the strip's children.
    ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
    setDrag(kind)
    applyDrag(kind, secondsFromEvent(e))
  }
  const applyDrag = (kind: NonNullable<typeof drag>, seconds: number) => {
    if (kind === 'in') setIn(seconds)
    else if (kind === 'out') setOut(seconds)
    else seekTo(seconds)
  }
  useEffect(() => {
    if (!drag) return
    const move = (e: PointerEvent) => applyDrag(drag, secondsFromEvent(e))
    const up = () => setDrag(null)
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up)
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up) }
  })

  const pct = (v: number) => (total > 0 ? `${Math.min(100, Math.max(0, v / total * 100))}%` : '0%')
  const frames = 48
  const cancel = () => { onChange(original.current); onClose() }
  const reset = () => onChange({ trimStart: 0, trimEnd: 0, duration: round(total) })

  return <div className="modal-backdrop dark-backdrop movie-backdrop" onMouseDown={cancel}>
    <div className="soundtrack-editor movie-editor" onMouseDown={e => e.stopPropagation()}>
      <div className="preview-top"><div><strong>{item.name}</strong><span>MOVIE EDITOR · SELECT THE SECTION TO USE</span></div><button type="button" onClick={cancel} aria-label="Close editor"><X size={20} /></button></div>

      <div className="editor-body">
        <div className="movie-stage">
          <video
            ref={videoRef}
            src={src}
            playsInline
            preload="metadata"
            onLoadedMetadata={e => { const d = (e.currentTarget as HTMLVideoElement).duration; if (Number.isFinite(d) && d > 0) setFileSeconds(d) }}
            onDurationChange={e => { const d = (e.currentTarget as HTMLVideoElement).duration; if (Number.isFinite(d) && d > 0) setFileSeconds(d) }}
            onTimeUpdate={e => setPosition((e.currentTarget as HTMLVideoElement).currentTime)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
          />
          {!total && <div className="movie-stage-note">Reading the movie…</div>}
        </div>

        <div className="editor-strip-wrap">
          <div className="ruler"><span>0:00</span><span>{formatClockPrecise(total / 4)}</span><span>{formatClockPrecise(total / 2)}</span><span>{formatClockPrecise(total * 3 / 4)}</span><span>{formatClockPrecise(total)}</span></div>
          <div ref={stripRef} className={`editor-strip ${drag ? 'dragging' : ''}`} onPointerDown={onStripDown('seek')}>
            <div className="filmstrip">{Array.from({ length: frames }).map((_, i) => {
              const at = (i + 0.5) / frames * total
              const inKeep = at >= start && at <= end
              return <i key={i} style={{ background: `hsl(${28 + ((i * 37) % 40)} ${inKeep ? 34 : 6}% ${inKeep ? 42 : 16}%)` }} />
            })}</div>
            <div className="cut-shade left" style={{ width: pct(start) }} />
            <div className="cut-shade right" style={{ left: pct(end) }} />
            <div className="playhead" style={{ left: pct(position) }} />
            <button type="button" className="trim-handle in" style={{ left: pct(start) }} title={`IN · ${formatClockPrecise(start)} · drag to set where the movie starts`} onPointerDown={onStripDown('in')}><ChevronRight size={12} /></button>
            <button type="button" className="trim-handle out" style={{ left: pct(end) }} title={`OUT · ${formatClockPrecise(end)} · drag to set where the movie ends`} onPointerDown={onStripDown('out')}><ChevronLeft size={12} /></button>
          </div>
          <div className="strip-legend"><span><i className="swatch keep" /> kept · {formatClockPrecise(kept)}</span><span><i className="swatch cut" /> cut · {formatClockPrecise(Math.max(0, total - kept))}</span></div>
        </div>

        <div className="editor-controls">
          <div className="transport">
            <button type="button" className={`btn ${playing ? 'dark' : 'soft'}`} onClick={togglePlay} disabled={!total}>{playing ? <Pause size={15} /> : <Play size={15} />} {playing ? 'Pause' : 'Play selection'}</button>
            <button type="button" className="btn ghost" onClick={() => seekTo(start)} title="Jump to IN"><ChevronLeft size={14} /> IN</button>
            <button type="button" className="btn ghost" onClick={() => seekTo(Math.max(start, end - 5))} title="Jump to 5 s before OUT">OUT <ChevronRight size={14} /></button>
            <em className="transport-clock">{formatClockPrecise(Math.max(0, position - start))} / {formatClockPrecise(kept)}</em>
          </div>
          <div className="editor-fields">
            <TimeField label="Start (IN)" value={start} min={0} max={Math.max(0, end - MIN_KEEP)} onCommit={setIn} />
            <TimeField label="End (OUT)" value={end} min={start + MIN_KEEP} max={total} onCommit={setOut} />
            <div className="time-field static"><span>Kept length</span><b>{formatClockPrecise(kept)}</b></div>
            <div className="time-field static"><span>File length</span><b>{formatClockPrecise(total)}</b></div>
          </div>
          <p className="editor-note"><Info size={13} /> Drag the green handles to choose where the movie starts and ends. Only the selected section is rendered, and the clip on the timeline becomes exactly that long.</p>
        </div>
      </div>

      <div className="modal-foot">
        <span><Scissors size={12} /> Keeping {formatClockPrecise(kept)} of {formatClockPrecise(total)}</span>
        <button className="btn ghost" onClick={reset} title="Use the whole movie again">Use whole movie</button>
        <button className="btn ghost" onClick={cancel}>Cancel</button>
        <button className="btn dark" onClick={onClose}>Done</button>
      </div>
    </div>
  </div>
}

/** Inclusive/exclusive: has the movie been cut down from its full length? */
export function movieIsTrimmed(item: MediaItem) {
  return (Number(item.trimStart) || 0) > 0.01 || (Number(item.trimEnd) || 0) > 0.01
}

export function movieKeptLabel(item: MediaItem) {
  const s = Number(item.trimStart) || 0
  const e = Number(item.trimEnd) || 0
  return `${formatClockPrecise(s)}–${e > 0 ? formatClockPrecise(e) : 'end'}`
}
