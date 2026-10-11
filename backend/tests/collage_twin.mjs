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
  const states = c.times.map(t => pls.map((_, i) => core.photoState(spec, i, t, c.leadIn, c.aspect)))
  const camera = c.camera
    ? { mode: c.camera, states: c.times.map(t => core.cameraState(spec, t, c.leadIn, c.aspect)) }
    : null
  out.push({
    id: c.id,
    hash: core.hash01(c.hashArgs[0], c.hashArgs[1], c.hashArgs[2]),
    matHeight: core.matHeight(c.matW, spec.shape, c.aspect),
    pin: core.pinAnchor(spec),
    // The three lanes per photo: what it enters with, what it does while it is
    // on screen, and how it leaves — plus the timings those imply.
    lanes: {
      anim: pls.map((_, i) => core.photoAnim(spec, i)),
      pin: pls.map((_, i) => core.pinAnchor(spec, i)),
      motion: pls.map((_, i) => core.photoMotion(spec, i)),
      look: pls.map((_, i) => core.photoLookAnim(spec, i)),
      // The animated look at every sampled time, and which beats a 'beat'
      // motion would punch on (both engines thin a long list the same way).
      lookState: c.times.map(t => pls.map((_, i) => core.photoLookState(spec, i, t, c.leadIn))),
      punches: c.punchWindow ? core.beatPunches(spec, c.punchWindow[0], c.punchWindow[1]) : null,
      exit: pls.map((_, i) => core.exitMode(spec, i)),
      schedule: core.exitScheduleMode(spec),
      exitOffset: pls.map((_, i) => core.exitOffset(spec, i)),
      exitFly: pls.map((_, i) => core.exitFlyLength(spec, i)),
      exitTotal: core.exitTotal(spec),
      base: core.baseDuration(spec),
    },
    start: pls.map((_, i) => core.photoStart(spec, i, c.leadIn)),
    duration: core.collageDuration(spec),
    placements: pls,
    states,
    camera,
    // Free seeded from what this arrangement shows (a chip dropped on the
    // preview) — and the placements that seed then produces.
    freeSeed: core.freeFromDisplayed(spec, c.aspect, false).map(p => ({ cx: p.cx, cy: p.cy, rot: p.rot, w: p.w })),
    freeSeedPlacements: core.placements({ ...spec, layout: 'free', photos: core.freeFromDisplayed(spec, c.aspect, false) }, c.aspect),
    mapBeats: c.mapBeats
      ? core.slideLocalBeats(c.mapBeats.beats, c.mapBeats.trackStart, c.mapBeats.trimStart, c.mapBeats.trimEnd, c.mapBeats.holdStart)
      : null,
    sizeActions: c.sizeActions ? {
      one: core.setPhotoSize(spec.photos, c.sizeActions.index, c.sizeActions.value).map(photo => photo.size ?? null),
      all: core.scaleAllPhotoSizes(spec, c.sizeActions.target).map(photo => photo.size ?? null),
    } : null,
    freeDrag: c.freeDrag
      ? core.freeDragCenter(c.freeDrag.cx, c.freeDrag.cy, c.freeDrag.dx, c.freeDrag.dy, c.freeDrag.width, c.freeDrag.height)
      : null,
    frameClips: c.includeFrameClips ? core.COLLAGE_FRAME_SHAPES.map(shape => [shape, core.frameClipPath(shape) ?? null]) : null,
  })
}
process.stdout.write(JSON.stringify(out))
