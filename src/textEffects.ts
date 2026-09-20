// The dynamic text-effect catalogue, shared by every text-animation picker.
//
// One list of 65 effects in three slots (enter / while shown / exit), read
// from registry/text-effects.json — the exact same file the backend's
// renderer and example-clip builder read, so labels and glyphs can never
// drift between the GUI and the MP4.
//
//   engine: dt  = animated drawtext expressions (the classic caption filter)
//   engine: ass = per-clip .ass overlay burned in by libass
//   engine: none = static (the "while" default)
//
// Saved projects store the *label*, never an index. Projects saved before
// text effects existed stored xfade transition labels in textEnter/textExit;
// normalizeTextEffect() maps the meaningful ones across and degrades the rest
// to the historic behaviour (Fade in / Fade out).

import registryData from '../registry/text-effects.json'

export type TextEffectSlot = 'enter' | 'while' | 'exit'
export type TextEffectEngine = 'dt' | 'ass' | 'none'

export interface TextEffectParamDef { name: string; default: string }
export interface TextEffectEntry {
  id: string
  label: string
  group: string
  slot: TextEffectSlot
  engine: TextEffectEngine
  symbol: string
  seconds: number
  params?: TextEffectParamDef[]
}

export const textEffectEntries = (registryData as { effects: TextEffectEntry[] }).effects

// Labels are unique across the whole catalogue (registry/text-effects.json says
// so, and the backend tests check it), so these bare-label maps are safe. The
// slot-qualified index exists for normalizeTextEffect: a label must only be
// accepted for the slot it belongs to.
const byLabel = new Map<string, TextEffectEntry>()
const bySlotLabel = new Map<string, TextEffectEntry>()
const groupIndex: Record<TextEffectSlot, Record<string, string[]>> = {
  enter: {}, while: {}, exit: {},
}
const symbols: Record<string, string> = {}
const defaultSeconds: Record<string, number> = {}
const defaultParams: Record<string, TextEffectParamDef[]> = {}

const slotKey = (slot: TextEffectSlot, label: string) => `${slot}\u0000${label}`

for (const entry of textEffectEntries) {
  byLabel.set(entry.label, entry)
  bySlotLabel.set(slotKey(entry.slot, entry.label), entry)
  ;(groupIndex[entry.slot][entry.group] = groupIndex[entry.slot][entry.group] || []).push(entry.label)
  symbols[entry.label] = entry.symbol
  defaultSeconds[entry.label] = entry.seconds
  if (entry.params?.length) defaultParams[entry.label] = entry.params
}

export const textEffectDefaultSeconds = defaultSeconds

/** Grouped labels for one slot, in registry order (drives pickers). */
export function textEffectGroupsFor(slot: TextEffectSlot): Record<string, string[]> {
  return groupIndex[slot]
}

export function allTextEffects(slot: TextEffectSlot): string[] {
  return Object.values(groupIndex[slot]).flat()
}

export function textEffectParams(label: string | undefined): TextEffectParamDef[] {
  return defaultParams[String(label || '').trim()] || []
}

export const DEFAULT_ENTER = 'Fade'
export const DEFAULT_WHILE = 'None (static)'
export const DEFAULT_EXIT = 'Fade out'
export const WHILE_SPEED_DEFAULT = 2

// The three "no animation" labels, one per slot: labels have to be unique
// across the whole catalogue (the maps above, the preview slugs and the backend
// lookup all key on them), so the exit entry reads "None (hold)" — the caption
// holds visible to the last frame of its window.
export const NONE_ENTER = 'None'
export const NONE_WHILE = DEFAULT_WHILE
export const NONE_EXIT = 'None (hold)'

/**
 * Legacy labels → catalogue labels. Old projects stored xfade transition
 * names ("Wipe left", "Slide up", …) in textEnter/textExit; every one of
 * them *rendered* as a plain fade. The direction names map onto their text
 * equivalents so they finally do what the chip says; anything else degrades
 * to the slot default, exactly like unknown labels.
 */
const LEGACY_ALIAS: [RegExp, (slot: TextEffectSlot) => string][] = [
  // Enter and exit were both called "None" for a while; the label is now
  // slot-specific, so a bare "None" means the entry of the slot it sits in.
  [/^none$/i, slot => (slot === 'exit' ? NONE_EXIT : slot === 'while' ? NONE_WHILE : NONE_ENTER)],
  [/wipe.*(left|←)/i, () => 'Wipe from left'],
  [/wipe.*(right|→)/i, () => 'Wipe from right'],
  [/wipe.*(up|↑)/i, () => 'Wipe from top'],
  [/wipe.*(down|↓)/i, () => 'Wipe from bottom'],
  [/slide.*(left|←)/i, () => 'Slide from left'],
  [/slide.*(right|→)/i, () => 'Slide from right'],
  [/slide.*(up|↑)/i, () => 'Slide from bottom'],
  [/slide.*(down|↓)/i, () => 'Slide from top'],
  [/^fade/i, slot => (slot === 'exit' ? DEFAULT_EXIT : DEFAULT_ENTER)],
]

export function normalizeTextEffect(label: string | undefined, slot: TextEffectSlot): string {
  const raw = String(label || '').trim()
  if (bySlotLabel.has(slotKey(slot, raw))) return raw
  for (const [pattern, resolve] of LEGACY_ALIAS) {
    if (pattern.test(raw)) return resolve(slot)
  }
  return slot === 'while' ? DEFAULT_WHILE : slot === 'exit' ? DEFAULT_EXIT : DEFAULT_ENTER
}

/** True for the "no animation" entries (engine `none`): the caption is simply
 * there, so its side has no duration to set and nothing to animate in the quick
 * preview. Also accepts the legacy plain "None" in the while/exit slots. */
export function isStaticTextEffect(label: string | undefined): boolean {
  const raw = String(label || '').trim()
  const entry = byLabel.get(raw)
  return entry ? entry.engine === 'none' : raw.toLowerCase() === 'none'
}

/** Chip and tile text: the parenthetical that keeps the three "None" labels
 * unique is picker detail — everywhere else they all just read "None". */
export function textEffectDisplayName(label: string | undefined): string {
  return isStaticTextEffect(label) ? 'None' : String(label || '')
}

/** Glyph for a timeline chip. Unknown labels (they should not happen after
 * normalization) fall back to the group glyph of the transition catalogue. */
export function textEffectSymbol(label: string | undefined): string {
  const known = symbols[String(label || '').trim()]
  if (known) return known
  const n = (label || '').toLowerCase()
  if (n.includes('left')) return '←'
  if (n.includes('right')) return '→'
  if (n.includes('up')) return '↑'
  if (n.includes('down')) return '↓'
  if (n.includes('wipe')) return '◧'
  return '◐'
}

// ---------------------------------------------------------------------------
// Cached backend examples: /api/text-effects/<slug>.mp4
// The slug rule must stay identical to backend slugify(): lowercase, every
// run of non-alphanumerics becomes one hyphen.
// ---------------------------------------------------------------------------

export function textEffectSlug(label: string) {
  const slug = (label || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return slug || 'effect'
}

export function textEffectPreviewUrl(label: string) {
  return `/api/text-effects/${textEffectSlug(label)}.mp4`
}
