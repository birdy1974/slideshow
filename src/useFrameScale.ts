import { useCallback, useEffect, useState } from 'react'

// The rendered slideshow is always 16:9 (any resolution, e.g. 1920×1080), and the
// backend sizes text in *frame* pixels (drawtext fontsize=48 means 48px of a 1080p
// frame). Preview boxes in the editors are smaller 16:9 frames, so frame-relative
// sizes map linearly onto the box height:  previewPx = framePx × boxHeight / 1080.
export const FRAME_HEIGHT = 1080

/**
 * Scale factor that converts frame pixels into preview pixels for a 16:9 stage.
 * Attach `setRef` to the stage element. A callback ref is used (not a useRef
 * effect) so the scale also works for stages that mount later, e.g. the motion
 * canvas that only appears once text motion is enabled.
 */
export function useFrameScale<T extends HTMLElement = HTMLElement>(frameHeight: number = FRAME_HEIGHT) {
  const [el, setEl] = useState<T | null>(null)
  const setRef = useCallback((node: T | null) => setEl(node), [])
  const [scale, setScale] = useState(0)
  useEffect(() => {
    if (!el) return
    const update = () => setScale(el.clientHeight > 0 ? el.clientHeight / frameHeight : 0)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => observer.disconnect()
  }, [el, frameHeight])
  return { setRef, scale }
}

/**
 * Blur amount for the letterbox backdrop. The renderer blurs a 1/8-size cover copy
 * (gblur sigma ≈ frameWidth/256 before upscaling ≈ 0.054 × frame height at 1080p),
 * so the visually equivalent CSS blur on a smaller preview is the same fraction of
 * the preview height.
 */
export function backdropBlurPx(frameScale: number): number {
  return Math.max(4, 0.054 * FRAME_HEIGHT * frameScale)
}
