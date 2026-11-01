// Photo-collage layout + choreography — the JavaScript twin of
// backend/app/collage.py. Both engines turn the same collage spec (stored on
// a text-frame item as `collage`) into the same placements and the same
// per-photo motion, so the editor preview and the MP4 agree.
//
// Everything here is pure maths on frame percentages:
//   cx / cy / w   — mat centre x/y and mat width, in % of the frame
//   dx            — x offset in % of the frame WIDTH
//   dy            — y offset in % of the frame HEIGHT
//   rot           — degrees (clockwise, CSS convention)
// The engines convert to their own pixels: CSS in the preview, FFmpeg
// filter expressions in the renderer.

export type CollageLayout = 'stack' | 'grid' | 'scatter' | 'filmstrip' | 'fan' | 'masonry' | 'free' | 'template' | 'honeycomb' | 'zigzag' | 'arc' | 'photowall' | 'booth' | 'silhouette' | 'cube'
// ---------------------------------------------------------------------------
/** The kinds offered in the picker. */
export type CollageRevealKind =
  | 'wipeUp' | 'wipeDown' | 'wipeLeft' | 'wipeRight'
  | 'diagDownRight' | 'diagDownLeft'
  | 'iris' | 'irisOut' | 'clock' | 'pinwheel'
  | 'blindsDown' | 'blindsRight' | 'mosaic' | 'mosaicOut'
  | 'diamond' | 'curtain'

/** Every id, in the order the picker lists them. */
export const COLLAGE_REVEALS: CollageRevealKind[] = [
  'wipeUp', 'wipeDown', 'wipeLeft', 'wipeRight',
  'diagDownRight', 'diagDownLeft',
  'iris', 'irisOut', 'clock', 'pinwheel',
  'blindsDown', 'blindsRight', 'mosaic', 'mosaicOut',
  'diamond', 'curtain',
]

export type RevealFamily = 'linear' | 'curtain' | 'diamond' | 'radial' | 'wedge' | 'repeat' | 'mosaic'

export interface RevealGeom {
  fam: RevealFamily
  label: string
  hint: string
  /** Axis `dir: 1` mirrors on; 'none' = reversing changes nothing visible. */
  mirror: 'x' | 'y' | 'both' | 'none'
  /** Bars / grid divisions / wedge count, for the tiled families. */
  n?: number
  /** Sweep the other way round: what is uncovered last becomes what is left. */
  out?: boolean
  /** Direction the uncovered side grows toward, degrees clockwise from up. */
  angle?: number
  /** Whether the picker offers the reverse toggle for this one. */
  reversible?: boolean
}

export const COLLAGE_REVEAL_GEOM: Record<CollageRevealKind, RevealGeom> = {
  wipeUp: { fam: 'linear', label: 'Wipe up', hint: 'Uncovered from the bottom edge upward', mirror: 'y', angle: 0, reversible: true },
  wipeDown: { fam: 'linear', label: 'Wipe down', hint: 'Uncovered from the top edge downward', mirror: 'y', angle: 180, reversible: true },
  wipeLeft: { fam: 'linear', label: 'Wipe left', hint: 'Uncovered from the right edge leftward', mirror: 'x', angle: 270, reversible: true },
  wipeRight: { fam: 'linear', label: 'Wipe right', hint: 'Uncovered from the left edge rightward', mirror: 'x', angle: 90, reversible: true },
  diagDownRight: { fam: 'linear', label: 'Diagonal down-right', hint: 'Uncovered along the diagonal from the top-left corner', mirror: 'both', angle: 135, reversible: true },
  diagDownLeft: { fam: 'linear', label: 'Diagonal down-left', hint: 'Uncovered along the diagonal from the top-right corner', mirror: 'both', angle: 225, reversible: true },
  iris: { fam: 'radial', label: 'Iris in', hint: 'A circle opening from the centre out', mirror: 'none' },
  irisOut: { fam: 'radial', label: 'Iris out', hint: 'The edges closing in on the centre', mirror: 'none', out: true },
  clock: { fam: 'wedge', label: 'Clock', hint: 'A pie sweeping round from twelve o’clock', mirror: 'x', n: 1, reversible: true },
  pinwheel: { fam: 'wedge', label: 'Pinwheel', hint: 'Four wedges sweeping round together', mirror: 'x', n: 4, reversible: true },
  blindsDown: { fam: 'repeat', label: 'Blinds', hint: 'Horizontal slats opening one after another, top first', mirror: 'y', n: 6, reversible: true },
  blindsRight: { fam: 'repeat', label: 'Blinds across', hint: 'Vertical slats opening left to right', mirror: 'x', n: 6, reversible: true },
  mosaic: { fam: 'mosaic', label: 'Mosaic in', hint: 'A grid of tiles appearing from their own centres', mirror: 'both', n: 4, reversible: true },
  mosaicOut: { fam: 'mosaic', label: 'Mosaic out', hint: 'The grid closing tile by tile onto the centre', mirror: 'both', n: 4, out: true, reversible: true },
  diamond: { fam: 'diamond', label: 'Diamond', hint: 'A diamond opening from the centre', mirror: 'none' },
  curtain: { fam: 'curtain', label: 'Curtain', hint: 'Two halves drawing open from the middle', mirror: 'x', reversible: true },
}

/** Seconds one reveal takes at speed 1. The tiled and round ones need a touch
 *  longer than a wipe to read as what they are. */
export const COLLAGE_REVEAL_LENGTH: Record<CollageRevealKind, number> = {
  wipeUp: 0.5, wipeDown: 0.5, wipeLeft: 0.5, wipeRight: 0.5,
  diagDownRight: 0.55, diagDownLeft: 0.55,
  iris: 0.6, irisOut: 0.6, clock: 0.75, pinwheel: 0.75,
  blindsDown: 0.7, blindsRight: 0.7, mosaic: 0.75, mosaicOut: 0.75,
  diamond: 0.6, curtain: 0.6,
}

/** The progress each family is eased with. A wipe wants to start and stop
 *  decisively; the tiled ones want to accelerate, because a constant-speed
 *  slat pattern reads as a machine and an easing one reads as paper. */
const EASE: Record<RevealFamily, (q: number) => number> = {
  linear: q => q * q * (3 - 2 * q),
  curtain: q => q * q * (3 - 2 * q),
  diamond: q => q * q * (3 - 2 * q),
  radial: q => q * q * (3 - 2 * q),
  wedge: q => q,
  repeat: q => q * (2 - q),
  mosaic: q => q * (2 - q),
}

export const isCollageReveal = (v: unknown): v is CollageRevealKind =>
  typeof v === 'string' && (COLLAGE_REVEALS as string[]).includes(v)

/** One field of a geometry entrance: `base + amp × curve(q)`, where `q` is how
 *  far through the entrance the photo is. The curve names are the whole
 *  vocabulary the render can build — powers, a smoothstep, an outBack, a
 *  half-sine — because FFmpeg gets these as text and the preview as numbers.
 *  `hinge`/`recip` are the two shapes that need a floor of their own: the width
 *  a hinge leaves you is the sine of the angle it travelled, and it must not
 *  reach zero or the photo is invisible in the first frames. */
export type GeomCurve = ['settle3' | 'settle2' | 'smooth' | 'one-smooth' | 'one-back' | 'bow', number?, number?]
  | ['hinge', number, 0 | 1] | ['recip', number, 0 | 1]
export type GeomRow = {
  dx?: GeomCurve, dy?: GeomCurve, rot?: GeomCurve, dim?: GeomCurve,
  scale?: GeomCurve, scaleX?: GeomCurve,
  /** Seconds of alpha ramp, which the render does with `fade=t=in` — so it must
   *  stay a linear ramp in both engines, and this is the only alpha a geometry
   *  entrance is allowed. */
  fade: number
}

const GEOM_BACK = (q: number): number => 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
const GEOM_SMOOTH = (q: number): number => q * q * (3 - 2 * q)

/** The value of one curve spec at `q`. `base` is 0 for the offsets and 1 for the
 *  scales, which is why it is a parameter rather than stored in every row. */
export function geomCurve (c: GeomCurve | undefined, q: number, base: number): number {
  if (!c) return base
  const [kind, a = 0, b = 0] = c as [string, number?, number?]
  switch (kind) {
    case 'settle3': return base + a * Math.pow(1 - q, 3)
    case 'settle2': return base + a * Math.pow(1 - q, 2)
    case 'smooth': return base + a * GEOM_SMOOTH(q)
    case 'one-smooth': return base + a * (1 - GEOM_SMOOTH(q))
    case 'one-back': return base + a * (1 - GEOM_BACK(q))
    case 'bow': return base + a * Math.sin(Math.PI * (1 - q))
    case 'hinge': return Math.max(a, Math.sin(Math.PI / 2 * (b ? GEOM_SMOOTH(q) : q)))
    case 'recip': return 1 / Math.max(a, Math.sin(Math.PI / 2 * (b ? GEOM_SMOOTH(q) : q)))
    default: return base
  }
}

export function isGeomAnim (anim: string): anim is CollageGeomAnim {
  return Object.prototype.hasOwnProperty.call(COLLAGE_GEOM, anim)
}

/** The twelve paper-and-card entrances. `dx`/`dy` are percent of the frame (the
 *  same unit the other entrances use), `rot` degrees, and the lengths are seconds
 *  at speed 1 — the number `photoState` divides by, so an entrance cannot take
 *  longer in the preview than in the MP4. */
export const COLLAGE_GEOM: Record<CollageGeomAnim, GeomRow & { label: string, hint: string, length: number }> = {
  cube: {
    label: 'Cube', hint: 'The photo turns in as the side face of a cube, thin until it squares up', length: 0.5,
    dx: ['settle3', 18], scaleX: ['hinge', 0.25, 0], dim: ['settle3', 0.15], fade: 0.14,
  },
  book: {
    label: 'Book', hint: 'Opens like a cover off its left hinge, straightening as it lies flat', length: 0.55,
    dx: ['one-smooth', -12], rot: ['one-smooth', -5], scaleX: ['hinge', 0.06, 1], dim: ['one-smooth', 0.12], fade: 0.12,
  },
  door: {
    label: 'Door', hint: 'Swings open on a hinge, coming out of shadow as it turns', length: 0.55,
    dx: ['one-smooth', -9], scaleX: ['hinge', 0.1, 1], dim: ['one-smooth', 0.25], fade: 0.1,
  },
  unfold: {
    // Only the height changes, which this model can express exactly: width is
    // scale × scaleX and height is scale, so height k at width 1 is scale k and
    // scaleX 1/k.
    label: 'Unfold', hint: 'A sheet unfolding from its middle line, widening in height as it flattens', length: 0.5,
    scale: ['hinge', 0.08, 0], scaleX: ['recip', 0.08, 0], fade: 0.12,
  },
  rollover: {
    label: 'Rollover', hint: 'Rolls up from below, turning over its own top edge as it lands', length: 0.6,
    dy: ['settle3', 28], rot: ['settle3', -28], scale: ['settle3', -0.18], fade: 0.16,
  },
  spiral: {
    label: 'Spiral', hint: 'Turns a quarter of the way round as it grows to size', length: 0.65,
    rot: ['settle3', 90], scale: ['one-back', -0.9, 1], fade: 0.18,
  },
  swish: {
    label: 'Swish', hint: 'Whipped in from the left with a lean that flicks back past upright', length: 0.5,
    dx: ['one-back', -30], rot: ['settle2', -14], fade: 0.1,
  },
  glide: {
    label: 'Glide', hint: 'A long, calm drift in from the lower right, unrotating as it goes', length: 0.7,
    dx: ['one-smooth', 16], dy: ['one-smooth', 12], rot: ['one-smooth', 3], fade: 0.28,
  },
  peek: {
    label: 'Peek', hint: 'Rises from behind the bottom edge, tilting back upright as it clears it', length: 0.5,
    dy: ['settle3', 16], rot: ['settle3', 7], scaleX: ['settle3', -0.1], fade: 0.14,
  },
  boomerang: {
    label: 'Boomerang', hint: 'Comes in short, throws past its resting place and settles back', length: 0.7,
    dx: ['one-back', -20], dy: ['one-back', -9], rot: ['settle3', 24], scale: ['one-back', -0.38, 1], fade: 0.16,
  },
  sling: {
    // The bow of the path is a half-sine of the part still to travel, so it
    // starts and ends on the straight line and is only ever in between.
    label: 'Sling', hint: 'Fired in along a bowed path that flattens as it arrives', length: 0.55,
    dx: ['one-smooth', 24], dy: ['bow', -18], rot: ['settle2', -16], fade: 0.12,
  },
  thread: {
    label: 'Thread', hint: 'Sweeps in from the corner, unspinning as it is pulled straight', length: 0.7,
    dx: ['settle3', -16], dy: ['settle3', -13], rot: ['settle3', -55], scale: ['settle3', -0.3], fade: 0.14,
  },
}

/** Which geometry entrances change a size, and therefore need the render's size
 *  ladder rather than a plain overlay. Derived, so a new row cannot be added
 *  with a scale and forget to ask for the ladder. */
export const COLLAGE_GEOM_SCALES: CollageGeomAnim[] =
  (Object.keys(COLLAGE_GEOM) as CollageGeomAnim[]).filter(id =>
    COLLAGE_GEOM[id].scale !== undefined || COLLAGE_GEOM[id].scaleX !== undefined)

/** One row of the entrance picker for each mask reveal. The label and hint come
 *  out of the table the two engines read, so the name in the UI, the name the
 *  validator accepts and the name the render switches on cannot drift apart. */
export type CollageAnimChoice = { id: CollageAnim, label: string, hint: string }
export const COLLAGE_GEOM_CHOICES: CollageAnimChoice[] = (Object.keys(COLLAGE_GEOM) as CollageGeomAnim[])
  .map(id => ({ id, label: COLLAGE_GEOM[id].label, hint: COLLAGE_GEOM[id].hint }))

export const COLLAGE_REVEAL_CHOICES: CollageAnimChoice[] = COLLAGE_REVEALS.map(id => ({
  id,
  label: COLLAGE_REVEAL_GEOM[id].label,
  hint: COLLAGE_REVEAL_GEOM[id].hint ?? `Uncovers the photo as a ${COLLAGE_REVEAL_GEOM[id].fam} mask`,
}))

export function revealGeom (kind: string | undefined | null): RevealGeom | null {
  return isCollageReveal(kind) ? COLLAGE_REVEAL_GEOM[kind] : null
}

export function revealLength (kind: string | undefined | null): number {
  return isCollageReveal(kind) ? COLLAGE_REVEAL_LENGTH[kind] : 0
}

/** Entrance progress `q` (0…1) → the mask's own `p`, with the family's easing.
 *  The only easing step either engine applies: the render samples `p` at the same
 *  points the preview does, so an eased sweep cannot land a frame apart. */
export function revealProgress (kind: CollageRevealKind, q: number): number {
  const g = COLLAGE_REVEAL_GEOM[kind]
  const t = q < 0 ? 0 : q > 1 ? 1 : q
  return g ? EASE[g.fam](t) : t
}

const DEG = Math.PI / 180

/** Each family reduces a point to one number in 0…1: how far *into* the sweep
 *  that point sits. A point is uncovered when its number has been passed —
 *  `q <= p` — or, for the "out" kinds, when it is still ahead: `q >= 1 - p`.
 *  One comparison for seven families is what makes the Python twin and the FFmpeg
 *  `geq` expression the same maths and not a translation of it. */
export function revealMeasure (g: RevealGeom, x: number, y: number): number {
  switch (g.fam) {
    case 'linear': {
      // Project the point on the sweep direction and renormalise over the mat's
      // own extent in that direction, so p = 1 always means "the far corner is
      // just uncovered" — for a diagonal too, not only for the four axis wipes.
      const a = (g.angle ?? 0) * DEG
      const sx = Math.sin(a)
      const sy = -Math.cos(a)
      const extent = Math.abs(sx) + Math.abs(sy)
      return Math.max(0, Math.min(1, 0.5 + ((x - 0.5) * sx + (y - 0.5) * sy) / extent))
    }
    case 'curtain':
      return Math.abs(x - 0.5) * 2
    case 'diamond':
      // 0 at the centre, 1 at the four corners; the 0.5 contour is a diamond
      // through the edge midpoints, which is what the name asks for.
      return Math.abs(x - 0.5) + Math.abs(y - 0.5)
    case 'radial': {
      // Scaled by the corner distance so p = 1 is exactly the whole mat.
      return Math.min(1, Math.hypot((x - 0.5) * 2, (y - 0.5) * 2) / Math.SQRT2)
    }
    case 'wedge': {
      const n = Math.max(1, Math.round(g.n ?? 1))
      let a = Math.atan2(x - 0.5, 0.5 - y) / (2 * Math.PI)
      if (a < 0) a += 1
      return n === 1 ? a : (a * n) % 1
    }
    case 'repeat': {
      // n slats along the mirror axis, each opening along the other one, a fixed
      // fraction apart so they read as a sequence rather than all at once.
      const n = Math.max(1, Math.round(g.n ?? 6))
      const along = g.mirror === 'y' ? y : x
      const idx = Math.min(n - 1, Math.max(0, Math.floor(along * n)))
      const lead = n > 1 ? idx / (n - 1) * 0.6 : 0
      const across = ((along * n) % 1 + 1) % 1
      return Math.min(1, lead + across * 0.4)
    }
    case 'mosaic': {
      // n×n tiles growing square from their own centres, started in a diagonal
      // wave so it reads as a wave and not as noise.
      const n = Math.max(1, Math.round(g.n ?? 4))
      const cx = Math.min(n - 1, Math.floor(x * n))
      const cy = Math.min(n - 1, Math.floor(y * n))
      const lead = n > 1 ? (cx + cy) / (2 * n - 2) * 0.55 : 0
      const inX = Math.abs((x * n) % 1 - 0.5) * 2
      const inY = Math.abs((y * n) % 1 - 0.5) * 2
      return Math.min(1, lead + Math.max(inX, inY) * 0.45)
    }
  }
  return 1
}

/** Is the point (u, v) of the mat — 0…1 rightwards, 0…1 downwards — uncovered at
 *  progress `p` (0 = nothing shown, 1 = the whole photo)? `dir: 1` mirrors.
 *
 *  Deliberately plain arithmetic: Python reimplements it and FFmpeg runs it as a
 *  `geq` expression, and every operator that differs between those three is a
 *  photo that reveals on screen differently than in the file. */
export function revealAlpha (kind: CollageRevealKind, u: number, v: number, p: number, dir = 0): number {
  const g = COLLAGE_REVEAL_GEOM[kind]
  if (!g) return 1
  let x = u
  let y = v
  if (dir === 1) {
    if (g.mirror === 'x') x = 1 - x
    else if (g.mirror === 'y') y = 1 - y
    else if (g.mirror === 'both') { x = 1 - x; y = 1 - y }
  }
  const t = p < 0 ? 0 : p > 1 ? 1 : p
  const q = revealMeasure(g, x, y)
  // The "out" kinds are the same measure read from the far side: an iris that
  // closes in on the centre instead of opening out of it. They are still
  // *entrances* — nothing is visible at p = 0, everything at p = 1 — which is
  // what the complement of the predicate alone would have got wrong.
  if (g.out) return t > 0 && q >= 1 - t ? 1 : 0
  return q <= t ? 1 : 0
}

/** The uncovered fraction of the mat at progress p, by sampling — used by the
 *  picker's thumbnails, the tests, and the `out` variants' mirror check. */
export function revealCoverage (kind: CollageRevealKind, p: number, dir = 0, grid = 16): number {
  let hit = 0
  for (let r = 0; r < grid; r++) {
    for (let c = 0; c < grid; c++) {
      hit += revealAlpha(kind, (c + 0.5) / grid, (r + 0.5) / grid, p, dir)
    }
  }
  return hit / (grid * grid)
}

/** How finely a sweep is stepped, in BOTH engines: the preview caches one mask
 *  image per step and the render holds one masked sprite per step, so the same
 *  constant is what makes them show the same pixels at the same moment. 32 is
 *  well under a frame of difference at collage sizes on a 25 fps timeline. */
export const REVEAL_STEPS = 32

/** The centre of the step `p` falls in — the mask's own quantised progress.
 *  `revealQuantise` in backend/app/collage_reveal.py is the same function. */
export function revealQuantise (p: number): number {
  if (p >= 1) return 1
  const t = p < 0 ? 0 : p
  return (Math.min(REVEAL_STEPS - 1, Math.max(0, Math.floor(t * REVEAL_STEPS))) + 0.5) / REVEAL_STEPS
}

const maskCache = new Map<string, { [key: string]: string } | undefined>()

/** CSS for one frame of one reveal. The sampled SVG mask is the whole point: the
 *  browser and FFmpeg then rasterise the *same* 0/1 grid the twin test compares,
 *  instead of each interpreting a description of its own. */
export function revealMaskStyle (kind: CollageRevealKind, p: number, dir = 0): { [key: string]: string } | undefined {
  if (p >= 1) return undefined
  const q = revealQuantise(p)
  const step = Math.round(q * REVEAL_STEPS - 0.5)
  const key = `${kind}|${dir}|${step}`
  if (maskCache.has(key)) return maskCache.get(key)
  const grid = 12
  const cells: string[] = []
  for (let r = 0; r < grid; r++) {
    for (let c = 0; c < grid; c++) {
      if (revealAlpha(kind, (c + 0.5) / grid, (r + 0.5) / grid, q, dir) > 0) {
        cells.push(`<rect x="${c}" y="${r}" width="1" height="1"/>`)
      }
    }
  }
  const style = cells.length
    ? (() => {
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${grid} ${grid}" preserveAspectRatio="none"><g fill="#fff">${cells.join('')}</g></svg>`
        const url = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`
        return { maskImage: url, WebkitMaskImage: url, maskSize: '100% 100%', WebkitMaskSize: '100% 100%' }
      })()
    : { maskImage: 'none', WebkitMaskImage: 'none' }
  maskCache.set(key, style)
  return style
}

// The entrance each photo plays. The mask reveals above are part of this type:
// one list of ids wherever an entrance is chosen, so a wipe cannot be offered
// in the picker and rejected by the engine.
export type CollageAnim = 'drop' | 'pop' | 'swing' | 'flip' | 'none' | 'fade' | 'slide' | 'rise' | 'tumble' | 'zoom' | 'fold' | 'glitch' | 'ink' | 'brush'
  | CollageGeomAnim | CollageRevealKind
/** The paper-and-card family: hinge, fold and swing motions that live entirely
 *  in the fields the render can build — offset, rotation, and the two scales.
 *  A real 3D cube or a book on a perspective hinge cannot be composited by
 *  FFmpeg's `scale`+`rotate` at all, and a preview that shows what the export
 *  cannot make is worse than no animation, so this family is expressed as
 *  foreshortening (`scaleX` following the hinge angle) instead of `rotateX`. */
export type CollageGeomAnim = 'cube' | 'book' | 'door' | 'unfold' | 'rollover' | 'spiral'
  | 'swish' | 'glide' | 'peek' | 'boomerang' | 'sling' | 'thread'
/** Every entrance id, reveals included: what the pickers list and what
 *  `normalizeCollage` accepts. */
export type CollageExit = 'none' | 'sweep' | 'deal' | 'shuffle' | 'fade'
export type CollageMotion = 'none' | 'drift' | 'float' | 'orbit' | 'shake' | 'sway' | 'breathe' | 'pulse' | 'beat' | 'move' | 'turn' | 'spin'
/** An animated look: the photo's colour changes over time, on top of whatever
 *  static filter it already has. */
export type CollageLookAnim = 'none' | 'toBw' | 'toColor' | 'flicker' | 'invert' | 'hueFlip'
export type CollageCamera = 'none' | 'pan' | 'zoom' | 'telescope' | 'droste'
export type CollageShape = '4:3' | 'square' | '3:4' | '16:9' | '9:16' | '3:2' | '2:3' | 'native'
export type CollageBgFit = 'fill' | 'fit' | 'stretch' | 'tile' | 'center' | 'span'
export type CollageFrameShape = 'polaroid' | 'none' | 'rect' | 'rounded' | 'circle' | 'oval' | 'heart' | 'star' | 'diamond' | 'hexagon' | 'triangle' | 'octagon' | 'cloud' | 'arch' | 'ticket'
export type CollageStickers = 'none' | 'tape' | 'pin' | 'mix'

export const COLLAGE_LAYOUTS_ALL: CollageLayout[] = ['stack', 'grid', 'scatter', 'filmstrip', 'fan', 'masonry', 'free', 'template', 'honeycomb', 'zigzag', 'arc', 'photowall', 'booth', 'silhouette', 'cube']
export const COLLAGE_SHAPES_ALL: CollageShape[] = ['native', '4:3', 'square', '3:4', '16:9', '9:16', '3:2', '2:3']
export const COLLAGE_ANIMS_ALL: CollageAnim[] = ['drop', 'pop', 'swing', 'flip', 'fade', 'slide', 'rise', 'tumble', 'zoom', 'fold', 'glitch', 'ink', 'brush',
  'cube', 'book', 'door', 'unfold', 'rollover', 'spiral', 'swish', 'glide', 'peek', 'boomerang', 'sling', 'thread',
  'none', ...COLLAGE_REVEALS]
/** Motion one photo keeps doing while it is on screen — the "while" lane of
 *  the three-lane model the text layers use (enter / while / exit). These
 *  five only move and turn the photo, which a render can do per frame; the
 *  scaling ones are separate because FFmpeg can only hold discrete sizes. */
/** Motions that only move and turn a photo: a render can follow them per
 *  frame, so they cost nothing but two expression terms. */
export const COLLAGE_MOTIONS: CollageMotion[] = ['none', 'drift', 'float', 'orbit', 'shake', 'sway', 'breathe', 'pulse', 'beat', 'move', 'turn', 'spin']
/** Motions that change SIZE. FFmpeg's `scale` freezes its output dimensions at
 *  init, so these ride the size ladder: the ladder samples the twin, quantises
 *  the tail and holds each size for its window. Slower to render, which is why
 *  they are a separate group in the picker. */
export const COLLAGE_SCALE_MOTIONS: CollageMotion[] = ['breathe', 'pulse', 'beat']
export const COLLAGE_LOOK_ANIMS: CollageLookAnim[] = ['none', 'toBw', 'toColor', 'flicker', 'invert', 'hueFlip']
/** Motions that travel to an end state and hold it, rather than looping. With
 *  a `motionDuration` they finish at that moment; looping motions settle back
 *  to rest instead, because freezing mid-bob looks broken. */
export const COLLAGE_ONESHOT_MOTIONS: CollageMotion[] = ['move', 'turn', 'spin', 'drift', 'breathe']
/** Motions whose end state the user picks rather than the strength slider: the
 *  editor offers a target handle instead of an amplitude. */
export const COLLAGE_TARGET_MOTIONS: CollageMotion[] = ['move', 'turn']
/** Amplitudes of the per-photo motions: dx / dy in % of the frame, rot in
 *  degrees, speed in radians per second, decay in 1/s (0 = no decay) and scale
 *  as a fraction of the resting size. Both engines build their numbers from
 *  this table, so the MP4 and the preview cannot drift apart. */
export const MOTION: Record<Exclude<CollageMotion, 'none'>, {
  dx: number; dy: number; rot: number; speed: number; decay: number; scale: number
}> = {
  drift: { dx: 9, dy: 3, rot: 0, speed: 0, decay: 0, scale: 0 },
  float: { dx: 0.7, dy: 1.6, rot: 1.2, speed: 1.1, decay: 0, scale: 0 },
  orbit: { dx: 1.8, dy: 1.3, rot: 0, speed: 0.9, decay: 0, scale: 0 },
  shake: { dx: 2.2, dy: 1.4, rot: 1.8, speed: 34, decay: 2.2, scale: 0 },
  sway: { dx: 0, dy: 0, rot: 1.5, speed: 2.15, decay: 0, scale: 0 },
  breathe: { dx: 0, dy: 0, rot: 0, speed: 0, decay: 0, scale: 0.08 },
  pulse: { dx: 0, dy: 0, rot: 0, speed: 2.4, decay: 0, scale: 0.035 },
  beat: { dx: 0, dy: 0, rot: 0, speed: 0, decay: 7, scale: 0.09 },
  // move and turn take their amplitude from the photo's own target (moveX /
  // moveY / turnTo), so their row is all zeros; spin's rot is degrees/second.
  move: { dx: 0, dy: 0, rot: 0, speed: 0, decay: 0, scale: 0 },
  turn: { dx: 0, dy: 0, rot: 0, speed: 0, decay: 0, scale: 0 },
  spin: { dx: 0, dy: 0, rot: 45, speed: 0, decay: 0, scale: 0 },
}
/** The turn a motion reaches at full amplitude, in degrees — sprite-canvas
 *  headroom, exactly like an entrance spin. */
export const MOTION_SPIN: Record<Exclude<CollageMotion, 'none'>, number> = {
  drift: 0, float: 1.2, orbit: 0, shake: 1.8, sway: 1.5, breathe: 0, pulse: 0, beat: 0,
  move: 0, turn: 180, spin: 180,
}
/** Amplitudes of the animated looks: how far the colour travels (saturation
 *  multiplier or brightness multiplier) and how fast it dies out. */
export const LOOK_ANIM: Record<Exclude<CollageLookAnim, 'none'>, {
  sat: number; bright: number; speed: number; decay: number; hue: number
}> = {
  toBw: { sat: 1, bright: 0, speed: 0, decay: 0, hue: 0 },
  toColor: { sat: 1, bright: 0, speed: 0, decay: 0, hue: 0 },
  flicker: { sat: 0, bright: 0.16, speed: 41, decay: 1.8, hue: 0 },
  // invert is a hard flip — FFmpeg's `negate` is on or off, it has no amount —
  // and hueFlip turns the colour wheel half way round.
  invert: { sat: 0, bright: 0, speed: 0, decay: 0, hue: 0 },
  hueFlip: { sat: 0, bright: 0, speed: 0, decay: 0, hue: 180 },
}
/** Most beat punches one photo renders. A long slide of fast music would
 *  otherwise put hundreds of terms into one expression; both engines thin the
 *  list the same way, so they still agree. */
export const BEAT_PUNCH_MAX = 24
export const MOTION_AMOUNT_MIN = 0.25
export const MOTION_AMOUNT_MAX = 2
/** Bounds of a motion's own duration, in seconds (0 = the rest of the slide). */
export const MOTION_DURATION_MIN = 0.3
export const MOTION_DURATION_MAX = 12
/** How far a `move` target may travel, in % of the frame, and how far a `turn`
 *  target may rotate, in degrees. Both clamp, neither fold. */
export const MOVE_RANGE = 60
export const TURN_RANGE = 180
/** Seconds of negative an `invert` look lasts on a photo that has no entrance
 *  to arrive in, so the flip is always visible. */
export const INVERT_MIN = 0.45
/** Seconds a motion takes to fade in once the photo has landed, so it starts
 *  from exactly the resting pose instead of jumping. */
export const MOTION_RAMP = 0.35
export const COLLAGE_FRAME_SHAPES: CollageFrameShape[] = ['polaroid', 'none', 'rect', 'rounded', 'circle', 'oval', 'heart', 'star', 'diamond', 'hexagon', 'triangle', 'octagon', 'cloud', 'arch', 'ticket']

export interface CollageFrame {
  /** Missing = rect (plain rectangular border). */
  shape?: CollageFrameShape
  /** Border thickness as % of the frame width (0 = none). Missing = 0.5; ignored for `none`. */
  width?: number
  /** Border / mat colour. Missing = #ffffff. */
  color?: string
  /** Corner radius as % of the shorter side, for `rounded`. Missing = 5. */
  radius?: number
  /** Soft drop shadow under the mat. Missing = on. */
  shadow?: boolean
}

export interface CollagePhotoLook {
  filter?: string
  filterAmount?: number
  filterAdjust?: Record<string, number>
  crop?: {
    rect?: { x: number; y: number; w: number; h: number } | null
    degrees?: number | null
    lasso?: [number, number][] | null
    feather?: number | null
  } | null
}

export interface CollagePhoto extends CollagePhotoLook {
  path: string
  name?: string
  /** Seconds after the PREVIOUS photo appears (photo 0: after the slide's
   *  visible hold starts). Missing = the auto stagger default. The slide's
   *  duration follows from these: total = Σ delays + entrance + hold. */
  delay?: number
  /** Mat size multiplier for this photo (1 = the layout's default width,
   *  0.5 = half, 3 = 300%). Missing = 1. */
  size?: number
  /** Free-layout resting position (percent of the frame). Missing = 50/50. */
  cx?: number
  cy?: number
  /** Free-layout mat width (percent of the frame width, before the size
   *  multiplier). Set when an arrangement is turned into Free so every mat
   *  keeps the size it had; missing = Free's own rule by photo count. */
  w?: number
  /** Per-photo frame override (shape / colour / thickness). Missing = the spec default. */
  frame?: CollageFrame
  /** Free-layout resting tilt in degrees, clockwise (the rotate handle on the
   *  preview or the tilt slider write it). Missing = 0 (straight). */
  rot?: number
  /** Arrangement position: which slot of the layout this photo occupies
   *  (0 = the first slot). Missing = its own index in the list. The appear
   *  order (list order, `delay`) is independent of this. Free ignores it. */
  slot?: number
  /** Per-photo shape override (the collage-wide `shape` is the default for
   *  photos without one). Missing = the collage's shape. */
  shape?: CollageShape
  /** Enter lane: this photo's own entrance, overriding `spec.animation`.
   *  Missing = inherit, which is what every existing project does. */
  anim?: CollageAnim
  /** Which way this photo's mask reveal runs: 1 mirrors the kind on its own
   *  axis (a "wipe up" becomes a wipe down). Missing = the collage's `animDir`. */
  animDir?: 0 | 1
  /** Speed multiplier on the entrance — 2 halves it, 0.5 doubles it, and it
   *  scales the whole arrival: sweep, alpha ramp, pendulum and the render's size
   *  ladder together. Clamped to [0.05, 2]; missing = the collage's. */
  animSpeed?: number
  /** Exit lane: how this photo leaves, overriding `spec.exit`. 'none' is a
   *  real choice here — it keeps this one on screen while the others go. */
  exit?: CollageExit
  /** While lane: motion this photo keeps doing once it has landed. */
  motion?: CollageMotion
  /** How strong that motion is, MOTION_AMOUNT_MIN…MAX (1 = MOTION's numbers).
   *  `move` and `turn` ignore it: their strength IS the target you picked. */
  motionAmount?: number
  /** How long the motion plays, in seconds from the photo's landing. Missing
   *  or 0 = the rest of the slide. A one-shot motion (move, turn, spin, drift,
   *  breathe) travels across it and holds the end state; a looping one plays
   *  for it and settles back to rest, so it cannot freeze mid-bob. */
  motionDuration?: number
  /** End position of the `move` motion: how far the photo travels from where
   *  it rests, in % of the frame width / height — the units `photoState`'s
   *  dx / dy use, so the render needs no new machinery. */
  moveX?: number
  moveY?: number
  /** End angle of the `turn` motion, in degrees clockwise from the photo's
   *  resting tilt. */
  turnTo?: number
  /** An animated look: this photo's colour changes over time, on top of the
   *  static filter it already has. */
  lookAnim?: CollageLookAnim
}

export interface CollageSpec {
  photos: CollagePhoto[]
  layout: CollageLayout
  animation: CollageAnim
  /** Direction of the mask reveals: 1 mirrors every one on its own axis, so a
   *  slide of "wipe up" photos becomes a slide of wipes going down without
   *  re-picking all of them. Per photo, `photos[].animDir` overrides it. */
  animDir?: 0 | 1
  /** How fast every photo in this collage arrives; see `CollagePhoto.animSpeed`. */
  animSpeed?: number
  shape: CollageShape
  seed: number
  /** Seconds the finished collage stays on screen after the last photo has
   *  landed. Missing = 2. The slide duration is derived from the photo
   *  timings plus this hold (collageDuration). */
  hold?: number
  /** Snap photo arrivals to the music: with this on, each photo's nominal
   *  arrival rolls forward to the next stored beat (see `beats`). */
  beatSync?: boolean
  /** Beat times in the slide's own hold clock (0 = the hold starts, i.e.
   *  before the first photo appears), seconds, ascending. The editor maps
   *  the soundtrack's detected onsets into this clock and stores the result
   *  here — the render uses the stored list, so preview and MP4 agree. */
  beats?: number[]
  /** Depth push-back: when a new photo lands, the photos already on the
   *  pile shrink back a little and dim (drop / pop / flip animations). */
  depth?: boolean
  /** How the photos leave at the end of the slide (after the hold): sweep
   *  outward from the centre, deal off one-by-one, or scatter in seeded
   *  directions. Missing = they stay on until the transition takes over. */
  exit?: CollageExit
  /** Virtual camera over the composed collage: pan across, or zoom into
   *  the last photo (telescope = deep, droste = accelerating deep zoom). */
  camera?: CollageCamera
  /** Optional library picture behind the photos (path like /photos/x.jpg).
   *  Missing = plain background colour. */
  backgroundImage?: string
  /** Background blur strength 0..1 (0 = sharp). Missing = 0. */
  backgroundBlur?: number
  /** How the background picture fills the frame. Missing = fill (cover). */
  backgroundFit?: CollageBgFit
  /** Look (filters/crop) applied to the background picture. */
  backgroundLook?: CollagePhotoLook
  /** Seeded random mat sizes between randomSizeMin and randomSizeMax. A
   *  per-photo size override wins for that photo; Shuffle changes the pattern. */
  randomSize?: boolean
  randomSizeMin?: number
  randomSizeMax?: number
  /** Speed of the photo exit animation, 0.05 (slow) .. 2 (fast). Scales both
   *  the fly-out of each photo and the gap between photos leaving. Missing = 1. */
  exitSpeed?: number
  /** Reverse the exit order: the last (top) photo leaves first instead of
   *  the first one. Missing = off. */
  exitReverse?: boolean
  /** Predefined template id when layout is 'template'. */
  template?: string
  /** Default frame for every photo (per-photo `frame` wins). */
  frame?: CollageFrame
  /** Gutter between photos for grid / photowall / honeycomb, % of the frame. Missing = layout default. */
  gap?: number
  /** Slow pan/zoom inside each photo after it lands (Apple Memories). */
  kenBurns?: boolean
  /** Gentle idle sway after landing (corkboard). */
  sway?: boolean
  /** Corner decorations: washi tape, a pin, or a mix. Missing = none. */
  stickers?: CollageStickers
}

export const MAX_COLLAGE_PHOTOS = 12

/** One photo's resting geometry: mat centre (% of frame), mat width (% of
 *  frame width) and resting tilt (degrees). */
export interface Placement { cx: number; cy: number; w: number; rot: number }

/** Animated offsets of one photo at time t (seconds since the slide's own
 *  segment start, lead-in handles included — pass leadIn so entrances start
 *  after the incoming transition handle). */
export interface PhotoState {
  dx: number; dy: number; rot: number; scale: number; scaleX: number; alpha: number; dim: number
  /** Set while a mask reveal is uncovering this photo: which kind, how far the
   *  sweep has got (0 = nothing shown, 1 = the whole photo) and which way it
   *  runs. Three flat fields rather than one object because the render's twin
   *  returns the same three and the comparison is field by field. The transform
   *  fields stay at rest — a reveal does not move the photo, it uncovers it —
   *  and `alpha` stays 1 because the mask *is* the opacity. */
  revealKind?: CollageRevealKind
  revealP?: number
  revealDir?: 0 | 1
}

// Deterministic pseudo-random number in [0, 1) — bit-identical to
// hash01() in backend/app/collage.py (and the text engines' hash).
export function hash01 (...values: number[]): number {
  let h = 2166136261 >>> 0
  for (const v of values) {
    h = (h ^ (Math.trunc(v) >>> 0)) >>> 0
    h = Math.imul(h, 16777619) >>> 0
  }
  h = (h ^ (h >>> 13)) >>> 0
  h = Math.imul(h, 0x5BD1E995) >>> 0
  h = (h ^ (h >>> 15)) >>> 0
  return h / 4294967296
}

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v)
/** Ease in and out of a 0…1 progress: no jolt at either end, which is what a
 *  photo travelling to a picked position or angle needs. */
const smoothstep = (v: number) => v * v * (3 - 2 * v)

export const COLLAGE_SHAPES: CollageShape[] = ['native', '4:3', '16:9', '3:2', 'square', '2:3', '3:4', '9:16']

/** Resolve the aspect ratio to use for a photo. For fixed shapes it's the
 *  shape's constant; for 'native' it's the photo's own measured w/h, falling
 *  back to 4:3 when the photo hasn't been measured yet. When classifying an
 *  UNKNOWN photo (no ratio yet, e.g. on the server before probing) we bucket
 *  via snapNativeAspect so mats stay at one of the common shapes. */
export function snapNativeAspect (ratio: number): number {
  const r = Number(ratio)
  if (!Number.isFinite(r) || r <= 0) return 4 / 3
  if (r >= 1.15) {
    return Math.abs(r - 16 / 9) < Math.abs(r - 4 / 3) ? 16 / 9 : 4 / 3
  }
  if (r <= 1 / 1.15) {
    return Math.abs(r - 9 / 16) < Math.abs(r - 3 / 4) ? 9 / 16 : 3 / 4
  }
  return 1
}

export function photoAspect (shape: CollageShape | string, nativeRatio?: number): number {
  if (shape === 'native') {
    // Measured: use the real ratio so the mat matches the photo exactly.
    // Unmeasured: fall back to a safe 4:3 default until dimensions arrive.
    const r = Number(nativeRatio)
    return Number.isFinite(r) && r > 0 ? r : 4 / 3
  }
  if (shape === 'square') return 1
  if (shape === '3:4') return 3 / 4
  if (shape === '16:9') return 16 / 9
  if (shape === '9:16') return 9 / 16
  if (shape === '3:2') return 3 / 2
  if (shape === '2:3') return 2 / 3
  return 4 / 3
}

/** The effective shape of photo i: its own override when set, else the
 *  collage-wide default shape. */
export function photoShape (spec: CollageSpec, i: number): CollageShape {
  const s = spec.photos[i]?.shape
  return COLLAGE_SHAPES_ALL.includes(s as CollageShape) ? s as CollageShape : spec.shape
}

/** Aspect ratio of photo i in a spec — its effective shape, or the measured
 *  native ratio of that photo when that shape is 'native'. */
export function photoAspectFor (spec: CollageSpec, i: number, ratios?: ArrayLike<number | undefined>): number {
  const shape = photoShape(spec, i)
  if (shape !== 'native') return photoAspect(shape)
  return photoAspect('native', ratios?.[i])
}

export function photoFrame (spec: { frame?: CollageFrame } | null | undefined, photo?: { frame?: CollageFrame } | null): { shape: CollageFrameShape; width: number; color: string; radius: number; shadow: boolean } {
  const a = spec?.frame || {}
  const b = photo?.frame || {}
  const shape = (COLLAGE_FRAME_SHAPES as string[]).includes(b.shape as string) ? b.shape as CollageFrameShape
    : (COLLAGE_FRAME_SHAPES as string[]).includes(a.shape as string) ? a.shape as CollageFrameShape : 'rect'
  const rawW: unknown = b.width !== undefined ? b.width : a.width
  const numericW = rawW === null || typeof rawW === 'boolean' || (typeof rawW === 'string' && rawW.trim() === '') ? NaN : Number(rawW)
  const width = Number.isFinite(numericW) ? Math.max(0, Math.min(12, numericW)) : 0.5
  const color = (typeof b.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(b.color) ? b.color : (typeof a.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(a.color) ? a.color : '#ffffff'))
  const rawR = b.radius !== undefined ? b.radius : a.radius
  const radius = Number.isFinite(Number(rawR)) ? Math.max(0, Math.min(50, Number(rawR))) : 5
  const shadow = (b.shadow !== undefined ? b.shadow : a.shadow) !== false
  return { shape, width, color, radius, shadow }
}

/** Border width as a share of the FRAME width (0.045 = 4.5 %). It does not
 *  grow with the photo: a big photo and a small one get the same frame
 *  thickness, exactly like the frame's share of the picture on screen. */
export function frameBorderFrac (frame: { shape: CollageFrameShape; width: number }): number {
  if (frame.shape === 'none') return 0
  return Math.max(0, Math.min(0.12, frame.width / 100))
}

/** Height of the mat for a mat width `w` (% of frame width), as a
 *  percentage of the frame HEIGHT. aspect = frameW / frameH. Polaroid
 *  keeps the classic white border + caption strip. */
export function matHeight (w: number, shape: CollageShape, aspect: number, frame?: CollageFrame): number {
  // For callers that don't have per-photo ratios (e.g. the drag ghost), treat
  // 'native' as a 4:3 default. The actual placements path uses photoMatHeight.
  const effShape: CollageShape = shape === 'native' ? '4:3' : shape
  const { k, c } = matLine(effShape, photoFrame({ frame }, null), aspect)
  return k * w + c
}

/** CSS clip-path for a non-rectangular photo frame. Undefined = rectangle. */
export function frameClipPath (shape: CollageFrameShape | string | undefined): string | undefined {
  if (shape === 'circle') return 'circle(50%)'
  if (shape === 'oval') return 'ellipse(50% 42%)'
  if (shape === 'diamond') return 'polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)'
  if (shape === 'hexagon') return 'polygon(25% 6.7%, 75% 6.7%, 100% 50%, 75% 93.3%, 25% 93.3%, 0% 50%)'
  if (shape === 'triangle') return 'polygon(50% 4%, 96% 92%, 4% 92%)'
  if (shape === 'octagon') return 'polygon(30% 0%, 70% 0%, 100% 30%, 100% 70%, 70% 100%, 30% 100%, 0% 70%, 0% 30%)'
  if (shape === 'star') return 'polygon(50% 2%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%)'
  // `path()` uses fixed CSS-pixel coordinates, so a 100×100 path only showed
  // a tiny corner of larger mats. Percent-based polygons scale with every mat.
  if (shape === 'heart') return 'polygon(50% 96%, 37% 84%, 23% 71%, 12% 58%, 4% 46%, 1% 37%, 3% 29%, 9% 21%, 18% 15%, 28% 13%, 38% 17%, 46% 25%, 50% 34%, 54% 25%, 62% 17%, 72% 13%, 82% 15%, 91% 21%, 97% 29%, 99% 37%, 96% 46%, 88% 58%, 77% 71%, 63% 84%)'
  if (shape === 'cloud') return 'polygon(18% 62%, 13% 62%, 9% 59%, 7% 54%, 7% 48%, 9% 43%, 14% 40%, 18% 39%, 19% 34%, 22% 27%, 28% 21%, 36% 18%, 44% 19%, 51% 23%, 56% 30%, 61% 25%, 67% 21%, 74% 20%, 81% 22%, 87% 27%, 90% 34%, 90% 40%, 88% 46%, 86% 49%, 92% 50%, 97% 54%, 99% 60%, 98% 66%, 95% 71%, 90% 73%, 84% 72%, 80% 77%, 74% 81%, 67% 83%, 59% 83%, 51% 82%, 44% 80%, 38% 76%, 33% 72%, 27% 74%, 22% 73%, 18% 70%, 16% 66%)'
  if (shape === 'arch') return 'polygon(8% 100%, 8% 48%, 9% 38%, 12% 29%, 17% 21%, 23% 14%, 31% 8%, 39% 4%, 46% 2%, 54% 2%, 61% 4%, 69% 8%, 77% 14%, 83% 21%, 88% 29%, 91% 38%, 92% 48%, 92% 100%)'
  if (shape === 'ticket') return 'polygon(0% 12%, 6% 0%, 94% 0%, 100% 12%, 100% 88%, 94% 100%, 6% 100%, 0% 88%)'
  if (shape === 'rounded') return undefined
  return undefined
}

/** Predefined fixed layouts: magazine mosaics (no tilt) and polaroid-wall
 *  scrapbook pages (tilted overlapping mats). Slot coordinates are % of the
 *  frame; extra photos beyond the slot count overlay with a seeded offset.
 *
 *  A slot is a box, not a mat size: `w` is the widest the mat may be (% of
 *  the frame width) and `h` the tallest (% of the frame HEIGHT). The mat is
 *  scaled down to fit both — see placements() — so a stacked 2-up stays two
 *  rows with tall polaroid mats or portrait photos instead of overflowing.
 *  Magazine boxes tile the page with ~4 % gaps; the polaroid-wall piles only
 *  cap the height (their overlap is the point) so a 9:16 polaroid cannot
 *  run off the frame. */
export interface TemplateSlot extends Placement { h?: number }

export interface CollageTemplate {
  id: string
  family: 'magazine' | 'polaroid'
  label: string
  hint: string
  slots: TemplateSlot[]
}

export const COLLAGE_TEMPLATES: CollageTemplate[] = [
  { id: 'split-v', family: 'magazine', label: '2-up split', hint: 'Two photos side by side', slots: [
    { cx: 26, cy: 50, w: 46, h: 88, rot: 0 }, { cx: 74, cy: 50, w: 46, h: 88, rot: 0 },
  ] },
  { id: 'split-h', family: 'magazine', label: '2-up stacked', hint: 'Two photos one above the other', slots: [
    { cx: 50, cy: 27, w: 70, h: 42, rot: 0 }, { cx: 50, cy: 73, w: 70, h: 42, rot: 0 },
  ] },
  { id: 'triptych', family: 'magazine', label: 'Triptych', hint: 'Three equal columns', slots: [
    { cx: 18, cy: 50, w: 30, h: 88, rot: 0 }, { cx: 50, cy: 50, w: 30, h: 88, rot: 0 }, { cx: 82, cy: 50, w: 30, h: 88, rot: 0 },
  ] },
  { id: 'trio-left', family: 'magazine', label: '1 + 2 left', hint: 'One large photo on the left, two stacked on the right', slots: [
    { cx: 30, cy: 50, w: 54, h: 88, rot: 0 }, { cx: 78, cy: 28, w: 36, h: 42, rot: 0 }, { cx: 78, cy: 72, w: 36, h: 42, rot: 0 },
  ] },
  { id: 'trio-right', family: 'magazine', label: '1 + 2 right', hint: 'Two stacked on the left, one large on the right', slots: [
    { cx: 22, cy: 28, w: 36, h: 42, rot: 0 }, { cx: 22, cy: 72, w: 36, h: 42, rot: 0 }, { cx: 70, cy: 50, w: 54, h: 88, rot: 0 },
  ] },
  { id: 'trio-top', family: 'magazine', label: '1 + 2 top', hint: 'One wide photo on top, two below', slots: [
    { cx: 50, cy: 27, w: 88, h: 46, rot: 0 }, { cx: 26, cy: 74, w: 42, h: 40, rot: 0 }, { cx: 74, cy: 74, w: 42, h: 40, rot: 0 },
  ] },
  { id: 'quad', family: 'magazine', label: '2 × 2', hint: 'Four equal tiles', slots: [
    { cx: 26, cy: 28, w: 44, h: 42, rot: 0 }, { cx: 74, cy: 28, w: 44, h: 42, rot: 0 },
    { cx: 26, cy: 72, w: 44, h: 42, rot: 0 }, { cx: 74, cy: 72, w: 44, h: 42, rot: 0 },
  ] },
  { id: 'one-plus-three', family: 'magazine', label: '1 + 3', hint: 'One large left, three stacked right', slots: [
    { cx: 32, cy: 50, w: 56, h: 88, rot: 0 }, { cx: 80, cy: 20, w: 32, h: 26, rot: 0 },
    { cx: 80, cy: 50, w: 32, h: 26, rot: 0 }, { cx: 80, cy: 80, w: 32, h: 26, rot: 0 },
  ] },
  { id: 'hero-row', family: 'magazine', label: 'Hero + 3', hint: 'Wide hero on top, three across the bottom', slots: [
    { cx: 50, cy: 30, w: 90, h: 52, rot: 0 }, { cx: 18, cy: 77, w: 28, h: 34, rot: 0 },
    { cx: 50, cy: 77, w: 28, h: 34, rot: 0 }, { cx: 82, cy: 77, w: 28, h: 34, rot: 0 },
  ] },
  { id: 'five-mosaic', family: 'magazine', label: '1 + 4', hint: 'One large photo on the left, four small in a block on the right', slots: [
    { cx: 30, cy: 50, w: 54, h: 88, rot: 0 }, { cx: 68, cy: 28, w: 18, h: 42, rot: 0 },
    { cx: 88, cy: 28, w: 18, h: 42, rot: 0 }, { cx: 68, cy: 72, w: 18, h: 42, rot: 0 }, { cx: 88, cy: 72, w: 18, h: 42, rot: 0 },
  ] },
  { id: 'six-grid', family: 'magazine', label: '3 × 2', hint: 'Six equal tiles', slots: [
    { cx: 18, cy: 28, w: 30, h: 42, rot: 0 }, { cx: 50, cy: 28, w: 30, h: 42, rot: 0 }, { cx: 82, cy: 28, w: 30, h: 42, rot: 0 },
    { cx: 18, cy: 72, w: 30, h: 42, rot: 0 }, { cx: 50, cy: 72, w: 30, h: 42, rot: 0 }, { cx: 82, cy: 72, w: 30, h: 42, rot: 0 },
  ] },
  { id: 'polaroid-pile', family: 'polaroid', label: 'Pile', hint: 'A fixed overlapping pile in the middle', slots: [
    { cx: 42, cy: 48, w: 34, h: 78, rot: -11 }, { cx: 58, cy: 44, w: 34, h: 78, rot: 8 },
    { cx: 48, cy: 56, w: 36, h: 78, rot: 3 }, { cx: 36, cy: 40, w: 30, h: 78, rot: -18 },
    { cx: 64, cy: 58, w: 30, h: 78, rot: 14 }, { cx: 50, cy: 38, w: 28, h: 78, rot: -4 },
  ] },
  { id: 'polaroid-diagonal', family: 'polaroid', label: 'Diagonal', hint: 'Photos stepping down from left to right', slots: [
    { cx: 22, cy: 28, w: 32, h: 78, rot: -8 }, { cx: 40, cy: 40, w: 32, h: 78, rot: 4 },
    { cx: 58, cy: 52, w: 32, h: 78, rot: -5 }, { cx: 74, cy: 66, w: 32, h: 78, rot: 7 },
    { cx: 50, cy: 24, w: 26, h: 78, rot: 12 },
  ] },
  { id: 'polaroid-rows', family: 'polaroid', label: 'Two rows', hint: 'Two overlapping rows of tilted polaroids', slots: [
    { cx: 22, cy: 32, w: 30, h: 78, rot: -7 }, { cx: 50, cy: 28, w: 30, h: 78, rot: 5 }, { cx: 78, cy: 34, w: 30, h: 78, rot: -4 },
    { cx: 28, cy: 70, w: 30, h: 78, rot: 6 }, { cx: 56, cy: 74, w: 30, h: 78, rot: -8 }, { cx: 82, cy: 68, w: 30, h: 78, rot: 3 },
  ] },
  { id: 'polaroid-stairs', family: 'polaroid', label: 'Staircase', hint: 'A stepped flight of overlapping frames', slots: [
    { cx: 20, cy: 70, w: 30, h: 78, rot: -6 }, { cx: 36, cy: 56, w: 30, h: 78, rot: 4 },
    { cx: 52, cy: 42, w: 30, h: 78, rot: -3 }, { cx: 68, cy: 28, w: 30, h: 78, rot: 7 },
    { cx: 82, cy: 18, w: 26, h: 78, rot: -10 },
  ] },
  { id: 'polaroid-heart', family: 'polaroid', label: 'Heart', hint: 'A loose heart-shaped cluster', slots: [
    { cx: 32, cy: 32, w: 28, h: 78, rot: -14 }, { cx: 68, cy: 32, w: 28, h: 78, rot: 14 },
    { cx: 22, cy: 52, w: 26, h: 78, rot: -8 }, { cx: 78, cy: 52, w: 26, h: 78, rot: 8 },
    { cx: 50, cy: 48, w: 30, h: 78, rot: 2 }, { cx: 50, cy: 76, w: 28, h: 78, rot: -3 },
  ] },
  { id: 'polaroid-strip', family: 'polaroid', label: 'Strip', hint: 'A band of overlapping frames across the middle', slots: [
    { cx: 16, cy: 50, w: 28, h: 78, rot: -6 }, { cx: 34, cy: 46, w: 28, h: 78, rot: 5 },
    { cx: 52, cy: 52, w: 28, h: 78, rot: -4 }, { cx: 70, cy: 47, w: 28, h: 78, rot: 7 },
    { cx: 86, cy: 53, w: 26, h: 78, rot: -5 },
  ] },
  { id: 'polaroid-corners', family: 'polaroid', label: 'Corners', hint: 'Four polaroids pinning the corners, one in the middle', slots: [
    { cx: 20, cy: 22, w: 30, h: 78, rot: -10 }, { cx: 80, cy: 22, w: 30, h: 78, rot: 9 },
    { cx: 20, cy: 78, w: 30, h: 78, rot: 7 }, { cx: 80, cy: 78, w: 30, h: 78, rot: -8 },
    { cx: 50, cy: 50, w: 36, h: 78, rot: 3 },
  ] },
]

export function collageTemplate (id: string | undefined): CollageTemplate {
  return COLLAGE_TEMPLATES.find(t => t.id === id) || COLLAGE_TEMPLATES[0]
}

function templateSlots (id: string | undefined, n: number): TemplateSlot[] {
  const slots = collageTemplate(id).slots
  const out: TemplateSlot[] = []
  for (let i = 0; i < n; i++) {
    if (i < slots.length) { out.push({ ...slots[i] }); continue }
    const base = slots[i % slots.length]
    const k = Math.floor(i / slots.length)
    out.push({
      cx: Math.max(8, Math.min(92, base.cx + (hash01(i, 11) - 0.5) * 10 * k)),
      cy: Math.max(10, Math.min(90, base.cy + (hash01(i, 13) - 0.5) * 10 * k)),
      w: base.w * 0.85,
      ...(base.h !== undefined ? { h: base.h * 0.85 } : {}),
      rot: base.rot + (hash01(i, 17) - 0.5) * 14,
    })
  }
  return out
}

/** Mat height in % of frame HEIGHT as a line of the mat width w (% of frame
 *  width): height = k * w + c. The border is a fixed share of the frame, so
 *  it adds a constant c; the polaroid caption strip (0.205 of the mat width)
 *  stays proportional. Derived from the pixel maths of the stage and the MP4
 *  sprite: photo = (w - 2b) / photoAspect, mat = photo + top/side b + bottom,
 *  with b the border in % of frame width. Twin of mat_line() in
 *  backend/app/collage.py.
 *  When `photoNativeRatio` is supplied and shape is 'native', the mat line
 *  uses that photo's snapped native aspect instead of the global shape. */
function matLineFor (shape: CollageShape, fr: ReturnType<typeof photoFrame>, aspect: number, photoNativeRatio?: number): { k: number, c: number } {
  const b = frameBorderFrac(fr) * 100
  const a = photoAspect(shape, photoNativeRatio)
  const inv = 1 / a
  if (fr.shape === 'polaroid') return { k: (inv + 0.205) * aspect, c: (b - 2 * b * inv) * aspect }
  return { k: inv * aspect, c: (2 * b - 2 * b * inv) * aspect }
}

/** Default frame's mat line (for the global shape, no per-photo override). */
function matLine (shape: CollageShape, fr: ReturnType<typeof photoFrame>, aspect: number): { k: number, c: number } {
  return matLineFor(shape, fr, aspect)
}

/** Widest mat that fits a template slot's box for this photo's frame — the
 *  slot width, or less when the mat would be taller than the slot height. */
function templateSlotWidth (slot: TemplateSlot, spec: CollageSpec, i: number, aspect: number, ratios?: ArrayLike<number | undefined>): number {
  if (slot.h === undefined || !(slot.h > 0)) return slot.w
  const { k, c } = matLineFor(photoShape(spec, i), photoFrame(spec, spec.photos[i]), aspect, ratios?.[i])
  return Math.min(slot.w, (slot.h - c) / k)
}

/** Resting placement of every photo. Deterministic given the spec. */
/** Arrangement slot of every photo (index = photo index, value = slot index).
 *  Stored slots are used when they form a valid permutation; photos with a
 *  missing, duplicate or out-of-range slot take the lowest free slots in
 *  list order, so adding or removing photos never leaves a hole. */
export function arrangementSlots (spec: CollageSpec): number[] {
  const n = spec.photos?.length ?? 0
  const out: number[] = new Array(n).fill(-1)
  const taken = new Set<number>()
  for (let i = 0; i < n; i++) {
    const v = Number(spec.photos[i]?.slot)
    if (Number.isInteger(v) && v >= 0 && v < n && !taken.has(v)) { out[i] = v; taken.add(v) }
  }
  let free = 0
  for (let i = 0; i < n; i++) {
    if (out[i] >= 0) continue
    while (taken.has(free)) free++
    out[i] = free; taken.add(free)
  }
  return out
}

export function placements (spec: CollageSpec, aspect: number, ratios?: ArrayLike<number | undefined>): Placement[] {
  const raw = basePlacements(spec, aspect, ratios)
  if (spec.layout === 'free' || spec.photos.length < 2) return raw
  // Move each photo onto its slot's spot. Its own size multiplier stays with
  // the photo, so a big photo moved to a small slot stays big.
  const slots = arrangementSlots(spec)
  if (slots.every((slot, i) => slot === i)) return raw
  return slots.map((slot, i) => {
    const from = raw[slot]
    if (!from) return raw[i]
    const base = from.w / photoSize(spec, slot)
    return { cx: from.cx, cy: from.cy, rot: from.rot, w: base * photoSize(spec, i) }
  })
}

/** Mat height (% of frame HEIGHT) for photo i given its mat width w (% of frame
 *  width). Uses the photo's own shape (override or the collage default); a
 *  'native' shape uses the photo's measured aspect. */
export function photoMatHeight (spec: CollageSpec, i: number, w: number, aspect: number, ratios?: ArrayLike<number | undefined>): number {
  const fr = photoFrame(spec, spec.photos[i])
  const { k, c } = matLineFor(photoShape(spec, i), fr, aspect, ratios?.[i])
  return k * w + c
}

function basePlacements (spec: CollageSpec, aspect: number, ratios?: ArrayLike<number | undefined>): Placement[] {
  const n = Math.max(1, spec.photos.length)
  const seed = Math.trunc(spec.seed) || 1
  const out: Placement[] = []
  const cells = (count: number): { cols: number; rows: number; cellW: number; cellH: number } => {
    const cols = Math.max(1, Math.ceil(Math.sqrt(count)))
    const rows = Math.ceil(count / cols)
    const marginX = 7
    const marginY = 8
    return { cols, rows, cellW: (100 - 2 * marginX) / cols, cellH: (100 - 2 * marginY) / rows, }
  }
  // Mat height (% of frame HEIGHT) = k * w + c for the collage's default
  // frame — the pixel maths of the stage and the MP4. Every layout that fits
  // mats into cells limits their height with it, so rows never overlap
  // whatever the photo shape or frame style. For 'native' shape we use a
  // 4:3 default for cell sizing (the preview may not have measured every
  // photo yet); per-photo photoMatHeight is used by masonry/photowall where
  // heights differ meaningfully between photos.
  const { k: lineK, c: lineC } = matLine(spec.shape, photoFrame(spec, null), aspect)
  // Largest mat width (as % of frame width) that fits a grid cell: 80 % of
  // the cell width, and no taller than 90 % of the cell height (the seeded
  // tilt needs the rest).
  const fitWidth = (cellW: number, cellH: number): number => Math.min(cellW * 0.8, (cellH * 0.9 - lineC) / lineK)
  // Per-photo size: each photo's mat is the layout width times its multiplier
  // (bigger photos overlap their neighbours — that is the point).
  const widthOf = (base: number, i: number): number => base * photoSize(spec, i)
  // Per-photo mat height helper used by masonry/photowall where photos may
  // legitimately differ in shape.
  const matHof = (w: number, i: number): number => {
    const fr = photoFrame(spec, spec.photos[i])
    const { k, c } = matLineFor(photoShape(spec, i), fr, aspect, ratios?.[i])
    return k * w + c
  }
  if (spec.layout === 'grid') {
    const { cols, cellW, cellH } = cells(n)
    const w = fitWidth(cellW, cellH)
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      // Seeded tilt so Shuffle visibly rearranges even a strict grid.
      out.push({ cx: 7 + cellW * (col + 0.5), cy: 8 + cellH * (row + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 10 })
    }
  } else if (spec.layout === 'scatter') {
    const { cols, cellW, cellH } = cells(n)
    const w = fitWidth(cellW, cellH) * 0.94
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      const cx = 7 + cellW * (col + 0.28 + 0.44 * hash01(seed, i, 29))
      const cy = 8 + cellH * (row + 0.28 + 0.44 * hash01(seed, i, 31))
      out.push({ cx, cy, w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 26 })
    }
  } else if (spec.layout === 'filmstrip') {
    // A horizontal band of overlapping frames, like film frames edge to
    // edge — one row up to 5 photos, two rows beyond.
    const rows = n <= 5 ? 1 : 2
    const perRow = Math.ceil(n / rows)
    const firstRow = n - perRow * (rows - 1)
    for (let i = 0; i < n; i++) {
      const row = i < firstRow ? 0 : 1
      const cols = row === 0 ? firstRow : perRow
      const col = row === 0 ? i : i - firstRow
      const cellWr = (100 - 2 * 5) / cols
      const cellH = (100 - 2 * 12) / rows
      const w = Math.min(cellWr * 1.1, (cellH * 0.9 - lineC) / lineK)
      out.push({ cx: 5 + cellWr * (col + 0.5), cy: 12 + cellH * (row + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 8 })
    }
  } else if (spec.layout === 'fan') {
    // Cards fanned out from a point below the frame — each card tilts
    // along its spoke, like a hand of cards offered to the viewer.
    const L = 46
    const spread = Math.min(48, 10 + 7 * n)
    const a0 = n === 1 ? 0 : (hash01(seed, 0, 19) - 0.5) * 10
    const w = n <= 3 ? 36 : n <= 6 ? 30 : 26
    for (let i = 0; i < n; i++) {
      const a = ((n === 1 ? 0 : (spread * (i / (n - 1) - 0.5))) + a0) * Math.PI / 180
      out.push({
        cx: 50 + L * aspect * Math.sin(a),
        cy: 96 - L * Math.cos(a),
        w: widthOf(w, i),
        rot: a * 180 / Math.PI,
      })
    }
  } else if (spec.layout === 'masonry') {
    // Pinterest-style columns: seeded size variety, each photo stacked into
    // the shortest column (a single photo is simply centred). Per-photo
    // heights respect each photo's own aspect when shape === 'native'.
    if (n === 1) {
      const wMax = photoShape(spec, 0) === 'native' ? 48 : Math.min(40, (76 - lineC) / lineK)
      out.push({ cx: 50, cy: 50, w: widthOf(wMax, 0), rot: 0 })
    } else {
      const cols = n <= 2 ? 2 : n <= 9 ? 3 : 4
      const marginX = 6
      const cellW = (100 - 2 * marginX) / cols
      const base = cellW * 0.88
      const ws: number[] = []
      for (let i = 0; i < n; i++) ws.push(base * (0.82 + 0.36 * hash01(seed, i, 41)) * photoSize(spec, i))
      const simulate = (scale: number) => {
        const fills = new Array<number>(cols).fill(0)
        const res: { cx: number; cy: number }[] = []
        const gap = 2.5 * scale
        for (let i = 0; i < n; i++) {
          let c = 0
          for (let j = 1; j < cols; j++) if (fills[j] < fills[c] - 1e-9) c = j
          const mh = matHof(ws[i] * scale, i)
          res.push({ cx: marginX + cellW * (c + 0.5), cy: 12 + fills[c] + gap / 2 + mh / 2 })
          fills[c] += gap + mh
        }
        return { res, maxFill: Math.max(...fills) }
      }
      let sim = simulate(1)
      let fit = 1
      if (sim.maxFill > 76) { fit = 76 / sim.maxFill; sim = simulate(fit) }
      for (let i = 0; i < n; i++) out.push({ cx: sim.res[i].cx, cy: sim.res[i].cy, w: ws[i] * fit, rot: (hash01(seed, i, 37) - 0.5) * 6 })
    }
  } else if (spec.layout === 'honeycomb') {
    const cols = n <= 2 ? n : n <= 6 ? 3 : 4
    const rows = Math.ceil(n / cols)
    const gap = Number.isFinite(Number(spec.gap)) ? Math.max(0, Math.min(12, Number(spec.gap))) : 1.6
    const cellW = (100 - 8) / (cols + 0.5)
    const cellH = (100 - 16) / Math.max(1, rows)
    // Brick-tight: the gutter between rows equals the gutter between columns.
    const w = Math.min(cellW - gap, (cellH - gap - lineC) / lineK)
    for (let i = 0; i < n; i++) {
      const row = Math.floor(i / cols)
      const col = i % cols
      const ox = (row % 2) * cellW * 0.5
      out.push({ cx: 6 + ox + cellW * (col + 0.5), cy: 10 + cellH * (row + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 4 })
    }
  } else if (spec.layout === 'zigzag') {
    const cols = Math.max(1, Math.ceil(Math.sqrt(n)))
    const rows = Math.ceil(n / cols)
    const cellW = (100 - 12) / cols
    const cellH = (100 - 18) / rows
    const w = fitWidth(cellW, cellH) * 0.92
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      // Alternate rows shift a little left / right of centre — symmetric, so
      // the outer tiles of both rows stay inside the frame.
      const ox = ((row % 2) ? 0.14 : -0.14) * cellW
      out.push({ cx: 6 + ox + cellW * (col + 0.5), cy: 10 + cellH * (row + 0.5), w: widthOf(w, i), rot: ((row + col) % 2 ? 1 : -1) * (6 + 4 * hash01(seed, i, 37)) })
    }
  } else if (spec.layout === 'arc') {
    const w = n <= 4 ? 28 : 22
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : i / (n - 1)
      const a = (12 + 156 * t) * Math.PI / 180
      out.push({
        cx: 50 + 40 * Math.cos(a),
        cy: 62 - 34 * Math.sin(a),
        w: widthOf(w, i),
        rot: 90 - a * 180 / Math.PI,
      })
    }
  } else if (spec.layout === 'photowall') {
    // A compact block of equal tiles with a small gutter. The column count
    // is the one that gives the largest tile for this frame and mat shape
    // (ties: the fewest empty cells); the block is centred and a short last
    // row is centred too, so the wall never has a hole. For 'native' shape
    // we size every tile by the TALLEST photo so even portrait mats fit in
    // every cell.
    const gap = Number.isFinite(Number(spec.gap)) ? Math.max(0, Math.min(12, Number(spec.gap))) : 0.7
    // Per-photo mat-height for a given tile width w (uses ratios when known);
    // the default frame's line is used when ratios are missing.
    const tileH = (w: number): number => {
      let h = 0
      for (let i = 0; i < n; i++) h = Math.max(h, matHof(w, i))
      return h
    }
    let best = { cols: 1, rows: n, w: 0, empty: 0 }
    for (let cols = 1; cols <= n; cols++) {
      const rows = Math.ceil(n / cols)
      const wAvail = (100 - gap * (cols + 1)) / cols
      // Find the largest w <= wAvail such that tileH(w) fits the row height.
      // For a non-native shape matH is linear (k*w+c); for native the tallest
      // photo's (k,c) is also linear so binary search works fine.
      let lo = 2, hi = Math.max(lo, wAvail)
      for (let _ = 0; _ < 12; _++) {
        const mid = (lo + hi) / 2
        const mh = tileH(mid)
        if (mh * rows + gap * (rows + 1) <= 100) lo = mid
        else hi = mid
      }
      const w = lo
      const empty = cols * rows - n
      if (w > best.w + 1e-9 || (Math.abs(w - best.w) <= 1e-9 && empty < best.empty)) best = { cols, rows, w, empty }
    }
    const { cols, rows } = best
    const w = Math.max(2, best.w)
    const matH = tileH(w)
    const y0 = (100 - (rows * matH + (rows - 1) * gap)) / 2
    for (let i = 0; i < n; i++) {
      const col = i % cols
      const row = Math.floor(i / cols)
      const inRow = row === rows - 1 ? n - cols * (rows - 1) : cols
      const x0 = (100 - (inRow * w + (inRow - 1) * gap)) / 2
      out.push({ cx: x0 + w / 2 + col * (w + gap), cy: y0 + matH / 2 + row * (matH + gap), w: widthOf(w, i), rot: 0 })
    }
  } else if (spec.layout === 'booth') {
    const w = Math.min(22, 90 / Math.max(1, n))
    for (let i = 0; i < n; i++) {
      out.push({ cx: 50, cy: 14 + (72 / Math.max(1, n)) * (i + 0.5), w: widthOf(w, i), rot: (hash01(seed, i, 37) - 0.5) * 2 })
    }
  } else if (spec.layout === 'silhouette') {
    // Photos packed along a heart curve (FigrCollage / Canva silhouette).
    const w = n <= 4 ? 22 : n <= 8 ? 16 : 12
    for (let i = 0; i < n; i++) {
      const t = (i / Math.max(1, n)) * 2 * Math.PI
      const hx = 16 * Math.pow(Math.sin(t), 3)
      const hy = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)
      out.push({
        cx: 50 + hx * 2.1 + (hash01(seed, i, 29) - 0.5) * 3,
        cy: 48 - hy * 1.7 + (hash01(seed, i, 31) - 0.5) * 3,
        w: widthOf(w, i),
        rot: (hash01(seed, i, 37) - 0.5) * 14,
      })
    }
  } else if (spec.layout === 'cube') {
    // Three visible isometric faces, leftover photos as a small row underneath.
    const faces = [
      { cx: 48, cy: 48, w: 34, rot: 0 },
      { cx: 70, cy: 44, w: 20, rot: 8 },
      { cx: 48, cy: 28, w: 28, rot: -6 },
    ]
    for (let i = 0; i < n; i++) {
      if (i < 3) out.push({ cx: faces[i].cx, cy: faces[i].cy, w: widthOf(faces[i].w, i), rot: faces[i].rot })
      else {
        const k = i - 3
        const m = n - 3
        out.push({ cx: 18 + (64 / Math.max(1, m)) * (k + 0.5), cy: 84, w: widthOf(16, i), rot: (hash01(seed, i, 37) - 0.5) * 6 })
      }
    }
  } else if (spec.layout === 'free') {
    const w = n <= 3 ? 42 : n <= 6 ? 34 : 30
    for (let i = 0; i < n; i++) {
      const p = spec.photos[i] || { path: '' }
      const cx = num(p.cx) ?? 50
      const cy = num(p.cy) ?? 50
      const rot = num(p.rot) ?? 0
      const own = num(p.w)
      const base = own !== undefined && own > 0 ? Math.max(FREE_MIN_W, Math.min(FREE_MAX_W, own)) : w
      out.push({
        cx: Math.max(0, Math.min(100, cx)),
        cy: Math.max(0, Math.min(100, cy)),
        w: widthOf(base, i),
        rot,
      })
    }
  } else if (spec.layout === 'template') {
    // Each mat is scaled down to fit its slot's box (width and height), so
    // the mosaic keeps its rows and columns whatever the photo shape or
    // frame style; the per-photo size multiplier still applies on top.
    const slots = templateSlots(spec.template, n)
    for (let i = 0; i < n; i++) out.push({ cx: slots[i].cx, cy: slots[i].cy, w: widthOf(templateSlotWidth(slots[i], spec, i, aspect, ratios), i), rot: slots[i].rot })
  } else {
    // stack: overlapping polaroids around the middle, seeded tilts
    const w = n <= 3 ? 42 : n <= 6 ? 34 : 30
    for (let i = 0; i < n; i++) {
      if (i === 0) { out.push({ cx: 50, cy: 46, w: widthOf(w, i), rot: (hash01(seed, i, 17) - 0.5) * 22 }); continue }
      const ang = hash01(seed, i, 11) * 2 * Math.PI
      const r = 5 + hash01(seed, i, 13) * 8
      out.push({
        cx: 50 + Math.cos(ang) * r * 0.9,
        cy: 46 + Math.sin(ang) * r / aspect,
        w: widthOf(w, i),
        rot: (hash01(seed, i, 17) - 0.5) * 22,
      })
    }
  }
  return out
}

/** Auto-stagger default for photo i's delay (seconds). Photo 0 appears 0.15 s
 *  after the hold starts; the rest spread over at most ~2.4 s, capped at
 *  0.32 s apart. These are the values an untouched collage uses. */
export function defaultDelay (n: number, i: number): number {
  if (i <= 0) return 0.15
  return n > 1 ? Math.min(0.32, 2.4 / (n - 1)) : 0
}

/** Photo i's delay in seconds — the stored value if present, the auto default
 *  otherwise. Clamped to 0..30. */
export function photoDelay (spec: CollageSpec, i: number): number {
  const photos = spec.photos ?? []
  const raw = photos[i]?.delay as number | string | boolean | undefined | null
  let d = NaN
  if (raw !== undefined && raw !== null && typeof raw !== 'boolean' && !(typeof raw === 'string' && raw.trim() === '')) d = Number(raw)
  if (!Number.isFinite(d)) d = defaultDelay(photos.length, i)
  return Math.max(0, Math.min(30, d))
}

/** Bounds for a photo's own Free-layout width (`CollagePhoto.w`). */
export const FREE_MIN_W = 4
export const FREE_MAX_W = 100

/** Centre reached by dragging a Free-layout mat: preserve the grab offset by
 *  applying pointer movement as a percentage of the stage dimensions. */
export function freeDragCenter (cx: number, cy: number, dx: number, dy: number, stageWidth: number, stageHeight: number): { cx: number; cy: number } {
  const px = stageWidth > 0 ? dx / stageWidth * 100 : 0
  const py = stageHeight > 0 ? dy / stageHeight * 100 : 0
  return {
    cx: Math.max(0, Math.min(100, cx + px)),
    cy: Math.max(0, Math.min(100, cy + py)),
  }
}

/** A tilt in degrees folded into (-180, 180] and rounded, so a dragged turn
 *  stores one tidy number that CSS and FFmpeg's rotate both read the same way
 *  (clockwise, y growing downwards). */
export function normalizeTilt (degrees: number, decimals = 1): number {
  const raw = Number(degrees)
  if (!Number.isFinite(raw)) return 0
  let d = ((raw + 180) % 360 + 360) % 360 - 180
  if (d <= -180) d = 180
  const f = Math.pow(10, Math.max(0, Math.min(6, Math.trunc(decimals))))
  const out = Math.round(d * f) / f
  return out === -180 ? 180 : out
}

/** A photo's stored Free tilt in degrees (0 when it has none, or junk). */
export function photoTilt (photo: CollagePhoto | null | undefined): number {
  const raw = photo?.rot as number | string | boolean | undefined | null
  if (raw === undefined || raw === null || typeof raw === 'boolean') return 0
  if (typeof raw === 'string' && raw.trim() === '') return 0
  return normalizeTilt(Number(raw))
}

/** Turn one photo in a Free arrangement (degrees, clockwise). 0 straightens it
 *  and drops the stored value, so an untouched photo keeps its original spec. */
export function setPhotoRot (photos: CollagePhoto[], index: number, degrees: number): CollagePhoto[] {
  const value = normalizeTilt(degrees)
  return photos.map((photo, i) => {
    if (i !== index) return photo
    const { rot, ...rest } = photo
    void rot
    return value === 0 ? rest : { ...rest, rot: value }
  })
}

/** Angle (degrees) of a pointer at (px, py) around a pivot (ox, oy) in client
 *  coordinates — the maths behind the preview's rotate handle. Screen y grows
 *  downwards, so a clockwise turn on screen is a positive angle, matching CSS. */
export function pointerAngle (ox: number, oy: number, px: number, py: number): number {
  return Math.atan2(py - oy, px - ox) * 180 / Math.PI
}

/** Tilt reached by a rotate drag: the tilt at grab time plus the angle the
 *  pointer travelled around the mat's pivot. A positive `snap` (Shift) rounds
 *  the result to whole steps of that many degrees. */
export function rotateDragTilt (startTilt: number, startAngle: number, angle: number, snap = 0): number {
  const step = Number.isFinite(Number(snap)) && Number(snap) > 0 ? Number(snap) : 0
  const next = startTilt + (angle - startAngle)
  return normalizeTilt(step ? Math.round(next / step) * step : next)
}

/** Signed distance of the preview's rotate knob from a mat's pivot, along the
 *  mat's own "above the top edge" direction after its turn (`upY` is that
 *  direction's y component, so a photo turned 180° lifts downwards on screen).
 *  `band` is the range of camera-local y the frame actually shows — the camera
 *  window — and `half` the knob's own half height in the same units. A knob
 *  that the frame would clip jumps to the other side of the mat; when neither
 *  side is on screen (a photo hanging over both edges) it sits on the pivot, so
 *  it stays visible and reachable whatever the arrangement. */
export function rotateKnobLift (gap: number, pivotY: number, upY: number, band: [number, number], half: number): number {
  const reach = Math.max(0, Number(gap) || 0)
  const fits = (lift: number) => {
    const y = pivotY + upY * lift
    return y - half >= band[0] && y + half <= band[1]
  }
  if (fits(reach)) return reach
  if (fits(-reach)) return -reach
  return 0
}

/** Where the selection ends up when the photo strip moves a photo from one
 *  index to another (drag to reorder, or the ‹ › buttons): it follows the photo
 *  it was on instead of silently pointing at whichever neighbour slid into that
 *  place. `selected` is null when nothing is selected. */
export function selectionAfterMove (selected: number | null, from: number, to: number): number | null {
  if (selected === null) return null
  if (from === to) return selected
  if (selected === from) return to
  const lo = Math.min(from, to)
  const hi = Math.max(from, to)
  if (selected < lo || selected > hi) return selected
  return from < to ? selected - 1 : selected + 1
}

/** Where the selection ends up when a photo leaves the collage: the selected
 *  photo itself deselects (its size and tilt controls would be about a photo
 *  that is gone), a later one keeps being the same photo. */
export function selectionAfterRemove (selected: number | null, removed: number): number | null {
  if (selected === null) return null
  if (selected === removed) return null
  return selected > removed ? selected - 1 : selected
}

/** Photos of a Free arrangement seeded from what another arrangement shows:
 *  each photo keeps its displayed centre, tilt and mat size (the size is
 *  stored as the photo's own width before its size multiplier, so the
 *  multiplier keeps working). `keepStored` prefers a photo's previously
 *  stored free position/size over the displayed one (the "Free" choice in
 *  the picker restores a manual layout that way); a drop onto the preview
 *  wants exactly what is on screen and passes false. */
export function freeFromDisplayed (spec: CollageSpec, aspect: number, keepStored: boolean, ratios?: ArrayLike<number | undefined>): CollagePhoto[] {
  if (spec.layout === 'free') return spec.photos
  const pls = placements(spec, aspect, ratios)
  return spec.photos.map((p, i) => {
    const pl = pls[i]
    const shown = pl ? { cx: pl.cx, cy: pl.cy, rot: pl.rot, w: pl.w / photoSize(spec, i) } : { cx: 50, cy: 50, rot: 0, w: undefined }
    return keepStored
      ? { ...p, cx: p.cx ?? shown.cx, cy: p.cy ?? shown.cy, rot: p.rot ?? shown.rot, w: p.w ?? shown.w }
      : { ...p, ...shown }
  })
}

/** Photo i's mat size multiplier — the stored value if present (clamped to
 *  0.5..3). When randomSize is on and this photo has no explicit size,
 *  a seeded value between randomSizeMin and randomSizeMax is used. */
export function photoSize (spec: CollageSpec, i: number): number {
  const raw = (spec.photos ?? [])[i]?.size as number | string | boolean | undefined | null
  let v = NaN
  if (raw !== undefined && raw !== null && typeof raw !== 'boolean' && !(typeof raw === 'string' && raw.trim() === '')) v = Number(raw)
  if (!Number.isFinite(v) && spec.randomSize === true) {
    const lo = Math.max(0.5, Math.min(1.5, Number.isFinite(Number(spec.randomSizeMin)) ? Number(spec.randomSizeMin) : 0.7))
    const hi = Math.max(0.5, Math.min(1.5, Number.isFinite(Number(spec.randomSizeMax)) ? Number(spec.randomSizeMax) : 1.3))
    const a = Math.min(lo, hi)
    const b = Math.max(lo, hi)
    v = a + (b - a) * hash01(Math.trunc(spec.seed) || 1, i, 41)
  }
  if (!Number.isFinite(v)) v = 1
  return Math.max(0.5, Math.min(3, v))
}

const clampPhotoSize = (value: number): number => Number.isFinite(value) ? Math.max(0.5, Math.min(3, value)) : 1

/** Change one photo's size without materialising defaults for its neighbours.
 *  In random-size mode, untouched photos therefore stay random. */
export function setPhotoSize (photos: CollagePhoto[], index: number, size: number): CollagePhoto[] {
  const value = clampPhotoSize(size)
  return photos.map((photo, i) => i === index ? { ...photo, size: value } : photo)
}

/** Scale the whole collage toward a target average size while keeping the
 *  current relative sizes (including seeded random sizes). The editor stores
 *  the result on each photo and turns random mode off, so the slider has a
 *  stable, visible effect until the user asks for a fresh random arrangement. */
export function scaleAllPhotoSizes (spec: CollageSpec, targetAverage: number): CollagePhoto[] {
  const photos = spec.photos ?? []
  if (!photos.length) return []
  const target = clampPhotoSize(targetAverage)
  const sizes = photos.map((_, i) => photoSize(spec, i))
  const average = sizes.reduce((sum, value) => sum + value, 0) / sizes.length || 1
  const ratio = target / average
  return photos.map((photo, i) => ({
    ...photo,
    size: Math.round(clampPhotoSize(sizes[i] * ratio) * 1000) / 1000,
  }))
}

/** The next stored beat at or after time t (in the hold clock), or null when
 *  the beat list runs out first. */
export function nextBeat (beats: number[], t: number): number | null {
  for (const b of beats) if (b >= t - 1e-9) return b
  return null
}

/** When photo i starts its entrance (seconds from segment start): the sum of
 *  the delays of photos 0..i plus the lead-in handle. With beat sync on, the
 *  nominal time rolls forward to the next stored beat — several photos may
 *  share a beat (the "pile lands on the drop" reveal). */
export function photoStart (spec: CollageSpec, i: number, leadIn = 0): number {
  let hold = 0
  for (let k = 0; k <= i; k++) hold += photoDelay(spec, k)
  if (spec.beatSync === true) {
    const beats = (Array.isArray(spec.beats) ? spec.beats : []).filter(b => typeof b === 'number' && Number.isFinite(b))
    const snapped = nextBeat(beats, hold)
    if (snapped !== null) hold = snapped
  }
  return leadIn + hold
}

/** How long an entrance takes from its start until the photo is fully at
 *  rest (drop 0.55 s fall, pop 0.5 s spring, swing ~2 s until the pendulum
 *  has visibly settled, none 0 — photos appear with the slide). */
export const ENTRANCE_LENGTH: Record<CollageAnim, number> = {
  drop: 0.55, pop: 0.5, swing: 2.0, flip: 0.45, none: 0,
  fade: 0.45, slide: 0.5, rise: 0.5, tumble: 0.6, zoom: 0.55,
  fold: 0.55, glitch: 0.5, ink: 0.55, brush: 0.5,
  // The mask reveals take their lengths from the same table the render's twin
  // reads, so a reveal cannot be a different length in the preview than in the
  // MP4 — and adding a kind there cannot leave one missing here.
  ...Object.fromEntries(
    (Object.keys(COLLAGE_GEOM) as CollageGeomAnim[]).map(id => [id, COLLAGE_GEOM[id].length])),
  ...COLLAGE_REVEAL_LENGTH,
} as Record<CollageAnim, number>

/** One entrance's alpha ramp in seconds, at speed 1. Both engines read this
 *  number — the preview as the divisor of a ramp, the export as `fade=t=in:` —
 *  so a photo cannot be a different opacity in the MP4 than in the editor at the
 *  same frame. `fade` and `ink` are the two whose preview alpha is a curve
 *  rather than a ramp; the export keeps the ramp, which is close on purpose and
 *  has always been. */
export const ENTRANCE_FADE: Record<CollageAnim, number> = {
  drop: 0.22, pop: 0.18, swing: 0.15, flip: 0.15, none: 0, fade: 0.45,
  slide: 0.18, rise: 0.18, tumble: 0.16, zoom: 0.16, fold: 0.14, glitch: 0.12,
  ink: 0.4, brush: 0.16,
  ...Object.fromEntries((Object.keys(COLLAGE_GEOM) as CollageGeomAnim[])
    .map(id => [id, COLLAGE_GEOM[id].fade])),
  ...Object.fromEntries(COLLAGE_REVEALS.map(id => [id, 0])),
} as Record<CollageAnim, number>

/** Bounds of the entrance speed multiplier: the range the exits already use, so
 *  one slider grammar means the same thing for arriving and for leaving. */
export const ENTRANCE_SPEED_MIN = 0.05
export const ENTRANCE_SPEED_MAX = 2

/** The speed multiplier on photo i's entrance — its own if it has one, the
 *  collage's otherwise, 1 when either is missing or malformed (twin of
 *  `anim_speed`). */
export function animSpeed (spec: CollageSpec, i?: number): number {
  const raw = i === undefined ? spec.animSpeed
    : (i < (spec.photos?.length ?? 0) ? spec.photos?.[i]?.animSpeed : undefined) ?? spec.animSpeed
  const v = Number(raw)
  return Number.isFinite(v) && v > 0 ? Math.max(ENTRANCE_SPEED_MIN, Math.min(ENTRANCE_SPEED_MAX, v)) : 1
}

/** Seconds photo i's entrance takes at its speed. EVERY length in the engine is
 *  read through here: a speed that scaled the sweep but not the alpha ramp, the
 *  pendulum or the size ladder would produce a photo that arrives in one shot
 *  and settles in another. */
export function entranceLength (spec: CollageSpec, i: number): number {
  return (ENTRANCE_LENGTH[photoAnim(spec, i)] ?? 0) / animSpeed(spec, i)
}

/** …and the same for the alpha ramp, so the fade keeps pace with the motion. */
export function entranceFade (spec: CollageSpec, i: number): number {
  return (ENTRANCE_FADE[photoAnim(spec, i)] ?? 0) / animSpeed(spec, i)
}

// ---------------------------------------------------------------------------
// Exits - how the photos leave after the hold
// ---------------------------------------------------------------------------

/** How long one photo's exit fly takes. */
export const EXIT_LENGTH: Record<Exclude<CollageExit, 'none'>, number> = { sweep: 0.45, deal: 0.32, shuffle: 0.4, fade: 0.5 }
/** Delay between consecutive photos leaving. */
export const EXIT_STAGGER: Record<Exclude<CollageExit, 'none'>, number> = { sweep: 0.05, deal: 0.22, shuffle: 0.1, fade: 0.08 }

/** Every exit mode, 'none' (stay until the transition) first. */
export const COLLAGE_EXITS: CollageExit[] = ['none', 'sweep', 'deal', 'shuffle', 'fade']
/** The modes that actually leave: everything but 'none'. One list on purpose —
 *  `exitMode` and `normalizeCollage` used to keep their own, and when 'fade'
 *  was added to the chips and to `exitMode` but not to the one inside
 *  `normalizeCollage`, a whole-collage "Fade out" was dropped on the next load:
 *  the setting vanished, and with it the seconds the exit added to the slide. */
export const COLLAGE_EXIT_MODES: readonly CollageExit[] = COLLAGE_EXITS.filter(e => e !== 'none')

/** Bounds of the exit speed multiplier (the editor slider uses the same range). */
export const EXIT_SPEED_MIN = 0.05
export const EXIT_SPEED_MAX = 2

/** Which way a photo's reveal runs: 0 is the direction its kind is named for,
 *  1 mirrors it on the axis that kind declares. Per photo, inheriting the
 *  collage's own setting — a mosaic that all opens inward is a look, and being
 *  able to say it once is why the setting exists on both levels. */
export function photoRevealDir (spec: CollageSpec, i?: number): 0 | 1 {
  if (i !== undefined) {
    const own = spec.photos?.[i]?.animDir
    if (own === 0 || own === 1) return own
  }
  return spec.animDir === 1 ? 1 : 0
}

/** The exit speed multiplier, clamped; 1 when missing or malformed. */
export function exitSpeed (spec: CollageSpec): number {
  const v = Number(spec.exitSpeed)
  return Number.isFinite(v) && v > 0 ? Math.max(EXIT_SPEED_MIN, Math.min(EXIT_SPEED_MAX, v)) : 1
}

/** One photo's fly-out length, in seconds, at the spec's exit speed. Pass i
 *  for a photo that leaves on its own; omit it for the collage's setting. */
export function exitFlyLength (spec: CollageSpec, i?: number): number {
  const mode = i === undefined ? exitMode(spec) : exitMode(spec, i)
  return mode === 'none' ? 0 : EXIT_LENGTH[mode] / exitSpeed(spec)
}

/** The exit mode: the collage's setting, or photo i's own when i is given. A
 *  missing or unknown override inherits; 'none' has to be written explicitly
 *  to keep one photo on screen while the others leave. */
export function exitMode (spec: CollageSpec, i?: number): CollageExit {
  if (i !== undefined) {
    const own = spec.photos?.[i]?.exit as CollageExit | undefined
    if (own && COLLAGE_EXITS.includes(own)) return own
  }
  return COLLAGE_EXIT_MODES.includes(spec.exit as CollageExit) ? spec.exit as CollageExit : 'none'
}

/** The mode that sets the ORDER of the exit: the collage's own exit when it
 *  has one, otherwise the first photo that leaves on its own. A mixed exit
 *  still needs one shared schedule, or the photos would leave in an order
 *  nobody chose. */
export function exitScheduleMode (spec: CollageSpec): CollageExit {
  const own = exitMode(spec)
  if (own !== 'none') return own
  const photos = spec.photos ?? []
  for (let i = 0; i < photos.length; i++) {
    const m = exitMode(spec, i)
    if (m !== 'none') return m
  }
  return 'none'
}

/** When photo i starts leaving, relative to the end of the hold. 'deal'
 *  clears the top of the pile first (photo n-1 leaves first); the others
 *  go in story order. `exitReverse` flips the order so the last/top photo
 *  leaves first in every mode. */
export function exitOffset (spec: CollageSpec, i: number): number {
  if (exitMode(spec, i) === 'none') return 0
  const mode = exitScheduleMode(spec)
  if (mode === 'none') return 0
  const n = spec.photos?.length ?? 0
  const speed = exitSpeed(spec)
  const rev = spec.exitReverse === true
  if (mode === 'deal') return EXIT_STAGGER.deal / speed * Math.max(0, rev ? i : n - 1 - i)
  return EXIT_STAGGER[mode] / speed * (rev ? Math.max(0, n - 1 - i) : i)
}

/** Total seconds the exit adds to the slide: when the last photo to leave is
 *  gone — its own offset plus its own fly. */
export function exitTotal (spec: CollageSpec): number {
  const n = spec.photos?.length ?? 0
  if (!n) return 0
  let worst = 0
  for (let i = 0; i < n; i++) {
    if (exitMode(spec, i) === 'none') continue
    worst = Math.max(worst, exitOffset(spec, i) + exitFlyLength(spec, i))
  }
  return worst
}

const round2 = (v: number) => Math.round(v * 100) / 100

/** The slide duration before any exit: the last photo's start + its
 *  entrance + the hold after it ('none' photos are all on screen from the
 *  first frame, so just the hold). 0 for an empty collage. */
export function baseDuration (spec: CollageSpec): number {
  const photos = spec.photos ?? []
  if (!photos.length) return 0
  const h = Number(spec.hold)
  const hold = Number.isFinite(h) ? Math.max(0, Math.min(120, h)) : 2
  // With per-photo entrances the slide has to stay up until the LAST photo
  // has finished arriving — which is not necessarily the last one to start.
  // A collage whose photos all just appear is on screen from frame one, so it
  // is only the hold (the stagger delays are not waiting time then).
  let last = 0
  let animated = false
  for (let i = 0; i < photos.length; i++) {
    const anim = photoAnim(spec, i)
    if (anim !== 'none') animated = true
    last = Math.max(last, photoStart(spec, i, 0) + entranceLength(spec, i))
  }
  if (!animated) return round2(hold)
  return round2(last + hold)
}

/** The slide duration implied by the photo timings: entrances + hold + the
 *  exit fly at the end. This is what the editor writes into the slide. */
export function collageDuration (spec: CollageSpec): number {
  const photos = spec.photos ?? []
  if (!photos.length) return 0
  return round2(baseDuration(spec) + exitTotal(spec))
}

/** Background blur: one CSS blur() radius in px on the 1920-wide stage (the
 *  preview draws it at stage scale, so it matches the render). */
export function bgBlurCssPx (blur: number): number {
  return 2 + 58 * clamp01(Number.isFinite(blur) ? blur : 0)
}

/** Background blur: FFmpeg boxblur luma radius for the same strength. */
export function bgBlurRadius (blur: number): number {
  return Math.max(1, Math.round(bgBlurCssPx(blur) / 2))
}

/** Animated state of photo i at segment time t. */
export function photoState (spec: CollageSpec, i: number, t: number, leadIn = 0, aspect = 16 / 9, ratios?: ArrayLike<number | undefined>): PhotoState {
  const t0 = photoStart(spec, i, leadIn)
  const push = pushDepth(spec, i, t, leadIn)
  const anim = photoAnim(spec, i)
  const spd = animSpeed(spec, i)
  const ent = (ENTRANCE_LENGTH[anim] ?? 0) / spd
  const fade = (ENTRANCE_FADE[anim] ?? 0) / spd
  let st: PhotoState
  if (anim === 'drop') {
    const q = clamp01((t - t0) / ent)
    const settle = Math.pow(1 - q, 3)          // 1 → 0, outCubic
    st = { dx: 0, dy: -26 * settle, rot: -7 * settle, scale: 1 - DEPTH.scale * push, scaleX: 1, alpha: clamp01((t - t0) / fade), dim: DEPTH.dim * push }
  } else if (anim === 'pop') {
    const q = clamp01((t - t0) / ent)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: 0, scale: (0.55 + 0.45 * back) * (1 - DEPTH.scale * push), scaleX: 1, alpha: clamp01((t - t0) / fade), dim: DEPTH.dim * push }
  } else if (anim === 'flip') {
    // A card flipping open around its vertical axis: edge-on (4 % width)
    // with an outBack overshoot, straightening as it settles.
    const q = clamp01((t - t0) / ent)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: -4 * (1 - q), scale: 1 - DEPTH.scale * push, scaleX: Math.max(0.04, 0.04 + 0.96 * back), alpha: clamp01((t - t0) / fade), dim: DEPTH.dim * push }
  } else if (anim === 'swing') {
    // The pendulum is the one entrance measured in raw seconds — its decay and
    // period are physical, not a curve over a window — so speed compresses the
    // clock it runs on rather than a divisor.
    const tau = Math.max(0, t - t0) * spd
    // Damped pendulum around the pin: 14° amplitude, ~1.6 s period, settles
    // in a few swings (the "pinned photo gallery" motion).
    const rot = 14 * Math.exp(-1.3 * tau) * Math.cos(2 * Math.PI * tau / 1.6)
    st = { dx: 0, dy: 0, rot, scale: 1, scaleX: 1, alpha: clamp01(tau / 0.15), dim: 0 }
  } else if (anim === 'fade') {
    const q = clamp01((t - t0) / ent)
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: q * q * (3 - 2 * q), dim: 0 }
  } else if (anim === 'slide') {
    const q = clamp01((t - t0) / ent)
    const settle = Math.pow(1 - q, 3)
    st = { dx: -28 * settle, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: clamp01((t - t0) / fade), dim: 0 }
  } else if (anim === 'rise') {
    const q = clamp01((t - t0) / ent)
    const settle = Math.pow(1 - q, 3)
    st = { dx: 0, dy: 22 * settle, rot: 0, scale: 1, scaleX: 1, alpha: clamp01((t - t0) / fade), dim: 0 }
  } else if (anim === 'tumble') {
    const q = clamp01((t - t0) / ent)
    const settle = Math.pow(1 - q, 3)
    st = { dx: 10 * settle, dy: -18 * settle, rot: 28 * settle, scale: 1 - 0.15 * settle, scaleX: 1, alpha: clamp01((t - t0) / fade), dim: 0 }
  } else if (anim === 'zoom') {
    const q = clamp01((t - t0) / ent)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: 0, scale: 0.2 + 0.8 * back, scaleX: 1, alpha: clamp01((t - t0) / fade), dim: 0 }
  } else if (anim === 'fold') {
    const q = clamp01((t - t0) / ent)
    const back = 1 + 2.70158 * Math.pow(q - 1, 3) + 1.70158 * Math.pow(q - 1, 2)
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: Math.max(0.04, 0.04 + 0.96 * back), alpha: clamp01((t - t0) / fade), dim: 0 }
  } else if (anim === 'glitch') {
    const q = clamp01((t - t0) / ent)
    const j = (1 - q) * (1 - q)
    const seed = Math.trunc(Number(spec.seed)) || 1
    st = { dx: (hash01(seed, i, 61) - 0.5) * 10 * j, dy: (hash01(seed, i, 63) - 0.5) * 6 * j, rot: (hash01(seed, i, 65) - 0.5) * 8 * j, scale: 1, scaleX: 1 + (hash01(seed, i, 67) - 0.5) * 0.18 * j, alpha: clamp01((t - t0) / fade), dim: 0.15 * j }
  } else if (anim === 'ink') {
    const q = clamp01((t - t0) / ent)
    const blob = q * q * (3 - 2 * q)
    st = { dx: 0, dy: 0, rot: 0, scale: 0.35 + 0.65 * blob, scaleX: 1, alpha: blob, dim: 0 }
  } else if (anim === 'brush') {
    const q = clamp01((t - t0) / ent)
    const settle = Math.pow(1 - q, 3)
    st = { dx: -36 * settle, dy: 0, rot: 0, scale: 1, scaleX: Math.max(0.12, 1 - 0.55 * settle), alpha: clamp01((t - t0) / fade), dim: 0 }
  } else if (isGeomAnim(anim)) {
    // The paper family, evaluated from one row of `COLLAGE_GEOM` rather than a
    // branch each: the render's twin turns the SAME row into FFmpeg expressions,
    // so a curve cannot mean one motion here and another one in the MP4.
    const q = clamp01((t - t0) / ent)
    const row = COLLAGE_GEOM[anim]
    const v = (c: GeomCurve | undefined, base: number) => geomCurve(c, q, base)
    st = {
      dx: v(row.dx, 0), dy: v(row.dy, 0), rot: v(row.rot, 0),
      scale: v(row.scale, 1) * (1 - DEPTH.scale * push),
      scaleX: v(row.scaleX, 1),
      alpha: clamp01((t - t0) / fade), dim: v(row.dim, 0),
    }
  } else if (isCollageReveal(anim)) {
    // A mask reveal: the photo sits exactly where it will stay and is
    // *uncovered*. Everything about the shape lives in collageReveal.ts, which
    // the render reads too — this branch only says how far the sweep has got.
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: 1, dim: 0, revealKind: anim, revealP: revealProgress(anim, clamp01((t - t0) / ent)), revealDir: photoRevealDir(spec, i) }
  } else {
    // none: the photos are simply part of the slide from the first frame,
    // incoming transition included.
    st = { dx: 0, dy: 0, rot: 0, scale: 1, scaleX: 1, alpha: 1, dim: 0 }
  }
  if (spec.kenBurns === true) {
    const land = t0 + ent
    const kb = clamp01((t - land) / Math.max(0.8, collageDuration(spec) * 0.7))
    const seed = Math.trunc(Number(spec.seed)) || 1
    st.scale *= 1 + 0.08 * kb
    st.dx += (hash01(seed, i, 51) - 0.5) * 5 * kb
    st.dy += (hash01(seed, i, 53) - 0.5) * 3.5 * kb
  }
  if (spec.sway === true && (t - t0) > ent * 0.7) {
    st.rot += 1.5 * Math.sin(t * 2.15 + i * 0.9)
  }
  st = addMotion(spec, i, t, st, t0, ent, leadIn)
  // Exit: after the hold (and every entrance) the photos leave the frame —
  // each one its own way when it has an override.
  const mode = exitMode(spec, i)
  if (mode !== 'none') {
    const seed = Math.trunc(Number(spec.seed)) || 1
    const te0 = leadIn + baseDuration(spec) + exitOffset(spec, i)
    const qe = clamp01((t - te0) / exitFlyLength(spec, i))
    if (qe > 0) {
      const ease = qe * qe                       // accelerating fly
      if (mode === 'fade') {
        // It leaves by fading where it stands: no travel, no turn, and the
        // whole fly is the fade — the movers fade over their last quarter.
        // Linear, because that is what the render's `fade=t=out` does.
      } else if (mode === 'sweep') {
        // Outward through the photo's own anchor direction.
        const pl = placements(spec, aspect, ratios)[i]
        const vx = (pl?.cx ?? 50) - 50
        const vy = (pl?.cy ?? 50) - 50
        const len = Math.hypot(vx, vy)
        const dirx = len < 1e-6 ? 0 : vx / len
        const diry = len < 1e-6 ? -1 : vy / len
        st.dx += 90 * dirx * ease
        st.dy += 90 * diry * ease
        st.rot += (hash01(seed, i, 71) - 0.5) * 20 * qe
      } else if (mode === 'deal') {
        // Dealt off to the right, one card at a time, with a little arc.
        st.dx += 90 * ease
        st.dy += -6 * Math.sin(Math.PI * qe)
        st.rot += 25 * qe
      } else {
        // Scattered off in seeded directions.
        const a = hash01(seed, i, 73) * 2 * Math.PI
        st.dx += 75 * Math.cos(a) * ease
        st.dy += 55 * Math.sin(a) * ease
        st.rot += (hash01(seed, i, 75) - 0.5) * 40 * qe
      }
      st.alpha *= mode === 'fade' ? 1 - qe : 1 - clamp01((qe - 0.75) / 0.25)
    }
  }
  return st
}

/** Depth push-back: how many "pushed back" units photo i has accumulated by
 *  time t — every photo that lands after it pushes it back a little (eased
 *  over DEPTH.span seconds, starting partway through the newcomer's
 *  entrance), capped at DEPTH.max. 0 unless the spec asks for depth and the
 *  animation can show it (drop / pop). */
export function pushDepth (spec: CollageSpec, i: number, t: number, leadIn = 0): number {
  if (spec.depth !== true) return 0
  const anim = spec.animation
  if (anim !== 'drop' && anim !== 'pop' && anim !== 'flip') return 0
  const n = spec.photos?.length ?? 0
  let p = 0
  for (let j = i + 1; j < n; j++) {
    const tj = photoStart(spec, j, leadIn)
    // The push follows the ARRIVING photo's landing, so a photo with its own
    // entrance pushes back on its own clock.
    const E = entranceLength(spec, j)
    const q = clamp01((t - (tj + DEPTH.delay * E)) / DEPTH.span)
    p += q * q * (3 - 2 * q)                   // smoothstep
  }
  return Math.min(DEPTH.max, p)
}

/** Virtual camera over the composed collage at segment time t: zoom factor
 *  z (1 = whole frame) and the window centre (cx / cy in % of the frame).
 *  'pan' drifts across a slightly zoomed frame; the zoom family centres on
 *  the LAST photo's anchor (the top of the pile) and the window is clamped
 *  so it always stays inside the frame — the FFmpeg zoompan chain
 *  (camera_filter in backend/app/collage.py) and the CSS transform are two
 *  views of the same numbers. */
export interface CameraState { z: number; cx: number; cy: number }

export function cameraState (spec: CollageSpec, t: number, leadIn = 0, aspect = 16 / 9, ratios?: ArrayLike<number | undefined>): CameraState {
  const mode = (['pan', 'zoom', 'telescope', 'droste'] as readonly CollageCamera[]).includes(spec.camera as CollageCamera) ? spec.camera as CollageCamera : 'none'
  if (mode === 'none') return { z: 1, cx: 50, cy: 50 }
  const D = Math.max(0.2, collageDuration(spec))
  const raw = clamp01((t - leadIn) / D)
  // Smoothstep the progress so the virtual camera eases in and out — the
  // FFmpeg chain drives zoompan (over a supersampled scene, so it moves on
  // a sub-pixel grid) with this same curve, so preview and MP4 stay in step.
  const p = raw * raw * (3 - 2 * raw)
  if (mode === 'pan') return { z: 1.09, cx: 54 - 8 * p, cy: 50 }
  const pls = placements(spec, aspect, ratios)
  const a = pls.length ? pls[pls.length - 1] : { cx: 50, cy: 50, w: 30, rot: 0 }
  let z = 1
  if (mode === 'zoom') z = 1 + 0.35 * p
  else if (mode === 'telescope') z = 1 + 0.9 * p
  else z = 1 + 1.1 * Math.pow(p, 1.4)
  const half = 50 / z
  return {
    z,
    cx: Math.min(100 - half, Math.max(half, a.cx)),
    cy: Math.min(100 - half, Math.max(half, a.cy)),
  }
}

/** Depth push-back tuning — mirrored as DEPTH in backend/app/collage.py. */
export const DEPTH = { scale: 0.06, dim: 0.11, max: 4, delay: 0.4, span: 0.35 }

/** Map music-track beat times (file-local seconds) into a slide's own hold
 *  clock (0 = the hold starts). The track's kept region [trimStart, trimEnd)
 *  plays at trackStartTimeline on the timeline; the slide's hold starts at
 *  holdStartTimeline. Pure data mapping — the editor runs it and stores the
 *  result as the spec's `beats`, which both engines then use verbatim. */
export function slideLocalBeats (beats: number[], trackStartTimeline: number, trimStart: number, trimEnd: number, holdStartTimeline: number): number[] {
  const out: number[] = []
  for (const b of beats) {
    if (!Number.isFinite(b)) continue
    if (b < trimStart) continue
    if (trimEnd > 0 && b >= trimEnd) continue
    const local = trackStartTimeline + (b - trimStart) - holdStartTimeline
    if (local < 0 || local > 600) continue
    out.push(Math.round(local * 1000) / 1000)
  }
  return out.sort((a, b) => a - b)
}

/** Rotation pivot: swinging photos hang from a pin at the mat's top edge. */
export function pinAnchor (spec: CollageSpec, i?: number): boolean {
  return (i === undefined ? spec.animation : photoAnim(spec, i)) === 'swing'
}

/** The "while" lane: one photo's own motion, added on top of its entrance,
 *  Ken Burns and the collage-wide sway. Everything here is additive and eases
 *  in from the resting pose over MOTION_RAMP, so switching a motion on for one
 *  photo cannot disturb the others. These numbers are the spec both engines
 *  implement: the preview applies them as CSS transforms, the render turns
 *  them into FFmpeg overlay/rotate expressions (motion_terms() in
 *  backend/app/collage.py) — which is why every motion has to be a closed-form
 *  function of t. Position and rotation can be evaluated per frame; scale
 *  cannot, so the scaling motions live in COLLAGE_SCALE_MOTIONS. */
export function addMotion (spec: CollageSpec, i: number, t: number, st: PhotoState, t0?: number, ent?: number, leadIn = 0): PhotoState {
  const id = photoMotion(spec, i).id
  if (id === 'none') return st
  const M = MOTION[id]
  // A motion whose end state the user picked (move, turn) takes its strength
  // from that target, not from the slider.
  const a = COLLAGE_TARGET_MOTIONS.includes(id) ? 1 : photoMotion(spec, i).amount
  const start = t0 === undefined ? photoStart(spec, i, leadIn) : t0
  const e0 = ent === undefined ? entranceLength(spec, i) : ent
  const u = Math.max(0, t - (start + e0))
  const g = clamp01(u / MOTION_RAMP)
  const dur = photoMotionDuration(spec, i)
  const span = dur > 0 ? dur : Math.max(0.8, collageDuration(spec) * 0.7)
  // A motion with a duration of its own stops being interesting when that time
  // is up. One-shot motions hold the end state they travelled to; looping ones
  // settle back to rest over the last part of their window, because freezing
  // mid-bob (or mid-orbit, off-centre) looks broken.
  const settle = dur > 0 && !COLLAGE_ONESHOT_MOTIONS.includes(id) ? Math.min(0.4, dur * 0.3) : 0
  const w = settle > 0 ? 1 - clamp01((u - (dur - settle)) / settle) : 1
  const seed = Math.trunc(Number(spec.seed)) || 1
  const TAU = Math.PI * 2
  const r1 = hash01(seed, i, 81)
  const r2 = hash01(seed, i, 83)
  const r3 = hash01(seed, i, 85)
  const out: PhotoState = { ...st }
  if (id === 'drift') {
    // One-way pan that ramps in across the hold; the seeded direction keeps a
    // grid of photos from sliding as one block.
    const e = clamp01(u / span)
    out.dx += (r1 - 0.5) * 2 * M.dx * a * e * w
    out.dy += (r2 - 0.5) * 2 * M.dy * a * e * w
  } else if (id === 'shake') {
    // A jolt as it lands, dying away — attention without fidgeting.
    const e = Math.exp(-M.decay * u) * g
    out.dx += M.dx * a * Math.sin(u * M.speed + r1 * TAU) * e * w
    out.dy += M.dy * a * Math.sin(u * M.speed * 0.91 + r2 * TAU) * e * w
    out.rot += M.rot * a * Math.sin(u * M.speed * 0.85 + r3 * TAU) * e * w
  } else if (id === 'sway') {
    // The collage-wide pendulum, but for one photo at its own strength.
    // Absolute clock, so photos that share it stay in step (i * 0.9 = phase).
    out.rot += M.rot * a * Math.sin(t * M.speed + i * 0.9) * g * w
  } else if (id === 'orbit') {
    // A slow ellipse around the resting spot.
    const wo = u * M.speed + r3 * TAU
    out.dx += M.dx * a * Math.cos(wo) * g * w
    out.dy += M.dy * a * Math.sin(wo) * g * w
  } else if (id === 'breathe') {
    // Ken Burns' zoom for one photo: a slow ramp across the hold. A render can
    // only hold discrete sizes, so this one rides the size ladder.
    out.scale *= 1 + M.scale * a * clamp01(u / span)
  } else if (id === 'pulse') {
    // A rhythmic zoom around the resting size, eased in like the rest.
    out.scale *= 1 + M.scale * a * Math.sin(u * M.speed + r2 * TAU) * g * w
  } else if (id === 'beat') {
    // A punch on every stored beat of the soundtrack: each one swells the
    // photo and lets it fall back. Beat times live in the hold clock (0 = the
    // hold starts), so the photo's own lead-in is taken back off t.
    // Only beats that have already sounded count — a beat still ahead would
    // otherwise swell the photo from the moment it lands — and the punch eases
    // in with the rest of the motion so landing mid-beat cannot pop.
    let punch = 0
    for (const b of beatPunches(spec, start + e0 - leadIn, collageDuration(spec))) {
      const since = t - leadIn - b
      if (since >= 0) punch += Math.exp(-M.decay * since)
    }
    out.scale *= 1 + M.scale * a * punch * g * w
  } else if (id === 'move') {
    // Travels to the end position the user picked and holds it there. Eased,
    // because a linear slide across the frame looks mechanical.
    const e = smoothstep(clamp01(u / span))
    out.dx += photoMove(spec, i).x * e
    out.dy += photoMove(spec, i).y * e
  } else if (id === 'turn') {
    // Turns to the end angle the user picked, from the photo's resting tilt.
    out.rot += photoTurnTo(spec, i) * smoothstep(clamp01(u / span))
  } else if (id === 'spin') {
    // Continuous rotation: it turns for `span` seconds and keeps the angle it
    // reached, which is what "spin for three seconds" means. M.rot is degrees
    // per second here, not an amplitude.
    out.rot += M.rot * a * Math.min(u, span) * g
  } else {
    // float: a bob with a little lean, three unrelated slow frequencies.
    out.dx += M.dx * a * Math.sin(u * M.speed * 0.64 + r1 * TAU) * g * w
    out.dy += M.dy * a * Math.sin(u * M.speed + r3 * TAU) * g * w
    out.rot += M.rot * a * Math.sin(u * M.speed * 0.73 + r2 * TAU) * g * w
  }
  return out
}

/** The beats a 'beat' motion punches on: the stored beat times inside this
 *  photo's own window (from its landing to the end of the slide), thinned to
 *  BEAT_PUNCH_MAX by taking every k-th one so a long, fast track cannot blow
 *  up the expression a render has to carry. Both engines thin identically. */
export function beatPunches (spec: CollageSpec, from: number, to: number): number[] {
  const beats = (Array.isArray(spec.beats) ? spec.beats : []).filter(b => Number.isFinite(b) && b >= from && b <= to)
  if (beats.length <= BEAT_PUNCH_MAX) return beats
  const step = Math.ceil(beats.length / BEAT_PUNCH_MAX)
  return beats.filter((_, k) => k % step === 0)
}

/** The animated look of photo i, sanitised ('none' when it has no own one). */
export function photoLookAnim (spec: CollageSpec, i: number): CollageLookAnim {
  const own = spec.photos?.[i]?.lookAnim as CollageLookAnim | undefined
  return own && COLLAGE_LOOK_ANIMS.includes(own) ? own : 'none'
}

/** How the animated look tints photo i at time t: a saturation multiplier and
 *  a brightness multiplier, 1 = untouched. The preview applies them as CSS
 *  saturate() / brightness() on the mat; the render drives saturation with a
 *  per-frame `hue=s='…'` expression (hue evaluates its expressions every
 *  frame, eq evaluates them once at init — measured on the render binary) and
 *  brightness with a sendcmd ladder into a second eq, the same machinery the
 *  depth dim uses. Each look moves only ONE of the two, so the two engines
 *  cannot disagree about how they compose. */
export function photoLookState (spec: CollageSpec, i: number, t: number, leadIn = 0): { sat: number; bright: number; inv: number; hue: number } {
  const id = photoLookAnim(spec, i)
  if (id === 'none') return { sat: 1, bright: 1, inv: 0, hue: 0 }
  const L = LOOK_ANIM[id]
  const t0 = photoStart(spec, i, leadIn)
  const ent = entranceLength(spec, i)
  const land = t0 + ent
  const plain = { sat: 1, bright: 1, inv: 0, hue: 0 }
  if (id === 'toBw') {
    // colour drains away across the hold, at Ken Burns' pace
    return { ...plain, sat: 1 - L.sat * clamp01((t - land) / Math.max(0.8, collageDuration(spec) * 0.7)) }
  }
  if (id === 'toColor') {
    // it arrives in black & white and blooms into colour during the entrance
    return { ...plain, sat: clamp01((t - t0) / Math.max(0.45, ent)) }
  }
  if (id === 'invert') {
    // It arrives as a negative and flips to its real colours once it has
    // landed. The flip is a hard cut because FFmpeg's `negate` is on or off —
    // there is no per-frame amount — and a photo with no entrance of its own
    // still gets INVERT_MIN seconds of negative to develop out of.
    return { ...plain, inv: t < t0 + Math.max(ent, INVERT_MIN) ? 1 : 0 }
  }
  if (id === 'hueFlip') {
    // The colour wheel turns half way round across the slide, so the photo
    // ends in its opposite colours. `hue=H='…'` re-evaluates per frame.
    return { ...plain, hue: L.hue * clamp01((t - t0) / Math.max(0.8, collageDuration(spec) * 0.7)) }
  }
  // flicker: an old-projector brightness jitter that dies out after landing
  const u = Math.max(0, t - land)
  const seed = Math.trunc(Number(spec.seed)) || 1
  const TAU = Math.PI * 2
  const r1 = hash01(seed, i, 87)
  const r2 = hash01(seed, i, 89)
  const env = Math.exp(-L.decay * u)
  const jitter = 0.6 * Math.sin(u * L.speed + r1 * TAU) + 0.4 * Math.sin(u * L.speed * 0.578 + r2 * TAU)
  return { ...plain, bright: 1 + L.bright * env * jitter }
}

/** The CSS filter the preview puts on a mat: the depth dim first, then the
 *  photo's animated look — and in the order the render's own filters sit in the
 *  chain (eq for the dim, one hue for the colour wheel and the saturation, a
 *  second eq for the brightness, negate for the flip), so a drained, turned or
 *  flickering photo looks the same on screen and in the MP4. CSS applies filters
 *  left to right, which is the same order FFmpeg applies its chain.
 *
 *  The thresholds are the ones the render uses to decide whether a filter is
 *  worth emitting at all, so both sides stay quiet about a photo that is not
 *  being touched. */
export function matFilterCss (dim: number, look: { sat: number; bright: number; inv: number; hue: number }): string {
  const parts: string[] = []
  if (dim > 0.004) parts.push(`brightness(${(1 - dim).toFixed(3)})`)
  if (Math.abs(look.hue) > 0.2) parts.push(`hue-rotate(${look.hue.toFixed(2)}deg)`)
  if (Math.abs(look.sat - 1) > 0.002) parts.push(`saturate(${look.sat.toFixed(3)})`)
  if (Math.abs(look.bright - 1) > 0.002) parts.push(`brightness(${look.bright.toFixed(3)})`)
  if (look.inv > 0.5) parts.push('invert(1)')
  return parts.join(' ')
}

/** The entrance a photo actually performs: its own override when it has one,
 *  the collage's animation otherwise. */
export function photoAnim (spec: CollageSpec, i: number): CollageAnim {
  const own = spec.photos?.[i]?.anim as CollageAnim | undefined
  return own && COLLAGE_ANIMS_ALL.includes(own) ? own : (spec.animation ?? 'drop')
}

/** How long photo i's motion plays, in seconds from its landing; 0 means
 *  "the rest of the slide", which is what every existing project does. */
export function photoMotionDuration (spec: CollageSpec, i: number): number {
  const v = num(spec.photos?.[i]?.motionDuration)
  if (v === undefined || v <= 0) return 0
  return Math.max(MOTION_DURATION_MIN, Math.min(MOTION_DURATION_MAX, v))
}

/** Where photo i's `move` motion travels to, in % of the frame from its
 *  resting spot (the units photoState's dx / dy use). */
export function photoMove (spec: CollageSpec, i: number): { x: number; y: number } {
  const photo = spec.photos?.[i]
  const x = num(photo?.moveX) ?? 0
  const y = num(photo?.moveY) ?? 0
  return {
    x: Math.max(-MOVE_RANGE, Math.min(MOVE_RANGE, x)),
    y: Math.max(-MOVE_RANGE, Math.min(MOVE_RANGE, y)),
  }
}

/** The angle photo i's `turn` motion rotates to, in degrees clockwise from its
 *  resting tilt. */
export function photoTurnTo (spec: CollageSpec, i: number): number {
  const v = num(spec.photos?.[i]?.turnTo) ?? 0
  return Math.max(-TURN_RANGE, Math.min(TURN_RANGE, v))
}

/** Degrees of sprite-canvas headroom photo i's motion needs — the turn a
 *  motion reaches at full amplitude, or for `turn` the photo's own target. */
export function motionSpin (spec: CollageSpec, i: number): number {
  const { id, amount } = photoMotion(spec, i)
  if (id === 'none') return 0
  if (id === 'turn') return Math.min(Math.abs(photoTurnTo(spec, i)), MOTION_SPIN.turn)
  return MOTION_SPIN[id] * amount
}

/** The motion a photo keeps doing while it is on screen, and how strongly. */
export interface PhotoMotion { id: CollageMotion; amount: number }
export function photoMotion (spec: CollageSpec, i: number): PhotoMotion {
  const own = spec.photos?.[i]?.motion as CollageMotion | undefined
  const id = own && COLLAGE_MOTIONS.includes(own) ? own : 'none'
  const v = Number(spec.photos?.[i]?.motionAmount)
  const amount = Number.isFinite(v) && v > 0 ? Math.max(MOTION_AMOUNT_MIN, Math.min(MOTION_AMOUNT_MAX, v)) : 1
  return { id, amount }
}

/** Normalise a stored collage spec: unknown layouts/anims fall back, the
 *  photo list is capped and deduplicated by path, the seed defaults, the
 *  timing/background fields are sanitised. */
/** Strict number: booleans and blank strings are "not set" (the Python twin
 *  reads them the same way), anything numeric-looking is parsed. */
function num (v: unknown): number | undefined {
  if (v === undefined || v === null || typeof v === 'boolean') return undefined
  if (typeof v === 'string' && v.trim() === '') return undefined
  const n = Number(v)
  return Number.isFinite(n) ? n : undefined
}

function frameFields (raw: unknown): CollageFrame | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const f = raw as CollageFrame
  if (!f.shape && f.width === undefined && !f.color && f.radius === undefined && f.shadow === undefined) return undefined
  return photoFrame({ frame: f }, null)
}

function photoLookOf (p: Partial<CollagePhotoLook> | Record<string, unknown>): CollagePhotoLook {
  const out: CollagePhotoLook = {}
  if (typeof p.filter === 'string' && p.filter) out.filter = p.filter
  const amount = num(p.filterAmount)
  if (amount !== undefined) out.filterAmount = clamp01(amount)
  if (p.filterAdjust && typeof p.filterAdjust === 'object') out.filterAdjust = p.filterAdjust as Record<string, number>
  if (p.crop && typeof p.crop === 'object') out.crop = p.crop as CollagePhotoLook['crop']
  return out
}

/** CSS background-size / position for a collage (or text-frame) picture. */
export function bgFitStyle (fit: CollageBgFit | string | undefined): { backgroundSize: string; backgroundPosition: string; backgroundRepeat: string } {
  const f = (['fill', 'fit', 'stretch', 'tile', 'center', 'span'] as const).includes(fit as CollageBgFit) ? fit as CollageBgFit : 'fill'
  if (f === 'fit') return { backgroundSize: 'contain', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  if (f === 'stretch') return { backgroundSize: '100% 100%', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  if (f === 'tile') return { backgroundSize: 'auto', backgroundPosition: '0 0', backgroundRepeat: 'repeat' }
  if (f === 'center') return { backgroundSize: 'auto', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  if (f === 'span') return { backgroundSize: '100% auto', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
  return { backgroundSize: 'cover', backgroundPosition: 'center', backgroundRepeat: 'no-repeat' }
}

export function normalizeCollage (raw: unknown): CollageSpec | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const c = raw as Partial<CollageSpec>
  const photos: CollagePhoto[] = []
  const seen = new Set<string>()
  for (const p of Array.isArray(c.photos) ? c.photos : []) {
    if (!p || typeof p.path !== 'string' || !p.path) continue
    if (seen.has(p.path)) continue
    seen.add(p.path)
    const delay = num(p.delay)
    const size = num(p.size)
    const cx = num(p.cx)
    const cy = num(p.cy)
    const rot = num(p.rot)
    const w = num(p.w)
    const slot = num(p.slot)
    const look = photoLookOf(p)
    photos.push({
      path: p.path,
      name: typeof p.name === 'string' ? p.name : undefined,
      delay: delay !== undefined ? Math.max(0, Math.min(30, delay)) : undefined,
      size: size !== undefined ? Math.max(0.5, Math.min(3, size)) : undefined,
      cx: cx !== undefined ? Math.max(0, Math.min(100, cx)) : undefined,
      cy: cy !== undefined ? Math.max(0, Math.min(100, cy)) : undefined,
      rot: rot !== undefined ? Math.max(-180, Math.min(180, rot)) : undefined,
      w: w !== undefined && w > 0 ? Math.max(FREE_MIN_W, Math.min(FREE_MAX_W, w)) : undefined,
      slot: slot !== undefined && Number.isInteger(slot) && slot >= 0 ? Math.min(MAX_COLLAGE_PHOTOS - 1, slot) : undefined,
      frame: frameFields(p.frame),
      shape: COLLAGE_SHAPES_ALL.includes(p.shape as CollageShape) ? p.shape as CollageShape : undefined,
      ...(COLLAGE_ANIMS_ALL.includes(p.anim as CollageAnim) ? { anim: p.anim as CollageAnim } : {}),
      ...(COLLAGE_EXITS.includes(p.exit as CollageExit) ? { exit: p.exit as CollageExit } : {}),
      ...(p.animDir === 1 ? { animDir: 1 as const } : p.animDir === 0 ? { animDir: 0 as const } : {}),
      ...(Number.isFinite(Number(p.animSpeed)) && Number(p.animSpeed) > 0
        ? { animSpeed: Math.max(ENTRANCE_SPEED_MIN, Math.min(ENTRANCE_SPEED_MAX, Number(p.animSpeed))) } : {}),
      ...(COLLAGE_MOTIONS.includes(p.motion as CollageMotion) ? { motion: p.motion as CollageMotion } : {}),
      ...(num(p.motionAmount) !== undefined && num(p.motionAmount)! > 0 ? { motionAmount: Math.max(MOTION_AMOUNT_MIN, Math.min(MOTION_AMOUNT_MAX, num(p.motionAmount)!)) } : {}),
      ...(COLLAGE_LOOK_ANIMS.includes(p.lookAnim as CollageLookAnim) ? { lookAnim: p.lookAnim as CollageLookAnim } : {}),
      ...(num(p.motionDuration) !== undefined && num(p.motionDuration)! > 0 ? { motionDuration: Math.max(MOTION_DURATION_MIN, Math.min(MOTION_DURATION_MAX, num(p.motionDuration)!)) } : {}),
      ...(num(p.moveX) !== undefined && num(p.moveX) !== 0 ? { moveX: Math.max(-MOVE_RANGE, Math.min(MOVE_RANGE, num(p.moveX)!)) } : {}),
      ...(num(p.moveY) !== undefined && num(p.moveY) !== 0 ? { moveY: Math.max(-MOVE_RANGE, Math.min(MOVE_RANGE, num(p.moveY)!)) } : {}),
      ...(num(p.turnTo) !== undefined && num(p.turnTo) !== 0 ? { turnTo: Math.max(-TURN_RANGE, Math.min(TURN_RANGE, num(p.turnTo)!)) } : {}),
      ...look,
    })
    if (photos.length >= MAX_COLLAGE_PHOTOS) break
  }
  if (!photos.length) return undefined
  const hold = num(c.hold)
  const blur = num(c.backgroundBlur)
  // Beat list: finite numbers, clamped, rounded to ms, ascending, de-duped.
  const rawBeats = Array.isArray(c.beats) ? c.beats : []
  const beatsIn: number[] = []
  for (const b of rawBeats) {
    const v = num(b)
    if (v === undefined) continue
    beatsIn.push(Math.round(Math.max(0, Math.min(600, v)) * 1000) / 1000)
  }
  beatsIn.sort((a, b) => a - b)
  const beats = beatsIn.filter((b, i) => i === 0 || b - beatsIn[i - 1] > 0.001).slice(0, 1200)
  const rmin = num(c.randomSizeMin)
  const rmax = num(c.randomSizeMax)
  const bgLook = c.backgroundLook && typeof c.backgroundLook === 'object' ? photoLookOf(c.backgroundLook) : undefined
  return {
    photos,
    layout: COLLAGE_LAYOUTS_ALL.includes(c.layout as CollageLayout) ? c.layout as CollageLayout : 'stack',
    animation: COLLAGE_ANIMS_ALL.includes(c.animation as CollageAnim) ? c.animation as CollageAnim : 'drop',
    exit: COLLAGE_EXIT_MODES.includes(c.exit as CollageExit) ? c.exit as CollageExit : undefined,
    animDir: c.animDir === 1 ? 1 : undefined,
    animSpeed: Number.isFinite(Number(c.animSpeed)) && Number(c.animSpeed) > 0
      ? Math.max(ENTRANCE_SPEED_MIN, Math.min(ENTRANCE_SPEED_MAX, Number(c.animSpeed))) : undefined,
    exitSpeed: num(c.exitSpeed) !== undefined && num(c.exitSpeed)! > 0 && num(c.exitSpeed) !== 1 ? Math.max(EXIT_SPEED_MIN, Math.min(EXIT_SPEED_MAX, num(c.exitSpeed)!)) : undefined,
    exitReverse: c.exitReverse === true ? true : undefined,
    camera: (['pan', 'zoom', 'telescope', 'droste'] as readonly CollageCamera[]).includes(c.camera as CollageCamera) ? c.camera as CollageCamera : undefined,
    shape: COLLAGE_SHAPES_ALL.includes(c.shape as CollageShape) ? c.shape as CollageShape : '4:3',
    seed: Number.isFinite(Number(c.seed)) ? Math.trunc(Number(c.seed)) : 1,
    hold: hold !== undefined ? Math.max(0, Math.min(120, hold)) : undefined,
    beatSync: c.beatSync === true ? true : undefined,
    beats: beats.length ? beats : undefined,
    depth: c.depth === true ? true : undefined,
    backgroundImage: typeof c.backgroundImage === 'string' && c.backgroundImage ? c.backgroundImage : undefined,
    backgroundBlur: blur !== undefined ? clamp01(blur) : undefined,
    backgroundFit: (['fill', 'fit', 'stretch', 'tile', 'center', 'span'] as const).includes(c.backgroundFit as CollageBgFit) ? c.backgroundFit as CollageBgFit : undefined,
    backgroundLook: bgLook && (bgLook.filter || bgLook.crop) ? bgLook : undefined,
    randomSize: c.randomSize === true ? true : undefined,
    randomSizeMin: rmin !== undefined ? Math.max(0.5, Math.min(1.5, rmin)) : undefined,
    randomSizeMax: rmax !== undefined ? Math.max(0.5, Math.min(1.5, rmax)) : undefined,
    template: typeof c.template === 'string' && COLLAGE_TEMPLATES.some(t => t.id === c.template) ? c.template : undefined,
    frame: frameFields(c.frame),
    gap: num(c.gap) !== undefined ? Math.max(0, Math.min(12, num(c.gap)!)) : undefined,
    kenBurns: c.kenBurns === true ? true : undefined,
    sway: c.sway === true ? true : undefined,
    stickers: c.stickers === 'tape' || c.stickers === 'pin' || c.stickers === 'mix' ? c.stickers : undefined,
  }
}
