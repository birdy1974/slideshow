// Driver for test_karaoke_wordbar.py: runs the word bar's geometry out of
// src/karaokeCore.ts over cases Python generates, so the bar under the karaoke
// strip is pinned by numbers and not by how the popup happens to look today.
//
//   node --experimental-strip-types karaoke_wordbar.mjs < cases.json
//
// Every case hands over one sentence (the plan's own shape) and gets back the bar
// as the popup draws it, plus the result of dragging one of its dividers.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'karaokeCore.ts'))

const out = []
for (const c of input_cases()) {
  const bar = core.karaokeWordBar(c.line, c.ceiling)
  const cuts = []
  for (const drag of c.drag || []) {
    const res = core.karaokeWordBarCut(bar, drag.cut, drag.seconds, { minGap: drag.minGap })
    cuts.push(res ? { i: res.i, t: res.t, delta: res.delta } : null)
  }
  out.push({
    bar: bar && {
      from: bar.from, to: bar.to, dur: bar.dur,
      words: bar.words.map(w => ({ i: w.i, t0: w.t0, t1: w.t1, left: round(w.left), width: round(w.width) })),
    },
    // The mapping both ways: a drawn word and a pointer are the same function.
    edges: bar ? [core.karaokeWordBarTime(bar, 0), core.karaokeWordBarTime(bar, 0.5), core.karaokeWordBarTime(bar, 1)] : null,
    frac: bar && bar.words.length ? round(core.karaokeWordBarFraction(bar, bar.words[bar.words.length - 1].t0)) : null,
    cuts,
  })
}
function round (v) { return Math.round(v * 10000) / 10000 }
function input_cases () {
  return JSON.parse(readFileSync(0, 'utf8')).cases || []
}
process.stdout.write(`${JSON.stringify(out, null, 1)}\n`)
