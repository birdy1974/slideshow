// Driver for test_text_motion_twin.py: evaluates stacks with the JavaScript
// twin (src/textMotionCore.ts) on the layout Python computed with HarfBuzz and
// prints every composed state as JSON, for a channel-by-channel comparison
// with backend/app/text_motion.py.
//
//   node --experimental-strip-types text_motion_twin.mjs < cases.json
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'textMotionCore.ts'))
const registry = JSON.parse(readFileSync(path.join(here, '..', '..', 'registry', 'text-motion.json'), 'utf8'))
const effects = Object.fromEntries(registry.effects.map(e => [e.id, e]))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases) {
  const ctx = core.compileStack(c.caption, c.layout, effects)
  const states = []
  for (const level of c.levels) {
    for (const u of (ctx.units[level] || [])) {
      for (const t of c.times) states.push({ level, index: u.index, t, s: core.evaluate(ctx, u, t) })
    }
  }
  out.push({ gran: ctx.gran, levels: ctx.levels, warnings: ctx.warnings, conflicts: ctx.conflicts, states,
             ambient: c.times.map(t => core.ambientOpacity(ctx, t)) })
}
process.stdout.write(JSON.stringify(out))
