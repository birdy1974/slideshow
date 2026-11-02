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

/** Replay one popup's burst of writes against the module's write-ahead slot.
 *
 *  `script` steps: `render` is a React render (the item may or may not carry what
 *  the popup just wrote), `write` is a `setSpec`, and `lane` is one photo's panel
 *  write — the thing a group edit repeats once per photo inside a single handler.
 *  Each step reports the spec the popup would have seen, so a build that composes
 *  onto the snapshot it was drawn with shows up as a photo that never changed.
 */
function laneChange (slot, lane) {
  if (!lane) return null
  const list = ((slot && slot.spec) || { photos: [] }).photos || []
  return { photos: list.map((photo, i) => (i === lane.index ? { ...photo, [lane.key]: lane.value } : photo)) }
}

function runBurst (m, script) {
  if (!script) return null
  let slot = null
  let source = null
  const out = []
  for (const step of script) {
    if (step.render !== undefined) {
      source = step.render.token
      const opened = m.beginWrite(slot, source, step.render.spec)
      slot = opened.slot
      out.push({ seen: opened.spec })
      continue
    }
    const change = step.write ?? laneChange(slot, step.lane)
    if (!change) continue
    const written = m.recordWrite(slot ?? { source, spec: {} }, source, { ...(slot ? slot.spec : {}), ...change })
    slot = written.slot
    out.push({ sent: written.spec })
  }
  return out
}

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
    selection: m.nextSelection(c.sel ?? [], c.index ?? 0, c.mode ?? 'single', c.count ?? 0),
    group: c.anchor ? m.groupShift(rect, span, c.from ?? 0, c.to ?? 0, c.anchor, c.others ?? []) : null,
    burst: runBurst(m, c.script),
  })
}
process.stdout.write(JSON.stringify(out))
