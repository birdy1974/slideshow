// Driver for test_collage_tilt_handles.py: evaluates the preview's
// turn-a-photo helpers (src/collageCore.ts) on the cases Python serialises and
// prints one result object per case, for a value-by-value comparison with an
// independent Python computation.
//
//   node --experimental-strip-types tilt_handles.mjs < cases.json
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const here = path.dirname(fileURLToPath(import.meta.url))
const core = await import(path.join(here, '..', '..', 'src', 'collageCore.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = input.cases.map(c => ({
  id: c.id,
  // normalizeTilt(value, decimals)
  normalize: c.normalize ? core.normalizeTilt(c.normalize.value, c.normalize.decimals) : null,
  // photoTilt(photo)
  tilt: c.tilt ? core.photoTilt(c.tilt.photo) : null,
  // setPhotoRot(photos, index, degrees) — rot reported as null when absent
  set: c.set
    ? core.setPhotoRot(c.set.photos, c.set.index, c.set.degrees)
      .map(p => ({ path: p.path, size: p.size ?? null, rot: p.rot ?? null, cx: p.cx ?? null, cy: p.cy ?? null }))
    : null,
  // pointerAngle(ox, oy, px, py)
  pointer: c.pointer ? core.pointerAngle(c.pointer[0], c.pointer[1], c.pointer[2], c.pointer[3]) : null,
  // rotateDragTilt(startTilt, startAngle, angle, snap)
  drag: c.drag ? core.rotateDragTilt(c.drag[0], c.drag[1], c.drag[2], c.drag[3] ?? 0) : null,
  // rotateKnobLift(gap, pivotY, upY, band, half) — signed, 0 = onto the pivot
  knob: c.knob ? core.rotateKnobLift(c.knob.gap, c.knob.pivotY, c.knob.upY, c.knob.band, c.knob.half) : null,
  // the rot a stored tilt becomes in the Free placements (the render reads this)
  freeRot: c.freeRot ? core.placements(core.normalizeCollage(c.freeRot), 16 / 9).map(p => p.rot) : null,
}))
process.stdout.write(JSON.stringify(out))
