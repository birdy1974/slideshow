// "Cut & crop" — the second tab of the picture editor popup.
//
// The stage shows the whole picture (turned the way the lightbox shows it, then
// straightened and zoomed exactly as far as the render zooms), with the crop
// frame on top. Everything is fractions of that view, so what is dragged here is
// what backend/app/picture_crop.py cuts out of the original file at full
// resolution — nothing is ever written to the picture itself.
//
// Four tools, all optional and combinable:
//   Crop frame   — drag the rectangle, with aspect presets
//   Straighten   — level a horizon; the picture zooms so no corner shows
//   Cut out      — click a polygon around something to remove it; the hole is
//                  filled with a blurred copy of the same picture
//   Black bars   — let FFmpeg's cropdetect propose the rectangle
import { useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent, ReactNode, SyntheticEvent, VideoHTMLAttributes } from 'react'
import { Crop as CropIcon, Eraser, Info, RotateCcw, ScanLine, SlidersHorizontal, Trash2, Undo2 } from 'lucide-react'
import type { MediaItem } from './mediaItem'
import {
  CROP_ASPECTS, DEFAULT_FEATHER, FULL_CROP, MAX_LASSO_POINTS, MAX_STRAIGHTEN, MIN_CROP,
  clampRect, cropSpriteStyle, inscribedZoom, lassoBoxPolygon, normalizeCrop, normalizeLasso,
  normalizeRect, normalizeRotation, rectForAspect, rotatedSize, cropSummary,
  type Cropish, type CropRect, type Intrinsic, type LassoPoint, type PictureCrop,
} from './pictureCrop'
import { useCroppedSource } from './usePictureCrop'

type Tool = 'frame' | 'straighten' | 'cutout' | 'bars'
type Drag =
  | { mode: 'move' | 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w'; from: { x: number; y: number }; rect: CropRect }
  | { mode: 'point'; index: number; points: LassoPoint[] }
  | null

const round3 = (value: number) => Math.round(value * 1000) / 1000
const FRAME_ASPECT = 16 / 9

/** The crop frame's corner/edge handles. */
const HANDLES: { id: string; className: string; title: string }[] = [
  { id: 'nw', className: 'h-nw', title: 'Drag to resize from the top-left' },
  { id: 'n', className: 'h-n', title: 'Drag the top edge' },
  { id: 'ne', className: 'h-ne', title: 'Drag to resize from the top-right' },
  { id: 'e', className: 'h-e', title: 'Drag the right edge' },
  { id: 'se', className: 'h-se', title: 'Drag to resize from the bottom-right' },
  { id: 's', className: 'h-s', title: 'Drag the bottom edge' },
  { id: 'sw', className: 'h-sw', title: 'Drag to resize from the bottom-left' },
  { id: 'w', className: 'h-w', title: 'Drag the left edge' },
] as const

/**
 * A playing movie wearing its crop.
 *
 * A movie cannot be repainted through a canvas copy (that would replace the
 * recording with one still), so the crop is done in CSS: the window box gets the
 * crop's aspect — measured against its parent, because a non-replaced element
 * cannot contain-fit itself — and inside it the video is oversized and offset so
 * the crop rectangle lands exactly on the window. Straightening rides along as a
 * rotate() on the same element. The cut-out polygon is *not* drawn here; it
 * shows in the thumbnail, in the editor's result preview and in the render.
 */
export function CropSpriteVideo({ item, style, windowClassName, ...video }: {
  item?: Cropish | null
  /** Class for the window box. A Ken Burns animation has to go here, not on the
    * video: its transform would overwrite the sprite's own rotate(). */
  windowClassName?: string
} & VideoHTMLAttributes<HTMLVideoElement>) {
  const host = useRef<HTMLSpanElement | null>(null)
  const [intrinsic, setIntrinsic] = useState<Intrinsic | null>(null)
  const [box, setBox] = useState({ width: 0, height: 0 })
  const cut = normalizeCrop(item)
  const sprite = cut && intrinsic ? cropSpriteStyle(cut, intrinsic) : null
  const aspect = sprite?.aspect || 0

  useEffect(() => {
    const parent = host.current?.parentElement
    if (!parent || !aspect) { setBox({ width: 0, height: 0 }); return }
    const fit = () => {
      const area = parent.getBoundingClientRect()
      const width = Math.max(1, Math.min(area.width, area.height * aspect))
      setBox({ width, height: width / aspect })
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(parent)
    return () => observer.disconnect()
  }, [aspect])

  const measure = (event: SyntheticEvent<HTMLVideoElement>) => {
    const element = event.currentTarget
    if (element.videoWidth && element.videoHeight) setIntrinsic({ width: element.videoWidth, height: element.videoHeight })
    video.onLoadedMetadata?.(event)
  }
  const ready = !!sprite && box.width > 0
  const element = <video {...video} style={ready ? { ...sprite!.media, ...style } : style} onLoadedMetadata={measure} />
  if (!cut) return element
  // The wrapper has to exist before it can be measured, so until then it stays
  // layout-transparent (display:contents) and the video behaves as it did before.
  return <span ref={host} className={`crop-window${windowClassName ? ` ${windowClassName}` : ''}`}
    style={ready ? { width: box.width, height: box.height } : { display: 'contents' }}>{element}</span>
}

export function PictureCropPanel({ item, src, onChange, onCancel, onClose, onReset, detectBars }: {
  item: MediaItem
  src: string
  onChange: (patch: Partial<MediaItem>) => void
  onCancel: () => void
  onClose: () => void
  onReset: () => void
  /** Asks the backend to measure the black bars (undefined when unavailable). */
  detectBars?: (item: MediaItem) => Promise<{ rect: CropRect; bars: boolean } | null>
}) {
  const isVideo = item.type === 'video'
  const turn = normalizeRotation(item.rotation)
  const [intrinsic, setIntrinsic] = useState<Intrinsic | null>(null)
  const [tool, setTool] = useState<Tool>(item.crop?.lasso ? 'cutout' : item.crop?.degrees ? 'straighten' : 'frame')
  const [draft, setDraft] = useState<PictureCrop>(() => ({ rect: item.crop?.rect ?? null, degrees: item.crop?.degrees ?? 0, lasso: item.crop?.lasso ?? null, feather: item.crop?.feather ?? DEFAULT_FEATHER }))
  const [aspect, setAspect] = useState('free')
  const [bars, setBars] = useState<{ state: 'idle' | 'loading' | 'proposed' | 'error'; text: string; rect?: CropRect }>({ state: 'idle', text: '' })
  // One-line notice for the cut-out tool (polygon full, points added, …).
  const [notice, setNotice] = useState('')
  // The result preview is rebuilt from a canvas copy, so it trails the drag by
  // a beat instead of repainting on every pointer move.
  const [settled, setSettled] = useState<PictureCrop>(draft)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)
  const drag = useRef<Drag>(null)
  // Pointer-up has to publish whatever the drag ended on; a ref avoids calling
  // setState from inside a state updater.
  const latest = useRef(draft)
  latest.current = draft
  const [box, setBox] = useState({ width: 0, height: 0 })

  const rotated = intrinsic ? rotatedSize(intrinsic, turn) : null
  const mediaAspect = rotated && rotated.height ? rotated.width / rotated.height : 0
  const degrees = Number(draft.degrees) || 0
  const rect = normalizeRect(draft.rect) ?? { ...FULL_CROP }
  const lasso = normalizeLasso(draft.lasso)
  const zoom = inscribedZoom(mediaAspect || 1, degrees)
  const edited = !!normalizeRect(draft.rect) || Math.abs(degrees) >= 0.05 || !!lasso

  // Keep the stage box exactly the shape of the (turned) picture, so every
  // percentage in the overlay is a fraction of the picture.
  useEffect(() => {
    const stage = stageRef.current
    if (!stage || !mediaAspect) return
    const fit = () => {
      const area = stage.getBoundingClientRect()
      const width = Math.max(1, Math.min(area.width, area.height * mediaAspect))
      setBox({ width, height: width / mediaAspect })
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(stage)
    return () => observer.disconnect()
  }, [mediaAspect])

  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(draft), 220)
    return () => window.clearTimeout(timer)
  }, [draft])

  // The result preview: one small cropped copy, letterboxed over a blurred
  // copy of itself — exactly what fit_frame_filter does in the render.
  const result = useCroppedSource(src, { crop: settled, rotation: item.rotation }, 'result', isVideo)

  /** Store the draft on the item; a full frame with nothing else means "no crop". */
  const publish = (next: PictureCrop) => {
    setDraft(next)
    const keptRect = normalizeRect(next.rect)
    const keptDegrees = Math.abs(Number(next.degrees) || 0) < 0.05 ? 0 : Math.max(-MAX_STRAIGHTEN, Math.min(MAX_STRAIGHTEN, Number(next.degrees)))
    const keptLasso = normalizeLasso(next.lasso)
    if (!keptRect && !keptDegrees && !keptLasso) { onChange({ crop: undefined }); return }
    const crop: PictureCrop = { feather: round3(Number(next.feather ?? DEFAULT_FEATHER)) }
    if (keptRect) crop.rect = { x: round3(keptRect.x), y: round3(keptRect.y), w: round3(keptRect.w), h: round3(keptRect.h) }
    if (keptDegrees) crop.degrees = round3(keptDegrees)
    if (keptLasso) crop.lasso = keptLasso.map(([x, y]) => [round3(x), round3(y)])
    onChange({ crop })
  }

  const point = (event: { clientX: number; clientY: number }) => {
    const area = boxRef.current?.getBoundingClientRect()
    if (!area || !area.width || !area.height) return { x: 0, y: 0 }
    return { x: (event.clientX - area.left) / area.width, y: (event.clientY - area.top) / area.height }
  }

  /** The pixel aspect an aspect preset asks for, or null for "free". */
  const ratioOf = (id: string): number | null => {
    const preset = CROP_ASPECTS.find(entry => entry.id === id)
    if (!preset || preset.ratio === null || !rotated || !rotated.height) return null
    return preset.ratio === 0 ? rotated.width / rotated.height : preset.ratio
  }

  /** Resize from one handle, honouring the aspect lock and the minimum size. */
  const resize = (mode: string, from: { x: number; y: number }, to: { x: number; y: number }, start: CropRect): CropRect => {
    const dx = to.x - from.x
    const dy = to.y - from.y
    let next = { ...start }
    if (mode.includes('w')) { next.x = start.x + dx; next.w = start.w - dx }
    if (mode.includes('e')) { next.w = start.w + dx }
    if (mode.includes('n')) { next.y = start.y + dy; next.h = start.h - dy }
    if (mode.includes('s')) { next.h = start.h + dy }
    // Never drag past the opposite edge.
    if (next.w < MIN_CROP) { if (mode.includes('w')) next.x = start.x + start.w - MIN_CROP; next.w = MIN_CROP }
    if (next.h < MIN_CROP) { if (mode.includes('n')) next.y = start.y + start.h - MIN_CROP; next.h = MIN_CROP }
    next = clampRect(next)
    const ratio = ratioOf(aspect)
    if (ratio !== null && mode.length === 2) {
      // Corner drag with an aspect lock: grow from the opposite corner.
      const anchorX = mode.includes('w') ? next.x + next.w : next.x
      const anchorY = mode.includes('n') ? next.y + next.h : next.y
      const locked = rectForAspect(next, ratio, rotated!)
      next = clampRect({
        w: locked.w, h: locked.h,
        x: mode.includes('w') ? anchorX - locked.w : anchorX,
        y: mode.includes('n') ? anchorY - locked.h : anchorY,
      })
    }
    return next
  }

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const current = drag.current
      if (!current) return
      const to = point(event)
      if (current.mode === 'point') {
        const points = current.points.map((entry, index) => index === current.index
          ? [Math.min(1, Math.max(0, to.x)), Math.min(1, Math.max(0, to.y))] as LassoPoint
          : entry)
        setDraft(draft => ({ ...draft, lasso: points }))
        return
      }
      if (current.mode === 'move') {
        setDraft(draft => ({ ...draft, rect: clampRect({ ...current.rect, x: current.rect.x + (to.x - current.from.x), y: current.rect.y + (to.y - current.from.y) }) }))
        return
      }
      setDraft(draft => ({ ...draft, rect: resize(current.mode, current.from, to, current.rect) }))
    }
    const up = () => {
      if (!drag.current) return
      drag.current = null
      publish(latest.current)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
    }
  })  // eslint-disable-line react-hooks/exhaustive-deps

  // Backspace removes the last polygon point while the cut-out tool is active.
  useEffect(() => {
    if (tool !== 'cutout') return
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
      if (event.key !== 'Backspace' && event.key !== 'Delete') return
      event.preventDefault()
      const next = (latest.current.lasso ?? []).slice(0, -1)
      publish({ ...latest.current, lasso: next.length >= 3 ? next : null })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })  // eslint-disable-line react-hooks/exhaustive-deps

  const startDrag = (mode: string) => (event: ReactPointerEvent) => {
    event.preventDefault()
    event.stopPropagation()
    drag.current = { mode, from: point(event), rect } as Drag
  }

  const startPointDrag = (index: number) => (event: ReactPointerEvent) => {
    event.preventDefault()
    event.stopPropagation()
    drag.current = { mode: 'point', index, points: (draft.lasso ?? []) as LassoPoint[] }
  }

  const onStageDown = (event: ReactPointerEvent) => {
    if (tool === 'cutout') {
      const soFar = (draft.lasso ?? []) as LassoPoint[]
      if (soFar.length >= MAX_LASSO_POINTS) { setNotice(`A polygon holds ${MAX_LASSO_POINTS} points — undo one before adding another.`); return }
      const at = point(event)
      const points = [...soFar, [round3(Math.min(1, Math.max(0, at.x))), round3(Math.min(1, Math.max(0, at.y)))] as LassoPoint]
      setNotice(points.length < 3 ? `${points.length} of 3 points — keep going to close the shape` : '')
      publish({ ...draft, lasso: points.length >= 3 ? points : null })
      return
    }
    // Clicking outside the frame recentres it on the click.
    const at = point(event)
    if (at.x < rect.x || at.x > rect.x + rect.w || at.y < rect.y || at.y > rect.y + rect.h) {
      publish({ ...draft, rect: clampRect({ ...rect, x: at.x - rect.w / 2, y: at.y - rect.h / 2 }) })
    }
  }

  const detect = async () => {
    if (!detectBars) return
    setBars({ state: 'loading', text: 'Measuring the file…' })
    try {
      const found = await detectBars(item)
      if (!found) { setBars({ state: 'error', text: 'FFmpeg found nothing to measure in this file.' }); return }
      setBars({
        state: 'proposed',
        rect: found.rect,
        text: found.bars
          ? `Bars found — the picture keeps ${Math.round(found.rect.w * 100)} × ${Math.round(found.rect.h * 100)} %.`
          : 'No bars: FFmpeg would keep the whole picture.',
      })
    } catch (error) {
      setBars({ state: 'error', text: error instanceof Error ? error.message : 'Could not measure this file.' })
    }
  }

  const mediaStyle = useMemo(() => {
    if (!box.width) return undefined
    const turned = turn === 90 || turn === 270
    return {
      position: 'absolute' as const,
      left: '50%',
      top: '50%',
      width: turned ? box.height : box.width,
      height: turned ? box.width : box.height,
      objectFit: 'fill' as const,
      // Rotations commute and the zoom is uniform, so the quarter turn and the
      // straightening can share one rotate() — the same picture the render makes.
      transform: `translate(-50%,-50%) rotate(${turn + degrees}deg) scale(${zoom})`,
    }
  }, [box, turn, degrees, zoom])

  const measured = (event: SyntheticEvent<HTMLImageElement | HTMLVideoElement>) => {
    const element = event.currentTarget
    const size = 'videoWidth' in element
      ? { width: element.videoWidth, height: element.videoHeight }
      : { width: element.naturalWidth, height: element.naturalHeight }
    if (size.width && size.height) setIntrinsic(size)
  }

  const kept = rect.w * rect.h
  const pixels = rotated
    ? `${Math.round(rotated.width * rect.w)} × ${Math.round(rotated.height * rect.h)} px`
    : `${Math.round(rect.w * 100)} × ${Math.round(rect.h * 100)} %`
  const outAspect = mediaAspect ? (rect.w * (rotated?.width ?? 1)) / (rect.h * (rotated?.height ?? 1)) : 0

  return <>
    <div className="editor-body crop-body">
      <div className="crop-stage" ref={stageRef}>
        {box.width > 0 && <div className="crop-box" ref={boxRef} style={{ width: box.width, height: box.height }} onPointerDown={onStageDown}>
          {isVideo
            ? <video className="crop-media" src={src} style={mediaStyle} muted playsInline preload="metadata" onLoadedMetadata={measured} />
            : <img className="crop-media" src={src} alt={item.name} style={mediaStyle} draggable={false} onLoad={measured} />}
          {/* The cut-out hole, filled with a blurred copy — pictures only, a
              playing movie cannot be repainted per frame. */}
          {!isVideo && lasso && <span className="crop-hole" style={{ clipPath: lassoBoxPolygon(lasso, rect) }}>
            <img src={src} alt="" style={{ ...mediaStyle, filter: `blur(${Math.round(6 + (Number(draft.feather ?? DEFAULT_FEATHER)) * 22)}px)` }} draggable={false} />
          </span>}
          <div className="crop-frame" style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}
            onPointerDown={tool === 'cutout' ? undefined : startDrag('move')}>
            {tool !== 'cutout' && HANDLES.map(handle => <i key={handle.id} className={`crop-handle ${handle.className}`} title={handle.title}
              onPointerDown={startDrag(handle.id as never)} />)}
            {tool === 'straighten' && <span className="crop-grid"><i /><i /><i /><i /></span>}
            {lasso && <span className="crop-lasso">
              <svg viewBox="0 0 100 100" preserveAspectRatio="none"><polygon points={lasso.map(([x, y]) => `${x * 100},${y * 100}`).join(' ')} /></svg>
              {lasso.map((entry, index) => <b key={index} className={`lasso-point ${index === 0 ? 'first' : ''}`} style={{ left: `${entry[0] * 100}%`, top: `${entry[1] * 100}%` }}
                onPointerDown={startPointDrag(index)} title={index === 0 ? 'First point' : `Point ${index + 1} — drag to move`} />)}
            </span>}
          </div>
          {!lasso && !edited && tool === 'cutout' && <em className="crop-hint">Click on the picture to place points around what should disappear</em>}
        </div>}
        {tool === 'cutout' && lasso && <em className="crop-hint bottom">Backspace removes the last point · drag a point to reshape</em>}
      </div>

      <div className="crop-panel">
        <div className="crop-tools">
          {([
            { id: 'frame', label: 'Crop frame', icon: <CropIcon size={13} />, hint: 'Drag the rectangle that stays visible' },
            { id: 'straighten', label: 'Straighten', icon: <SlidersHorizontal size={13} />, hint: 'Level the horizon; the picture zooms in so no corner shows' },
            { id: 'cutout', label: 'Cut out', icon: <Eraser size={13} />, hint: 'Click a polygon around something to remove it; the hole is filled with a blurred copy' },
            { id: 'bars', label: 'Black bars', icon: <ScanLine size={13} />, hint: 'Let FFmpeg measure the letterbox bars and propose the crop' },
          ] as { id: Tool; label: string; icon: ReactNode; hint: string }[]).map(entry =>
            <button type="button" key={entry.id} className={`crop-tool ${tool === entry.id ? 'active' : ''}`} title={entry.hint}
              onClick={() => setTool(entry.id)}>{entry.icon}<span>{entry.label}</span></button>)}
        </div>

        {tool === 'frame' && <div className="crop-block">
          <strong>Aspect</strong>
          <div className="crop-aspects">
            {CROP_ASPECTS.map(preset => <button type="button" key={preset.id} className={`crop-aspect ${aspect === preset.id ? 'active' : ''}`}
              disabled={!intrinsic && preset.ratio !== null}
              title={preset.ratio === null ? 'Any shape' : preset.ratio === 0 ? 'The shape of this picture' : 'The shape of the slideshow frame'}
              onClick={() => {
                setAspect(preset.id)
                const target = preset.ratio === null ? null : preset.ratio === 0 ? mediaAspect : preset.ratio
                if (target !== null && rotated) publish({ ...draft, rect: rectForAspect(rect, target, rotated) })
              }}>{preset.label}</button>)}
          </div>
          <div className="crop-readout"><span>Keeps</span><b>{pixels}</b><span>of the picture · {Math.round(kept * 100)} % of its area</span></div>
        </div>}

        {tool === 'straighten' && <div className="crop-block">
          <strong>Straighten <small>−{MAX_STRAIGHTEN}° … +{MAX_STRAIGHTEN}°</small></strong>
          <label className="look-slider"><span>Angle</span>
            <input className="range" type="range" min={-MAX_STRAIGHTEN} max={MAX_STRAIGHTEN} step={0.1} value={degrees} aria-label="Straighten angle"
              onChange={event => setDraft(current => ({ ...current, degrees: Number(event.target.value) }))}
              onPointerUp={() => publish(draft)} onKeyUp={() => publish(draft)} />
            <b>{degrees ? `${degrees > 0 ? '+' : '−'}${Math.abs(degrees).toFixed(1)}°` : '0°'}</b>
          </label>
          <p className="crop-explain">The picture zooms to <b>{Math.round(zoom * 100)} %</b> so the turned corners never show — the render zooms by exactly the same amount.</p>
          <button type="button" className="crop-mini" onClick={() => publish({ ...draft, degrees: 0 })} disabled={!degrees}><RotateCcw size={12}/> Level</button>
        </div>}

        {tool === 'cutout' && <div className="crop-block">
          <strong>Cut out <small>{lasso ? `${lasso.length} of ${MAX_LASSO_POINTS} points` : 'no polygon yet'}</small></strong>
          <label className="look-slider"><span>Feather</span>
            <input className="range" type="range" min={0} max={1} step={0.05} value={Number(draft.feather ?? DEFAULT_FEATHER)} aria-label="Feather the cut edge"
              onChange={event => setDraft(current => ({ ...current, feather: Number(event.target.value) }))}
              onPointerUp={() => publish(draft)} onKeyUp={() => publish(draft)} />
            <b>{Math.round((Number(draft.feather ?? DEFAULT_FEATHER)) * 100)} %</b>
          </label>
          <div className="crop-row">
            <button type="button" className="crop-mini" disabled={!draft.lasso?.length} onClick={() => publish({ ...draft, lasso: (draft.lasso ?? []).slice(0, -1) })}><Undo2 size={12}/> Undo point</button>
            <button type="button" className="crop-mini" disabled={!lasso} onClick={() => publish({ ...draft, lasso: null })}><Trash2 size={12}/> Clear cut</button>
          </div>
          {notice && <p className="crop-note">{notice}</p>}
          <p className="crop-explain">Everything inside the polygon is replaced by a blurred copy of the same picture, so the hole never shows the background of the frame. The edge is softened by the feather amount in the render.</p>
        </div>}

        {tool === 'bars' && <div className="crop-block">
          <strong>Black bars</strong>
          <p className="crop-explain">FFmpeg scans {isVideo ? 'the first seconds of this movie' : 'this picture'} for near-black borders — letterboxed movies, scans with a dark edge, a slide filmed off a projector.</p>
          <div className="crop-row">
            <button type="button" className="crop-mini" onClick={detect} disabled={!detectBars || bars.state === 'loading'}>
              <ScanLine size={12}/> {bars.state === 'loading' ? 'Measuring…' : 'Detect bars'}
            </button>
            {bars.rect && <button type="button" className="crop-mini" onClick={() => publish({ ...draft, rect: bars.rect })}>Use this crop</button>}
          </div>
          {!detectBars && <p className="crop-note warn">This needs the backend (FFmpeg) — it is not available in this browser session.</p>}
          {bars.text && <p className={`crop-note ${bars.state === 'error' ? 'warn' : ''}`}>{bars.text}</p>}
        </div>}

        <div className="crop-block result-block">
          <strong>In the slideshow frame</strong>
          <div className="crop-result">
            <div className="crop-result-frame" style={{ aspectRatio: `${FRAME_ASPECT}` }}>
              <img className="back" src={result.src} alt="" aria-hidden="true" />
              <img className="front" src={result.src} alt="The cropped picture as the slideshow shows it" />
            </div>
          </div>
          <div className="crop-readout"><span>Result</span><b>{outAspect ? outAspect.toFixed(2) : '—'}:1</b>
            <span>{outAspect && Math.abs(outAspect - FRAME_ASPECT) < 0.02 ? 'fills the frame edge to edge' : 'the frame fills the bars with a blurred copy'}</span></div>
        </div>

        <p className="look-note"><Info size={13} /> Cutting never modifies the file on the NAS: the rectangle, the angle and the polygon are stored with the project and applied by FFmpeg at render time{isVideo ? '. A playing movie shows the crop frame and the straightening live; the cut-out hole appears in the thumbnail, in the result above and in the render.' : '.'}</p>
      </div>
    </div>

    <div className="modal-foot">
      <span>{edited ? <><CropIcon size={12}/> {cropSummary({ crop: draft, rotation: item.rotation }) || 'Cropped'}</> : 'No cut or crop — the whole picture is used'}</span>
      <button className="btn ghost" onClick={onReset} disabled={!edited}>Reset crop</button>
      <button className="btn ghost" onClick={onCancel}>Cancel</button>
      <button className="btn dark" onClick={onClose}>Done</button>
    </div>
  </>
}
