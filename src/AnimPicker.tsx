// The animation browser: a chip that opens a searchable, grouped popover of every
// photo entrance, each one previewed live — and the same browser for photo *exits*,
// because a departure is an arrival read backwards (see collageCore's exitChoice).
//
// The collage's catalogue outgrew a row of chips long ago: the classics, the paper
// family, the colour entrances, the mask reveals and FFmpeg's own transition names
// together are more than ninety buttons in a line, and every one of them is now a
// legal answer to "how does it leave" as well. So the picker borrows the shape the
// transition browser already uses (search box, family tabs, favourites, recents) and
// keeps one thing the transition browser cannot: its thumbnails are drawn by the very
// functions the preview and the MP4 run, so a tile cannot promise a move the render
// will not make. In exit phase the tile runs that same function with the photo's
// arrival switched off and the departure mirrored, which is how the two phases stay
// honest about what the MP4 will do.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, Clock3, Search, Sparkles, Star, X } from 'lucide-react'
import { useEscapeToClose, useLayer } from './layers'
import {
  animFamily, animHasDirection, animLabelOf, baseDuration, collageAnimRows, COLLAGE_EXIT_ROWS,
  entranceLength, exitFlyLength, isApproxBridge, matFilterCss, photoLookState, photoState,
  revealMaskStyle,
  type CollageAnimFamily, type CollageAnimation, type CollageAnimRow, type CollageSpec,
} from './collageCore'

// The five departures that are not an arrival read backwards. In the exit phase they
// join the same browser, so one control answers "how does this photo leave" completely
// and a photo's lane needs no second, smaller widget beside it. `COLLAGE_EXIT_ROWS` is
// the editor's own list, so the chips and these rows can never disagree about what a
// mode is called or what it does.
const EXIT_FAMILY = 'exit'
const EXIT_ROWS: CollageAnimRow[] = COLLAGE_EXIT_ROWS.map(
  r => ({ id: r.id, label: r.label, hint: r.hint, family: EXIT_FAMILY } as unknown as CollageAnimRow))

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

const TILE_SPEC = (id: string, dir: 0 | 1, out = false): CollageSpec => ({
  // A one-photo grid, so the tile shows the entrance and nothing else. This is
  // the same `photoState` the stage samples at 1280×720: the state speaks in
  // percent-of-frame and degrees, which is the only reason a 104-pixel square can
  // reuse it at all — there is nothing here scaled by the frame size.
  layout: 'grid', cols: 1, rows: 1, animation: id, animDir: dir,
  frame: 'none', photos: [{ src: '', anim: id }],
  // …and for an exit, the same id on the *departure* lane with nothing arriving of
  // its own. `hold: 0` starts the exit as early as the engine allows, so the tile is
  // the leaving and not the waiting, and the mirrored curve runs over the length that
  // departure actually takes — the arrival's own for an entrance id, because an exit
  // IS that arrival, and the mode's fly for one of the five. 'reverse' needs something
  // to mirror, so its demo photo arrives the way most do: a drop, straight back up.
  // ('none' is a row too — the tile for it is simply a photo that stays put, which is
  // exactly what choosing it does to this photo.)
  ...(out ? {
    animation: id === 'reverse' ? 'drop' as const : 'none' as const,
    hold: 0, exit: id as CollageSpec['exit'], photos: [{ src: '', exit: id as CollageSpec['exit'] }],
  } : {}),
} as unknown as CollageSpec)

/** The tile's photo at time `t`: transform, opacity, filter and mask, from the
 *  functions the preview itself uses. A `null` t rests at the end state, which is
 *  what an unfocused tile shows so the grid is not 90 animations at once. */
function tileStyle (row: CollageAnimRow, t: number | null, out = false): React.CSSProperties {
  const spec = TILE_SPEC(row.id, 0, out)
  // The clock is the departure's own length in exit phase and the arrival's otherwise
  // — and for an entrance id those are the same number, which is the whole design.
  const length = (out ? exitFlyLength(spec, 0) : entranceLength(spec, 0)) || 0.6
  // A departure starts after the hold, so the sample is offset by it; without that the
  // first two thirds of every exit tile would be a photo standing perfectly still.
  const pre = out ? baseDuration(spec) : 0
  // At rest an arrival sits where it landed, and a departure one step before its own
  // start: an unfocused exit tile shows the photo still hanging on the board.
  const at = t === null ? pre + (out ? -0.2 : length + 0.2) : pre + t * length
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

function AnimTile ({ row, active, playing, favourite, approx, out, onPick, onFavourite, onEnter }: {
  row: CollageAnimRow
  active: boolean
  playing: boolean
  favourite: boolean
  /** the name whose edge comes from FFmpeg's transition rather than from a shape
   *  this app draws — badge it, because the tile is the one place a viewer decides
   *  what they are about to get, and an approximation they cannot see is a bug */
  approx: boolean
  /** preview the *departure* this id produces rather than its arrival */
  out: boolean
  onPick: () => void
  onFavourite: () => void
  onEnter: () => void
}) {
  const [t, setT] = useState(playing ? 0 : 1)
  useEffect(() => {
    if (!playing) { setT(1); return }
    let raf = 0
    const start = performance.now()
    const spec = TILE_SPEC(row.id, 0, out)
    const length = ((out ? exitFlyLength(spec, 0) : entranceLength(spec, 0)) || 0.6) * 1000
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
    title={`${row.label} · ${out ? `leaves the way it would arrive, read backwards — ${row.hint}` : row.hint}${animHasDirection(row.id) ? ' · directionable' : ''}`}
    onClick={onPick} onPointerEnter={onEnter}
    onKeyDown={event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onPick() }
    }}
  >
    <span className="tile-stage anim-tile-stage">
      <span className="anim-tile-photo" style={tileStyle(row, t, out)}>
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

export function AnimPicker ({ value, onChange, onChangeExit, ariaLabel, inherit, id, phase = 'in' }: {
  value: CollageAnimation | undefined
  onChange: (anim: CollageAnimation) => void
  /** the exit phase's writer: a departure may be an entrance id *or* one of the five
   *  modes, which is wider than `CollageAnimation` — so it gets its own hole instead of
   *  making every entrance call site lie about its type. */
  onChangeExit?: (id: string) => void
  ariaLabel?: string
  /** a label for "use the collage's choice", for the per-photo picker */
  inherit?: { label: string, pick: () => void }
  id?: string
  /** 'in' picks how a photo arrives; 'out' picks the arrival to play backwards as
   *  its departure. One browser for both because one table is the whole point: a
   *  new entrance is immediately also a new exit, in the preview and in the MP4. */
  phase?: 'in' | 'out'
}) {
  const out = phase === 'out'
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [family, setFamily] = useState<(typeof FAMILIES)[number]['id'] | typeof EXIT_FAMILY>('all')
  const [hover, setHover] = useState<string | null>(null)
  const [favourites, setFavourites] = useState<string[]>(() => readList(FAV_KEY))
  const [recents, setRecents] = useState<string[]>(() => readList(RECENT_KEY))
  const [rect, setRect] = useState<DOMRect | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)
  const layer = useLayer(open)
  useEscapeToClose(layer, open ? () => setOpen(false) : undefined)

  const rows = out ? [...collageAnimRows(), ...EXIT_ROWS] : collageAnimRows()
  const label = animLabelOf(value)
  // The wording of everything this control owns, so the exit phase cannot leave a
  // stray "arrive" in a popup that is about leaving.
  const PHASE = out
    ? {
        pick: 'Pick how the photos leave',
        choose: 'Choose an exit…',
        title: 'Photo exits',
        lane: "Forget this photo's own exit and follow the collage's again",
        foot: 'the tile plays the departure the preview and the MP4 run',
      }
    : {
        pick: 'Pick how the photos arrive',
        choose: 'Choose an entrance…',
        title: 'Photo entrances',
        lane: "Forget this photo's own entrance and follow the collage's again",
        foot: 'the tile plays the same animation the preview and the MP4 run',
      }
  // Which group the current name belongs to, from the same list the browser shows:
  // `animFamily` only knows the arrivals, so a departure mode would otherwise reach the
  // chip with no badge at all.
  const familyOf = value === undefined ? undefined
    : out ? EXIT_ROWS.find(r => r.id === value)?.family ?? animFamily(value) : animFamily(value)

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

  const pick = (anim: string) => {
    if (out) onChangeExit?.(anim); else onChange(anim as CollageAnimation)
    const next = [anim, ...recents.filter(x => x !== anim)].slice(0, 12)
    setRecents(next); writeList(RECENT_KEY, next)
    setOpen(false)
  }
  const toggleFavourite = (anim: string) => {
    const next = favourites.includes(anim) ? favourites.filter(x => x !== anim) : [anim, ...favourites]
    setFavourites(next); writeList(FAV_KEY, next)
  }

  // the family tabs, plus a place for the five departures when this is an exit
  const tabs: { id: (typeof FAMILIES)[number]['id'] | typeof EXIT_FAMILY, label: string }[] =
    out ? [...FAMILIES, { id: EXIT_FAMILY, label: 'Departs' }] : FAMILIES
  const count = rows.length

  return <>
    <button type="button" id={id} ref={triggerRef} aria-label={ariaLabel}
      className={`transition-chip anim-chip ${open ? 'open' : ''}`}
      title={value === undefined ? PHASE.pick : `${label}${out ? ' — backwards, as a departure' : ''} — ${rows.find(r => r.id === value)?.hint ?? ''}`}
      onClick={() => setOpen(o => !o)}>
      <i className="chip-symbol"><Sparkles size={11} /></i>
      <span className="chip-name">{value === undefined ? PHASE.choose : label}{out && value !== undefined && <em className="chip-phase">backwards</em>}</span>
      {familyOf && <i className="chip-kind">{FAMILIES.find(f => f.id === familyOf)?.label ?? familyOf}</i>}
      <ChevronDown size={13} />
    </button>
    {open && rect && createPortal(<div className="transition-browser anim-browser" ref={popRef}
      role="dialog" aria-label={PHASE.title}
      style={{
        position: 'fixed', zIndex: layer, width: 470, height: Math.min(452, Math.max(300, window.innerHeight - 40)),
        left: Math.max(8, Math.min(rect.left, window.innerWidth - 478)),
        top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - Math.min(452, Math.max(300, window.innerHeight - 40)) - 8)),
      }}>
      <header>
      <div className="browser-search">
        <Search size={12} />
        <input ref={searchRef} value={query} placeholder={`Search ${count} ${out ? 'exits' : 'entrances'} — name, feel, direction…`}
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
          {tabs.map(f => {
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
            title={PHASE.lane}>
            <span>{inherit.label}</span>
            <Clock3 size={11} />
          </button>}
          <div className="browser-grid" role="listbox" aria-label="Entrances">
            {shown.length === 0 && <p className="browser-empty">Nothing matches “{query}”. Try a feeling
              instead:{' '}<em>springy</em>, <em>soft</em>, <em>tile</em>, <em>colour</em>.</p>}
            {shown.map(r => <AnimTile key={r.id} row={r} out={out}
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
      <p className="browser-count">{shown.length} of {count} · {PHASE.foot}{shown.some(r => animHasDirection(r.id)) ? ' · directionable ones flip with the Reversed toggle' : ''}</p>
    </div>, document.body)}
  </>
}
