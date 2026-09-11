// The text-effect browser: a compact chip that opens a grouped, searchable
// popover with real rendered examples — the text counterpart of
// TransitionPicker, consuming the same backend cache pattern
// (/api/text-effects/<slug>.mp4, rendered once per effect, then static).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Loader2, RefreshCw, Search, X } from 'lucide-react'
import {
  allTextEffects, textEffectGroupsFor, textEffectParams, textEffectPreviewUrl,
  textEffectSlug, textEffectSymbol, type TextEffectSlot,
} from './textEffects'

// ---------------------------------------------------------------------------
// Cached backend examples: one module-level status cache per page.
// ---------------------------------------------------------------------------

export type EffectPreviewState = 'ready' | 'pending' | 'failed'

export interface EffectPreviewStatus {
  total: number; ready: number; pending: number; failed: number
  building: boolean; buildDone: number; buildTotal: number; hasFfmpeg: boolean
  items: Record<string, { label: string; slot: string; kind: string; status: EffectPreviewState; error: string }>
}

let effectStatusCache: EffectPreviewStatus | null = null
let effectInflight: Promise<EffectPreviewStatus | null> | null = null
const effectListeners = new Set<(s: EffectPreviewStatus | null) => void>()

function emitEffectStatus(status: EffectPreviewStatus | null) {
  effectStatusCache = status
  effectListeners.forEach(listener => listener(status))
}

export function refreshEffectStatus(): Promise<EffectPreviewStatus | null> {
  if (effectInflight) return effectInflight
  effectInflight = fetch('/api/text-effects/status')
    .then(r => (r.ok ? r.json() as Promise<EffectPreviewStatus> : Promise.resolve(null)))
    .then(data => { emitEffectStatus(data); return data })
    .catch(() => { emitEffectStatus(null); return null })
    .finally(() => { effectInflight = null })
  return effectInflight
}

export async function buildAllEffectPreviews() {
  try {
    const response = await fetch('/api/text-effects/build', { method: 'POST' })
    if (response.ok) emitEffectStatus(await response.json() as EffectPreviewStatus)
  } catch { /* backend offline — the picker stays on its CSS approximations */ }
}

export function useEffectStatus(enabled: boolean) {
  const [status, setStatus] = useState<EffectPreviewStatus | null>(effectStatusCache)
  useEffect(() => {
    if (!enabled) return
    effectListeners.add(setStatus)
    if (effectStatusCache) setStatus(effectStatusCache)
    if (!effectStatusCache && !effectInflight) void refreshEffectStatus()
    return () => { effectListeners.delete(setStatus) }
  }, [enabled])
  useEffect(() => {
    if (!enabled || !status?.building) return
    const timer = window.setInterval(() => void refreshEffectStatus(), 1500)
    return () => window.clearInterval(timer)
  }, [enabled, status?.building])
  return status
}

// ---------------------------------------------------------------------------
// One tile in the grid (CSS approximation + cached real clip when ready)
// ---------------------------------------------------------------------------

function EffectTile({
  label, active, state, playing, onSelect,
}: {
  label: string
  active: boolean
  state: EffectPreviewState | null
  playing: boolean
  onSelect: () => void
}) {
  const [visible, setVisible] = useState(false)
  const [armed, setArmed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [broken, setBroken] = useState(false)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const element = hostRef.current
    if (!element || visible) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '240px' })
    observer.observe(element)
    return () => observer.disconnect()
  }, [visible])

  useEffect(() => { if (visible && state === 'ready') setArmed(true) }, [visible, state])

  useEffect(() => {
    const video = videoRef.current
    if (!video || !armed) return
    if (active || playing) void video.play().catch(() => { /* autoplay refused */ })
    else { video.pause(); video.currentTime = 0 }
  }, [armed, active, playing])

  const canPreview = state === 'ready' || state === 'pending'
  const showVideo = armed && canPreview && !broken
  return <div
    ref={hostRef}
    className={`transition-tile text-effect-tile ${active ? 'active' : ''}`}
    role="option"
    aria-selected={active}
    tabIndex={-1}
    title={state === 'failed' ? `${label} · example failed to render` : label}
    onClick={onSelect}
    onKeyDown={event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect() }
    }}
  >
    <span className="tile-stage effect-stage">
      {showVideo && <video
        ref={videoRef}
        src={textEffectPreviewUrl(label)}
        muted loop playsInline preload="none"
        onLoadedData={() => setLoaded(true)}
        onError={() => setBroken(true)}
      />}
      {(!showVideo || !loaded) && <span className="effect-fallback"><i className="effect-fallback-text">{textEffectSymbol(label)} {label.replace(' (static)', '')}</i></span>}
      {showVideo && !loaded && <span className="tile-loading"><Loader2 size={12} className="spin" /></span>}
    </span>
    <span className="tile-name">{label}</span>
  </div>
}

// ---------------------------------------------------------------------------
// The chip + popover
// ---------------------------------------------------------------------------

const labelToGroup = new Map<string, Map<string, string[]>>()
for (const slot of ['enter', 'while', 'exit'] as TextEffectSlot[]) {
  const groups = new Map<string, string[]>()
  for (const [group, names] of Object.entries(textEffectGroupsFor(slot))) groups.set(group, names)
  labelToGroup.set(slot, groups)
}

export function TextEffectChip({ value, slot, onChange, ariaLabel, title, className, showSeconds, seconds, onSecondsChange, params, onParamsChange }: {
  value: string
  slot: TextEffectSlot
  onChange: (label: string) => void
  ariaLabel?: string
  title?: string
  className?: string
  // Optional duration stepper next to the chip (enter/exit durations, or the
  // while loop period when slot === 'while').
  showSeconds?: boolean
  seconds?: number
  onSecondsChange?: (v: number) => void
  // Per-effect parameters (Count up from/to …).
  params?: Record<string, string>
  onParamsChange?: (next: Record<string, string>) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState<string | null>(null)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const status = useEffectStatus(open)

  const measure = useCallback(() => {
    const element = triggerRef.current
    if (element) setRect(element.getBoundingClientRect())
  }, [])

  useEffect(() => {
    if (!open) return
    measure()
    const onMove = () => measure()
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => { window.removeEventListener('scroll', onMove, true); window.removeEventListener('resize', onMove) }
  }, [open, measure])

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => searchRef.current?.focus(), 10)
    return () => window.clearTimeout(timer)
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (popRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); setOpen(false); triggerRef.current?.focus() }
    }
    document.addEventListener('pointerdown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    return () => { document.removeEventListener('pointerdown', onDown, true); document.removeEventListener('keydown', onKey, true) }
  }, [open])

  const sections = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const groups = labelToGroup.get(slot) || new Map()
    const matching = (needle ? allTextEffects(slot) : Object.values(groups).flat()).filter(t => t.toLowerCase().includes(needle))
    if (group) {
      const items = matching.filter(t => (groups.get(group) || []).includes(t))
      return items.length ? [{ name: group, items }] : []
    }
    const out: { name: string; items: string[] }[] = []
    for (const [name, names] of groups) {
      const items = matching.filter(t => names.includes(t))
      if (items.length) out.push({ name, items })
    }
    return out
  }, [query, slot, group])

  const flat = useMemo(() => sections.flatMap(s => s.items), [sections])
  const activeParams = textEffectParams(value)
  const popLeft = rect ? Math.min(rect.left, window.innerWidth - 560) : 0
  const popTop = rect ? rect.bottom + 6 : 0

  return <>
    <span className="text-effect-controls">
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        title={title || `${value} · click to browse text effects`}
        className={`text-effect-chip ${className || ''}`}
        onClick={() => setOpen(o => !o)}
      >
        <i className="text-effect-symbol" aria-hidden>{textEffectSymbol(value)}</i>
        <span className="text-effect-label">{value}</span>
        <ChevronDown size={11} />
      </button>
      {showSeconds && onSecondsChange && seconds !== undefined && <input
        type="number"
        className="text-effect-seconds"
        aria-label={slot === 'while' ? 'Effect loop period' : 'Effect duration'}
        title={slot === 'while' ? 'One loop of the effect, in seconds' : 'Effect duration in seconds'}
        min={0.4} max={12} step={0.1} value={seconds}
        onChange={e => onSecondsChange(Number(e.target.value))}
      />}
    </span>
    {activeParams.length > 0 && onParamsChange && <span className="text-effect-params">
      {activeParams.map(def => <label key={def.name} className="text-effect-param">
        <span>{def.name}</span>
        <input
          type="number"
          value={params?.[def.name] ?? def.default}
          onChange={e => onParamsChange({ ...(params || {}), [def.name]: e.target.value })}
        />
      </label>)}
    </span>}
    {open && createPortal(
      <div
        ref={popRef}
        className="transition-popover text-effect-popover"
        style={{ position: 'fixed', left: Math.max(8, popLeft), top: Math.min(popTop, window.innerHeight - 380), zIndex: 90 }}
        onClick={e => e.stopPropagation()}
      >
        <div className="popover-search">
          <Search size={13} />
          <input ref={searchRef} value={query} onChange={e => setQuery(e.target.value)} placeholder="Search effects…" aria-label="Search text effects" />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="Clear search"><X size={12} /></button>}
        </div>
        <div className="popover-groups">
          <button type="button" className={!group ? 'active' : ''} onClick={() => setGroup(null)}>All</button>
          {[...(labelToGroup.get(slot) || new Map()).keys()].map(name => (
            <button key={name} type="button" className={group === name ? 'active' : ''} onClick={() => setGroup(name)}>{name}</button>
          ))}
        </div>
        {status && status.ready < status.total && <div className="popover-buildnote">
          <span>{status.ready}/{status.total} examples rendered</span>
          <button type="button" onClick={() => void buildAllEffectPreviews()} title="Render every missing example clip now (background)"><RefreshCw size={11} /> Render all</button>
        </div>}
        <div className="popover-grid transition-grid" role="listbox" aria-label={`${slot} text effects`}>
          {flat.map(label => (
            <EffectTile
              key={label}
              label={label}
              active={label === value}
              state={status?.items?.[textEffectSlug(label)]?.status ?? null}
              playing={false}
              onSelect={() => { onChange(label); setOpen(false); triggerRef.current?.focus() }}
            />
          ))}
          {!flat.length && <div className="popover-empty">No effect matches “{query}”.</div>}
        </div>
      </div>,
      document.body,
    )}
  </>
}
