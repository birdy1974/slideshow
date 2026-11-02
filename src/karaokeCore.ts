// Karaoke timing model — the browser twin of backend/app/karaoke.py.
//
// Karaoke here is *caption text with a per-word clock*: the words stay in the
// slide's own `text` field, and the timing rides next to them as one small
// entry per word. Sentences, the per-character fill of the sweep, the ASS tags
// and the preview state are all derived, so the text stays editable and the
// timing survives an edit — an entry is anchored to a *word index*, never to a
// byte offset in the string.
//
// The same maths runs in the browser (this file: preview, strip, drag targets)
// and in Python (karaoke.py: the .ass the render burns in).
// backend/tests/test_karaoke_twin.py pins them together the way
// test_text_motion_twin.py pins the text engine. Where the two languages have
// different builtins that matter, this file says so — see `textLength`.
//
// Times are **clip-local seconds**: 0 is the clip's own IN point, which is how
// `textStart`/`textEnd` already work, so re-trimming a movie never shifts the
// karaoke.

export type KaraokeEditorMode = 'strip' | 'tap' | 'chips' | 'wave'
export type KaraokeStyle = 'sweep' | 'word' | 'reveal' | 'pop'

/** One word of the caption: when it starts, and whether a sentence ends here. */
export interface KaraokeWord {
  /** Start in clip-local seconds. Missing = never timed; the word then takes
   *  its share of the gap to the next timed word (that is the "coarse pass
   *  first, words second" rule every karaoke guide recommends). */
  t?: number
  /** 1 = force a sentence break after this word, -1 = glue it to the next.
   *  Undefined = the automatic rule (`. ! ? …` or a line break). */
  br?: 1 | -1
}

export interface KaraokeData {
  /** Which timing surface the popup opens in — per item on purpose: a spoken
   *  clip is timed against a waveform, a song is timed by tapping. */
  editor: KaraokeEditorMode
  /** What the words do as they are sung. */
  style: KaraokeStyle
  /** Colour of a word once it has been sung. The caption's own colour is the
   *  "not yet sung" side, so one field is enough. */
  fill: string
  /** Seconds a sentence shows before its first word. Every karaoke guide asks
   *  for a lead-in: text that lands with the vocal reads late. */
  leadIn: number
  /** Seconds the finished sentence stays up after its last word. */
  tail: number
  /** Onset-magnet radius in seconds, 0 = snapping off. */
  snap: number
  /** Auto-split cap: a run longer than this many words is cut anyway. */
  maxWords: number
  /** How the sung text looks, when it should not look like the caption. Every
   *  field is optional and unset means "the caption's own value", which is what
   *  makes karaoke able to carry a *different* typography from the text it is
   *  sung from (a bigger face, a serif, no outline) without a second slide.
   *  Absent `text` entirely = the caption's style exactly. */
  text?: KaraokeTextStyle
  /** The timing, one entry per word of `text`. */
  words: KaraokeWord[]
}

/** The nine settings a caption's look is made of — the same nine the render's
 *  `Style: FX` line carries (font, size, weight trio, colour, outline, position)
 *  and the same nine `KaraokeStage` paints.
 *
 *  Deliberately not here: outline *colour*, shadow depth, letter spacing and text
 *  alignment. Those are fixed for every text on this path by that one shared
 *  style line (backend/app/text_effects.py `_header`: `Spacing 0`, `Shadow 2`,
 *  `Alignment 5` with `\pos`, a constant outline colour), so a karaoke-only
 *  control for them would show up in the preview and never in the MP4. */
export interface KaraokeTextStyle {
  family?: string
  /** px at the 1920-wide stage, exactly like the caption's own size. */
  size?: number
  bold?: boolean
  italic?: boolean
  underline?: boolean
  /** The "not yet sung" colour; `fill` is the sung one. */
  colour?: string
  outline?: boolean
  /** Percent of the frame, like the caption's position. */
  x?: number
  y?: number
}

/** A word with its char span in the caption text, as `tokenize` returns it. */
export interface KaraokeToken {
  w: string
  i0: number
  i1: number
  /** Set when a newline follows the word: an explicit break the user typed,
   *  and a `\N` in the render. */
  nl?: boolean
}

export interface KaraokePlanWord {
  /** Index into `tokenize(text)` / `data.words`. */
  i: number
  t0: number
  t1: number
}

export interface KaraokePlanSentence {
  from: number
  to: number
  start: number
  end: number
  words: KaraokePlanWord[]
  /** True when the source text had a line break inside this sentence. */
  hard?: boolean
}

export interface KaraokePlan {
  sentences: KaraokePlanSentence[]
  /** End of the last sentence; the caller clamps it to the slide's window. */
  end: number
}

/** What the preview has to paint at one instant. */
export interface KaraokeFrame {
  /** Sentence index; -1 = nothing on screen. */
  s: number
  /** Index inside that sentence of the word being sung; -1 = before the first. */
  k: number
  /** 0..1 fill of the word at `k`. */
  u: number
  /** 0..1 index of the *last* word already finished, for the styles that keep
   *  everything after the current word hidden (`reveal`). */
  done: number
  /** 0..1 fade of the sentence itself (lead-in and tail). */
  fade: number
}

export const KARAOKE_EDITORS: { id: KaraokeEditorMode; label: string; hint: string }[] = [
  { id: 'strip', label: 'Sentence strip', hint: 'One block per sentence on the movie’s own filmstrip: drag an edge, Shift-click sets the start, Ctrl-click the end, Ctrl+Shift ripples everything after' },
  { id: 'tap', label: 'Tap along', hint: 'Press the key in time with each word (or each sentence in the coarse pass) and the timing is written while the movie plays — no aiming at all' },
  { id: 'chips', label: 'Word chips', hint: 'The text itself is the editor: drag the gap after a sentence, double-click a word to start it here, Enter makes a sentence out of the selection' },
  { id: 'wave', label: 'Waveform', hint: 'The same strip over the movie’s own audio peaks, so a consonant you can hear is a line you can put your mouse on' },
]

export const KARAOKE_STYLES: { id: KaraokeStyle; label: string; hint: string }[] = [
  { id: 'sweep', label: 'Sweep', hint: 'True karaoke: each word fills left to right while it is sung (ASS \\kf)' },
  { id: 'word', label: 'Word highlight', hint: 'The word being sung flips to the highlight colour at its start, the rest stays plain' },
  { id: 'reveal', label: 'Reveal', hint: 'The sentence builds: nothing is visible until it is sung, and it stays' },
  { id: 'pop', label: 'Pop', hint: 'One word at a time, centred and punched in — the Reels/TikTok caption look' },
]

/** The fields a chip toggles rather than a box types — three states (inherit,
 *  on, off), because "off" is a look people want and "the caption's value" is the
 *  default that must stay reachable. */
export type KaraokeTextFlag = 'bold' | 'italic' | 'underline' | 'outline'

/** One row per overridable field of `KaraokeData.text`: the label, the widget, the
 *  range and the promise. The popup builds its controls from this table and the
 *  sanitizer validates against it, so a control cannot exist that nothing honours.
 *  `TEXT_STYLE` in backend/app/karaoke.py is the mirror, and it additionally names
 *  the caption key each override is spliced into; a wiring test asserts the two
 *  lists hold the same names in the same order. */
export interface KaraokeTextField {
  id: keyof KaraokeTextStyle
  label: string
  kind: 'font' | 'size' | 'flag' | 'colour' | 'pos'
  hint: string
  min?: number
  max?: number
  step?: number
}

export const KARAOKE_TEXT_FIELDS: readonly KaraokeTextField[] = [
  { id: 'family', label: 'Font', kind: 'font', hint: 'The face the words are sung in' },
  { id: 'size', label: 'Size', kind: 'size', hint: 'Pixels on a 1920-wide frame, like the caption’s own size', min: 8, max: 400, step: 1 },
  { id: 'bold', label: 'Bold', kind: 'flag', hint: 'Heavy face' },
  { id: 'italic', label: 'Italic', kind: 'flag', hint: 'Slanted face' },
  { id: 'underline', label: 'Underline', kind: 'flag', hint: 'Underlined as it is sung' },
  { id: 'colour', label: 'Unsung colour', kind: 'colour', hint: 'The colour before a word is sung — the fill is the colour after' },
  { id: 'outline', label: 'Outline', kind: 'flag', hint: 'Readability ring. Off is what most lyric videos do' },
  { id: 'x', label: 'X %', kind: 'pos', hint: 'Percent of the frame width', min: 0, max: 100, step: 0.5 },
  { id: 'y', label: 'Y %', kind: 'pos', hint: 'Percent of the frame height', min: 0, max: 100, step: 0.5 },
]

export const KARAOKE_DEFAULTS = {
  editor: 'strip',
  style: 'sweep',
  fill: '#ffd166',
  leadIn: 0.35,
  tail: 0.3,
  snap: 0.25,
  maxWords: 12,
} as const

const num = (v: unknown, dflt: number) => (Number.isFinite(Number(v)) ? Number(v) : dflt)
const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v)
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v)
/** Round to milliseconds — the resolution both engines agree on, so a save →
 *  load → render cycle cannot drift by a frame. */
export const msRound = (v: number) => Math.round(v * 1000) / 1000

/** `#rrggbb` and nothing else — the form both CSS and the ASS colour field want,
 *  and exactly the rule `fill` follows in both engines. A 3-digit or named colour
 *  is dropped rather than guessed at, and a valid one survives verbatim: the
 *  loader must not be the place where a colour's case (or meaning) changes. */
export const KARAOKE_COLOUR = /^#[0-9a-fA-F]{6}$/

export function karaokeColour(value: unknown): string | undefined {
  return typeof value === 'string' && KARAOKE_COLOUR.test(value) ? value : undefined
}

/** A number in range, or undefined. `typeof` first on purpose: JS would happily
 *  turn `true` into `1`, and a toggle is never a font size. */
function karaokeNumberInRange(value: unknown, min: number, max: number): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return clamp(value, min, max)
}

/** Keep only the fields `KaraokeTextStyle` knows, each inside its own range, and
 *  return undefined when nothing is left — so "no overrides" is one shape (absent)
 *  for the loader, the preview and the renderer to agree on. Unknown keys are
 *  dropped quietly: this block is written by older and newer builds and by hand.
 *  Mirrored field for field by `sanitize_text_style` in backend/app/karaoke.py. */
export function sanitizeKaraokeTextStyle(raw: unknown): KaraokeTextStyle | undefined {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
  const src = raw as Record<string, unknown>
  const out: KaraokeTextStyle = {}
  const range = (id: string) => {
    const field = KARAOKE_TEXT_FIELDS.find(f => f.id === id)
    return { min: field?.min ?? 0, max: field?.max ?? 100 }
  }
  const family = typeof src.family === 'string' ? src.family.trim() : ''
  if (family) out.family = family.slice(0, 64)
  const size = karaokeNumberInRange(src.size, range('size').min, range('size').max)
  if (size !== undefined) out.size = Math.round(size)
  const colour = karaokeColour(src.colour)
  if (colour) out.colour = colour
  for (const key of ['bold', 'italic', 'underline', 'outline'] as const) {
    if (typeof src[key] === 'boolean') out[key] = src[key] as boolean
  }
  for (const key of ['x', 'y'] as const) {
    const v = karaokeNumberInRange(src[key], range(key).min, range(key).max)
    if (v !== undefined) out[key] = v
  }
  return Object.keys(out).length ? out : undefined
}

/** What a karaoke block paints with: the caption's style, in the names
 *  `KaraokeStage` takes as props, with the block's overrides on top.
 *
 *  `size` is the one field spelled differently between the two models (the stage
 *  prop is `fontSize`, like every other caption path), and that rename lives here
 *  instead of in a caller — which is what keeps the popup preview and the main
 *  preview from drifting apart, and keeps `style_overrides` in
 *  backend/app/karaoke.py as the only mapping on the render side. */
export interface KaraokePaintStyle extends KaraokeTextStyle {
  family: string
  fontSize: number
  bold: boolean
  italic: boolean
  colour: string
  x: number
  y: number
  align?: 'center' | 'left'
}

export function karaokePaintStyle<S extends KaraokePaintStyle>(caption: S, data?: { text?: KaraokeTextStyle } | null): S {
  const over = sanitizeKaraokeTextStyle(data?.text)
  if (!over) return caption
  const { size, ...rest } = over
  return { ...caption, ...rest, ...(size === undefined ? {} : { fontSize: size }) }
}

/** Whether anything is overridden at all — the popup only offers a reset then. */
export function karaokeTextStyleTouched(data?: { text?: KaraokeTextStyle } | null): boolean {
  return sanitizeKaraokeTextStyle(data?.text) !== undefined
}

/** Length of a token in UTF-16 code units, i.e. `String.length` in JS and
 *  `len(s.encode('utf-16-le')) // 2` in Python. Python counts code *points*,
 *  which would disagree on an emoji, so neither side uses its own builtin. */
export function textLength (s: string): number {
  return s.length
}

/** ------------------------------------------------------------------ *
 * Tokenising and sentence grouping
 * ------------------------------------------------------------------ */

export function tokenize (text: string): KaraokeToken[] {
  const out: KaraokeToken[] = []
  const re = /\S+/g
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    const i0 = m.index
    const i1 = i0 + m[0].length
    out.push({ w: m[0], i0, i1, nl: /^[ \t]*\r?\n/.test(text.slice(i1)) })
  }
  return out
}

/** Weight of a word = its length, so a long word takes longer to sing. This is
 *  the cheap guess every tool uses before alignment; the floor keeps "a" from
 *  collapsing to nothing. Deliberately character-count based, because that is
 *  also what the ASS `\kf` step divides. */
export function wordWeight (word: string): number {
  return Math.max(2, textLength(word))
}

const STRONG = /[.!?…]["'”’)\]]*$/
const SOFT = /[,;:…]["'”’)\]]*$/
const LOWER = /^[a-z]/
const DIGIT = /^[0-9]/
/** Upper-case words that end in a full stop without ending a sentence. */
const ABBREVIATIONS = new Set(['mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'no', 'vs', 'etc', 'approx', 'fig', 'inc', 'ltd', 'govt', 'dept', 'ave', 'blvd', 'rd'])

/** True when a trailing `.` is not a sentence end ("Mr.", "J.", "vol. 3"). */
function abbreviation (word: string, next: KaraokeToken | undefined): boolean {
  const bare = word.replace(/["'”’)\]]+$/, '')
  if (!bare.endsWith('.')) return false
  const stem = bare.slice(0, -1)
  if (!stem) return false
  if (stem.length === 1) return true                       // an initial: "J."
  if (next && DIGIT.test(next.w)) return true               // "vol." before "3"
  return ABBREVIATIONS.has(stem.toLowerCase())
}

/** The automatic break after word `i`. `runStart` is where the current
 *  sentence began, so the max-words cap can be measured from it. */
export function autoBreak (tokens: KaraokeToken[], i: number, runStart: number, maxWords: number): boolean {
  const t = tokens[i]
  if (!t) return false
  if (t.nl) return true
  if (i >= tokens.length - 1) return false
  // The cap is a legibility promise, so when it fires the line ends there.
  // Where it ends can still be nicer — `sentenceGroups` steps back onto a comma
  // one word earlier — but deferring the cut used to run the line on to the
  // next full stop, which is how "4 words per line" produced an eight-word
  // sentence and a one-word orphan.
  const cap = Math.max(1, Math.round(maxWords))
  if (i - runStart + 1 >= cap) return true
  if (!STRONG.test(t.w)) return false
  if (abbreviation(t.w, tokens[i + 1])) return false
  // A lower-case follower after a bare full stop is nearly always "1.5 km" or
  // a mid-sentence dot, not a new sentence.
  const next = tokens[i + 1]
  if (next && LOWER.test(next.w) && /\.$/.test(t.w.replace(/["'”’)\]]+$/, ''))) {
    const stem = t.w.replace(/["'”’)\]]+$/, '').slice(0, -1)
    if (!/[!?…]/.test(t.w) && stem.length > 1 && !ABBREVIATIONS.has(stem.toLowerCase())) return false
  }
  return true
}

/** Word-index ranges of the sentences, honouring the forced `br` flags. */
export function sentenceGroups (tokens: KaraokeToken[], words: KaraokeWord[], maxWords: number): [number, number][] {
  const groups: [number, number][] = []
  const cap = Math.max(1, Math.round(maxWords))
  let start = 0
  for (let i = 0; i < tokens.length; i++) {
    const forced = words[i]?.br
    const cut = forced === 1 ? true : forced === -1 ? false : autoBreak(tokens, i, start, maxWords)
    if (cut || i === tokens.length - 1) {
      let end = i
      if (forced !== 1 && forced !== -1 && cut && i - start + 1 >= cap) {
        // The cap, not a full stop, ended this line: if the word before it
        // closes a clause, cut there instead. One word back is the limit, so a
        // run never grows past the cap and a short line never becomes an orphan.
        const prev = i - 1
        if (prev > start && SOFT.test(tokens[prev].w) && !STRONG.test(tokens[i].w) && !SOFT.test(tokens[i].w)) end = prev
        // …and a handful of words left over is folded back in rather than
        // stranded as its own caption: two even lines beat a long one and a stub.
        const short = Math.max(2, Math.ceil(cap / 4))
        const remaining = tokens.length - (end + 1)
        if (remaining > 0 && remaining < short) {
          const back = Math.min(short - remaining, end + 1 - start - Math.ceil(cap / 2))
          if (back > 0) end -= back
        }
      }
      groups.push([start, end])
      start = end + 1
    }
  }
  return groups
}

/** ------------------------------------------------------------------ *
 * Timing
 * ------------------------------------------------------------------ */

/** Spread `tokens` over [t0, t1] weighted by length. `keep` preserves the
 *  `br` flags of the incoming array (it is returned as a new array). */
export function distribute (tokens: KaraokeToken[], words: KaraokeWord[], t0: number, t1: number): KaraokeWord[] {
  const n = tokens.length
  const out: KaraokeWord[] = tokens.map((_, i) => (words[i] ? { ...words[i] } : {}))
  if (!n) return out
  const weights = tokens.map(t => wordWeight(t.w))
  const total = weights.reduce((a, b) => a + b, 0) || 1
  const span = Math.max(0.2, t1 - t0)
  let acc = 0
  for (let i = 0; i < n; i++) {
    out[i] = { ...out[i], t: msRound(t0 + (acc / total) * span) }
    acc += weights[i]
  }
  return out
}

/** Even spread of one sentence: used when the user timed the sentences but not
 *  the words (the coarse pass). */
export function fillSentence (tokens: KaraokeToken[], words: KaraokeWord[], from: number, to: number, t0: number, t1: number): KaraokeWord[] {
  const out = words.map(w => ({ ...w }))
  const slice = tokens.slice(from, to + 1)
  const weights = slice.map(t => wordWeight(t.w))
  const total = weights.reduce((a, b) => a + b, 0) || 1
  const span = Math.max(0.15, t1 - t0)
  let acc = 0
  for (let k = 0; k < slice.length; k++) {
    const i = from + k
    if (!Number.isFinite(Number(out[i]?.t))) {
      out[i] = { ...out[i], t: msRound(t0 + (acc / total) * span) }
    }
    acc += weights[k]
  }
  return out
}

/** Whole-text first pass: sentences get equal-ish share of the window (weighted
 *  by their words), the words inside them take theirs. Returns a ready `words`
 *  array — this is the "correct a guess, don't fill a form" starting point. */
export function autoTime (tokens: KaraokeToken[], words: KaraokeWord[], maxWords: number, t0: number, t1: number): KaraokeWord[] {
  const groups = sentenceGroups(tokens, words, maxWords)
  if (!tokens.length || !groups.length) return words.map(w => ({ ...w }))
  const weights = groups.map(([a, b]) => {
    let s = 0
    for (let i = a; i <= b; i++) s += wordWeight(tokens[i].w)
    return s
  })
  const total = weights.reduce((a, b) => a + b, 0) || 1
  const span = Math.max(0.5, t1 - t0)
  let out = words.map(w => ({ ...w }))
  let acc = 0
  for (let g = 0; g < groups.length; g++) {
    const s0 = t0 + (acc / total) * span
    acc += weights[g]
    const s1 = t0 + (acc / total) * span
    out = fillSentence(tokens, out, groups[g][0], groups[g][1], s0, s1)
  }
  return out
}

/** Pull each time onto the nearest onset inside `radius`, keeping order and a
 *  minimum gap (two words at the same instant make one of them invisible). */
export function snapTimes (times: number[], onsets: number[], radius: number, minGap = 0.04): number[] {
  if (!(radius > 0) || !onsets.length) return times.slice()
  const sorted = onsets.slice().sort((a, b) => a - b)
  const out: number[] = []
  for (let i = 0; i < times.length; i++) {
    const t = times[i]
    let lo = 0
    let hi = sorted.length - 1
    let best = Infinity
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      const d = Math.abs(sorted[mid] - t)
      if (d < Math.abs(best - t)) best = sorted[mid]
      if (sorted[mid] < t) lo = mid + 1
      else hi = mid - 1
    }
    let v = Number.isFinite(best) && Math.abs(best - t) <= radius ? best : t
    if (out.length) v = Math.max(v, out[out.length - 1] + minGap)
    out.push(msRound(v))
  }
  return out
}

/** Snap the timed words of `words` onto onsets (the magnet of the strip). */
export function snapWords (words: KaraokeWord[], onsets: number[], radius: number): KaraokeWord[] {
  const idx: number[] = []
  const times: number[] = []
  words.forEach((w, i) => {
    if (Number.isFinite(Number(w.t))) {
      idx.push(i)
      times.push(Number(w.t))
    }
  })
  if (!idx.length) return words.map(w => ({ ...w }))
  const snapped = snapTimes(times, onsets, radius)
  const out = words.map(w => ({ ...w }))
  idx.forEach((wi, k) => {
    out[wi] = { ...out[wi], t: snapped[k] }
  })
  return out
}

/** Push everything from `from` on by `delta` — Subtitle Edit's "set start and
 *  offset all following cues", which is what keeps a fix at sentence 3 from
 *  breaking sentences 4 to 40. */
export function ripple (words: KaraokeWord[], from: number, delta: number): KaraokeWord[] {
  return words.map((w, i) => (i >= from && Number.isFinite(Number(w.t)) ? { ...w, t: Math.max(0, msRound(Number(w.t) + delta)) } : { ...w }))
}

/** Keep the sequence increasing without moving anything earlier than it is. */
export function makeContinuous (words: KaraokeWord[], minGap = 0.04): KaraokeWord[] {
  const out = words.map(w => ({ ...w }))
  let prev = -Infinity
  for (const w of out) {
    if (!Number.isFinite(Number(w.t))) continue
    w.t = msRound(Math.max(Number(w.t), prev + minGap))
    prev = Number(w.t)
  }
  return out
}

/** Absolute windows, the only thing either engine needs to draw or to emit.
 *
 *  A word ends where the next one starts, so a sweep always lands exactly on
 *  the next syllable; a sentence opens `leadIn` before its first word and
 *  holds `tail` after the last one. Words with no time of their own sit
 *  between their nearest timed neighbours, so a coarse pass (one tap per
 *  sentence) already looks like karaoke and only gets better as words are
 *  timed. */
export function planOf (text: string, data: KaraokeData, opts: { from?: number; to?: number; duration?: number } = {}): KaraokePlan {
  const tokens = tokenize(text)
  const groups = sentenceGroups(tokens, data.words || [], data.maxWords)
  const leadIn = clamp(num(data.leadIn, KARAOKE_DEFAULTS.leadIn), 0, 5)
  const tail = clamp(num(data.tail, KARAOKE_DEFAULTS.tail), 0, 10)
  const lo = Math.max(0, num(opts.from, 0))
  const ceiling = num(opts.to, 0) > 0 ? num(opts.to, 0) : num(opts.duration, 0)
  const raw: (number | null)[] = tokens.map((_, i) => {
    const t = Number(data.words?.[i]?.t)
    return Number.isFinite(t) && t >= 0 ? msRound(t) : null
  })
  // Fill the gaps: an un-timed word splits the distance between the timed
  // neighbours around it by weight.
  for (const [from, to] of groups) {
    for (let i = from; i <= to; i++) {
      if (raw[i] !== null) continue
      let prev = i - 1
      while (prev >= from && raw[prev] === null) prev--
      let next = i + 1
      while (next <= to && raw[next] === null) next++
      const a = prev >= from ? raw[prev] : null
      const b = next <= to ? raw[next] : null
      if (a !== null && b !== null && b > a) {
        let acc = 0
        for (let k = prev + 1; k < i; k++) acc += wordWeight(tokens[k].w)
        let span = 0
        for (let k = prev + 1; k <= next - 1; k++) span += wordWeight(tokens[k].w)
        raw[i] = msRound(a + ((b - a) * (acc + wordWeight(tokens[i].w) / 2)) / Math.max(span, 1))
      } else if (a !== null) {
        raw[i] = msRound(a + wordWeight(tokens[i].w) * 0.045)
      } else if (b !== null) {
        raw[i] = msRound(Math.max(lo, b - wordWeight(tokens[i].w) * 0.045))
      }
    }
  }
  const firstTime = raw.find(v => v !== null)
  if (firstTime === null || firstTime === undefined) return { sentences: [], end: 0 }
  const sentences: KaraokePlanSentence[] = []
  let end = 0
  for (let g = 0; g < groups.length; g++) {
    const [from, to] = groups[g]
    const start = raw[from]
    if (start === null) continue
    // The sentence's own end: the next sentence's first word, else the window.
    let boundary: number | null = null
    for (let h = g + 1; h < groups.length && boundary === null; h++) boundary = raw[groups[h][0]]
    if (boundary === null) boundary = ceiling > 0 ? ceiling : msRound(start + 1.2)
    const words: KaraokePlanWord[] = []
    let last1 = start
    for (let i = from; i <= to; i++) {
      // Never earlier than the previous word ended: a mistimed tap cannot
      // reorder the line, it just stops the previous word there.
      const t0 = raw[i] === null ? last1 : Math.max(raw[i] as number, last1)
      words.push({ i, t0, t1: 0 })
      last1 = t0
    }
    // Each word is sung until the next one starts; the last one owns the rest
    // of the sentence but keeps a breath before the next sentence lands.
    for (let k = 0; k < words.length; k++) {
      const w = words[k]
      const nxt = words[k + 1]
      if (nxt) w.t1 = msRound(nxt.t0)   // sung until the next word starts; no floor, or two words would overlap
      else {
        const room = Math.max(0.3, Math.min(1.1, boundary - w.t0))
        w.t1 = msRound(w.t0 + room)
      }
      last1 = Math.max(last1, w.t1)
    }
    if (last1 > boundary) last1 = boundary
    let hard = false
    for (let i = from; i < to; i++) if (tokens[i]?.nl) hard = true
    const s: KaraokePlanSentence = {
      from, to, words, hard,
      start: msRound(Math.max(lo, start - leadIn)),
      end: msRound(ceiling > 0 ? Math.min(ceiling, last1 + tail) : last1 + tail),
    }
    if (s.end <= s.start) s.end = msRound(s.start + 0.2)
    sentences.push(s)
    end = Math.max(end, s.end)
  }
  // Two sentences must never be on screen at once: they share one \pos, so an
  // overlap is two lines printed on top of each other. The handoff point is the
  // next line's first word — the incoming line is what the eye is already
  // looking for, and a word's sweep finishing exactly as its line leaves is
  // what makes a contiguous caption look seamless. So the previous line may hold
  // until that word, but never past it: when a user taps two sentences on top of
  // each other the earlier line is cut short instead of overprinting, which is
  // the visible, honest signal that they need a gap. Word times are never moved
  // here, only the display windows. Mirrored in backend/app/karaoke.py.
  for (let i = 1; i < sentences.length; i++) {
    const prev = sentences[i - 1]
    const cur = sentences[i]
    if (cur.start >= prev.end) continue
    const firstWord = cur.words.length ? cur.words[0].t0 : cur.start
    const target = Math.min(Math.max(cur.start, prev.end), firstWord)
    cur.start = msRound(target)
    prev.end = msRound(target)
    if (prev.end <= prev.start) {
      // Nothing left to truncate: keep a blink on the earlier line and let the
      // later one start after it, rather than drawing both at once.
      prev.end = msRound(prev.start + 0.2)
      cur.start = msRound(Math.max(cur.start, prev.end))
    }
  }
  end = sentences.length ? msRound(sentences.reduce((m, s) => Math.max(m, s.end), 0)) : 0
  return { sentences, end }
}

/** ------------------------------------------------------------------ *
 * Preview state (the DOM twin of the ASS the render writes)
 * ------------------------------------------------------------------ */

/** Which sentence the panel and the strip should be on while the playhead moves.
 *
 *  The rule subtitle editors use (Aegisub's cursor, Subtitle Edit's follow): the
 *  line that is showing, and during a gap the *next* one to come, so the line you
 *  are about to hear is already selected and ready to nudge. Past the end it stays
 *  on the last. -1 = nothing to follow.
 *
 *  It lives here rather than in the popup because the popup is not the only thing
 *  that has to agree about it, and `follow_sentence` in backend/app/karaoke.py is
 *  its twin, pinned by the karaoke twin test.
 */
export function followSentence (plan: KaraokePlan, t: number): number {
  const lines = plan.sentences
  if (!lines.length) return -1
  for (let i = 0; i < lines.length; i++) if (t < lines[i].end) return i
  return lines.length - 1
}

export function karaokeAt (plan: KaraokePlan, t: number, style: KaraokeStyle = 'sweep'): KaraokeFrame {
  const empty: KaraokeFrame = { s: -1, k: -1, u: 0, done: -1, fade: 1 }
  for (let si = 0; si < plan.sentences.length; si++) {
    const s = plan.sentences[si]
    if (t < s.start || t > s.end) continue
    const lead = s.words.length ? s.words[0].t0 - s.start : 0.2
    const fade = lead > 0.06 ? clamp01((t - s.start) / lead) : 1
    let k = -1
    let u = 0
    for (let wi = 0; wi < s.words.length; wi++) {
      const w = s.words[wi]
      if (t < w.t0) break
      k = wi
      u = w.t1 > w.t0 ? clamp01((t - w.t0) / (w.t1 - w.t0)) : 1
      if (t < w.t1) break
    }
    const done = u >= 1 ? k : k - 1
    if (style === 'word' || style === 'pop') u = k >= 0 ? 1 : 0
    if (style === 'reveal') u = k >= 0 ? 1 : 0
    return { s: si, k, u, done, fade }
  }
  return empty
}

/** Colour state of one word of the visible sentence, for the DOM preview.
 *  `filled` words are sung, `partial` carries the sweep fraction of the word
 *  being sung. */
export function wordStates (plan: KaraokePlan, frame: KaraokeFrame, style: KaraokeStyle): ('next' | 'now' | 'sung')[] {
  const s = plan.sentences[frame.s]
  if (!s) return []
  return s.words.map((w, i) => {
    if (style === 'word' || style === 'pop') return i === frame.k ? 'now' : i < frame.k ? 'sung' : 'next'
    if (style === 'reveal') return i <= frame.done ? 'sung' : 'next'
    if (i < frame.k) return 'sung'
    if (i > frame.k) return 'next'
    return frame.u >= 1 ? 'sung' : 'now'
  })
}

/** ------------------------------------------------------------------ *
 * Model plumbing
 * ------------------------------------------------------------------ */

export const hasKaraoke = (item: { karaoke?: KaraokeData | null } | null | undefined): boolean =>
  !!item?.karaoke?.words?.some(w => Number.isFinite(Number(w.t)) || w.br !== undefined)

/** Validate whatever came out of a project file or a paste. Out-of-range
 *  numbers are clamped and unknown keys dropped — the `normalizeCollage`
 *  policy, for the same reason: a corrupt field must never reach FFmpeg. */
export function sanitizeKaraoke (raw: unknown, wordCount: number): KaraokeData | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const r = raw as Record<string, unknown>
  const editors: KaraokeEditorMode[] = ['strip', 'tap', 'chips', 'wave']
  const styles: KaraokeStyle[] = ['sweep', 'word', 'reveal', 'pop']
  const editor = editors.includes(r.editor as KaraokeEditorMode) ? (r.editor as KaraokeEditorMode) : KARAOKE_DEFAULTS.editor
  const style = styles.includes(r.style as KaraokeStyle) ? (r.style as KaraokeStyle) : KARAOKE_DEFAULTS.style
  const text = sanitizeKaraokeTextStyle(r.text)
  const fill = karaokeColour(r.fill) ?? KARAOKE_DEFAULTS.fill
  const list = Array.isArray(r.words) ? r.words : []
  const n = clamp(Math.round(num(wordCount, list.length)), 0, 5000)
  const words: KaraokeWord[] = []
  let timed = 0
  for (let i = 0; i < n; i++) {
    const w = list[i] as Record<string, unknown> | null | undefined
    const wv = w && typeof w === 'object' ? w : null
    const t = Number(wv?.t)
    const br = wv?.br
    const entry: KaraokeWord = {}
    if (Number.isFinite(t) && t >= 0 && t < 86400) {
      entry.t = msRound(t)
      timed++
    }
    if (br === 1 || br === '1') entry.br = 1
    else if (br === -1 || br === '-1') entry.br = -1
    words.push(entry)
  }
  if (!timed && !words.some(w => w.br !== undefined)) return undefined
  return {
    editor, style, fill,
    leadIn: clamp(num(r.leadIn, KARAOKE_DEFAULTS.leadIn), 0, 5),
    tail: clamp(num(r.tail, KARAOKE_DEFAULTS.tail), 0, 10),
    snap: clamp(num(r.snap, KARAOKE_DEFAULTS.snap), 0, 2),
    maxWords: clamp(Math.round(num(r.maxWords, KARAOKE_DEFAULTS.maxWords)), 1, 80),
    // Only written when something is actually overridden, so a project saved
    // before this existed (or one that never touches the block) stays byte-same.
    ...(text ? { text } : {}),
    words,
  }
}

/** Re-anchor the timing after the text changed, so typing a word *inserts* a
 *  slot instead of shifting every later word by one. Matched words keep their
 *  own time; a new word inherits the time of the old word it replaced, then
 *  `makeContinuous` restores the order. */
export function retarget (previous: KaraokeData | undefined, before: KaraokeToken[], after: KaraokeToken[]): KaraokeWord[] {
  const old = previous?.words || []
  const out: KaraokeWord[] = after.map(() => ({}))
  const a = before.map(t => t.w)
  const b = after.map(t => t.w)
  const big = a.length * b.length
  if (big && big <= 1500000) {
    // Longest common subsequence on the word strings: exact for the common
    // case (a typo, an added clause, a deleted sentence).
    const dp: number[] = new Array((b.length + 1) * (a.length + 1)).fill(0)
    const at = (i: number, j: number) => i * (a.length + 1) + j
    for (let i = b.length - 1; i >= 0; i--) {
      for (let j = a.length - 1; j >= 0; j--) {
        dp[at(i, j)] = b[i] === a[j] ? dp[at(i + 1, j + 1)] + 1 : Math.max(dp[at(i + 1, j)], dp[at(i, j + 1)])
      }
    }
    let i = 0
    let j = 0
    while (i < b.length && j < a.length) {
      if (b[i] === a[j]) {
        const prev = old[j]
        if (prev) out[i] = { ...(prev.t !== undefined ? { t: prev.t } : {}), ...(prev.br !== undefined ? { br: prev.br } : {}) }
        i++
        j++
      } else if (dp[at(i + 1, j)] >= dp[at(i, j + 1)]) i++
      else j++
    }
  } else {
    // Too many words for the table: map by position, which is what a
    // wholesale text replacement needs anyway.
    const m = Math.max(1, b.length - 1)
    const o = Math.max(1, a.length - 1)
    for (let i = 0; i < b.length; i++) {
      const prev = old[Math.round((i / m) * o)]
      if (prev?.t !== undefined) out[i] = { t: prev.t }
    }
  }
  return makeContinuous(out)
}

/** A cheap signature of the token list, so the popup can say "the text moved,
 *  re-time?" instead of silently re-guessing. */
export function textSignature (tokens: KaraokeToken[]): string {
  let h = 2166136261
  const s = tokens.map(t => t.w).join(' ')
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0).toString(36)
}

/** Words per second, for the "this is going to be hard to sing" hint the
 *  karaoke tools all show. Above ~3.5 words/s the line should be split. */
export function wordsPerSecond (plan: KaraokePlan): number {
  let words = 0
  let dur = 0
  for (const s of plan.sentences) {
    words += s.words.length
    dur += Math.max(0.001, (s.words.length ? s.words[s.words.length - 1].t1 - s.words[0].t0 : 0))
  }
  return dur > 0 ? words / dur : 0
}

/** ------------------------------------------------------------------ *
 * Importing timings that already exist
 *
 * A song's timing is very often already written down, and every tool
 * researched treats that as the fast path. Each parser returns word times when
 * the format has them (ASS `\k`) and sentence windows when it does not (LRC,
 * SRT, VTT) — `timeFromImport` then decides what to fill.
 * ------------------------------------------------------------------ */

export interface KaraokeImport {
  text: string
  /** One entry per imported line/cue: its start, and word times when known. */
  cues: { start: number; end?: number; words: string[]; wordTimes?: number[] }[]
  source: 'lrc' | 'srt' | 'vtt' | 'ass'
}

const TAGS = /\{[^}]*\}/g
const HTMLISH = /<[^>]+>/g

export function parseLrc (src: string): KaraokeImport {
  const cues: KaraokeImport['cues'] = []
  for (const lineRaw of String(src || '').split(/\r?\n/)) {
    const line = lineRaw.trim()
    if (!line) continue
    const stamps = [...line.matchAll(/\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g)].map(m => {
      const frac = m[3] ? Number(`0.${m[3]}`.slice(0, 5)) : 0
      return Number(m[1]) * 60 + Number(m[2]) + frac
    })
    if (!stamps.length) continue
    const text = line.replace(/\[[^\]]*\]/g, '').trim()
    if (!text) continue
    for (const start of stamps) cues.push({ start, words: text.split(/\s+/).filter(Boolean) })
  }
  cues.sort((a, b) => a.start - b.start)
  for (let i = 0; i < cues.length; i++) {
    const next = cues[i + 1]
    if (next) cues[i].end = Math.max(cues[i].start + 0.4, next.start - 0.15)
  }
  return { text: cues.map(c => c.words.join(' ')).join('\n'), cues, source: 'lrc' }
}

function timeToSeconds (raw: string): number {
  const m = /(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})|(\d{1,2}):(\d{2})[.,](\d{1,3})/.exec(raw.trim())
  if (!m) return NaN
  return m[1] !== undefined
    ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(`0.${m[4]}`)
    : Number(m[5]) * 60 + Number(m[6]) + Number(`0.${m[7]}`)
}

/** SubRip and WebVTT share the shape: `start --> end` then 1..3 text lines. */
export function parseSubtitles (src: string, source: 'srt' | 'vtt' = 'srt'): KaraokeImport {
  const text0 = String(src || '').replace(HTMLISH, '')
  const cues: KaraokeImport['cues'] = []
  for (const block of text0.split(/\r?\n\s*\r?\n/)) {
    const lines = block.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
    if (!lines.length) continue
    const idx = lines.findIndex(l => l.includes('-->'))
    if (idx < 0) continue
    const [a, b] = lines[idx].split('-->')
    const start = timeToSeconds(a || '')
    const end = timeToSeconds(b || '')
    const words = lines.slice(idx + 1).join(' ').replace(/\{[^}]*\}/g, '').trim().split(/\s+/).filter(Boolean)
    if (!Number.isFinite(start) || !words.length) continue
    cues.push({ start, end: Number.isFinite(end) ? end : undefined, words })
  }
  cues.sort((a, b) => a.start - b.start)
  return { text: cues.map(c => c.words.join(' ')).join('\n'), cues, source }
}

/** ASS/SSA with karaoke tags: `\k`, `\kf`, `\K`, `\ko` and `\kn` carry a
 *  *duration in centiseconds* for the syllable that follows them, which is
 *  exactly a per-word clock. The render writes the same tags, so importing a
 *  fansub karaoke file reproduces its timing almost exactly. */
export function parseAssKaraoke (src: string): KaraokeImport {
  const cues: KaraokeImport['cues'] = []
  for (const lineRaw of String(src || '').split(/\r?\n/)) {
    const line = lineRaw.trim()
    if (!/^Dialogue:/i.test(line)) continue
    const parts = line.slice(line.indexOf(':') + 1).split(',')
    if (parts.length < 10) continue
    const start = timeToSeconds(parts[1] || '')
    const end = timeToSeconds(parts[2] || '')
    if (!Number.isFinite(start)) continue
    const body = parts.slice(9).join(',').replace(/\\N/gi, '\n')
    // Karaoke tags live inside an override block; the text up to the next
    // block is the syllable they time.
    const chunks: { dur: number; text: string }[] = []
    const re = /\{([^}]*)\}([^\{]*)/g
    let m: RegExpExecArray | null
    let last = 0
    let head = ''
    while ((m = re.exec(body))) {
      if (m.index !== last) head += body.slice(last, m.index) // tags before the first \k
      const tags = m[1]
      const text = m[2]
      const km = /\\k[foan]?\s*(-?\d+)/i.exec(tags)
      if (km) {
        const prev = chunks[chunks.length - 1]
        if (prev && !prev.text) prev.text = text
        else chunks.push({ dur: Math.max(0, Number(km[1])) / 100, text })
        last = re.lastIndex
        continue
      }
      head += text
      last = re.lastIndex
    }
    head += body.slice(last)
    if (!chunks.length) continue
    const words: string[] = []
    const wordTimes: number[] = []
    let t = start
    for (const c of chunks) {
      // One chunk can hold several words ("\k40 two words"); they share it.
      const list = (head + ' ' + c.text).trim().split(/\s+/).filter(Boolean)
      head = ''
      const own = c.text.trim().split(/\s+/).filter(Boolean)
      const use = own.length ? own : list.length ? list : []
      const step = c.dur / Math.max(1, use.length)
      use.forEach((w, i) => {
        words.push(w)
        wordTimes.push(msRound(t + i * step))
      })
      t += c.dur
    }
    if (!words.length) continue
    cues.push({ start: msRound(start), end: Number.isFinite(end) ? msRound(end) : undefined, words, wordTimes })
  }
  cues.sort((a, b) => a.start - b.start)
  return { text: cues.map(c => c.words.join(' ')).join('\n'), cues, source: 'ass' }
}

/** Turn an import into a `words` array for the *current* text.
 *
 *  Two cases: the imported words match the slide's words (the usual one — the
 *  user pasted the same lyrics), then every word gets its own time; when they
 *  do not match, the cue boundaries are mapped onto the text by character
 *  offset, so a plain LRC still times the sentences and the words share the
 *  window. Times come back relative to `origin`, the clip's IN point. */
export function timeFromImport (
  tokens: KaraokeToken[], text: string, imp: KaraokeImport, origin: number, opts: { maxWords?: number; leadIn?: number; tail?: number } = {},
): { words: KaraokeWord[]; filled: number } {
  const all: string[] = []
  const times: number[] = []
  for (const c of imp.cues) {
    const has = c.wordTimes && c.wordTimes.length === c.words.length
    c.words.forEach((w, i) => {
      all.push(w)
      times.push(has ? (c.wordTimes as number[])[i] : c.start)
    })
  }
  const out: KaraokeWord[] = tokens.map(() => ({}))
  if (!all.length || !tokens.length) return { words: out, filled: 0 }
  // Align by word sequence when the text matches; fall back to proportional.
  const equal = all.length === tokens.length && all.every((w, i) => normWord(w) === normWord(tokens[i].w))
  let filled = 0
  if (equal) {
    times.forEach((t, i) => {
      if (i < out.length) {
        out[i] = { ...out[i], t: msRound(Math.max(0, t - origin)) }
        filled++
      }
    })
    // A cue is a sentence: break where each cue's last word ends.
    let cursor = 0
    for (const c of imp.cues) {
      cursor += c.words.length
      const i = cursor - 1
      if (i >= 0 && i < out.length && cursor < all.length) out[i] = { ...out[i], br: 1 }
    }
    return { words: makeContinuous(out), filled }
  }
  if (imp.source === 'ass' && all.length !== tokens.length) {
    // Karaoke file, different wording: keep the shape, scale the clocks.
    const m = Math.max(1, all.length - 1)
    const o = Math.max(1, tokens.length - 1)
    tokens.forEach((tok, i) => {
      const src = Math.round((i / m) * o)
      const t = times[Math.min(all.length - 1, Math.max(0, src))]
      if (Number.isFinite(t)) {
        out[i] = { ...out[i], t: msRound(Math.max(0, t - origin)) }
        filled++
      }
    })
    return { words: makeContinuous(out), filled }
  }
  // Cue text mapped onto our text by character offset: each cue's first word
  // starts at its time, so the sentences land where the file says and the
  // words split the gap (the two-pass rule: lines first, words second).
  let search = 0
  const marks: { i: number; t: number }[] = []
  const tails: (number | undefined)[] = []
  const cues = imp.cues
  for (const c of cues) {
    const head = normWord(c.words[0] || '')
    if (!head) continue
    let at = -1
    for (let i = search; i < tokens.length; i++) {
      if (normWord(tokens[i].w) === head) {
        at = i
        break
      }
    }
    if (at < 0) continue
    marks.push({ i: at, t: msRound(Math.max(0, c.start - origin)) })
    // Walk the cue's own words forward to find where it ends in our text.
    let tail = at
    let k = 1
    while (k < c.words.length && tail + 1 < tokens.length && tail - at <= c.words.length + 6) {
      const want = normWord(c.words[k])
      let found = -1
      for (let j = tail + 1; j <= Math.min(tokens.length - 1, tail + 4); j++) {
        if (normWord(tokens[j].w) === want) {
          found = j
          break
        }
      }
      if (found < 0) break
      tail = found
      k++
    }
    tails.push(tail > at ? tail : undefined)
    search = at + 1
  }
  if (!marks.length) return { words: out, filled: 0 }
  for (let k = 0; k < marks.length; k++) {
    out[marks[k].i] = { ...out[marks[k].i], t: marks[k].t }
    filled++
    // The tail anchor: the cue's last word sits one slot before the cue ends,
    // which is what keeps an LRC's line length instead of stretching the song.
    const tail = tails[k]
    if (tail !== undefined && tail > marks[k].i) {
      const stop = cues[k].end !== undefined ? cues[k].end as number : marks[k].t + origin + 2.5
      out[tail] = { ...out[tail], t: msRound(Math.max(marks[k].t + 0.4, stop - origin - 0.35)), br: 1 }
      filled++
    } else if (k + 1 < marks.length) {
      // No tail matched: break before the next cue's head instead, so the
      // sentences still line up with the file.
      const prev = marks[k + 1].i - 1
      if (prev > marks[k].i) out[prev] = { ...out[prev], br: 1 }
    }
  }
  return { words: makeContinuous(out), filled }
}

const normWord = (w: string) => String(w || '').toLowerCase().replace(/[^0-9a-zà-öø-ÿ]+/g, '')

/** The words the caption text is made of, as an editor writes them back. */
export function rebuildText (tokens: KaraokeToken[], text: string): string {
  // Insertions/deletions are done on the text itself; this only normalises the
  // whitespace of a rebuilt block so the token spans stay valid.
  void tokens
  return String(text || '').replace(/[ \t]+\r?\n/g, '\n').replace(/\r?\n[ \t]+/g, '\n')
}

/** Where the karaoke ends (for the "the text outlives the slide" warning). */
export function planEnd (plan: KaraokePlan): number {
  return plan.sentences.length ? plan.sentences[plan.sentences.length - 1].end : 0
}

// ---------------------------------------------------------------------------
// The strip's zoom
//
// Every lane of the timing strip is laid out as a percent of the clip (`t/span`),
// which is what makes a zoom cheap: the strip is simply drawn `zoom` times as
// wide inside a scroller and the same percentages land further apart, so blocks,
// handles, dividers and `pointerTime` all stay exact without a second coordinate
// system. What *does* have to follow the zoom is anything measured in screen
// pixels instead of in time — how dense the ruler is, and the floor that keeps a
// one-word sentence grabbable — so those are pure numbers here, where
// backend/tests/test_karaoke_twin.py can pin them.
// ---------------------------------------------------------------------------

/**
 * The piece of the *file* the karaoke clock runs on. A movie can be cut to an
 * IN/OUT pair, and every timing in `karaokeData` is a second inside the kept
 * region (never a second of the file) — which is what lets a re-cut leave the words
 * where they are. `renderer.video_trim_window` applies the same rules to the
 * segment it builds, so reading them here too is what keeps the strip, the video
 * element and the MP4 telling one story; `backend/tests/test_karaoke_twin.py`
 * measures the pair against each other.
 */
export interface KaraokeClipWindow {
  /** Where the strip's zero is, in file seconds: the IN point. */
  start: number
  /** The strip's whole width: the kept region, or the item's length if it was never cut. */
  span: number
    /** File time at which the media element has to stop, or `null` when the movie was
   *  never cut and so plays to the end of the file. */
  cutAt: number | null
}

export function karaokeClipWindow (
  item: { trimStart?: number; trimEnd?: number; duration?: number },
  kept?: number
): KaraokeClipWindow {
  const start = Math.max(0, Number(item?.trimStart) || 0)
  const out = Number(item?.trimEnd) || 0
  const stored = Number(item?.duration) || 0
  const cut = start > 0.01 || out > 0.01
  // Three shapes, in the renderer's order: both handles, an OUT point alone, and an
  // IN point alone. The last means "to the end of the file", and a movie's
  // `duration` *is* the file's length when nobody shortened it — so what is left of
  // it is what the strip has to show. Answering `duration` there would be the same
  // mistake in miniature: seconds of movie that never reaches the screen, timed
  // against anyway.
  const visible = out > 0 ? Math.max(0.5, out - start) : start > 0 ? Math.max(0.5, stored - start) : 0
  const span = Math.max(0.5, Number(kept) > 0 ? Number(kept) : (cut ? visible : (stored || visible || 5)))
  // Only a *cut* movie has somewhere to stop. An untrimmed one plays to the end of
  // the file — `duration` is a floor for a movie, never a cap — so `null` is the
  // honest answer there, and the popup behaves exactly as it did before.
  return { start, span, cutAt: cut ? start + span : null }
}

/**
 * What the strip's playhead reads off a media element's clock, and whether the cut
 * has run out. Split out of the effect because "the complete movie plays on after
 * the strip ends" is a bug in this mapping, not in the movie.
 */
export function karaokeClipClock (
  videoTime: number,
  win: KaraokeClipWindow
): { position: number; ended: boolean } {
  const t = Number(videoTime)
  const span = Math.max(0.5, Number(win?.span) || 0.5)
  if (!Number.isFinite(t)) return { position: 0, ended: false }
  const position = Math.min(span, Math.max(0, t - (Number(win?.start) || 0)))
  // `ended` is the pause signal, and only a movie that was actually cut has an OUT
  // point to pause at — `null` there is not zero, so it is tested as a type before
  // it is read as a number. Running out of *strip* is the other thing, and that one
  // parks the playhead at the right edge without touching the picture.
  const cutAt = win?.cutAt
  const ended = typeof cutAt === 'number' && Number.isFinite(cutAt) && t >= cutAt - 0.02
  return { position, ended }
}

/** Narrowest the strip can be: its own box, which already shows the whole clip. */
export const KARAOKE_ZOOM_MIN = 1
/**
 * Widest: a 3-minute clip becomes 12 screens wide. The ceiling is set by the
 * ruler, not by the eye — `karaokeTickStep` has to stay on its ladder (0.01 s is
 * one frame at 25 fps), and past ~12× a 1-second clip would want a finer ladder
 * than the render can even express. backend/tests/test_karaoke_strip.py pins both
 * halves: the clamp, and 40–140 px between ticks across the whole range.
 */
export const KARAOKE_ZOOM_MAX = 12

/** 1 = the whole clip fits the box; the strip's width is this × its container. */
export function clampKaraokeZoom (zoom: number): number {
  const value = Number(zoom)
  if (!Number.isFinite(value)) return KARAOKE_ZOOM_MIN
  return Math.min(KARAOKE_ZOOM_MAX, Math.max(KARAOKE_ZOOM_MIN, Math.round(value * 1000) / 1000))
}

/**
 * Where the scroller has to sit for the playhead to stand under the fixed centre
 * line of the box. The strip used to scroll only when the playhead reached an edge,
 * which meant the thing being timed — the beat just hit — was against the glass
 * while the middle of the box stayed empty; a timeline that follows a transport is
 * read from a line that does not move, with the film slipping under it. So the
 * centre is the target, and `max(0, …)` is not a nicety: below one screenful of
 * content there is nothing to scroll and the scroller refuses a negative offset.
 */
export function karaokeFollowScroll(playheadPx: number, viewportPx: number): number {
  const x = Number(playheadPx)
  const box = Number(viewportPx)
  if (!Number.isFinite(x) || x <= 0 || !Number.isFinite(box) || box <= 0) return 0
  return Math.max(0, x - Math.max(0, box) / 2)
}

/**
 * The ruler's ladder, seconds apart, fine to coarse. The bottom rungs are there so
 * a short clip zoomed to the top of the range still has a tick every ~70 px, and
 * 0.04 is one frame of the render at 25 fps — below that the labels are a lie,
 * because nothing in the MP4 happens between two frames.
 */
const KARAOKE_TICK_STEPS = [0.01, 0.02, 0.04, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]

/** Roughly how wide the strip's box is, and how much room one tick wants. */
const KARAOKE_BOX_PX = 700
const KARAOKE_TICK_PX = 55
/** Labels the ruler is allowed to draw at once, whatever the zoom says it wants. */
const KARAOKE_MAX_TICKS = 139

/**
 * Seconds between ruler ticks for `span` seconds of clip at `zoom`.
 *
 * The zoom is deliberately not part of the *label* maths: a tick is a tick
 * wherever you are, and the only thing that changes is that a wider strip can
 * afford finer rungs of the ladder. Measured against a 700 px box a tick gets
 * about 55 px of its own, which is what the strip's old fixed ladder was aiming
 * at (a 20 s clip still ticks every 2 s, a minute one every 5 s) without its
 * mid-range crowding: a 100 s clip used to be labelled every 5 s, 20 times across
 * the box, 35 px apart, which is not legible at a 7 px font.
 */
export function karaokeTickStep (span: number, zoom: number): number {
  const seconds = Math.max(0.5, Number(span) || 0.5)
  const pxPerSecond = (KARAOKE_BOX_PX * clampKaraokeZoom(zoom)) / seconds
  // Scanned from the short end, so this returns the *finest* rung at least as
  // wide as the spacing we want — the other order answers with 300 to everything.
  const want = KARAOKE_TICK_PX / pxPerSecond
  let chosen = KARAOKE_TICK_STEPS[KARAOKE_TICK_STEPS.length - 1]
  for (const step of KARAOKE_TICK_STEPS) if (step >= want) { chosen = step; break }
  // Every label is a node in a strip that is already `zoom` screens wide, so the
  // ladder gets a ceiling as well as a floor: past ~140 labels the ruler is a bill
  // the browser pays on every frame. Escalating a rung costs legibility nothing —
  // the ticks just stand further apart — so it wins over the pixel target.
  for (let guard = 0; guard < KARAOKE_TICK_STEPS.length; guard += 1) {
    if (seconds / chosen <= KARAOKE_MAX_TICKS) break
    const next = KARAOKE_TICK_STEPS.indexOf(chosen) + 1
    if (next >= KARAOKE_TICK_STEPS.length) break
    chosen = KARAOKE_TICK_STEPS[next]
  }
  return chosen
}

/** Where to put a tick, in percent of the strip — `toPercent` at any zoom. */
export function karaokeTicks (span: number, zoom: number): number[] {
  const seconds = Math.max(0.5, Number(span) || 0.5)
  const step = karaokeTickStep(seconds, zoom)
  const out: number[] = []
  // Rounded to the step's own precision, or the last tick lands on 14.999999.
  const digits = step >= 1 ? 0 : Math.min(3, String(step).split('.')[1]?.length || 0)
  for (let t = 0; t <= seconds + step * 0.001; t += step) out.push(Number(t.toFixed(digits)))
  return out
}

/**
 * A tick's caption: whole seconds stay plain, anything finer is written with the
 * step's own number of decimals. Not fewer — a ruler that reads 10.3 above the
 * tick at 10.25 tells you the divider is somewhere it is not, and the strip is
 * exactly the place people read a time off a mark.
 */
export function karaokeTickLabel (step: number, t: number): string {
  if (step >= 1) return String(Math.round(t))
  const digits = Math.min(3, String(step).split('.')[1]?.replace(/0+$/, '').length || 0)
  return t.toFixed(Math.max(1, digits)).replace(/\.?0+$/, '')
}

/**
 * The narrowest a sentence block may look, as a percent of the strip.
 *
 * 0.8 % of a 700 px box is the ~6 px a pointer needs to hit a handle; divide by
 * the zoom and it stays ~6 px *on screen* however wide the strip is drawn, so a
 * zoomed-in 0.2 s line is not stretched into a lie about its length.
 */
export function karaokeBlockFloor (zoom: number): number {
  return 0.8 / clampKaraokeZoom(zoom)
}
