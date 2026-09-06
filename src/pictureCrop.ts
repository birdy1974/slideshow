// Cut & crop for pictures and movies — the browser half of the shared model.
//
// The same numbers live on the media item (`item.crop`) and are turned into
// FFmpeg filters by backend/app/picture_crop.py and into canvas/CSS here, so
// the timeline, the lightbox, the editor and the MP4 agree. Nothing is ever
// written to the file: /photos and /videos are read-only mounts, exactly like
// `rotation` and the picture looks.
//
// Coordinate space (identical on both sides):
//   1. the picture turned by the item's whole-quarter `rotation`
//   2. straightened by `degrees` and zoomed to the largest inscribed rectangle,
//      so the rotated corners never show
//   3. `rect` — fractions x/y/w/h of that straightened view
//   4. `lasso` — a polygon in fractions of the *cropped* view; its interior is
//      cut away and filled with a blurred copy of the same picture
//
// Anything missing, malformed or out of range falls back to "no crop".

export type CropRect = { x: number; y: number; w: number; h: number }
export type LassoPoint = [number, number]
export type PictureCrop = {
  rect?: CropRect | null
  degrees?: number | null
  lasso?: LassoPoint[] | null
  feather?: number | null
}
/** Anything that can carry a crop — a MediaItem or a loose editor draft. */
export type Cropish = { crop?: PictureCrop | null; rotation?: number | null }
export type ResolvedCrop = { rect: CropRect; degrees: number; lasso: LassoPoint[] | null; feather: number }
/** Intrinsic pixel size of the file as stored (before the quarter turn). */
export type Intrinsic = { width: number; height: number }

// Must stay in sync with backend/app/picture_crop.py — the test suite reads
// this file and compares the constants.
export const MAX_STRAIGHTEN = 15
export const MIN_CROP = 0.05
export const MIN_LASSO_POINTS = 3
export const MAX_LASSO_POINTS = 24
export const DEFAULT_FEATHER = 0.35
export const LASSO_MASK_SIZE = 512

export const FULL_CROP: CropRect = { x: 0, y: 0, w: 1, h: 1 }

/** Aspect choices for the crop frame. `null` = free, `0` = the picture's own. */
export const CROP_ASPECTS: { id: string; label: string; ratio: number | null }[] = [
  { id: 'free', label: 'Free', ratio: null },
  { id: 'original', label: 'Original', ratio: 0 },
  { id: 'frame', label: 'Frame', ratio: 16 / 9 },
  { id: '16:9', label: '16:9', ratio: 16 / 9 },
  { id: '4:3', label: '4:3', ratio: 4 / 3 },
  { id: '3:2', label: '3:2', ratio: 3 / 2 },
  { id: '1:1', label: '1:1', ratio: 1 },
  { id: '9:16', label: '9:16', ratio: 9 / 16 },
]

/** Mirror of Python's `_number()`: strict, so `null`/`[]` never become 0. */
const finite = (value: unknown): number | null => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'boolean') return value ? 1 : 0
  if (typeof value === 'string' && value.trim() !== '') {
    const number = Number(value)
    return Number.isFinite(number) ? number : null
  }
  return null
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}

const clamp01 = (value: number) => Math.min(1, Math.max(0, value))

/** The whole picture counts as "no crop", so an untouched rect is dropped. */
const isFull = (rect: CropRect) =>
  Math.abs(rect.x) < 0.002 && Math.abs(rect.y) < 0.002 && Math.abs(rect.w - 1) < 0.002 && Math.abs(rect.h - 1) < 0.002

export function normalizeRect(value: unknown): CropRect | null {
  const source = record(value)
  const x = finite(source.x), y = finite(source.y), w = finite(source.w), h = finite(source.h)
  if (x === null || y === null || w === null || h === null) return null
  const width = Math.min(1, Math.max(MIN_CROP, w))
  const height = Math.min(1, Math.max(MIN_CROP, h))
  const rect = {
    x: Math.min(Math.max(0, x), 1 - width),
    y: Math.min(Math.max(0, y), 1 - height),
    w: width,
    h: height,
  }
  return isFull(rect) ? null : rect
}

/** Shoelace area in fraction space — used to drop polygons that cover nothing. */
export function polygonArea(points: LassoPoint[]): number {
  let total = 0
  for (let index = 0; index < points.length; index++) {
    const [x, y] = points[index]
    const [x2, y2] = points[(index + 1) % points.length]
    total += x * y2 - x2 * y
  }
  return total / 2
}

export function normalizeLasso(value: unknown): LassoPoint[] | null {
  if (!Array.isArray(value)) return null
  const points: LassoPoint[] = []
  for (const entry of value) {
    let x: number | null = null
    let y: number | null = null
    if (Array.isArray(entry) && entry.length >= 2) {
      x = finite(entry[0]); y = finite(entry[1])
    } else if (entry && typeof entry === 'object') {
      const source = entry as Record<string, unknown>
      x = finite(source.x); y = finite(source.y)
    }
    if (x === null || y === null) continue
    points.push([clamp01(x), clamp01(y)])
    if (points.length >= MAX_LASSO_POINTS) break
  }
  if (points.length < MIN_LASSO_POINTS) return null
  if (Math.abs(polygonArea(points)) < 1e-4) return null
  return points
}

/**
 * Flatten a stored crop into the numbers both renderers use. `null` means the
 * clip renders exactly as the untouched file.
 */
export function normalizeCrop(item?: Cropish | null): ResolvedCrop | null {
  const raw = (item as Cropish | null | undefined)?.crop
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const stored = record(raw)
  const rect = normalizeRect(stored.rect)
  let degrees = finite(stored.degrees) ?? 0
  degrees = Math.min(MAX_STRAIGHTEN, Math.max(-MAX_STRAIGHTEN, degrees))
  if (Math.abs(degrees) < 0.05) degrees = 0
  const lasso = normalizeLasso(stored.lasso)
  const feather = clamp01(finite(stored.feather) ?? DEFAULT_FEATHER)
  if (!rect && !degrees && !lasso) return null
  return { rect: rect ?? { ...FULL_CROP }, degrees, lasso, feather }
}

export function hasCrop(item?: Cropish | null): boolean {
  return normalizeCrop(item) !== null
}

/** Quarter turns, clockwise — the same normalisation the renderer uses. */
export function normalizeRotation(value: unknown): number {
  const degrees = finite(value) ?? 0
  return ((Math.round(degrees / 90) * 90) % 360 + 360) % 360
}

/**
 * How far the picture must be zoomed after straightening for the rotated
 * corners to disappear: k = max(cos θ + sin θ / a, a·sin θ + cos θ) with
 * a = width/height. `picture_crop.inscribed_zoom` is the identical formula and
 * the FFmpeg crop expression this module's geometry is checked against.
 */
export function inscribedZoom(aspect: number, degrees: number): number {
  const angle = Math.abs(degrees) * Math.PI / 180
  const cos = Math.cos(angle)
  const sin = Math.sin(angle)
  if (!(aspect > 0) || sin === 0) return 1
  return Math.max(cos + sin / aspect, aspect * sin + cos)
}

/**
 * Where the crop rectangle sits in *rotated picture* fractions once the
 * straightening zoom is folded in. Everything downstream (canvas, CSS sprite,
 * FFmpeg) works from this rectangle, so the three cannot drift apart.
 */
export function cropGeometry(crop: ResolvedCrop, rotated: Intrinsic) {
  const aspect = rotated.height ? rotated.width / rotated.height : 0
  const zoom = inscribedZoom(aspect, crop.degrees)
  const rect = {
    x: 0.5 - 0.5 / zoom + crop.rect.x / zoom,
    y: 0.5 - 0.5 / zoom + crop.rect.y / zoom,
    w: crop.rect.w / zoom,
    h: crop.rect.h / zoom,
  }
  // The crop's own aspect, in pixels, is what the frame preview must show.
  const outAspect = (crop.rect.w * rotated.width) / (crop.rect.h * rotated.height)
  return { zoom, rect, outAspect }
}

/** Pixel size after the item's quarter turn. */
export function rotatedSize(intrinsic: Intrinsic, rotation: unknown): Intrinsic {
  const turn = normalizeRotation(rotation)
  return turn === 90 || turn === 270
    ? { width: intrinsic.height, height: intrinsic.width }
    : { width: intrinsic.width, height: intrinsic.height }
}

/**
 * Resize a rectangle around its centre so the crop has `ratio` as its pixel
 * aspect (w·W)/(h·H). `ratio` null = free, 0 = the picture's own aspect. The
 * result always stays inside the picture and above MIN_CROP.
 */
export function rectForAspect(rect: CropRect, ratio: number | null, intrinsic: Intrinsic): CropRect {
  if (ratio === null) return { ...rect }
  const { width, height } = intrinsic
  if (!width || !height) return { ...rect }
  const target = ratio === 0 ? width / height : ratio
  // In fraction space the picture is a unit square, so a pixel aspect of
  // `target` means h = w · (width / height) / target.
  const factor = width / height / target
  let w = rect.w
  let h = w * factor
  if (h > 1) { h = 1; w = h / factor }
  if (w > 1) { w = 1; h = w * factor }
  w = Math.max(MIN_CROP, Math.min(1, w))
  h = Math.max(MIN_CROP, Math.min(1, h))
  const cx = rect.x + rect.w / 2
  const cy = rect.y + rect.h / 2
  return {
    w, h,
    x: Math.min(Math.max(0, cx - w / 2), 1 - w),
    y: Math.min(Math.max(0, cy - h / 2), 1 - h),
  }
}

/** Keep a dragged rectangle inside the picture, at its current size. */
export function clampRect(rect: CropRect): CropRect {
  const w = Math.min(1, Math.max(MIN_CROP, rect.w))
  const h = Math.min(1, Math.max(MIN_CROP, rect.h))
  return {
    w, h,
    x: Math.min(Math.max(0, rect.x), 1 - w),
    y: Math.min(Math.max(0, rect.y), 1 - h),
  }
}

/**
 * CSS for showing a *live* crop on a media element that cannot be repainted —
 * a playing movie (pictures go through the canvas copy instead). The window box
 * gets the crop's aspect; inside it the media element is oversized and offset
 * so the crop rectangle lands exactly on the window (the classic sprite trick),
 * which keeps the picture undistorted: the element box ends up with the
 * picture's own aspect, so `object-fit: fill` stretches nothing.
 *
 * Movies carry no quarter turn in this app (rotation is a picture control), so
 * `intrinsic` is the element's own `videoWidth`/`videoHeight`. The cut-out
 * polygon is *not* drawn here — a live video cannot be repainted per frame —
 * so a movie's hole shows in its thumbnail and in the render, and the lightbox
 * says so.
 */
export function cropSpriteStyle(crop: ResolvedCrop, intrinsic: Intrinsic) {
  if (!intrinsic.width || !intrinsic.height) return null
  const geometry = cropGeometry(crop, intrinsic)
  const { rect, outAspect } = geometry
  const originX = (0.5 - rect.x) / rect.w * 100
  const originY = (0.5 - rect.y) / rect.h * 100
  return {
    /** Pixel aspect of the crop — the window box must be sized to this. */
    aspect: outAspect,
    media: {
      position: 'absolute' as const,
      left: `${-rect.x / rect.w * 100}%`,
      top: `${-rect.y / rect.h * 100}%`,
      width: `${100 / rect.w}%`,
      height: `${100 / rect.h}%`,
      objectFit: 'fill' as const,
      maxWidth: 'none' as const,
      maxHeight: 'none' as const,
      transform: crop.degrees ? `rotate(${crop.degrees}deg)` : undefined,
      transformOrigin: `${originX}% ${originY}%`,
    },
  }
}

/**
 * The canvas recipe for a cropped copy: how big the output is, where the whole
 * (quarter-turned) picture has to be drawn, and how far to rotate it. One
 * function so thumbnails, the lightbox and the editor's result preview all
 * paint exactly the same pixels.
 */
export function cropPaintPlan(crop: ResolvedCrop, intrinsic: Intrinsic, rotation: unknown, targetWidth: number) {
  const turn = normalizeRotation(rotation)
  const rotated = rotatedSize(intrinsic, turn)
  if (!rotated.width || !rotated.height) return null
  const geometry = cropGeometry(crop, rotated)
  const outW = Math.max(8, Math.round(targetWidth))
  const outH = Math.max(8, Math.round(outW / geometry.outAspect))
  // Size the whole picture has to be drawn at, so the crop rect fills outW×outH.
  const drawW = outW / geometry.rect.w
  const drawH = outH / geometry.rect.h
  const originX = (0.5 - geometry.rect.x) / geometry.rect.w * outW
  const originY = (0.5 - geometry.rect.y) / geometry.rect.h * outH
  // The source is drawn in its own (unturned) proportions, then turned.
  const localW = intrinsic.width ? drawW * intrinsic.width / rotated.width : drawW
  const localH = intrinsic.height ? drawH * intrinsic.height / rotated.height : drawH
  return {
    outW, outH, drawW, drawH, originX, originY, localW, localH,
    turn, degrees: crop.degrees,
    // The lasso lives in the *cropped* view, so its fractions map straight onto
    // the output canvas.
    lasso: crop.lasso,
    feather: crop.feather,
  }
}

/**
 * The cut-out polygon as a CSS `polygon()` in *stage box* fractions. The lasso
 * is stored relative to the crop rectangle (that is the space the renderer cuts
 * in), so it has to be lifted into the straightened view to be drawn over it.
 */
export function lassoBoxPolygon(lasso: LassoPoint[], rect: CropRect): string {
  const points = lasso.map(([x, y]) =>
    `${((rect.x + x * rect.w) * 100).toFixed(3)}% ${((rect.y + y * rect.h) * 100).toFixed(3)}%`)
  return `polygon(${points.join(',')})`
}

/** "Cropped 62 × 48 % · −3° · cut out" — for badges and tooltips. */
export function cropSummary(item?: Cropish | null): string {
  const crop = normalizeCrop(item)
  if (!crop) return ''
  const parts: string[] = []
  if (crop.rect.w < 0.998 || crop.rect.h < 0.998) {
    parts.push(`Crop ${Math.round(crop.rect.w * 100)} × ${Math.round(crop.rect.h * 100)} %`)
  }
  if (crop.degrees) parts.push(`${crop.degrees > 0 ? '+' : '−'}${Math.abs(Math.round(crop.degrees * 10) / 10)}°`)
  if (crop.lasso) parts.push(`Cut out · ${crop.lasso.length} points`)
  return parts.join(' · ')
}

/** Short label for a chip or button. */
export function cropLabel(item?: Cropish | null): string {
  const crop = normalizeCrop(item)
  if (!crop) return 'Original'
  if (crop.lasso) return 'Cut out'
  if (crop.degrees && (crop.rect.w < 0.998 || crop.rect.h < 0.998)) return 'Crop + straighten'
  if (crop.degrees) return 'Straightened'
  return 'Cropped'
}
