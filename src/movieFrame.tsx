// How a movie is framed inside the slideshow frame. The whole picture is always
// shown (landscape or portrait); the empty area around it is either a blurred
// copy of the movie or a solid colour. Mirrors movie_frame_filter() in
// backend/app/renderer.py.
import { useEffect, useRef, type CSSProperties, type RefObject } from 'react'
import type { MediaItem } from './mediaItem'

export const MOVIE_BACKGROUND_COLOURS = ['#000000', '#ffffff', '#30382a', '#7a7f72', '#1f3b57', '#6b2a2a', '#e8dcc0'] as const

export function movieBackgroundOf(item: Partial<MediaItem>): { mode: 'blur' | 'colour'; colour: string } {
  const colour = /^#[0-9a-fA-F]{6}$/.test(item.movieBackgroundColour || '') ? (item.movieBackgroundColour as string) : '#000000'
  return { mode: item.movieBackground === 'colour' ? 'colour' : 'blur', colour }
}

/** Inline style for a frame that shows the movie whole: the colour mode paints the
 *  surround; the blur mode keeps the surround black under the blurred copy. */
export function movieFrameStyle(item: Partial<MediaItem>): CSSProperties {
  const { mode, colour } = movieBackgroundOf(item)
  return mode === 'colour' ? { background: colour } : { background: '#000' }
}

/**
 * A blurred, dimmed copy of the movie behind the picture. It is a second muted
 * <video> of the same file, kept in step with the main one found inside
 * `hostRef` (play/pause and position), so the backdrop moves with the movie.
 */
export function MovieBlurBackdrop({ src, hostRef }: { src: string; hostRef: RefObject<HTMLElement | null> }) {
  const bg = useRef<HTMLVideoElement | null>(null)
  useEffect(() => {
    let timer = 0
    const sync = () => {
      const video = bg.current
      const main = hostRef.current?.querySelector('video') as HTMLVideoElement | null | undefined
      if (video && main) {
        if (Number.isFinite(main.currentTime) && Math.abs(main.currentTime - video.currentTime) > 0.3) {
          try { video.currentTime = main.currentTime } catch { /* not seekable yet */ }
        }
        if (main.paused && !video.paused) video.pause()
        if (!main.paused && video.paused) void video.play().catch(() => undefined)
      }
      timer = window.setTimeout(sync, 250)
    }
    sync()
    return () => window.clearTimeout(timer)
  }, [src, hostRef])
  return <video ref={bg} className="movie-blur-bg" src={src} muted playsInline preload="auto" aria-hidden="true" tabIndex={-1} />
}
