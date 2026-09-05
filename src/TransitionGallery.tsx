// Standalone transition gallery: a full-screen window for *looking* at every
// example rather than picking one.
//
// It shares the picker's cached clips, tiles and search, so nothing is
// rendered twice, but adds a large stage: hover a tile to see it move, click
// one to park it on the stage and study it. There is no "apply" — choosing a
// transition for a slide stays with the chips in the storyline.
import { useEffect, useMemo, useRef, useState } from 'react'
import { LayoutGrid, Loader2, RefreshCw, Search, X } from 'lucide-react'
import {
  totalTransitionCount, transitionGroups, isGLTransition, getGLParams,
  loadFavouriteTransitions, toggleFavouriteTransition, transitionPreviewUrl,
  transitionSlug, transitionDirection,
} from './transitionCatalog'
import {
  TransitionTile, buildAllPreviews, usePreviewStatus,
  type PreviewState,
} from './TransitionPicker'

type Scope = 'all' | 'xfade' | 'gl' | 'favourites' | 'recent'

const labelToGroup = new Map<string, string>()
for (const [group, names] of Object.entries(transitionGroups)) for (const name of names) labelToGroup.set(name, group)

export function TransitionGallery({ initial, onClose }: { initial?: string; onClose: () => void }) {
  const [focused, setFocused] = useState(initial || 'Fade')
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState<Scope>('all')
  const [group, setGroup] = useState<string | null>(null)
  const [favourites, setFavourites] = useState<string[]>([])
  const status = usePreviewStatus(true)
  const gridRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => { setFavourites(loadFavouriteTransitions()) }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const sections = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const pool = scope === 'xfade' ? Object.values(transitionGroups).flat().filter(t => !isGLTransition(t))
      : scope === 'gl' ? Object.values(transitionGroups).flat().filter(t => isGLTransition(t))
        : scope === 'favourites' ? favourites
          : Object.values(transitionGroups).flat()
    const matching = (needle ? Object.values(transitionGroups).flat() : pool).filter(t => t.toLowerCase().includes(needle))
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
  }, [query, scope, group, favourites])

  const flat = useMemo(() => sections.flatMap(s => s.items), [sections])
  const state: PreviewState | null = status?.items?.[transitionSlug(focused)]?.status ?? null
  const previewAllowed = status !== null && status.hasFfmpeg !== false
  const canPreview = previewAllowed && (state === 'ready' || state === 'pending')
  const cached = status?.ready ?? 0
  const params = getGLParams(focused)

  return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}>
    <div className="transition-gallery" onMouseDown={e => e.stopPropagation()}>
      <div className="preview-top">
        <div><strong>Transition gallery</strong><span>{totalTransitionCount} EXAMPLES · RENDERED ONCE, THEN CACHED</span></div>
        <button type="button" onClick={onClose} aria-label="Close gallery"><X size={20} /></button>
      </div>

      <div className="gallery-body">
        <div className="gallery-side">
          <label className="gallery-search">
            <Search size={13} />
            <input value={query} placeholder="Search transitions…" aria-label="Search transitions"
              onChange={e => { setQuery(e.target.value); setGroup(null) }} />
            {query && <button type="button" onClick={() => setQuery('')} aria-label="Clear search"><X size={12} /></button>}
          </label>
          <div className="gallery-tabs">
            {([['all', `All ${totalTransitionCount}`], ['xfade', 'XFade 58'], ['gl', 'GL 133'], ['favourites', '★ Favourites']] as [Scope, string][]).map(([key, text]) =>
              <button type="button" key={key} className={scope === key ? 'active' : ''}
                onClick={() => { setScope(key); setGroup(null); setQuery('') }}>{text}</button>)}
          </div>
          <nav className="browser-groups compact" aria-label="Categories">
            <button type="button" className={group === null ? 'active' : ''} onClick={() => setGroup(null)}>All categories</button>
            {(scope === 'xfade' ? Object.keys(transitionGroups).filter(g => !g.startsWith('GL'))
              : scope === 'gl' ? Object.keys(transitionGroups).filter(g => g.startsWith('GL'))
                : Object.keys(transitionGroups)).map(name =>
              <button type="button" key={name} className={group === name ? 'active' : ''} onClick={() => setGroup(name === group ? null : name)}>
                {name.replace(/^GL · /, '')}<b>{Object.values(transitionGroups).flat().filter(t => labelToGroup.get(t) === name).length}</b>
              </button>)}
          </nav>
          <div className="gallery-detail">
            <strong>{focused}</strong>
            <span>{labelToGroup.get(focused)?.replace(/^GL · /, '') || 'Transition'}{isGLTransition(focused) ? ' · GL' : ' · xfade'}</span>
            {state === 'unsupported' && <em className="gallery-warn">Falls back on this FFmpeg build</em>}
            {state === 'failed' && <em className="gallery-warn">Preview could not be rendered</em>}
            {params.length > 0 && <small>{params.length} adjustable parameter{params.length === 1 ? '' : 's'}</small>}
          </div>
        </div>

        <div className="gallery-stage" style={{ '--transition-speed': '.9s' } as React.CSSProperties}>
          {canPreview
            ? <video key={focused} src={transitionPreviewUrl(focused)} autoPlay loop muted playsInline
              onError={e => { (e.currentTarget as HTMLVideoElement).style.display = 'none' }} />
            : null}
          {!canPreview && <div className={`quick-transition ${transitionDirection(focused)}`} aria-hidden>
            <span className="tile-frame a" /><span className="tile-frame b" />
          </div>}
          {state === 'pending' && status?.hasFfmpeg !== false && <span className="tile-loading"><Loader2 size={16} className="spin" /></span>}
        </div>
      </div>

      <div className="gallery-grid" ref={gridRef}>
        {flat.length === 0 && <p className="browser-empty">No transition matches “{query}”.</p>}
        {sections.map(section => <section key={section.name}>
          <strong>{section.name.replace(/^GL · /, '')}</strong>
          <div className="tile-row">
            {section.items.map(label => <TransitionTile
              key={label}
              label={label}
              active={label === focused}
              state={status?.items?.[transitionSlug(label)]?.status ?? null}
              previewAllowed={previewAllowed}
              playing={false}
              favourite={favourites.includes(label)}
              onSelect={() => setFocused(label)}
              onToggleFavourite={() => { toggleFavouriteTransition(label); setFavourites(loadFavouriteTransitions()) }}
              onFocusTile={() => setFocused(label)}
              tileRef={() => { /* the gallery does not manage roving focus */ }}
            />)}
          </div>
        </section>)}
      </div>

      <div className="gallery-foot">
        <span className="browser-count">{flat.length} of {totalTransitionCount}</span>
        <em><LayoutGrid size={11} /> Hover a tile to see it move · click to park it on the big stage</em>
        {status
          ? <span className="browser-cache">
            {status.building
              ? <>Rendering {status.buildDone}/{status.buildTotal} <Loader2 size={10} className="spin" /></>
              : status.hasFfmpeg === false
                ? 'FFmpeg unavailable · approximations shown'
                : `${cached}/${status.total} cached`}
          </span>
          : <span className="browser-cache">Preview cache offline</span>}
        {status && !status.building && status.pending > 0 && status.hasFfmpeg !== false &&
          <button type="button" className="browser-build" onClick={() => void buildAllPreviews()}>
            <RefreshCw size={11} /> Render all {status.pending} missing
          </button>}
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
    </div>
  </div>
}
