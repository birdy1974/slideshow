// Companion to backend/tests/test_collage_shuffle.py: exercises the picker's
// random roll in the *real* module, because the shuffle lives on this side of the
// bridge (the engines only ever see the concrete ids it writes).
//
// Prints one JSON object; the python test asserts on it. Run with:
//   node --experimental-strip-types backend/tests/collage_shuffle.mjs
import * as core from '../../src/collageCore.ts'

const spec = anims => ({
  layout: 'grid', cols: 3, rows: 2, animation: 'drop',
  photos: anims.map(a => (a ? { src: 'x', anim: a[0], animLock: a[1] } : { src: 'x' })),
})

const out = {}

// 1. the same seed and the same pool must give the same roll — that is all a seed
//    promises, and it is the promise the UI's "replay" button makes
const a = core.collageShuffle(spec([]), 6, { seed: 42 })
const b = core.collageShuffle(spec([]), 6, { seed: 42 })
const c = core.collageShuffle(spec([]), 6, { seed: 43 })
out.seedRepeatable = JSON.stringify(a.anims) === JSON.stringify(b.anims)
out.seedReported = a.seed === 42 && b.seed === 42 && c.seed === 43
out.differs = JSON.stringify(a.anims) !== JSON.stringify(c.anims)

// 2. every rolled id must be a real entrance that the validator accepts, and it
//    must not be 'none' (a shuffled photo that never arrives is a bug, not a
//    surprise the user chose)
out.allValid = a.anims.every(x => core.COLLAGE_ANIM_IDS_ALL.includes(x) && x !== 'none')

// 3. the pool is what the tick-boxes say it is
const onlyColour = core.collageShuffle(spec([]), 8, { families: ['colour'], seed: 7 })
out.scopeOnlyColour = onlyColour.anims.every(x => core.animFamily(x) === 'colour')
const noRepeat = core.collageShuffle(spec([]), 5, { families: ['colour'], oneOfEach: true, seed: 11 })
out.oneOfEachOverPool = new Set(noRepeat.anims).size === 5
//    …and when the pool runs out it repeats rather than running out of ideas
const tooMany = core.collageShuffle(spec([]), 12, { families: ['colour'], oneOfEach: true, seed: 11 })
out.wrapsWhenExhausted = tooMany.anims.length === 12 && new Set(tooMany.anims).size > 1

// 4. a locked photo keeps its entrance through every roll, and "one of each" does
//    not hand the same name to a second photo behind its back
const locked = spec([['iris', true]])
const roll = core.collageShuffle(locked, 4, { oneOfEach: true, seed: 5 })
out.lockKept = roll.anims[0] === 'iris'
out.lockNotDuplicated = roll.anims.slice(1).filter(x => x === 'iris').length === 0

// 5. the catalogue the picker lists is the catalogue the shuffle draws from: one
//    table, no fifth place a name can be forgotten
const rows = core.collageAnimRows()
out.rowsCoverIds = JSON.stringify(rows.map(r => r.id).sort()) ===
  JSON.stringify([...core.COLLAGE_ANIM_IDS_ALL].sort())
out.everyRowHasHint = rows.every(r => r.label && r.hint && r.family)
out.everyAnimHasFamily = core.COLLAGE_ANIMS_ALL.every(id => core.animFamily(id) !== null)
out.bridgeRowsResolve = core.COLLAGE_BRIDGE_NAMES.every(n => core.COLLAGE_ANIMS_ALL.includes(core.resolveAnim(n)))

// 6. the direction rule: an entrance with an axis can be flipped, a round one
//    cannot, and a name that carries its own meaning does not offer the toggle
out.irisNotDirectionable = core.animHasDirection('iris') === false
out.curtainIsDirectionable = core.animHasDirection('curtain') === true
out.slideNotDirectionable = core.animHasDirection('slideleft') === false
out.smoothleftIsDirectionable = core.animHasDirection('smoothleft') === true

// 7. and the *other* engine's validator keeps the same four keys — the roll's
//    bookkeeping surviving one normaliser and not the other is the divergence that
//    costs the most to find, so both are read from the same input here
const saved = core.normalizeCollage({
  layout: 'grid', cols: 2, rows: 1, photos: [{ path: '/a.jpg', anim: 'iris', animLock: true }, { path: '/b.jpg' }],
  animSeed: '7.9', animFamilies: ['mask', 'nope', 'paper'], animOneOfEach: true,
})
out.tsNormalizesTheSame = saved.animSeed === 7 && JSON.stringify(saved.animFamilies) === '["mask","paper"]'
  && saved.animOneOfEach === true && saved.photos[0].animLock === true
  && saved.photos[1].animLock === undefined
// the seed fold both engines must apply, or a roll replayed after a save is not
// the roll that made the file
const big = core.normalizeCollage({ layout: 'grid', cols: 1, rows: 1, photos: [{ path: '/a.jpg' }], animSeed: 3000000000 })
const neg = core.normalizeCollage({ layout: 'grid', cols: 1, rows: 1, photos: [{ path: '/a.jpg' }], animSeed: -5 })
out.tsSeedFold = big.animSeed === (3000000000 & 0x7fffffff) && neg.animSeed === ((-5) & 0x7fffffff)
// every bridge target must be a real animation *and* the geometry/mask/colour row
// the state reads, or the name renders as plain 'drop' and the label is decoration
out.tsBridgeTargetsHaveLengths = core.COLLAGE_BRIDGE_NAMES
  .every(n => (core.ENTRANCE_LENGTH[core.resolveAnim(n)] ?? 0) > 0)

console.log(JSON.stringify(out))
