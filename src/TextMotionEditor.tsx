// Text animation editor: the stack of effects of one caption.
//
//   ● ENTER        ⋮⋮ ⊕ Pop in        Letter  0.60 s  ⚙ ◉ ✕
//   ● WHILE SHOWN  ⋮⋮ ∿ Wave          Letter  ↻ 1.8 s ⚙ ◉ ✕
//   ● EXIT         ⋮⋮ ⊖ Pop out       Text    0.50 s  ⚙ ◉ ✕
//
// Every lane holds any number of layers; they combine (channels are composed
// by the engine, see src/textMotionCore.ts). The browser (TextEffectBrowser)
// opens to add or swap an effect; hovering one of its tiles previews it on top
// of the current stack on the editor stage. Presets replace the whole stack;
// the current stack can be saved as a preset (per browser, like favourites).
// TextMotionTimeline draws the lanes over time — with the text frame's colour
// A → B background change on the same clock — and edits durations by drag.
import { useEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'
import { ChevronDown, Eye, EyeOff, GripVertical, LayoutGrid, Pause, Play, Plus, Save, Settings2, Sparkles, Trash2, X } from 'lucide-react'
import { layerSpan, type BgChange } from './textMotionCore'
import {
  CATEGORY_BY_ID, EFFECTS, LOOP_LABEL, ORDER_LABEL, PHASES, PHASE_LABEL, UNIT_LABEL, UNIT_SHORT, effectNeedsBg, effectSyncsToBg,
  insertLayer, layersOf, newLayerId, pushRecent, readSavedPresets, withIds, writeSavedPresets,
  type EffectDef, type MotionPreset, type Phase, type TextFxLayer,
} from './textFx'
import { TextEffectBrowser, TextEffectGallery, type BrowserCaption, type BrowserRequest } from './TextEffectBrowser'
import type { MotionClock, SceneInput } from './textMotionScene'

const fmt = (s: number) => (Math.round(s * 100) / 100).toFixed(2)
const COLOUR_TOKENS: [string, string][] = [['base', 'Text colour'], ['bgA', 'Colour A'], ['bgB', 'Colour B'], ['contrastA', 'Readable on A'], ['contrastB', 'Readable on B']]

export interface StackEditorProps {
  stack: TextFxLayer[]
  onChange: (stack: TextFxLayer[]) => void
  caption: BrowserCaption
  /** Seconds the caption is on screen (lane spans, loop labels). */
  windowSeconds: number
  wordCount: number
  conflicts?: Record<number, string>
  warnings?: string[]
  /** Hover preview: a candidate stack for the stage (null = back to the stack). */
  onCandidate?: (stack: TextFxLayer[] | null, flag: string | null) => void
  /** The editor's caption with another stack, for the gallery's big stage. */
  sceneFor?: (stack: TextFxLayer[]) => { input: SceneInput; background?: string; bg?: BgChange | null; duration: number }
  /** A preset asks for a font / frame colours (text frames): the parent applies it. */
  onPresetExtras?: (preset: MotionPreset) => void
  onEditPath?: () => void
  compact?: boolean
  light?: boolean
}

export function TextMotionEditor({ stack, onChange, caption, windowSeconds, wordCount, conflicts = {}, warnings = [], onCandidate, sceneFor, onPresetExtras, onEditPath, compact = false, light = false }: StackEditorProps) {
  const [browser, setBrowser] = useState<(BrowserRequest & { anchor: DOMRect | null }) | null>(null)
  const [gallery, setGallery] = useState<BrowserRequest | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [preset, setPreset] = useState<string | null>(null)
  const [dragId, setDragId] = useState<string | null>(null)
  const hasBg = Boolean(caption.isFrame && caption.bgChange)

  const commit = (next: TextFxLayer[], keepPreset = false) => {
    if (!keepPreset) setPreset(null)
    onChange(next)
  }
  const update = (id: string, patch: Partial<TextFxLayer>) => commit(stack.map(l => (l.id === id ? { ...l, ...patch } : l)))
  const setParam = (layer: TextFxLayer, name: string, value: unknown) => update(layer.id!, { params: { ...(layer.params || {}), [name]: value } })

  const pick = (effectId: string, req: BrowserRequest) => {
    pushRecent(effectId)
    if (req.mode === 'replace' && req.replacing && EFFECTS[req.replacing.effect]?.phase === EFFECTS[effectId]?.phase) {
      commit(stack.map(l => (l.id === req.replacing!.id ? { id: l.id, effect: effectId, ...(l.muted ? { muted: true } : {}) } : l)))
    } else {
      const layer: TextFxLayer = { id: newLayerId(), effect: effectId }
      commit(insertLayer(stack, layer))
      setOpen(layer.id!)
    }
    onCandidate?.(null, null)
  }
  const applyPreset = (p: MotionPreset) => {
    commit(withIds(p.layers), true)
    setPreset(p.label)
    onPresetExtras?.(p)
    onCandidate?.(null, null)
    setBrowser(null)
    setGallery(null)
  }
  const savePreset = () => {
    const name = window.prompt('Name for this preset (saved in this browser)', preset ? `${preset} (mine)` : 'My text animation')
    if (!name) return
    const saved = readSavedPresets()
    const entry: MotionPreset = { id: `mine-${Date.now().toString(36)}`, label: name.slice(0, 60), symbol: '★', description: 'Saved from the editor', layers: stack.map(({ id, ...l }) => l as TextFxLayer) }
    writeSavedPresets([...saved.filter(p => p.label !== entry.label), entry])
    setPreset(entry.label)
  }
  const hover = (req: BrowserRequest) => (effectId: string | null, p?: MotionPreset | null) => {
    if (!onCandidate) return
    if (p) { onCandidate(p.layers.map((l, i) => ({ ...l, id: l.id || `p${i}` })), `Preset: ${p.label}`); return }
    if (!effectId) { onCandidate(null, null); return }
    const fx = EFFECTS[effectId]
    if (req.mode === 'replace' && req.replacing && EFFECTS[req.replacing.effect]?.phase === fx.phase) {
      onCandidate(stack.map(l => (l.id === req.replacing!.id ? { ...l, effect: effectId, params: undefined } : l)), `Swap → ${fx.label}`)
    } else onCandidate(insertLayer(stack, { id: 'candidate', effect: effectId }), `Previewing + ${fx.label}`)
  }
  const openBrowser = (req: BrowserRequest, el: HTMLElement) => setBrowser({ ...req, anchor: el.getBoundingClientRect() })
  const closeBrowser = () => { setBrowser(null); onCandidate?.(null, null) }

  const reorder = (fromId: string, toId: string) => {
    if (fromId === toId) return
    const from = stack.find(l => l.id === fromId)
    const to = stack.find(l => l.id === toId)
    if (!from || !to || EFFECTS[from.effect]?.phase !== EFFECTS[to.effect]?.phase) return
    const next = stack.filter(l => l.id !== fromId)
    next.splice(next.findIndex(l => l.id === toId), 0, from)
    commit(next)
  }

  const laneSpan = (phase: Phase) => {
    const layers = layersOf(stack, phase).filter(l => !l.muted)
    if (!layers.length) return ''
    if (phase === 'hold') return 'whole window'
    return `${fmt(Math.max(...layers.map(l => layerSpan(l, EFFECTS[l.effect], windowSeconds))))} s`
  }

  return <div className={`text-motion-editor${compact ? ' compact' : ''}${light ? ' light' : ''}`}>
    <div className="tme-head">
      <strong>Text animation</strong>
      <span className="grow" />
      <button type="button" className="transition-chip tme-preset" title="Presets replace the whole stack" onClick={e => openBrowser({ mode: 'presets' }, e.currentTarget)}>
        <i className="chip-symbol">★</i><span className="chip-name">{preset || 'Custom stack'}</span><i className="chip-kind">PRESET</i><ChevronDown size={12} />
      </button>
      <button type="button" className="btn ghost small" title="Save this stack as a preset (in this browser)" onClick={savePreset} disabled={!stack.length}><Save size={12} /> Save</button>
      {sceneFor && <button type="button" className="icon-button" title="Open the full text effects gallery" onClick={() => setGallery({ mode: 'add', phase: null })}><LayoutGrid size={13} /></button>}
    </div>
    <div className="tme-lanes">
      {PHASES.map(ph => {
        const layers = layersOf(stack, ph.id)
        return <div key={ph.id} className={`tme-lane ${ph.id}`}>
          <header><span className="dot" /><strong>{ph.label.toUpperCase()}</strong><span className="count">{layers.length || ''}</span><span className="span">{laneSpan(ph.id)}</span></header>
          <div className="tme-layers">
            {!layers.length && <div className="tme-empty">{ph.empty}</div>}
            {layers.map(layer => {
              const idx = stack.indexOf(layer)
              const fx = EFFECTS[layer.effect]
              if (!fx) return null
              const conflict = conflicts[idx]
              const isOpen = open === layer.id
              const unit = layer.unit || fx.unit
              const loop = layer.loop || fx.loop || 'loop'
              const dur = layer.duration ?? fx.duration
              const timeLabel = ph.id === 'hold' ? (loop === 'once' ? (dur ? `${fmt(dur)} s` : 'once') : `↻ ${fmt(dur ?? 2)} s`) : `${fmt(dur ?? 0.5)} s`
              return <div key={layer.id} className={`tme-layer${layer.muted ? ' muted' : ''}${conflict ? ' lost' : ''}${dragId === layer.id ? ' dragging' : ''}`}
                draggable onDragStart={e => { e.dataTransfer.setData('text/plain', layer.id!); setDragId(layer.id!) }} onDragEnd={() => setDragId(null)}
                onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); reorder(e.dataTransfer.getData('text/plain'), layer.id!) }}>
                <div className="tme-row">
                  <span className="grip" title="Drag to reorder inside the lane (the top layer wins for colour)"><GripVertical size={12} /></span>
                  <i className="chip-symbol">{fx.symbol}</i>
                  <button type="button" className="tme-name" title={`Swap for another ${PHASE_LABEL[ph.id].toLowerCase()} effect`} onClick={e => openBrowser({ mode: 'replace', phase: ph.id, replacing: layer }, e.currentTarget)}>
                    {fx.label}<small>{CATEGORY_BY_ID[fx.category]?.label}</small>
                  </button>
                  {(layer.sync === 'bg' || effectSyncsToBg(fx)) && hasBg && <span className="tme-badge bg" title="Timed to the frame's colour change">A→B</span>}
                  {layer.range && <span className="tme-badge" title="Only these words">W{layer.range[0] + 1}–{layer.range[1] + 1}</span>}
                  <span className="tme-badge unit" title="Animated unit">{UNIT_SHORT[unit] || unit}</span>
                  <span className="tme-time">{timeLabel}</span>
                  <button type="button" className={`icon-button tiny${isOpen ? ' on' : ''}`} title="Timing & parameters" onClick={() => setOpen(isOpen ? null : layer.id!)}><Settings2 size={12} /></button>
                  <button type="button" className="icon-button tiny" title={layer.muted ? 'Unmute' : 'Mute (kept in the stack, skipped in preview and render)'} onClick={() => update(layer.id!, { muted: !layer.muted || undefined })}>{layer.muted ? <EyeOff size={12} /> : <Eye size={12} />}</button>
                  <button type="button" className="icon-button tiny" title="Remove" onClick={() => commit(stack.filter(l => l.id !== layer.id))}><Trash2 size={12} /></button>
                </div>
                {conflict && <span className="tme-conflict">⚠ {conflict}</span>}
                {isOpen && <LayerParams layer={layer} fx={fx} hasBg={hasBg} isFrame={caption.isFrame} wordCount={wordCount} onPatch={p => update(layer.id!, p)} onParam={(n, v) => setParam(layer, n, v)} onEditPath={onEditPath} />}
              </div>
            })}
          </div>
          <div className="tme-add"><button type="button" onClick={e => openBrowser({ mode: 'add', phase: ph.id }, e.currentTarget)}><Plus size={12} /> {ph.add}</button></div>
        </div>
      })}
    </div>
    {warnings.length > 0 && <div className="tme-warnings">{warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}</div>}
    {browser && <TextEffectBrowser request={browser} anchor={browser.anchor} stack={stack} caption={caption}
      onPick={id => { pick(id, browser); closeBrowser() }} onPreset={applyPreset} onHover={hover(browser)} onClose={closeBrowser}
      onOpenGallery={sceneFor ? () => { setGallery(browser); setBrowser(null); onCandidate?.(null, null) } : undefined} />}
    {gallery && sceneFor && <TextEffectGallery request={gallery} stack={stack} caption={caption} sceneFor={sceneFor}
      onPick={(id) => { pick(id, gallery) ; if (gallery.mode === 'replace') setGallery(null) }} onPreset={applyPreset} onClose={() => setGallery(null)} />}
  </div>
}

function NumberField({ label, value, placeholder, min, max, step = 0.05, suffix, onChange, title }: {
  label: string; value: number | undefined | null; placeholder?: string; min?: number; max?: number; step?: number; suffix?: string; title?: string
  onChange: (v: number | undefined) => void
}) {
  return <label className="tme-field" title={title}>{label}
    <span><input type="number" value={value ?? ''} placeholder={placeholder} min={min} max={max} step={step}
      onChange={e => { const raw = e.target.value; if (raw === '') { onChange(undefined); return } const n = Number(raw); if (Number.isFinite(n)) onChange(min !== undefined || max !== undefined ? Math.max(min ?? -Infinity, Math.min(max ?? Infinity, n)) : n) }} />
      {suffix && <em>{suffix}</em>}</span>
  </label>
}

function LayerParams({ layer, fx, hasBg, isFrame, wordCount, onPatch, onParam, onEditPath }: {
  layer: TextFxLayer
  fx: EffectDef
  hasBg: boolean
  isFrame: boolean
  wordCount: number
  onPatch: (p: Partial<TextFxLayer>) => void
  onParam: (name: string, value: unknown) => void
  onEditPath?: () => void
}) {
  const phase = fx.phase as Phase
  const units = (fx.units && fx.units.length ? fx.units : ['char', 'word', 'line', 'text']) as string[]
  const loop = layer.loop || fx.loop || 'loop'
  const fixedUnit = Boolean(fx.content && ['count', 'countdown'].includes(fx.content.type)) || fx.id === 'motion-path' || effectNeedsBg(fx)
  const durationLabel = phase === 'hold' ? (loop === 'once' ? 'Travel time' : 'Loop period') : 'Duration'
  const durationTitle = phase === 'hold'
    ? (loop === 'once' ? 'Seconds from start to end value · empty = the whole text window (minus the steady tail)' : 'Seconds per cycle')
    : 'Seconds the whole layer takes, all units included'
  return <div className="tme-params">
    {!fixedUnit && <label className="tme-field">Unit<select value={layer.unit || fx.unit} onChange={e => onPatch({ unit: e.target.value as any })}>
      {units.map(u => <option key={u} value={u}>{UNIT_LABEL[u] || u}</option>)}</select></label>}
    {fx.id !== 'motion-path' && !effectNeedsBg(fx) && <NumberField label={durationLabel} title={durationTitle} value={layer.duration} placeholder={fx.duration ? String(fx.duration) : phase === 'hold' ? 'window' : '0.5'} min={0.05} max={60} step={0.05} suffix="s" onChange={v => onPatch({ duration: v })} />}
    {(layer.unit || fx.unit) !== 'text' && <label className="tme-field" title="0 = all units together · 1 = strictly one after another">Stagger<span><input type="range" min={0} max={1} step={0.05} value={layer.stagger ?? fx.stagger ?? 0} onChange={e => onPatch({ stagger: Number(e.target.value) })} /><em>{Math.round((layer.stagger ?? fx.stagger ?? 0) * 100)}%</em></span></label>}
    {(layer.unit || fx.unit) !== 'text' && <label className="tme-field">Order<select value={layer.order || fx.order || 'forward'} onChange={e => onPatch({ order: e.target.value })}>
      {Object.entries(ORDER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
    <NumberField label="Delay" value={layer.delay} placeholder="0" min={0} max={60} step={0.05} suffix="s" title={phase === 'out' ? 'Seconds before the end of the window' : 'Seconds after the start'} onChange={v => onPatch({ delay: v })} />
    <label className="tme-field" title="Scales every movement, size and blur of this layer (1 = as designed)">Intensity<span><input type="range" min={0} max={2} step={0.05} value={layer.intensity ?? 1} onChange={e => onPatch({ intensity: Number(e.target.value) })} /><em>{(layer.intensity ?? 1).toFixed(2)}</em></span></label>
    {phase === 'hold' && fx.id !== 'motion-path' && <label className="tme-field">Loop<select value={loop} onChange={e => onPatch({ loop: e.target.value })}>
      {Object.entries(LOOP_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>}
    {isFrame && !effectNeedsBg(fx) && <label className="tme-field check" title={hasBg ? 'Run this layer during the frame\'s colour A → B transition' : 'Needs a second colour (colour B) on this text frame'}>
      <input type="checkbox" disabled={!hasBg} checked={layer.sync === 'bg' || fx.sync === 'bg'} onChange={e => onPatch({ sync: e.target.checked ? 'bg' : undefined })} /> Timed to colour change</label>}
    {wordCount > 1 && fx.id !== 'motion-path' && !(fx.content && ['count', 'countdown'].includes(fx.content.type)) && <label className="tme-field wide" title="Apply this layer to some words only (word-range targeting)">Words
      <span className="tme-range">
        <select value={layer.range ? layer.range[0] : -1} onChange={e => { const a = Number(e.target.value); onPatch({ range: a < 0 ? undefined : [a, Math.max(a, layer.range ? layer.range[1] : a)] }) }}>
          <option value={-1}>All</option>{Array.from({ length: wordCount }, (_, i) => <option key={i} value={i}>{i + 1}</option>)}</select>
        {layer.range && <>–<select value={layer.range[1]} onChange={e => onPatch({ range: [layer.range![0], Math.max(layer.range![0], Number(e.target.value))] })}>
          {Array.from({ length: wordCount }, (_, i) => <option key={i} value={i} disabled={i < layer.range![0]}>{i + 1}</option>)}</select></>}
      </span></label>}
    {(fx.params || []).map(p => {
      const current = layer.params && p.name in layer.params ? layer.params[p.name] : p.default
      if (p.type === 'colour') {
        const token = typeof current === 'string' && !/^#[0-9a-f]{6}$/i.test(current) ? current : null
        return <label key={p.name} className="tme-field" title={p.label}>{p.label}
          <span className="tme-colour">
            <input type="color" value={token ? '#ffffff' : String(current || '#ffffff')} onChange={e => onParam(p.name, e.target.value)} />
            <select value={token || 'custom'} onChange={e => onParam(p.name, e.target.value === 'custom' ? (token ? '#ffffff' : current) : e.target.value)}>
              <option value="custom">Custom</option>
              {COLOUR_TOKENS.filter(([k]) => hasBg || !k.includes('A') && !k.includes('B')).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </span></label>
      }
      if (p.type === 'select') {
        return <label key={p.name} className="tme-field">{p.label}<select value={String(current)} onChange={e => onParam(p.name, e.target.value)}>
          {(p.options || []).map(o => <option key={o} value={o}>{o}</option>)}</select></label>
      }
      if (p.type === 'text') {
        return <label key={p.name} className="tme-field">{p.label}<input value={String(current ?? '')} maxLength={40} onChange={e => onParam(p.name, e.target.value)} /></label>
      }
      return <NumberField key={p.name} label={p.label} value={Number(current)} min={p.min} max={p.max} step={p.step ?? 0.1} suffix={p.unit} onChange={v => onParam(p.name, v ?? p.default)} />
    })}
    {fx.id === 'motion-path' && <div className="tme-note">The path is drawn in the <b>Motion path</b> section{onEditPath ? <> — <button type="button" className="linkish" onClick={onEditPath}>edit it</button></> : ''}.</div>}
    {effectNeedsBg(fx) && <div className="tme-note">{hasBg ? 'Follows the frame\'s colour A → B transition: same shape and timing.' : 'Pick a colour B for this text frame to use this effect.'}</div>}
    {fx.notes && !effectNeedsBg(fx) && fx.id !== 'motion-path' && <div className="tme-note">{fx.notes}</div>}
  </div>
}

// ---------------------------------------------------------------------------
// Mini timeline
// ---------------------------------------------------------------------------
export function TextMotionTimeline({ stack, onChange, clock, duration, start, end, bg, playing, onPlaying, light = false }: {
  stack: TextFxLayer[]
  onChange?: (stack: TextFxLayer[]) => void
  clock: MotionClock
  duration: number
  start: number
  end: number
  bg?: { start: number; time: number; from: string; to: string; transition: string } | null
  playing: boolean
  onPlaying: (p: boolean) => void
  light?: boolean
}) {
  const rulerRef = useRef<HTMLDivElement | null>(null)
  const headRef = useRef<HTMLDivElement | null>(null)
  const timeRef = useRef<HTMLSpanElement | null>(null)
  const D = Math.max(0.2, duration)
  const win = Math.max(0.1, end - start)
  useEffect(() => clock.subscribe(t => {
    if (headRef.current) headRef.current.style.left = `${Math.min(100, t / D * 100)}%`
    if (timeRef.current) timeRef.current.textContent = `${fmt(t)} / ${fmt(D)} s`
  }), [clock, D])
  const seek = (e: React.PointerEvent) => {
    const r = rulerRef.current?.getBoundingClientRect()
    if (!r) return
    const move = (x: number) => clock.seek(Math.max(0, Math.min(D, (x - r.left) / r.width * D)))
    move(e.clientX)
    onPlaying(false)
    const mm = (ev: PointerEvent) => move(ev.clientX)
    const up = () => { window.removeEventListener('pointermove', mm); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', mm)
    window.addEventListener('pointerup', up)
  }
  const drag = (e: React.PointerEvent, layer: TextFxLayer, phase: Phase) => {
    e.preventDefault(); e.stopPropagation()
    const r = rulerRef.current?.getBoundingClientRect()
    if (!r || !onChange) return
    const mm = (ev: PointerEvent) => {
      const t = Math.max(0, Math.min(D, (ev.clientX - r.left) / r.width * D))
      const delay = layer.delay || 0
      const total = phase === 'in' ? t - start - delay : end - delay - t
      const next = Math.max(0.05, Math.round(total * 20) / 20)
      onChange(stack.map(l => (l.id === layer.id ? { ...l, duration: next } : l)))
    }
    const up = () => { window.removeEventListener('pointermove', mm); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', mm)
    window.addEventListener('pointerup', up)
  }
  const ticks: number[] = []
  const step = D > 12 ? 2 : D > 6 ? 1 : 0.5
  for (let s = 0; s <= D + 1e-6; s += step) ticks.push(s)
  const pctOf = (s: number) => `${Math.max(0, Math.min(100, s / D * 100))}%`
  const bar = (layer: TextFxLayer, i: number, phase: Phase) => {
    const fx = EFFECTS[layer.effect]
    if (!fx) return null
    const synced = (layer.sync === 'bg' || effectSyncsToBg(fx)) && bg
    const ws = synced ? bg!.start : start
    const we = synced ? bg!.start + bg!.time : end
    const sp = Math.min(we - ws, layerSpan(layer, fx, we - ws))
    const delay = layer.delay || 0
    let a: number, b: number
    if (phase === 'in') { a = ws + delay; b = ws + delay + (synced && layer.duration == null ? we - ws : sp - delay) }
    else if (phase === 'out') { b = we - delay; a = b - (synced && layer.duration == null ? we - ws : sp - delay) }
    else { a = ws + delay; b = Math.min(we, ws + sp) }
    return <div key={layer.id} className={`tl-bar ${phase}${layer.muted ? ' muted' : ''}${i > 0 ? ' stacked' : ''}`} title={`${fx.label}: ${fmt(a)} → ${fmt(b)} s${synced ? ' · timed to the colour change' : ''}`}
      style={{ left: pctOf(a), width: `${Math.max(0.8, (b - a) / D * 100)}%`, top: i > 0 ? 3 + Math.min(i, 3) * 5 : undefined }}>
      <span>{fx.symbol} {fx.label}</span>
      {phase !== 'hold' && onChange && <i className={`handle ${phase === 'in' ? 'r' : 'l'}`} title="Drag to change the duration" onPointerDown={e => drag(e, layer, phase)} />}
    </div>
  }
  return <div className={`tme-timeline${light ? ' light' : ''}`}>
    <div className="tl-transport">
      <button type="button" className="icon-button" onClick={() => onPlaying(!playing)} title={playing ? 'Pause' : 'Play'}>{playing ? <Pause size={13} /> : <Play size={13} />}</button>
      <span ref={timeRef} className="tl-time">0.00 / {fmt(D)} s</span>
      <small>Drag a bar's inner edge to change its duration · click the ruler to scrub</small>
    </div>
    <div className="tl-grid">
      <div className="tl-labels">
        <span />
        {bg && <label>BACKGROUND</label>}
        <label>ENTER</label><label>WHILE SHOWN</label><label>EXIT</label>
      </div>
      <div className="tl-tracks">
        <div ref={rulerRef} className="tl-ruler" onPointerDown={seek}>
          {ticks.map(s => <span key={s} style={{ left: pctOf(s) }}>{s % 1 === 0 ? `${s}s` : '·'}</span>)}
          <i className="tl-window" style={{ left: pctOf(start), width: `${win / D * 100}%` }} title="Text window" />
        </div>
        {bg && <div className="tl-lane bg" title={`Colour A → B · ${bg.transition} · starts ${fmt(bg.start)} s · ${fmt(bg.time)} s`}>
          <i style={{ left: 0, width: pctOf(bg.start), background: bg.from }} />
          <i style={{ left: pctOf(bg.start), width: `${bg.time / D * 100}%`, background: `linear-gradient(90deg, ${bg.from}, ${bg.to})` }} className="mix"><span>{bg.transition}</span></i>
          <i style={{ left: pctOf(bg.start + bg.time), right: 0, background: bg.to }} />
        </div>}
        {(['in', 'hold', 'out'] as Phase[]).map(ph => <div key={ph} className="tl-lane">{layersOf(stack, ph).map((l, i) => bar(l, i, ph))}</div>)}
        <div ref={headRef} className="tl-playhead" />
      </div>
    </div>
  </div>
}

/** Compact read-only summary of a stack (storyline popover, default style). */
export function StackSummary({ stack }: { stack: TextFxLayer[] }) {
  if (!stack.length) return <span className="stack-summary empty">No animation</span>
  return <span className="stack-summary">{stack.filter(l => EFFECTS[l.effect]).map(l => <em key={l.id} className={`${EFFECTS[l.effect].phase}${l.muted ? ' muted' : ''}`} title={`${PHASE_LABEL[EFFECTS[l.effect].phase as Phase]}: ${EFFECTS[l.effect].label}`}>{EFFECTS[l.effect].symbol} {EFFECTS[l.effect].label}</em>)}</span>
}

export function presetForRandom(isFrame: boolean, hasBg: boolean, list: MotionPreset[]): MotionPreset | null {
  const pool = list.filter(p => (!p.frame || isFrame) && (!p.layers.some(l => effectNeedsBg(EFFECTS[l.effect])) || hasBg || p.frame))
  return pool.length ? pool[Math.floor(Math.random() * pool.length)] : null
}

export { Sparkles, X }
