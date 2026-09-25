/*
 * JavaScript twin of docs/demo/text_motion_stack.py (the channel compositor).
 *
 * Pure logic, no DOM: the mockup (text-effects-stack.html) feeds it a unit
 * layout measured in the browser, the parity check (docs/demo/
 * check_twin_engines.py) feeds it the HarfBuzz layout from Python and compares
 * every channel value. Same registry (text-motion-effects.json), same maths,
 * same pseudo-random numbers: that is what keeps the live preview and the
 * FFmpeg/libass render identical, and what the app would ship as
 * src/textMotion.ts + backend/app/text_motion.py.
 */
(function (root) {
  'use strict'

  const LEVELS = ['char', 'word', 'line', 'text']            // finest -> coarsest
  const LEVEL_RANK = { char: 0, word: 1, line: 2, text: 3 }
  const MUL = new Set(['opacity', 'scale', 'sx', 'sy', 'fill'])
  const MAXC = new Set(['glow', 'depth', 'rgb', 'orbit'])
  const ADD = new Set(['x', 'y', 'px', 'py', 'rz', 'rx', 'ry', 'skew', 'blur', 'spacing', 'line'])
  const NEUTRAL = {}
  for (const c of MUL) NEUTRAL[c] = 1
  for (const c of MAXC) NEUTRAL[c] = 0
  for (const c of ADD) NEUTRAL[c] = 0

  // ---- easing (names shared with the Python engine) -----------------------
  function outBounce (x) {
    const n1 = 7.5625, d1 = 2.75
    if (x < 1 / d1) return n1 * x * x
    if (x < 2 / d1) { x -= 1.5 / d1; return n1 * x * x + 0.75 }
    if (x < 2.5 / d1) { x -= 2.25 / d1; return n1 * x * x + 0.9375 }
    x -= 2.625 / d1; return n1 * x * x + 0.984375
  }
  const EASE = {
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
    outBounce
  }

  /** Deterministic pseudo-random number in [0, 1), bit-identical to Python. */
  function hash01 (...values) {
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

  function rgb (hex) {
    const h = hex.replace('#', '')
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]
  }
  function mix (a, b, f) {
    if (Array.isArray(a)) return a.map((x, i) => x + (b[i] - x) * f)
    return a + (b - a) * f
  }
  const clamp01 = v => Math.max(0, Math.min(1, v))

  // ---- layers ---------------------------------------------------------------
  /** A stack entry: {effect, unit?, duration?, delay?, stagger?, order?, loop?, intensity?, params?, muted?} */
  function layerInfo (layer, effects) {
    const fx = effects[layer.effect]
    if (!fx) throw new Error('unknown effect ' + layer.effect)
    return {
      ...layer,
      fx,
      phase: fx.phase,
      unit: layer.unit || fx.unit || 'text',
      delay: layer.delay || 0,
      intensity: layer.intensity == null ? 1 : layer.intensity,
      param (name, dflt) {
        if (layer.params && name in layer.params) return layer.params[name]
        if (fx.params && name in fx.params) return fx.params[name]
        return dflt
      }
    }
  }

  function ranks (n, order, seed) {
    const idx = Array.from({ length: n }, (_, i) => i)
    if (order === 'reverse') return idx.map(i => n - 1 - i)
    if (order === 'center' || order === 'edges') {
      const c = (n - 1) / 2
      const dist = idx.map(i => Math.abs(i - c))
      const mx = Math.max(...dist)
      return order === 'center' ? dist : dist.map(d => mx - d)
    }
    if (order === 'random') {
      const perm = idx.slice().sort((a, b) => hash01(seed, a) - hash01(seed, b))
      return idx.map(i => perm.indexOf(i))
    }
    return idx
  }

  /** [u, seconds since this unit's local start] for a layer. */
  function localU (L, rank, maxRank, t, win) {
    const fx = L.fx
    const dur = L.duration != null ? L.duration : (fx.duration != null ? fx.duration : 1)
    const stagger = L.stagger != null ? L.stagger : (fx.stagger || 0)
    if (L.phase === 'in') {
      const t0 = win.start + L.delay + stagger * rank
      return [clamp01((t - t0) / Math.max(dur, 1e-6)), t - t0]
    }
    if (L.phase === 'out') {
      const tEnd = win.end - L.delay - stagger * (maxRank - rank)
      const t0 = tEnd - dur
      return [clamp01((t - t0) / Math.max(dur, 1e-6)), t - t0]
    }
    const loop = L.loop || fx.loop || 'loop'
    const t0 = win.start + L.delay + stagger * rank
    if (loop === 'once') {
      const span = Math.max(1e-6, win.end - win.start - L.delay)
      return [clamp01((t - win.start - L.delay) / span), t - t0]
    }
    let w = (t - t0) / Math.max(dur, 1e-6)
    if (loop === 'pingpong') {
      w = (((w / 2) % 1) + 1) % 1 * 2
      return [w <= 1 ? w : 2 - w, t - t0]
    }
    return [((w % 1) + 1) % 1, t - t0]
  }

  // ---- tracks ---------------------------------------------------------------
  function resolve (value, base, unitSeed, layerSeed, key) {
    if (value && typeof value === 'object' && !Array.isArray(value) && 'rand' in value) {
      const [a, b] = value.rand
      return a + (b - a) * hash01(unitSeed, layerSeed, key, 99)
    }
    if (typeof value === 'string') return value === 'base' ? base : rgb(value)
    return value
  }

  function keys (ks, u, base, unitSeed, layerSeed) {
    if (u <= ks[0][0]) return resolve(ks[0][1], base, unitSeed, layerSeed, 0)
    for (let k = 1; k < ks.length; k++) {
      const [u0, v0] = ks[k - 1]
      const [u1, v1] = ks[k]
      if (u <= u1) {
        let f = u1 <= u0 ? 0 : (u - u0) / (u1 - u0)
        if (ks[k].length > 2) f = EASE[ks[k][2]](f)
        return mix(resolve(v0, base, unitSeed, layerSeed, k - 1), resolve(v1, base, unitSeed, layerSeed, k), f)
      }
    }
    return resolve(ks[ks.length - 1][1], base, unitSeed, layerSeed, ks.length - 1)
  }

  function generator (spec, u, tl, unitSeed, layerSeed) {
    let v
    if (spec.sine) {
      const g = spec.sine
      v = (g.base || 0) + g.amp * Math.sin(2 * Math.PI * (u + (g.phase || 0)))
    } else if (spec.noise) {
      const g = spec.noise
      const step = Math.floor(tl * g.rate)
      v = (spec.base || 0) + g.amp * (2 * hash01(step, g.seed == null ? 1 : g.seed, unitSeed, layerSeed) - 1)
    } else if (spec.flicker) {
      const g = spec.flicker
      const ramp = g.ramp
      if (ramp === 'up' && u <= 0) return 0
      if (ramp === 'up' && u >= 1) return 1
      if (ramp === 'down' && u <= 0) return 1
      if (ramp === 'down' && u >= 1) return 0
      let on = g.on
      if (ramp === 'up') on = on + (1 - on) * u * u
      else if (ramp === 'down') on = on * (1 - u) ** 1.5
      const step = Math.floor(tl * g.rate)
      v = hash01(step, unitSeed, layerSeed, 5) < on ? 1 : (g.low || 0)
    } else {
      throw new Error('unknown generator ' + JSON.stringify(spec))
    }
    if (spec.env) v *= keys(spec.env, u, null, unitSeed, layerSeed)
    return v
  }

  function pathPoint (points, f) {
    const seg = []
    for (let i = 0; i < points.length - 1; i++) seg.push(Math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]))
    const total = seg.reduce((a, b) => a + b, 0) || 1
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

  // ---- content (the only exclusive channel) --------------------------------
  function content (L, e, u, tl, t, win, useed) {
    const c = L.fx.content
    if (c.type === 'scramble') {
      if (u > 0 && u < 1) {
        const cs = c.charset
        const step = Math.floor(tl * (c.rate || 18))
        return [cs[Math.floor(hash01(step, useed, 3) * cs.length)], {}]
      }
      return [e.text, {}]
    }
    if (c.type === 'flap') {
      if (u > 0 && u < 1) {
        const flips = c.flips || 8
        const pos = u * flips
        const n = Math.floor(pos), p = pos - n
        const cs = c.charset
        return [cs[Math.floor(hash01(n, useed, 11) * cs.length)], { sy: Math.max(0.12, Math.abs(Math.cos(Math.PI * p)) ** 0.7) }]
      }
      return [e.text, {}]
    }
    if (c.type === 'count') {
      const f = EASE[c.easing || 'linear'](u)
      const a = Number(L.param('from', 0)), b = Number(L.param('to', 100))
      const n = Math.round(a + (b - a) * f)
      const sep = L.param('separator', '')
      const s = sep ? n.toLocaleString('en-US').replace(/,/g, sep) : String(n)
      return [`${L.param('prefix', '')}${s}${L.param('suffix', '')}`, {}]
    }
    if (c.type === 'countdown') {
      const start = Math.trunc(L.param('from', 3))
      const span = (win.end - win.start) / (start + 1)
      const k = Math.min(start, Math.trunc((t - win.start) / span))
      const p = ((t - win.start) - k * span) / span
      const label = start - k > 0 ? String(start - k) : 'GO!'
      const pop = 1 + 0.45 * (1 - Math.min(1, p / 0.35)) ** 3
      return [label, { scale: pop, opacity: p < 0.82 ? 1 : Math.max(0, 1 - (p - 0.82) / 0.18) }]
    }
    throw new Error(c.type)
  }

  // ---- compile + evaluate ---------------------------------------------------
  /**
   * units: {char:[], word:[], line:[], text:[]} with {index, text, cx, cy, w, h,
   * anc:{level:index}, col, ncol}; caption: {size, colour, start, end, frameW, frameH}.
   */
  function compile (stackIn, units, caption, effects) {
    const warnings = []
    const conflicts = {}                     // layer index -> message (for the GUI)
    const stack = []
    stackIn.forEach((layer, i) => { if (!layer.muted) stack.push({ ...layerInfo(layer, effects), src: i }) })
    const contentLayers = {}
    for (const L of stack) {
      if (L.fx.content) {
        const prev = contentLayers[L.phase]
        if (prev) {
          warnings.push(`'${L.fx.label}' replaces '${prev.fx.label}' (both rewrite the text in the ${L.phase} phase)`)
          conflicts[prev.src] = `replaced by ${L.fx.label}`
        }
        contentLayers[L.phase] = L
      }
    }
    const wholeText = Object.values(contentLayers).filter(L => L.fx.content.type === 'count' || L.fx.content.type === 'countdown')
    let need = stack.map(L => L.unit)
    for (const L of Object.values(contentLayers)) if (L.fx.content.type === 'scramble' || L.fx.content.type === 'flap') need.push('char')
    if (stack.some(L => L.fx.caret)) need.push('char')
    if (wholeText.length) {
      for (const L of stack) {
        if (LEVEL_RANK[L.unit] < LEVEL_RANK.text) {
          warnings.push(`'${wholeText[0].fx.label}' rewrites the whole text, so '${L.fx.label}' runs on the whole text instead of per ${L.unit}`)
          conflicts[L.src] = `whole text (${wholeText[0].fx.label})`
          L.unit = 'text'
        }
      }
      need = ['text']
    }
    const gran = need.length ? need.reduce((a, b) => LEVEL_RANK[b] < LEVEL_RANK[a] ? b : a) : 'text'
    const rankCache = stack.map((L, li) => {
      const n = units[L.unit].length
      const r = ranks(n, L.order || L.fx.order || 'forward', li * 131 + 7)
      return [r, r.length ? Math.max(...r) : 0]
    })
    let orgLevel = null
    for (const L of stack) {
      const tr = L.fx.tracks || {}
      if (('rx' in tr || 'ry' in tr) && LEVEL_RANK[L.unit] > LEVEL_RANK[gran]) {
        if (orgLevel == null || LEVEL_RANK[L.unit] > LEVEL_RANK[orgLevel]) orgLevel = L.unit
      }
    }
    return { stack, units, caption, gran, rankCache, orgLevel, contentLayers, warnings, conflicts,
      win: { start: caption.start, end: caption.end }, base: rgb(caption.colour), em: caption.size }
  }

  function anc (ctx, e, level) {
    return level === ctx.gran ? e : ctx.units[level][e.anc[level]]
  }

  /** Composed state of event unit e at time t: same result as Python's evaluate(). */
  function evaluate (ctx, e, t) {
    const lv = LEVELS.slice(LEVEL_RANK[ctx.gran])
    const acc = {}
    for (const l of lv) acc[l] = { ...NEUTRAL }
    let colour = ctx.base
    let clip = null
    let text = e.text
    const mods = {}
    const em = ctx.em
    ctx.stack.forEach((L, li) => {
      const lvl = L.unit
      const a = anc(ctx, e, lvl)
      const [r, maxRank] = ctx.rankCache[li]
      const [u, tl] = localU(L, r[a.index], maxRank, t, ctx.win)
      const useed = a.index * 7919 + LEVEL_RANK[lvl]
      const lseed = li * 104729 + 13
      const tracks = L.fx.tracks || {}
      for (const ch of Object.keys(tracks)) {
        const spec = tracks[ch]
        if (ch === 'path') {
          const [px, py] = pathPoint(L.param('points'), u)
          acc[lvl].px += px / 100 * ctx.caption.frameW - a.cx
          acc[lvl].py += py / 100 * ctx.caption.frameH - a.cy
          continue
        }
        if (ch === 'clip') {
          const running = (L.phase === 'in' && u < 1) || (L.phase === 'out' && u > 0) || L.phase === 'hold'
          if (!running) continue
          const [l, tp, rr, b] = keys(spec, u, null, useed, lseed)
          const x0 = a.cx - a.w / 2, y0 = a.cy - a.h / 2, x1 = a.cx + a.w / 2, y1 = a.cy + a.h / 2
          const rect = [x0 + (x1 - x0) * l, y0 + (y1 - y0) * tp, x0 + (x1 - x0) * rr, y0 + (y1 - y0) * b]
          clip = clip == null ? rect : [Math.max(clip[0], rect[0]), Math.max(clip[1], rect[1]), Math.min(clip[2], rect[2]), Math.min(clip[3], rect[3])]
          continue
        }
        if (ch === 'colour') { colour = keys(spec, u, colour, useed, lseed); continue }
        let v = (spec && !Array.isArray(spec)) ? generator(spec, u, tl, useed, lseed) : keys(spec, u, null, useed, lseed)
        if (L.intensity !== 1) v = NEUTRAL[ch] + (v - NEUTRAL[ch]) * L.intensity
        if (MUL.has(ch)) acc[lvl][ch] *= v
        else if (MAXC.has(ch)) acc[lvl][ch] = Math.max(acc[lvl][ch], v)
        else acc[lvl][ch] += v
      }
      if (L.fx.content && ctx.contentLayers[L.phase] === L) {
        const [txt, extra] = content(L, e, u, tl, t, ctx.win, useed)
        text = txt
        for (const k of Object.keys(extra)) mods[k] = (k in mods ? mods[k] : 1) * extra[k]
      }
    })
    const tot = { ...NEUTRAL }
    for (const l of lv) {
      for (const ch of Object.keys(acc[l])) {
        const v = acc[l][ch]
        if (MUL.has(ch)) tot[ch] *= v
        else if (MAXC.has(ch)) tot[ch] = Math.max(tot[ch], v)
        else tot[ch] += v
      }
    }
    for (const k of Object.keys(mods)) tot[k] = (k in tot ? tot[k] : 1) * mods[k]
    let x = e.cx, y = e.cy
    if (ctx.gran === 'char' && tot.spacing) x += tot.spacing * em * (e.col - (e.ncol - 1) / 2)
    for (const l of lv) {
      const a = anc(ctx, e, l)
      const s = acc[l]
      if (l !== ctx.gran) {
        let dx = x - a.cx, dy = y - a.cy
        dx *= s.scale * s.sx
        dy *= s.scale * s.sy
        const th = s.rz * Math.PI / 180              // ASS \frz: counter-clockwise
        const ndx = dx * Math.cos(th) + dy * Math.sin(th)
        const ndy = -dx * Math.sin(th) + dy * Math.cos(th)
        x = a.cx + ndx; y = a.cy + ndy
      }
      x += s.x * em + s.px
      y += s.y * em + s.py
    }
    let org = null
    if (ctx.orgLevel != null) {
      const a = anc(ctx, e, ctx.orgLevel)
      const s = acc[ctx.orgLevel]
      org = [a.cx + s.x * em + s.px, a.cy + s.y * em + s.py]
    }
    return { x, y, text, tot, colour, clip, org }
  }

  /** Total seconds a layer occupies in its phase (for the timeline bars). */
  function span (L, units, effects) {
    const info = layerInfo(L, effects)
    const n = (units[info.unit] || []).length || 1
    const fx = info.fx
    const dur = L.duration != null ? L.duration : (fx.duration != null ? fx.duration : 1)
    const stagger = L.stagger != null ? L.stagger : (fx.stagger || 0)
    const r = ranks(n, L.order || fx.order || 'forward', 0)
    return info.delay + dur + stagger * Math.max(0, ...r)
  }

  const api = { LEVELS, LEVEL_RANK, NEUTRAL, EASE, hash01, rgb, ranks, localU, keys, generator, pathPoint, compile, evaluate, span, layerInfo }
  if (typeof module !== 'undefined' && module.exports) module.exports = api
  else root.TextMotion = api
})(typeof window !== 'undefined' ? window : globalThis)
