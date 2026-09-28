// A small mosaic that shows a collage template's layout — the picture
// instead of the words "1 + 2 left". Drawn from the same slot table the
// stage and the MP4 use (COLLAGE_TEMPLATES), with the collage's current
// photo shape and frame style, so a 4:3 page really looks different from a
// square one and polaroid mats keep their caption strip. Only the template's
// own slots are drawn (extra photos pile onto them on the stage, which would
// hide the design here); slots the current photos do not fill are dashed.
import { FRAME_H, FRAME_W } from './textMotionScene'
import { frameBorderFrac, photoAspect, photoFrame, type CollageSpec, type CollageTemplate } from './collageCore'

const W = 100
const H = W / (FRAME_W / FRAME_H)
const TONES = 3

export function CollageTemplateThumb({ template, spec, photoCount }: { template: CollageTemplate; spec: CollageSpec; photoCount: number }) {
  const fr = photoFrame(spec, null)
  const aspect = photoAspect(spec.shape)
  const n = Math.max(0, Math.floor(photoCount))
  const round = fr.shape === 'circle' || fr.shape === 'oval'
  return <svg className="collage-template-thumb" viewBox={`0 0 ${W} ${H}`} aria-hidden="true" focusable="false">
    <rect className="tt-frame" x={0} y={0} width={W} height={H} rx={2} />
    {template.slots.map((p, i) => {
      const border = frameBorderFrac(fr) * p.w
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
  </svg>
}
