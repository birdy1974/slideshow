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

export type CollageLayout = 'stack' | 'grid' | 'scatter' | 'filmstrip' | 'fan' | 'masonry' | 'free' | 'template' | 'honeycomb' | 'zigzag' | 'arc' | 'photowall' | 'booth' | 'silhouette' | 'cube'
export type CollageAnim = 'drop' | 'pop' | 'swing' | 'flip' | 'none' | 'fade' | 'slide' | 'rise' | 'tumble' | 'zoom' | 'fold' | 'glitch' | 'ink' | 'brush'
export type CollageExit = 'none' | 'sweep' | 'deal' | 'shuffle'
export type CollageCamera = 'none' | 'pan' | 'zoom' | 'telescope' | 'droste'
export type CollageShape = '4:3' | 'square' | '3:4' | '16:9' | '9:16' | '3:2' | '2:3'
export type CollageBgFit = 'fill' | 'fit' | 'stretch' | 'tile' | 'center' | 'span'
export type CollageFrameShape = 'polaroid' | 'none' | 'rect' | 'rounded' | 'circle' | 'oval' | 'heart' | 'star' | 'diamond' | 'hexagon' | 'triangle' | 'octagon' | 'cloud' | 'arch' | 'ticket'
export type CollageStickers = 'none' | 'tape' | 'pin' | 'mix'

export const COLLAGE_LAYOUTS_ALL: CollageLayout[] = ['stack', 'grid', 'scatter', 'filmstrip', 'fan', 'masonry', 'free', 'template', 'honeycomb', 'zigzag', 'arc', 'photowall', 'booth', 'silhouette', 'cube']
export const COLLAGE_ANIMS_ALL: CollageAnim[] = ['drop', 'pop', 'swing', 'flip', 'fade', 'slide', 'rise', 'tumble', 'zoom', 'fold', 'glitch', 'ink', 'brush', 'none']
export const COLLAGE_FRAME_SHAPES: CollageFrameShape[] = ['polaroid', 'none', 'rect', 'rounded', 'circle', 'oval', 'heart', 'star', 'diamond', 'hexagon', 'triangle', 'octagon', 'cloud', 'arch', 'ticket']

export interface CollageFrame {
  /** Missing = polaroid (white mat + caption strip). */
  shape?: CollageFrameShape
  /** Border thickness as % of the mat width (0 = none, typical 2–8). Missing = 4.5 for polaroid, 3 otherwise. */
  width?: number
  /** Border / mat colour. Missing = #ffffff. */
  color?: string
  /** Corner radius as % of the shorter side, for `rounded`. Missing = 12. */
  radius?: number
  /** Soft drop shadow under the mat. Missing = on. */
  shadow?: boolean
}

export interface CollagePhotoLook {
  filter?: string
  filterAmount?: number
  filterAdjust?: Record<string, number>
  crop?: {
    rect?: { x: number; y: number; w: number; h: number } | null
    degrees?: number | null
    lasso?: [number, number][] | null
    feather?: number | null
  } | null
}

export interface CollagePhoto extends CollagePhotoLook {
  path: string
  name?: string
  /** Seconds after the PREVIOUS photo appears (photo 0: after the slide's
   *  visible hold starts). Missing = the auto stagger default. The slide's
   *  duration follows from these: total = Σ delays + entrance + hold. */
  delay?: number
  /** Mat size multiplier for this photo (1 = the layout's default width,
   *  0.5 = half, 1.5 = larger). Missing = 1. */
  size?: number
  /** Free-layout resting position (percent of the frame). Missing = 50/50. */
  cx?: number
  cy?: number
  /** Per-photo frame override (shape / colour / thickness). Missing = the spec default. */
  frame?: CollageFrame
  /** Free-layout resting tilt in degrees. Missing = 0. */
  rot?: number
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
  /** How the background picture fills the frame. Missing = fill (cover). */
  backgroundFit?: CollageBgFit
  /** Look (filters/crop) applied to the background picture. */
  backgroundLook?: CollagePhotoLook
  /** Seeded random mat sizes between randomSizeMin and randomSizeMax. An
   *  explicit per-photo `size` still wins. */
  randomSize?: boolean
  randomSizeMin?: number
  randomSizeMax?: number
  /** Predefined template id when layout is 'template'. */
  template?: string
  /** Default frame for every photo (per-photo `frame` wins). */
  frame?: CollageFrame
  /** Gutter between photos for grid / photowall / honeycomb, % of the frame. Missing = layout default. */
  gap?: number
  /** Slow pan/zoom inside each photo after it lands (Apple Memories). */
  kenBurns?: boolean
  /** Gentle idle sway after landing (corkboard). */
  sway?: boolean
  /** Corner decorations: washi tape, a pin, or a mix. Missing = none. */
  stickers?: CollageStickers
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

export const COLLAGE_SHAPES: CollageShape[] = ['4:3', '16:9', '3:2', 'square', '2:3', '3:4', '9:16']

function photoAspect (shape: CollageShape | string): number {
  if (shape === 'square') return 1
  if (shape === '3:4') return 3 / 4
  if (shape === '16:9') return 16 / 9
  if (shape === '9:16') return 9 / 16
  if (shape === '3:2') return 3 / 2
  if (shape === '2:3') return 2 / 3
  return 4 / 3
}

export function photoFrame (spec: { frame?: CollageFrame } | null | undefined, photo?: { frame?: CollageFrame } | null): { shape: CollageFrameShape; width: number; color: string; radius: number; shadow: boolean } {
  const a = spec?.frame || {}
  const b = photo?.frame || {}
  const shape = (COLLAGE_FRAME_SHAPES as string[]).includes(b.shape as string) ? b.shape as CollageFrameShape
    : (COLLAGE_FRAME_SHAPES as string[]).includes(a.shape as string) ? a.shape as CollageFrameShape : 'polaroid'
  const rawW = b.width !== undefined ? b.width : a.width
  const width = Number.isFinite(Number(rawW)) ? Math.max(0, Math.min(12, Number(rawW))) : (shape === 'polaroid' ? 4.5 : shape === 'none' ? 0 : 3)
  const color = (typeof b.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(b.color) ? b.color : (typeof a.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(a.color) ? a.color : '#ffffff'))
  const rawR = b.radius !== undefined ? b.radius : a.radius
  const radius = Number.isFinite(Number(rawR)) ? Math.max(0, Math.min(50, Number(rawR))) : 12
  const shadow = (b.shadow !== undefined ? b.shadow : a.shadow) !== false
  return { shape, width, color, radius, shadow }
}

export function frameBorderFrac (frame: { shape: CollageFrameShape; width: number }): number {
  if (frame.shape === 'none') return 0
  return Math.max(0, Math.min(0.12, frame.width / 100))
}

/** Height of the mat for a mat width `w` (% of frame width), as a
 *  percentage of the frame HEIGHT. aspect = frameW / frameH. Polaroid
 *  (the default) keeps the classic white border + caption strip. */
export function matHeight (w: number, shape: CollageShape, aspect: number, frame?: CollageFrame): number {
  const fr = photoFrame({ frame }, null)
  const border = frameBorderFrac(fr) * w
  const bottom = fr.shape === 'polaroid' ? 0.205 * w : border
  const photoW = Math.max(1e-6, w - 2 * border)
  const photoH = photoW / photoAspect(shape)
  return (photoH + border + bottom) / aspect
}

/** CSS clip-path for a non-rectangular photo frame. Undefined = rectangle. */
export function frameClipPath (shape: CollageFrameShape | string | undefined): string | undefined {
  if (shape === 'circle') return 'circle(50%)'
  if (shape === 'oval') return 'ellipse(50% 42%)'
  if (shape === 'diamond') return 'polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)'
  if (shape === 'hexagon') return 'polygon(25% 6.7%, 75% 6.7%, 100% 50%, 75% 93.3%, 25% 93.3%, 0% 50%)'
  if (shape === 'triangle') return 'polygon(50% 4%, 96% 92%, 4% 92%)'
  if (shape === 'octagon') return 'polygon(30% 0%, 70% 0%, 100% 30%, 100% 70%, 70% 100%, 30% 100%, 0% 70%, 0% 30%)'
  if (shape === 'star') return 'polygon(50% 2%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%)'
  if (shape === 'heart') return 'path("M 50 88 C 18 64 2 42 20 22 C 32 10 50 18 50 34 C 50 18 68 10 80 22 C 98 42 82 64 50 88 Z")'
  if (shape === 'cloud') return 'path("M 18 62 C 8 62 8 42 22 40 C 24 22 48 18 56 32 C 68 18 92 28 86 48 C 98 52 96 72 78 70 C 70 82 40 84 28 72 C 18 76 12 70 18 62 Z")'
  if (shape === 'arch') return 'path("M 8 96 L 8 48 A 42 42 0 0 1 92 48 L 92 96 Z")'
  if (shape === 'ticket') return 'polygon(0% 12%, 6% 0%, 94% 0%, 100% 12%, 100% 88%, 94% 100%, 6% 100%, 0% 88%)'
  if (shape === 'rounded') return undefined
  return undefined
}

/** Predefined fixed layouts: magazine mosaics (no tilt) and polaroid-wall
 *  scrapbook pages (tilted overlapping mats). Slot coordinates are % of the
 *  frame; extra photos beyond the slot count overlay with a seeded offset. */
export interface CollageTemplate {
  id: string
  family: 'magazine' | 'polaroid'
  label: string
  hint: string
  slots: Placement[]
}

export const COLLAGE_TEMPLATES: CollageTemplate[] = [
  { id: 'split-v', family: 'magazine', label: '2-up split', hint: 'Two photos side by side', slots: [
    { cx: 26, cy: 50, w: 46, rot: 0 }, { cx: 74, cy: 50, w: 46, rot: 0 },
  ] },
  { id: 'split-h', family: 'magazine', label: '2-up stacked', hint: 'Two photos one above the other', slots: [
    { cx: 50, cy: 27, w: 70, rot: 0 }, { cx: 50, cy: 73, w: 70, rot: 0 },
  ] },
  { id: 'triptych', family: 'magazine', label: 'Triptych', hint: 'Three equal columns', slots: [
    { cx: 18, cy: 50, w: 30, rot: 0 }, { cx: 50, cy: 50, w: 30, rot: 0 }, { cx: 82, cy: 50, w: 30, rot: 0 },
  ] },
  { id: 'trio-left', family: 'magazine', label: '1 + 2 left', hint: 'One large photo on the left, two stacked on the right', slots: [
    { cx: 30, cy: 50, w: 54, rot: 0 }, { cx: 78, cy: 28, w: 36, rot: 0 }, { cx: 78, cy: 72, w: 36, rot: 0 },
  ] },
  { id: 'trio-right', family: 'magazine', label: '1 + 2 right', hint: 'Two stacked on the left, one large on the right', slots: [
    { cx: 22, cy: 28, w: 36, rot: 0 }, { cx: 22, cy: 72, w: 36, rot: 0 }, { cx: 70, cy: 50, w: 54, rot: 0 },
  ] },
  { id: 'trio-top', family: 'magazine', label: '1 + 2 top', hint: 'One wide photo on top, two below', slots: [
    { cx: 50, cy: 28, w: 88, rot: 0 }, { cx: 26, cy: 74, w: 42, rot: 0 }, { cx: 74, cy: 74, w: 42, rot: 0 },
  ] },
  { id: 'quad', family: 'magazine', label: '2 × 2', hint: 'Four equal tiles', slots: [
    { cx: 26, cy: 28, w: 44, rot: 0 }, { cx: 74, cy: 28, w: 44, rot: 0 },
    { cx: 26, cy: 72, w: 44, rot: 0 }, { cx: 74, cy: 72, w: 44, rot: 0 },
  ] },
  { id: 'one-plus-three', family: 'magazine', label: '1 + 3', hint: 'One large left, three stacked right', slots: [
    { cx: 32, cy: 50, w: 56, rot: 0 }, { cx: 80, cy: 20, w: 32, rot: 0 },
    { cx: 80, cy: 50, w: 32, rot: 0 }, { cx: 80, cy: 80, w: 32, rot: 0 },
  ] },
  { id: 'hero-row', family: 'magazine', label: 'Hero + 3', hint: 'Wide hero on top, three across the bottom', slots: [
    { cx: 50, cy: 30, w: 90, rot: 0 }, { cx: 18, cy: 76, w: 28, rot: 0 },
    { cx: 50, cy: 76, w: 28, rot: 0 }, { cx: 82, cy: 76, w: 28, rot: 0 },
  ] },
  { id: 'five-mosaic', family: 'magazine', label: 'Five mosaic', hint: 'Large centre-left with four small around it', slots: [
    { cx: 32, cy: 50, w: 56, rot: 0 }, { cx: 78, cy: 18, w: 30, rot: 0 },
    { cx: 78, cy: 50, w: 30, rot: 0 }, { cx: 78, cy: 82, w: 30, rot: 0 }, { cx: 32, cy: 86, w: 28, rot: 0 },
  ] },
  { id: 'six-grid', family: 'magazine', label: '3 × 2', hint: 'Six equal tiles', slots: [
    { cx: 18, cy: 28, w: 30, rot: 0 }, { cx: 50, cy: 28, w: 30, rot: 0 }, { cx: 82, cy: 28, w: 30, rot: 0 },
    { cx: 18, cy: 72, w: 30, rot: 0 }, { cx: 50, cy: 72, w: 30, rot: 0 }, { cx: 82, cy: 72, w: 30, rot: 0 },
  ] },
  { id: 'polaroid-pile', family: 'polaroid', label: 'Pile', hint: 'A fixed overlapping pile in the middle', slots: [
    { cx: 42, cy: 48, w: 34, rot: -11 }, { cx: 58, cy: 44, w: 34, rot: 8 },
    { cx: 48, cy: 56, w: 36, rot: 3 }, { cx: 36, cy: 40, w: 30, rot: -18 },
    { cx: 64, cy: 58, w: 30, rot: 14 }, { cx: 50, cy: 38, w: 28, rot: -4 },
  ] },
  { id: 'polaroid-diagonal', family: 'polaroid', label: 'Diagonal', hint: 'Photos stepping down from left to right', slots: [
    { cx: 22, cy: 28, w: 32, rot: -8 }, { cx: 40, cy: 40, w: 32, rot: 4 },
    { cx: 58, cy: 52, w: 32, rot: -5 }, { cx: 74, cy: 66, w: 32, rot: 7 },
    { cx: 50, cy: 24, w: 26, rot: 12 },
  ] },
  { id: 'polaroid-rows', family: 'polaroid', label: 'Two rows', hint: 'Two overlapping rows of tilted polaroids', slots: [
    { cx: 22, cy: 32, w: 30, rot: -7 }, { cx: 50, cy: 28, w: 30, rot: 5 }, { cx: 78, cy: 34, w: 30, rot: -4 },
    { cx: 28, cy: 70, w: 30, rot: 6 }, { cx: 56, cy: 74, w: 30, rot: -8 }, { cx: 82, cy: 68, w: 30, rot: 3 },
  ] },
  { id: 'polaroid-stairs', family: 'polaroid', label: 'Staircase', hint: 'A stepped flight of overlapping frames', slots: [
    { cx: 20, cy: 70, w: 30, rot: -6 }, { cx: 36, cy: 56, w: 30, rot: 4 },
    { cx: 52, cy: 42, w: 30, rot: -3 }, { cx: 68, cy: 28, w: 30, rot: 7 },
    { cx: 82, cy: 18, w: 26, rot: -10 },
  ] },
  { id: 'polaroid-heart', family: 'polaroid', label: 'Heart', hint: 'A loose heart-shaped cluster', slots: [
    { cx: 32, cy: 32, w: 28, rot: -14 }, { cx: 68, cy: 32, w: 28, rot: 14 },
    { cx: 22, cy: 52, w: 26, rot: -8 }, { cx: 78, cy: 52, w: 26, rot: 8 },
    { cx: 50, cy: 48, w: 30, rot: 2 }, { cx: 50, cy: 76, w: 28, rot: -3 },
  ] },
  { id: 'polaroid-strip', family: 'polaroid', label: 'Overlapping strip', hint: 'A band of overlapping frames across the middle', slots: [
    { cx: 16, cy: 50, w: 28, rot: -6 }, { cx: 34, cy: 46, w: 28, rot: 5 },
    { cx: 52, cy: 52, w: 28, rot: -4 }, { cx: 70, cy: 47, w: 28, rot: 7 },
    { cx: 86, cy: 53, w: 26, rot: -5 },
  ] },
  { id: 'polaroid-corners', family: 'polaroid', label: 'Corners', hint: 'Four polaroids pinning the corners, one in the middle', slots: [
    { cx: 20, cy: 22, w: 30, rot: -10 }, { cx: 80, cy: 22, w: 30, rot: 9 },
    { cx: 20, cy: 78, w: 30, rot: 7 }, { cx: 80, cy: 78, w: 30, rot: -8 },
    { cx: 50, cy: 50, w: 36, rot: 3 },
  ] },
]

export function collageTemplate (id: string | undefined): CollageTemplate {
  return COLLAGE_TEMPLATES.find(t => t.id === id) || COLLAGE_TEMPLATES[0]
}

function templateSlots (id: string | undefined, n: number): Placement[] {
  const slots = collageTemplate(id).slots
  const out: Placement[] = []
  for (let i = 0; i < n; i++) {
    if (i < slots.length) { out.push({ ...slots[i] }); continue }
    const base = slots[i % slots.length]
    const k = Math.floor(i / slots.length)
    out.push({
      cx: Math.max(8, Math.min(92, base.cx + (hash01(i, 11) - 0.5) * 10 * k)),
      cy: Math.max(10, Math.min(90, base.cy + (hash01(i, 13) - 0.5) * 10 * k)),
      w: base.w * 0.85,
      rot: base.rot + (hash01(i, 17) - 0.5) * 14,
    })
  }
  return out
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
      // Seeded tilt so Shuffle visibly rearranges even a strict grid.
      out.push({ cx: 7 + cellW * (col + 0.5), cy: 12 + cellH * (row + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 10 })
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
      out.push({ cx: 5 + cellWr * (col + 0.5), cy: 12 + cellH * (row + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 8 })
    }
  } else if (spec.layout === 'fan') {
    // Cards fanned out from a point below the frame — each card tilts
    // along its spoke, like a hand of cards offered to the viewer.
    const L = 46
    const spread = Math.min(48, 10 + 7 * n)
    const a0 = n === 1 ? 0 : (hash01(seed, 0, 19) - 0.5) * 10
    const w = n <= 3 ? 36 : n <= 6 ? 30 : 26
    for (let i = 0; i < n; i++) {
      const a = ((n === 1 ? 0 : (spread * (i / (n - 1) - 0.5))) + a0) * Math.PI / 180
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
      for (let i = 0; i < n; i++) out.push({ cx: sim.res[i].cx, cy: sim.res[i].cy, w: ws[i] * fit, rot: (hash01(seed, i, 37) - 0.5) * 6 })
    }
  } else if (spec.layout === 'honeycomb') {
    const cols = n <= 2 ? n : n <= 6 ? 3 : 4
    const rows = Math.ceil(n / cols)
    const gap = Number.isFinite(Number(spec.gap)) ? Math.max(0, Math.min(12, Number(spec.gap))) : 1.6
    const cellW = (100 - 8) / (cols + 0.5)
    const cellH = (100 - 16) / Math.max(1, rows)
    const k = 0.91 / photoAspect(spec.shape) + 0.25
    const w = Math.min(cellW - gap, cellH * 0.82 * aspect / k)
    for (let i = 0; i < n; i++) {
      const row = Math.floor(i / cols)
      const col = i % cols
      const ox = (row % 2) * cellW * 0.5
      out.push({ cx: 6 + ox + cellW * (col + 0.5), cy: 10 + cellH * (row + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 4 })
    }
  } else if (spec.layout === 'zigzag') {
    const cols = Math.max(1, Math.ceil(Math.sqrt(n)))
    const rows = Math.ceil(n / cols)
    const cellW = (100 - 12) / cols
    const cellH = (100 - 18) / rows
    const w = fitWidth(cellW, cellH) * 0.92
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      const ox = (row % 2) * cellW * 0.28
      out.push({ cx: 6 + ox + cellW * (col + 0.5), cy: 10 + cellH * (row + 0.5), w: widthOf(w, i), rot: ((row + col) % 2 ? 1 : -1) * (6 + 4 * hash01(seed, i, 37)) })
    }
  } else if (spec.layout === 'arc') {
    const w = n <= 4 ? 28 : 22
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : i / (n - 1)
      const a = (12 + 156 * t) * Math.PI / 180
      out.push({
        cx: 50 + 40 * Math.cos(a),
        cy: 62 - 34 * Math.sin(a),
        w: widthOf(w, i),
        rot: 90 - a * 180 / Math.PI,
      })
    }
  } else if (spec.layout === 'photowall') {
    const cols = Math.max(1, Math.ceil(Math.sqrt(n)))
    const rows = Math.ceil(n / cols)
    const gap = Number.isFinite(Number(spec.gap)) ? Math.max(0, Math.min(12, Number(spec.gap))) : 0.7
    const cellW = (100 - gap) / cols
    const cellH = (100 - gap) / rows
    const k = 0.91 / photoAspect(spec.shape) + 0.25
    const w = Math.min(cellW - gap, cellH * aspect / k)
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      out.push({ cx: gap / 2 + cellW * (col + 0.5), cy: gap / 2 + cellH * (row + 0.5), w: widthOf(w, i), rot: 0 })
    }
  } else if (spec.layout === 'booth') {
    const w = Math.min(22, 90 / Math.max(1, n))
    for (let i = 0; i < n; i++) {
      out.push({ cx: 50, cy: 14 + (72 / Math.max(1, n)) * (i + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 2 })
    }
  } else if (spec.layout === 'silhouette') {
    // Photos packed along a heart curve (FigrCollage / Canva silhouette).
    const w = n <= 4 ? 22 : n <= 8 ? 16 : 12
    for (let i = 0; i < n; i++) {
      const t = (i / Math.max(1, n)) * 2 * Math.PI
      const hx = 16 * Math.pow(Math.sin(t), 3)
      const hy = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)
      out.push({
        cx: 50 + hx * 2.1 + (hash01(seed, i, 29) - 0.5) * 3,
        cy: 48 - hy * 1.7 + (hash01(seed, i, 31) - 0.5) * 3,
        w: widthOf(w, i),
        rot: (hash01(seed, i, 37) - 0.5) * 14,
      })
    }
  } else if (spec.layout === 'cube') {
    // Three visible isometric faces, leftover photos as a small row underneath.
    const faces = [
      { cx: 48, cy: 48, w: 34, rot: 0 },
      { cx: 70, cy: 44, w: 20, rot: 8 },
      { cx: 48, cy: 28, w: 28, rot: -6 },
    ]
    for (let i = 0; i < n; i++) {
      if (i < 3) out.push({ cx: faces[i].cx, cy: faces[i].cy, w: widthOf(faces[i].w, i), rot: faces[i].rot })
      else {
        const k = i - 3
        const m = n - 3
        out.push({ cx: 18 + (64 / Math.max(1, m)) * (k + 0.5), cy: 84, w: widthOf(16, i), rot: (hash01(seed, i, 37) - 0.5) * 6 })
      }
    }
  } else if (spec.layout === 'free') {
    const w = n <= 3 ? 42 : n <= 6 ? 34 : 30
    for (let i = 0; i < n; i++) {
      const p = spec.photos[i] || { path: '' }
      const cx = Number.isFinite(Number(p.cx)) ? Number(p.cx) : 50
      const cy = Number.isFinite(Number(p.cy)) ? Number(p.cy) : 50
      const rot = Number.isFinite(Number(p.rot)) ? Number(p.rot) : 0
      out.push({
        cx: Math.max(0, Math.min(100, cx)),
        cy: Math.max(0, Math.min(100, cy)),
        w: widthOf(w, i),
        rot,
      })
    }
  } else if (spec.layout === 'template') {
    const slots = templateSlots(spec.template, n)
    for (let i = 0; i < n; i++) out.push({ cx: slots[i].cx, cy: slots[i].cy, w: widthOf(slots[i].w, i), rot: slots[i].rot })
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
 *  0.5..1.5). When randomSize is on and this photo has no explicit size,
 *  a seeded value between randomSizeMin and randomSizeMax is used. */
export function photoSize (spec: CollageSpec, i: number): number {
  const raw = (spec.photos ?? [])[i]?.size as number | string | boolean | undefined | null
  let v = NaN
  if (raw !== undefined && raw !== null && typeof raw !== 'boolean' && !(typeof raw === 'string' && raw.trim() === '')) v = Number(raw)
  if (!Number.isFinite(v) && spec.randomSize === true) {
    const lo = Math.max(0.5, Math.min(1.5, Number.isFinite(Number(spec.randomSizeMin)) ? Number(spec.randomSizeMin) : 0.7))
    const hi = Math.max(0.5, Math.min(1.5, Number.isFinite(Number(spec.randomSizeMax)) ? Number(spec.randomSizeMax) : 1.3))
    const a = Math.min(lo, hi)
    const b = Math.max(lo, hi)
    v = a + (b - a) * hash01(Math.trunc(spec.seed) || 1, i, 41)
  }
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
export const ENTRANCE_LENGTH: Record<CollageAnim, number> = {
  drop: 0.55, pop: 0.5, swing: 2.0, flip: 0.45, none: 0,
  fade: 0.45, slide: 0.5, rise: 0.5, tumble: 0.6, zoom: 0.55,
  fold: 0.55, glitch: 0.5, ink: 0.55, brush: 0.5,
}

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
  } else if (spec.animation === 'fade') {
    const q = clamp01((t - t0) / 0.45)
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: q * q * (3 - 2 * q), dim: 0 }
  } else if (spec.animation === 'slide') {
    const q = clamp01((t - t0) / 0.5)
    const settle = Math.pow(1 - q, 3)
    st = { dx: -28 * settle, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: clamp01((t - t0) / 0.18), dim: 0 }
  } else if (spec.animation === 'rise') {
    const q = clamp01((t - t0) / 0.5)
    const settle = Math.pow(1 - q, 3)
    st = { dx: 0, dy: 22 * settle, rot: 0, scale: 1, scaleX: 1, alpha: clamp01((t - t0) / 0.18), dim: 0 }
  } else if (spec.animation === 'tumble') {
    const q = clamp01((t - t0) / 0.6)
    const settle = Math.pow(1 - q, 3)
    st = { dx: 10 * settle, dy: -18 * settle, rot: 28 * settle, scale: 1 - 0.15 * settle, scaleX: 1, alpha: clamp01((t - t0) / 0.16), dim: 0 }
  } else if (spec.animation === 'zoom') {
    const q = clamp01((t - t0) / 0.55)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: 0, scale: 0.2 + 0.8 * back, scaleX: 1, alpha: clamp01((t - t0) / 0.16), dim: 0 }
  } else if (spec.animation === 'fold') {
    const q = clamp01((t - t0) / 0.55)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: Math.max(0.04, 0.04 + 0.96 * back), alpha: clamp01((t - t0) / 0.14), dim: 0 }
  } else if (spec.animation === 'glitch') {
    const q = clamp01((t - t0) / 0.5)
    const j = (1 - q) * (1 - q)
    const seed = Math.trunc(Number(spec.seed)) || 1
    st = { dx: (hash01(seed, i, 61) - 0.5) * 10 * j, dy: (hash01(seed, i, 63) - 0.5) * 6 * j, rot: (hash01(seed, i, 65) - 0.5) * 8 * j, scale: 1, scaleX: 1 + (hash01(seed, i, 67) - 0.5) * 0.18 * j, alpha: clamp01((t - t0) / 0.12), dim: 0.15 * j }
  } else if (spec.animation === 'ink') {
    const q = clamp01((t - t0) / 0.55)
    const blob = q * q * (3 - 2 * q)
    st = { dx: 0, dy: 0, rot: 0, scale: 0.35 + 0.65 * blob, scaleX: 1, alpha: blob, dim: 0 }
  } else if (spec.animation === 'brush') {
    const q = clamp01((t - t0) / 0.5)
    const settle = Math.pow(1 - q, 3)
    st = { dx: -36 * settle, dy: 0, rot: 0, scale: 1, scaleX: Math.max(0.12, 1 - 0.55 * settle), alpha: clamp01((t - t0) / 0.16), dim: 0 }
  } else {
    // none: the photos are simply part of the slide from the first frame,
    // incoming transition included.
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: 1, dim: 0 }
  }
  if (spec.kenBurns === true) {
    const land = t0 + (ENTRANCE_LENGTH[spec.animation] ?? 0)
    const kb = clamp01((t - land) / Math.max(0.8, collageDuration(spec) * 0.7))
    const seed = Math.trunc(Number(spec.seed)) || 1
    st.scale *= 1 + 0.08 * kb
    st.dx += (hash01(seed, i, 51) - 0.5) * 5 * kb
    st.dy += (hash01(seed, i, 53) - 0.5) * 3.5 * kb
  }
  if (spec.sway === true && (t - t0) > (ENTRANCE_LENGTH[spec.animation] ?? 0) * 0.7) {
    st.rot += 1.5 * Math.sin(t * 2.15 + i * 0.9)
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
 *  so it always stays inside the frame — the FFmpeg zoompan chain
 *  (camera_filter in backend/app/collage.py) and the CSS transform are two
 *  views of the same numbers. */
export interface CameraState { z: number; cx: number; cy: number }

export function cameraState (spec: CollageSpec, t: number, leadIn = 0, aspect = 16 / 9): CameraState {
  const mode = (['pan', 'zoom', 'telescope', 'droste'] as readonly CollageCamera[]).includes(spec.camera as CollageCamera) ? spec.camera as CollageCamera : 'none'
  if (mode === 'none') return { z: 1, cx: 50, cy: 50 }
  const D = Math.max(0.2, collageDuration(spec))
  const raw = clamp01((t - leadIn) / D)
  // Smoothstep the progress so the virtual camera eases in and out — the
  // FFmpeg chain drives zoompan (over a supersampled scene, so it moves on
  // a sub-pixel grid) with this same curve, so preview and MP4 stay in step.
  const p = raw * raw * (3 - 2 * raw)
  if (mode === 'pan') return { z: 1.09, cx: 54 - 8 * p, cy: 50 }
  const pls = placements(spec, aspect)
  const a = pls.length ? pls[pls.length - 1] : { cx: 50, cy: 50, w: 30, rot: 0 }
  let z = 1
  if (mode === 'zoom') z = 1 + 0.35 * p
  else if (mode === 'telescope') z = 1 + 0.9 * p
  else z = 1 + 1.1 * Math.pow(p, 1.4)
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

function frameFields (raw: unknown): CollageFrame | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const f = raw as CollageFrame
  if (!f.shape && f.width === undefined && !f.color && f.radius === undefined && f.shadow === undefined) return undefined
  return photoFrame({ frame: f }, null)
}

function photoLookOf (p: Partial<CollagePhotoLook> | Record<string, unknown>): CollagePhotoLook {
  const out: CollagePhotoLook = {}
  if (typeof p.filter === 'string' && p.filter) out.filter = p.filter
  const amount = num(p.filterAmount)
  if (amount !== undefined) out.filterAmount = clamp01(amount)
  if (p.filterAdjust && typeof p.filterAdjust === 'object') out.filterAdjust = p.filterAdjust as Record<string, number>
  if (p.crop && typeof p.crop === 'object') out.crop = p.crop as CollagePhotoLook['crop']
  return out
}

/** CSS background-size / position for a collage (or text-frame) picture. */
export function bgFitStyle (fit: CollageBgFit | string | undefined): { backgroundSize: string; backgroundPosition: string; backgroundRepeat: string } {
  const f = (['fill', 'fit', 'stretch', 'tile', 'center', 'span'] as const).includes(fit as CollageBgFit) ? fit as CollageBgFit : 'fill'
  if (f === 'fit') return { backgroundSize: 'contain', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  if (f === 'stretch') return { backgroundSize: '100% 100%', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  if (f === 'tile') return { backgroundSize: 'auto', backgroundPosition: '0 0', backgroundRepeat: 'repeat' }
  if (f === 'center') return { backgroundSize: 'auto', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  if (f === 'span') return { backgroundSize: '100% auto', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  return { backgroundSize: 'cover', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
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
    const cx = num(p.cx)
    const cy = num(p.cy)
    const rot = num(p.rot)
    const look = photoLookOf(p)
    photos.push({
      path: p.path,
      name: typeof p.name === 'string' ? p.name : undefined,
      delay: delay !== undefined ? Math.max(0, Math.min(30, delay)) : undefined,
      size: size !== undefined ? Math.max(0.5, Math.min(1.5, size)) : undefined,
      cx: cx !== undefined ? Math.max(0, Math.min(100, cx)) : undefined,
      cy: cy !== undefined ? Math.max(0, Math.min(100, cy)) : undefined,
      rot: rot !== undefined ? Math.max(-45, Math.min(45, rot)) : undefined,
      frame: frameFields(p.frame),
      ...look,
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
  const rmin = num(c.randomSizeMin)
  const rmax = num(c.randomSizeMax)
  const bgLook = c.backgroundLook && typeof c.backgroundLook === 'object' ? photoLookOf(c.backgroundLook) : undefined
  return {
    photos,
    layout: COLLAGE_LAYOUTS_ALL.includes(c.layout as CollageLayout) ? c.layout as CollageLayout : 'stack',
    animation: COLLAGE_ANIMS_ALL.includes(c.animation as CollageAnim) ? c.animation as CollageAnim : 'drop',
    exit: (['sweep', 'deal', 'shuffle'] as readonly CollageExit[]).includes(c.exit as CollageExit) ? c.exit as CollageExit : undefined,
    camera: (['pan', 'zoom', 'telescope', 'droste'] as readonly CollageCamera[]).includes(c.camera as CollageCamera) ? c.camera as CollageCamera : undefined,
    shape: (['4:3', 'square', '3:4', '16:9', '9:16', '3:2', '2:3'] as const).includes(c.shape as CollageShape) ? c.shape as CollageShape : '4:3',
    seed: Number.isFinite(Number(c.seed)) ? Math.trunc(Number(c.seed)) : 1,
    hold: hold !== undefined ? Math.max(0, Math.min(120, hold)) : undefined,
    beatSync: c.beatSync === true ? true : undefined,
    beats: beats.length ? beats : undefined,
    depth: c.depth === true ? true : undefined,
    backgroundImage: typeof c.backgroundImage === 'string' && c.backgroundImage ? c.backgroundImage : undefined,
    backgroundBlur: blur !== undefined ? clamp01(blur) : undefined,
    backgroundFit: (['fill', 'fit', 'stretch', 'tile', 'center', 'span'] as const).includes(c.backgroundFit as CollageBgFit) ? c.backgroundFit as CollageBgFit : undefined,
    backgroundLook: bgLook && (bgLook.filter || bgLook.crop) ? bgLook : undefined,
    randomSize: c.randomSize === true ? true : undefined,
    randomSizeMin: rmin !== undefined ? Math.max(0.5, Math.min(1.5, rmin)) : undefined,
    randomSizeMax: rmax !== undefined ? Math.max(0.5, Math.min(1.5, rmax)) : undefined,
    template: typeof c.template === 'string' && COLLAGE_TEMPLATES.some(t => t.id === c.template) ? c.template : undefined,
    frame: frameFields(c.frame),
    gap: num(c.gap) !== undefined ? Math.max(0, Math.min(12, num(c.gap)!)) : undefined,
    kenBurns: c.kenBurns === true ? true : undefined,
    sway: c.sway === true ? true : undefined,
    stickers: c.stickers === 'tape' || c.stickers === 'pin' || c.stickers === 'mix' ? c.stickers : undefined,
  }
}
