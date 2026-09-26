// Photo-collage layout + choreography — the JavaScript twin of
// backend/app/collage.py. Both engines turn the same collage spec (stored on
// a text-frame item as `collage`) into the same placements and the same
// per-photo motion, so the editor preview and the MP4 agree.
//
// Everything here is pure maths on frame percentages:
//   cx / cy / w   — mat centre x/y and mat width, in % of the frame
//   dx            — x offset in % of the frame WIDTH
//   dy            — y offset in % of the frame HEIGHT
//   rot           — degrees (clockwise, CSS convention)
// The engines convert to their own pixels: CSS in the preview, FFmpeg
// filter expressions in the renderer.

export type CollageLayout = 'stack' | 'grid' | 'scatter' | 'filmstrip' | 'fan' | 'masonry'
export type CollageAnim = 'drop' | 'pop' | 'swing' | 'flip' | 'none'
export type CollageExit = 'none' | 'sweep' | 'deal' | 'shuffle'
export type CollageCamera = 'none' | 'pan' | 'zoom' | 'telescope' | 'droste'
export type CollageShape = '4:3' | 'square' | '3:4'

export interface CollagePhoto {
  path: string
  name?: string
  /** Seconds after the PREVIOUS photo appears (photo 0: after the slide's
   *  visible hold starts). Missing = the auto stagger default. The slide's
   *  duration follows from these: total = Σ delays + entrance + hold. */
  delay?: number
  /** Mat size multiplier for this photo (1 = the layout's default width,
   *  0.5 = half, 1.5 = larger). Missing = 1. */
  size?: number
}

export interface CollageSpec {
  photos: CollagePhoto[]
  layout: CollageLayout
  animation: CollageAnim
  shape: CollageShape
  seed: number
  /** Seconds the finished collage stays on screen after the last photo has
   *  landed. Missing = 2. The slide duration is derived from the photo
   *  timings plus this hold (collageDuration). */
  hold?: number
  /** Snap photo arrivals to the music: with this on, each photo's nominal
   *  arrival rolls forward to the next stored beat (see `beats`). */
  beatSync?: boolean
  /** Beat times in the slide's own hold clock (0 = the hold starts, i.e.
   *  before the first photo appears), seconds, ascending. The editor maps
   *  the soundtrack's detected onsets into this clock and stores the result
   *  here — the render uses the stored list, so preview and MP4 agree. */
  beats?: number[]
  /** Depth push-back: when a new photo lands, the photos already on the
   *  pile shrink back a little and dim (drop / pop / flip animations). */
  depth?: boolean
  /** How the photos leave at the end of the slide (after the hold): sweep
   *  outward from the centre, deal off one-by-one, or scatter in seeded
   *  directions. Missing = they stay on until the transition takes over. */
  exit?: CollageExit
  /** Virtual camera over the composed collage: pan across, or zoom into
   *  the last photo (telescope = deep, droste = accelerating deep zoom). */
  camera?: CollageCamera
  /** Optional library picture behind the photos (path like /photos/x.jpg).
   *  Missing = plain background colour. */
  backgroundImage?: string
  /** Background blur strength 0..1 (0 = sharp). Missing = 0. */
  backgroundBlur?: number
}

export const MAX_COLLAGE_PHOTOS = 12

/** One photo's resting geometry: mat centre (% of frame), mat width (% of
 *  frame width) and resting tilt (degrees). */
export interface Placement { cx: number; cy: number; w: number; rot: number }

/** Animated offsets of one photo at time t (seconds since the slide's own
 *  segment start, lead-in handles included — pass leadIn so entrances start
 *  after the incoming transition handle). */
export interface PhotoState { dx: number; dy: number; rot: number; scale: number; scaleX: number; alpha: number; dim: number }

// Deterministic pseudo-random number in [0, 1) — bit-identical to
// hash01() in backend/app/collage.py (and the text engines' hash).
export function hash01 (...values: number[]): number {
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

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v)

function photoAspect (shape: CollageShape): number {
  if (shape === 'square') return 1
  if (shape === '3:4') return 3 / 4
  return 4 / 3
}

/** Height of the polaroid mat for a mat width `w` (% of frame width), as a
 *  percentage of the frame HEIGHT. aspect = frameW / frameH. */
export function matHeight (w: number, shape: CollageShape, aspect: number): number {
  // White border 4.5% of the mat width on every side, plus a 16% caption
  // strip at the bottom — classic instant-photo proportions.
  const border = 0.045 * w
  const bottom = 0.205 * w
  const photoW = w - 2 * border
  const photoH = photoW / photoAspect(shape)
  return (photoH + border + bottom) / aspect
}

/** Resting placement of every photo. Deterministic given the spec. */
export function placements (spec: CollageSpec, aspect: number): Placement[] {
  const n = Math.max(1, spec.photos.length)
  const seed = Math.trunc(spec.seed) || 1
  const out: Placement[] = []
  const cells = (count: number): { cols: number; rows: number; cellW: number; cellH: number } => {
    const cols = Math.max(1, Math.ceil(Math.sqrt(count)))
    const rows = Math.ceil(count / cols)
    const marginX = 7
    const marginY = 12
    return { cols, rows, cellW: (100 - 2 * marginX) / cols, cellH: (100 - 2 * marginY) / rows, }
  }
  // Largest mat width (as % of frame width) whose mat HEIGHT still fits in a
  // grid cell: mat height % of H = w · k / aspect, with k from matHeight().
  const fitWidth = (cellW: number, cellH: number): number => {
    const k = 0.91 / photoAspect(spec.shape) + 0.25
    const byWidth = cellW * 0.8
    const byHeight = cellH * 0.82 * aspect / k
    return Math.min(byWidth, byHeight)
  }
  // Per-photo size: each photo's mat is the layout width times its multiplier
  // (bigger photos overlap their neighbours — that is the point).
  const widthOf = (base: number, i: number): number => base * photoSize(spec, i)
  if (spec.layout === 'grid') {
    const { cols, cellW, cellH } = cells(n)
    const w = fitWidth(cellW, cellH)
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      out.push({ cx: 7 + cellW * (col + 0.5), cy: 12 + cellH * (row + 0.5), w: widthOf(w, i), rot: 0 })
    }
  } else if (spec.layout === 'scatter') {
    const { cols, cellW, cellH } = cells(n)
    const w = fitWidth(cellW, cellH) * 0.94
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      const cx = 7 + cellW * (col + 0.28 + 0.44 * hash01(seed, i, 29))
      const cy = 12 + cellH * (row + 0.28 + 0.44 * hash01(seed, i, 31))
      out.push({ cx, cy, w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 26 })
    }
  } else if (spec.layout === 'filmstrip') {
    // A horizontal band of overlapping frames, like film frames edge to
    // edge — one row up to 5 photos, two rows beyond.
    const rows = n <= 5 ? 1 : 2
    const perRow = Math.ceil(n / rows)
    const firstRow = n - perRow * (rows - 1)
    const k = 0.91 / photoAspect(spec.shape) + 0.25
    for (let i = 0; i < n; i++) {
      const row = i < firstRow ? 0 : 1
      const cols = row === 0 ? firstRow : perRow
      const col = row === 0 ? i : i - firstRow
      const cellWr = (100 - 2 * 5) / cols
      const cellH = (100 - 2 * 12) / rows
      const w = Math.min(cellWr * 1.1, cellH * 0.82 * aspect / k)
      out.push({ cx: 5 + cellWr * (col + 0.5), cy: 12 + cellH * (row + 0.5), w: widthOf(w, i), rot: 0 })
    }
  } else if (spec.layout === 'fan') {
    // Cards fanned out from a point below the frame — each card tilts
    // along its spoke, like a hand of cards offered to the viewer.
    const L = 46
    const spread = Math.min(48, 10 + 7 * n)
    const w = n <= 3 ? 36 : n <= 6 ? 30 : 26
    for (let i = 0; i < n; i++) {
      const a = n === 1 ? 0 : (spread * (i / (n - 1) - 0.5)) * Math.PI / 180
      out.push({
        cx: 50 + L * aspect * Math.sin(a),
        cy: 96 - L * Math.cos(a),
        w: widthOf(w, i),
        rot: a * 180 / Math.PI,
      })
    }
  } else if (spec.layout === 'masonry') {
    // Pinterest-style columns: seeded size variety, each photo stacked into
    // the shortest column (a single photo is simply centred).
    if (n === 1) {
      out.push({ cx: 50, cy: 50, w: widthOf(40, 0), rot: 0 })
    } else {
      const cols = n <= 2 ? 2 : n <= 9 ? 3 : 4
      const marginX = 6
      const cellW = (100 - 2 * marginX) / cols
      const k = 0.91 / photoAspect(spec.shape) + 0.25
      const base = cellW * 0.88
      const ws: number[] = []
      for (let i = 0; i < n; i++) ws.push(base * (0.82 + 0.36 * hash01(seed, i, 41)) * photoSize(spec, i))
      const simulate = (scale: number) => {
        const fills = new Array<number>(cols).fill(0)
        const res: { cx: number; cy: number }[] = []
        const gap = 2.5 * scale
        for (let i = 0; i < n; i++) {
          let c = 0
          for (let j = 1; j < cols; j++) if (fills[j] < fills[c] - 1e-9) c = j
          const mh = ws[i] * scale * k / aspect
          res.push({ cx: marginX + cellW * (c + 0.5), cy: 12 + fills[c] + gap / 2 + mh / 2 })
          fills[c] += gap + mh
        }
        return { res, maxFill: Math.max(...fills) }
      }
      let sim = simulate(1)
      let fit = 1
      if (sim.maxFill > 76) { fit = 76 / sim.maxFill; sim = simulate(fit) }
      for (let i = 0; i < n; i++) out.push({ cx: sim.res[i].cx, cy: sim.res[i].cy, w: ws[i] * fit, rot: 0 })
    }
  } else {
    // stack: overlapping polaroids around the middle, seeded tilts
    const w = n <= 3 ? 42 : n <= 6 ? 34 : 30
    for (let i = 0; i < n; i++) {
      if (i === 0) { out.push({ cx: 50, cy: 46, w: widthOf(w, i), rot: (hash01(seed, i, 17) - 0.5) * 22 }); continue }
      const ang = hash01(seed, i, 11) * 2 * Math.PI
      const r = 5 + hash01(seed, i, 13) * 8
      out.push({
        cx: 50 + Math.cos(ang) * r * 0.9,
        cy: 46 + Math.sin(ang) * r / aspect,
        w: widthOf(w, i),
        rot: (hash01(seed, i, 17) - 0.5) * 22,
      })
    }
  }
  return out
}

/** Auto-stagger default for photo i's delay (seconds). Photo 0 appears 0.15 s
 *  after the hold starts; the rest spread over at most ~2.4 s, capped at
 *  0.32 s apart. These are the values an untouched collage uses. */
export function defaultDelay (n: number, i: number): number {
  if (i <= 0) return 0.15
  return n > 1 ? Math.min(0.32, 2.4 / (n - 1)) : 0
}

/** Photo i's delay in seconds — the stored value if present, the auto default
 *  otherwise. Clamped to 0..30. */
export function photoDelay (spec: CollageSpec, i: number): number {
  const photos = spec.photos ?? []
  const raw = photos[i]?.delay as number | string | boolean | undefined | null
  let d = NaN
  if (raw !== undefined && raw !== null && typeof raw !== 'boolean' && !(typeof raw === 'string' && raw.trim() === '')) d = Number(raw)
  if (!Number.isFinite(d)) d = defaultDelay(photos.length, i)
  return Math.max(0, Math.min(30, d))
}

/** Photo i's mat size multiplier — the stored value if present (clamped to
 *  0.5..1.5), 1 otherwise. */
export function photoSize (spec: CollageSpec, i: number): number {
  const raw = (spec.photos ?? [])[i]?.size as number | string | boolean | undefined | null
  let v = NaN
  if (raw !== undefined && raw !== null && typeof raw !== 'boolean' && !(typeof raw === 'string' && raw.trim() === '')) v = Number(raw)
  if (!Number.isFinite(v)) v = 1
  return Math.max(0.5, Math.min(1.5, v))
}

/** The next stored beat at or after time t (in the hold clock), or null when
 *  the beat list runs out first. */
export function nextBeat (beats: number[], t: number): number | null {
  for (const b of beats) if (b >= t - 1e-9) return b
  return null
}

/** When photo i starts its entrance (seconds from segment start): the sum of
 *  the delays of photos 0..i plus the lead-in handle. With beat sync on, the
 *  nominal time rolls forward to the next stored beat — several photos may
 *  share a beat (the "pile lands on the drop" reveal). */
export function photoStart (spec: CollageSpec, i: number, leadIn = 0): number {
  let hold = 0
  for (let k = 0; k <= i; k++) hold += photoDelay(spec, k)
  if (spec.beatSync === true) {
    const beats = (Array.isArray(spec.beats) ? spec.beats : []).filter(b => typeof b === 'number' && Number.isFinite(b))
    const snapped = nextBeat(beats, hold)
    if (snapped !== null) hold = snapped
  }
  return leadIn + hold
}

/** How long an entrance takes from its start until the photo is fully at
 *  rest (drop 0.55 s fall, pop 0.5 s spring, swing ~2 s until the pendulum
 *  has visibly settled, none 0 — photos appear with the slide). */
export const ENTRANCE_LENGTH: Record<CollageAnim, number> = { drop: 0.55, pop: 0.5, swing: 2.0, flip: 0.45, none: 0 }

// ---------------------------------------------------------------------------
// Exits - how the photos leave after the hold
// ---------------------------------------------------------------------------

/** How long one photo's exit fly takes. */
export const EXIT_LENGTH: Record<Exclude<CollageExit, 'none'>, number> = { sweep: 0.45, deal: 0.32, shuffle: 0.4 }
/** Delay between consecutive photos leaving. */
export const EXIT_STAGGER: Record<Exclude<CollageExit, 'none'>, number> = { sweep: 0.05, deal: 0.22, shuffle: 0.1 }

/** The spec's exit mode, sanitised. */
export function exitMode (spec: CollageSpec): CollageExit {
  return (['sweep', 'deal', 'shuffle'] as readonly CollageExit[]).includes(spec.exit as CollageExit) ? spec.exit as CollageExit : 'none'
}

/** When photo i starts leaving, relative to the end of the hold. 'deal'
 *  clears the top of the pile first (photo n-1 leaves first); the others
 *  go in story order. */
export function exitOffset (spec: CollageSpec, i: number): number {
  const mode = exitMode(spec)
  if (mode === 'none') return 0
  const n = spec.photos?.length ?? 0
  if (mode === 'deal') return EXIT_STAGGER.deal * Math.max(0, n - 1 - i)
  return EXIT_STAGGER[mode] * i
}

/** Total seconds the exit adds to the slide (the longest offset + fly). */
export function exitTotal (spec: CollageSpec): number {
  const mode = exitMode(spec)
  if (mode === 'none') return 0
  const n = spec.photos?.length ?? 0
  if (!n) return 0
  return EXIT_LENGTH[mode] + Math.max(exitOffset(spec, 0), exitOffset(spec, n - 1))
}

const round2 = (v: number) => Math.round(v * 100) / 100

/** The slide duration before any exit: the last photo's start + its
 *  entrance + the hold after it ('none' photos are all on screen from the
 *  first frame, so just the hold). 0 for an empty collage. */
export function baseDuration (spec: CollageSpec): number {
  const photos = spec.photos ?? []
  if (!photos.length) return 0
  const h = Number(spec.hold)
  const hold = Number.isFinite(h) ? Math.max(0, Math.min(120, h)) : 2
  if (spec.animation === 'none') return round2(hold)
  const last = photoStart(spec, photos.length - 1, 0)
  return round2(last + (ENTRANCE_LENGTH[spec.animation] ?? 0) + hold)
}

/** The slide duration implied by the photo timings: entrances + hold + the
 *  exit fly at the end. This is what the editor writes into the slide. */
export function collageDuration (spec: CollageSpec): number {
  const photos = spec.photos ?? []
  if (!photos.length) return 0
  return round2(baseDuration(spec) + exitTotal(spec))
}

/** Background blur: one CSS blur() radius in px on the 1920-wide stage (the
 *  preview draws it at stage scale, so it matches the render). */
export function bgBlurCssPx (blur: number): number {
  return 2 + 58 * clamp01(Number.isFinite(blur) ? blur : 0)
}

/** Background blur: FFmpeg boxblur luma radius for the same strength. */
export function bgBlurRadius (blur: number): number {
  return Math.max(1, Math.round(bgBlurCssPx(blur) / 2))
}

/** Animated state of photo i at segment time t. */
export function photoState (spec: CollageSpec, i: number, t: number, leadIn = 0, aspect = 16 / 9): PhotoState {
  const t0 = photoStart(spec, i, leadIn)
  const push = pushDepth(spec, i, t, leadIn)
  let st: PhotoState
  if (spec.animation === 'drop') {
    const q = clamp01((t - t0) / 0.55)
    const settle = Math.pow(1 - q, 3)          // 1 → 0, outCubic
    st = { dx: 0, dy: -26 * settle, rot: -7 * settle, scale: 1 - DEPTH.scale * push, scaleX: 1, alpha: clamp01((t - t0) / 0.22), dim: DEPTH.dim * push }
  } else if (spec.animation === 'pop') {
    const q = clamp01((t - t0) / 0.5)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: 0, scale: (0.55 + 0.45 * back) * (1 - DEPTH.scale * push), scaleX: 1, alpha: clamp01((t - t0) / 0.18), dim: DEPTH.dim * push }
  } else if (spec.animation === 'flip') {
    // A card flipping open around its vertical axis: edge-on (4 % width)
    // with an outBack overshoot, straightening as it settles.
    const q = clamp01((t - t0) / 0.45)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: -4 * (1 - q), scale: 1 - DEPTH.scale * push, scaleX: Math.max(0.04, 0.04 + 0.96 * back), alpha: clamp01((t - t0) / 0.15), dim: DEPTH.dim * push }
  } else if (spec.animation === 'swing') {
    const tau = Math.max(0, t - t0)
    // Damped pendulum around the pin: 14° amplitude, ~1.6 s period, settles
    // in a few swings (the "pinned photo gallery" motion).
    const rot = 14 * Math.exp(-1.3 * tau) * Math.cos(2 * Math.PI * tau / 1.6)
    st = { dx: 0, dy: 0, rot, scale: 1, scaleX: 1, alpha: clamp01(tau / 0.15), dim: 0 }
  } else {
    // none: the photos are simply part of the slide from the first frame,
    // incoming transition included.
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: 1, dim: 0 }
  }
  // Exit: after the hold (and every entrance) the photos leave the frame.
  const mode = exitMode(spec)
  if (mode !== 'none') {
    const seed = Math.trunc(Number(spec.seed)) || 1
    const te0 = leadIn + baseDuration(spec) + exitOffset(spec, i)
    const qe = clamp01((t - te0) / EXIT_LENGTH[mode])
    if (qe > 0) {
      const ease = qe * qe                       // accelerating fly
      if (mode === 'sweep') {
        // Outward through the photo's own anchor direction.
        const pl = placements(spec, aspect)[i]
        const vx = (pl?.cx ?? 50) - 50
        const vy = (pl?.cy ?? 50) - 50
        const len = Math.hypot(vx, vy)
        const dirx = len < 1e-6 ? 0 : vx / len
        const diry = len < 1e-6 ? -1 : vy / len
        st.dx += 90 * dirx * ease
        st.dy += 90 * diry * ease
        st.rot += (hash01(seed, i, 71) - 0.5) * 20 * qe
      } else if (mode === 'deal') {
        // Dealt off to the right, one card at a time, with a little arc.
        st.dx += 90 * ease
        st.dy += -6 * Math.sin(Math.PI * qe)
        st.rot += 25 * qe
      } else {
        // Scattered off in seeded directions.
        const a = hash01(seed, i, 73) * 2 * Math.PI
        st.dx += 75 * Math.cos(a) * ease
        st.dy += 55 * Math.sin(a) * ease
        st.rot += (hash01(seed, i, 75) - 0.5) * 40 * qe
      }
      st.alpha *= 1 - clamp01((qe - 0.75) / 0.25)
    }
  }
  return st
}

/** Depth push-back: how many "pushed back" units photo i has accumulated by
 *  time t — every photo that lands after it pushes it back a little (eased
 *  over DEPTH.span seconds, starting partway through the newcomer's
 *  entrance), capped at DEPTH.max. 0 unless the spec asks for depth and the
 *  animation can show it (drop / pop). */
export function pushDepth (spec: CollageSpec, i: number, t: number, leadIn = 0): number {
  if (spec.depth !== true) return 0
  const anim = spec.animation
  if (anim !== 'drop' && anim !== 'pop' && anim !== 'flip') return 0
  const E = ENTRANCE_LENGTH[anim]
  const n = spec.photos?.length ?? 0
  let p = 0
  for (let j = i + 1; j < n; j++) {
    const tj = photoStart(spec, j, leadIn)
    const q = clamp01((t - (tj + DEPTH.delay * E)) / DEPTH.span)
    p += q * q * (3 - 2 * q)                   // smoothstep
  }
  return Math.min(DEPTH.max, p)
}

/** Virtual camera over the composed collage at segment time t: zoom factor
 *  z (1 = whole frame) and the window centre (cx / cy in % of the frame).
 *  'pan' drifts across a slightly zoomed frame; the zoom family centres on
 *  the LAST photo's anchor (the top of the pile) and the window is clamped
 *  so it always stays inside the frame — the FFmpeg crop/zoompan and the
 *  CSS transform are two views of the same numbers. */
export interface CameraState { z: number; cx: number; cy: number }

export function cameraState (spec: CollageSpec, t: number, leadIn = 0, aspect = 16 / 9): CameraState {
  const mode = (['pan', 'zoom', 'telescope', 'droste'] as readonly CollageCamera[]).includes(spec.camera as CollageCamera) ? spec.camera as CollageCamera : 'none'
  if (mode === 'none') return { z: 1, cx: 50, cy: 50 }
  const D = Math.max(0.2, collageDuration(spec))
  const p = clamp01((t - leadIn) / D)
  if (mode === 'pan') return { z: 1.09, cx: 54 - 8 * p, cy: 50 }
  const pls = placements(spec, aspect)
  const a = pls.length ? pls[pls.length - 1] : { cx: 50, cy: 50, w: 30, rot: 0 }
  let z = 1
  if (mode === 'zoom') z = 1 + 0.35 * p
  else if (mode === 'telescope') z = 1 + 0.9 * (p * p * (3 - 2 * p))
  else z = 1 + 1.1 * Math.pow(p, 2.2)
  const half = 50 / z
  return {
    z,
    cx: Math.min(100 - half, Math.max(half, a.cx)),
    cy: Math.min(100 - half, Math.max(half, a.cy)),
  }
}

/** Depth push-back tuning — mirrored as DEPTH in backend/app/collage.py. */
export const DEPTH = { scale: 0.06, dim: 0.11, max: 4, delay: 0.4, span: 0.35 }

/** Map music-track beat times (file-local seconds) into a slide's own hold
 *  clock (0 = the hold starts). The track's kept region [trimStart, trimEnd)
 *  plays at trackStartTimeline on the timeline; the slide's hold starts at
 *  holdStartTimeline. Pure data mapping — the editor runs it and stores the
 *  result as the spec's `beats`, which both engines then use verbatim. */
export function slideLocalBeats (beats: number[], trackStartTimeline: number, trimStart: number, trimEnd: number, holdStartTimeline: number): number[] {
  const out: number[] = []
  for (const b of beats) {
    if (!Number.isFinite(b)) continue
    if (b < trimStart) continue
    if (trimEnd > 0 && b >= trimEnd) continue
    const local = trackStartTimeline + (b - trimStart) - holdStartTimeline
    if (local < 0 || local > 600) continue
    out.push(Math.round(local * 1000) / 1000)
  }
  return out.sort((a, b) => a - b)
}

/** Rotation pivot: swinging photos hang from a pin at the mat's top edge. */
export function pinAnchor (spec: CollageSpec): boolean {
  return spec.animation === 'swing'
}

/** Normalise a stored collage spec: unknown layouts/anims fall back, the
 *  photo list is capped and deduplicated by path, the seed defaults, the
 *  timing/background fields are sanitised. */
/** Strict number: booleans and blank strings are "not set" (the Python twin
 *  reads them the same way), anything numeric-looking is parsed. */
function num (v: unknown): number | undefined {
  if (v === undefined || v === null || typeof v === 'boolean') return undefined
  if (typeof v === 'string' && v.trim() === '') return undefined
  const n = Number(v)
  return Number.isFinite(n) ? n : undefined
}

export function normalizeCollage (raw: unknown): CollageSpec | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const c = raw as Partial<CollageSpec>
  const photos: CollagePhoto[] = []
  const seen = new Set<string>()
  for (const p of Array.isArray(c.photos) ? c.photos : []) {
    if (!p || typeof p.path !== 'string' || !p.path) continue
    if (seen.has(p.path)) continue
    seen.add(p.path)
    const delay = num(p.delay)
    const size = num(p.size)
    photos.push({
      path: p.path,
      name: typeof p.name === 'string' ? p.name : undefined,
      delay: delay !== undefined ? Math.max(0, Math.min(30, delay)) : undefined,
      size: size !== undefined ? Math.max(0.5, Math.min(1.5, size)) : undefined,
    })
    if (photos.length >= MAX_COLLAGE_PHOTOS) break
  }
  if (!photos.length) return undefined
  const hold = num(c.hold)
  const blur = num(c.backgroundBlur)
  // Beat list: finite numbers, clamped, rounded to ms, ascending, de-duped.
  const rawBeats = Array.isArray(c.beats) ? c.beats : []
  const beatsIn: number[] = []
  for (const b of rawBeats) {
    const v = num(b)
    if (v === undefined) continue
    beatsIn.push(Math.round(Math.max(0, Math.min(600, v)) * 1000) / 1000)
  }
  beatsIn.sort((a, b) => a - b)
  const beats = beatsIn.filter((b, i) => i === 0 || b - beatsIn[i - 1] > 0.001).slice(0, 1200)
  return {
    photos,
    layout: (['stack', 'grid', 'scatter', 'filmstrip', 'fan', 'masonry'] as const).includes(c.layout as CollageLayout) ? c.layout as CollageLayout : 'stack',
    animation: (['drop', 'pop', 'swing', 'flip', 'none'] as const).includes(c.animation as CollageAnim) ? c.animation as CollageAnim : 'drop',
    exit: (['sweep', 'deal', 'shuffle'] as readonly CollageExit[]).includes(c.exit as CollageExit) ? c.exit as CollageExit : undefined,
    camera: (['pan', 'zoom', 'telescope', 'droste'] as readonly CollageCamera[]).includes(c.camera as CollageCamera) ? c.camera as CollageCamera : undefined,
    shape: (['4:3', 'square', '3:4'] as const).includes(c.shape as CollageShape) ? c.shape as CollageShape : '4:3',
    seed: Number.isFinite(Number(c.seed)) ? Math.trunc(Number(c.seed)) : 1,
    hold: hold !== undefined ? Math.max(0, Math.min(120, hold)) : undefined,
    beatSync: c.beatSync === true ? true : undefined,
    beats: beats.length ? beats : undefined,
    depth: c.depth === true ? true : undefined,
    backgroundImage: typeof c.backgroundImage === 'string' && c.backgroundImage ? c.backgroundImage : undefined,
    backgroundBlur: blur !== undefined ? clamp01(blur) : undefined,
  }
}
