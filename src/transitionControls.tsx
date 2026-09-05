// Reusable transition-related form controls: the easing picker, the
// per-transition GL parameter editors and the "random source" selector.
// Used by App.tsx, the transition browser and the preview modal.
import type { ReactNode } from 'react'
import { Check } from 'lucide-react'
import { Select } from './ui'
import {
  EASING_DEFAULT, easingGroups, getGLParams, glTransitions, nativeTransitions, transitions,
} from './transitionCatalog'

export function EasingSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const v = value && value.trim() ? value : EASING_DEFAULT
  return <Select value={v} onChange={onChange}>{Object.entries(easingGroups).map(([g, opts]) => <optgroup label={g} key={g}>{opts.map(o => <option key={o} value={o}>{o}</option>)}</optgroup>)}</Select>
}

// Which catalogue a "Random" action is allowed to draw from.
// 'xfade' = native FFmpeg xfade only, 'gl' = ported GL transitions only,
// 'both' = the whole combined catalogue.
export type RandomScope = 'xfade' | 'gl' | 'both'
export const randomScopeLabels: Record<RandomScope, string> = {
  xfade: `Random xfade (${nativeTransitions.length})`,
  gl: `Random GL (${glTransitions.length})`,
  both: `Random both (${transitions.length})`,
}
export function randomPoolFor(scope: RandomScope): string[] {
  return scope === 'xfade' ? nativeTransitions : scope === 'gl' ? glTransitions : transitions
}
export function pickRandomTransition(scope: RandomScope): string {
  const pool = randomPoolFor(scope)
  return pool[Math.floor(Math.random() * pool.length)]
}
export function RandomScopeSelect({ value, onChange }: { value: RandomScope; onChange: (v: RandomScope) => void }) {
  return <Select ariaLabel="Random transition source" value={value} onChange={v => onChange(v as RandomScope)}>
    {(Object.keys(randomScopeLabels) as RandomScope[]).map(k => <option key={k} value={k}>{randomScopeLabels[k]}</option>)}
  </Select>
}

export function GLParamControls({ transition, params, onChange }: { transition: string; params: Record<string, string | number>; onChange: (next: Record<string, string | number>) => void }) {
  const defs = getGLParams(transition)
  if (!defs.length) return <small className="gl-no-params">No extra parameters — uses defaults.</small>
  return <div className="gl-params">
    {defs.map(def => {
      const raw = params[def.name]
      const value = raw !== undefined ? String(raw) : def.default
      const isColor = /^0x/i.test(def.default) || /color/i.test(def.name)
      // numeric slider range heuristic: 0..max based on default
      const numDefault = Number(def.default)
      const isNumeric = Number.isFinite(numDefault) && !isColor
      // registry entries may carry explicit slider limits; otherwise derive from the default
      const min = isNumeric ? (def.min !== undefined ? Number(def.min) : Math.min(0, numDefault)) : 0
      const max = isNumeric ? (def.max !== undefined ? Number(def.max)
        : (numDefault <= 1 ? 1 : numDefault < 5 ? 5 : numDefault < 20 ? 20 : numDefault <= 100 ? 120 : 360)) : 10
      const step = isNumeric ? (def.step !== undefined ? Number(def.step) : (max - min <= 1 ? 0.01 : max - min <= 20 ? 0.1 : 1)) : 0.1
      return <label key={def.name} className="gl-param">
        <span title={def.hint || def.name}>{def.name}<em>{value}</em></span>
        {isColor ? <div className="color-control compact"><input type="color" value={String(value).startsWith('#') ? String(value) : '#30382a'} onChange={e => { const next = { ...params, [def.name]: e.target.value }; onChange(next) }} /><input type="text" value={String(value)} onChange={e => { const next = { ...params, [def.name]: e.target.value }; onChange(next) }} placeholder={def.default} /></div>
          : isNumeric ? <div className="gl-slider"><input type="range" min={min} max={max} step={step} value={Number(value) || 0} onChange={e => { const next = { ...params, [def.name]: e.target.value }; onChange(next) }} /><input type="text" value={String(value)} onChange={e => { const next = { ...params, [def.name]: e.target.value }; onChange(next) }} placeholder={def.default} /></div>
            : <input type="text" value={String(value)} onChange={e => { const next = { ...params, [def.name]: e.target.value }; onChange(next) }} placeholder={def.default} />}
      </label>
    })}
  </div>
}

/** Compact read-only summary of the non-default bits of a transition config. */
export function TransitionMeta({ children }: { children: ReactNode }) {
  return <div className="transition-meta">{children}</div>
}

export function ReverseToggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return <label className="check-label"><input type="checkbox" checked={value} onChange={e => onChange(e.target.checked)} /><span><Check size={11} /></span> Reverse</label>
}
