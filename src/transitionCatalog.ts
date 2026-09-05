// The transition catalogue, shared by every picker in the app.
//
// Two catalogues are merged into one list of 191 entries:
//   1. native xfade (FFmpeg built-in, 58) — grouped here in source order
//   2. GL transitions (gl-transitions.com, ported to the custom ffmpeg via
//      ffmpeg-patch/, 133) — read from registry/transitions.json, the exact
//      same file the backend reads, so names/groups/params can never drift.
//
// Saved projects store the *label*, never an index.

import glRegistryData from '../registry/transitions.json'

export interface GLParamDef { name: string; default: string; min?: string; max?: string; step?: string; hint?: string }
export interface GLEntry { id: string; label: string; group: string; author?: string; params: GLParamDef[] }

// 1) Native xfade catalogue (FFmpeg built-in) — 58 transitions, unchanged from ayosec/FFmpeg docs
export const nativeTransitionGroups: Record<string, string[]> = {
  'Fades & blends': ['Fade', 'Fade black', 'Fade white', 'Fade grays', 'Fade fast', 'Fade slow', 'Dissolve', 'Distance', 'Pixelize', 'H blur'],
  'Wipes': ['Wipe left', 'Wipe right', 'Wipe up', 'Wipe down', 'Wipe top-left', 'Wipe top-right', 'Wipe bottom-left', 'Wipe bottom-right'],
  'Slides & smooth': ['Slide left', 'Slide right', 'Slide up', 'Slide down', 'Smooth left', 'Smooth right', 'Smooth up', 'Smooth down'],
  'Shapes': ['Circle crop', 'Rectangle crop', 'Circle open', 'Circle close', 'Vertical open', 'Vertical close', 'Horizontal open', 'Horizontal close', 'Radial'],
  'Slices': ['Diagonal top-left', 'Diagonal top-right', 'Diagonal bottom-left', 'Diagonal bottom-right', 'Horizontal left slice', 'Horizontal right slice', 'Vertical up slice', 'Vertical down slice'],
  'Squeeze, wind & zoom': ['Squeeze horizontal', 'Squeeze vertical', 'Zoom in', 'Horizontal left wind', 'Horizontal right wind', 'Vertical up wind', 'Vertical down wind'],
  'Cover & reveal': ['Cover left', 'Cover right', 'Cover up', 'Cover down', 'Reveal left', 'Reveal right', 'Reveal up', 'Reveal down'],
}

// 2) GL transitions — ported GLSL from https://github.com/scriptituk/xfade-easing#ported-glsl-transitions
// and https://gl-transitions.com/. Entry order defines the visual group order.
export const glEntries = (glRegistryData as { gl: GLEntry[] }).gl
export const glTransitionGroups: Record<string, string[]> = {}
export const glParams: Record<string, GLParamDef[]> = {}
for (const e of glEntries) {
  (glTransitionGroups[e.group] = glTransitionGroups[e.group] || []).push(e.label)
  glParams[e.label] = e.params || []
}

// Combined catalogue (used for global search / random / bulk assignment).
export const transitionGroups: Record<string, string[]> = { ...nativeTransitionGroups, ...glTransitionGroups }
export const transitions = Object.values(transitionGroups).flat()
export const nativeTransitions = Object.values(nativeTransitionGroups).flat()
export const glTransitions = Object.values(glTransitionGroups).flat()

export const totalTransitionCount = transitions.length

// Easing catalogue for the custom xfade-easing build (native-like + CSS + extra)
export const easingGroups: Record<string, string[]> = {
  'Linear': ['linear'],
  'Standard (in/out/in-out)': ['quadratic', 'quadratic-in', 'quadratic-out', 'quadratic-in-out', 'cubic', 'cubic-in', 'cubic-out', 'cubic-in-out', 'quartic', 'quartic-in', 'quartic-out', 'quartic-in-out', 'quintic', 'quintic-in', 'quintic-out', 'quintic-in-out', 'sinusoidal', 'sinusoidal-in', 'sinusoidal-out', 'sinusoidal-in-out', 'exponential', 'exponential-in', 'exponential-out', 'exponential-in-out', 'circular', 'circular-in', 'circular-out', 'circular-in-out'],
  'Elastic / Back / Bounce': ['elastic', 'elastic-in', 'elastic-out', 'elastic-in-out', 'back', 'back-in', 'back-out', 'back-in-out', 'bounce', 'bounce-in', 'bounce-out', 'bounce-in-out', 'squareroot', 'cuberoot', 'flipelastic', 'flipback'],
  'CSS': ['ease', 'ease-in', 'ease-out', 'ease-in-out', 'cubic-bezier(0.42,0,0.58,1)', 'cubic-bezier(0.25,0.1,0.25,1)', 'step-start', 'step-end'],
}
export const easings = Object.values(easingGroups).flat()
export const EASING_DEFAULT = 'linear'

/** Cheap glyph hint so a row can show what a transition does without loading anything. */
export function transitionSymbol(name: string) {
  const n = (name || '').toLowerCase()
  if (n.startsWith('gl')) return '✦'
  if (n.includes('left')) return '←'
  if (n.includes('right')) return '→'
  if (n.includes('up')) return '↑'
  if (n.includes('down')) return '↓'
  if (n.includes('circle') || n.includes('radial')) return '◉'
  if (n.includes('zoom')) return '⊕'
  if (n.includes('dissolve') || n.includes('pixel')) return '░'
  if (n.includes('fade')) return '◐'
  return '◇'
}

export function isGLTransition(name: string) {
  return (name || '').startsWith('GL ·') || (name || '').startsWith('gl_')
}

/** Direction class for the CSS approximation of an FFmpeg transition. */
export function transitionDirection(name: string) {
  const n = (name || '').toLowerCase()
  if (n.includes('left')) return 'from-left'
  if (n.includes('right')) return 'from-right'
  if (n.includes('up')) return 'from-up'
  if (n.includes('down')) return 'from-down'
  return 'fade'
}

export function getGLParams(name: string): GLParamDef[] {
  return glParams[name] || []
}

// ---------------------------------------------------------------------------
// Cached backend previews
//
// /api/transition-previews/<slug>.mp4 serves a short clip of the transition
// between two synthetic example frames. It is rendered once and then stored on
// the config volume, so the slug has to be stable and identical to the one the
// backend derives from the same label.
// ---------------------------------------------------------------------------

export function transitionSlug(label: string) {
  const slug = (label || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return slug || 'transition'
}

export function transitionPreviewUrl(label: string) {
  return `/api/transition-previews/${transitionSlug(label)}.mp4`
}

// ---------------------------------------------------------------------------
// Recent / favourite transitions (per browser, not per project)
// ---------------------------------------------------------------------------

const RECENT_KEY = 'slideshow.transitions.recent'
const FAVOURITE_KEY = 'slideshow.transitions.favourites'
const RECENT_LIMIT = 8

function readList(key: string): string[] {
  try {
    const raw = window.localStorage.getItem(key)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string').filter(x => transitions.includes(x)) : []
  } catch {
    return []
  }
}
function writeList(key: string, items: string[]) {
  try { window.localStorage.setItem(key, JSON.stringify(items)) } catch { /* private mode / quota — non-fatal */ }
}

export function loadRecentTransitions() { return readList(RECENT_KEY) }
export function loadFavouriteTransitions() { return readList(FAVOURITE_KEY) }

/** Most-recent-first, de-duplicated, capped. Called whenever a transition is picked. */
export function rememberTransition(label: string) {
  if (!transitions.includes(label)) return
  const next = [label, ...readList(RECENT_KEY).filter(x => x !== label)].slice(0, RECENT_LIMIT)
  writeList(RECENT_KEY, next)
}

export function toggleFavouriteTransition(label: string) {
  const current = readList(FAVOURITE_KEY)
  const next = current.includes(label) ? current.filter(x => x !== label) : [...current, label]
  writeList(FAVOURITE_KEY, next)
  return next.includes(label)
}
