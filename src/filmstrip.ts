// Frames for the movie editor's trim strip: the browser grabs them first,
// the backend FFmpeg filmstrip is the fallback.
//
// Client side: seek one hidden <video> to the centre of N equal slices and
// draw each frame into one wide canvas — a sprite, exactly like the server
// builds it. Same-origin stream, so the canvas stays untainted, and the
// HTTP range-requested file only downloads the chunks around the seek
// points. Camera AVIs (Motion JPEG) and other formats the browser cannot
// decode make every step fail — that is what the server sprite is for.
//
// movieRootAndPath mirrors mediaItemPath/mediaRootFromPath in App.tsx (kept
// local so this module stays import-cycle-free).

const CELL_WIDTH = 160

export const FILMSTRIP_CELLS = 10

export type MovieRoot = 'photos' | 'videos' | 'uploads'

export function movieRootAndPath(item: { path: string; name: string }): { root: MovieRoot; relative: string } {
  const full = String(item.path || '').replace(/\\/g, '/')
  const name = String(item.name || '').replace(/\\/g, '/')
  // Newer snapshots keep the whole file path in `path`; older ones stored the
  // parent folder there with the filename in `name`.
  const lastSegment = full.split('/').filter(Boolean).pop() || ''
  const pathIsFile = lastSegment.includes('.')
  const joined = pathIsFile ? full : `${full.replace(/\/+$/, '')}/${name}`
  const normalized = joined.replace(/\/{2,}/g, '/')
  const root: MovieRoot = normalized.startsWith('/uploads') ? 'uploads'
    : normalized.startsWith('/videos') ? 'videos' : 'photos'
  const relative = normalized.replace(/^\/(photos|videos|uploads)\//, '').replace(/^\/+/, '')
  return { root, relative }
}

export function movieFilmstripUrl(item: { path: string; name: string }): string {
  const { root, relative } = movieRootAndPath(item)
  return `/api/media/filmstrip?root=${root}&path=${encodeURIComponent(relative)}&count=${FILMSTRIP_CELLS}&width=${CELL_WIDTH}`
}

/** Ask the backend (FFmpeg) for the movie's length when the browser cannot
 * decode the file and therefore reports no duration of its own. */
export async function serverMovieDuration(item: { path: string; name: string }): Promise<number> {
  const { root, relative } = movieRootAndPath(item)
  try {
    const response = await fetch(`/api/media/probe?root=${root}&path=${encodeURIComponent(relative)}`)
    if (!response.ok) return 0
    const data = await response.json().catch(() => null)
    return Number.isFinite(data?.duration) ? Number(data.duration) : 0
  } catch {
    return 0
  }
}

// Resolve when the event fires; reject on timeout or a media error so a wedged
// decoder falls back to the server sprite instead of hanging the editor open.
function waitOnce(target: EventTarget, event: string, timeoutMs: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => { cleanup(); reject(new Error(`${event} timed out`)) }, timeoutMs)
    const onEvent = () => { cleanup(); resolve() }
    const onError = () => { cleanup(); reject(new Error('media error')) }
    const cleanup = () => {
      window.clearTimeout(timer)
      target.removeEventListener(event, onEvent)
      if (target instanceof HTMLMediaElement) target.removeEventListener('error', onError)
    }
    target.addEventListener(event, onEvent, { once: true })
    if (target instanceof HTMLMediaElement) target.addEventListener('error', onError, { once: true })
  })
}

/** One wide JPEG data URL with `count` frames of the movie, or null. */
export async function captureFilmstrip(src: string, count: number, totalSeconds: number): Promise<string | null> {
  if (!src || !(totalSeconds > 0)) return null
  const video = document.createElement('video')
  video.src = src
  video.muted = true
  video.preload = 'auto'
  try {
    await waitOnce(video, 'loadeddata', 8000)
    const cellWidth = CELL_WIDTH
    const cellHeight = Math.max(40, Math.round(cellWidth * (video.videoHeight || 9) / (video.videoWidth || 16)))
    const canvas = document.createElement('canvas')
    canvas.width = cellWidth * count
    canvas.height = cellHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    for (let index = 0; index < count; index++) {
      video.currentTime = (index + 0.5) / count * totalSeconds
      await waitOnce(video, 'seeked', 6000)
      ctx.drawImage(video, index * cellWidth, 0, cellWidth, cellHeight)
    }
    return canvas.toDataURL('image/jpeg', 0.72)
  } catch {
    return null
  } finally {
    video.removeAttribute('src')
    video.load()
  }
}
