// The entrance browser: a chip that opens a searchable, grouped popover of every
// photo entrance, each one previewed live.
//
// The collage's catalogue outgrew a row of chips long ago — 13 classics, 12 paper
// entrances, 8 colour entrances, 16 mask reveals and 41 of FFmpeg's own transition
// names is 90 buttons in a line. So the picker borrows the shape the transition
// browser already uses (search box, family tabs, favourites, recents) and keeps
// one thing the transition browser cannot: its thumbnails are drawn by the very
// functions the preview and the MP4 run, so a tile cannot promise a move the
// render will not make.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Clock3, Search, Sparkles, Star, X } from 'lucide-react'
import { useEscapeToClose, useLayer } from './layers'
import {
  animFamily, animHasDirection, animLabelOf, collageAnimRows, entranceLength, isApproxBridge,
  matFilterCss, photoLookState, photoState, revealMaskStyle,
  type CollageAnimFamily, type CollageAnimation, type CollageAnimRow, type CollageSpec,
} from './collageCore'

const FAMILIES: { id: CollageAnimFamily | 'all' | 'star' | 'recent'; label: string }[] = [
  { id: 'star', label: '★ Favourites' },
  { id: 'recent', label: 'Recent' },
  { id: 'all', label: 'All' },
  { id: 'classic', label: 'Classic' },
  { id: 'paper', label: 'Paper' },
  { id: 'colour', label: 'Colour' },
  { id: 'mask', label: 'Reveals' },
  { id: 'ffmpeg', label: 'FFmpeg' },
]

// Favourites and recents live here, on their own keys: the transition browser's
// lists are keyed by transition slug and a collage entrance id in that list would
// show up as an unknown tile in a picker that has nothing to do with this one.
const FAV_KEY = 'collage.anim.favourites'
const RECENT_KEY = 'collage.anim.recent'

function readList (key: string): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? '[]')
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : []
  } catch { return [] }
}
function writeList (key: string, values: string[]) {
  try { localStorage.setItem(key, JSON.stringify(values.slice(0, 12))) } catch { /* private mode */ }
}

// ---------------------------------------------------------------------------
// The live thumbnail
// ---------------------------------------------------------------------------

const TILE_SPEC = (id: CollageAnimation, dir: 0 | 1): CollageSpec => ({
  // A one-photo grid, so the tile shows the entrance and nothing else. This is
  // the same `photoState` the stage samples at 1280×720: the state speaks in
  // percent-of-frame and degrees, which is the only reason a 104-pixel square can
  // reuse it at all — there is nothing here scaled by the frame size.
  layout: 'grid', cols: 1, rows: 1, animation: id, animDir: dir,
  frame: 'none', photos: [{ src: '', anim: id }],
} as unknown as CollageSpec)

/** The tile's photo at time `t`: transform, opacity, filter and mask, from the
 *  functions the preview itself uses. A `null` t rests at the end state, which is
 *  what an unfocused tile shows so the grid is not 90 animations at once. */
function tileStyle (row: CollageAnimRow, t: number | null): React.CSSProperties {
  const spec = TILE_SPEC(row.id, 0)
  const length = entranceLength(spec, 0) || 0.6
  const at = t === null ? length + 0.2 : t * length
  const st = photoState(spec, 0, at, 0, 16 / 9)
  const style: React.CSSProperties = {
    position: 'absolute',
    inset: '12%',
    opacity: Math.max(0, Math.min(1, st.alpha)),
    transform: `translate(${st.dx}%, ${st.dy}%) rotate(${st.rot}deg) scale(${st.scale}) scaleX(${st.scaleX})`,
    transformOrigin: row.id === 'swing' ? '50% 0' : '50% 50%',
    filter: matFilterCss(st.dim, photoLookState(spec, 0, at, 0)),
  }
  if (st.revealKind) Object.assign(style, revealMaskStyle(st.revealKind, st.revealP ?? 0, st.revealDir ?? 0))
  return style
}

function AnimTile ({ row, active, playing, favourite, approx, onPick, onFavourite, onEnter }: {
  row: CollageAnimRow
  active: boolean
  playing: boolean
  favourite: boolean
  /** the name whose edge comes from FFmpeg's transition rather than from a shape
   *  this app draws — badge it, because the tile is the one place a viewer decides
   *  what they are about to get, and an approximation they cannot see is a bug */
  approx: boolean
  onPick: () => void
  onFavourite: () => void
  onEnter: () => void
}) {
  const [t, setT] = useState(playing ? 0 : 1)
  useEffect(() => {
    if (!playing) { setT(1); return }
    let raf = 0
    const start = performance.now()
    const length = (entranceLength(TILE_SPEC(row.id, 0), 0) || 0.6) * 1000
    const loop = (now: number) => {
      // one entrance, a beat of hold, then again — so a hovered tile reads as the
      // animation rather than as a single frame of it
      const p = ((now - start) % (length * 1.8)) / length
      setT(Math.min(1, p)); raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [playing, row.id])
  return <div
    className={`transition-tile ${active ? 'active' : ''}`}
    role="option" aria-selected={active} tabIndex={-1}
    title={`${row.label} · ${row.hint}${animHasDirection(row.id) ? ' · directionable' : ''}`}
    onClick={onPick} onPointerEnter={onEnter}
    onKeyDown={event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onPick() }
    }}
  >
    <span className="tile-stage anim-tile-stage">
      <span className="anim-tile-photo" style={tileStyle(row, t)}>
        <span className="anim-tile-art" />
      </span>
    </span>
    {approx && <span className="tile-flag approx" title="Rendered with FFmpeg's own transition; the tile shows the nearest shape">approx</span>}
    <span className="tile-name">{row.label}</span>
    <button type="button"
      className={`tile-star ${favourite ? 'on' : ''}`}
      aria-label={favourite ? `Unstar ${row.label}` : `Star ${row.label}`}
      onClick={event => { event.stopPropagation(); onFavourite() }}>
      <Star size={10} fill={favourite ? 'currentColor' : 'none'} />
    </button>
  </div>
}

// ---------------------------------------------------------------------------
// The chip + popover
// ---------------------------------------------------------------------------

export function AnimPicker ({ value, onChange, ariaLabel, inherit, id }: {
  value: CollageAnimation | undefined
  onChange: (anim: CollageAnimation) => void
  ariaLabel?: string
  /** a label for "use the collage's choice", for the per-photo picker */
  inherit?: { label: string, pick: () => void }
  id?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [family, setFamily] = useState<(typeof FAMILIES)[number]['id']>('all')
  const [hover, setHover] = useState<string | null>(null)
  const [favourites, setFavourites] = useState<string[]>(() => readList(FAV_KEY))
  const [recents, setRecents] = useState<string[]>(() => readList(RECENT_KEY))
  const [rect, setRect] = useState<DOMRect | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const layer = useLayer(open)
  useEscapeToClose(layer, open ? () => setOpen(false) : undefined)

  const rows = collageAnimRows()
  const label = animLabelOf(value)
  const familyOf = animFamily(value)

  const measure = useCallback(() => {
    const el = triggerRef.current
    if (el) setRect(el.getBoundingClientRect())
  }, [])
  useEffect(() => {
    if (!open) return
    measure()
    const onMove = () => measure()
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    const away = (event: MouseEvent) => {
      const node = event.target as Node | null
      if (node && popRef.current?.contains(node)) return
      if (node && triggerRef.current?.contains(node)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', away)
    return () => {
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
      document.removeEventListener('mousedown', away)
    }
  }, [open, measure])
  useEffect(() => { if (open) setTimeout(() => searchRef.current?.focus(), 20) }, [open])

  const shown = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    let list = rows
    if (family === 'star') list = list.filter(r => favourites.includes(r.id))
    else if (family === 'recent') list = recents.map(id => rows.find(r => r.id === id)).filter((r): r is CollageAnimRow => !!r)
    else if (family !== 'all') list = list.filter(r => r.family === family)
    if (words.length) {
      list = list.filter(r => words.every(w =>
        r.label.toLowerCase().includes(w) || r.id.toLowerCase().includes(w) || r.hint.toLowerCase().includes(w)))
    }
    return list
  }, [rows, query, family, favourites, recents])

  const pick = (anim: CollageAnimation) => {
    onChange(anim)
    const next = [anim, ...recents.filter(x => x !== anim)].slice(0, 12)
    setRecents(next); writeList(RECENT_KEY, next)
    setOpen(false)
  }
  const toggleFavourite = (anim: string) => {
    const next = favourites.includes(anim) ? favourites.filter(x => x !== anim) : [anim, ...favourites]
    setFavourites(next); writeList(FAV_KEY, next)
  }

  const count = rows.length

  return <>
    <button type="button" id={id} ref={triggerRef} aria-label={ariaLabel}
      className={`transition-chip anim-chip ${open ? 'open' : ''}`}
      title={value === undefined ? 'Pick how the photos arrive' : `${label} — ${rows.find(r => r.id === value)?.hint ?? ''}`}
      onClick={() => setOpen(o => !o)}>
      <i className="chip-symbol"><Sparkles size={11} /></i>
      <span className="chip-name">{value === undefined ? 'Choose an entrance…' : label}</span>
      {familyOf && <i className="chip-kind">{FAMILIES.find(f => f.id === familyOf)?.label ?? familyOf}</i>}
      <ChevronDown size={13} />
    </button>
    {open && rect && createPortal(<div className="transition-browser anim-browser" ref={popRef}
      role="dialog" aria-label="Photo entrances"
      style={{
        position: 'fixed', zIndex: layer, width: 470, height: Math.min(452, Math.max(300, window.innerHeight - 40)),
        left: Math.max(8, Math.min(rect.left, window.innerWidth - 478)),
        top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - Math.min(452, Math.max(300, window.innerHeight - 40)) - 8)),
      }}>
      <header>
      <div className="browser-search">
        <Search size={12} />
        <input ref={searchRef} value={query} placeholder={`Search ${count} entrances — name, feel, direction…`}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') { const first = shown[0]; if (first) pick(first.id) }
          }} />
        {query && <button type="button" className="browser-close" aria-label="Clear search"
          onClick={() => setQuery('')}><X size={12} /></button>}
      </div>
      </header>
      <div className="browser-body">
        <nav className="browser-groups" aria-label="Families">
          {FAMILIES.map(f => {
            const n = f.id === 'all' ? count
              : f.id === 'star' ? favourites.length
                : f.id === 'recent' ? recents.length
                  : rows.filter(r => r.family === f.id).length
            return <button key={f.id} type="button" className={family === f.id ? 'active' : ''}
              onClick={() => setFamily(f.id)}>{f.label}<b>{n}</b></button>
          })}
        </nav>
        <div className="browser-grid-wrap">
          {inherit && <button type="button" className="anim-inherit" onClick={inherit.pick}
            title="Forget this photo's own entrance and follow the collage's again">
            <span>{inherit.label}</span>
            <Clock3 size={11} />
          </button>}
          <div className="browser-grid" role="listbox" aria-label="Entrances">
            {shown.length === 0 && <p className="browser-empty">Nothing matches “{query}”. Try a feeling
              instead:{' '}<em>springy</em>, <em>soft</em>, <em>tile</em>, <em>colour</em>.</p>}
            {shown.map(r => <AnimTile key={r.id} row={r}
              approx={isApproxBridge(r.id)}
              active={r.id === value}
              playing={hover === r.id}
              favourite={favourites.includes(r.id)}
              onEnter={() => setHover(r.id)}
              onPick={() => pick(r.id)}
              onFavourite={() => toggleFavourite(r.id)} />)}
          </div>
        </div>
      </div>
      <p className="browser-count">{shown.length} of {count} · the tile plays the same animation the preview
        and the MP4 run{shown.some(r => animHasDirection(r.id)) ? ' · directionable ones flip with the Reversed toggle' : ''}</p>
    </div>, document.body)}
  </>
}
