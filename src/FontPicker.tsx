// Font picker: every bundled family drawn in its own face, grouped like the
// catalogue (Sans, Serif, Display, Handwriting, Script, Typewriter), with a
// search box — a plain <select> cannot show what a handwriting font looks
// like. The files are the ones the MP4 is rendered with (registry/fonts.json).
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Search, X } from 'lucide-react'
import { FONT_GROUPS, GROUP_HINTS, FONTS_WITHOUT_BOLD, FONTS_WITHOUT_ITALIC, fontEntry, fontStack } from './fonts'

export function FontPicker({ value, onChange, sample, dark = false }: { value: string; onChange: (family: string) => void; sample?: string; dark?: boolean }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState<string | null>(null)
  const chipRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState({ left: 0, top: 0, width: 520, height: 440 })
  const text = (sample || '').split('\n')[0].trim().slice(0, 40) || 'Summer, slowly'

  useLayoutEffect(() => {
    if (!open || !chipRef.current) return
    const r = chipRef.current.getBoundingClientRect()
    const vw = window.innerWidth, vh = window.innerHeight
    const width = Math.min(560, vw - 16)
    const height = Math.min(460, vh - 16)
    const left = Math.max(8, Math.min(vw - width - 8, r.left))
    const top = r.bottom + 6 + height <= vh - 8 ? r.bottom + 6 : Math.max(8, r.top - height - 6)
    setPos({ left, top, width, height })
  }, [open])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    const onDown = (e: MouseEvent) => { if (popRef.current && !popRef.current.contains(e.target as Node) && !chipRef.current?.contains(e.target as Node)) setOpen(false) }
    window.addEventListener('keydown', onKey, true)
    document.addEventListener('mousedown', onDown)
    return () => { window.removeEventListener('keydown', onKey, true); document.removeEventListener('mousedown', onDown) }
  }, [open])

  const needle = query.trim().toLowerCase()
  const groups = useMemo(() => Object.entries(FONT_GROUPS)
    .map(([name, families]) => ({ name, families: families.filter(f => !needle || `${f} ${name} ${(fontEntry(f)?.tags || []).join(' ')}`.toLowerCase().includes(needle)) }))
    .filter(g => g.families.length && (!group || g.name === group)), [needle, group])
  const entry = fontEntry(value)

  return <>
    <button ref={chipRef} type="button" className={`font-chip${open ? ' open' : ''}${dark ? ' dark' : ''}`} onClick={() => setOpen(o => !o)} title={`${value}${entry ? ` · ${entry.group}` : ''} — click to browse all fonts`}>
      <span className="font-chip-name" style={{ fontFamily: fontStack(value) }}>{value}</span>
      {entry && <i>{entry.group}</i>}
      <ChevronDown size={13} />
    </button>
    {open && <div ref={popRef} className="font-picker" style={{ left: pos.left, top: pos.top, width: pos.width, height: pos.height }}>
      <header>
        <label className="browser-search"><Search size={13} /><input autoFocus value={query} placeholder="Search fonts (e.g. hand, script, typewriter)…" onChange={e => setQuery(e.target.value)} />{query && <button type="button" onClick={() => setQuery('')}><X size={12} /></button>}</label>
        <button type="button" className="browser-close" onClick={() => setOpen(false)} aria-label="Close"><X size={14} /></button>
      </header>
      <div className="browser-tabs">
        <button type="button" className={group === null ? 'active' : ''} onClick={() => setGroup(null)}>All<b>{Object.values(FONT_GROUPS).flat().length}</b></button>
        {Object.entries(FONT_GROUPS).map(([name, families]) => <button type="button" key={name} className={group === name ? 'active' : ''} onClick={() => setGroup(group === name ? null : name)}>{name}<b>{families.length}</b></button>)}
      </div>
      <div className="font-list">
        {groups.map(g => <section key={g.name}>
          <strong>{g.name}<small>{GROUP_HINTS[g.name] || ''}</small></strong>
          {g.families.map(f => <button type="button" key={f} className={f === value ? 'active' : ''} onClick={() => { onChange(f); setOpen(false) }}>
            <span className="font-sample" style={{ fontFamily: fontStack(f) }}>{text}</span>
            <span className="font-meta">{f}{FONTS_WITHOUT_BOLD.has(f) ? ' · one weight' : ''}{FONTS_WITHOUT_ITALIC.has(f) && !FONTS_WITHOUT_BOLD.has(f) ? ' · no italic' : ''}</span>
          </button>)}
        </section>)}
        {!groups.length && <p className="browser-empty">No font matches “{query}”.</p>}
      </div>
    </div>}
  </>
}
