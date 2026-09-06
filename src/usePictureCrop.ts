// Hooks that turn an item's stored crop into something an <img> can wear.
//
// A crop cannot be expressed as a CSS filter, so the browser pays for it the
// same way it pays for the Pixelate look: one small canvas copy per clip, drawn
// with the exact geometry `cropPaintPlan()` computes (quarter turn → straighten
// → rectangle → cut-out fill) and cached. The copy becomes the element's `src`,
// which is why every surface — storyline, compact grid, detailed list, filmstrip,
// lightbox, Preview — shows the crop without a single extra CSS rule.
//
// Two things never go through a proxy:
//   · a playing movie (a JPEG would replace the recording) — those wear the
//     CSS sprite from `cropSpriteStyle()` instead, live and full resolution;
//   · the crop editor's own stage, which has to show the *whole* picture so the
//     handles can be dragged.
import { useEffect, useState } from 'react'
import {
  cropPaintPlan, normalizeCrop, normalizeRotation,
  type Cropish, type Intrinsic, type ResolvedCrop,
} from './pictureCrop'

export type CropTier = 'thumb' | 'result' | 'stage'

// Thumb copies feed 54 px timeline cells and 88 px grid cards; `result` feeds
// the editor's "in the slideshow frame" preview; `stage` feeds the lightbox and
// the Preview stage, which are up to ~1100 px wide on screen.
const TIER_WIDTH: Record<CropTier, number> = { thumb: 360, result: 720, stage: 1600 }
// Rough memory ceilings per tier, so a storyline full of cropped 24 MP photos
// cannot grow the cache without bound. Oldest entries go first.
const TIER_BUDGET: Record<CropTier, number> = { thumb: 12e6, result: 8e6, stage: 24e6 }

type Entry = { url: string; bytes: number }
const caches: Record<CropTier, Map<string, Entry>> = { thumb: new Map(), result: new Map(), stage: new Map() }
const inflight = new Set<string>()
// Per-key subscriptions: with a storyline full of cropped clips, waking every
// listener on every finished copy would re-render the whole timeline per item.
const listeners = new Map<string, Set<() => void>>()

const notify = (key: string) => listeners.get(key)?.forEach(listener => listener())

const subscribe = (key: string, listener: () => void) => {
  const set = listeners.get(key) ?? new Set()
  set.add(listener)
  listeners.set(key, set)
  return () => {
    set.delete(listener)
    if (!set.size) listeners.delete(key)
  }
}

function store(tier: CropTier, key: string, url: string) {
  const cache = caches[tier]
  // A data URL is ~4/3 of its bytes; good enough for a budget.
  cache.set(key, { url, bytes: url.length })
  let total = 0
  for (const entry of cache.values()) total += entry.bytes
  for (const old of cache.keys()) {
    if (total <= TIER_BUDGET[tier]) break
    const dropped = cache.get(old)
    cache.delete(old)
    if (dropped) total -= dropped.bytes
  }
}

/** Draw the blurred copy of the canvas back through the lasso polygon. */
function paintCutOut(context: CanvasRenderingContext2D, plan: NonNullable<ReturnType<typeof cropPaintPlan>>) {
  const points = plan.lasso
  if (!points || points.length < 3) return
  // Blur without ctx.filter (Safari): shrink hard, grow back with smoothing.
  // Feather picks how hard the shrink is, so the slider does something on
  // screen too — the render feathers the mask edge as well.
  const shrink = Math.max(4, Math.round(6 + 20 * plan.feather))
  const small = document.createElement('canvas')
  small.width = Math.max(2, Math.round(plan.outW / shrink))
  small.height = Math.max(2, Math.round(plan.outH / shrink))
  const smallContext = small.getContext('2d')
  if (!smallContext) return
  smallContext.drawImage(context.canvas, 0, 0, small.width, small.height)
  context.save()
  context.beginPath()
  points.forEach(([x, y], index) => {
    const px = x * plan.outW
    const py = y * plan.outH
    if (index === 0) context.moveTo(px, py)
    else context.lineTo(px, py)
  })
  context.closePath()
  context.clip()
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'
  context.drawImage(small, 0, 0, plan.outW, plan.outH)
  context.restore()
}

/** Paint one cropped copy. Returns '' when the source cannot be read. */
function paint(
  source: CanvasImageSource,
  intrinsic: Intrinsic,
  crop: ResolvedCrop,
  rotation: number,
  tier: CropTier,
): string {
  const plan = cropPaintPlan(crop, intrinsic, rotation, TIER_WIDTH[tier])
  if (!plan) return ''
  const canvas = document.createElement('canvas')
  canvas.width = plan.outW
  canvas.height = plan.outH
  const context = canvas.getContext('2d')
  if (!context) return ''
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'
  // Straightening rotates about the picture's own centre, exactly like the
  // CSS sprite does, so the browser and the render turn the same way.
  context.translate(plan.originX, plan.originY)
  if (plan.degrees) context.rotate(plan.degrees * Math.PI / 180)
  if (plan.turn) context.rotate(plan.turn * Math.PI / 180)
  context.drawImage(source, -plan.localW / 2, -plan.localH / 2, plan.localW, plan.localH)
  context.setTransform(1, 0, 0, 1, 0, 0)
  paintCutOut(context, plan)
  try {
    return canvas.toDataURL('image/jpeg', tier === 'thumb' ? 0.82 : 0.9)
  } catch {
    return '' // tainted canvas — the caller keeps the real file
  }
}

async function build(workKey: string, key: string, tier: CropTier, src: string, crop: ResolvedCrop, rotation: number, isVideo: boolean) {
  if (inflight.has(workKey)) return
  inflight.add(workKey)
  try {
    const url = isVideo ? await paintFromVideo(src, crop, rotation, tier) : await paintFromImage(src, crop, rotation, tier)
    if (url) store(tier, key, url)
  } catch {
    // Unreadable, undecodable or cross-origin: the element keeps its own src.
  } finally {
    inflight.delete(workKey)
    notify(workKey)
  }
}

function paintFromImage(src: string, crop: ResolvedCrop, rotation: number, tier: CropTier): Promise<string> {
  return new Promise(resolve => {
    const image = new Image()
    image.onload = () => {
      if (!image.naturalWidth) { resolve(''); return }
      resolve(paint(image, { width: image.naturalWidth, height: image.naturalHeight }, crop, rotation, tier))
    }
    image.onerror = () => resolve('')
    image.crossOrigin = 'anonymous'
    image.src = src
  })
}

/** A movie cannot be repainted while it plays, so thumbnails sample one frame. */
function paintFromVideo(src: string, crop: ResolvedCrop, rotation: number, tier: CropTier): Promise<string> {
  return new Promise(resolve => {
    const video = document.createElement('video')
    let settled = false
    const finish = (url: string) => {
      if (settled) return
      settled = true
      video.removeAttribute('src')
      video.load()
      resolve(url)
    }
    video.muted = true
    video.playsInline = true
    video.preload = 'metadata'
    video.crossOrigin = 'anonymous'
    video.onloadeddata = () => {
      const seconds = Number.isFinite(video.duration) && video.duration > 0.2 ? video.duration * 0.1 : 0.1
      video.currentTime = Math.min(seconds, Math.max(0, (video.duration || 1) - 0.05))
    }
    video.onseeked = () => {
      if (!video.videoWidth) { finish(''); return }
      finish(paint(video, { width: video.videoWidth, height: video.videoHeight }, crop, rotation, tier))
    }
    video.onerror = () => finish('')
    // A file that never decodes must not keep the caller waiting forever.
    window.setTimeout(() => finish(''), 15000)
    video.src = src
  })
}

/**
 * The src a surface should use for this item: the cropped copy once it exists,
 * the real file until then (and always the real file when nothing is cropped).
 *
 * `isVideo` samples a frame instead of decoding a picture, which is what the
 * small thumbnails want; a stage that has to keep playing the movie must not
 * use this hook at all — it uses `cropSpriteStyle()` on the live element.
 */
export function useCroppedSource(src: string, item?: Cropish | null, tier: CropTier = 'thumb', isVideo = false) {
  const crop = normalizeCrop(item)
  const rotation = normalizeRotation(item?.rotation)
  const key = crop ? `${src}|r${rotation}|${JSON.stringify(crop)}` : ''
  const [, refresh] = useState(0)
  useEffect(() => {
    if (!key) return
    const workKey = `${tier}|${key}`
    const unsubscribe = subscribe(workKey, () => refresh(value => value + 1))
    // Re-derived inside the effect so `item`'s changing identity (every render
    // creates a fresh object) cannot retrigger the work; `key` covers the crop.
    const resolved = normalizeCrop(item)
    if (resolved && !caches[tier].has(key)) void build(workKey, key, tier, src, resolved, rotation, isVideo)
    return unsubscribe
  }, [key, tier, src, rotation, isVideo])  // eslint-disable-line react-hooks/exhaustive-deps
  if (!key || !crop) return { src, cropped: false, ready: true, rotationApplied: false }
  const entry = caches[tier].get(key)
  // The copy already carries the quarter turn, so callers must not rotate twice.
  return { src: entry ? entry.url : src, cropped: true, ready: !!entry, rotationApplied: !!entry }
}

/** True when this item's crop would change what a surface shows. */
export function isCropped(item?: Cropish | null): boolean {
  return normalizeCrop(item) !== null
}
