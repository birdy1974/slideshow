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
// Only a prefix of each path is compared: the point of the shape is that a bucket
// maps to a column, and a full 1000-point path would make the test a diff of numbers.
const trimPaths = p => (p ? { envelope: p.envelope.slice(0, 24), inner: (p.inner || '').slice(0, 24), centre: p.centre } : null)
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
    // The rail's wheel, and one line divided evenly: both decisions live in core so
    // that they can be measured instead of eyeballed in a browser.
    wheel: c.wheel === undefined ? null : core.karaokeRailWheelScroll(
      c.wheel.deltaY, c.wheel.deltaX, c.wheel.deltaMode, c.wheel.scrollHeight, c.wheel.clientHeight),
    // Does the plan read the flags the way the pane draws them? One case per row of
    // the rail, so "the sentence in the words pane matches the numbered sentence" is
    // a measured property of `planOf`, not a hope about CSS.
    breaks: c.breaks === undefined ? null : core.planOf(String(c.breaks.text ?? ""), { words: c.breaks.words }, { duration: 12 })
      .sentences.map(l => [l.from, l.to]),
    even: c.even === undefined ? null : core.karaokeEvenWordLine(c.even.from, c.even.to, c.even.count),
    // The audio lane's vertical scale, and the two paths it draws: a peak envelope
    // with an RMS band inside it, silence left as the centre line only.
    wave: c.wave === undefined ? null : {
      linear: c.wave.values.map(v => round(core.karaokeWaveLevel(v, c.wave.reference, {}))),
      dB: c.wave.values.map(v => round(core.karaokeWaveLevel(v, c.wave.reference, { log: true, floorDb: c.wave.floorDb ?? 48 }))),
      paths: trimPaths(core.karaokeWavePaths(c.wave.values, c.wave.rms ?? null, c.wave.reference, { log: !!c.wave.log })),
    },
    budget: c.zoomAt === undefined ? null : core.karaokeLabelBudget(c.zoomAt),
    // The three-line zoom: what the strip has to be widened to, and what is
    // actually in view when the caption has no neighbour on one side.
    fit: (c.span === undefined || !c.lines) ? null : (() => {
      // The zoom is derived from the very span the helper reports, which is the
      // pair the popup uses: a preset that fitted a different window than it
      // announces would be a lie in the notice.
      const span = core.karaokeNeighbourSpan(c.plan, c.at, c.back, c.forward)
      return { zoom: core.karaokeZoomToFit(c.span, span ? span.to - span.from : 0), span, max: core.KARAOKE_ZOOM_MAX }
    })(),
    // A typed sentence end, turned back into the last word's hold — the field and
    // the drag have to produce the same number, or the bar shows one end and the
    // render does another.
    hold: (!bar || c.end === undefined) ? null
      : core.karaokeHoldForEnd(bar.words[bar.words.length - 1].t0, c.end, c.tail, bar.to),
  })
}
function round (v) { return Math.round(v * 10000) / 10000 }
function input_cases () {
  return JSON.parse(readFileSync(0, 'utf8')).cases || []
}
process.stdout.write(`${JSON.stringify(out, null, 1)}\n`)
