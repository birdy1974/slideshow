// Small presentational helpers shared by App.tsx and the transition pickers.
import { useEffect, useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { formatClockPrecise, parseClock } from './time'

/** While a popup is open, keep the page behind it from scrolling. Wheel and
 *  touch still work inside the popup's own scrollable panes. */
export function useModalScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return
    const html = document.documentElement
    const body = document.body
    const prevHtml = html.style.overflow
    const prevBody = body.style.overflow
    const prevPad = body.style.paddingRight
    const sb = window.innerWidth - html.clientWidth
    html.style.overflow = 'hidden'
    body.style.overflow = 'hidden'
    if (sb > 0) body.style.paddingRight = `${sb}px`
    html.classList.add('modal-open')
    const block = (event: Event) => {
      const target = event.target as HTMLElement | null
      if (!target) return
      const pane = target.closest(
        'aside, .browser-grid-wrap, .gallery-grid, .file-area, .file-grid, .picker-body, .kb-panel, .tme-lanes, .collage-timing, textarea, .lightbox-stage, .transition-gallery, .text-effect-gallery, .browser-modal, .soundtrack-editor, .movie-editor, .look-editor, .frame-editor, .confirm-modal, .text-style-modal',
      ) as HTMLElement | null
      if (pane) {
        const canScroll = pane.scrollHeight > pane.clientHeight + 1 || pane.scrollWidth > pane.clientWidth + 1
        if (canScroll) return
      }
      event.preventDefault()
    }
    document.addEventListener('wheel', block, { passive: false })
    document.addEventListener('touchmove', block, { passive: false })
    return () => {
      html.style.overflow = prevHtml
      body.style.overflow = prevBody
      body.style.paddingRight = prevPad
      html.classList.remove('modal-open')
      document.removeEventListener('wheel', block)
      document.removeEventListener('touchmove', block)
    }
  }, [active])
}

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
