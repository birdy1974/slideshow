// Picture looks (filters / effects) — the browser half of the shared catalogue.
//
// Presets come from registry/picture-filters.json, the very same file
// backend/app/picture_filters.py reads, so a preset can never drift between
// what the editor shows and what FFmpeg renders. Each parameter is turned into
// a CSS filter function here and into a filter chain there, from the same
// numbers:
//
//   brightness / contrast / saturation → brightness() contrast() saturate()
//   grayscale / sepia / hue-rotate     → grayscale() sepia() hue-rotate()
//   warmth                             → url(#look-warmth-N), an SVG
//                                         feColorMatrix holding the same RGB
//                                         gain matrix the renderer multiplies
//                                         into its colorchannelmixer
//   invert                             → invert(1)
//   softness                           → blur(Npx)
//   sharpen                            → a small contrast bump only; the real
//                                         unsharp mask happens in the render
//   vignette                           → a radial-gradient overlay element
//                                         (vignetteOverlayStyle), not a filter
//   pixelate                           → the picture is shown from a tiny
//                                         nearest-neighbour proxy instead
//
// Nothing is ever baked into the source files: /photos and /videos are
// read-only mounts, so a look is stored as numbers on the media item and
// applied on the fly, exactly like `rotation`.
import registry from '../registry/picture-filters.json'

export type LookParam =
  | 'brightness' | 'contrast' | 'saturation' | 'grayscale' | 'sepia' | 'hueRotate'
  | 'warmth' | 'vignette' | 'softness' | 'sharpen' | 'pixelate' | 'invert'
export type LookParams = Record<LookParam, number>
// The manual sliders. Brightness/contrast/saturation scale the preset value,
// warmth/vignette/softness shift it; `amount` fades the whole preset.
export type LookAdjustKey = 'amount' | 'brightness' | 'contrast' | 'saturation' | 'warmth' | 'vignette' | 'softness'
export type LookAdjust = Partial<Record<LookAdjustKey, number>>
export type LookPreset = { id: string; label: string; group: string; hint?: string; params: Partial<LookParams> }
export type LookRange = { min: number; max: number; step: number; identity: number }
// Anything that carries a look — a MediaItem, or the loose object a bulk
// control keeps before it is applied.
export type Lookish = {
  filter?: string; filterAmount?: number; filterAdjust?: LookAdjust | Record<string, number>
  /** Only read for the quarter turn a canvas copy has to bake in. */
  rotation?: number | null
}

const data = registry as { identity: LookParams; ranges: Record<string, LookRange>; presets: LookPreset[] }

export const LOOK_IDENTITY: LookParams = data.identity
export const LOOK_RANGES: Record<string, LookRange> = data.ranges
export const LOOK_PRESETS: LookPreset[] = data.presets
export const LOOK_GROUPS: string[] = LOOK_PRESETS.reduce<string[]>(
  (groups, preset) => (groups.includes(preset.group) ? groups : [...groups, preset.group]), [])

// Relative pixelate block size and the warmth matrix gains — both must stay in
// sync with backend/app/picture_filters.py (PIXELATE_DIVISOR, WARMTH_*).
export const PIXELATE_DIVISOR = 120
export const WARMTH_RED = 0.045
export const WARMTH_GREEN = 0.012
// Vignette is an overlay in the browser and a `vignette=angle` filter in the
// render; this is the strongest overlay opacity the slider reaches.
export const VIGNETTE_OPACITY = 0.6

export const ADJUST_CONTROLS: { key: LookAdjustKey; label: string }[] = [
  { key: 'amount', label: 'Intensity' },
  { key: 'brightness', label: 'Brightness' },
  { key: 'contrast', label: 'Contrast' },
  { key: 'saturation', label: 'Saturation' },
  { key: 'warmth', label: 'Warmth' },
  { key: 'vignette', label: 'Vignette' },
  { key: 'softness', label: 'Soft focus' },
]

/**
 * Coerce a stored value to a usable number, mirroring `_number()` in
 * backend/app/picture_filters.py. Project JSON can be hand-edited or come from
 * an older version, so anything that is not a finite number (or a numeric
 * string) counts as "not set" and falls back to the identity value.
 */
const finite = (value: unknown): number | null => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'boolean') return value ? 1 : 0
  if (typeof value === 'string' && value.trim() !== '') {
    const number = Number(value)
    return Number.isFinite(number) ? number : null
  }
  return null
}

/** Same idea for preset ids: `str(item.get("filter") or "").strip()` in Python. */
const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '')

/** A stored object we can read slider values from (arrays are not dicts). */
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}

export const clampParam = (name: LookParam | LookAdjustKey, value: number): number => {
  const range = LOOK_RANGES[name]
  if (!range) return value
  return Math.min(range.max, Math.max(range.min, value))
}

export const lookPreset = (id?: unknown): LookPreset | undefined =>
  LOOK_PRESETS.find(preset => preset.id === text(id))

/** What a chip/badge should call this item's look. */
export function lookLabel(item?: Lookish | null): string {
  const preset = lookPreset(item?.filter)
  return preset && preset.id !== 'none' ? preset.label : 'Original'
}

/** "Mono · 80 %" — label plus anything that makes this look non-default. */
export function lookSummary(item?: Lookish | null): string {
  if (!hasLook(item)) return ''
  const amount = finite(item?.filterAmount)
  const parts = [lookLabel(item)]
  if (amount !== null && Math.abs(amount - 1) > 0.01) parts.push(`${Math.round(amount * 100)} %`)
  if (hasAdjust(item)) parts.push('adjusted')
  return parts.join(' · ')
}

export function hasAdjust(item?: Lookish | null): boolean {
  const adjust = record(item?.filterAdjust)
  return ADJUST_CONTROLS.some(({ key }) => {
    const value = finite(adjust[key])
    if (value === null) return false
    const identity = key === 'amount' ? 1 : LOOK_RANGES[key]?.identity ?? (key === 'brightness' || key === 'contrast' || key === 'saturation' ? 1 : 0)
    return Math.abs(value - identity) > 0.001
  })
}

/** Is this item rendered differently from the untouched original? */
export function hasLook(item?: Lookish | null): boolean {
  const preset = lookPreset(item?.filter)
  const presetActive = !!preset && preset.id !== 'none' && Object.keys(preset.params).length > 0
  const amount = finite(item?.filterAmount) ?? 1
  return (presetActive && amount > 0.001) || hasAdjust(item)
}

/**
 * Flatten an item's look into concrete parameter values.
 *
 * Mirrors backend/app/picture_filters.py::resolve_look line for line: preset
 * values fade toward the identity by `amount`, then the manual sliders scale
 * (brightness/contrast/saturation) or shift (warmth/vignette/softness).
 */
export function resolveLook(item?: Lookish | null): LookParams {
  const preset = record(lookPreset(item?.filter)?.params)
  const amount = Math.min(1, Math.max(0, finite(item?.filterAmount) ?? 1))
  const params = { ...LOOK_IDENTITY }
  for (const name of Object.keys(params) as LookParam[]) {
    const target = finite(preset[name])
    if (target !== null) params[name] = LOOK_IDENTITY[name] + (clampParam(name, target) - LOOK_IDENTITY[name]) * amount
  }
  const adjust = record(item?.filterAdjust)
  for (const name of ['brightness', 'contrast', 'saturation'] as const) {
    const factor = finite(adjust[name])
    if (factor !== null) params[name] = clampParam(name, params[name] * factor)
  }
  for (const name of ['warmth', 'vignette', 'softness'] as const) {
    const delta = finite(adjust[name])
    if (delta !== null) params[name] = clampParam(name, params[name] + delta)
  }
  return params
}

/** Warmth is quantised to the registry step so a fixed set of SVG filters covers it. */
export const WARMTH_STEP = LOOK_RANGES.warmth?.step || 0.25
export const WARMTH_STEPS: number[] = (() => {
  const range = LOOK_RANGES.warmth ?? { min: -3, max: 3 }
  const steps: number[] = []
  for (let index = Math.round(range.min / WARMTH_STEP); index <= Math.round(range.max / WARMTH_STEP); index++) steps.push(index)
  return steps
})()

export const warmthFilterId = (index: number) => `look-warmth-${index}`

/** Index of the SVG feColorMatrix that stands in for CSS's missing white balance. */
export function warmthIndex(warmth: number): number | null {
  if (Math.abs(warmth) < WARMTH_STEP / 2) return null
  const range = LOOK_RANGES.warmth ?? { min: -3, max: 3 }
  const max = Math.round(range.max / WARMTH_STEP)
  return Math.max(-max, Math.min(max, Math.round(warmth / WARMTH_STEP)))
}

/** The 20 feColorMatrix values for one warmth step (same gains as the renderer). */
export function warmthMatrixValues(index: number): string {
  const warmth = index * WARMTH_STEP
  const r = 1 + WARMTH_RED * warmth
  const g = 1 + WARMTH_GREEN * warmth
  const b = 1 - WARMTH_RED * warmth
  return [r, 0, 0, 0, 0, 0, g, 0, 0, 0, 0, 0, b, 0, 0, 0, 0, 0, 1, 0]
    .map(value => (Math.round(value * 10000) / 10000).toString()).join(' ')
}

const round3 = (value: number) => Math.round(value * 1000) / 1000

/**
 * The CSS filter string for a resolved look, in the same order the renderer
 * chains its FFmpeg filters. '' means "leave the picture alone".
 *
 * The `url(#look-warmth-N)` reference needs <PictureLookDefs/> to be mounted:
 * a filter reference that resolves to nothing makes browsers hide the element
 * entirely, which is why the defs live in the app root instead of in the
 * editor that owns the sliders.
 */
export function cssFilter(params: LookParams): string {
  const out: string[] = []
  if (Math.abs(params.brightness - 1) > 0.001) out.push(`brightness(${round3(params.brightness)})`)
  // Sharpen has no CSS twin: hint at it with a little contrast, while the
  // render applies a real unsharp mask.
  const contrast = params.contrast * (1 + 0.05 * params.sharpen)
  if (Math.abs(contrast - 1) > 0.001) out.push(`contrast(${round3(contrast)})`)
  if (Math.abs(params.saturation - 1) > 0.001) out.push(`saturate(${round3(params.saturation)})`)
  if (params.grayscale > 0.001) out.push(`grayscale(${round3(params.grayscale)})`)
  if (params.sepia > 0.001) out.push(`sepia(${round3(params.sepia)})`)
  if (Math.abs(params.hueRotate) > 0.5) out.push(`hue-rotate(${Math.round(params.hueRotate)}deg)`)
  const warmth = warmthIndex(params.warmth)
  if (warmth !== null) out.push(`url(#${warmthFilterId(warmth)})`)
  if (params.invert > 0.5) out.push('invert(1)')
  if (params.softness > 0.01) out.push(`blur(${round3(params.softness)}px)`)
  return out.join(' ')
}

/** Inline style for any <img>/<video> that shows this item. */
export function pictureFilterStyle(item?: Lookish | null): { filter?: string } {
  if (!hasLook(item)) return {}
  const filter = cssFilter(resolveLook(item))
  return filter ? { filter } : {}
}

/** Style for the vignette overlay element that sits on top of a stage. */
export function vignetteOverlayStyle(item?: Lookish | null): { opacity: number } | undefined {
  if (!hasLook(item)) return undefined
  const vignette = resolveLook(item).vignette
  return vignette > 0.01 ? { opacity: Math.min(1, vignette * VIGNETTE_OPACITY) } : undefined
}

/** Pixelated looks show a tiny proxy upscaled with nearest-neighbour instead. */
export function pixelateProxyWidth(item?: Lookish | null): number | null {
  if (!hasLook(item)) return null
  const pixelate = resolveLook(item).pixelate
  return pixelate > 0.001 ? Math.max(8, Math.round(PIXELATE_DIVISOR / pixelate)) : null
}
