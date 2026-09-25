// React wrappers around the live text-motion scene (src/textMotionScene.ts).
//
//   <MotionStage>  the big preview: text frame background (colour A, colour B
//                  arriving through the frame transition with FFmpeg's exact
//                  geometry) and the animated caption on one shared clock
//   <MotionTile>   browser tiles: built lazily, one still frame, animated on
//                  hover (or with "Autoplay tiles")
//   useMotionClock a play/pause/seek clock shared with the mini timeline
import { useEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'
import type { BgChange } from './textMotionCore'
import { FRAME_H, FRAME_W, MotionClock, MotionScene, ensureFont, fontLoaded, paintBackground, prepareScene, type Prepared, type SceneInput } from './textMotionScene'

export function useMotionClock(duration: number, playing = true): MotionClock {
  const clock = useMemo(() => new MotionClock(duration), [])
  useEffect(() => { clock.setDuration(duration) }, [clock, duration])
  useEffect(() => { clock.setPlaying(playing) }, [clock, playing])
  return clock
}

/** Rebuild when the fonts arrive: layouts measured with a fallback font are off. */
function useFontTick(input: SceneInput | null): number {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!input) return
    let alive = true
    if (!fontLoaded(input.family, input.bold, input.italic)) {
      ensureFont(input.family, input.bold, input.italic).then(() => { if (alive) setTick(t => t + 1) })
    }
    return () => { alive = false }
  }, [input?.family, input?.bold, input?.italic])
  return tick
}

function useSceneScale(host: React.RefObject<HTMLDivElement | null>, root: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const el = host.current
    if (!el) return
    const apply = () => {
      const w = el.clientWidth || FRAME_W
      if (root.current) root.current.style.transform = `scale(${w / FRAME_W})`
    }
    apply()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(apply) : null
    ro?.observe(el)
    return () => ro?.disconnect()
  }, [host, root])
}

export interface MotionStageProps {
  input: SceneInput | null
  clock?: MotionClock
  at?: number
  /** Text frame colour A (omit for pictures: the caller draws the picture). */
  background?: string
  /** Text frame colour change (seconds on the caption clock). */
  bg?: BgChange | null
  className?: string
  style?: React.CSSProperties
  children?: React.ReactNode
  onPrepared?: (prep: Prepared) => void
  flag?: string | null
}

export function MotionStage({ input, clock, at = 0, background, bg = null, className = '', style, children, onPrepared, flag }: MotionStageProps) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const layerRef = useRef<HTMLDivElement | null>(null)
  const baseRef = useRef<HTMLDivElement | null>(null)
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<MotionScene | null>(null)
  const fontTick = useFontTick(input)
  const key = useMemo(() => (input ? JSON.stringify(input) : ''), [input])
  const bgKey = JSON.stringify([background, bg])
  const onPreparedRef = useRef(onPrepared)
  onPreparedRef.current = onPrepared
  useSceneScale(hostRef, layerRef)

  const paint = (t: number) => {
    if (background && baseRef.current && overlayRef.current) paintBackground(baseRef.current, overlayRef.current, background, bg, t)
    sceneRef.current?.render(t)
  }
  const paintRef = useRef(paint)
  paintRef.current = paint

  useEffect(() => {
    if (!input || !layerRef.current) return
    let scene: MotionScene | null = null
    try {
      const prep = prepareScene(input)
      scene = new MotionScene(layerRef.current, prep)
      sceneRef.current = scene
      onPreparedRef.current?.(prep)
    } catch (err) {
      console.error('text motion preview failed', err)
    }
    paintRef.current(clock ? clock.now() : at)
    return () => { scene?.destroy(); if (sceneRef.current === scene) sceneRef.current = null }
  }, [key, fontTick])

  useEffect(() => {
    if (!clock) return
    return clock.subscribe(t => paintRef.current(t))
  }, [clock])

  useEffect(() => {
    if (!clock) paintRef.current(at)
  }, [at, clock, key, fontTick, bgKey])

  return <div ref={hostRef} className={`motion-stage ${className}`} style={style}>
    {background && <><div ref={baseRef} className="motion-bg" style={{ background }} /><div ref={overlayRef} className="motion-bg motion-bg-b" /></>}
    <div ref={layerRef} className="motion-layer" style={{ width: FRAME_W, height: FRAME_H }} />
    {flag && <span className="motion-flag">{flag}</span>}
    {children}
  </div>
}

const tileObserver: { io: IntersectionObserver | null; cbs: Map<Element, () => void> } = { io: null, cbs: new Map() }
function observeOnce(el: Element, cb: () => void) {
  if (typeof IntersectionObserver === 'undefined') { cb(); return () => undefined }
  if (!tileObserver.io) {
    tileObserver.io = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const fn = tileObserver.cbs.get(entry.target)
        tileObserver.cbs.delete(entry.target)
        tileObserver.io!.unobserve(entry.target)
        fn?.()
      }
    }, { rootMargin: '120px' })
  }
  tileObserver.cbs.set(el, cb)
  tileObserver.io.observe(el)
  return () => { tileObserver.cbs.delete(el); tileObserver.io?.unobserve(el) }
}

/** One browser tile: a still frame of the stack, animated while `live`. */
export function MotionTile({ input, duration, live, background = '#20231f', bg = null }: {
  input: SceneInput
  duration: number
  live: boolean
  background?: string
  bg?: BgChange | null
}) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const layerRef = useRef<HTMLDivElement | null>(null)
  const baseRef = useRef<HTMLDivElement | null>(null)
  const overlayRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<MotionScene | null>(null)
  const [visible, setVisible] = useState(false)
  const fontTick = useFontTick(visible ? input : null)
  const key = useMemo(() => JSON.stringify(input), [input])
  useSceneScale(hostRef, layerRef)
  const still = duration * 0.55

  useEffect(() => {
    const el = hostRef.current
    if (!el) return
    return observeOnce(el, () => setVisible(true))
  }, [])

  const paint = (t: number) => {
    if (baseRef.current && overlayRef.current) paintBackground(baseRef.current, overlayRef.current, background, bg, t)
    sceneRef.current?.render(t)
  }

  useEffect(() => {
    if (!visible || !layerRef.current) return
    let scene: MotionScene | null = null
    try {
      scene = new MotionScene(layerRef.current, prepareScene(input))
      sceneRef.current = scene
      paint(still)
    } catch (err) {
      console.error('text motion tile failed', err)
    }
    return () => { scene?.destroy(); sceneRef.current = null }
  }, [visible, key, fontTick])

  useEffect(() => {
    if (!live || !visible) { paint(still); return }
    let raf = 0
    const t0 = performance.now()
    const loop = (now: number) => {
      paint(((now - t0) / 1000) % (duration + 0.5))
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [live, visible, key, fontTick, duration])

  return <span ref={hostRef} className="motion-stage motion-tile-stage">
    <span ref={baseRef} className="motion-bg" style={{ background }} />
    <span ref={overlayRef} className="motion-bg motion-bg-b" />
    <span ref={layerRef as any} className="motion-layer" style={{ width: FRAME_W, height: FRAME_H }} />
  </span>
}
