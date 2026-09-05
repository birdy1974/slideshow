// The transition browser: a compact chip that opens a searchable, grouped,
// previewing popover.
//
// It replaces the 191-entry <select> that used to sit in every row of the
// detailed slide list. Rows keep a one-line chip; the browsing (search,
// categories, animated previews, favourites) happens in one shared popover
// rendered in a portal so panel overflow can never clip it.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, LayoutGrid, Loader2, RefreshCw, Search, Star, X } from 'lucide-react'
import {
  EASING_DEFAULT, isGLTransition, loadFavouriteTransitions, loadRecentTransitions,
  rememberTransition, toggleFavouriteTransition, totalTransitionCount, transitionGroups,
  transitionPreviewUrl, transitionSlug, transitionSymbol, transitionDirection, transitions,
} from './transitionCatalog'

// ---------------------------------------------------------------------------
// Cached backend previews: /api/transition-previews/<slug>.mp4
// One module-level cache shared by every picker instance on the page.
// ---------------------------------------------------------------------------

export type PreviewState = 'ready' | 'pending' | 'failed' | 'unsupported'
export interface PreviewStatus {
  total: number; ready: number; pending: number; failed: number; unsupported: number
  building: boolean; buildDone: number; buildTotal: number; hasFfmpeg: boolean
  items: Record<string, { label: string; kind: string; status: PreviewState; error: string }>
}

let statusCache: PreviewStatus | null = null
let inflight: Promise<PreviewStatus | null> | null = null
const listeners = new Set<(s: PreviewStatus | null) => void>()

function emit(status: PreviewStatus | null) {
  statusCache = status
  listeners.forEach(listener => listener(status))
}

export function refreshPreviewStatus(): Promise<PreviewStatus | null> {
  if (inflight) return inflight
  inflight = fetch('/api/transition-previews/status')
    .then(r => (r.ok ? r.json() as Promise<PreviewStatus> : Promise.resolve(null)))
    .then(data => { emit(data); return data })
    .catch(() => { emit(null); return null })
    .finally(() => { inflight = null })
  return inflight
}

export async function buildAllPreviews() {
  try {
    const response = await fetch('/api/transition-previews/build', { method: 'POST' })
    if (response.ok) emit(await response.json() as PreviewStatus)
  } catch { /* backend offline — the picker stays on its CSS approximations */ }
}

export function usePreviewStatus(enabled: boolean) {
  const [status, setStatus] = useState<PreviewStatus | null>(statusCache)
  useEffect(() => {
    if (!enabled) return
    listeners.add(setStatus)
    if (statusCache) setStatus(statusCache)
    if (!statusCache && !inflight) void refreshPreviewStatus()
    return () => { listeners.delete(setStatus) }
  }, [enabled])
  // While the backend renders the catalogue, keep the counts ticking.
  useEffect(() => {
    if (!enabled || !status?.building) return
    const timer = window.setInterval(() => void refreshPreviewStatus(), 1500)
    return () => window.clearInterval(timer)
  }, [enabled, status?.building])
  return status
}

// ---------------------------------------------------------------------------
// One tile in the grid
// ---------------------------------------------------------------------------

export function TransitionTile({
  label, active, state, previewAllowed, playing, favourite, onSelect, onToggleFavourite, onFocusTile, tileRef,
}: {
  label: string
  active: boolean
  state: PreviewState | null      // null = backend status unknown (offline)
  previewAllowed: boolean         // false when the backend has no usable FFmpeg
  playing: boolean
  favourite: boolean
  onSelect: () => void
  onToggleFavourite: () => void
  onFocusTile: () => void
  tileRef: (element: HTMLDivElement | null) => void
}) {
  const [visible, setVisible] = useState(false)
  const [armed, setArmed] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [broken, setBroken] = useState(false)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const hostRef = useRef<HTMLDivElement | null>(null)

  // Only tiles that scroll into view ever touch the network.
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
    if (active || playing) void video.play().catch(() => { /* autoplay refused — the CSS fallback shows */ })
    else { video.pause(); video.currentTime = 0 }
  }, [armed, active, playing])

  // 'ready' clips load as soon as the tile scrolls into view; 'pending' ones
  // (not rendered yet) only on hover, so scrolling the grid never fires a
  // hundred synchronous FFmpeg renders.
  const canPreview = previewAllowed && (state === 'ready' || state === 'pending')
  const showVideo = armed && canPreview && !broken
  const showFallback = !showVideo || !loaded

  return <div
    ref={element => { hostRef.current = element; tileRef(element) }}
    className={`transition-tile ${active ? 'active' : ''} ${isGLTransition(label) ? 'gl' : ''}`}
    role="option"
    aria-selected={active}
    tabIndex={-1}
    title={`${label}${state === 'unsupported' ? ' · falls back on this FFmpeg build' : ''}`}
    onClick={onSelect}
    onFocus={() => { if (state === 'pending') setArmed(true); onFocusTile() }}
    onPointerEnter={() => { if (state === 'pending') setArmed(true) }}
    onKeyDown={event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect() }
    }}
  >
    <span className="tile-stage" style={{ '--transition-speed': '.9s' } as React.CSSProperties}>
      {showFallback && <span className={`quick-transition ${transitionDirection(label)}`} aria-hidden>
        <span className="tile-frame a" />
        <span className="tile-frame b" />
      </span>}
      {showVideo && <video
        ref={videoRef}
        src={transitionPreviewUrl(label)}
        muted loop playsInline preload="none"
        onLoadedData={() => setLoaded(true)}
        onError={() => setBroken(true)}
      />}
      {showVideo && !loaded && <span className="tile-loading"><Loader2 size={12} className="spin" /></span>}
      {state === 'unsupported' && <span className="tile-flag">fallback</span>}
      <button type="button" className={`tile-star ${favourite ? 'on' : ''}`}
        title={favourite ? 'Remove from favourites' : 'Add to favourites'}
        aria-label={favourite ? `Unfavourite ${label}` : `Favourite ${label}`}
        onClick={event => { event.stopPropagation(); onToggleFavourite() }}>
        <Star size={11} fill={favourite ? 'currentColor' : 'none'} />
      </button>
    </span>
    <span className="tile-name">{label.replace(/^GL · /, '')}</span>
  </div>
}

// ---------------------------------------------------------------------------
// The chip + popover
// ---------------------------------------------------------------------------

const labelToGroup = new Map<string, string>()
for (const [group, names] of Object.entries(transitionGroups)) for (const name of names) labelToGroup.set(name, group)

type Scope = 'all' | 'xfade' | 'gl' | 'favourites' | 'recent'

export function TransitionChip({ value, onChange, ariaLabel, title, className, onOpenGallery }: {
  value: string
  onChange: (label: string) => void
  ariaLabel?: string
  title?: string
  className?: string
  // Optional door into the standalone gallery (browse without picking).
  onOpenGallery?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<Scope>('all')
  const [group, setGroup] = useState<string | null>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [autoplayVisible, setAutoplayVisible] = useState(false)
  const [favourites, setFavourites] = useState<string[]>([])
  const [recents, setRecents] = useState<string[]>([])
  const [rect, setRect] = useState<DOMRect | null>(null)

  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const gridRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const tileRefs = useRef<(HTMLDivElement | null)[]>([])

  const status = usePreviewStatus(open)

  const measure = useCallback(() => {
    const element = triggerRef.current
    if (element) setRect(element.getBoundingClientRect())
  }, [])

  // Keep the popover glued to its chip while the page scrolls.
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
    setFavourites(loadFavouriteTransitions())
    setRecents(loadRecentTransitions())
    const timer = window.setTimeout(() => searchRef.current?.focus(), 10)
    return () => window.clearTimeout(timer)
  }, [open])

  // Dismiss on outside click / Escape.
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
    const pool = scope === 'xfade' ? transitions.filter(t => !isGLTransition(t))
      : scope === 'gl' ? transitions.filter(t => isGLTransition(t))
        : scope === 'favourites' ? favourites
          : scope === 'recent' ? recents
            : transitions
    const matching = (needle ? transitions : pool).filter(t => t.toLowerCase().includes(needle))
    if (group) {
      const items = matching.filter(t => labelToGroup.get(t) === group)
      return items.length ? [{ name: group, items }] : []
    }
    const out: { name: string; items: string[] }[] = []
    for (const [name, names] of Object.entries(transitionGroups)) {
      const items = matching.filter(t => names.includes(t))
      if (items.length) out.push({ name, items })
    }
    return out
  }, [query, scope, group, favourites, recents])

  const flat = useMemo(() => sections.flatMap(section => section.items), [sections])

  useEffect(() => {
    const index = flat.indexOf(value)
    setActiveIndex(index >= 0 ? index : 0)
  }, [flat, value])

  const select = (label: string) => {
    onChange(label)
    rememberTransition(label)
    setRecents(loadRecentTransitions())
    setOpen(false)
    triggerRef.current?.focus()
  }

  const toggleFavourite = (label: string) => {
    toggleFavouriteTransition(label)
    setFavourites(loadFavouriteTransitions())
  }

  const columns = () => {
    const grid = gridRef.current
    const first = tileRefs.current.find(Boolean)
    if (!grid || !first) return 4
    const tile = first.getBoundingClientRect().width + 8
    return Math.max(1, Math.floor((grid.clientWidth + 8) / tile))
  }

  const moveActive = (delta: number) => {
    if (!flat.length) return
    const next = Math.max(0, Math.min(flat.length - 1, activeIndex + delta))
    setActiveIndex(next)
    tileRefs.current[next]?.focus()
    tileRefs.current[next]?.scrollIntoView({ block: 'nearest' })
  }

  const onSearchKey = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); moveActive(1) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); moveActive(-1) }
    else if (event.key === 'Enter') {
      event.preventDefault()
      const label = flat[activeIndex]
      if (label) select(label)
    }
  }

  const onGridKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const cols = columns()
    if (event.key === 'ArrowRight') { event.preventDefault(); moveActive(1) }
    else if (event.key === 'ArrowLeft') { event.preventDefault(); moveActive(-1) }
    else if (event.key === 'ArrowDown') { event.preventDefault(); moveActive(cols) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); moveActive(-cols) }
    else if (event.key === 'Home') { event.preventDefault(); moveActive(-flat.length) }
    else if (event.key === 'End') { event.preventDefault(); moveActive(flat.length) }
  }

  // ---- popover geometry ----
  const geometry = (() => {
    if (!rect) return null
    const width = Math.min(780, Math.max(360, window.innerWidth - 24))
    const left = Math.max(12, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 12))
    const below = window.innerHeight - rect.bottom - 12
    const above = rect.top - 12
    const flip = below < 420 && above > below
    const height = Math.max(220, Math.min(470, flip ? above : below))
    const top = flip ? rect.top - height - 6 : rect.bottom + 6
    return { width, height, left, top: Math.max(12, top) }
  })()

  const gl = isGLTransition(value)
  const cached = status?.ready ?? 0

  return <>
    <button type="button" ref={triggerRef}
      className={`transition-chip ${gl ? 'gl' : ''} ${open ? 'open' : ''} ${className || ''}`}
      aria-haspopup="listbox" aria-expanded={open} aria-label={ariaLabel || `Transition: ${value}`}
      title={title || `${value} — click to browse all ${totalTransitionCount} transitions`}
      onClick={() => setOpen(o => !o)}
      onKeyDown={event => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true) } }}>
      <i className="chip-symbol">{transitionSymbol(value)}</i>
      <span className="chip-name">{value.replace(/^GL · /, '')}</span>
      {gl && <em className="chip-kind">GL</em>}
      <ChevronDown size={13} />
    </button>

    {open && rect && geometry && createPortal(<div
      ref={popRef}
      className="transition-browser"
      style={{ left: geometry.left, top: geometry.top, width: geometry.width, height: geometry.height }}
      role="dialog"
      aria-label="Choose a transition"
    >
      <header>
        <label className="browser-search">
          <Search size={13} />
          <input ref={searchRef} value={query} placeholder={`Search ${totalTransitionCount} transitions…`}
            aria-label="Search transitions"
            onChange={e => { setQuery(e.target.value); setGroup(null) }}
            onKeyDown={onSearchKey} />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="Clear search"><X size={12} /></button>}
        </label>
        <button type="button" className="browser-close" onClick={() => { setOpen(false); triggerRef.current?.focus() }} aria-label="Close"><X size={15} /></button>
      </header>

      <div className="browser-tabs">
        {([['all', `All ${totalTransitionCount}`], ['xfade', 'XFade 58'], ['gl', 'GL 133']] as [Scope, string][]).map(([key, text]) =>
          <button type="button" key={key} className={scope === key ? 'active' : ''}
            onClick={() => { setScope(key); setGroup(null); setQuery('') }}>{text}</button>)}
        <i />
        <button type="button" className={scope === 'favourites' ? 'active' : ''} onClick={() => { setScope('favourites'); setGroup(null); setQuery('') }} title="Your starred transitions"><Star size={11} fill={scope === 'favourites' || favourites.length ? 'currentColor' : 'none'} /> {favourites.length || ''}</button>
        <button type="button" className={scope === 'recent' ? 'active' : ''} onClick={() => { setScope('recent'); setGroup(null); setQuery('') }} title="Used recently in this browser">Recent <b>{recents.length}</b></button>
      </div>

      <div className="browser-body">
        <nav className="browser-groups" aria-label="Categories">
          <button type="button" className={group === null ? 'active' : ''} onClick={() => setGroup(null)}>All categories</button>
          {(scope === 'xfade' ? Object.keys(transitionGroups).filter(g => !g.startsWith('GL'))
            : scope === 'gl' ? Object.keys(transitionGroups).filter(g => g.startsWith('GL'))
              : Object.keys(transitionGroups)).map(name =>
            <button type="button" key={name} className={group === name ? 'active' : ''} onClick={() => setGroup(name === group ? null : name)}>
              {name.replace(/^GL · /, '')}
              <b>{transitions.filter(t => labelToGroup.get(t) === name).length}</b>
            </button>)}
        </nav>

        <div className="browser-grid-wrap">
          <div className="browser-grid" ref={gridRef} role="listbox" aria-label="Transitions" onKeyDown={onGridKey}>
            {flat.length === 0 && <p className="browser-empty">
              {scope === 'favourites' ? 'No favourites yet — click the star on any transition to pin it here.'
                : scope === 'recent' ? 'Nothing used yet in this browser.'
                  : `No transition matches “${query}”.`}
            </p>}
            {sections.map((section, sectionIndex) => {
              const offset = sections.slice(0, sectionIndex).reduce((sum, s) => sum + s.items.length, 0)
              return <section key={section.name}>
                <strong>{section.name.replace(/^GL · /, '')}</strong>
                <div className="tile-row">
                  {section.items.map((label, index) => {
                    const flatIndex = offset + index
                    const slug = transitionSlug(label)
                    return <TransitionTile
                      key={label}
                      label={label}
                      active={label === value}
                      state={status?.items?.[slug]?.status ?? null}
                      previewAllowed={status !== null && status.hasFfmpeg !== false}
                      playing={autoplayVisible && flatIndex === activeIndex}
                      favourite={favourites.includes(label)}
                      onSelect={() => select(label)}
                      onToggleFavourite={() => toggleFavourite(label)}
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
        <span className="browser-count">{flat.length} of {totalTransitionCount}</span>
        <label className="check-label tiny" title="Play every preview while you scroll">
          <input type="checkbox" checked={autoplayVisible} onChange={e => setAutoplayVisible(e.target.checked)} />
          <span><svg viewBox="0 0 24 24" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="4"><path d="M20 6 9 17l-5-5" /></svg></span>
          Autoplay
        </label>
        {status
          ? <span className="browser-cache">
            {status.building
              ? <>Rendering {status.buildDone}/{status.buildTotal} <Loader2 size={10} className="spin" /></>
              : status.hasFfmpeg === false
                ? 'FFmpeg unavailable · approximations shown'
                : `${cached}/${status.total} previews cached`}
          </span>
          : <span className="browser-cache">Preview cache offline</span>}
        {status && !status.building && status.pending > 0 && status.hasFfmpeg !== false &&
          <button type="button" className="browser-build" onClick={() => void buildAllPreviews()}>
            <RefreshCw size={11} /> Render all {status.pending} missing
          </button>}
        {onOpenGallery && <button type="button" className="browser-gallery" onClick={() => { setOpen(false); onOpenGallery() }}>
          <LayoutGrid size={11} /> Open full gallery
        </button>}
      </footer>
    </div>, document.body)}
  </>
}

/** Easing/reverse summary line, shared by the row popover and the inspector. */
export function transitionSummary(easing?: string, reverse?: number) {
  const bits: string[] = []
  if (easing && easing !== EASING_DEFAULT) bits.push(easing)
  if (reverse) bits.push('reverse')
  return bits.join(' · ')
}
