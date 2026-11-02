// Driver for test_karaoke_strip.py: runs the strip's pure geometry helpers out of
// src/karaokeCore.ts over cases Python generates, so the ruler's density, the
// zoom clamp and the block's minimum width are pinned by a test and not by how
// the popup happens to look today.
//
//   node --experimental-strip-types karaoke_strip.mjs < cases.json
//
// The strip draws everything as a percent of the clip, so a zoom only changes how
// wide the strip is; these are the three things that are *not* percentages and so
// have to follow it.
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'karaokeCore.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases || []) {
  const span = Number(c.span)
  const raw = Number(c.zoom)
  const zoom = core.clampKaraokeZoom(raw)
  const step = core.karaokeTickStep(span, zoom)
  out.push({
    span,
    zoom,
    requested: raw,
    step,
    ticks: core.karaokeTicks(span, zoom),
    labels: core.karaokeTicks(span, zoom).map(t => core.karaokeTickLabel(step, t)),
    floor: Math.round(core.karaokeBlockFloor(zoom) * 10000) / 10000,
    clip: core.karaokeClipWindow(c.item || {}, c.kept),
    clock: core.karaokeClipClock(Number(c.t ?? 0), core.karaokeClipWindow(c.item || {}, c.kept)),
    x: Number(c.x ?? 0),
    box: Number(c.box ?? 0),
    follow: core.karaokeFollowScroll(Number(c.x ?? 0), Number(c.box ?? 0)),
    min: core.KARAOKE_ZOOM_MIN,
    max: core.KARAOKE_ZOOM_MAX,
  })
}
process.stdout.write(`${JSON.stringify(out, null, 1)}\n`)
