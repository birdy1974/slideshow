// Hooks that turn an item's picture look into something an <img>/<video> can
// actually wear: the CSS filter string, the vignette overlay opacity, and —
// for the pixelate look — a tiny proxy that nearest-neighbour upscaling turns
// back into blocks (CSS has no pixelate function).
//
// Only the big stages use this (lightbox, editor, slideshow preview). The small
// thumbnails across the timeline just take `pictureFilterStyle(item)`: at 43 px
// tall a vignette or a 16 px block is invisible, and drawing a canvas per clip
// would cost more than it shows.
import { useEffect, useState } from 'react'
import {
  cssFilter, hasLook, pixelateProxyWidth, resolveLook, vignetteOverlayStyle,
  type Lookish,
} from './pictureFilters'
import { normalizeRotation } from './pictureCrop'

/** Anything `drawImage` accepts and that reports its own pixel size. */
type Paintable = HTMLImageElement | HTMLVideoElement

function intrinsicSize(source: Paintable): { width: number; height: number } {
  return 'videoWidth' in source
    ? { width: source.videoWidth, height: source.videoHeight }
    : { width: source.naturalWidth, height: source.naturalHeight }
}

/**
 * Draw `source` into a canvas `width` px wide and return it as a JPEG data URL.
 * A quarter turn is baked in here rather than left to CSS: the copies feed
 * preset chips (whose 4:3 box cannot be turned) and the Pixelate proxy, and a
 * turned copy means every consumer shows the picture the way the render does.
 */
function scaledDataUrl(source: Paintable, width: number, turn = 0): string {
  const { width: natural, height } = intrinsicSize(source)
  if (!natural || !height) return ''
  const turned = turn === 90 || turn === 270
  const canvas = document.createElement('canvas')
  const long = Math.max(1, Math.round(width))
  const short = Math.max(1, Math.round(width * height / natural))
  canvas.width = turned ? short : long
  canvas.height = turned ? long : short
  const context = canvas.getContext('2d')
  if (!context) return ''
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'
  context.translate(canvas.width / 2, canvas.height / 2)
  if (turn) context.rotate(turn * Math.PI / 180)
  context.drawImage(source, -long / 2, -short / 2, long, short)
  return canvas.toDataURL('image/jpeg', 0.85)
}

/**
 * Downscaled stand-ins for one clip: `chip` feeds the preset chips in the
 * editor (20 chips on a 24 MP JPEG is wasted decode work) and `pixel` is the
 * 120 px copy that shows the Pixelate look. Empty strings mean "not ready /
 * not available" — a canvas tainted by a cross-origin picture simply falls
 * back to the real file, which stays correct, only heavier.
 *
 * Movies are sampled instead of decoded: seek once to a tenth of a second in
 * and grab that frame, so a movie's chips show its own picture rather than an
 * empty box. `preload="metadata"` plus the seek keeps it to a range request —
 * the file is never downloaded twice.
 */
export function useLookProxies(src: string, chipWidth = 320, pixelWidth: number | null = null, isVideo = false, turn = 0) {
  const [chip, setChip] = useState('')
  const [pixel, setPixel] = useState('')
  useEffect(() => {
    let cancelled = false
    setChip(''); setPixel('')
    if (!src || (chipWidth <= 0 && !pixelWidth)) return
    const grab = (source: Paintable) => {
      if (cancelled) return
      try {
        if (chipWidth > 0) setChip(scaledDataUrl(source, chipWidth, turn))
        if (pixelWidth) setPixel(scaledDataUrl(source, pixelWidth, turn))
      } catch { /* tainted canvas — the caller falls back to `src` */ }
    }
    // Anonymous so a same-origin `/api/media/file` clip keeps working if the
    // backend ever serves it from another origin; it never hurts locally.
    if (isVideo) {
      const video = document.createElement('video')
      video.muted = true
      video.playsInline = true
      video.preload = 'metadata'
      video.crossOrigin = 'anonymous'
      video.onloadeddata = () => {
        if (cancelled) return
        const seconds = Number.isFinite(video.duration) && video.duration > 0.2 ? video.duration * 0.1 : 0.1
        video.currentTime = Math.min(seconds, Math.max(0, (video.duration || 1) - 0.05))
      }
      video.onseeked = () => { if (video.videoWidth) grab(video) }
      video.src = src
      return () => { cancelled = true; video.onseeked = null; video.onloadeddata = null; video.removeAttribute('src'); video.load() }
    }
    const image = new Image()
    image.onload = () => { if (image.naturalWidth) grab(image) }
    image.crossOrigin = 'anonymous'
    image.src = src
    return () => { cancelled = true }
  }, [src, chipWidth, pixelWidth, isVideo, turn])
  return { chip, pixel }
}

/**
 * What a big stage should render for this item right now.
 *
 * `suspended` (the editor's "hold Space to compare") drops the look entirely
 * and reports it, so the caller can badge the original.
 */
export function usePictureLook(src: string, item?: Lookish | null, suspended = false, allowPixelProxy = true, alreadyTurned = false) {
  // Movies never swap their src: a JPEG proxy would replace the recording with
  // a single frame, so a pixelated movie keeps its colour look in the preview
  // and only the render shows the blocks.
  const pixelWidth = !suspended && allowPixelProxy ? pixelateProxyWidth(item) : null
  // `alreadyTurned` is set when src is itself a canvas copy that already carries
  // the quarter turn (a cropped clip), so the turn is not applied twice.
  const turn = alreadyTurned ? 0 : normalizeRotation(item?.rotation)
  const { pixel } = useLookProxies(src, 0, pixelWidth, false, turn)
  const [style, vignette] = (() => {
    if (suspended || !hasLook(item)) return [{ filter: undefined } as { filter?: string; imageRendering?: 'pixelated' }, undefined]
    const params = resolveLook(item)
    const filter = cssFilter(params) || undefined
    const pixelated = params.pixelate > 0.001
    return [
      { filter, imageRendering: pixelated ? 'pixelated' as const : undefined },
      vignetteOverlayStyle(item),
    ]
  })()
  return {
    // Prefer the blocky proxy while it loads in; fall back to the real file.
    src: pixelWidth && pixel ? pixel : src,
    style,
    vignette,
    compare: suspended,
    // True when the returned src already carries the item's quarter turn, so the
    // caller must not rotate it again.
    rotationBaked: !!(pixelWidth && pixel && turn),
  }
}
