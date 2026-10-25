// Driver for test_media_thumbs.py: replays request/release scripts of media
// browser cards against src/mediaThumbs.ts (the shared thumbnail queue and the
// viewport watcher) and prints the queue state after every step, so Python can
// compare it with its own independent model.
//
//   node --experimental-strip-types media_thumbs.mjs < cases.json

import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const thumbs = await import(path.join(here, '..', '..', 'src', 'mediaThumbs.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = [{
  id: 'constants', slots: thumbs.THUMB_SLOTS, margin: thumbs.THUMB_MARGIN, stallMs: thumbs.THUMB_STALL_MS,
}]

for (const testCase of input.cases) {
  if (testCase.kind === 'script') {
    // Each card id is a picture waiting for one of the shared slots. `ready`
    // records the order in which cards were told to start downloading — that
    // order is what the user sees as the grid filling in.
    const releases = new Map()
    const ready = []
    const steps = []
    for (const step of testCase.steps) {
      for (const id of step.reserve || []) {
        releases.set(id, thumbs.reserveThumbnailSlot(() => ready.push(id)))
      }
      for (const id of step.release || []) {
        releases.get(id)?.()
        // A second call must be a no-op: the same release is also wired to the
        // card's unmount, so double-freeing has to be harmless.
        if (step.again) releases.get(id)?.()
      }
      steps.push({
        inFlight: thumbs.thumbnailsInFlight(),
        queued: thumbs.thumbnailQueueLength(),
        ready: [...ready],
      })
    }
    for (const release of releases.values()) release()
    out.push({
      id: testCase.id,
      steps,
      leftoverInFlight: thumbs.thumbnailsInFlight(),
      leftoverQueued: thumbs.thumbnailQueueLength(),
    })
  } else if (testCase.kind === 'classification') {
    out.push({ id: testCase.id, thumbnail: testCase.files.map(file => thumbs.isThumbnailFile(file)) })
  } else if (testCase.kind === 'viewport') {
    // No IntersectionObserver in Node, so the watcher must fall back to
    // "start now" exactly once and still hand back a working unsubscribe.
    const calls = []
    const stop = thumbs.watchViewportOnce({}, () => calls.push('near'))
    stop()
    stop()
    out.push({ id: testCase.id, calls: calls.length, stopIsFunction: typeof stop === 'function' })
  }
}

console.log(JSON.stringify(out))
