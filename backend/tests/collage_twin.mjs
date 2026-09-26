// Driver for test_collage_twin.py: evaluates the collage layout/animation
// twin (src/collageCore.ts) on the specs Python serialises and prints every
// placement and per-photo state as JSON for a value-by-value comparison with
// backend/app/collage.py.
//
//   node --experimental-strip-types collage_twin.mjs < cases.json
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'collageCore.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases) {
  const spec = c.spec
  const pls = core.placements(spec, c.aspect)
  const states = c.times.map(t => pls.map((_, i) => core.photoState(spec, i, t, c.leadIn)))
  out.push({
    id: c.id,
    hash: core.hash01(c.hashArgs[0], c.hashArgs[1], c.hashArgs[2]),
    matHeight: core.matHeight(c.matW, spec.shape, c.aspect),
    pin: core.pinAnchor(spec),
    start: pls.map((_, i) => core.photoStart(spec, i, c.leadIn)),
    duration: core.collageDuration(spec),
    placements: pls,
    states,
    mapBeats: c.mapBeats
      ? core.slideLocalBeats(c.mapBeats.beats, c.mapBeats.trackStart, c.mapBeats.trimStart, c.mapBeats.trimEnd, c.mapBeats.holdStart)
      : null,
  })
}
process.stdout.write(JSON.stringify(out))
