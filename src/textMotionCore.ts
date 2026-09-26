// Stackable text effects — the JavaScript twin of backend/app/text_motion.py.
//
// Pure logic, no DOM and no imports, so the same file drives
//   * the live preview in the editor (src/TextMotionPreview.tsx feeds it a
//     layout measured in the browser), and
//   * the parity test (backend/tests/test_text_motion_twin.py runs it in Node
//     with the HarfBuzz layout from Python and compares every channel).
// Same registry (registry/text-motion.json), same maths, same pseudo-random
// numbers: that is what keeps the preview and the FFmpeg/libass render equal.
//
// Only erasable TypeScript syntax is used (no enums / namespaces / parameter
// properties) so `node --experimental-strip-types` can run it unchanged.

export type Phase = 'in' | 'hold' | 'out'
export type UnitLevel = 'char' | 'word' | 'line' | 'text'
export type Rgb = [number, number, number]

export interface EffectParam { name: string; label: string; type: 'number' | 'colour' | 'text' | 'select'; default: unknown; min?: number; max?: number; step?: number; unit?: string; options?: string[] }
export interface EffectDef {
  id: string
  label: string
  phase: Phase
  category: string
  symbol: string
  unit: UnitLevel
  duration?: number
  stagger?: number
  order?: string
  loop?: string
  tracks: Record<string, any>
  params?: EffectParam[]
  copies?: any[]
  content?: any
  caret?: { char: string; blink: number }
  clipPad?: [number, number]
  colourWipe?: { from: string; to: string; dir?: string }
  bg?: { mode: 'follow' | 'arrive' | 'leave'; from?: string; to?: string }
  needsBg?: boolean
  sync?: string
  approx?: string
  tags?: string[]
  legacy?: { slot: string; label: string }
  source?: string
  notes?: string
  units?: string[]
}
export interface Registry { version: number; categories: { id: string; label: string; symbol: string }[]; effects: EffectDef[]; presets: any[] }

/** One stored layer of MediaItem.textFx. */
export interface TextFxLayer {
  id?: string
  effect: string
  unit?: UnitLevel
  duration?: number
  delay?: number
  stagger?: number
  order?: string
  loop?: string
  intensity?: number
  params?: Record<string, unknown>
  muted?: boolean
  sync?: 'bg'
  range?: [number, number]
}

export interface Unit {
  level: string
  index: number
  text: string
  cx: number
  cy: number
  w: number
  h: number
  anc: Record<string, number>
  line: number
  c0: number
  c1: number
  col: number
  ncol: number
  word: number
}
export interface Layout { units: Record<string, Unit[]>; lines: string[]; lineH: number; em: number; align: string }
export interface BgChange { colourA: string; colourB: string; transition: string; start: number; time: number }
export interface MotionPath { points: [number, number][]; easing: string; rotateAlong?: boolean }
export interface Caption {
  stack: TextFxLayer[]
  em: number
  colour: string
  start: number
  end: number
  frameW: number
  frameH: number
  steady: number
  bg: BgChange | null
  motion: MotionPath | null
}

// ============================================================================
// Maths shared with text_motion.py (keep both in step)
// ============================================================================

export const LEVEL_ORDER: UnitLevel[] = ['char', 'word', 'line', 'text']
export const MUL = ['opacity', 'scale', 'sx', 'sy', 'fill', 'squash']
export const ADD = ['x', 'y', 'vx', 'vy', 'px', 'py', 'rz', 'rx', 'ry', 'skew', 'blur', 'spacing', 'line', 'gather']
export const MAXC = ['glow', 'depth', 'rgb', 'orbit']
export const CHANNELS = [...MUL, ...ADD, ...MAXC]
const MUL_SET = new Set(MUL)
const MAX_SET = new Set(MAXC)
export const NEUTRAL: Record<string, number> = {}
for (const c of MUL) NEUTRAL[c] = 1
for (const c of [...ADD, ...MAXC]) NEUTRAL[c] = 0

function outBounce (x: number) {
  const n1 = 7.5625, d1 = 2.75
  if (x < 1 / d1) return n1 * x * x
  if (x < 2 / d1) { x -= 1.5 / d1; return n1 * x * x + 0.75 }
  if (x < 2.5 / d1) { x -= 2.25 / d1; return n1 * x * x + 0.9375 }
  x -= 2.625 / d1
  return n1 * x * x + 0.984375
}

export const EASE: Record<string, (x: number) => number> = {
  linear: x => x,
  inQuad: x => x * x,
  outQuad: x => 1 - (1 - x) ** 2,
  inOutQuad: x => x < 0.5 ? 2 * x * x : 1 - (-2 * x + 2) ** 2 / 2,
  inCubic: x => x ** 3,
  outCubic: x => 1 - (1 - x) ** 3,
  inOutCubic: x => x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2,
  inQuart: x => x ** 4,
  outQuart: x => 1 - (1 - x) ** 4,
  inOutSine: x => -(Math.cos(Math.PI * x) - 1) / 2,
  outSine: x => Math.sin(x * Math.PI / 2),
  inBack: x => 2.70158 * x ** 3 - 1.70158 * x ** 2,
  outBack: x => 1 + 2.70158 * (x - 1) ** 3 + 1.70158 * (x - 1) ** 2,
  outElastic: x => x <= 0 ? 0 : x >= 1 ? 1 : 2 ** (-10 * x) * Math.sin((x * 10 - 0.75) * (2 * Math.PI / 3)) + 1,
  outBounce,
}

export const PATH_EASE: Record<string, (p: number) => number> = {
  linear: p => p,
  'ease-in': p => p * p,
  'ease-out': p => 1 - (1 - p) * (1 - p),
  'ease-in-out': p => p < 0.5 ? 2 * p * p : 1 - 2 * (1 - p) * (1 - p),
  smooth: p => p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2,
}

/** Deterministic pseudo-random number in [0, 1), bit-identical to Python. */
export function hash01 (...values: number[]) {
  let h = 2166136261 >>> 0
  for (const v of values) {
    h = (h ^ (Math.trunc(v) >>> 0)) >>> 0
    h = Math.imul(h, 16777619) >>> 0
  }
  h = (h ^ (h >>> 13)) >>> 0
  h = Math.imul(h, 0x5BD1E995) >>> 0
  h = (h ^ (h >>> 15)) >>> 0
  return h / 4294967296
}

const HEX = /^#[0-9a-fA-F]{6}$/
export const isHexColour = (v: unknown): v is string => typeof v === 'string' && HEX.test(v)
export function rgb (hex: string): Rgb {
  const h = String(hex).replace('#', '')
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
}
export function hexOf (c: number[]) {
  return '#' + c.slice(0, 3).map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('')
}
function mix (a: any, b: any, f: number): any {
  if (Array.isArray(a)) return a.map((x: number, i: number) => x + (b[i] - x) * f)
  return a + (b - a) * f
}
const clamp = (v: number, lo: number, hi: number) => v < lo ? lo : v > hi ? hi : v
const clamp01 = (v: number) => v < 0 ? 0 : v > 1 ? 1 : v
/** Python float() semantics for the values the engine reads. */
export function num (value: unknown, dflt: number | null): number | null {
  if (value === null || value === undefined || value === '') return dflt
  const n = typeof value === 'number' ? value : typeof value === 'boolean' ? (value ? 1 : 0) : Number(String(value).trim())
  return Number.isFinite(n) ? n : dflt
}
const radians = (deg: number) => deg * Math.PI / 180

function luminance (c: Rgb) {
  const ch = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])
}
export function contrastRatio (a: Rgb, b: Rgb) {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}
/** Readable text colour on a background (WCAG AA 4.5:1), like text_motion.contrast_colour. */
export function contrastColour (background: Rgb, preferred: Rgb | null = null): Rgb {
  if (preferred && contrastRatio(preferred, background) >= 4.5) return preferred
  const light: Rgb = [255, 255, 255], dark: Rgb = [17, 17, 17]
  return contrastRatio(light, background) >= contrastRatio(dark, background) ? light : dark
}

// ============================================================================
// Layout
// ============================================================================

export function cleanLines (text: string): string[] {
  const lines = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n').map(l => l.trim())
  while (lines.length && !lines[lines.length - 1]) lines.pop()
  while (lines.length && !lines[0]) lines.shift()
  return lines.length ? lines : ['']
}

const isSpace = (ch: string) => /\s/.test(ch)

function unit (level: string, index: number, text: string, cx: number, cy: number, w: number, h: number, extra: Partial<Unit> = {}): Unit {
  return { level, index, text, cx, cy, w, h, anc: {}, line: 0, c0: 0, c1: 0, col: 0, ncol: 1, word: -1, ...extra }
}

/** Rest geometry — identical to build_layout() in text_motion.py. */
export function buildLayout (lines: string[], advances: (line: string) => number[], em: number, lineH: number, cx: number, cy: number, align = 'center'): Layout {
  const units: Record<string, Unit[]> = { char: [], word: [], line: [], text: [] }
  const advPerLine = lines.map(line => advances(line))
  const widths = advPerLine.map(a => a.reduce((s, v) => s + v, 0))
  const blockW = widths.length ? Math.max(...widths) : 0
  const n = lines.length
  const top = cy - lineH * n / 2
  const whole = unit('text', 0, lines.join('\n'), cx, cy, blockW, lineH * n)
  whole.anc = { text: 0 }
  units.text.push(whole)
  lines.forEach((line, li) => {
    const adv = advPerLine[li]
    const width = widths[li]
    const left = align !== 'left' ? cx - width / 2 : cx - blockW / 2
    const lcy = top + (li + 0.5) * lineH
    const lu = unit('line', li, line, left + width / 2, lcy, width, lineH, { line: li, c0: 0, c1: line.length })
    lu.anc = { line: li, text: 0 }
    units.line.push(lu)
    const pens: number[] = []
    let pen = 0
    for (let i = 0; i < line.length; i++) { pens.push(pen); pen += i < adv.length ? adv[i] : 0 }
    const charIds: Record<number, number> = {}
    let wordChars: number[] = []
    const closeWord = () => {
      if (!wordChars.length) return
      const i0 = wordChars[0], i1 = wordChars[wordChars.length - 1]
      const x0 = left + pens[i0]
      const x1 = left + pens[i1] + adv[i1]
      const wi = units.word.length
      const wu = unit('word', wi, line.slice(i0, i1 + 1), (x0 + x1) / 2, lcy, x1 - x0, lineH, { line: li, c0: i0, c1: i1 + 1, word: wi })
      wu.anc = { word: wi, line: li, text: 0 }
      units.word.push(wu)
      for (const ci of wordChars) {
        const cu = units.char[charIds[ci]]
        Object.assign(cu.anc, { word: wi, line: li, text: 0 })
        cu.word = wi
      }
      wordChars = []
    }
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (isSpace(ch)) { closeWord(); continue }
      const a = i < adv.length ? adv[i] : 0
      const idx = units.char.length
      const cu = unit('char', idx, ch, left + pens[i] + a / 2, lcy, a, lineH, { line: li, c0: i, c1: i + 1, col: i, ncol: line.length })
      cu.anc = { char: idx }
      charIds[i] = idx
      units.char.push(cu)
      wordChars.push(i)
    }
    closeWord()
  })
  return { units, lines, lineH, em, align }
}

// ============================================================================
// Background colour change — the exact shape of FFmpeg's xfade
// ============================================================================

export const XFADE_IDS: Record<string, string> = {
  'fade': 'fade', 'fade black': 'fadeblack', 'fade white': 'fadewhite', 'fade grays': 'fadegrays', 'fade fast': 'fadefast',
  'fade slow': 'fadeslow', 'dissolve': 'dissolve', 'distance': 'distance', 'pixelize': 'pixelize', 'h blur': 'hblur',
  'wipe left': 'wipeleft', 'wipe right': 'wiperight', 'wipe up': 'wipeup', 'wipe down': 'wipedown', 'wipe top-left': 'wipetl',
  'wipe top-right': 'wipetr', 'wipe bottom-left': 'wipebl', 'wipe bottom-right': 'wipebr', 'slide left': 'slideleft',
  'slide right': 'slideright', 'slide up': 'slideup', 'slide down': 'slidedown', 'smooth left': 'smoothleft',
  'smooth right': 'smoothright', 'smooth up': 'smoothup', 'smooth down': 'smoothdown', 'circle crop': 'circlecrop',
  'rectangle crop': 'rectcrop', 'circle open': 'circleopen', 'circle close': 'circleclose', 'vertical open': 'vertopen',
  'vertical close': 'vertclose', 'horizontal open': 'horzopen', 'horizontal close': 'horzclose', 'radial': 'radial',
  'diagonal top-left': 'diagtl', 'diagonal top-right': 'diagtr', 'diagonal bottom-left': 'diagbl',
  'diagonal bottom-right': 'diagbr', 'horizontal left slice': 'hlslice', 'horizontal right slice': 'hrslice',
  'vertical up slice': 'vuslice', 'vertical down slice': 'vdslice', 'squeeze horizontal': 'squeezeh',
  'squeeze vertical': 'squeezev', 'zoom in': 'zoomin', 'horizontal left wind': 'hlwind', 'horizontal right wind': 'hrwind',
  'vertical up wind': 'vuwind', 'vertical down wind': 'vdwind', 'cover left': 'coverleft', 'cover right': 'coverright',
  'cover up': 'coverup', 'cover down': 'coverdown', 'reveal left': 'revealleft', 'reveal right': 'revealright',
  'reveal up': 'revealup', 'reveal down': 'revealdown',
}
const XFADE_VALUES = new Set(Object.values(XFADE_IDS))

export function xfadeId (label: string) {
  const base = String(label || '').trim().split('(')[0].trim().toLowerCase()
  if (base in XFADE_IDS) return XFADE_IDS[base]
  if (XFADE_VALUES.has(base)) return base
  return base.startsWith('gl') ? 'gl' : 'fade'
}

/** How text can follow this transition: rect | circle | poly | mix (crossfade). */
export function bgShapeKind (transition: string) {
  const t = xfadeId(transition)
  if (t === 'circleopen' || t === 'circleclose') return 'circle'
  if (['radial', 'diagtl', 'diagtr', 'diagbl', 'diagbr'].includes(t)) return 'poly'
  if (['fade', 'fadeblack', 'fadewhite', 'fadegrays', 'fadefast', 'fadeslow', 'dissolve', 'distance', 'pixelize', 'hblur', 'circlecrop', 'rectcrop', 'zoomin', 'gl'].includes(t)) return 'mix'
  return 'rect'
}

function smoothstep (e0: number, e1: number, x: number) {
  const t = e1 !== e0 ? clamp01((x - e0) / (e1 - e0)) : (x >= e1 ? 1 : 0)
  return t * t * (3 - 2 * t)
}

export type Region =
  | { kind: 'none' } | { kind: 'all' } | { kind: 'mix'; f: number }
  | { kind: 'rect'; rect: [number, number, number, number]; inside: boolean }
  | { kind: 'circle'; cx: number; cy: number; r: number; inside: boolean }
  | { kind: 'poly'; points: [number, number][]; inside: boolean }

/** Where colour B is at transition progress p (0 → 1) — bg_region() in text_motion.py. */
export function bgRegion (transition: string, pIn: number, w: number, h: number): Region {
  const t = xfadeId(transition)
  const p = clamp01(pIn)
  const pf = 1 - p
  if (p <= 0) return { kind: 'none' }
  if (p >= 1) return { kind: 'all' }
  const R = (x0: number, y0: number, x1: number, y1: number, inside = true): Region => ({ kind: 'rect', rect: [x0, y0, x1, y1], inside })
  if (['wipeleft', 'slideleft', 'coverleft', 'revealleft'].includes(t)) return R(w * pf, 0, w, h)
  if (['wiperight', 'slideright', 'coverright', 'revealright'].includes(t)) return R(0, 0, w * p, h)
  if (['wipeup', 'slideup', 'coverup', 'revealup'].includes(t)) return R(0, h * pf, w, h)
  if (['wipedown', 'slidedown', 'coverdown', 'revealdown'].includes(t)) return R(0, 0, w, h * p)
  if (t === 'wipetl') return R(0, 0, w * pf, h * pf, false)
  if (t === 'wipetr') return R(w * p, 0, w, h * pf, false)
  if (t === 'wipebl') return R(0, h * p, w * pf, h, false)
  if (t === 'wipebr') return R(w * p, h * p, w, h, false)
  if (t === 'smoothleft' || t === 'hlwind') return R(w * clamp01(1.5 - 2 * p), 0, w, h)
  if (t === 'smoothright' || t === 'hrwind') return R(0, 0, w * clamp01(2 * p - 0.5), h)
  if (t === 'smoothup' || t === 'vuwind') return R(0, h * clamp01(1.5 - 2 * p), w, h)
  if (t === 'smoothdown' || t === 'vdwind') return R(0, 0, w, h * clamp01(2 * p - 0.5))
  if (t === 'hlslice') return R(w * clamp01(1.25 - 1.5 * p), 0, w, h)
  if (t === 'hrslice') return R(0, 0, w * clamp01(1.5 * p - 0.25), h)
  if (t === 'vuslice') return R(0, h * clamp01(1.25 - 1.5 * p), w, h)
  if (t === 'vdslice') return R(0, 0, w, h * clamp01(1.5 * p - 0.25))
  if (t === 'vertopen') { const hw = w / 2 * clamp01(2 * p - 0.5); return R(w / 2 - hw, 0, w / 2 + hw, h) }
  if (t === 'vertclose') { const hw = w / 2 * clamp01(1.5 - 2 * p); return R(w / 2 - hw, 0, w / 2 + hw, h, false) }
  if (t === 'horzopen') { const hh = h / 2 * clamp01(2 * p - 0.5); return R(0, h / 2 - hh, w, h / 2 + hh) }
  if (t === 'horzclose') { const hh = h / 2 * clamp01(1.5 - 2 * p); return R(0, h / 2 - hh, w, h / 2 + hh, false) }
  if (t === 'squeezeh') { const hh = h / 2 * pf; return R(0, h / 2 - hh, w, h / 2 + hh, false) }
  if (t === 'squeezev') { const hw = w / 2 * pf; return R(w / 2 - hw, 0, w / 2 + hw, h, false) }
  if (t === 'circleopen') { const z = Math.hypot(w / 2, h / 2); return { kind: 'circle', cx: w / 2, cy: h / 2, r: Math.max(0, z * (3 * p - 1)), inside: true } }
  if (t === 'circleclose') { const z = Math.hypot(w / 2, h / 2); return { kind: 'circle', cx: w / 2, cy: h / 2, r: Math.max(0, z * (2 - 3 * p)), inside: false } }
  if (t === 'radial') {
    const theta = 0.5 + (0.5 - p) * 2.5 * Math.PI
    if (theta >= Math.PI) return { kind: 'none' }
    const start = Math.max(-Math.PI, theta)
    const big = Math.hypot(w, h)
    const pts: [number, number][] = [[w / 2, h / 2]]
    const steps = 48
    for (let i = 0; i <= steps; i++) { const a = start + (Math.PI - start) * i / steps; pts.push([w / 2 + big * Math.sin(a), h / 2 + big * Math.cos(a)]) }
    return { kind: 'poly', points: pts, inside: true }
  }
  if (['diagtl', 'diagtr', 'diagbl', 'diagbr'].includes(t)) {
    const c = 1.5 - 2 * p
    if (c >= 1) return { kind: 'none' }
    if (c <= 0) return { kind: 'all' }
    const steps = 40
    const uv: [number, number][] = [[1, 1], [c, 1]]
    for (let i = 1; i < steps; i++) { const uu = c + (1 - c) * i / steps; uv.push([uu, c / uu]) }
    uv.push([1, c])
    const fx = t === 'diagtr' || t === 'diagbr', fy = t === 'diagbl' || t === 'diagbr'
    return { kind: 'poly', points: uv.map(([u, v]) => [w * (fx ? 1 - u : u), h * (fy ? 1 - v : v)] as [number, number]), inside: true }
  }
  if (t === 'zoomin') return { kind: 'mix', f: 1 - smoothstep(0, 0.5, pf) }
  if (t === 'fadeblack' || t === 'fadewhite' || t === 'fadegrays') return { kind: 'mix', f: smoothstep(0.35, 0.65, p) }
  if (t === 'circlecrop' || t === 'rectcrop') return { kind: 'mix', f: p >= 0.5 ? 1 : 0 }
  return { kind: 'mix', f: p }
}

// ============================================================================
// Compilation
// ============================================================================

interface ResolvedLayer {
  src: number
  layer: TextFxLayer
  fx: EffectDef
  phase: Phase
  unit: string
  duration: number | null
  delay: number
  stagger: number
  order: string
  loop: string
  intensity: number
  synced: boolean
  seed: number
  range: [number, number] | null
  ranks: Map<number, number>
  maxRank: number
  param: (name: string, dflt?: unknown) => unknown
}

export interface Ctx {
  cap: Caption
  layout: Layout
  stack: ResolvedLayer[]
  gran: string
  levels: string[]
  units: Record<string, Unit[]>
  anc: Record<string, Map<number, number>>
  content: Record<string, ResolvedLayer>
  warnings: string[]
  conflicts: Record<number, string>
  base: Rgb
  orgLevel: string | null
}

export interface State {
  x: number
  y: number
  text: string
  tot: Record<string, number>
  colour: Rgb
  clip: [number, number, number, number] | null
  org: [number, number] | null
  split: { a: Rgb; b: Rgb; region: Region; kind: string; f: number; mask?: string } | null
}

export function ranks (n: number, order: string, seed: number): number[] {
  const idx = Array.from({ length: n }, (_, i) => i)
  if (order === 'reverse') return idx.map(i => n - 1 - i)
  if (order === 'center' || order === 'edges') {
    const c = (n - 1) / 2
    const dist = idx.map(i => Math.abs(i - c))
    const mx = dist.length ? Math.max(...dist) : 0
    return order === 'center' ? dist : dist.map(d => mx - d)
  }
  if (order === 'random') {
    const perm = idx.slice().sort((a, b) => (hash01(seed, a) - hash01(seed, b)) || (a - b))
    const pos = new Map(perm.map((v, k) => [v, k]))
    return idx.map(i => pos.get(i) as number)
  }
  return idx
}

export const levelRank = (level: string) => level.startsWith('g') ? 1.5 : LEVEL_ORDER.indexOf(level as UnitLevel)

function paramOf (layer: TextFxLayer, fx: EffectDef) {
  return (name: string, dflt?: unknown) => {
    if (layer.params && name in layer.params) return layer.params[name]
    for (const p of fx.params || []) if (p.name === name) return p.default === undefined ? dflt : p.default
    return dflt
  }
}

export function compileStack (cap: Caption, layout: Layout, effects: Record<string, EffectDef>): Ctx {
  const warnings: string[] = []
  const conflicts: Record<number, string> = {}
  const resolved: ResolvedLayer[] = []
  const units: Record<string, Unit[]> = {}
  for (const k of Object.keys(layout.units)) units[k] = layout.units[k].slice()
  const groupAnc: Record<string, Map<number, number>> = {}
  const nWords = units.word.length
  cap.stack.forEach((layer, i) => {
    if (layer.muted) return
    const fx = effects[layer.effect]
    if (!fx) return
    const phase = (fx.phase || 'hold') as Phase
    let unitName: string = layer.unit || fx.unit || 'text'
    if (Array.isArray(fx.units) && fx.units.length && !fx.units.includes(unitName)) unitName = fx.unit || 'text'
    const needsBg = Boolean(fx.needsBg) || Boolean(fx.bg)
    const synced = (layer.sync === 'bg' || fx.sync === 'bg' || needsBg) && cap.bg !== null
    if (needsBg && cap.bg === null) {
      warnings.push(`'${fx.label}' needs a text frame with a colour change (background A → B)`)
      conflicts[i] = 'needs a colour change'
      return
    }
    if (layer.sync === 'bg' && cap.bg === null && !needsBg) conflicts[i] = 'no colour change to sync to'
    let loop = layer.loop || fx.loop || 'loop'
    if (phase !== 'hold') loop = 'once'
    let rng: [number, number] | null = Array.isArray(layer.range) && layer.range.length === 2 ? [Math.min(layer.range[0], layer.range[1]), Math.max(layer.range[0], layer.range[1])] : null
    if (rng && nWords === 0) rng = null
    if (rng) {
      rng = [Math.min(rng[0], nWords - 1), Math.min(rng[1], nWords - 1)]
      if (unitName === 'line' || unitName === 'text') {
        const gname = `g${i}`
        const ws = units.word.slice(rng[0], rng[1] + 1)
        const x0 = Math.min(...ws.map(u => u.cx - u.w / 2)), x1 = Math.max(...ws.map(u => u.cx + u.w / 2))
        const y0 = Math.min(...ws.map(u => u.cy - u.h / 2)), y1 = Math.max(...ws.map(u => u.cy + u.h / 2))
        const g = unit(gname, 0, ws.map(u => u.text).join(' '), (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, { line: ws[0].line })
        g.anc = { [gname]: 0, line: ws[0].line, text: 0 }
        units[gname] = [g]
        const m = new Map<number, number>()
        for (let k = rng[0]; k <= rng[1]; k++) m.set(k, 0)
        groupAnc[gname] = m
        unitName = gname
      }
    }
    const duration = layer.duration !== undefined && layer.duration !== null ? layer.duration : num(fx.duration, null)
    const stagger = layer.stagger !== undefined && layer.stagger !== null ? layer.stagger : (fx.stagger || 0)
    const order = layer.order || fx.order || 'forward'
    resolved.push({
      src: i, layer, fx, phase, unit: unitName, duration, delay: layer.delay || 0, stagger, order, loop,
      intensity: layer.intensity === undefined || layer.intensity === null ? 1 : layer.intensity, synced,
      seed: resolved.length * 104729 + 13, range: rng, ranks: new Map(), maxRank: 0, param: paramOf(layer, fx),
    })
  })
  const content: Record<string, ResolvedLayer> = {}
  for (const L of resolved) {
    if (L.fx.content) {
      const prev = content[L.phase]
      if (prev) {
        warnings.push(`'${L.fx.label}' replaces '${prev.fx.label}' (both rewrite the text in the ${L.phase} phase)`)
        conflicts[prev.src] = `replaced by ${L.fx.label}`
      }
      content[L.phase] = L
    }
  }
  const whole = Object.values(content).filter(L => ['count', 'countdown', 'swap'].includes(L.fx.content.type))
  if (whole.length) {
    for (const L of resolved) {
      if (L.unit !== 'text') {
        warnings.push(`'${whole[0].fx.label}' rewrites the whole text, so '${L.fx.label}' runs on the whole text instead of per ${L.unit}`)
        conflicts[L.src] = `whole text (${whole[0].fx.label})`
        L.unit = 'text'
      }
    }
  }
  let need: string[] = ['line']
  for (const L of resolved) need.push(L.unit.startsWith('g') ? 'word' : L.unit)
  for (const L of Object.values(content)) if (L.fx.content.type === 'scramble' || L.fx.content.type === 'flap' || L.fx.content.type === 'roll') need.push('char')
  if (resolved.some(L => L.fx.caret)) need.push('char')
  if (whole.length) need = ['text']
  const gran = need.reduce((a, b) => levelRank(b) < levelRank(a) ? b : a)
  const groups = Object.keys(units).filter(k => k.startsWith('g')).sort()
  const levels = [
    ...(['char', 'word'] as string[]).filter(l => levelRank(l) >= levelRank(gran)),
    ...groups.filter(g => levelRank(g) >= levelRank(gran)),
    ...(['line', 'text'] as string[]).filter(l => levelRank(l) >= levelRank(gran)),
  ]
  for (const L of resolved) {
    const idx = units[L.unit].filter(u => inRange(L, u)).map(u => u.index)
    const r = ranks(idx.length, L.order, L.seed)
    L.ranks = new Map(idx.map((ui, k) => [ui, r[k]]))
    L.maxRank = r.length ? Math.max(...r) : 0
  }
  let orgLevel: string | null = null
  for (const L of resolved) {
    const tr = L.fx.tracks || {}
    if (('rx' in tr || 'ry' in tr) && levelRank(L.unit) > levelRank(gran)) {
      if (orgLevel === null || levelRank(L.unit) > levelRank(orgLevel)) orgLevel = L.unit
    }
  }
  return { cap, layout, stack: resolved, gran, levels, units, anc: groupAnc, content, warnings, conflicts, base: rgb(cap.colour), orgLevel }
}

function inRange (L: ResolvedLayer, u: Unit) {
  const rng = L.range
  if (!rng || L.unit.startsWith('g')) return true
  if (u.level === 'char' || u.level === 'word') return rng[0] <= u.word && u.word <= rng[1]
  return true
}

function ancestor (ctx: Ctx, e: Unit, level: string): Unit | null {
  if (level === e.level) return e
  if (level.startsWith('g')) {
    const w = e.level === 'char' || e.level === 'word' ? e.word : -1
    const gi = ctx.anc[level]?.get(w)
    return gi === undefined ? null : ctx.units[level][gi]
  }
  const idx = e.anc[level]
  return idx === undefined ? null : ctx.units[level][idx]
}

export function layerWindow (ctx: Ctx, L: ResolvedLayer): [number, number] {
  if (L.synced && ctx.cap.bg) return [ctx.cap.bg.start, ctx.cap.bg.start + ctx.cap.bg.time]
  return [ctx.cap.start, ctx.cap.end]
}

const pmod = (a: number, m: number) => ((a % m) + m) % m

export function localU (ctx: Ctx, L: ResolvedLayer, rank: number, t: number): [number, number] {
  const [ws, we] = layerWindow(ctx, L)
  const k = L.stagger
  const R = L.maxRank
  if (L.phase === 'in' || L.phase === 'out') {
    let total = L.duration !== null ? L.duration : (L.synced ? we - ws : 0.5)
    total = Math.max(total, 1e-3)
    const unitD = total / (1 + k * R)
    const t0 = L.phase === 'in' ? ws + L.delay + rank * k * unitD : we - L.delay - (R - rank) * k * unitD - unitD
    return [clamp01((t - t0) / unitD), t - t0]
  }
  if (L.loop === 'once') {
    const spanEnd = we - (L.synced ? 0 : Math.min(ctx.cap.steady, Math.max(0, we - ws - 0.05)))
    let span = L.duration !== null ? L.duration : spanEnd - ws - L.delay
    span = Math.max(span, 1e-3)
    const unitD = span / (1 + k * R)
    const t0 = ws + L.delay + rank * k * unitD
    return [clamp01((t - t0) / unitD), t - t0]
  }
  const period = Math.max(L.duration !== null ? L.duration : 2, 0.05)
  const t0 = ws + L.delay + rank * k * period
  if (t < t0 || (L.synced && t > we)) return [0, t - t0]
  let w = (t - t0) / period
  if (L.loop === 'pingpong') { w = pmod(w, 2); return [w <= 1 ? w : 2 - w, t - t0] }
  return [pmod(w, 1), t - t0]
}

export function colourToken (ctx: Ctx, value: string, base: Rgb): Rgb {
  const v = String(value || '').trim()
  if (v === 'base' || !v) return base
  const bg = ctx.cap.bg
  if (v === 'bgA' || v === 'bgB' || v === 'contrastA' || v === 'contrastB') {
    if (!bg) return base
    const a = rgb(bg.colourA), b = rgb(bg.colourB)
    return ({ bgA: a, bgB: b, contrastA: contrastColour(a, base), contrastB: contrastColour(b, base) } as Record<string, Rgb>)[v]
  }
  if (HEX.test(v)) return rgb(v)
  return base
}

function resolveValue (ctx: Ctx, L: ResolvedLayer, value: any, base: Rgb, useed: number, key: number): any {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'rand' in value) {
    const [a, b] = value.rand
    return a + (b - a) * hash01(useed, L.seed, key, 99)
  }
  if (typeof value === 'string') {
    let v: any = value
    if (v.startsWith('$')) {
      v = L.param(v.slice(1), 0)
      if (typeof v !== 'string') return num(v, 0)
    }
    return colourToken(ctx, v, base)
  }
  return value
}

function keys (ctx: Ctx, L: ResolvedLayer, ks: any[], u: number, base: Rgb, useed: number): any {
  if (u <= ks[0][0]) return resolveValue(ctx, L, ks[0][1], base, useed, 0)
  for (let k = 1; k < ks.length; k++) {
    const u0 = ks[k - 1][0], v0 = ks[k - 1][1]
    const u1 = ks[k][0], v1 = ks[k][1]
    if (u <= u1) {
      let f = u1 <= u0 ? 0 : (u - u0) / (u1 - u0)
      if (ks[k].length > 2) f = (EASE[ks[k][2]] || EASE.linear)(f)
      return mix(resolveValue(ctx, L, v0, base, useed, k - 1), resolveValue(ctx, L, v1, base, useed, k), f)
    }
  }
  return resolveValue(ctx, L, ks[ks.length - 1][1], base, useed, ks.length - 1)
}

function pnum (L: ResolvedLayer, v: unknown, dflt: number): number {
  let value = v
  if (typeof value === 'string' && value.startsWith('$')) value = L.param(value.slice(1), dflt)
  const n = num(value, dflt)
  return n === null ? dflt : n
}

function generator (ctx: Ctx, L: ResolvedLayer, spec: any, u: number, tl: number, useed: number): number {
  let v: number
  if (spec.sine) {
    const g = spec.sine
    v = pnum(L, g.base ?? 0, 0) + pnum(L, g.amp, 0) * Math.sin(2 * Math.PI * (u + Number(g.phase ?? 0)))
  } else if (spec.noise) {
    const g = spec.noise
    const step = Math.floor(tl * Number(g.rate))
    v = Number(spec.base ?? 0) + pnum(L, g.amp, 0) * (2 * hash01(step, Math.trunc(g.seed ?? 1), useed, L.seed) - 1)
  } else if (spec.flicker) {
    const g = spec.flicker
    const ramp = g.ramp
    if (ramp === 'up' && u <= 0) return 0
    if (ramp === 'up' && u >= 1) return 1
    if (ramp === 'down' && u <= 0) return 1
    if (ramp === 'down' && u >= 1) return 0
    let on = Number(g.on)
    if (ramp === 'up') on = on + (1 - on) * u * u
    else if (ramp === 'down') on = on * (1 - u) ** 1.5
    const step = Math.floor(tl * Number(g.rate))
    v = hash01(step, useed, L.seed, 5) < on ? 1 : Number(g.low ?? 0)
  } else if (spec.bounce) {
    const g = spec.bounce
    const height = clamp(pnum(L, g.height, 12), 0, 30) / 100
    const n = Math.trunc(Math.floor(clamp(pnum(L, g.bounces, 3), 1, 8) + 0.5))
    const d = clamp(pnum(L, g.damping, 0.35), 0, 0.95)
    const pu = clamp01(u)
    const idx = Math.min(n - 1, Math.trunc(Math.floor(pu * n)))
    const s = pu < 1 ? pu * n - idx : 1
    v = -height * (1 - d) ** idx * 4 * s * (1 - s)
  } else if (spec.physics) {
    // Ballistics over the layer's own progress: s = clamp01(u)·time seconds of
    // flight, then v = base + velocity·s + ½·gravity·s². Pick velocity and
    // gravity so the curve ends at 0 and it is a settle (in-phase); give it a
    // large gravity and it is an accelerating exit (out-phase). Twin of the
    // "physics" branch in text_motion.py.
    const g = spec.physics
    const s = clamp01(u) * Math.max(0.05, pnum(L, g.time ?? 1, 1))
    v = pnum(L, g.base ?? 0, 0) + pnum(L, g.velocity ?? 0, 0) * s + 0.5 * pnum(L, g.gravity ?? 0, 0) * s * s
  } else {
    v = 0
  }
  if (spec.env) v *= keys(ctx, L, spec.env, u, ctx.base, useed)
  return v
}

export function pathPoint (points: [number, number][], f: number): [number, number] {
  if (!points.length) return [50, 50]
  if (points.length === 1) return points[0]
  const seg: number[] = []
  for (let i = 0; i < points.length - 1; i++) seg.push(Math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]))
  const total = seg.reduce((a, b) => a + b, 0)
  if (total <= 1e-9) return points[0]
  let target = clamp01(f) * total
  for (let i = 0; i < seg.length; i++) {
    const s = seg[i]
    if (target <= s || i === seg.length - 1) {
      const g = s <= 0 ? 0 : Math.min(1, target / s)
      const [x0, y0] = points[i], [x1, y1] = points[i + 1]
      return [x0 + (x1 - x0) * g, y0 + (y1 - y0) * g]
    }
    target -= s
  }
  return points[points.length - 1]
}

function groupThousands (n: number, sep: string) {
  const s = String(n)
  let out = ''
  for (let i = 0; i < s.length; i++) {
    if (i && (s.length - i) % 3 === 0) out += sep
    out += s[i]
  }
  return out
}

function contentOf (ctx: Ctx, L: ResolvedLayer, e: Unit, u: number, tl: number, t: number, useed: number): [string, Record<string, number>] {
  const c = L.fx.content
  const kind = c.type
  if (kind === 'scramble') {
    const mode = c.mode || 'during'
    const active = mode === 'during' ? (u > 0 && u < 1) : mode === 'pending' ? u <= 0 : u > 0
    if (active && e.text.trim()) {
      const cs: string = c.charset || '#@$%&*'
      const step = Math.floor(tl * Number(c.rate ?? 18))
      let out = ''
      for (let k = 0; k < e.text.length; k++) {
        const ch = e.text[k]
        out += isSpace(ch) ? ch : cs[Math.trunc(hash01(step, useed, 3, k) * cs.length)]
      }
      return [out, {}]
    }
    return [e.text, {}]
  }
  if (kind === 'flap') {
    if (u > 0 && u < 1) {
      const flips = Math.trunc(c.flips ?? 8)
      const pos = u * flips
      const n = Math.trunc(pos), p = pos - Math.trunc(pos)
      const cs: string = c.charset || 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
      const ch = cs[Math.trunc(hash01(n, useed, 11) * cs.length)]
      return [ch, { sy: Math.max(0.12, Math.abs(Math.cos(Math.PI * p)) ** 0.7) }]
    }
    return [e.text, {}]
  }
  if (kind === 'roll') {
    // Slot machine / rolodex: each unit cycles through the charset while the
    // current glyph slides up into place (y offset + a slight squash). The
    // y override rides the add-channel support of the mods merge.
    if (u > 0 && u < 1) {
      const rolls = Math.trunc(c.rolls ?? 7)
      const pos = u * rolls
      const n = Math.trunc(pos), p = pos - Math.trunc(pos)
      const cs: string = c.charset || 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
      const ch = cs[Math.trunc(hash01(n, useed, 17) * cs.length)]
      return [ch, { y: (1 - p) * 0.55, sy: 0.6 + 0.4 * p }]
    }
    return [e.text, {}]
  }
  if (kind === 'swap') {
    // Phrase swap (Remotion Value Swap / CapCut text swap): the unit's text
    // cycles through a pipe-separated phrase list; every swap slides the new
    // phrase in from just below with a quick fade. Timed by tl (seconds since
    // the unit's local start), so it runs for the whole layer window.
    let raw = ''
    if (typeof c.phrases === 'string' && c.phrases.startsWith('$')) raw = String(L.param(c.phrases.slice(1), '') ?? '')
    else if (typeof c.phrases === 'string') raw = c.phrases
    else if (Array.isArray(c.phrases)) raw = c.phrases.filter((s: unknown) => typeof s === 'string').join('|')
    const phrases = raw.split('|').map(s => s.trim()).filter(s => s.length)
    if (phrases.length) {
      const every = Math.max(0.05, pnum(L, c.every ?? 0.45, 0.45))
      const tt = Math.max(0, tl)
      const slot = Math.floor(tt / every)
      const p = tt / every - slot
      const q = Math.min(1, p * 3)
      return [phrases[slot % phrases.length], { y: (1 - q) * 0.35, opacity: q }]
    }
    return [e.text, {}]
  }
  if (kind === 'count') {
    const easing = String(L.param('easing', c.easing || 'linear') || 'linear')
    const f = (EASE[easing] || EASE.linear)(u)
    const a = pnum(L, L.param('from', 0), 0)
    const b = pnum(L, L.param('to', 100), 100)
    const n = Math.trunc(Math.floor(a + (b - a) * f + 0.5))
    const sep = String(L.param('separator', '') || '')
    let s = sep ? groupThousands(Math.abs(n), sep) : String(Math.abs(n))
    if (n < 0) s = '-' + s
    return [`${L.param('prefix', '') || ''}${s}${L.param('suffix', '') || ''}`, {}]
  }
  if (kind === 'countdown') {
    const [ws, we] = layerWindow(ctx, L)
    const start = Math.trunc(clamp(pnum(L, L.param('from', 3), 3), 1, 10))
    const span = Math.max(1e-3, (we - ws) / (start + 1))
    const k = Math.trunc(clamp(Math.floor((t - ws) / span), 0, start))
    const p = clamp01(((t - ws) - k * span) / span)
    const label = start - k > 0 ? String(start - k) : 'GO!'
    const pop = 1 + 0.45 * (1 - Math.min(1, p / 0.35)) ** 3
    return [label, { scale: pop, opacity: p < 0.82 ? 1 : Math.max(0, 1 - (p - 0.82) / 0.18) }]
  }
  return [e.text, {}]
}

const paramStr = (L: ResolvedLayer, v: unknown) => (typeof v === 'string' && v.startsWith('$')) ? String(L.param(v.slice(1), 'base') || 'base') : String(v)
const unitBox = (u: Unit): [number, number, number, number] => [u.cx - u.w / 2, u.cy - u.h / 2, u.cx + u.w / 2, u.cy + u.h / 2]

/** Composed state of unit e at time t — evaluate() in text_motion.py. */
export function evaluate (ctx: Ctx, e: Unit, t: number): State {
  const cap = ctx.cap
  const em = cap.em
  const startRank = levelRank(e.level)
  let levels = ctx.levels.filter(l => levelRank(l) >= startRank)
  if (!levels.includes(e.level)) levels = [e.level, ...levels]
  const acc: Record<string, Record<string, number>> = {}
  for (const l of levels) acc[l] = { ...NEUTRAL }
  let colour: Rgb = ctx.base
  let split: State['split'] = null
  let mask: string | null = null
  let maskMix = 1
  let clip: [number, number, number, number] | null = null
  let text = e.text
  const mods: Record<string, number> = {}
  const bg = cap.bg
  for (const L of ctx.stack) {
    const lvl = L.unit
    if (!(lvl in acc)) continue
    const a = ancestor(ctx, e, lvl)
    if (!a || !inRange(L, a)) continue
    const rank = L.ranks.get(a.index)
    if (rank === undefined) continue
    const [u, tl] = localU(ctx, L, rank, t)
    const useed = a.index * 7919 + Math.trunc(levelRank(lvl) * 2)
    const fx = L.fx
    const tracks = fx.tracks || {}
    for (const ch of Object.keys(tracks)) {
      const spec = tracks[ch]
      if (ch === 'path') {
        if (!cap.motion || !cap.motion.points.length) continue
        const f = (PATH_EASE[cap.motion.easing] || PATH_EASE.linear)(u)
        const [px, py] = pathPoint(cap.motion.points, f)
        acc[lvl].px += px / 100 * cap.frameW - a.cx
        acc[lvl].py += py / 100 * cap.frameH - a.cy
        if (cap.motion.rotateAlong && cap.motion.points.length >= 2) {
          // tangent angle (degrees) just ahead on the path; at the very end
          // look backwards instead — twin of the 'path' branch in text_motion.py
          const f2 = f + 0.02
          let dx: number, dy: number
          if (f2 > 1) {
            const [qx, qy] = pathPoint(cap.motion.points, Math.max(0, f - 0.02))
            dx = px - qx; dy = py - qy
          } else {
            const [qx, qy] = pathPoint(cap.motion.points, f2)
            dx = qx - px; dy = qy - py
          }
          if (Math.abs(dx) > 1e-9 || Math.abs(dy) > 1e-9) acc[lvl].rz += Math.atan2(dy, dx) * 180 / Math.PI
        }
        continue
      }
      if (ch === 'clip') {
        const running = (L.phase === 'in' && u < 1) || (L.phase === 'out' && u > 0) || L.phase === 'hold'
        if (!running) continue
        const [l, tp, r, b] = keys(ctx, L, spec, u, ctx.base, useed)
        const pad = fx.clipPad || [0.3, 0.3]
        let [x0, y0, x1, y1] = unitBox(a)
        x0 -= pad[0] * em; x1 += pad[0] * em; y0 -= pad[1] * em; y1 += pad[1] * em
        const rect: [number, number, number, number] = [x0 + (x1 - x0) * l, y0 + (y1 - y0) * tp, x0 + (x1 - x0) * r, y0 + (y1 - y0) * b]
        clip = clip === null ? rect : [Math.max(clip[0], rect[0]), Math.max(clip[1], rect[1]), Math.min(clip[2], rect[2]), Math.min(clip[3], rect[3])]
        continue
      }
      if (ch === 'colour') {
        colour = keys(ctx, L, spec, u, colour, useed)
        const cur = split as NonNullable<State['split']> | null
        if (cur) split = { ...cur, a: keys(ctx, L, spec, u, cur.a, useed), b: keys(ctx, L, spec, u, cur.b, useed) }
        continue
      }
      let v: any
      if (spec && !Array.isArray(spec)) v = generator(ctx, L, spec, u, tl, useed)
      else {
        v = keys(ctx, L, spec, u, ctx.base, useed)
        if (Array.isArray(v)) continue
      }
      if (L.intensity !== 1) v = NEUTRAL[ch] + (v - NEUTRAL[ch]) * L.intensity
      if (MUL_SET.has(ch)) acc[lvl][ch] *= v
      else if (MAX_SET.has(ch)) acc[lvl][ch] = Math.max(acc[lvl][ch], v)
      else if (ch in NEUTRAL) acc[lvl][ch] += v
    }
    const wipe = fx.colourWipe
    if (wipe) {
      const ca = colourToken(ctx, paramStr(L, wipe.from ?? 'base'), colour)
      const cb = colourToken(ctx, paramStr(L, wipe.to ?? 'base'), colour)
      if (u <= 0) colour = ca
      else if (u >= 1) colour = cb
      else {
        let [x0, y0, x1, y1] = unitBox(a)
        const pad = 0.12 * em
        x0 -= pad; x1 += pad; y0 -= em; y1 += em
        const d = wipe.dir || 'ltr'
        let rect: [number, number, number, number]
        if (d === 'rtl') rect = [x1 - (x1 - x0) * u, y0, x1, y1]
        else if (d === 'ttb') rect = [x0, y0, x1, y0 + (y1 - y0) * u]
        else if (d === 'btt') rect = [x0, y1 - (y1 - y0) * u, x1, y1]
        else rect = [x0, y0, x0 + (x1 - x0) * u, y1]
        split = { a: ca, b: cb, region: { kind: 'rect', rect, inside: true }, kind: 'wipe', f: u }
        colour = ca
      }
    }
    const bgspec = fx.bg
    if (bgspec && bg) {
      const p = clamp01((t - bg.start) / Math.max(bg.time, 1e-6))
      const region = bgRegion(bg.transition, p, cap.frameW, cap.frameH)
      if (bgspec.mode === 'follow') {
        const ca = colourToken(ctx, paramStr(L, bgspec.from ?? 'base'), colour)
        const cb = colourToken(ctx, paramStr(L, bgspec.to ?? 'contrastB'), colour)
        if (region.kind === 'none') colour = ca
        else if (region.kind === 'all') colour = cb
        else if (region.kind === 'mix') colour = mix(ca, cb, region.f)
        else { split = { a: ca, b: cb, region, kind: 'bg', f: p }; colour = ca }
      } else if (bgspec.mode === 'arrive' || bgspec.mode === 'leave') {
        const visibleIn = bgspec.mode === 'arrive'
        if (region.kind === 'none') maskMix *= visibleIn ? 0 : 1
        else if (region.kind === 'all') maskMix *= visibleIn ? 1 : 0
        else if (region.kind === 'mix') maskMix *= visibleIn ? region.f : 1 - region.f
        else {
          mask = visibleIn ? 'in' : 'out'
          if (split && split.kind === 'wipe') { colour = mix(split.a, split.b, split.f); split = null }
          if (!split) split = { a: colour, b: colour, region, kind: 'mask', f: p }
        }
      }
    }
    if (fx.content && ctx.content[L.phase] === L) {
      const [txt, extra] = contentOf(ctx, L, e, u, tl, t, useed)
      text = txt
      for (const k2 of Object.keys(extra)) mods[k2] = (k2 in mods ? mods[k2] : 1) * extra[k2]
    }
  }
  const tot: Record<string, number> = { ...NEUTRAL }
  for (const l of levels) {
    for (const ch of Object.keys(acc[l])) {
      const v = acc[l][ch]
      if (MUL_SET.has(ch)) tot[ch] *= v
      else if (MAX_SET.has(ch)) tot[ch] = Math.max(tot[ch], v)
      else tot[ch] += v
    }
  }
  for (const k2 of Object.keys(mods)) {
    // Add channels (y, rz, …) start at 0, so a multiply would cancel them;
    // content overrides on add channels ADD instead. No v1 content ever
    // returned an add channel, so this changes nothing for saved projects.
    if (k2 in NEUTRAL && !MUL_SET.has(k2) && !MAX_SET.has(k2)) tot[k2] += mods[k2]
    else tot[k2] = (k2 in tot ? tot[k2] : 1) * mods[k2]
  }
  tot.opacity *= maskMix
  const sq = tot.squash
  tot.sy *= sq
  tot.sx *= 1 + (1 - sq) * 0.5
  let x = e.cx, y = e.cy
  if (ctx.gran === 'char' && e.level === 'char' && tot.spacing) x += tot.spacing * em * (e.col - (e.ncol - 1) / 2)
  const whole = ctx.units.text[0]
  for (const l of levels) {
    const a = ancestor(ctx, e, l)
    if (!a) continue
    const s = acc[l]
    if (s.gather) { x += (whole.cx - x) * s.gather; y += (whole.cy - y) * s.gather }
    if (l !== e.level) {
      let dx = x - a.cx, dy = y - a.cy
      dx *= s.scale * s.sx * (1 + (1 - s.squash) * 0.5)
      dy *= s.scale * s.sy * s.squash
      const th = radians(s.rz)
      const ndx = dx * Math.cos(th) - dy * Math.sin(th)
      const ndy = dx * Math.sin(th) + dy * Math.cos(th)
      x = a.cx + ndx; y = a.cy + ndy
    }
    x += s.x * em + s.vx * cap.frameW + s.px
    y += s.y * em + s.vy * cap.frameH + s.py
  }
  if (clip) {
    let pdx = 0, pdy = 0
    for (const l of levels) { pdx += acc[l].px; pdy += acc[l].py }
    if (pdx || pdy) clip = [clip[0] + pdx, clip[1] + pdy, clip[2] + pdx, clip[3] + pdy]
  }
  if (split && split.kind === 'wipe' && split.region.kind === 'rect') {
    const dx = x - e.cx, dy = y - e.cy
    const r0 = split.region.rect
    split = { ...split, region: { kind: 'rect', rect: [r0[0] + dx, r0[1] + dy, r0[2] + dx, r0[3] + dy], inside: true } }
  }
  let org: [number, number] | null = null
  if (ctx.orgLevel !== null && ctx.orgLevel in acc) {
    const a = ancestor(ctx, e, ctx.orgLevel)
    if (a) {
      const s = acc[ctx.orgLevel]
      org = [a.cx + s.x * em + s.vx * cap.frameW + s.px, a.cy + s.y * em + s.vy * cap.frameH + s.py]
    }
  }
  if (split && mask !== null) split.mask = mask
  return { x, y, text, tot, colour, clip, org, split }
}

/** Opacity of the text-wide layers only (a caret fades with a Fade out). */
export function ambientOpacity (ctx: Ctx, t: number) {
  let op = 1
  for (const L of ctx.stack) {
    const spec = (L.fx.tracks || {}).opacity
    if (spec === undefined || L.fx.caret || L.unit !== 'text') continue
    const [u, tl] = localU(ctx, L, L.ranks.get(0) ?? 0, t)
    const v = spec && !Array.isArray(spec) ? generator(ctx, L, spec, u, tl, 6) : keys(ctx, L, spec, u, ctx.base, 6)
    op *= Number(v)
  }
  return clamp01(op)
}

/** Local u of a layer for a unit (box copies with their own clip / tracks). */
export function layerU (ctx: Ctx, L: ResolvedLayer, unitIndex: number, t: number) {
  return localU(ctx, L, L.ranks.get(unitIndex) ?? 0, t)[0]
}
export function layerKeys (ctx: Ctx, L: ResolvedLayer, ks: any[], u: number) {
  return keys(ctx, L, ks, u, ctx.base, 0)
}
export function inLayerRange (L: ResolvedLayer, u: Unit) { return inRange(L, u) }
export function paramString (L: ResolvedLayer, v: unknown) { return paramStr(L, v) }
export type { ResolvedLayer }

/** Seconds a layer occupies in its lane (for the timeline bars). */
export function layerSpan (layer: TextFxLayer, fx: EffectDef | undefined, windowSeconds: number) {
  if (!fx) return 0
  const delay = layer.delay || 0
  if (fx.phase === 'hold') {
    const loop = layer.loop || fx.loop || 'loop'
    if (loop === 'once' && (layer.duration ?? fx.duration)) return Math.min(windowSeconds, delay + Number(layer.duration ?? fx.duration))
    return Math.max(0, windowSeconds - delay)
  }
  const d = layer.duration ?? fx.duration ?? 0.5
  return delay + Number(d)
}
