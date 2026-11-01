// Driver for test_karaoke_twin.py: runs the browser twin (src/karaokeCore.ts)
// over cases Python generates, and prints the derived plan plus the preview
// state at sampled times, so every number can be compared against
// backend/app/karaoke.py.
//
//   node --experimental-strip-types karaoke_twin.mjs < cases.json
//
// The plan is what both engines draw from — sentence windows, word windows — so
// this is the file that decides whether the popup can promise "what you time is
// what the MP4 does".
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'karaokeCore.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases) {
  const data = { ...core.KARAOKE_DEFAULTS, ...(c.data || {}) }
  const plan = core.planOf(c.text, data, { from: c.from ?? 0, to: c.to ?? 0 })
  const frames = []
  for (const t of c.times || []) {
    const frame = core.karaokeAt(plan, t, data.style)
    frames.push({ t, frame, states: core.wordStates(plan, frame, data.style) })
  }
  out.push({
    plan,
    frames,
    groups: core.sentenceGroups(core.tokenize(c.text), data.words, data.maxWords),
    auto: core.autoTime(core.tokenize(c.text), [], data.maxWords, c.from ?? 0, c.to ?? 0).map(w => w.t),
    signed: core.textSignature(core.tokenize(c.text)),
    warnings: plan.sentences.length === 0 && (data.words || []).some(w => Number.isFinite(w?.t)) ? 'times but no sentences' : '',
  })
}
process.stdout.write(JSON.stringify(out))
