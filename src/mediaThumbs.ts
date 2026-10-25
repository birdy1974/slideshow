// ---------------------------------------------------------------------------
// Thumbnail loading for the media browser
//
// A folder on the NAS can hold hundreds of pictures, and every card used to ask
// for its full-size original the moment the popup opened. A browser keeps only
// a handful of connections per host, so the requests pile up in front of the
// picker: the first rows stay unusable until the whole folder has arrived, and
// decoding megapixels blocks the main thread on top of that — clicking a file
// name looked like nothing happened.
//
// Two rules keep the browser usable from the first paint:
//   * a card only asks for its picture once it is near the viewport, and
//   * at most THUMB_SLOTS pictures are in flight at any moment.
// Selection never waits for either. Picking files (and the names-only list
// view, which requests no pictures at all) works while the grid is still
// filling in, so a huge folder is usable immediately.
// ---------------------------------------------------------------------------

/** How many of the browser's connections to the backend the thumbnail wall may
 *  occupy. The rest of the queue has to leave room for the API calls the popup
 *  also makes (folder listing, previews) — that is what felt "blocked". */
export const THUMB_SLOTS = 4

/** How far outside the visible area a card starts loading (px of scroll). */
export const THUMB_MARGIN = 240

/** How long a picture that has started downloading may hold its slot before
 *  the queue moves on without it (ms). A stalled request on a tired NAS must
 *  not freeze the whole wall. */
export const THUMB_STALL_MS = 20000

const waiters: Array<() => void> = []
let running = 0

/** Cards holding a slot right now (0…THUMB_SLOTS). */
export function thumbnailsInFlight(): number {
  return running
}

/** Cards still queued behind them. */
export function thumbnailQueueLength(): number {
  return waiters.length
}

/**
 * Claim one of the shared thumbnail slots. `onReady` runs synchronously when a
 * slot is free, otherwise as soon as an earlier card releases one. The returned
 * function hands the slot back; it is safe to call it before `onReady` ever ran
 * (the card leaves the queue) and safe to call twice.
 */
export function reserveThumbnailSlot(onReady: () => void): () => void {
  let state: 'queued' | 'running' | 'released' = 'queued'
  const start = () => {
    if (state !== 'queued') return
    state = 'running'
    running += 1
    onReady()
  }
  if (running < THUMB_SLOTS) start()
  else waiters.push(start)
  return () => {
    if (state === 'queued') {
      state = 'released'
      const index = waiters.indexOf(start)
      if (index >= 0) waiters.splice(index, 1)
      return
    }
    if (state !== 'running') return
    state = 'released'
    running = Math.max(0, running - 1)
    // Let exactly one waiter in, so the queue drains in the order it filled.
    waiters.shift()?.()
  }
}

/** Only real, readable pictures and movies get a thumbnail: folders, music and
 *  anything unplayable stay an icon, and a 0-byte file would only answer the
 *  backend's "empty file" error — so none of them touch the network at all and
 *  never take a slot from the queue. */
export function isThumbnailFile(file: { kind?: unknown, empty?: unknown }): boolean {
  return (file.kind === 'image' || file.kind === 'video') && file.empty !== true
}

// One shared, one-shot IntersectionObserver for every card that is still
// waiting: a long list does not need an observer per row, and a card is woken
// up only once — scrolling back never restarts a download.
const viewport: { io: IntersectionObserver | null, cbs: Map<Element, () => void> } = { io: null, cbs: new Map() }

/** Run `onNear` the first time `el` comes within THUMB_MARGIN of the viewport.
 *  Returns a function that stops watching. Without IntersectionObserver support
 *  (tests, ancient browsers) `onNear` runs straight away. */
export function watchViewportOnce(el: Element, onNear: () => void): () => void {
  if (typeof IntersectionObserver === 'undefined') {
    onNear()
    return () => undefined
  }
  if (!viewport.io) {
    viewport.io = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const cb = viewport.cbs.get(entry.target)
        viewport.cbs.delete(entry.target)
        viewport.io?.unobserve(entry.target)
        cb?.()
      }
    }, { rootMargin: `${THUMB_MARGIN}px` })
  }
  viewport.cbs.set(el, onNear)
  viewport.io.observe(el)
  return () => {
    viewport.cbs.delete(el)
    viewport.io?.unobserve(el)
  }
}
