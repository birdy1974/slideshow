// Reusable transition-related form controls: the easing picker, the
// per-transition GL parameter editors and the "random source" selector.
// Used by App.tsx, the transition browser and the preview modal.
import { Select } from './ui'
import {
  EASING_DEFAULT, easingGroups, getGLParams, glTransitions, isGLTransition, nativeTransitions, transitions,
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

// A fresh random parameter set for one GL transition, using exactly the
// bounds the editor's parameter sliders show (GLParamControls: explicit
// registry min/max/step when present, otherwise the same derived ranges).
// Numeric params draw uniformly; colour-like params (including packed colour
// parameters such as Colour Phase's fromStep/toStep) become random hex; any
// genuinely textual parameter keeps its registry default.
export function randomGLParams(label: string): Record<string, string> {
  const next: Record<string, string> = {}
  for (const def of getGLParams(label)) {
    const defaultValue = String(def.default ?? '').trim()
    const isColor = /^(?:#|0x)[0-9a-f]{6,8}$/i.test(defaultValue) || /color/i.test(def.name)
    const numDefault = Number(defaultValue)
    const isNumeric = Number.isFinite(numDefault) && !isColor
    if (isNumeric) {
      const min = def.min !== undefined ? Number(def.min) : Math.min(0, numDefault)
      const max = def.max !== undefined ? Number(def.max)
        : (numDefault <= 1 ? 1 : numDefault < 5 ? 5 : numDefault < 20 ? 20 : numDefault <= 100 ? 120 : 360)
      const step = def.step !== undefined ? Number(def.step) : (max - min <= 1 ? 0.01 : max - min <= 20 ? 0.1 : 1)
      const steps = Math.max(1, Math.round((max - min) / step))
      const value = min + step * Math.floor(Math.random() * (steps + 1))
      const decimals = (String(def.step !== undefined ? def.step : step).split('.')[1] || '').length
      next[def.name] = value.toFixed(Math.min(4, Math.max(0, decimals)))
    } else if (isColor) {
      const hex = () => Math.floor(Math.random() * 256).toString(16).padStart(2, '0')
      next[def.name] = `#${hex()}${hex()}${hex()}`
    }
  }
  // Colour Phase interprets these two packed colours as per-channel lower and
  // upper steps. Keep that relationship valid while still randomizing both
  // values; otherwise a random draw could make the custom transition reject
  // the settings.
  const from = next.fromStep?.match(/^#([0-9a-f]{6})$/i)
  const to = next.toStep?.match(/^#([0-9a-f]{6})$/i)
  if (from && to) {
    const lower = [0, 1, 2].map(() => Math.floor(Math.random() * 255))
    const upper = lower.map(value => value + 1 + Math.floor(Math.random() * (255 - value)))
    const packed = (values: number[]) => `#${values.map(value => value.toString(16).padStart(2, '0')).join('')}`
    next.fromStep = packed(lower)
    next.toStep = packed(upper)
  }
  return next
}

// Generic transition settings are separate from the transition's duration:
// randomization may change the easing, reverse flag, and any GL-specific
// values, but never touches transitionTime. This is used by both random
// transition actions and the explicit parameter-only action.
export function randomTransitionSettings(label: string): {
  transitionEasing: string;
  transitionReverse: number;
  transitionParams: Record<string, string> | undefined;
} {
  const easings = Object.values(easingGroups).flat()
  return {
    transitionEasing: easings[Math.floor(Math.random() * easings.length)] || EASING_DEFAULT,
    transitionReverse: Math.random() < 0.5 ? 0 : 1,
    transitionParams: isGLTransition(label) ? randomGLParams(label) : undefined,
  }
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
      const isColor = /^(?:#|0x)[0-9a-f]{6,8}$/i.test(String(def.default).trim()) || /color/i.test(def.name)
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
