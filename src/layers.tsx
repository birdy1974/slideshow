// Popup stacking: whichever popup opened last sits on top.
//
// The app used to rely on a hand-kept ladder of z-index classes (50 / 60
// "stacked" / 70 "over-editor") plus DOM order to decide which popup wins.
// That breaks as soon as a popup is opened from another popup that happens
// to sit on the same rung or later in the tree — the transition gallery
// opened from the text frame editor, the look editor opened from a stacked
// collage editor, a confirm opened over a project loader, and so on.
//
// Instead every popup root calls useLayer() and puts the returned z-index on
// its outermost fixed element (backdrop or popover). Layers are handed out in
// mount order, always one step above the highest layer that is still open,
// so a popup can never end up below the one it was opened from, no matter
// where it is mounted or which CSS class it carries. Nested popups rendered
// inside another backdrop (font picker, effect browser, the media browser's
// own lightbox) get a layer too: it only has to beat their parent's content
// inside that parent's stacking context, which it does.
//
// The class-based z-indexes in styles.css stay as fallbacks. Toasts keep a
// fixed, much higher z-index (they are never a layer).
//
// The same registry decides who owns the Escape key: only the top-most open
// layer closes on Escape (useEscapeToClose), so pressing Escape in a gallery
// no longer also closes the editor underneath it.
import { useEffect, useLayoutEffect, useState, type CSSProperties, type ReactNode } from 'react'

export const LAYER_BASE = 50
export const LAYER_STEP = 10

const open: number[] = []

const highest = () => (open.length ? Math.max(...open) : LAYER_BASE - LAYER_STEP)
const next = () => highest() + LAYER_STEP

/** True when `z` is the top-most open layer (or no layer is registered at all). */
export function isTopLayer(z: number): boolean {
  return open.length === 0 || highest() === z
}

/** Number of popup layers currently open — handy for tests and debugging. */
export function openLayerCount(): number {
  return open.length
}

/**
 * Register the calling popup as the newest layer and return the z-index it
 * must render with. Pass `active=false` while the popup is closed (for
 * components that render their popover conditionally, like the pickers).
 * Changing `bump` re-registers the layer on top again — the upload tray uses
 * it to surface when a new batch of uploads starts.
 */
export function useLayer(active = true, bump?: unknown): number {
  const [z, setZ] = useState<number>(next)
  useLayoutEffect(() => {
    if (!active) return
    const mine = next()
    open.push(mine)
    setZ(mine)
    return () => {
      const at = open.indexOf(mine)
      if (at >= 0) open.splice(at, 1)
    }
  }, [active, bump])
  return z
}

/**
 * Close on Escape, but only while this layer is the top-most one. Handlers
 * run in the same keydown dispatch as the layers above, and the registry only
 * changes when React commits, so a parent never reacts to the Escape that
 * closed its child. Pass `enabled=false` to switch the key off temporarily.
 */
export function useEscapeToClose(z: number, onEscape: (() => void) | undefined, enabled = true) {
  useEffect(() => {
    if (!enabled || !onEscape) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !isTopLayer(z)) return
      onEscape()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [z, onEscape, enabled])
}

/**
 * A `.modal-backdrop` that is also a popup layer: newest on top, Escape and a
 * mousedown on the backdrop itself call `onDismiss`. The dismissing mousedown
 * stops there, so a dialog nested inside another backdrop (the media
 * browser's delete confirm, the project loader's confirms) closes without
 * taking its parent down with it.
 */
export function PopupBackdrop({ className, style, onDismiss, children, role, ariaLabel }: {
  className?: string
  style?: CSSProperties
  onDismiss?: () => void
  children: ReactNode
  role?: string
  ariaLabel?: string
}) {
  const layer = useLayer()
  useEscapeToClose(layer, onDismiss)
  return <div
    className={className ? `modal-backdrop ${className}` : 'modal-backdrop'}
    style={{ ...style, zIndex: layer }}
    role={role}
    aria-label={ariaLabel}
    onMouseDown={onDismiss ? event => { event.stopPropagation(); onDismiss() } : undefined}
  >{children}</div>
}
