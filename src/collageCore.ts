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

export type CollageLayout = 'stack' | 'grid' | 'scatter'
export type CollageAnim = 'drop' | 'pop' | 'swing' | 'none'
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
export interface PhotoState { dx: number; dy: number; rot: number; scale: number; alpha: number }

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

/** When photo i starts its entrance (seconds from segment start): the sum of
 *  the delays of photos 0..i plus the lead-in handle. */
export function photoStart (spec: CollageSpec, i: number, leadIn = 0): number {
  let t = leadIn
  for (let k = 0; k <= i; k++) t += photoDelay(spec, k)
  return t
}

/** How long an entrance takes from its start until the photo is fully at
 *  rest (drop 0.55 s fall, pop 0.5 s spring, swing ~2 s until the pendulum
 *  has visibly settled, none 0 — photos appear with the slide). */
export const ENTRANCE_LENGTH: Record<CollageAnim, number> = { drop: 0.55, pop: 0.5, swing: 2.0, none: 0 }

const round2 = (v: number) => Math.round(v * 100) / 100

/** The slide duration implied by the photo timings: the last photo's start +
 *  its entrance + the hold after it. With animation 'none' every photo is on
 *  screen from the first frame, so the duration is just the hold. Returns 0
 *  for an empty collage (no timing to derive). */
export function collageDuration (spec: CollageSpec): number {
  const photos = spec.photos ?? []
  if (!photos.length) return 0
  const h = Number(spec.hold)
  const hold = Number.isFinite(h) ? Math.max(0, Math.min(120, h)) : 2
  if (spec.animation === 'none') return round2(hold)
  const last = photoStart(spec, photos.length - 1, 0)
  return round2(last + (ENTRANCE_LENGTH[spec.animation] ?? 0) + hold)
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
export function photoState (spec: CollageSpec, i: number, t: number, leadIn = 0): PhotoState {
  const t0 = photoStart(spec, i, leadIn)
  if (spec.animation === 'drop') {
    const q = clamp01((t - t0) / 0.55)
    const settle = Math.pow(1 - q, 3)          // 1 → 0, outCubic
    return { dx: 0, dy: -26 * settle, rot: -7 * settle, scale: 1, alpha: clamp01((t - t0) / 0.22) }
  }
  if (spec.animation === 'pop') {
    const q = clamp01((t - t0) / 0.5)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    return { dx: 0, dy: 0, rot: 0, scale: 0.55 + 0.45 * back, alpha: clamp01((t - t0) / 0.18) }
  }
  if (spec.animation === 'swing') {
    const tau = Math.max(0, t - t0)
    // Damped pendulum around the pin: 14° amplitude, ~1.6 s period, settles
    // in a few swings (the "pinned photo gallery" motion).
    const rot = 14 * Math.exp(-1.3 * tau) * Math.cos(2 * Math.PI * tau / 1.6)
    return { dx: 0, dy: 0, rot, scale: 1, alpha: clamp01(tau / 0.15) }
  }
  // none: the photos are simply part of the slide from the first frame,
  // incoming transition included.
  return { dx: 0, dy: 0, rot: 0, scale: 1, alpha: 1 }
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
  return {
    photos,
    layout: (['stack', 'grid', 'scatter'] as const).includes(c.layout as CollageLayout) ? c.layout as CollageLayout : 'stack',
    animation: (['drop', 'pop', 'swing', 'none'] as const).includes(c.animation as CollageAnim) ? c.animation as CollageAnim : 'drop',
    shape: (['4:3', 'square', '3:4'] as const).includes(c.shape as CollageShape) ? c.shape as CollageShape : '4:3',
    seed: Number.isFinite(Number(c.seed)) ? Math.trunc(Number(c.seed)) : 1,
    hold: hold !== undefined ? Math.max(0, Math.min(120, hold)) : undefined,
    backgroundImage: typeof c.backgroundImage === 'string' && c.backgroundImage ? c.backgroundImage : undefined,
    backgroundBlur: blur !== undefined ? clamp01(blur) : undefined,
  }
}
