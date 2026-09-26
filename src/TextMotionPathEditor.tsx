import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Eraser, Move, Pencil, Trash2, Play, Pause, Route, Circle, Waves, Zap, Star, Diamond, Triangle, GitBranch, Plus, Minus, ArrowUpDown, Shell, Infinity as InfinityIcon, Activity, Heart, Hexagon, Undo2 } from 'lucide-react'
import type { MediaItem } from './mediaItem'
import { backdropBlurPx, useFrameScale } from './useFrameScale'

type Point = [number, number]
type PathType = 'straight' | 'freehand' | 'circle' | 'sine' | 'star' | 'diamond' | 'triangle' | 'polyline' | 'sine-vertical' | 'bounce' | 'spiral' | 'figure-8' | 'lissajous' | 'zigzag' | 'heart' | 'polygon' | 'pendulum'
type Easing = 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out' | 'smooth'

function clampPct(v: number) {
  return Math.max(0, Math.min(100, v))
}

function dist(a: Point, b: Point) {
  const dx = a[0] - b[0], dy = a[1] - b[1]
  return Math.sqrt(dx*dx + dy*dy)
}

// The caption's font size arrives in frame pixels (the value the render's drawtext
// uses, e.g. "48px" on a 1080p frame). Rescale it to this canvas so the moving
// caption matches the size the picture will have in the final MP4.
function scaleFrameFont(fontSize: string | number | undefined, frameScale: number): string | number | undefined {
  if (fontSize == null || frameScale <= 0) return fontSize
  const n = Number.parseFloat(String(fontSize))
  if (!Number.isFinite(n) || n <= 0) return fontSize
  return `${Math.max(4, n * frameScale)}px`
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

function generateSineVerticalPoints(fromX: number, fromY: number, toX: number, toY: number, amplitude: number, frequency: number, num = 80): Point[] {
  const amp = Math.max(0, Math.min(30, amplitude))
  const freq = Math.max(0.1, Math.min(10, frequency))
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const bx = fromX + (toX - fromX) * p
    const by = fromY + (toY - fromY) * p
    const off = amp * Math.sin(freq * 2 * Math.PI * p)
    pts.push([clampPct(bx), clampPct(by + off)])
  }
  return pts
}

function generateStarPoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, points: number, innerRatio: number, rotation: number): Point[] {
  const n = Math.max(3, Math.min(10, Math.round(points || 5)))
  const ratio = Math.max(0.2, Math.min(0.85, innerRatio ?? 0.45))
  let cx: number, cy: number, r: number
  if (radius != null && radius > 2) {
    cx = fromX; cy = fromY; r = radius
  } else {
    cx = (fromX + toX) / 2; cy = (fromY + toY) / 2
    const d = Math.hypot(toX - fromX, toY - fromY)
    r = Math.max(8, d * 0.45)
  }
  const rot = (rotation || 0) * Math.PI / 180
  const pts: Point[] = []
  const step = Math.PI / n
  // create closed star polyline: outer, inner alternating, close loop, plus extra to show traversal
  const vertices: Point[] = []
  for (let i = 0; i < n * 2; i++) {
    const ang = rot - Math.PI / 2 + i * step
    const rad = i % 2 === 0 ? r : r * ratio
    vertices.push([clampPct(cx + rad * Math.cos(ang)), clampPct(cy + rad * Math.sin(ang))])
  }
  vertices.push(vertices[0])
  // sample along vertices to create smooth path following star outline
  const perSeg = 12
  for (let i = 0; i < vertices.length - 1; i++) {
    const a = vertices[i], b = vertices[i+1]
    for (let k = 0; k < perSeg; k++) {
      const t = k / perSeg
      pts.push([clampPct(a[0] + (b[0]-a[0])*t), clampPct(a[1] + (b[1]-a[1])*t)])
    }
  }
  pts.push(vertices[vertices.length-1])
  return pts
}

function generateDiamondPoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, rotation: number): Point[] {
  let cx: number, cy: number, r: number
  if (radius != null && radius > 2) {
    cx = fromX; cy = fromY; r = radius
  } else {
    cx = (fromX + toX) / 2; cy = (fromY + toY) / 2
    const d = Math.hypot(toX - fromX, toY - fromY)
    r = Math.max(10, d * 0.5)
  }
  const rot = (rotation || 0) * Math.PI / 180
  const base: Point[] = [
    [cx, cy - r],
    [cx + r, cy],
    [cx, cy + r],
    [cx - r, cy],
  ].map(([x,y]) => {
    const dx = x - cx, dy = y - cy
    const nx = dx * Math.cos(rot) - dy * Math.sin(rot)
    const ny = dx * Math.sin(rot) + dy * Math.cos(rot)
    return [clampPct(cx + nx), clampPct(cy + ny)] as Point
  })
  base.push(base[0])
  const pts: Point[] = []
  const perSeg = 20
  for (let i = 0; i < base.length - 1; i++) {
    const a = base[i], b = base[i+1]
    for (let k = 0; k < perSeg; k++) {
      const t = k / perSeg
      pts.push([clampPct(a[0] + (b[0]-a[0])*t), clampPct(a[1] + (b[1]-a[1])*t)])
    }
  }
  pts.push(base[base.length-1])
  return pts
}

function generateTrianglePoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, rotation: number): Point[] {
  let cx: number, cy: number, r: number
  if (radius != null && radius > 2) {
    cx = fromX; cy = fromY; r = radius
  } else {
    cx = (fromX + toX) / 2; cy = (fromY + toY) / 2
    const d = Math.hypot(toX - fromX, toY - fromY)
    r = Math.max(10, d * 0.55)
  }
  const rot = (rotation || 0) * Math.PI / 180
  const vertices: Point[] = []
  for (let i = 0; i < 3; i++) {
    const ang = rot - Math.PI/2 + i * (2*Math.PI/3)
    vertices.push([clampPct(cx + r * Math.cos(ang)), clampPct(cy + r * Math.sin(ang))])
  }
  vertices.push(vertices[0])
  const pts: Point[] = []
  const perSeg = 24
  for (let i = 0; i < vertices.length - 1; i++) {
    const a = vertices[i], b = vertices[i+1]
    for (let k = 0; k < perSeg; k++) {
      const t = k / perSeg
      pts.push([clampPct(a[0] + (b[0]-a[0])*t), clampPct(a[1] + (b[1]-a[1])*t)])
    }
  }
  pts.push(vertices[vertices.length-1])
  return pts
}

function generateBouncePoints(fromX: number, fromY: number, toX: number, toY: number, height: number, bounces: number, damping: number, numPerBounce = 28): Point[] {
  const h = Math.max(0, Math.min(30, height ?? 14))
  const n = Math.max(1, Math.min(8, Math.round(bounces ?? 4)))
  const d = Math.max(0, Math.min(0.9, damping ?? 0.35))
  // linear base from start to end, vertical bounce offset subtracted (upwards is smaller y in %: 0 top, 100 bottom)
  // bounce height decays: bounce k has amplitude h * (1-d)^k
  const pts: Point[] = []
  for (let i = 0; i < n; i++) {
    const amp = h * Math.pow(1 - d, i)
    // segment progress range
    const segStart = i / n
    const segEnd = (i+1) / n
    for (let k = 0; k < numPerBounce; k++) {
      const tSeg = k / numPerBounce // 0..1 within bounce
      const p = segStart + tSeg * (segEnd - segStart) // global 0..1
      const bx = fromX + (toX - fromX) * p
      const byBase = fromY + (toY - fromY) * p
      // parabola: 4*t*(1-t) peaks at 0.5 => 1. So bounce up (negative y)
      const parabola = 4 * tSeg * (1 - tSeg)
      const off = -amp * parabola
      pts.push([clampPct(bx), clampPct(byBase + off)])
    }
  }
  // ensure end point exactly
  pts.push([clampPct(toX), clampPct(toY)])
  return pts
}

// ---- New path shapes (2026-09 round): twins of the App.tsx generators the
// preview and the renderer use — same formulas and sample counts. ----
function generateSpiralPoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, turns: number, num = 96): Point[] {
  const d = Math.hypot(toX - fromX, toY - fromY)
  let cx: number, cy: number, r0: number, startAng: number
  if (radius != null && radius > 2) { cx = fromX; cy = fromY; r0 = radius; startAng = -Math.PI / 2 }
  else if (d > 0.5) { cx = toX; cy = toY; r0 = d; startAng = Math.atan2(fromY - cy, fromX - cx) }
  else { cx = fromX; cy = fromY; r0 = 15; startAng = -Math.PI / 2 }
  const t = Math.max(0.2, Math.min(6, turns))
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const ang = startAng + p * t * 2 * Math.PI
    const r = r0 * (1 - p)
    pts.push([clampPct(cx + r * Math.cos(ang)), clampPct(cy + r * Math.sin(ang))])
  }
  pts.push([clampPct(cx), clampPct(cy)])
  return pts
}
function generateFigure8Points(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, num = 120): Point[] {
  const d = Math.hypot(toX - fromX, toY - fromY)
  const explicit = radius != null && radius > 2
  const cx = explicit ? fromX : (fromX + toX) / 2
  const cy = explicit ? fromY : (fromY + toY) / 2
  const a = explicit ? radius as number : Math.max(10, d * 0.4)
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const t = (i / num) * 2 * Math.PI
    const s = Math.sin(t), c = Math.cos(t)
    const den = 1 + s * s
    pts.push([clampPct(cx + a * c / den), clampPct(cy + a * s * c / den)])
  }
  return pts
}
function generateLissajousPoints(fromX: number, fromY: number, toX: number, toY: number, amp: number, freqX: number, freqY: number, num = 140): Point[] {
  const ax = Math.max(2, Math.min(40, amp || 14))
  const ay = ax * 0.7
  const f1 = Math.max(0.5, Math.min(8, freqX || 3))
  const f2 = Math.max(0.5, Math.min(8, freqY || 2))
  const cx = fromX, cy = fromY
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const t = i / num
    pts.push([clampPct(cx + ax * Math.sin(2 * Math.PI * f1 * t + Math.PI / 2)), clampPct(cy + ay * Math.sin(2 * Math.PI * f2 * t))])
  }
  return pts
}
function generateZigzagPoints(fromX: number, fromY: number, toX: number, toY: number, amplitude: number, frequency: number, num = 96): Point[] {
  const amp = Math.max(0, Math.min(40, amplitude ?? 10))
  const freq = Math.max(0.5, Math.min(10, frequency ?? 3))
  const dx = toX - fromX, dy = toY - fromY
  const len = Math.hypot(dx, dy)
  const ux = len < 1e-6 ? 1 : dx / len, uy = len < 1e-6 ? 0 : dy / len
  const px = -uy, py = ux
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const s = p * freq
    const f = s - Math.floor(s)
    const tri = f < 0.5 ? f * 4 - 1 : 3 - f * 4
    const off = amp * tri
    pts.push([clampPct(fromX + dx * p + px * off), clampPct(fromY + dy * p + py * off)])
  }
  return pts
}
function generateHeartPoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, rotation: number, num = 120): Point[] {
  const d = Math.hypot(toX - fromX, toY - fromY)
  const explicit = radius != null && radius > 2
  const cx = explicit ? fromX : (fromX + toX) / 2
  const cy = explicit ? fromY : (fromY + toY) / 2
  const r = explicit ? radius as number : Math.max(10, d * 0.4)
  const s = r / 16
  const rot = (rotation || 0) * Math.PI / 180
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const t = (i / num) * 2 * Math.PI
    const hx = 16 * Math.pow(Math.sin(t), 3)
    const hy = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)
    const x = s * hx, y = -s * hy
    pts.push([clampPct(cx + x * Math.cos(rot) - y * Math.sin(rot)), clampPct(cy + x * Math.sin(rot) + y * Math.cos(rot))])
  }
  return pts
}
function generatePolygonPoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, sides: number, rotation: number, numPerSeg = 24): Point[] {
  const n = Math.max(3, Math.min(10, Math.round(sides || 5)))
  const d = Math.hypot(toX - fromX, toY - fromY)
  let cx: number, cy: number, r: number
  if (radius != null && radius > 2) { cx = fromX; cy = fromY; r = radius }
  else { cx = (fromX + toX) / 2; cy = (fromY + toY) / 2; r = Math.max(10, d * 0.5) }
  const rot = (rotation || 0) * Math.PI / 180
  const vertices: Point[] = []
  for (let i = 0; i < n; i++) {
    const ang = rot - Math.PI / 2 + i * (2 * Math.PI / n)
    vertices.push([clampPct(cx + r * Math.cos(ang)), clampPct(cy + r * Math.sin(ang))])
  }
  vertices.push(vertices[0])
  const pts: Point[] = []
  for (let i = 0; i < vertices.length - 1; i++) {
    const a = vertices[i], b = vertices[i + 1]
    for (let k = 0; k < numPerSeg; k++) {
      const t = k / numPerSeg
      pts.push([clampPct(a[0] + (b[0] - a[0]) * t), clampPct(a[1] + (b[1] - a[1]) * t)])
    }
  }
  pts.push(vertices[vertices.length - 1])
  return pts
}
function generatePendulumPoints(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, num = 60): Point[] {
  const arm = radius != null && radius > 2 ? radius : 20
  const px = (fromX + toX) / 2, py = (fromY + toY) / 2 - arm
  const d0 = Math.hypot(fromX - px, fromY - py)
  const d1 = Math.hypot(toX - px, toY - py)
  const r = Math.max(2, (d0 + d1) / 2)
  let a0 = Math.atan2(fromY - py, fromX - px)
  let a1 = Math.atan2(toY - py, toX - px)
  if (d0 < 0.5 && d1 < 0.5) { a0 = Math.PI / 2 - 0.5; a1 = Math.PI / 2 + 0.5 }
  const norm2pi = (a: number) => { let x = a % (2 * Math.PI); if (x < 0) x += 2 * Math.PI; return x }
  const crosses = (start: number, delta: number, target: number) => {
    const t = norm2pi(target - start)
    const span = Math.abs(delta)
    return delta >= 0 ? t <= span + 1e-9 : (2 * Math.PI - t) <= span + 1e-9
  }
  let cw = norm2pi(a1 - a0)
  if (cw < 1e-9) cw = 2 * Math.PI
  const ccw = cw - 2 * Math.PI
  const cwPasses = crosses(a0, cw, Math.PI / 2)
  const ccwPasses = crosses(a0, ccw, Math.PI / 2)
  let delta: number
  if (cwPasses && ccwPasses) delta = Math.abs(cw) <= Math.abs(ccw) ? cw : ccw
  else if (cwPasses) delta = cw
  else if (ccwPasses) delta = ccw
  else delta = Math.abs(cw) <= Math.abs(ccw) ? cw : ccw
  const pts: Point[] = []
  for (let i = 0; i <= num; i++) {
    const ang = a0 + (i / num) * delta
    pts.push([clampPct(px + r * Math.cos(ang)), clampPct(py + r * Math.sin(ang))])
  }
  return pts
}

function applySinusUpDown(points: Point[], enabled: boolean, amplitude: number, frequency: number): Point[] {
  if (!enabled || points.length < 2) return points
  const amp = Math.max(0, Math.min(20, amplitude ?? 6))
  if (amp < 0.2) return points
  const freq = Math.max(0.1, Math.min(10, frequency ?? 2))
  const total = pathLength(points)
  if (total < 1e-6) return points
  // resample with vertical sine offset applied along progress
  const num = Math.max(points.length, 80)
  const out: Point[] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const base = pointAlongPath(points, p)
    const off = amp * Math.sin(freq * 2 * Math.PI * p)
    out.push([clampPct(base[0]), clampPct(base[1] + off)])
  }
  return out
}

function effectivePoints(
  fromX: number, fromY: number, toX: number, toY: number,
  path: Point[] | undefined,
  pathType: PathType,
  circleRadius: number | undefined,
  circleTurns: number,
  sineAmp: number,
  sineFreq: number,
  starPoints?: number,
  starInnerRatio?: number,
  symbolRotation?: number,
  sinusEnabled?: boolean,
  sinusAmp?: number,
  sinusFreq?: number,
  bounceHeight?: number,
  bounceCount?: number,
  bounceDamping?: number,
  lissajousFreqY?: number,
): Point[] {
  let base: Point[]
  if ((pathType === 'freehand' || pathType === 'polyline') && path && path.length >= 2) base = path
  else if (pathType === 'circle') base = generateCirclePoints(fromX, fromY, toX, toY, circleRadius, circleTurns)
  else if (pathType === 'sine') base = generateSinePoints(fromX, fromY, toX, toY, sineAmp, sineFreq)
  else if (pathType === 'sine-vertical') base = generateSineVerticalPoints(fromX, fromY, toX, toY, sineAmp, sineFreq)
  else if (pathType === 'star') base = generateStarPoints(fromX, fromY, toX, toY, circleRadius, starPoints ?? 5, starInnerRatio ?? 0.45, symbolRotation ?? 0)
  else if (pathType === 'diamond') base = generateDiamondPoints(fromX, fromY, toX, toY, circleRadius, symbolRotation ?? 0)
  else if (pathType === 'triangle') base = generateTrianglePoints(fromX, fromY, toX, toY, circleRadius, symbolRotation ?? 0)
  else if (pathType === 'bounce') base = generateBouncePoints(fromX, fromY, toX, toY, bounceHeight ?? 14, bounceCount ?? 4, bounceDamping ?? 0.35)
  else if (pathType === 'spiral') base = generateSpiralPoints(fromX, fromY, toX, toY, circleRadius, circleTurns)
  else if (pathType === 'figure-8') base = generateFigure8Points(fromX, fromY, toX, toY, circleRadius)
  else if (pathType === 'lissajous') base = generateLissajousPoints(fromX, fromY, toX, toY, sineAmp, sineFreq, lissajousFreqY ?? 2)
  else if (pathType === 'zigzag') base = generateZigzagPoints(fromX, fromY, toX, toY, sineAmp, sineFreq)
  else if (pathType === 'heart') base = generateHeartPoints(fromX, fromY, toX, toY, circleRadius, symbolRotation ?? 0)
  else if (pathType === 'polygon') base = generatePolygonPoints(fromX, fromY, toX, toY, circleRadius, starPoints ?? 5, symbolRotation ?? 0)
  else if (pathType === 'pendulum') base = generatePendulumPoints(fromX, fromY, toX, toY, circleRadius)
  else if (pathType === 'polyline' && path && path.length >= 2) base = path
  else {
    if (Math.abs(fromX - toX) < 0.01 && Math.abs(fromY - toY) < 0.01) base = [[fromX, fromY]]
    else base = [[fromX, fromY], [toX, toY]]
  }
  if (sinusEnabled) {
    base = applySinusUpDown(base, true, sinusAmp ?? 6, sinusFreq ?? 2)
  }
  return base
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
  starPoints?: number
  starInnerRatio?: number
  symbolRotation?: number
  sinusEnabled?: boolean
  sinusAmplitude?: number
  sinusFrequency?: number
  bounceHeight?: number
  bounceCount?: number
  bounceDamping?: number
  // Lissajous only: vertical frequency (the horizontal one is sineFrequency).
  lissajousFreqY?: number
  // Caption turns to follow the path tangent (item: textMoveRotateAlongPath).
  rotateAlong?: boolean
  // Rotate/squash preview: undefined pair = off. The moving caption tilts and
  // squashes over the same loop as the path, around its own centre.
  rotateFrom?: number
  rotateTo?: number
  rotateSpeed?: number
  squishFrom?: number
  squishTo?: number
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
  starPoints = 5,
  starInnerRatio = 0.45,
  symbolRotation = 0,
  sinusEnabled = false,
  sinusAmplitude = 6,
  sinusFrequency = 2,
  bounceHeight = 14,
  bounceCount = 4,
  bounceDamping = 0.35,
  lissajousFreqY = 2,
  rotateAlong = false,
  rotateFrom,
  rotateTo,
  rotateSpeed,
  squishFrom,
  squishTo,
  onChange,
  src,
  isVideo,
  background,
  caption,
  captionStyle,
}: MotionEditorProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  // Scales frame-pixel font sizes (the value the render uses) onto this canvas.
  // The canvas only exists while motion is enabled, so the scale ref must be a
  // callback ref attached alongside the pointer-calculation ref.
  const { setRef: motionScaleRef, scale: frameScale } = useFrameScale<HTMLDivElement>()
  const [drawing, setDrawing] = useState(false)
  const [drawPoints, setDrawPoints] = useState<Point[]>([])
  const [dragging, setDragging] = useState<null | 'from' | 'to' | number>(null)
  const [playing, setPlaying] = useState(true)
  const [progress, setProgress] = useState(0)

  const curPathType: PathType = (pathType as PathType) || (path && path.length >= 2 ? 'freehand' : 'straight')
  const curEasing: Easing = easing || 'linear'

  const effectivePath: Point[] = useMemo(() => {
    return effectivePoints(fromX, fromY, toX, toY, path, curPathType, circleRadius, circleTurns, sineAmplitude, sineFrequency, starPoints, starInnerRatio, symbolRotation, sinusEnabled, sinusAmplitude, sinusFrequency, bounceHeight, bounceCount, bounceDamping, lissajousFreqY)
  }, [fromX, fromY, toX, toY, path, curPathType, circleRadius, circleTurns, sineAmplitude, sineFrequency, starPoints, starInnerRatio, symbolRotation, sinusEnabled, sinusAmplitude, sinusFrequency, bounceHeight, bounceCount, bounceDamping, lissajousFreqY])

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
  // Tangent angle of the path at the current position, in the canvas's own
  // pixel space (so it matches the render, which measures the angle in frame
  // pixels). Twin of the 'path' branch in textMotionCore.ts / text_motion.py.
  const rotateAlongAngle = useMemo(() => {
    if (!rotateAlong || effectivePath.length < 2) return 0
    const rect = containerRef.current?.getBoundingClientRect()
    const w = rect?.width || 16
    const h = rect?.height || 9
    const f = easedProgress
    let dx: number, dy: number
    if (f + 0.02 > 1) {
      const q = pointAlongPath(effectivePath, Math.max(0, f - 0.02))
      dx = (currentPos[0] - q[0]) * w; dy = (currentPos[1] - q[1]) * h
    } else {
      const q = pointAlongPath(effectivePath, f + 0.02)
      dx = (q[0] - currentPos[0]) * w; dy = (q[1] - currentPos[1]) * h
    }
    return (Math.abs(dx) > 1e-9 || Math.abs(dy) > 1e-9) ? Math.atan2(dy, dx) * 180 / Math.PI : 0
  }, [rotateAlong, effectivePath, easedProgress, currentPos])
  // Rotate/squash the moving caption over the same loop (linear, full window —
  // the main canvas and the MP4 do the same; the path easing only shapes the
  // position). Off (undefined) pairs leave the caption untouched.
  const rotEn = rotateFrom !== undefined && rotateTo !== undefined
  const sqEn = squishFrom !== undefined && squishTo !== undefined
  const captionTransform = (rotEn || sqEn || (rotateAlong && effectivePath.length >= 2))
    ? (() => {
        // This canvas loops every 3 s, so that is its "window" for the speed
        // math (the main canvas and the MP4 use the real text window).
        const rotProg = rotEn && rotateSpeed && rotateSpeed > 0
          ? Math.max(0, Math.min(1, (progress * 3) / rotateSpeed))
          : progress
        const angle = rotateAlongAngle + (rotEn ? rotateFrom! + (rotateTo! - rotateFrom!) * rotProg : 0)
        const f = sqEn ? squishFrom! + (squishTo! - squishFrom!) * progress : 1
        return `translate(-50%,-50%) rotate(${angle.toFixed(2)}deg) scale(${(1 + (1 - f) * 0.5).toFixed(3)}, ${f.toFixed(3)})`
      })()
    : undefined

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
    // Polyline waypoint hit test
    if ((curPathType === 'polyline' || curPathType === 'freehand') && path && path.length >= 2) {
      for (let i = 0; i < path.length; i++) {
        if (dist(pt, path[i]) < 4.5) {
          setDragging(i)
          ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
          e.preventDefault()
          return
        }
      }
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
      } else if (curPathType === 'polyline') {
        // add new waypoint at click position (append or insert near segment)
        const current = path && path.length >= 2 ? path : [[fromX, fromY], [toX, toY]] as Point[]
        // find closest segment to insert
        let insertIdx = current.length
        let bestDist = Infinity
        for (let i = 0; i < current.length - 1; i++) {
          const a = current[i], b = current[i+1]
          // distance from pt to segment
          const l2 = (b[0]-a[0])**2 + (b[1]-a[1])**2
          if (l2 === 0) continue
          let t = ((pt[0]-a[0])*(b[0]-a[0]) + (pt[1]-a[1])*(b[1]-a[1])) / l2
          t = Math.max(0, Math.min(1, t))
          const proj: Point = [a[0]+t*(b[0]-a[0]), a[1]+t*(b[1]-a[1])]
          const d = dist(pt, proj)
          if (d < bestDist && d < 8) { bestDist = d; insertIdx = i+1 }
        }
        const next = [...current]
        next.splice(insertIdx, 0, pt)
        onChange({ textMovePath: next.slice(0, 120), textMovePathType: 'polyline', textMoveFromX: next[0][0], textMoveFromY: next[0][1], textMoveToX: next[next.length-1][0], textMoveToY: next[next.length-1][1] })
        setDragging(insertIdx as any)
        ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
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
    } else if (dragging !== null) {
      if (dragging === 'from') {
        onChange({ textMoveFromX: pt[0], textMoveFromY: pt[1], textX: pt[0], textY: pt[1] })
        if ((curPathType === 'freehand' || curPathType === 'polyline') && path && path.length >= 2) {
          const newPath: Point[] = [[pt[0], pt[1]], ...path.slice(1)]
          onChange({ textMovePath: newPath })
        }
      } else if (dragging === 'to') {
        onChange({ textMoveToX: pt[0], textMoveToY: pt[1] })
        if ((curPathType === 'freehand' || curPathType === 'polyline') && path && path.length >= 2) {
          const newPath: Point[] = [...path.slice(0, -1), [pt[0], pt[1]]]
          onChange({ textMovePath: newPath })
        }
      } else if (typeof dragging === 'number') {
        if (path && path.length >= 2) {
          const next = [...path]
          next[dragging] = pt
          onChange({ textMovePath: next, textMoveFromX: next[0][0], textMoveFromY: next[0][1], textMoveToX: next[next.length-1][0], textMoveToY: next[next.length-1][1] })
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

  const handlePolyAddMid = () => {
    const current = path && path.length >= 2 ? path : [[fromX, fromY], [toX, toY]] as Point[]
    const mid: Point = [(current[0][0]+current[current.length-1][0])/2, (current[0][1]+current[current.length-1][1])/2]
    const idx = Math.floor(current.length/2)
    const next = [...current]
    next.splice(idx, 0, mid)
    onChange({ textMovePath: next, textMovePathType: 'polyline' })
  }
  const handlePolyRemove = (idx: number) => {
    if (!path || path.length <= 2) return
    const next = path.filter((_, i) => i !== idx)
    onChange({ textMovePath: next, textMoveFromX: next[0][0], textMoveFromY: next[0][1], textMoveToX: next[next.length-1][0], textMoveToY: next[next.length-1][1] })
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
        <div className="motion-type-buttons" style={{flexWrap:'wrap'}}>
          <button type="button" className={`btn ghost small ${curPathType==='straight'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'straight' })} title="Straight line"><Move size={12}/> Straight</button>
          <button type="button" className={`btn ghost small ${curPathType==='freehand'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'freehand' })} title="Freehand drawn path"><Pencil size={12}/> Free draw</button>
          <button type="button" className={`btn ghost small ${curPathType==='polyline'?'active':''}`} onClick={()=>{ const init = path && path.length>=2 ? path : [[fromX,fromY],[toX,toY]] as Point[]; onChange({ textMovePathType: 'polyline', textMovePath: init }) }} title="Multi-point polyline — click to add intermediate points, drag to move"><GitBranch size={12}/> Polyline</button>
          <button type="button" className={`btn ghost small ${curPathType==='circle'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'circle' })} title="Circular path"><Circle size={12}/> Circle</button>
          <button type="button" className={`btn ghost small ${curPathType==='sine'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'sine' })} title="Sinus wave path"><Waves size={12}/> Sinus</button>
          <button type="button" className={`btn ghost small ${curPathType==='sine-vertical'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'sine-vertical' })} title="Vertical sinus up/down — straight line with vertical wave"><Waves size={12} style={{transform:'rotate(90deg)'}}/> Sine vertical</button>
          <button type="button" className={`btn ghost small ${curPathType==='star'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'star' })} title="Star-shaped path"><Star size={12}/> Star</button>
          <button type="button" className={`btn ghost small ${curPathType==='diamond'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'diamond' })} title="Diamond path"><Diamond size={12}/> Diamond</button>
          <button type="button" className={`btn ghost small ${curPathType==='triangle'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'triangle' })} title="Triangle path"><Triangle size={12}/> Triangle</button>
          <button type="button" className={`btn ghost small ${curPathType==='bounce'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'bounce' })} title="Bouncy parabolic arcs — each bounce can be damped"><ArrowUpDown size={12}/> Bounce</button>
          <button type="button" className={`btn ghost small ${curPathType==='spiral'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'spiral' })} title="Spiral that winds inward — into the start point, or into the end handle"><Shell size={12}/> Spiral</button>
          <button type="button" className={`btn ghost small ${curPathType==='figure-8'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'figure-8' })} title="Figure-8 / infinity loop around the path"><InfinityIcon size={12}/> Figure 8</button>
          <button type="button" className={`btn ghost small ${curPathType==='lissajous'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'lissajous' })} title="Lissajous curve — two independent frequencies make loops and pretzels"><Activity size={12}/> Lissajous</button>
          <button type="button" className={`btn ghost small ${curPathType==='zigzag'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'zigzag' })} title="Sharp zigzag between start and end"><Zap size={12}/> Zigzag</button>
          <button type="button" className={`btn ghost small ${curPathType==='heart'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'heart' })} title="Heart-shaped loop"><Heart size={12}/> Heart</button>
          <button type="button" className={`btn ghost small ${curPathType==='polygon'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'polygon' })} title="Regular polygon with 3–10 sides — generalises diamond and triangle"><Hexagon size={12}/> Polygon</button>
          <button type="button" className={`btn ghost small ${curPathType==='pendulum'?'active':''}`} onClick={()=>onChange({ textMovePathType: 'pendulum' })} title="Pendulum swing — an arc under a pivot, always through the lowest point"><Undo2 size={12}/> Pendulum</button>
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

      {(curPathType === 'sine' || curPathType === 'sine-vertical') && <div className="motion-params">
        <label>Amplitude <input type="range" min={0} max={30} step={1} value={sineAmplitude} onChange={e=>onChange({ textMoveSineAmplitude: Number(e.target.value) })} /> <em>{sineAmplitude}%</em></label>
        <label>Frequency <input type="range" min={0.5} max={6} step={0.5} value={sineFrequency} onChange={e=>onChange({ textMoveSineFrequency: Number(e.target.value) })} /> <em>{sineFrequency} waves</em></label>
      </div>}

      {(curPathType === 'star' || curPathType === 'diamond' || curPathType === 'triangle') && <div className="motion-params">
        <label>Size <input type="range" min={5} max={40} step={1} value={circleRadius ?? 18} onChange={e=>onChange({ textMoveCircleRadius: Number(e.target.value) })} /> <em>{Math.round(circleRadius ?? 18)}%</em></label>
        {curPathType === 'star' && <>
          <label>Points <input type="range" min={3} max={8} step={1} value={starPoints} onChange={e=>onChange({ textMoveStarPoints: Number(e.target.value) })} /> <em>{starPoints}</em></label>
          <label>Inner <input type="range" min={0.2} max={0.8} step={0.05} value={starInnerRatio} onChange={e=>onChange({ textMoveStarInnerRatio: Number(e.target.value) })} /> <em>{starInnerRatio.toFixed(2)}</em></label>
        </>}
        <label>Rotation <input type="range" min={0} max={360} step={5} value={symbolRotation} onChange={e=>onChange({ textMoveSymbolRotation: Number(e.target.value) })} /> <em>{symbolRotation}°</em></label>
      </div>}

      {curPathType === 'bounce' && <div className="motion-params">
        <label>Height <input type="range" min={2} max={28} step={1} value={bounceHeight} onChange={e=>onChange({ textMoveBounceHeight: Number(e.target.value) })} /> <em>{bounceHeight}%</em></label>
        <label>Bounces <input type="range" min={1} max={8} step={1} value={bounceCount} onChange={e=>onChange({ textMoveBounceCount: Number(e.target.value) })} /> <em>{bounceCount}×</em></label>
        <label>Damping <input type="range" min={0} max={0.85} step={0.05} value={bounceDamping} onChange={e=>onChange({ textMoveBounceDamping: Number(e.target.value) })} /> <em>{bounceDamping.toFixed(2)}{bounceDamping>0.01 ? ' damped' : ' no damp'}</em></label>
      </div>}

      {curPathType === 'spiral' && <div className="motion-params">
        <label>Radius <input type="range" min={3} max={40} step={1} value={circleRadius ?? 18} onChange={e=>onChange({ textMoveCircleRadius: Number(e.target.value), textMovePathType: 'spiral' })} /> <em>{Math.round(circleRadius ?? 18)}%</em></label>
        <label>Turns <input type="range" min={0.25} max={4} step={0.25} value={circleTurns} onChange={e=>onChange({ textMoveCircleTurns: Number(e.target.value) })} /> <em>{circleTurns}×</em></label>
      </div>}

      {(curPathType === 'figure-8' || curPathType === 'heart') && <div className="motion-params">
        <label>Size <input type="range" min={5} max={40} step={1} value={circleRadius ?? 18} onChange={e=>onChange({ textMoveCircleRadius: Number(e.target.value), textMovePathType: curPathType })} /> <em>{Math.round(circleRadius ?? 18)}%</em></label>
        {curPathType === 'heart' && <label>Rotation <input type="range" min={0} max={360} step={5} value={symbolRotation} onChange={e=>onChange({ textMoveSymbolRotation: Number(e.target.value) })} /> <em>{symbolRotation}°</em></label>}
      </div>}

      {curPathType === 'lissajous' && <div className="motion-params">
        <label>Width <input type="range" min={3} max={40} step={1} value={sineAmplitude} onChange={e=>onChange({ textMoveSineAmplitude: Number(e.target.value) })} /> <em>{sineAmplitude}%</em></label>
        <label>Freq X <input type="range" min={1} max={6} step={1} value={sineFrequency} onChange={e=>onChange({ textMoveSineFrequency: Number(e.target.value) })} /> <em>{sineFrequency}</em></label>
        <label>Freq Y <input type="range" min={1} max={6} step={1} value={lissajousFreqY} onChange={e=>onChange({ textMoveLissajousFreqY: Number(e.target.value) })} /> <em>{lissajousFreqY}</em></label>
      </div>}

      {curPathType === 'zigzag' && <div className="motion-params">
        <label>Amplitude <input type="range" min={2} max={30} step={1} value={sineAmplitude} onChange={e=>onChange({ textMoveSineAmplitude: Number(e.target.value) })} /> <em>{sineAmplitude}%</em></label>
        <label>Zigs <input type="range" min={1} max={10} step={1} value={sineFrequency} onChange={e=>onChange({ textMoveSineFrequency: Number(e.target.value) })} /> <em>{sineFrequency}×</em></label>
      </div>}

      {curPathType === 'polygon' && <div className="motion-params">
        <label>Size <input type="range" min={5} max={40} step={1} value={circleRadius ?? 18} onChange={e=>onChange({ textMoveCircleRadius: Number(e.target.value) })} /> <em>{Math.round(circleRadius ?? 18)}%</em></label>
        <label>Sides <input type="range" min={3} max={10} step={1} value={starPoints} onChange={e=>onChange({ textMoveStarPoints: Number(e.target.value) })} /> <em>{starPoints}</em></label>
        <label>Rotation <input type="range" min={0} max={360} step={5} value={symbolRotation} onChange={e=>onChange({ textMoveSymbolRotation: Number(e.target.value) })} /> <em>{symbolRotation}°</em></label>
      </div>}

      {curPathType === 'pendulum' && <div className="motion-params">
        <label>Arm <input type="range" min={5} max={40} step={1} value={circleRadius ?? 20} onChange={e=>onChange({ textMoveCircleRadius: Number(e.target.value), textMovePathType: 'pendulum' })} /> <em>{Math.round(circleRadius ?? 20)}%</em></label>
      </div>}

      {curPathType === 'polyline' && <div className="motion-params polyline-params">
        <span className="polyline-info"><GitBranch size={12}/> {path?.length ?? 2} points — click on canvas to insert, drag points to move</span>
        <button type="button" className="btn ghost small" onClick={handlePolyAddMid}><Plus size={12}/> Add middle point</button>
        <button type="button" className="btn ghost small" disabled={!path || path.length<=2} onClick={()=>{ if(path && path.length>2){ const next=path.slice(0,-1); onChange({ textMovePath: next }) } }}><Minus size={12}/> Remove last</button>
        {path && path.length>2 && <div className="polyline-points-list">
          {path.map((pt, i) => <span key={i} className="polyline-point-tag">{i===0?'S':i===path.length-1?'E':String(i)}: {Math.round(pt[0])}%,{Math.round(pt[1])}% <button type="button" className="mini-x" onClick={()=>handlePolyRemove(i)} title="Remove this point">×</button></span>)}
        </div>}
      </div>}

      <div className="motion-type-row" style={{marginTop:8}}>
        <label className="check-label" style={{fontSize:'13px'}}>
          <input type="checkbox" checked={sinusEnabled} onChange={e=>onChange({ textMoveSinusUpDownEnabled: e.target.checked })} />
          <span><Check size={11}/></span>
          Sinus up/down overlay <small style={{opacity:.7}}>vertical wave on top of base path</small>
        </label>
        {sinusEnabled && <div className="motion-params" style={{marginLeft:12}}>
          <label>Up/down amp <input type="range" min={0} max={20} step={1} value={sinusAmplitude} onChange={e=>onChange({ textMoveSinusAmplitude: Number(e.target.value) })} /> <em>{sinusAmplitude}%</em></label>
          <label>Freq <input type="range" min={0.5} max={6} step={0.5} value={sinusFrequency} onChange={e=>onChange({ textMoveSinusFrequency: Number(e.target.value) })} /> <em>{sinusFrequency}</em></label>
        </div>}
      </div>

      <div className="motion-type-row">
        <label className="check-label" style={{fontSize:'13px'}}>
          <input type="checkbox" checked={rotateAlong} onChange={e=>onChange({ textMoveRotateAlongPath: e.target.checked })} />
          <span><Check size={11}/></span>
          Rotate along path <small style={{opacity:.7}}>caption turns to follow the path direction</small>
        </label>
      </div>


      <div
        ref={node => { containerRef.current = node; motionScaleRef(node) }}
        className={`text-motion-canvas ${drawing?'drawing':''} ${dragging?'dragging':''} type-${curPathType}`}
        style={{ background: background || '#222' }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        title={drawing ? 'Drawing path — drag to draw, release to finish' : curPathType==='freehand' ? 'Drag start/end handles or draw a freehand path' : 'Drag S/E handles — path preview updates'}
      >
        {src && !isVideo && <div className="motion-bg-blur" style={{ backgroundImage: `url(${src})`, filter: `blur(${backdropBlurPx(frameScale).toFixed(1)}px) brightness(0.88) saturate(1.2)` }} />}
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
        {(curPathType === 'polyline' || (curPathType === 'freehand' && path && path.length>2)) && path && path.map((pt,i)=> i===0 || i===path.length-1 ? null : <span key={i} className="motion-handle waypoint" style={{ left:`${pt[0]}%`, top:`${pt[1]}%`, width:'16px', height:'16px', fontSize:'9px', background:'rgba(90,140,255,0.9)', border:'1px solid white' }} title={`Point ${i}: ${Math.round(pt[0])}%, ${Math.round(pt[1])}% — drag to move`}><b>{i}</b></span>)}

        <span className="motion-caption" style={{ left:`${currentPos[0]}%`, top:`${currentPos[1]}%`, ...captionStyle, fontSize: scaleFrameFont(captionStyle.fontSize, frameScale), ...(captionTransform ? { transform: captionTransform } : {}) }}>{caption}</span>

        <em className="motion-hint">{drawing ? 'Drawing… release to set path' : curPathType==='polyline' ? `Polyline · ${path?.length ?? 2} points · click to insert · drag S/E/waypoints${sinusEnabled ? ' · sinus up/down' : ''}` : curPathType==='freehand' ? `Draw a path — S → E · drag waypoints to tweak${sinusEnabled ? ' · sinus up/down' : ''}` : `${curPathType} · ${curEasing}${sinusEnabled ? ' · sinus up/down' : ''} · drag S/E to adjust`}</em>
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

            <p className="motion-note"><small>Text moves during its visible window (textStart → textEnd). Duration determines speed (longer = slower). Predefined paths: straight, free draw, circle (radius & turns), sinus/bounce (height/damping), star/diamond/triangle/polyline. Damping makes each bounce smaller. Easing fades speed in/out: linear, fade-in (ease-in), fade-out (ease-out), in-out, smooth cubic.</small></p>
    </>}
  </div>
}
