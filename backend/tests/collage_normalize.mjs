// Driver for test_collage_normalize.py: runs the editor's own normalizer over
// collage specs and reports what survived it, so a setting that is offered in
// the UI and quietly dropped on load cannot come back.
//
//   node --experimental-strip-types collage_normalize.mjs < cases.json
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'collageCore.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases) {
  const spec = core.normalizeCollage(c)
  if (!spec) { out.push(null); continue }
  const n = spec.photos.length
  out.push({
    exit: spec.exit ?? null,
    exitSpeed: spec.exitSpeed ?? null,
    exitReverse: spec.exitReverse === true,
    photoExits: spec.photos.map(p => p.exit ?? null),
    exitMode: core.exitMode(spec),
    exitModeFirst: core.exitMode(spec, 0),
    exitModeLast: core.exitMode(spec, n - 1),
    schedule: core.exitScheduleMode(spec),
    exitTotal: core.exitTotal(spec),
    offsets: spec.photos.map((_, i) => core.exitOffset(spec, i)),
    flies: spec.photos.map((_, i) => core.exitFlyLength(spec, i)),
    anims: spec.photos.map(p => p.anim ?? null),
  })
}
process.stdout.write(JSON.stringify(out))
