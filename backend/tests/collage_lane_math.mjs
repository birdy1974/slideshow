// Driver for test_collage_photo_window.py: runs the timing lane's pointer maths
// outside React, so the mapping between a cursor and a second is a measured fact
// instead of a habit. The lane is the only place a person can set when a photo
// appears and disappears, and every bug in here is a drag that lands somewhere
// other than under the finger.
//
//   node --experimental-strip-types collage_lane_math.mjs < cases.json
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const m = await import(path.join(here, '..', '..', 'src', 'CollageLaneMath.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases) {
  const rect = c.rect ?? null
  const span = c.span
  const has = c.at !== undefined && c.until !== undefined
  out.push({
    secondsAt: m.secondsAt(rect, span, c.x ?? 0),
    delta: m.deltaSeconds(rect, span, c.from ?? 0, c.to ?? 0),
    snap: m.snapSeconds(c.snap ?? 0),
    move: has ? m.moveWindow(rect, span, c.from ?? 0, c.to ?? 0, c.at, c.until) : null,
    pinStart: has ? m.pinStart(rect, span, c.x ?? 0, c.until) : null,
    pinStop: has ? m.pinStop(rect, span, c.x ?? 0, c.at) : null,
    edgeStart: has ? m.dragEdge(rect, span, c.from ?? 0, c.to ?? 0, c.at, c.until, 'start') : null,
    edgeEnd: has ? m.dragEdge(rect, span, c.from ?? 0, c.to ?? 0, c.at, c.until, 'end') : null,
  })
}
process.stdout.write(JSON.stringify(out))
