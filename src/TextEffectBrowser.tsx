// Text effect browser — the same anatomy as the transition browser
// (TransitionPicker / TransitionGallery): search, tabs, a category rail with
// counts, tiles with a ★, Recent and Presets, and "Open full gallery".
//
// Tiles are live: each one is the effect on the user's own font, rendered by
// the preview engine (the JavaScript twin of the MP4 renderer). Hovering a
// tile previews it ON TOP of the current stack on the editor stage (or the
// gallery stage) — that is how effects are combined without trial renders.
// Badges: unit (CH / W / L / T), ≈ approximation of a reference, conflicts
// ("replaces Typewriter") and "needs colour B" for the background effects.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'
import { Check, LayoutGrid, Search, Sparkles, Star, X } from 'lucide-react'
import type { BgChange } from './textMotionCore'
import {
  CATEGORIES, CATEGORY_BY_ID, EFFECTS, EFFECT_LIST, PHASE_LABEL, PRESETS, UNIT_LABEL, UNIT_SHORT,
  candidateConflict, effectNeedsBg, readFavourites, readRecent, readSavedPresets, sampleStackFor, writeFavourites,
  type EffectDef, type MotionPreset, type Phase, type TextFxLayer,
} from './textFx'
import { MotionStage, MotionTile, useMotionClock } from './TextMotionStage'
import type { SceneInput } from './textMotionScene'

export type BrowserTab = 'all' | Phase | 'fav' | 'recent' | 'presets'

/** The caption the browser previews: the user's own font, colour and frame. */
export interface BrowserCaption {
  text: string
  family: string
  bold: boolean
  italic: boolean
  colour: string
  isFrame: boolean
  background?: string           // text frame colour A
  bgChange?: { colourB: string; transition: string } | null
}

export interface BrowserRequest {
  mode: 'add' | 'replace' | 'presets'
  phase?: Phase | null
  replacing?: TextFxLayer | null
}

const TILE_SECONDS = 2.4
const TILE_BG: BgChange = { colourA: '#26372f', colourB: '#e9c46a', transition: 'wiperight', start: 0.45, time: 1.1 }

function tileText(fx: EffectDef | undefined, caption: BrowserCaption) {
  if (fx?.content?.type === 'count') return '2026'
  if (fx?.content?.type === 'countdown') return '3'
  const word = caption.text.split(/\s+/).find(w => w.length >= 2) || 'Text'
  const sample = word.replace(/[^\p{L}\p{N}&'-]/gu, '').slice(0, 9) || 'Text'
  if (fx?.unit === 'line') return `${sample}\nlines`
  return sample
}

function tileInput(stack: TextFxLayer[], caption: BrowserCaption, text: string, font?: MotionPreset['font']): SceneInput {
  return {
    text, stack, family: font?.family || caption.family, bold: font?.bold ?? caption.bold, italic: font?.italic ?? caption.italic,
    underline: false, colour: caption.colour, fontSize: text.includes('\n') ? 210 : 290, x: 50, y: 50, align: 'center', outline: false,
    start: 0, end: TILE_SECONDS, steady: 0, bg: null, motion: null,
  }
}

function tileBg(caption: BrowserCaption): BgChange {
  if (caption.isFrame && caption.background && caption.bgChange) {
    return { colourA: caption.background, colourB: caption.bgChange.colourB, transition: caption.bgChange.transition, start: 0.45, time: 1.1 }
  }
  return TILE_BG
}

function matches(fx: EffectDef, needle: string) {
  if (!needle) return true
  const cat = CATEGORY_BY_ID[fx.category]?.label || ''
  return `${fx.label} ${cat} ${fx.source || ''} ${(fx.tags || []).join(' ')} ${fx.notes || ''} ${PHASE_LABEL[fx.phase as Phase]}`.toLowerCase().includes(needle)
}

export function EffectBrowserBody({ request, stack, caption, onPick, onPreset, onHover, onFocus, footerExtra, compact = false, autoplayDefault = false }: {
  request: BrowserRequest
  stack: TextFxLayer[]
  caption: BrowserCaption
  onPick: (effectId: string) => void
  onPreset: (preset: MotionPreset) => void
  onHover?: (effectId: string | null, preset?: MotionPreset | null) => void
  onFocus?: (fx: EffectDef | null, preset?: MotionPreset | null) => void
  footerExtra?: React.ReactNode
  compact?: boolean
  autoplayDefault?: boolean
}) {
  const replacing = request.mode === 'replace' ? request.replacing || null : null
  const lockedPhase: Phase | null = replacing ? (EFFECTS[replacing.effect]?.phase as Phase) : null
  const [tab, setTab] = useState<BrowserTab>(request.mode === 'presets' ? 'presets' : (request.phase || lockedPhase || 'all'))
  const [category, setCategory] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [favourites, setFavourites] = useState<string[]>(() => readFavourites())
  const [recent] = useState<string[]>(() => readRecent())
  const [saved] = useState<MotionPreset[]>(() => readSavedPresets())
  const [hovered, setHovered] = useState<string | null>(null)
  const [autoplay, setAutoplay] = useState(autoplayDefault)
  const hasBg = Boolean(caption.isFrame && caption.bgChange)
  const searchRef = useRef<HTMLInputElement | null>(null)
  useEffect(() => { searchRef.current?.focus() }, [])

  const pool = useMemo(() => EFFECT_LIST.filter(fx => !lockedPhase || fx.phase === lockedPhase), [lockedPhase])
  const needle = query.trim().toLowerCase()
  const items: EffectDef[] = useMemo(() => {
    let list = pool
    if (tab === 'in' || tab === 'hold' || tab === 'out') list = list.filter(fx => fx.phase === tab)
    else if (tab === 'fav') list = list.filter(fx => favourites.includes(fx.id))
    else if (tab === 'recent') list = recent.map(id => EFFECTS[id]).filter(fx => fx && (!lockedPhase || fx.phase === lockedPhase))
    return list.filter(fx => matches(fx, needle))
  }, [pool, tab, favourites, recent, needle, lockedPhase])
  const presets = useMemo(() => {
    const all = [...PRESETS.map(p => ({ ...p, section: 'Curated looks' })), ...saved.map(p => ({ ...p, section: 'My presets' }))]
    return all.filter(p => (caption.isFrame || !p.frame) && (!needle || `${p.label} ${p.description || ''}`.toLowerCase().includes(needle)))
  }, [saved, needle, caption.isFrame])

  const counts: Record<BrowserTab, number> = {
    all: pool.length,
    in: pool.filter(f => f.phase === 'in').length,
    hold: pool.filter(f => f.phase === 'hold').length,
    out: pool.filter(f => f.phase === 'out').length,
    fav: pool.filter(f => favourites.includes(f.id)).length,
    recent: recent.filter(id => EFFECTS[id] && (!lockedPhase || EFFECTS[id].phase === lockedPhase)).length,
    presets: presets.length,
  }
  const tabs: [BrowserTab, string][] = [['all', 'All'], ['in', 'Enter'], ['hold', 'While shown'], ['out', 'Exit'], ['fav', '★'], ['recent', 'Recent'], ['presets', 'Presets']]
  const sections = useMemo(() => {
    const out: { id: string; label: string; symbol: string; items: EffectDef[] }[] = []
    for (const c of CATEGORIES) {
      const list = items.filter(fx => fx.category === c.id)
      if (list.length) out.push({ ...c, items: list })
    }
    return out
  }, [items])

  const toggleFav = (id: string) => {
    const next = favourites.includes(id) ? favourites.filter(x => x !== id) : [...favourites, id]
    setFavourites(next)
    writeFavourites(next)
  }
  const hover = (id: string | null, preset?: MotionPreset | null) => {
    setHovered(id || (preset ? `preset:${preset.id}` : null))
    onHover?.(id, preset)
    if (id) onFocus?.(EFFECTS[id] || null, null)
    else if (preset) onFocus?.(null, preset)
  }

  const effectTile = (fx: EffectDef) => {
    const conflict = candidateConflict(stack, fx.id, { replacingId: replacing?.id, hasBg })
    const fav = favourites.includes(fx.id)
    const active = replacing?.effect === fx.id
    const needsBg = effectNeedsBg(fx)
    const input = tileInput(sampleStackFor(fx.id), caption, tileText(fx, caption))
    const bg = needsBg || fx.sync === 'bg' ? tileBg(caption) : null
    return <button type="button" key={fx.id} className={`transition-tile text-motion-tile${active ? ' active' : ''}${conflict === 'needs colour B' ? ' dim' : ''}`}
      title={`${fx.label} · ${PHASE_LABEL[fx.phase as Phase]} · ${UNIT_LABEL[fx.unit] || fx.unit}${fx.source ? `\n${fx.source}` : ''}${fx.notes ? `\n${fx.notes}` : ''}${conflict ? `\n⚠ ${conflict}` : ''}`}
      onMouseEnter={() => hover(fx.id)} onMouseLeave={() => hover(null)} onFocus={() => hover(fx.id)} onBlur={() => hover(null)}
      onClick={() => onPick(fx.id)}>
      <span className="tile-stage">
        <MotionTile input={input} duration={TILE_SECONDS} live={autoplay || hovered === fx.id} background={bg ? bg.colourA : '#20231f'} bg={bg} />
        <span className="tile-unit">{UNIT_SHORT[fx.unit] || 'T'}</span>
        {conflict && <span className="tile-flag" title={conflict}>{conflict}</span>}
        {fx.approx && <span className="tile-flag approx" title={`Approximation: ${fx.approx}`}>≈</span>}
        <span role="button" tabIndex={-1} className={`tile-star${fav ? ' on' : ''}`} title={fav ? 'Remove from favourites' : 'Add to favourites'}
          onClick={e => { e.stopPropagation(); toggleFav(fx.id) }}><Star size={11} fill={fav ? 'currentColor' : 'none'} /></span>
      </span>
      <span className="tile-name"><i className="tile-symbol">{fx.symbol}</i>{fx.label}</span>
    </button>
  }
  const presetTile = (p: MotionPreset & { section?: string }) => {
    const layers = p.layers.map((l, i) => ({ ...l, id: l.id || `p${i}` }))
    const input = tileInput(layers, caption, tileText(undefined, caption), p.font)
    const bg: BgChange | null = p.frame ? { colourA: p.frame.background, colourB: p.frame.background2, transition: p.frame.transition, start: 0.45, time: 1.1 } : null
    const title = `${p.label}${p.description ? ` — ${p.description}` : ''}\n${layers.map(l => `${PHASE_LABEL[EFFECTS[l.effect]?.phase as Phase] || ''}: ${EFFECTS[l.effect]?.label || l.effect}`).join('\n')}${p.font ? `\nFont: ${p.font.family}` : ''}${p.frame ? '\nSets the frame colours A → B' : ''}`
    return <button type="button" key={`${p.mine ? 'mine' : 'cur'}-${p.id}`} className="transition-tile text-motion-tile" title={title}
      onMouseEnter={() => hover(null, p)} onMouseLeave={() => hover(null)} onClick={() => onPreset(p)}>
      <span className="tile-stage">
        <MotionTile input={input} duration={TILE_SECONDS} live={autoplay || hovered === `preset:${p.id}`} background={bg ? bg.colourA : '#20231f'} bg={bg} />
        <span className="tile-unit">{layers.length} layers</span>
        {p.font && <span className="tile-flag approx" title={`Sets the font: ${p.font.family}`}>Aa</span>}
        {p.frame && <span className="tile-flag" title="Also sets the frame colours A → B">A→B</span>}
      </span>
      <span className="tile-name"><i className="tile-symbol">{p.symbol || '★'}</i>{p.label}</span>
    </button>
  }

  const presetSections = tab === 'presets' ? ['Curated looks', 'My presets'].map(name => ({ name, items: presets.filter(p => p.section === name) })).filter(s => s.items.length) : []
  const modeLabel = tab === 'presets' ? 'Presets · replace the stack' : replacing ? `Swap ${EFFECTS[replacing.effect]?.label || 'effect'}` : request.phase ? `Add to ${PHASE_LABEL[request.phase]}` : 'Add effect'

  return <>
    <header>
      <span className="browser-mode">{modeLabel}</span>
      <label className="browser-search"><Search size={13} /><input ref={searchRef} value={query} placeholder="Search effects, sources, tags…" onChange={e => setQuery(e.target.value)} />{query && <button type="button" onClick={() => setQuery('')} aria-label="Clear search"><X size={12} /></button>}</label>
    </header>
    <div className="browser-tabs">
      {tabs.map(([id, label]) => {
        const disabled = (lockedPhase !== null && (id === 'in' || id === 'hold' || id === 'out') && id !== lockedPhase) || (replacing !== null && id === 'presets')
        return <button type="button" key={id} className={tab === id ? 'active' : ''} disabled={disabled} onClick={() => { setTab(id); setCategory(null) }}>{label}<b>{counts[id]}</b></button>
      })}
    </div>
    <div className={`browser-body${compact ? ' compact' : ''}`}>
      <nav className="browser-groups" aria-label="Categories">
        {tab === 'presets'
          ? presetSections.map(s => <button type="button" key={s.name} className="active">{s.name}<b>{s.items.length}</b></button>)
          : <>
            <button type="button" className={category === null ? 'active' : ''} onClick={() => setCategory(null)}>All<b>{items.length}</b></button>
            {sections.map(s => <button type="button" key={s.id} className={category === s.id ? 'active' : ''} onClick={() => setCategory(category === s.id ? null : s.id)}><span className="tile-symbol">{s.symbol}</span>{s.label}<b>{s.items.length}</b></button>)}
          </>}
      </nav>
      <div className="browser-grid-wrap"><div className="browser-grid">
        {tab === 'presets'
          ? (presetSections.length ? presetSections.map(s => <section key={s.name}><strong>{s.name}</strong><div className="tile-row">{s.items.map(presetTile)}</div></section>)
            : <p className="browser-empty">No preset matches.</p>)
          : (sections.length ? sections.filter(s => !category || s.id === category).map(s => <section key={s.id}><strong>{s.symbol} {s.label}</strong><div className="tile-row">{s.items.map(effectTile)}</div></section>)
            : <p className="browser-empty">{tab === 'fav' ? 'Star effects to collect them here.' : tab === 'recent' ? 'Effects you add appear here.' : `No effect matches “${query}”.`}</p>)}
      </div></div>
    </div>
    <footer>
      <span className="browser-count">{tab === 'presets' ? `${presets.length} presets` : `${items.length} effects · all stackable`}</span>
      <label className="check-label tiny"><input type="checkbox" checked={autoplay} onChange={e => setAutoplay(e.target.checked)} /><span><Check size={9} /></span>Autoplay tiles</label>
      {footerExtra}
    </footer>
  </>
}

/** Popover anchored to a lane button / layer name / preset chip. */
export function TextEffectBrowser({ request, anchor, stack, caption, onPick, onPreset, onHover, onClose, onOpenGallery }: {
  request: BrowserRequest
  anchor: DOMRect | null
  stack: TextFxLayer[]
  caption: BrowserCaption
  onPick: (effectId: string) => void
  onPreset: (preset: MotionPreset) => void
  onHover?: (effectId: string | null, preset?: MotionPreset | null) => void
  onClose: () => void
  onOpenGallery?: () => void
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState<{ left: number; top: number; width: number; height: number }>({ left: 20, top: 20, width: 700, height: 470 })
  useLayoutEffect(() => {
    const vw = window.innerWidth, vh = window.innerHeight
    const width = Math.min(760, vw - 16)
    const height = Math.min(480, vh - 16)
    const r = anchor || new DOMRect(vw / 2 - width / 2, vh / 2 - height / 2, 0, 0)
    const left = Math.max(8, Math.min(vw - width - 8, r.left))
    const below = r.bottom + 6
    const top = below + height <= vh - 8 ? below : Math.max(8, r.top - height - 6)
    setPos({ left, top, width, height })
  }, [anchor])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose() }
    window.addEventListener('keydown', onKey, true)
    const timer = window.setTimeout(() => document.addEventListener('mousedown', onDown), 0)
    return () => { window.removeEventListener('keydown', onKey, true); window.clearTimeout(timer); document.removeEventListener('mousedown', onDown) }
  }, [onClose])
  return <div ref={ref} className="transition-browser text-effect-browser" style={{ left: pos.left, top: pos.top, width: pos.width, height: pos.height }}
    onMouseDown={e => e.stopPropagation()}>
    <EffectBrowserBody request={request} stack={stack} caption={caption} onPick={onPick} onPreset={onPreset} onHover={onHover} compact
      footerExtra={<>
        {onOpenGallery && <button type="button" className="browser-build" onClick={onOpenGallery}><LayoutGrid size={11} /> Open full gallery</button>}
        <button type="button" className="browser-close" onClick={onClose} aria-label="Close"><X size={14} /></button>
      </>} />
  </div>
}

function describe(fx: EffectDef) {
  const channels = Object.keys(fx.tracks || {}).map(ch => (ch === 'vx' || ch === 'vy' ? `${ch} (frame)` : ch))
  const extras = [
    ...(fx.copies || []).map(c => `copy: ${c.type}`),
    ...(fx.content ? [`rewrites text: ${fx.content.type}`] : []),
    ...(fx.caret ? ['caret'] : []),
    ...(fx.colourWipe ? ['colour wipe'] : []),
    ...(fx.bg ? [`background: ${fx.bg.mode}`] : []),
    ...(fx.sync === 'bg' ? ['timed to the colour change'] : []),
  ]
  return { channels, extras }
}

/** Full gallery (mirror of TransitionGallery): the user's caption and stack on
 * a big stage, the hovered effect on top, details, and the whole catalogue. */
export function TextEffectGallery({ request, stack, caption, sceneFor, onPick, onPreset, onClose }: {
  request: BrowserRequest
  stack: TextFxLayer[]
  caption: BrowserCaption
  /** Stage input for a candidate stack (the editor's caption with that stack). */
  sceneFor: (stack: TextFxLayer[]) => { input: SceneInput; background?: string; bg?: BgChange | null; duration: number }
  onPick: (effectId: string, phase: Phase) => void
  onPreset: (preset: MotionPreset) => void
  onClose: () => void
}) {
  const [candidate, setCandidate] = useState<{ effect: string | null; preset: MotionPreset | null }>({ effect: null, preset: null })
  const [focus, setFocus] = useState<{ fx: EffectDef | null; preset: MotionPreset | null }>({ fx: null, preset: null })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])
  const previewStack = useMemo(() => {
    if (candidate.preset) return candidate.preset.layers.map((l, i) => ({ ...l, id: l.id || `p${i}` }))
    if (!candidate.effect) return stack
    if (request.mode === 'replace' && request.replacing) return stack.map(l => (l.id === request.replacing!.id ? { ...l, effect: candidate.effect!, params: undefined } : l))
    return [...stack, { id: 'candidate', effect: candidate.effect }]
  }, [candidate, stack, request])
  const scene = sceneFor(previewStack)
  const clock = useMotionClock(scene.duration)
  const flag = candidate.preset ? `Preset: ${candidate.preset.label}` : candidate.effect ? `${request.mode === 'replace' ? 'Swap →' : 'Previewing +'} ${EFFECTS[candidate.effect]?.label}` : null
  const fx = focus.fx
  const info = fx ? describe(fx) : null
  return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}>
    <div className="transition-gallery text-effect-gallery" onMouseDown={e => e.stopPropagation()}>
      <div className="preview-top"><div><strong>Text effects gallery</strong><span>HOVER A TILE TO PREVIEW IT ON TOP OF YOUR STACK · CLICK TO ADD</span></div><button type="button" onClick={onClose} aria-label="Close gallery"><X size={20} /></button></div>
      <div className="gallery-body text-effect-gallery-body">
        <div className="gallery-left">
          <MotionStage className="gallery-motion-stage" input={scene.input} clock={clock} background={scene.background} bg={scene.bg} flag={flag} />
          <div className="gallery-detail">
            {fx ? <>
              <strong><span className="tile-symbol">{fx.symbol}</span> {fx.label}</strong>
              <span>{PHASE_LABEL[fx.phase as Phase]} · {CATEGORY_BY_ID[fx.category]?.label} · per {UNIT_LABEL[fx.unit]?.toLowerCase() || fx.unit}{fx.duration ? ` · ${fx.duration}s` : ''}</span>
              <div className="detail-tags">{info!.channels.map(c => <em key={c}>{c}</em>)}{info!.extras.map(c => <em key={c} className="extra">{c}</em>)}{fx.approx ? <em className="approx">≈ {fx.approx}</em> : <em className="exact">exact in the render</em>}</div>
              {fx.notes && <small>{fx.notes}</small>}
              {fx.source && <small>Source: {fx.source}</small>}
              <small>{fx.content ? 'Rewrites the text: only one text-rewriting effect per lane; everything else still stacks.' : effectNeedsBg(fx) ? 'Needs a text frame with a colour change (colour B): it follows the frame transition exactly.' : 'Combines with every other effect: channels are composed, not overwritten.'}</small>
              <div className="detail-actions">
                <button type="button" className="btn dark small" onClick={() => onPick(fx.id, fx.phase as Phase)}><Sparkles size={12} /> {request.mode === 'replace' && request.replacing && EFFECTS[request.replacing.effect]?.phase === fx.phase ? 'Swap in' : `Add to ${PHASE_LABEL[fx.phase as Phase]}`}</button>
              </div>
            </> : focus.preset ? <>
              <strong><span className="tile-symbol">{focus.preset.symbol}</span> {focus.preset.label}</strong>
              <span>{focus.preset.description}</span>
              <div className="detail-tags">{focus.preset.layers.map((l, i) => <em key={i}>{PHASE_LABEL[EFFECTS[l.effect]?.phase as Phase]}: {EFFECTS[l.effect]?.label}</em>)}</div>
              {focus.preset.font && <small>Sets the font to {focus.preset.font.family}.</small>}
              {focus.preset.frame && <small>Sets the frame colours and the A → B transition ({focus.preset.frame.transition}).</small>}
              <div className="detail-actions"><button type="button" className="btn dark small" onClick={() => onPreset(focus.preset!)}><Sparkles size={12} /> Use this preset</button></div>
            </> : <span>Hover a tile to see what it animates and how it combines. The stage shows your own caption and stack.</span>}
          </div>
        </div>
        <div className="transition-browser text-effect-browser in-gallery">
          <EffectBrowserBody request={request} stack={stack} caption={caption} autoplayDefault={false}
            onPick={id => onPick(id, EFFECTS[id].phase as Phase)} onPreset={onPreset}
            onHover={(id, preset) => { if (id || preset) setCandidate({ effect: id, preset: preset || null }) }}
            onFocus={(f, p) => setFocus({ fx: f, preset: p || null })}
            footerExtra={<button type="button" className="btn ghost small" onClick={onClose}>Close</button>} />
        </div>
      </div>
    </div>
  </div>
}
