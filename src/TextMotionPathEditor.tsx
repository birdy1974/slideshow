import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Eraser, Move, Pencil, Trash2, Play, Pause, Route, Circle, Waves, Zap } from 'lucide-react'
import type { MediaItem } from './mediaItem'

type Point = [number, number]
type PathType = 'straight' | 'freehand' | 'circle' | 'sine'
type Easing = 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out' | 'smooth'

function clampPct(v: number) {
  return Math.max(0, Math.min(100, v))
}

function dist(a: Point, b: Point) {
  const dx = a[0] - b[0], dy = a[1] - b[1]
  return Math.sqrt(dx*dx + dy*dy)
}

function pathLength(points: Point[]) {
  let len = 0
  for (let i = 1; i < points.length; i++) len += dist(points[i-1], points[i])
  return len
}

function pointAlongPath(points: Point[], progress: number): Point {
  if (!points.length) return [50, 50]
  if (points.length === 1) return points[0]
  const p = Math.max(0, Math.min(1, progress))
  const total = pathLength(points)
  if (total < 0.001) return points[0]
  let target = total * p
  for (let i = 1; i < points.length; i++) {
    const seg = dist(points[i-1], points[i])
    if (target <= seg) {
      const t = seg === 0 ? 0 : target / seg
      return [
        points[i-1][0] + (points[i][0] - points[i-1][0]) * t,
        points[i-1][1] + (points[i][1] - points[i-1][1]) * t,
      ]
    }
    target -= seg
  }
  return points[points.length - 1]
}

function simplifyPath(points: Point[], minDist = 1.2): Point[] {
  if (points.length <= 2) return points
  const out: Point[] = [points[0]]
  for (let i = 1; i < points.length; i++) {
    if (dist(out[out.length-1], points[i]) >= minDist) out.push(points[i])
  }
  const last = points[points.length-1]
  if (dist(out[out.length-1], last) > 0.01) out.push(last)
  return out
}

function easeProgress(p: number, easing: Easing): number {
  p = Math.max(0, Math.min(1, p))
  if (easing === 'ease-in') return p*p
  if (easing === 'ease-out') return 1 - (1-p)*(1-p)
  if (easing === 'ease-in-out') {
    if (p < 0.5) return 2*p*p
    return 1 - 2*(1-p)*(1-p)
  }
  if (easing === 'smooth') {
    if (p < 0.5) return 4*p*p*p
    return 1 - Math.pow(-2*p+2, 3)/2
  }
  return p
}

function generateCirclePoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, turns: number, num = 64): Point[] {
  let cx: number, cy: number, r: number
  if (radius != null && radius > 0) {
    cx = fromX
    cy = fromY
    r = radius
  } else {
    cx = (fromX + toX) / 2
    cy = (fromY + toY) / 2
    const d = Math.hypot(toX - fromX, toY - fromY)
    r = d / 2
    if (r < 1) {
      r = 15
      cx = fromX
      cy = fromY
    }
  }
  turns = Math.max(0.1, Math.min(4, turns))
  const pts: Point[] = []
  let startAng = 0
  if (radius == null || radius <= 0) {
    startAng = Math.atan2(fromY - cy, fromX - cx)
  }
  for (let i = 0; i <= num; i++) {
    const ang = startAng + (i / num) * turns * 2 * Math.PI
    const x = cx + r * Math.cos(ang)
    const y = cy + r * Math.sin(ang)
    pts.push([clampPct(x), clampPct(y)])
  }
  return pts
}

function generateSinePoints(fromX: number, fromY: number, toX: number, toY: number, amplitude: number, frequency: number, num = 80): Point[] {
  const amp = Math.max(0, Math.min(40, amplitude))
  const freq = Math.max(0.1, Math.min(10, frequency))
  const dx = toX - fromX
  const dy = toY - fromY
  const len = Math.hypot(dx, dy)
  if (len < 1e-6) {
    const pts: Point[] = []
    for (let i = 0; i <= num; i++) {
      const p = i / num
      const x = fromX + amp * Math.sin(freq * 2 * Math.PI * p)
      const y = fromY + p * 20
      pts.push([clampPct(x), clampPct(y)])
    }
    return pts
  }
  const ux = dx / len
  const uy = dy / len
  const px = -uy
  const py = ux
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const bx = fromX + dx * p
    const by = fromY + dy * p
    const off = amp * Math.sin(freq * 2 * Math.PI * p)
    pts.push([clampPct(bx + px * off), clampPct(by + py * off)])
  }
  return pts
}

function effectivePoints(
  fromX: number, fromY: number, toX: number, toY: number,
  path: Point[] | undefined,
  pathType: PathType,
  circleRadius: number | undefined,
  circleTurns: number,
  sineAmp: number,
  sineFreq: number,
): Point[] {
  if (pathType === 'freehand' && path && path.length >= 2) return path
  if (pathType === 'circle') return generateCirclePoints(fromX, fromY, toX, toY, circleRadius, circleTurns)
  if (pathType === 'sine') return generateSinePoints(fromX, fromY, toX, toY, sineAmp, sineFreq)
  if (Math.abs(fromX - toX) < 0.01 && Math.abs(fromY - toY) < 0.01) return [[fromX, fromY]]
  return [[fromX, fromY], [toX, toY]]
}

type MotionEditorProps = {
  enabled: boolean
  fromX: number
  fromY: number
  toX: number
  toY: number
  path: Point[] | undefined
  pathType?: PathType
  easing?: Easing
  circleRadius?: number
  circleTurns?: number
  sineAmplitude?: number
  sineFrequency?: number
  onChange: (patch: Partial<MediaItem>) => void
  src?: string
  isVideo?: boolean
  background?: string
  caption: string
  captionStyle: React.CSSProperties
}

export function TextMotionPathEditor({
  enabled,
  fromX, fromY, toX, toY,
  path,
  pathType = 'straight',
  easing = 'linear',
  circleRadius,
  circleTurns = 1,
  sineAmplitude = 8,
  sineFrequency = 2,
  onChange,
  src,
  isVideo,
  background,
  caption,
  captionStyle,
}: MotionEditorProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [drawing, setDrawing] = useState(false)
  const [drawPoints, setDrawPoints] = useState<Point[]>([])
  const [dragging, setDragging] = useState<null | 'from' | 'to'>(null)
  const [playing, setPlaying] = useState(true)
  const [progress, setProgress] = useState(0)

  const curPathType: PathType = pathType || (path && path.length >= 2 ? 'freehand' : 'straight')
  const curEasing: Easing = easing || 'linear'

  const effectivePath: Point[] = useMemo(() => {
    return effectivePoints(fromX, fromY, toX, toY, path, curPathType, circleRadius, circleTurns, sineAmplitude, sineFrequency)
  }, [fromX, fromY, toX, toY, path, curPathType, circleRadius, circleTurns, sineAmplitude, sineFrequency])

  useEffect(() => {
    if (!enabled || !playing) return
    let raf = 0
    const start = performance.now()
    const duration = 3000
    const tick = (now: number) => {
      const elapsed = (now - start) % duration
      setProgress(elapsed / duration)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [enabled, playing, effectivePath, curEasing])

  const easedProgress = useMemo(() => easeProgress(progress, curEasing), [progress, curEasing])
  const currentPos = useMemo(() => pointAlongPath(effectivePath, easedProgress), [effectivePath, easedProgress])

  const pctFromEvent = (e: React.PointerEvent | PointerEvent) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return null
    const x = (e.clientX - rect.left) / rect.width * 100
    const y = (e.clientY - rect.top) / rect.height * 100
    return [clampPct(x), clampPct(y)] as Point
  }

  const handlePointerDown = (e: React.PointerEvent) => {
    if (!enabled) return
    const pt = pctFromEvent(e)
    if (!pt) return
    if (curPathType === 'freehand' && drawing) {
      e.preventDefault()
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
      setDrawPoints([pt])
      return
    }
    const nearFrom = dist(pt, [fromX, fromY]) < 6
    const nearTo = dist(pt, [toX, toY]) < 6
    if (nearFrom) {
      setDragging('from')
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
      e.preventDefault()
    } else if (nearTo) {
      setDragging('to')
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
      e.preventDefault()
    } else {
      if (curPathType === 'freehand') {
        ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
        setDrawing(true)
        setDrawPoints([pt])
        e.preventDefault()
      }
    }
  }

  const handlePointerMove = (e: React.PointerEvent) => {
    const pt = pctFromEvent(e)
    if (!pt) return
    if (drawing && curPathType === 'freehand') {
      setDrawPoints(cur => {
        if (!cur.length) return [pt]
        if (dist(cur[cur.length-1], pt) < 0.8) return cur
        return [...cur, pt]
      })
    } else if (dragging) {
      if (dragging === 'from') {
        onChange({ textMoveFromX: pt[0], textMoveFromY: pt[1], textX: pt[0], textY: pt[1] })
        if (curPathType === 'freehand' && path && path.length >= 2) {
          const newPath: Point[] = [[pt[0], pt[1]], ...path.slice(1)]
          onChange({ textMovePath: newPath })
        }
      } else {
        onChange({ textMoveToX: pt[0], textMoveToY: pt[1] })
        if (curPathType === 'freehand' && path && path.length >= 2) {
          const newPath: Point[] = [...path.slice(0, -1), [pt[0], pt[1]]]
          onChange({ textMovePath: newPath })
        }
      }
    }
  }

  const handlePointerUp = () => {
    if (drawing && curPathType === 'freehand') {
      const finalPoints = simplifyPath(drawPoints, 1.0)
      if (finalPoints.length >= 2) {
        const limited = finalPoints.length > 120
          ? finalPoints.filter((_, i) => i % Math.ceil(finalPoints.length / 120) === 0 || i === finalPoints.length - 1)
          : finalPoints
        onChange({
          textMovePath: limited,
          textMovePathType: 'freehand',
          textMoveFromX: limited[0][0],
          textMoveFromY: limited[0][1],
          textMoveToX: limited[limited.length-1][0],
          textMoveToY: limited[limited.length-1][1],
          textX: limited[0][0],
          textY: limited[0][1],
        })
      }
      setDrawPoints([])
      setDrawing(false)
    }
    setDragging(null)
  }

  const toggleEnabled = (on: boolean) => {
    if (on) {
      onChange({
        textMoveEnabled: true,
        textMoveFromX: fromX,
        textMoveFromY: fromY,
        textMoveToX: toX,
        textMoveToY: toY,
        textMovePathType: curPathType,
        textMoveEasing: curEasing,
      })
    } else {
      onChange({ textMoveEnabled: false })
    }
  }

  const clearPath = () => {
    onChange({ textMovePath: undefined, textMovePathType: 'straight' })
  }

  const useStraight = () => {
    onChange({ textMovePath: undefined, textMovePathType: 'straight' })
  }

  const resetPositions = () => {
    onChange({
      textMoveFromX: 20, textMoveFromY: 50,
      textMoveToX: 80, textMoveToY: 50,
      textMovePath: undefined,
      textMovePathType: 'straight',
      textX: 20, textY: 50,
    })
  }

  const svgD = useMemo(() => {
    const pts = drawing ? drawPoints : effectivePath
    if (!pts.length) return ''
    return pts.map((p, i) => `${i===0?'M':'L'} ${p[0]} ${p[1]}`).join(' ')
  }, [effectivePath, drawPoints, drawing])

  const drawD = useMemo(() => {
    if (!drawing || drawPoints.length < 2) return ''
    return drawPoints.map((p,i)=> `${i===0?'M':'L'} ${p[0]} ${p[1]}`).join(' ')
  }, [drawing, drawPoints])

  return <div className="text-motion-section">
    <div className="text-motion-head">
      <label className="check-label">
        <input type="checkbox" checked={enabled} onChange={e=>toggleEnabled(e.target.checked)} />
        <span><Check size={11}/></span>
        Enable text motion path
        <small style={{marginLeft:6, opacity:.7}}><Route size={12}/> moves from start to end</small>
      </label>
      {enabled && <div className="motion-head-actions">
        <button type="button" className={`icon-button small ${playing?'active':''}`} title={playing?'Pause motion preview':'Play motion preview'} onClick={()=>setPlaying(p=>!p)}>{playing?<Pause size={12}/>:<Play size={12}/>}</button>
      </div>}
    </div>

    {enabled && <>
      <div className="motion-type-row">
        <span className="motion-label"><Route size={12}/> Path type</span>
        <div className="motion-type-buttons">
          <button type="button" className={`btn ghost small ${curPathType==='straight'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'straight' })} title="Straight line"><Move size={12}/> Straight</button>
          <button type="button" className={`btn ghost small ${curPathType==='freehand'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'freehand' })} title="Freehand drawn path"><Pencil size={12}/> Free draw</button>
          <button type="button" className={`btn ghost small ${curPathType==='circle'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'circle' })} title="Circular path"><Circle size={12}/> Circle</button>
          <button type="button" className={`btn ghost small ${curPathType==='sine'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'sine' })} title="Sinus wave path"><Waves size={12}/> Sinus</button>
        </div>
      </div>

      <div className="motion-type-row">
        <span className="motion-label"><Zap size={12}/> Speed easing</span>
        <div className="motion-type-buttons">
          <button type="button" className={`btn ghost small ${curEasing==='linear'?'active':''}`} onClick={()=>onChange({ textMoveEasing: 'linear' })}>Linear</button>
          <button type="button" className={`btn ghost small ${curEasing==='ease-in'?'active':''}`} onClick={()=>onChange({ textMoveEasing: 'ease-in' })}>Fade in</button>
          <button type="button" className={`btn ghost small ${curEasing==='ease-out'?'active':''}`} onClick={()=>onChange({ textMoveEasing: 'ease-out' })}>Fade out</button>
          <button type="button" className={`btn ghost small ${curEasing==='ease-in-out'?'active':''}`} onClick={()=>onChange({ textMoveEasing: 'ease-in-out' })}>In-Out</button>
          <button type="button" className={`btn ghost small ${curEasing==='smooth'?'active':''}`} onClick={()=>onChange({ textMoveEasing: 'smooth' })}>Smooth</button>
        </div>
      </div>

      {curPathType === 'circle' && <div className="motion-params">
        <label>Radius <input type="range" min={2} max={40} step={1} value={circleRadius ?? (Math.hypot(toX-fromX, toY-fromY)/2 || 15)} onChange={e=>onChange({ textMoveCircleRadius: Number(e.target.value), textMovePathType: 'circle' })} /> <em>{Math.round(circleRadius ?? (Math.hypot(toX-fromX, toY-fromY)/2 || 15))}%</em></label>
        <label>Turns <input type="range" min={0.25} max={3} step={0.25} value={circleTurns} onChange={e=>onChange({ textMoveCircleTurns: Number(e.target.value) })} /> <em>{circleTurns}×</em></label>
      </div>}

      {curPathType === 'sine' && <div className="motion-params">
        <label>Amplitude <input type="range" min={0} max={30} step={1} value={sineAmplitude} onChange={e=>onChange({ textMoveSineAmplitude: Number(e.target.value) })} /> <em>{sineAmplitude}%</em></label>
        <label>Frequency <input type="range" min={0.5} max={6} step={0.5} value={sineFrequency} onChange={e=>onChange({ textMoveSineFrequency: Number(e.target.value) })} /> <em>{sineFrequency} waves</em></label>
      </div>}

      <div
        ref={containerRef}
        className={`text-motion-canvas ${drawing?'drawing':''} ${dragging?'dragging':''} type-${curPathType}`}
        style={{ background: background || '#222' }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        title={drawing ? 'Drawing path — drag to draw, release to finish' : curPathType==='freehand' ? 'Drag start/end handles or draw a freehand path' : 'Drag S/E handles — path preview updates'}
      >
        {src && (isVideo
          ? <video src={src} muted playsInline autoPlay loop className="motion-bg" />
          : <img src={src} alt="" className="motion-bg" draggable={false} />)}
        <svg className="motion-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
          <path d={svgD} fill="none" stroke="rgba(145,169,107,0.9)" strokeWidth="0.7" strokeDasharray={curPathType==='straight' ? "2 2" : undefined} strokeLinecap="round" strokeLinejoin="round" />
          {drawing && <path d={drawD} fill="none" stroke="rgba(255,220,90,0.95)" strokeWidth="0.9" strokeLinecap="round" strokeLinejoin="round" />}
          {effectivePath.length>=2 && <circle cx={effectivePath[effectivePath.length-1][0]} cy={effectivePath[effectivePath.length-1][1]} r="0.8" fill="rgba(255,80,80,0.9)" />}
        </svg>

        <span className="motion-handle from" style={{ left:`${fromX}%`, top:`${fromY}%` }} title={`Start ${Math.round(fromX)}%, ${Math.round(fromY)}%`}><b>S</b></span>
        <span className="motion-handle to" style={{ left:`${toX}%`, top:`${toY}%` }} title={`End ${Math.round(toX)}%, ${Math.round(toY)}%`}><b>E</b></span>

        <span className="motion-caption" style={{ left:`${currentPos[0]}%`, top:`${currentPos[1]}%`, ...captionStyle }}>{caption}</span>

        <em className="motion-hint">{drawing ? 'Drawing… release to set path' : curPathType==='freehand' ? 'Draw a path with mouse/finger — S → E' : `${curPathType} · ${curEasing} · drag S/E to adjust`}</em>
      </div>

      <div className="motion-readout">
        <span><Move size={12}/> Start <strong>X {Math.round(fromX)}% Y {Math.round(fromY)}%</strong></span>
        <span>→ End <strong>X {Math.round(toX)}% Y {Math.round(toY)}%</strong></span>
        <span>{curPathType} · {curEasing} · {effectivePath.length} pts</span>
      </div>

      <div className="motion-controls">
        <button type="button" className="btn ghost small" onClick={resetPositions} title="Reset start left, end right"><Move size={12}/> Reset S→E</button>
        <button type="button" className="btn ghost small" onClick={useStraight} disabled={curPathType==='straight' && (!path || path.length<2)} title="Use straight line"><Route size={12}/> Straight</button>
        <button type="button" className="btn ghost small" onClick={clearPath} disabled={!path || path.length<2} title="Clear custom path"><Eraser size={12}/> Clear path</button>
        <button type="button" className="btn ghost small danger" onClick={()=>onChange({ textMovePath: undefined, textMovePathType: 'straight', textMoveToX: fromX, textMoveToY: fromY })} title="Make static"><Trash2 size={12}/> Make static</button>
      </div>

      <p className="motion-note"><small>Text moves during its visible window (textStart → textEnd). Duration determines speed (longer = slower). Predefined paths: straight, free draw, circle (radius & turns), sinus (amplitude & frequency). Easing fades speed in/out: linear, fade-in (ease-in), fade-out (ease-out), in-out, smooth cubic.</small></p>
    </>}
  </div>
}
