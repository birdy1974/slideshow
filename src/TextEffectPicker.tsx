// The text-effect browser: a compact chip that opens a grouped, searchable
// popover with real rendered examples — the text counterpart of
// TransitionPicker, consuming the same backend cache pattern
// (/api/text-effects/<slug>.mp4, rendered once per effect, then static).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, ChevronDown, Loader2, RefreshCw, Search, X } from 'lucide-react'
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
  label, active, state, error, playing, onSelect, onFocusTile, tileRef,
}: {
  label: string
  active: boolean
  state: EffectPreviewState | null
  error?: string
  playing: boolean
  onSelect: () => void
  onFocusTile?: () => void
  tileRef?: (element: HTMLDivElement | null) => void
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
    ref={element => { hostRef.current = element; tileRef?.(element) }}
    className={`transition-tile text-effect-tile ${active ? 'active' : ''}`}
    role="option"
    aria-selected={active}
    tabIndex={-1}
    title={state === 'failed' ? `${label} · example failed to render${error ? `: ${error}` : ''}` : label}
    onClick={onSelect}
    onFocus={onFocusTile}
    onMouseEnter={onFocusTile}
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
      {state === 'failed' && <span className="tile-flag failed" title={error || 'Example could not be rendered'}>failed</span>}
    </span>
    <span className="tile-name"><i className="tile-symbol" aria-hidden>{textEffectSymbol(label)}</i>{label.replace(' (static)', '')}</span>
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

const slotTitle: Record<TextEffectSlot, string> = { enter: 'Text appears', while: 'While shown', exit: 'Text disappears' }

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
  const [activeIndex, setActiveIndex] = useState(0)
  const [autoplayVisible, setAutoplayVisible] = useState(false)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const tileRefs = useRef<(HTMLDivElement | null)[]>([])
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
    setQuery(''); setGroup(null)
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

  const groups = labelToGroup.get(slot) || new Map<string, string[]>()
  const total = allTextEffects(slot).length

  const sections = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matching = allTextEffects(slot).filter(t => t.toLowerCase().includes(needle))
    const out: { name: string; items: string[] }[] = []
    for (const [name, names] of groups) {
      if (group && name !== group) continue
      const items = matching.filter(t => names.includes(t))
      if (items.length) out.push({ name, items })
    }
    return out
  }, [query, slot, group, groups])

  const flat = useMemo(() => sections.flatMap(s => s.items), [sections])

  useEffect(() => {
    if (!open) return
    const current = flat.indexOf(value)
    setActiveIndex(current >= 0 ? current : 0)
  }, [open, flat, value])

  const select = (label: string) => { onChange(label); setOpen(false); triggerRef.current?.focus() }

  const focusTile = (index: number) => {
    const clamped = Math.max(0, Math.min(flat.length - 1, index))
    setActiveIndex(clamped)
    tileRefs.current[clamped]?.focus()
    tileRefs.current[clamped]?.scrollIntoView({ block: 'nearest' })
  }
  const onGridKey = (event: React.KeyboardEvent) => {
    if (!flat.length) return
    if (event.key === 'ArrowRight') { event.preventDefault(); focusTile(activeIndex + 1) }
    else if (event.key === 'ArrowLeft') { event.preventDefault(); focusTile(activeIndex - 1) }
    else if (event.key === 'ArrowDown') { event.preventDefault(); focusTile(activeIndex + 4) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); focusTile(activeIndex - 4) }
    else if (event.key === 'Home') { event.preventDefault(); focusTile(0) }
    else if (event.key === 'End') { event.preventDefault(); focusTile(flat.length - 1) }
  }
  const onSearchKey = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); focusTile(activeIndex) }
    else if (event.key === 'Enter' && flat.length) { event.preventDefault(); select(flat[activeIndex] ?? flat[0]) }
  }

  // Same placement rules as the transition browser: centred on the chip,
  // flipped above it when there is more room there.
  const geometry = rect && (() => {
    const width = Math.min(720, Math.max(360, window.innerWidth - 24))
    const left = Math.max(12, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 12))
    const below = window.innerHeight - rect.bottom - 12
    const above = rect.top - 12
    const flip = below < 420 && above > below
    const height = Math.max(220, Math.min(470, flip ? above : below))
    const top = flip ? rect.top - height - 6 : rect.bottom + 6
    return { width, height, left, top: Math.max(12, top) }
  })()

  const activeParams = textEffectParams(value)
  const cached = status?.ready ?? 0

  return <>
    <span className="text-effect-controls">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox" aria-expanded={open}
        aria-label={ariaLabel || `${slotTitle[slot]}: ${value}`}
        title={title || `${value} — click to browse all ${total} ${slotTitle[slot].toLowerCase()} effects`}
        className={`transition-chip text-effect-chip ${open ? 'open' : ''} ${className || ''}`}
        onClick={() => setOpen(o => !o)}
        onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true) } }}
      >
        <i className="chip-symbol text-effect-symbol" aria-hidden>{textEffectSymbol(value)}</i>
        <span className="chip-name text-effect-label">{value.replace(' (static)', '')}</span>
        <ChevronDown size={13} />
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
    {open && rect && geometry && createPortal(
      <div
        ref={popRef}
        className="transition-browser text-effect-browser"
        style={{ left: geometry.left, top: geometry.top, width: geometry.width, height: geometry.height }}
        role="dialog"
        aria-label={`Choose a ${slotTitle[slot].toLowerCase()} effect`}
        onClick={e => e.stopPropagation()}
      >
        <header>
          <label className="browser-search">
            <Search size={13} />
            <input ref={searchRef} value={query} placeholder={`Search ${total} ${slotTitle[slot].toLowerCase()} effects…`}
              aria-label="Search text effects"
              onChange={e => { setQuery(e.target.value); setGroup(null) }}
              onKeyDown={onSearchKey} />
            {query && <button type="button" onClick={() => setQuery('')} aria-label="Clear search"><X size={12} /></button>}
          </label>
          <button type="button" className="browser-close" onClick={() => { setOpen(false); triggerRef.current?.focus() }} aria-label="Close"><X size={15} /></button>
        </header>

        <div className="browser-tabs">
          <button type="button" className="active" disabled>{slotTitle[slot]} <b>{total}</b></button>
          <i />
          <span className="browser-current" title="Currently selected">Current: <b>{value.replace(' (static)', '')}</b></span>
        </div>

        <div className="browser-body">
          <nav className="browser-groups" aria-label="Categories">
            <button type="button" className={group === null ? 'active' : ''} onClick={() => setGroup(null)}>All categories</button>
            {[...groups.entries()].map(([name, names]) =>
              <button type="button" key={name} className={group === name ? 'active' : ''} onClick={() => setGroup(name === group ? null : name)}>
                {name}<b>{names.length}</b>
              </button>)}
          </nav>

          <div className="browser-grid-wrap">
            <div className="browser-grid" role="listbox" aria-label={`${slotTitle[slot]} effects`} onKeyDown={onGridKey}>
              {flat.length === 0 && <p className="browser-empty">No effect matches “{query}”.</p>}
              {sections.map((section, sectionIndex) => {
                const offset = sections.slice(0, sectionIndex).reduce((sum, s) => sum + s.items.length, 0)
                return <section key={section.name}>
                  <strong>{section.name}</strong>
                  <div className="tile-row">
                    {section.items.map((label, index) => {
                      const flatIndex = offset + index
                      const entry = status?.items?.[textEffectSlug(label)]
                      return <EffectTile
                        key={label}
                        label={label}
                        active={label === value}
                        state={entry?.status ?? null}
                        error={entry?.error}
                        playing={autoplayVisible || flatIndex === activeIndex}
                        onSelect={() => select(label)}
                        onFocusTile={() => setActiveIndex(flatIndex)}
                        tileRef={element => { tileRefs.current[flatIndex] = element }}
                      />
                    })}
                  </div>
                </section>
              })}
              {flat.length > 0 && <div className="grid-tail" />}
            </div>
          </div>
        </div>

        <footer>
          <span className="browser-count">{flat.length} of {total}</span>
          <label className="check-label tiny" title="Play every example while you scroll">
            <input type="checkbox" checked={autoplayVisible} onChange={e => setAutoplayVisible(e.target.checked)} />
            <span><svg viewBox="0 0 24 24" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="4"><path d="M20 6 9 17l-5-5" /></svg></span>
            Autoplay
          </label>
          {status
            ? <span className="browser-cache">
              {status.building
                ? <>Rendering {status.buildDone}/{status.buildTotal} <Loader2 size={10} className="spin" /></>
                : status.hasFfmpeg === false
                  ? 'FFmpeg unavailable · names only'
                  : `${cached}/${status.total} examples cached`}
            </span>
            : <span className="browser-cache">Example cache offline</span>}
          {status && !status.building && status.failed > 0 && (() => {
            const reason = Object.values(status.items).find(item => item.status === 'failed' && item.error)?.error
            return <span className="browser-cache failed" title={reason ? `Last FFmpeg error: ${reason}` : 'Some examples could not be rendered'}>
              <AlertTriangle size={10} /> {status.failed} failed
            </span>
          })()}
          {status && !status.building && (status.pending > 0 || status.failed > 0) && status.hasFfmpeg !== false &&
            <button type="button" className="browser-build" onClick={() => void buildAllEffectPreviews()}>
              <RefreshCw size={11} /> Render all {status.pending + status.failed} missing
            </button>}
        </footer>
      </div>,
      document.body,
    )}
  </>
}
