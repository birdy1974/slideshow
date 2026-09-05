// Small presentational helpers shared by App.tsx and the transition pickers.
import { useEffect, useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { formatClockPrecise, parseClock } from './time'

export function Select({ value, onChange, children, ariaLabel }: { value: string, onChange?: (v: string) => void, children: ReactNode, ariaLabel?: string }) {
  return <div className="select-wrap"><select aria-label={ariaLabel} value={value} onChange={e => onChange?.(e.target.value)}>{children}</select><ChevronDown size={14} /></div>
}

export function FieldLabel({ children, hint }: { children: ReactNode, hint?: string }) {
  return <label className="field-label">{children}{hint && <span>{hint}</span>}</label>
}

export function TimeField({ label, value, onCommit, min, max }: { label: string; value: number; onCommit: (v: number) => void; min: number; max: number }) {
  const [text, setText] = useState(formatClockPrecise(value))
  useEffect(() => setText(formatClockPrecise(value)), [value])
  const commit = () => { const v = parseClock(text); if (Number.isFinite(v) && text.trim()) onCommit(Math.min(max, Math.max(min, v))); else setText(formatClockPrecise(value)) }
  return <label className="time-field"><span>{label}</span><input value={text} onChange={e => setText(e.target.value)} onBlur={commit} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() } }} /></label>
}
