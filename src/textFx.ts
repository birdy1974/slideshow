// Stacked text effects: the data model of MediaItem.textFx and the registry
// (registry/text-motion.json) as the editor sees it.
//
// A caption's textFx is an ordered list of layers. The phase of a layer (the
// Enter / While shown / Exit lane it sits in) comes from its effect; order
// inside a lane only matters for colour (the top layer wins). Projects store
// effect ids, never labels. Items saved before the stack existed carry the v1
// fields (three single-effect slots + the animation toggles);
// migrateLegacyTextFx() turns them into layers when a project is opened —
// the same mapping as legacy_to_stack() in backend/app/text_motion.py
// (backend/tests/test_text_motion_twin.py checks both agree).

import registryData from '../registry/text-motion.json'
import type { EffectDef, Phase, Registry, TextFxLayer, UnitLevel } from './textMotionCore'
import { normalizeTextEffect } from './textEffects'

export type { EffectDef, Phase, TextFxLayer, UnitLevel }

export interface MotionPreset {
  id: string
  label: string
  symbol: string
  description?: string
  font?: { family: string; bold?: boolean; italic?: boolean }
  frame?: { background: string; background2: string; transition: string; time: number; start: number }
  layers: TextFxLayer[]
  mine?: boolean
}

const registry = registryData as unknown as Registry & { presets: MotionPreset[] }

export const EFFECT_LIST: EffectDef[] = registry.effects
export const EFFECTS: Record<string, EffectDef> = Object.fromEntries(EFFECT_LIST.map(e => [e.id, e]))
export const CATEGORIES = registry.categories
export const CATEGORY_BY_ID: Record<string, { id: string; label: string; symbol: string }> = Object.fromEntries(CATEGORIES.map(c => [c.id, c]))
export const PRESETS: MotionPreset[] = registry.presets || []

export const PHASES: { id: Phase; label: string; add: string; empty: string }[] = [
  { id: 'in', label: 'Enter', add: 'Add enter effect', empty: 'Appears instantly.' },
  { id: 'hold', label: 'While shown', add: 'Add loop effect', empty: 'Static while shown.' },
  { id: 'out', label: 'Exit', add: 'Add exit effect', empty: 'Disappears instantly.' },
]
export const PHASE_LABEL: Record<Phase, string> = { in: 'Enter', hold: 'While shown', out: 'Exit' }
export const UNIT_LABEL: Record<string, string> = { char: 'Letter', word: 'Word', line: 'Line', text: 'Text' }
export const UNIT_SHORT: Record<string, string> = { char: 'CH', word: 'W', line: 'L', text: 'T' }
export const ORDER_LABEL: Record<string, string> = { forward: 'Forward', reverse: 'Reverse', center: 'From the centre', edges: 'From the edges', random: 'Random' }
export const LOOP_LABEL: Record<string, string> = { loop: 'Loop', pingpong: 'Ping-pong', once: 'Once' }

export function effectOf(layer: TextFxLayer | undefined): EffectDef | undefined {
  return layer ? EFFECTS[layer.effect] : undefined
}
export function phaseOf(layer: TextFxLayer): Phase {
  return (EFFECTS[layer.effect]?.phase || 'hold') as Phase
}

/** Effects that need a text frame with a colour change (background A → B). */
export function effectNeedsBg(fx: EffectDef | undefined): boolean {
  return Boolean(fx && (fx.needsBg || fx.bg))
}
/** Effects timed to the colour change when there is one. */
export function effectSyncsToBg(fx: EffectDef | undefined): boolean {
  return Boolean(fx && (fx.needsBg || fx.bg || fx.sync === 'bg'))
}

let uid = 0
export function newLayerId(): string {
  uid += 1
  return `fx-${Date.now().toString(36)}-${uid.toString(36)}-${Math.floor(Math.random() * 46656).toString(36)}`
}
export const withIds = (layers: TextFxLayer[]): TextFxLayer[] => layers.map(l => ({ ...JSON.parse(JSON.stringify(l)), id: newLayerId() }))

export function layersOf(stack: TextFxLayer[] | undefined, phase: Phase): TextFxLayer[] {
  return (stack || []).filter(l => EFFECTS[l.effect] && EFFECTS[l.effect].phase === phase)
}

/** Insert a layer after the last layer of its phase (stack order = lane order). */
export function insertLayer(stack: TextFxLayer[], layer: TextFxLayer): TextFxLayer[] {
  const order: Phase[] = ['in', 'hold', 'out']
  const phase = phaseOf(layer)
  let at = -1
  stack.forEach((l, i) => { if (order.indexOf(phaseOf(l)) <= order.indexOf(phase)) at = i })
  const next = stack.slice()
  next.splice(at + 1, 0, layer)
  return next
}

/** Storyline / detailed-list chip: first layer's symbol + "+N" for the rest of that lane. */
export function laneChip(stack: TextFxLayer[] | undefined, phase: Phase): { symbol: string; label: string; more: number; title: string } {
  const live = layersOf(stack, phase).filter(l => !l.muted)
  const first = live[0] ? EFFECTS[live[0].effect] : undefined
  const title = (stack || []).filter(l => EFFECTS[l.effect])
    .map(l => `${PHASE_LABEL[EFFECTS[l.effect].phase as Phase]}: ${EFFECTS[l.effect].label}${l.muted ? ' (muted)' : ''}`).join('\n') || 'No text animation'
  return { symbol: first ? first.symbol : '∅', label: first ? first.label : 'None', more: Math.max(0, live.length - 1), title }
}

/** What adding (or swapping in) `effectId` would do to `stack` — the tile badge. */
export function candidateConflict(stack: TextFxLayer[], effectId: string, opts: { replacingId?: string; hasBg?: boolean } = {}): string | null {
  const fx = EFFECTS[effectId]
  if (!fx) return null
  if (effectNeedsBg(fx) && opts.hasBg === false) return 'needs colour B'
  const live = stack.filter(l => !l.muted && l.id !== opts.replacingId && EFFECTS[l.effect])
  if (!fx.content) {
    const whole = live.find(l => ['count', 'countdown', 'swap'].includes(EFFECTS[l.effect].content?.type))
    if (whole && fx.unit !== 'text') return `whole text (${EFFECTS[whole.effect].label})`
    return null
  }
  const clash = live.find(l => EFFECTS[l.effect].content && EFFECTS[l.effect].phase === fx.phase)
  if (clash) return `replaces ${EFFECTS[clash.effect].label}`
  if (['count', 'countdown', 'swap'].includes(fx.content.type) && live.some(l => (l.unit || EFFECTS[l.effect].unit) !== 'text')) return 'runs on the whole text'
  return null
}

/** A browser tile shows the effect alone, framed by the gentlest partners. */
export function sampleStackFor(effectId: string): TextFxLayer[] {
  const fx = EFFECTS[effectId]
  if (!fx) return []
  if (fx.phase === 'in') return [{ id: 's1', effect: effectId }, { id: 's2', effect: 'fade-out', duration: 0.3 }]
  if (fx.phase === 'out') return [{ id: 's1', effect: 'fade', duration: 0.25 }, { id: 's2', effect: effectId }]
  return [{ id: 's1', effect: 'fade', duration: 0.25 }, { id: 's2', effect: effectId }, { id: 's3', effect: 'fade-out', duration: 0.25 }]
}

export function hasMotionPath(stack: TextFxLayer[] | undefined): boolean {
  return (stack || []).some(l => l.effect === 'motion-path' && !l.muted)
}

export const DEFAULT_STACK: TextFxLayer[] = [{ effect: 'fade', duration: 0.5 }, { effect: 'fade-out', duration: 0.5 }]
export const defaultTextFx = (): TextFxLayer[] => withIds(DEFAULT_STACK)

/** Validate a saved stack: unknown effects dropped, numbers clamped, ids added. */
export function normalizeTextFx(raw: unknown): TextFxLayer[] {
  if (!Array.isArray(raw)) return []
  const out: TextFxLayer[] = []
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object' || out.length >= 24) continue
    const e = entry as Record<string, any>
    if (!EFFECTS[e.effect]) continue
    const layer: TextFxLayer = { id: typeof e.id === 'string' && e.id ? e.id : newLayerId(), effect: e.effect }
    if (['char', 'word', 'line', 'text'].includes(e.unit)) layer.unit = e.unit
    for (const [key, lo, hi] of [['duration', 0.02, 120], ['delay', 0, 120], ['stagger', 0, 1], ['intensity', 0, 3]] as const) {
      const v = Number(e[key])
      if (e[key] === undefined || e[key] === null || e[key] === '' || !Number.isFinite(v)) continue
      if (key === 'duration' && v <= 0) continue   // like parse_stack(): no duration = the effect's default
      ;(layer as any)[key] = Math.max(lo, Math.min(hi, v))
    }
    if (['forward', 'reverse', 'center', 'edges', 'random'].includes(e.order)) layer.order = e.order
    if (['loop', 'pingpong', 'once'].includes(e.loop)) layer.loop = e.loop
    if (e.params && typeof e.params === 'object' && !Array.isArray(e.params)) layer.params = { ...e.params }
    if (e.muted === true) layer.muted = true
    if (e.sync === 'bg') layer.sync = 'bg'
    if (Array.isArray(e.range) && e.range.length === 2 && e.range.every((v: unknown) => Number.isInteger(v) && (v as number) >= 0)) layer.range = [Math.min(e.range[0], e.range[1]), Math.max(e.range[0], e.range[1])]
    out.push(layer)
  }
  return out
}

// ---------------------------------------------------------------------------
// Legacy (v1) fields -> stack: mirror of legacy_to_stack() in text_motion.py
// ---------------------------------------------------------------------------
const V1_DEFAULTS: Record<string, string> = { enter: 'Fade', while: 'None (static)', exit: 'Fade out' }
const V1_NONE: Record<string, string[]> = { enter: ['none'], while: ['none (static)', 'none'], exit: ['none (hold)', 'none'] }
const LEGACY_INDEX = new Map<string, string>()
for (const e of EFFECT_LIST) {
  if (e.legacy && e.legacy.label) LEGACY_INDEX.set(`${e.legacy.slot}\u0000${String(e.legacy.label).trim().toLowerCase()}`, e.id)
}

/** Python float(value), or null where float() would raise / give inf or nan. */
function pyFloat(value: unknown): number | null {
  if (value === null || value === undefined) return null
  if (typeof value === 'boolean') return value ? 1 : 0
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const text = String(value).trim()
  if (!text) return null
  const n = Number(text.replace(/_/g, ''))
  return Number.isFinite(n) ? n : null
}
/** _num(value, default) of text_motion.py. */
function numOr(value: unknown, dflt: number): number {
  const n = pyFloat(value)
  return n === null ? dflt : n
}
/** _num(value, value): the number if it parses, else the value itself. */
function pyNum(value: unknown): unknown {
  const n = pyFloat(value)
  return n === null ? value : n
}
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

export interface LegacyDefaults { fxEnter?: string; fxWhile?: string; fxExit?: string; fxWhileSpeed?: number }

export function legacyToStack(item: Record<string, any>, defaults: LegacyDefaults = {}): TextFxLayer[] {
  const title = item.type === 'title'
  const enter = String(item.textFxEnter || item.textEnter || (title ? 'Fade' : defaults.fxEnter || 'Fade'))
  const whileLabel = String(item.textFxWhile || (title ? 'None (static)' : defaults.fxWhile || 'None (static)'))
  const exit = String(item.textFxExit || item.textExit || (title ? 'Fade out' : defaults.fxExit || 'Fade out'))
  const di = clamp(numOr(item.textEnterDuration, 0.5) || 0.5, 0.05, 30)
  const dOut = clamp(numOr(item.textExitDuration, 0.5) || 0.5, 0.05, 30)
  const speedDefault = title ? 2 : (numOr(defaults.fxWhileSpeed, 2) || 2)
  const speed = clamp(numOr(item.textFxWhileSpeed, speedDefault) || speedDefault, 0.4, 12)
  const rawParams: Record<string, unknown> = item.textFxParams && typeof item.textFxParams === 'object' ? item.textFxParams : {}
  const stack: TextFxLayer[] = []
  const lookup = (slot: string, label: string) => LEGACY_INDEX.get(`${slot}\u0000${label.trim().toLowerCase()}`)
  const add = (slot: 'enter' | 'exit', label: string, extra: Partial<TextFxLayer>) => {
    const key = label.trim().toLowerCase()
    if (V1_NONE[slot].includes(key)) return
    const eid = lookup(slot, label) || lookup(slot, normalizeTextEffect(label, slot)) || lookup(slot, V1_DEFAULTS[slot])
    if (eid) stack.push({ effect: eid, ...extra })
  }
  add('enter', enter, { duration: di })
  const whileKey = whileLabel.trim().toLowerCase()
  const fxWhile = lookup('while', whileLabel)
  if (fxWhile && !V1_NONE.while.includes(whileKey)) {
    const entry: TextFxLayer = { effect: fxWhile }
    const e = EFFECTS[fxWhile]
    if ((e.loop || 'loop') !== 'once') entry.duration = speed
    const names = new Set((e.params || []).map(p => p.name))
    const params: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(rawParams)) {
      if (!names.has(k)) continue
      params[k] = (k === 'from' || k === 'to') && fxWhile === 'colour-morph' ? v : pyNum(v)
    }
    if (Object.keys(params).length) entry.params = params
    stack.push(entry)
  }
  if (item.textScaleEnabled) stack.push({ effect: 'resize', params: { from: numOr(item.textScaleFrom, 1), to: numOr(item.textScaleTo, 1.45) } })
  if (item.textRotateEnabled) {
    const entry: TextFxLayer = { effect: 'rotate', params: { from: numOr(item.textRotateFrom, -10), to: numOr(item.textRotateTo, 0) } }
    const speedR = numOr(item.textRotateSpeed, 0) || 0
    if (speedR > 0) entry.duration = speedR
    stack.push(entry)
  }
  if (item.textSquishEnabled) stack.push({ effect: 'squash', params: { from: numOr(item.textSquishFrom, 0.5), to: numOr(item.textSquishTo, 1) } })
  if (item.textColorAnimEnabled) {
    const cFrom = String(item.textColorFrom || item.fontColor || '#ffffff')
    let cTo = String(item.textColorTo || cFrom)
    if (cTo.toLowerCase() === '#ffcc33' && cFrom.toLowerCase() !== '#ffcc33') cTo = cFrom
    if (!stack.some(l => l.effect === 'colour-morph')) stack.push({ effect: 'colour-morph', params: { from: cFrom, to: cTo } })
  }
  if (item.textBouncyEnabled && !stack.some(l => l.effect === 'bouncy')) {
    stack.push({ effect: 'bouncy', params: { height: numOr(item.textBouncyHeight, 12), bounces: numOr(item.textBouncyBounces, 3), damping: numOr(item.textBouncyDamping, 0.35) } })
  }
  if (item.textMoveEnabled) stack.push({ effect: 'motion-path' })
  add('exit', exit, { duration: dOut })
  return stack
}

/** The v1 keys a migrated item no longer carries (textFx is written instead). */
export const LEGACY_TEXT_FX_KEYS = [
  'textFxEnter', 'textFxWhile', 'textFxExit', 'textFxWhileSpeed', 'textFxParams', 'textEnter', 'textExit',
  'textEnterDuration', 'textExitDuration', 'textScaleEnabled', 'textScaleFrom', 'textScaleTo', 'textRotateEnabled',
  'textRotateFrom', 'textRotateTo', 'textRotateSpeed', 'textSquishEnabled', 'textSquishFrom', 'textSquishTo',
  'textColorAnimEnabled', 'textColorFrom', 'textColorTo', 'textBouncyEnabled', 'textBouncyHeight', 'textBouncyBounces',
  'textBouncyDamping', 'textBouncyFrequency', 'textMoveEnabled',
] as const

/** Migrate on load, write textFx only. Items that already have textFx are
 * normalized (and lose any stale v1 keys). */
export function migrateLegacyTextFx<T extends Record<string, any>>(item: T, defaults: LegacyDefaults = {}): T {
  const textFx = Array.isArray(item.textFx) ? normalizeTextFx(item.textFx) : withIds(legacyToStack(item, defaults))
  const next: Record<string, any> = { ...item, textFx }
  for (const key of LEGACY_TEXT_FX_KEYS) delete next[key]
  return next as T
}

// ---------------------------------------------------------------------------
// Favourites, recent and saved presets (per browser, like the transitions)
// ---------------------------------------------------------------------------
const FAV_KEY = 'slideshow.textFx.favourites'
const RECENT_KEY = 'slideshow.textFx.recent'
const PRESET_KEY = 'slideshow.textFx.presets'

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}
function writeJson(key: string, value: unknown) {
  try { window.localStorage.setItem(key, JSON.stringify(value)) } catch { /* private mode */ }
}

export function readFavourites(): string[] { return readJson<string[]>(FAV_KEY, []).filter(id => EFFECTS[id]) }
export function writeFavourites(ids: string[]) { writeJson(FAV_KEY, ids) }
export function readRecent(): string[] { return readJson<string[]>(RECENT_KEY, []).filter(id => EFFECTS[id]) }
export function pushRecent(id: string): string[] {
  const next = [id, ...readRecent().filter(x => x !== id)].slice(0, 16)
  writeJson(RECENT_KEY, next)
  return next
}
export function readSavedPresets(): MotionPreset[] {
  return readJson<MotionPreset[]>(PRESET_KEY, []).filter(p => p && Array.isArray(p.layers)).map(p => ({ ...p, mine: true, layers: normalizeTextFx(p.layers) }))
}
export function writeSavedPresets(presets: MotionPreset[]) {
  writeJson(PRESET_KEY, presets.map(({ mine, ...p }) => ({ ...p, layers: p.layers.map(({ id, ...l }) => l) })))
}

/** Presets that look intentional on this caption (colour-change looks need a text frame). */
export function presetsFor(isFrame: boolean): MotionPreset[] {
  return PRESETS.filter(p => isFrame || !p.frame)
}
