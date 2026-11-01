// Driver for test_collage_reveal.py: runs src/collageReveal.ts over the cases
// Python generates so the render's twin can be compared point by point.
//
//   node --experimental-strip-types collage_reveal_twin.mjs < cases.json
//
// The grid is returned as a bit string (one char per sampled point, row-major):
// small enough to put in a failure message, exact enough to prove the two
// implementations agree on which pixels a given reveal has uncovered.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'collageCore.ts'))

const GRID = 12
function grid (kind, p, dir) {
  const out = []
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) out.push(core.revealAlpha(kind, (c + 0.5) / GRID, (r + 0.5) / GRID, p, dir))
  }
  return out.join('')
}

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases) {
  const geom = core.revealGeom(c.kind)
  out.push({
    kind: c.kind,
    grid: grid(c.kind, c.p, c.dir || 0),
    coverage: core.revealCoverage(c.kind, c.p, c.dir || 0, 16),
    progress: core.revealProgress(c.kind, c.p),
    length: core.revealLength(c.kind),
    known: !!geom,
    fam: geom ? geom.fam : '',
    mirror: geom ? geom.mirror : '',
    mask: core.revealMaskStyle(c.kind, c.p, c.dir || 0) ? JSON.stringify(core.revealMaskStyle(c.kind, c.p, c.dir || 0)) : '',
  })
}
process.stdout.write(JSON.stringify(out))
