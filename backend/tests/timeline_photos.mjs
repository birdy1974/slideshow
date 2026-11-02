// Driver for test_timeline_photos.py: runs `src/timelinePhotos.ts` — the list the
// background pickers offer — over fake storylines Python generates, so the rules
// about which files may become a background are pinned by a test rather than by
// what the popup happens to show today.
//
//   node --experimental-strip-types timeline_photos.mjs < cases.json
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const m = await import(path.join(here, '..', '..', 'src', 'timelinePhotos.ts'))

const input = JSON.parse(readFileSync(0, 'utf8'))
const out = []
for (const c of input.cases || []) {
  const pictures = m.timelinePictures(c.media)
  out.push({
    pictures,
    reuse: m.pictureReuse(pictures),
    roots: m.timelinePictures(c.media).map(p => `${p.rootName}|${p.path}`),
    labels: m.timelinePictures(c.media).map(p => p.note),
    split: m.pictureRootAndPath(String(c.split?.path ?? ''), String(c.split?.name ?? '')),
  })
}
process.stdout.write(`${JSON.stringify(out, null, 1)}\n`)
