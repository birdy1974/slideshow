// Live preview of stacked text effects: a DOM scene driven by the JavaScript
// twin of the render engine (src/textMotionCore.ts).
//
// The layout is measured in the browser with the very same font files libass
// draws with (public/fonts), sized like the MP4 (em = font size, line pitch
// and baseline from the file's win metrics, exactly where libass puts them),
// then every frame asks the core for the composed state of every unit — the
// numbers backend/app/text_motion.py writes into the .ass file. Copies
// (glow, trail, extrusion, mirror, RGB split, dancing shadow, boxes and bars),
// clips, the caret and the colour split of text that follows a frame's
// colour change are drawn from those states.
//
// Colour-change background: paintBackground() draws colour B with the exact
// geometry of FFmpeg's xfade (bgRegion() — wipes, slides, circles, diagonals;
// a crossfade otherwise) at the same clock as the text, so the editor shows
// the transition and the text reacting to it frame-accurately.

import {
  ambientOpacity, bgRegion, buildLayout, cleanLines, colourToken, compileStack, EASE, evaluate, layerKeys, layerU, paramString,
  type BgChange, type Caption, type Ctx, type Layout, type MotionPath, type Region, type Rgb, type State, type TextFxLayer, type Unit,
} from './textMotionCore'
import { EFFECTS } from './textFx'
import { effectiveFontStyle, fontMetrics, fontStack } from './fonts'

export const FRAME_W = 1920
export const FRAME_H = 1080

export interface SceneInput {
  text: string
  stack: TextFxLayer[]
  family: string
  bold: boolean
  italic: boolean
  underline: boolean
  colour: string
  fontSize: number            // editor font size (px in a 1920-wide frame)
  x: number                   // block centre, % of the frame
  y: number
  align: 'center' | 'left'
  outline: boolean            // caption outline + shadow (pictures)
  start: number               // text window, seconds
  end: number
  steady: number
  bg: BgChange | null
  motion: MotionPath | null
}

export interface Prepared {
  input: SceneInput
  ctx: Ctx
  layout: Layout
  em: number
  lineH: number
  fontCss: string
  family: string
  bold: boolean
  italic: boolean
  baselineOff: number         // baseline below the line-box centre (px)
  capH: number
  fa: number                  // browser font ascent / descent at em (px)
  fd: number
  frameW: number
  frameH: number
}

// ---------------------------------------------------------------------------
// Measuring
// ---------------------------------------------------------------------------
let measureCtx: CanvasRenderingContext2D | null | undefined
function measurer(): CanvasRenderingContext2D | null {
  if (measureCtx === undefined) {
    try { measureCtx = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null } catch { measureCtx = null }
  }
  return measureCtx
}

export function fontCssFor(family: string, bold: boolean, italic: boolean, px: number) {
  return `${italic ? 'italic ' : ''}${bold ? 700 : 400} ${px.toFixed(2)}px ${fontStack(family)}`
}

const fontPromises = new Map<string, Promise<void>>()
/** Resolves once the browser has the caption font (then the layout is exact). */
export function ensureFont(family: string, bold: boolean, italic: boolean): Promise<void> {
  const st = effectiveFontStyle(family, bold, italic)
  const key = `${family}|${st.bold}|${st.italic}`
  let p = fontPromises.get(key)
  if (!p) {
    p = (typeof document !== 'undefined' && (document as any).fonts?.load)
      ? (document as any).fonts.load(fontCssFor(family, st.bold, st.italic, 64), 'AaGg').then(() => undefined, () => undefined)
      : Promise.resolve()
    fontPromises.set(key, p!)
  }
  return p!
}
export function fontLoaded(family: string, bold: boolean, italic: boolean): boolean {
  const st = effectiveFontStyle(family, bold, italic)
  try { return Boolean((document as any).fonts?.check?.(fontCssFor(family, st.bold, st.italic, 64))) } catch { return true }
}

function advancesFor(line: string, fontCss: string, em: number): number[] {
  const m = measurer()
  if (!m) return Array.from(line, ch => (/\s/.test(ch) ? em * 0.28 : em * 0.56))
  m.font = fontCss
  const out: number[] = []
  let prev = 0
  for (let i = 0; i < line.length; i++) {
    const w = m.measureText(line.slice(0, i + 1)).width
    out.push(w - prev)
    prev = w
  }
  return out
}

/** Lay the caption out and compile its stack (both engines' rest geometry). */
export function prepareScene(input: SceneInput, frameW = FRAME_W, frameH = FRAME_H): Prepared {
  const st = effectiveFontStyle(input.family, input.bold, input.italic)
  const metrics = fontMetrics(input.family, st.bold, st.italic)
  const em = Math.max(4, input.fontSize * frameW / 1920)
  const k = em / metrics.upm
  const lineH = em * (metrics.winAscent + metrics.winDescent) / metrics.upm
  const fontCss = fontCssFor(input.family, st.bold, st.italic, em)
  const lines = cleanLines(input.text)
  const layout = buildLayout(lines, line => advancesFor(line, fontCss, em), em, lineH, frameW * input.x / 100, frameH * input.y / 100, input.align)
  const caption: Caption = {
    stack: input.stack, em, colour: input.colour, start: input.start, end: input.end,
    frameW, frameH, steady: input.steady, bg: input.bg, motion: input.motion,
  }
  const ctx = compileStack(caption, layout, EFFECTS)
  let fa = metrics.hheaAscent * k
  let fd = -metrics.hheaDescent * k
  const m = measurer()
  if (m) {
    m.font = fontCss
    const tm = m.measureText('Hg') as TextMetrics & { fontBoundingBoxAscent?: number; fontBoundingBoxDescent?: number }
    if (tm.fontBoundingBoxAscent && tm.fontBoundingBoxDescent) { fa = tm.fontBoundingBoxAscent; fd = tm.fontBoundingBoxDescent }
  }
  return {
    input, ctx, layout, em, lineH, fontCss, family: input.family, bold: st.bold, italic: st.italic,
    baselineOff: (metrics.winAscent - metrics.winDescent) / 2 * k, capH: (metrics.capHeight || metrics.upm * 0.7) * k,
    fa, fd, frameW, frameH,
  }
}

// ---------------------------------------------------------------------------
// Background colour change (exact xfade geometry)
// ---------------------------------------------------------------------------
const pct = (v: number, of: number) => `${(v / of * 100).toFixed(3)}%`

function regionShape(region: Region, w: number, h: number): string | null {
  if (region.kind === 'rect') {
    const [x0, y0, x1, y1] = region.rect
    return `polygon(${pct(x0, w)} ${pct(y0, h)}, ${pct(x1, w)} ${pct(y0, h)}, ${pct(x1, w)} ${pct(y1, h)}, ${pct(x0, w)} ${pct(y1, h)})`
  }
  const pts = regionPoints(region)
  if (!pts) return null
  return `polygon(${pts.map(([x, y]) => `${pct(x, w)} ${pct(y, h)}`).join(', ')})`
}

function regionPoints(region: Region): [number, number][] | null {
  if (region.kind === 'rect') {
    const [x0, y0, x1, y1] = region.rect
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
  }
  if (region.kind === 'circle') {
    const n = 64
    const r = Math.max(0.5, region.r)
    return Array.from({ length: n }, (_, i) => [region.cx + r * Math.cos(2 * Math.PI * i / n), region.cy + r * Math.sin(2 * Math.PI * i / n)] as [number, number])
  }
  if (region.kind === 'poly') return region.points
  return null
}

/** clip-path showing the region (show = true) or everything but it. Units: % of w × h. */
export function regionClip(region: Region, show: boolean, w: number, h: number): string {
  const pts = regionPoints(region)
  if (!pts) return show ? 'none' : 'inset(50% 50% 50% 50%)'
  if (show) return regionShape(region, w, h) || 'none'
  const frame = `0% 0%, 100% 0%, 100% 100%, 0% 100%, 0% 0%`
  return `polygon(evenodd, ${frame}, ${pts.map(([x, y]) => `${pct(x, w)} ${pct(y, h)}`).join(', ')}, ${pct(pts[0][0], w)} ${pct(pts[0][1], h)})`
}

/** Paint the text frame's background at time t: colour A, colour B arriving
 * through the frame transition with FFmpeg's geometry. */
export function paintBackground(base: HTMLElement, overlay: HTMLElement, colourA: string, bg: BgChange | null, t: number, w = FRAME_W, h = FRAME_H) {
  base.style.background = colourA
  if (!bg) { overlay.style.opacity = '0'; return }
  const p = Math.max(0, Math.min(1, (t - bg.start) / Math.max(bg.time, 1e-6)))
  const region = bgRegion(bg.transition, p, w, h)
  overlay.style.background = bg.colourB
  if (region.kind === 'none') { overlay.style.opacity = '0'; overlay.style.clipPath = 'none'; return }
  if (region.kind === 'all') { overlay.style.opacity = '1'; overlay.style.clipPath = 'none'; return }
  if (region.kind === 'mix') { overlay.style.opacity = region.f.toFixed(3); overlay.style.clipPath = 'none'; return }
  overlay.style.opacity = '1'
  overlay.style.clipPath = regionClip(region, region.inside !== false, w, h)
}

// ---------------------------------------------------------------------------
// Scene
// ---------------------------------------------------------------------------
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v)
const rgba = (c: Rgb | number[], a = 1) => `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${a.toFixed(3)})`
const mixRgb = (a: Rgb, b: Rgb, f: number): Rgb => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]

interface GlyphView {
  e: Unit
  wrap: HTMLDivElement        // unit clip (stage coordinates)
  partA: HTMLDivElement       // region clip for colour A
  glyphA: HTMLSpanElement
  partB: HTMLDivElement
  glyphB: HTMLSpanElement
  clones: { el: HTMLSpanElement; k: number; spec: any; L: any }[]
  mirror: { el: HTMLSpanElement; spec: any } | null
}
interface BoxView { L: any; spec: any; u: Unit; el: HTMLDivElement; gap: HTMLDivElement | null }

function h<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, style?: Partial<CSSStyleDeclaration>): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag)
  el.className = cls
  if (style) Object.assign(el.style, style)
  return el
}

export class MotionScene {
  root: HTMLDivElement
  prep: Prepared
  private units: GlyphView[] = []
  private lines: GlyphView[] = []
  private members: Map<number, GlyphView[]> = new Map()
  private boxes: BoxView[] = []
  private bubbles: BoxView[] = []
  private caret: HTMLSpanElement | null = null
  private lastCount = -1
  private lastChange = 0
  private copies: { L: any; spec: any }[] = []

  constructor(host: HTMLElement, prep: Prepared, opts: { className?: string } = {}) {
    this.prep = prep
    const { frameW, frameH } = prep
    this.root = h('div', `tm-scene${opts.className ? ` ${opts.className}` : ''}`, { width: `${frameW}px`, height: `${frameH}px` })
    const ctx = prep.ctx
    this.copies = ctx.stack.flatMap(L => (L.fx.copies || []).filter((s: any) => s.type !== 'box' && s.type !== 'bubble').map((spec: any) => ({ L, spec })))
    const boxSpecs = ctx.stack.flatMap(L => (L.fx.copies || []).filter((s: any) => s.type === 'box').map((spec: any) => ({ L, spec })))
    const bubbleSpecs = ctx.stack.flatMap(L => (L.fx.copies || []).filter((s: any) => s.type === 'bubble').map((spec: any) => ({ L, spec })))
    const below = h('div', 'tm-layer tm-below')
    const above = h('div', 'tm-layer tm-above')
    for (const { L, spec } of boxSpecs) {
      for (const u of ctx.units[L.unit] || []) {
        if (L.ranks.get(u.index) === undefined) continue
        if ((u.level === 'char' || u.level === 'word') && !u.text.trim()) continue
        const el = h('div', 'tm-box')
        const gap = spec.split ? h('div', 'tm-box') : null
        ;(spec.below === false ? above : below).append(el)
        if (gap) (spec.below === false ? above : below).append(gap)
        this.boxes.push({ L, spec, u, el, gap })
      }
    }
    for (const { L, spec } of bubbleSpecs) {
      for (const u of ctx.units[L.unit] || []) {
        if (L.ranks.get(u.index) === undefined) continue
        if ((u.level === 'char' || u.level === 'word') && !u.text.trim()) continue
        const el = h('div', 'tm-box tm-bubble')
        el.style.borderRadius = '9999px'
        ;(spec.below === false ? above : below).append(el)
        this.bubbles.push({ L, spec, u, el, gap: null })
      }
    }
    this.root.append(below)
    const gran = ctx.gran
    const eventUnits = (ctx.units[gran] || []).filter(e => e.text.trim())
    const makeView = (e: Unit): GlyphView => {
      const wrap = h('div', 'tm-u')
      const clones: GlyphView['clones'] = []
      let mirror: GlyphView['mirror'] = null
      for (const { L, spec } of this.copies) {
        if (spec.type === 'trail') {
          const n = Math.trunc(Number(spec.count ?? 4))
          for (let k = n; k >= 1; k--) { const el = this.glyph(e); wrap.append(el); clones.push({ el, k, spec, L }) }
        } else if (spec.type === 'mirror' && !mirror) {
          const el = this.glyph(e); wrap.append(el); mirror = { el, spec }
        }
      }
      const partA = h('div', 'tm-part')
      const glyphA = this.glyph(e)
      partA.append(glyphA)
      const partB = h('div', 'tm-part')
      const glyphB = this.glyph(e)
      partB.append(glyphB)
      wrap.append(partA, partB)
      this.root.append(wrap)
      return { e, wrap, partA, glyphA, partB, glyphB, clones, mirror }
    }
    if (gran === 'char' || gran === 'word') {
      // letters at rest are drawn as their whole line (keeps script joins)
      for (const lu of ctx.units.line) if (lu.text.trim()) this.lines.push(makeView(lu))
      for (const e of eventUnits) {
        const v = makeView(e)
        this.units.push(v)
        const list = this.members.get(e.line) || []
        list.push(v)
        this.members.set(e.line, list)
      }
    } else {
      for (const e of eventUnits) this.units.push(makeView(e))
    }
    this.root.append(above)
    if (ctx.stack.some(L => L.fx.caret) && gran === 'char') {
      this.caret = h('span', 'tm-caret', { font: prep.fontCss, lineHeight: `${prep.lineH}px`, height: `${prep.lineH}px` })
      this.caret.textContent = '|'
      this.root.append(this.caret)
    }
    host.append(this.root)
  }

  private glyph(e: Unit): HTMLSpanElement {
    const p = this.prep
    const pad = p.em * 1.2
    const baselineY = e.cy + p.baselineOff
    const baseTop = (p.lineH - (p.fa + p.fd)) / 2 + p.fa
    const top = baselineY - baseTop
    const el = h('span', 'tm-g', {
      left: `${e.cx - e.w / 2 - pad}px`, top: `${top}px`, width: `${e.w + 2 * pad}px`, height: `${p.lineH}px`,
      lineHeight: `${p.lineH}px`, font: p.fontCss, transformOrigin: `${e.w / 2 + pad}px ${e.cy - top}px`,
      textDecoration: p.input.underline ? 'underline' : 'none',
    })
    el.textContent = e.text
    return el
  }

  destroy() { this.root.remove() }

  /** Draw the scene at time t (seconds on the caption's clock). */
  render(t: number) {
    const ctx = this.prep.ctx
    const states = new Map<GlyphView, State>()
    for (const v of this.units) states.set(v, evaluate(ctx, v.e, t))
    if (this.lines.length) {
      for (const lv of this.lines) {
        const ls = evaluate(ctx, lv.e, t)
        const group = this.members.get(lv.e.index) || []
        const coarse = group.length > 0 && group.every(v => sameAsLine(lv.e, ls, v.e, states.get(v)!))
        this.draw(lv, ls, t, coarse)
        for (const v of group) this.draw(v, states.get(v)!, t, !coarse)
      }
    } else {
      for (const v of this.units) this.draw(v, states.get(v)!, t, true)
    }
    for (const b of this.boxes) this.drawBox(b, t)
    for (const b of this.bubbles) this.drawBubble(b, t)
    if (this.caret) this.drawCaret(t, states)
  }

  private draw(v: GlyphView, s: State, t: number, show: boolean) {
    if (!show) { v.wrap.style.display = 'none'; return }
    v.wrap.style.display = ''
    const { frameW: W, frameH: H, em } = this.prep
    // unit clip: a rectangle in frame coordinates (masks do not move with the glyph)
    if (s.clip) {
      const [x0, y0, x1, y1] = s.clip
      v.wrap.style.clipPath = x1 <= x0 || y1 <= y0 ? 'inset(50% 50% 50% 50%)'
        : `inset(${y0.toFixed(1)}px ${(W - x1).toFixed(1)}px ${(H - y1).toFixed(1)}px ${x0.toFixed(1)}px)`
    } else v.wrap.style.clipPath = ''
    const shadows = this.copyShadows(s)
    const split = s.split
    let colourA = s.colour
    let alphaA = 1
    let showB = false
    v.partA.style.clipPath = ''
    v.partB.style.clipPath = ''
    if (split) {
      const region = split.region
      const kind = split.kind
      const f = split.f
      if (s.clip || !['rect', 'circle', 'poly'].includes(region.kind)) {
        if (kind === 'mask') alphaA = split.mask === 'in' ? f : 1 - f
        else {
          colourA = mixRgb(split.a, split.b, f)
          alphaA = split.mask === 'in' ? f : split.mask === 'out' ? 1 - f : 1
        }
      } else {
        const inside = (region as any).inside !== false
        const clipB = regionClip(region, inside, W, H)
        const clipA = regionClip(region, !inside, W, H)
        if (kind === 'mask') {
          if (split.mask === 'in') { v.partA.style.clipPath = clipB; colourA = split.b } else { v.partA.style.clipPath = clipA; colourA = split.a }
        } else {
          colourA = split.a
          v.partA.style.clipPath = clipA
          v.partB.style.clipPath = clipB
          showB = split.mask !== 'out'
          if (split.mask === 'in') alphaA = 0
          styleGlyph(v.glyphB, v.e, s, em, { colour: split.b, shadow: shadows, outline: this.prep.input.outline })
        }
      }
    }
    v.partB.style.display = showB ? '' : 'none'
    styleGlyph(v.glyphA, v.e, s, em, { colour: colourA, alpha: alphaA, shadow: shadows, outline: this.prep.input.outline })
    const ctx = this.prep.ctx
    for (const c of v.clones) {
      const sk = evaluate(ctx, v.e, t - c.k * Number(c.spec.delay ?? 0.05))
      const n = Math.trunc(Number(c.spec.count ?? 4))
      const op = Number(c.spec.alpha ?? 0.5) * (1 - (c.k - 1) / n)
      const col = colourToken(ctx, paramString(c.L, c.spec.colour ?? 'base'), sk.colour)
      styleGlyph(c.el, v.e, sk, em, { colour: col, alpha: op, plain: true })
    }
    if (v.mirror) {
      const whole = ctx.units.text[0]
      const spec = v.mirror.spec
      const axis = whole.cy + whole.h / 2 + Number(spec.gap ?? 0.06) * em
      styleGlyph(v.mirror.el, v.e, s, em, { alpha: Number(spec.alpha ?? 0.3), y: 2 * axis - s.y, flip: true, blur: Number(spec.blur ?? 0.03) * em, plain: true })
    }
  }

  /** Glow, extrusion, dancing shadow and RGB split as CSS text-shadows (drawn below the text like the ASS copies). */
  private copyShadows(s: State): string[] {
    const out: string[] = []
    const em = this.prep.em
    const ctx = this.prep.ctx
    const op = clamp01(s.tot.opacity)
    for (const { L, spec } of this.copies) {
      if (spec.type === 'glow' && s.tot.glow > 0.01) {
        const col = colourToken(ctx, paramString(L, spec.colour ?? 'base'), s.colour)
        const r = Number(spec.blur ?? 0.16) * em * s.tot.glow
        const b = Number(spec.bord ?? 0.07) * em * s.tot.glow
        out.push(`0 0 ${(b + r * 0.5).toFixed(1)}px ${rgba(col, op)}`, `0 0 ${(b + r * 1.4).toFixed(1)}px ${rgba(col, op * 0.85)}`)
      } else if (spec.type === 'extrude' && s.tot.depth > 0.01) {
        const base = colourToken(ctx, paramString(L, spec.colour ?? '#6a4de0'), ctx.base)
        const n = Math.trunc(Number(spec.count ?? 8))
        for (let k = 1; k <= n; k++) {
          const shade = 1 - Number(spec.shade ?? 0.5) * k / n
          out.push(`${(k * Number(spec.dx) * em * s.tot.depth).toFixed(1)}px ${(k * Number(spec.dy) * em * s.tot.depth).toFixed(1)}px 0 ${rgba(base.map(c => c * shade), op)}`)
        }
      } else if (spec.type === 'shadow') {
        const cols: string[] = spec.colours || ['#ff4f8b', '#3fd0ff']
        const r = Number(spec.radius ?? 0.06) * em
        cols.forEach((c, i) => {
          const a = 2 * Math.PI * (s.tot.orbit + i / cols.length)
          out.push(`${(r * Math.cos(a)).toFixed(1)}px ${(r * Math.sin(a)).toFixed(1)}px 0 ${c}`)
        })
      } else if (spec.type === 'rgb' && s.tot.rgb > 0.01) {
        const d = Number(spec.dx) * em * s.tot.rgb
        const cols: string[] = spec.colours || ['#ff2a6d', '#05d9e8']
        out.push(`${(-d).toFixed(1)}px 0 0 ${cols[0]}d9`, `${d.toFixed(1)}px 0 0 ${cols[1]}d9`)
      }
    }
    return out
  }

  private drawBox(b: BoxView, t: number) {
    const ctx = this.prep.ctx
    const { em, baselineOff, capH } = this.prep
    const { L, spec, u, el, gap } = b
    const s = evaluate(ctx, u, t)
    const lu = layerU(ctx, L, u.index, t)
    const col = colourToken(ctx, paramString(L, spec.colour ?? '#000000'), s.colour)
    const op = clamp01(s.tot.opacity) * clamp01(Number(spec.alpha ?? 1))
    let sx = 1
    if (spec.tracks && spec.tracks.sx) sx = Number(layerKeys(ctx, L, spec.tracks.sx, lu))
    const pw = Number((spec.pad || [0.1, 0])[0])
    const w = u.w + 2 * pw * em
    const hh = Number(spec.height ?? 1) * em
    let off = Number(spec.offsetY ?? 0) * em
    if (spec.valign === 'baseline') off += baselineOff
    else if (spec.valign === 'cap') off += baselineOff - capH / 2
    const cx = s.x, cy = s.y + off
    const visible = op > 0.004 && sx > 0.002
    for (const [node, height, colour] of [[el, hh, col], [gap, Math.max(1, hh * 0.03), spec.split ? hexRgb(spec.split) : col]] as const) {
      if (!node) continue
      node.style.display = visible ? '' : 'none'
      if (!visible) continue
      node.style.left = `${cx - w / 2}px`
      node.style.top = `${cy - height / 2}px`
      node.style.width = `${w}px`
      node.style.height = `${height}px`
      node.style.background = rgba(colour as Rgb, op)
      node.style.transformOrigin = spec.anchor === 'left' ? '0 50%' : '50% 50%'
      node.style.transform = `rotate(${s.tot.rz.toFixed(2)}deg) scaleX(${sx.toFixed(4)})`
      if (spec.clip && lu < 1 && node === el) {
        const [l, tp, r, bt] = layerKeys(ctx, L, spec.clip, lu) as number[]
        node.style.clipPath = `inset(${(height * tp).toFixed(1)}px ${(w * (1 - r)).toFixed(1)}px ${(height * (1 - bt)).toFixed(1)}px ${(w * l).toFixed(1)}px)`
      } else node.style.clipPath = ''
    }
  }

  /** Bubbles: rounded pill behind each unit, popping in with an outBack
   *  overshoot. Twin of _bubble_events() in backend/app/text_motion.py. */
  private drawBubble(b: BoxView, t: number) {
    const ctx = this.prep.ctx
    const { em, baselineOff, capH } = this.prep
    const { L, spec, u, el } = b
    const s = evaluate(ctx, u, t)
    const lu = layerU(ctx, L, u.index, t)
    const col = colourToken(ctx, paramString(L, spec.colour ?? '#ffffff'), s.colour)
    let op = clamp01(s.tot.opacity) * clamp01(Number(spec.alpha ?? 1))
    const pw = Number((spec.pad || [0.22, 0])[0])
    const w = u.w + 2 * pw * em
    const hh = Number(spec.height ?? 1.15) * em
    let off = Number(spec.offsetY ?? 0) * em
    if (spec.valign === 'baseline') off += baselineOff
    else if (spec.valign === 'cap') off += baselineOff - capH / 2
    let sc = 1
    if (spec.pop !== false) {
      sc = Math.max(0, EASE.outBack(clamp01(lu / 0.6)))
      op = op * clamp01(lu * 5)
    }
    const cx = s.x, cy = s.y + off
    const visible = op > 0.004 && sc > 0.002
    el.style.display = visible ? '' : 'none'
    if (!visible) return
    el.style.left = `${cx - w / 2}px`
    el.style.top = `${cy - hh / 2}px`
    el.style.width = `${w}px`
    el.style.height = `${hh}px`
    el.style.background = rgba(col, op)
    el.style.transformOrigin = '50% 50%'
    el.style.transform = `rotate(${s.tot.rz.toFixed(2)}deg) scale(${sc.toFixed(4)})`
  }

  private drawCaret(t: number, states: Map<GlyphView, State>) {
    const ctx = this.prep.ctx
    const em = this.prep.em
    let last: { e: Unit; s: State } | null = null
    let count = 0
    for (const v of this.units) {
      const s = states.get(v)!
      if (s.tot.opacity > 0.5 && (!s.clip || s.clip[2] > s.clip[0] + 1)) { last = { e: v.e, s }; count++ }
    }
    if (count !== this.lastCount) { this.lastCount = count; this.lastChange = t }
    const first = this.units[0]?.e
    if (!first) return
    const x = last ? last.s.x + last.e.w / 2 + 0.07 * em : first.cx - first.w / 2
    const y = last ? last.s.y : first.cy
    const typing = (t - this.lastChange) < 0.35 && t > ctx.cap.start + 1e-6
    const on = typing || (((t - this.lastChange) % 1) + 1) % 1 < 0.5
    const baselineY = y + this.prep.baselineOff
    const baseTop = (this.prep.lineH - (this.prep.fa + this.prep.fd)) / 2 + this.prep.fa
    Object.assign(this.caret!.style, {
      left: `${x}px`, top: `${baselineY - baseTop}px`, color: rgba(ctx.base, 1),
      opacity: on ? ambientOpacity(ctx, t).toFixed(3) : '0',
    })
  }
}

function hexRgb(hex: string): Rgb {
  const v = String(hex).replace('#', '')
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)]
}

/** Would the whole-line draw show unit e exactly like its own draw? (Compiler._same_as_line) */
function sameAsLine(line: Unit, ls: State, e: Unit, s: State): boolean {
  if (s.text !== e.text) return false
  if ((s.org == null) !== (ls.org == null)) return false
  if ((s.clip == null) !== (ls.clip == null)) return false
  if (s.clip && ls.clip && s.clip.some((v, i) => Math.abs(v - ls.clip![i]) > 0.25)) return false
  if ((s.split == null) !== (ls.split == null)) return false
  if (s.split && ls.split && (s.split.kind !== ls.split.kind || s.split.mask !== ls.split.mask || JSON.stringify(s.split.region) !== JSON.stringify(ls.split.region))) return false
  if (s.colour.some((v, i) => Math.abs(v - ls.colour[i]) > 0.01)) return false
  for (const ch of Object.keys(s.tot)) if (Math.abs(s.tot[ch] - ls.tot[ch]) > 1e-5) return false
  if (Math.abs(ls.tot.spacing) > 1e-6) return false
  const sx = ls.tot.scale * ls.tot.sx
  const th = ls.tot.rz * Math.PI / 180
  const d = e.cx - line.cx
  return Math.abs(s.x - (ls.x + d * sx * Math.cos(th))) < 0.3 && Math.abs(s.y - (ls.y + d * sx * Math.sin(th))) < 0.3
}

interface GlyphOpts { colour?: Rgb | number[]; alpha?: number; shadow?: string[]; outline?: boolean; y?: number; flip?: boolean; blur?: number; plain?: boolean }

function styleGlyph(el: HTMLSpanElement, e: Unit, s: State, em: number, o: GlyphOpts) {
  const T = s.tot
  const op = clamp01(T.opacity) * (o.alpha ?? 1)
  if (op <= 0.003) { el.style.opacity = '0'; return }
  const fill = clamp01(T.fill)
  const tx = s.x - e.cx
  const ty = (o.y ?? s.y) - e.cy
  let tf = `translate(${tx.toFixed(2)}px,${ty.toFixed(2)}px)`
  if (T.rx || T.ry) tf += ` perspective(${(em * 9).toFixed(0)}px) rotateX(${(-T.rx).toFixed(2)}deg) rotateY(${T.ry.toFixed(2)}deg)`
  if (T.rz) tf += ` rotate(${T.rz.toFixed(2)}deg)`
  tf += ` scale(${(T.scale * T.sx).toFixed(4)},${(T.scale * T.sy * (o.flip ? -0.9 : 1)).toFixed(4)})`
  if (T.skew) tf += ` skewX(${(-Math.atan(T.skew) * 180 / Math.PI).toFixed(2)}deg)`
  el.style.transform = tf
  el.style.opacity = op.toFixed(3)
  const blur = Math.max(0, T.blur * em) + (o.blur || 0)
  el.style.filter = blur > 0.05 ? `blur(${(blur * 0.5).toFixed(2)}px)` : ''
  const colour = o.colour || s.colour
  el.style.color = rgba(colour, fill)
  const line = Math.max(0, T.line * em)
  const outlinePx = o.outline && !o.plain ? Math.max(1, Math.round(em / 16)) : 0
  if (line > 0.05) {
    el.style.webkitTextStroke = `${(2 * (line + outlinePx)).toFixed(2)}px ${rgba(colour, 1)}`
    ;(el.style as any).paintOrder = 'stroke fill'
  } else if (outlinePx) {
    el.style.webkitTextStroke = `${2 * outlinePx}px rgba(0,0,0,0.85)`
    ;(el.style as any).paintOrder = 'stroke fill'
  } else {
    el.style.webkitTextStroke = ''
  }
  const shadows = o.plain ? [] : [...(o.shadow || [])]
  if (outlinePx && !o.plain) shadows.push(`2px 2px 0 rgba(0,0,0,${(0.55 * fill).toFixed(3)})`)
  el.style.textShadow = shadows.length ? shadows.join(',') : 'none'
  el.style.letterSpacing = (e.level === 'line' || e.level === 'text') && T.spacing ? `${(T.spacing * em).toFixed(2)}px` : ''
  if (el.textContent !== s.text) el.textContent = s.text
}

// ---------------------------------------------------------------------------
// Clock shared by the stage, the background and the mini timeline
// ---------------------------------------------------------------------------
export class MotionClock {
  t = 0
  playing = true
  duration: number
  private tail: number
  private listeners = new Set<(t: number) => void>()
  private raf = 0
  private last: number | null = null

  constructor(duration: number, tail = 0.6) {
    this.duration = Math.max(0.2, duration)
    this.tail = tail
  }
  subscribe(fn: (t: number) => void) {
    this.listeners.add(fn)
    fn(this.t)
    if (!this.raf) this.start()
    return () => { this.listeners.delete(fn); if (!this.listeners.size) this.stop() }
  }
  private start() {
    const loop = (now: number) => {
      const dt = this.last == null ? 0 : (now - this.last) / 1000
      this.last = now
      if (this.playing) {
        this.t += dt
        if (this.t > this.duration + this.tail) this.t = 0
        this.emit()
      }
      this.raf = requestAnimationFrame(loop)
    }
    this.raf = requestAnimationFrame(loop)
  }
  private stop() { cancelAnimationFrame(this.raf); this.raf = 0; this.last = null }
  private emit() { const t = Math.min(this.t, this.duration); for (const fn of this.listeners) fn(t) }
  setDuration(d: number) { this.duration = Math.max(0.2, d); if (this.t > this.duration + this.tail) this.t = 0 }
  seek(t: number) { this.t = Math.max(0, Math.min(this.duration, t)); this.emit() }
  setPlaying(p: boolean) { this.playing = p; this.last = null; this.emit() }
  now() { return Math.min(this.t, this.duration) }
}
