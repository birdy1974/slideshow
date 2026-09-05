// Small presentational helpers shared by App.tsx and the transition pickers.
import type { ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'

export function Select({ value, onChange, children, ariaLabel }: { value: string, onChange?: (v: string) => void, children: ReactNode, ariaLabel?: string }) {
  return <div className="select-wrap"><select aria-label={ariaLabel} value={value} onChange={e => onChange?.(e.target.value)}>{children}</select><ChevronDown size={14} /></div>
}

export function FieldLabel({ children, hint }: { children: ReactNode, hint?: string }) {
  return <label className="field-label">{children}{hint && <span>{hint}</span>}</label>
}
