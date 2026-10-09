// A small mosaic that shows a collage arrangement — the picture instead of
// the words "Honeycomb" or "1 + 2 left". The mats come from the same
// placements() the stage and the MP4 use, run with the collage's current
// photo shape and frame style — so a 4:3 page really looks different from a
// square one, polaroid mats keep their caption strip, and a template mat
// that is scaled down to fit its slot is small here too.
//
// Generated layouts are drawn for the collage's own photo count (they
// re-flow as photos come and go); templates draw their own slots, and slots
// the current photos do not fill are dashed. Per-photo sizes and frames are
// left out on purpose: the thumbnails compare arrangements, not photos.
import { useMemo } from 'react'
import { FRAME_H, FRAME_W } from './textMotionScene'
import { frameBorderFrac, freeFromDisplayed, photoAspect, photoFrame, placements, type CollageLayout, type CollagePhoto, type CollageSpec, type CollageTemplate } from './collageCore'

const W = 100
const H = W / (FRAME_W / FRAME_H)
const TONES = 3
/** Placeholder count for generated layouts while the collage has no photos. */
const EMPTY_PREVIEW_COUNT = 5

export function CollageArrangementThumb({ spec, layout, template, photoCount }: {
  spec: CollageSpec
  /** The arrangement to draw — a generated layout, or 'template' with `template`. */
  layout: CollageLayout
  template?: CollageTemplate
  photoCount: number
}) {
  const fr = photoFrame(spec, null)
  // Arrangement thumbs compare layouts, not individual photos; for 'native'
  // shape fall back to 4:3 (the most common photo ratio) so every slot in
  // the schematic is the same height. The real preview measures each photo.
  const aspect = photoAspect(spec.shape === 'native' ? '4:3' : spec.shape)
  const n = Math.max(0, Math.floor(photoCount))
  const round = fr.shape === 'circle' || fr.shape === 'oval'
  const isTemplate = layout === 'template'
  // Free keeps the photos where the user dragged them; before that it starts
  // from the current arrangement (the same seeding the Layout switch does).
  const freeKey = layout === 'free'
    ? JSON.stringify([spec.layout, spec.template, spec.photos.map(p => [p.cx, p.cy, p.rot, p.w])])
    : ''
  const frameKey = JSON.stringify(spec.frame ?? null)
  const mats = useMemo(() => {
    const count = isTemplate ? (template?.slots.length ?? 0) : (n > 0 ? n : EMPTY_PREVIEW_COUNT)
    const photos: CollagePhoto[] = Array.from({ length: count }, (_, i) => ({ path: `slot-${i}` }))
    if (layout === 'free') {
      const from: CollageSpec = { ...spec, layout: spec.layout === 'free' ? 'stack' : spec.layout, photos: spec.photos.length ? spec.photos : photos }
      const seeded = freeFromDisplayed(from, FRAME_W / FRAME_H, true)
      photos.forEach((p, i) => {
        p.cx = seeded[i]?.cx ?? 50
        p.cy = seeded[i]?.cy ?? 50
        p.rot = seeded[i]?.rot ?? 0
        p.w = seeded[i]?.w
      })
    }
    const design: CollageSpec = { ...spec, layout, template: isTemplate ? template?.id : spec.template, randomSize: false, photos }
    return placements(design, FRAME_W / FRAME_H)
    // The design depends on these primitives only — not on the spec object
    // identity, which changes on every edit (and every drag on the stage).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout, template?.id, n, spec.shape, spec.seed, spec.gap, frameKey, freeKey])
  return <svg className="collage-arrangement-thumb" viewBox={`0 0 ${W} ${H}`} aria-hidden="true" focusable="false">
    <rect className="tt-frame" x={0} y={0} width={W} height={H} rx={2} />
    {mats.map((p, i) => {
      const border = frameBorderFrac(fr) * W
      const bottom = fr.shape === 'polaroid' ? 0.205 * p.w : border
      const photoW = Math.max(0.5, p.w - 2 * border)
      const photoH = photoW / aspect
      const matH = photoH + border + bottom
      const cx = p.cx
      const cy = p.cy / 100 * H
      const x = cx - p.w / 2
      const y = cy - matH / 2
      const rx = fr.shape === 'rounded' ? Math.min(p.w, matH) * Math.max(0, Math.min(50, fr.radius)) / 100 : 0
      const filled = i < n
      const cls = `tt-slot ${filled ? `filled tone-${i % TONES}` : 'empty'}`
      const matStyle = filled && fr.shape !== 'none' ? { fill: fr.color } : undefined
      return <g key={i} className={cls} transform={p.rot ? `rotate(${p.rot.toFixed(2)} ${cx.toFixed(2)} ${cy.toFixed(2)})` : undefined}>
        {fr.shape !== 'none' && (round
          ? <ellipse className="tt-mat" style={matStyle} cx={cx} cy={cy} rx={p.w / 2} ry={matH / 2} />
          : <rect className="tt-mat" style={matStyle} x={x} y={y} width={p.w} height={matH} rx={rx} />)}
        {round
          ? <ellipse className="tt-photo" cx={cx} cy={y + border + photoH / 2} rx={photoW / 2} ry={photoH / 2 * (fr.shape === 'oval' ? 0.84 : 1)} />
          : <rect className="tt-photo" x={x + border} y={y + border} width={photoW} height={photoH} rx={rx > 0 ? Math.max(0, rx - 0.6) : 0} />}
      </g>
    })}
    {layout === 'free' && <g className="tt-glyph" transform={`translate(${W - 11} ${H - 11})`}>
      <circle cx={5} cy={5} r={6.5} />
      <path d="M5 1.2 L5 8.8 M1.2 5 L8.8 5 M3.6 2.6 L5 1.2 L6.4 2.6 M3.6 7.4 L5 8.8 L6.4 7.4 M2.6 3.6 L1.2 5 L2.6 6.4 M7.4 3.6 L8.8 5 L7.4 6.4" />
    </g>}
  </svg>
}
