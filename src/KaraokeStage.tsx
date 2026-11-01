// The karaoke caption in the live preview — the DOM side of the twin whose
// render side is backend/app/karaoke.py.
//
// Both read the same derived plan (src/karaokeCore.ts), so what you time here
// is what the MP4 does: the same sentence windows, the same per-word colour
// hand-off, the same lead-in fade. The words themselves are laid out by the
// browser and by libass respectively — exactly the situation the caption
// preview has always had (docs/text-motion.md), where the *timing* is pinned by
// twin tests and the wrapping is left to the renderer.
import { useEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'
import { FRAME_H, FRAME_W, ensureFont, fontCssFor } from './textMotionScene'
import { karaokeAt, planOf, tokenize, wordStates, type KaraokeData } from './karaokeCore'

export interface KaraokeStageProps {
  text: string
  karaoke: KaraokeData
  /** The caption's own style, so karaoke is the same text, only timed. */
  family: string
  bold: boolean
  italic: boolean
  underline?: boolean
  colour: string
  fontSize: number
  /** Block position, % of the frame — the caption's own coordinates. */
  x: number
  y: number
  align?: 'center' | 'left'
  outline?: boolean
  /** Seconds on the clip's own clock (0 = the kept cut's IN point). */
  at: number
  /** Window the karaoke may use: the slide's `textStart`/`textEnd`. */
  from?: number
  to?: number
  className?: string
  style?: React.CSSProperties
}

/** The slide clock, as React state.
 *
 *  `MotionStage` mutates DOM nodes per frame instead of re-rendering, which is
 *  right for a hundred animated letters. A karaoke caption is one block of word
 *  spans, and the sweep is quantised to the render's frame rate anyway, so the
 *  preview updates on a 25 Hz tick — same visible result, a quarter of the work.
 */
export function useKaraokeClockTime(clock: { subscribe: (fn: (t: number) => void) => () => void } | null, fps = 25): number {
  const [t, setT] = useState(0)
  const lastRef = useRef(-1)
  useEffect(() => {
    if (!clock) return
    const step = 1 / Math.max(5, fps)
    return clock.subscribe(next => {
      if (Math.abs(next - lastRef.current) < step && lastRef.current >= 0) return
      lastRef.current = next
      setT(next)
    })
  }, [clock, fps])
  return t
}

/** Scale a 1920-wide root to the host's width, the way `MotionStage` does, so
 *  a 48 px caption means 48 px in the frame and not on the screen. */
function useStageScale(host: React.RefObject<HTMLDivElement | null>, root: React.RefObject<HTMLDivElement | null>) {
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
    return () => {
      ro?.disconnect()
    }
  }, [host, root])
}

export function KaraokeStage(props: KaraokeStageProps) {
  const host = useRef<HTMLDivElement | null>(null)
  const root = useRef<HTMLDivElement | null>(null)
  useStageScale(host, root)
  const { text, karaoke } = props
  const plan = useMemo(() => planOf(text, karaoke, { from: props.from ?? 0, to: props.to ?? 0 }), [text, karaoke, props.from, props.to])
  const tokens = useMemo(() => tokenize(text), [text])
  const styleKind = karaoke.style
  const frame = karaokeAt(plan, props.at, styleKind)
  const states = wordStates(plan, frame, styleKind)
  const sentence = frame.s >= 0 ? plan.sentences[frame.s] : null
  // A layout measured with the fallback font is off by a few pixels, so the
  // block fades in once the real face is in.
  const [ready, setReady] = useState(true)
  useEffect(() => {
    let alive = true
    setReady(false)
    void ensureFont(props.family, props.bold, props.italic).then(() => {
      if (alive) setReady(true)
    }).catch(() => {
      if (alive) setReady(true)
    })
    return () => {
      alive = false
    }
  }, [props.family, props.bold, props.italic])

  const px = Math.max(4, (Number(props.fontSize) || 48) * FRAME_W / 1920)
  const fill = karaoke.fill
  const blockStyle: React.CSSProperties = {
    font: fontCssFor(props.family, props.bold, props.italic, px),
    color: props.colour,
    left: `${props.align === 'left' ? props.x : 50}%`,
    top: `${props.y}%`,
    transform: props.align === 'left' ? 'translate(0, -50%)' : 'translate(-50%, -50%)',
    maxWidth: '86%',
    textAlign: props.align === 'left' ? 'left' : 'center',
    lineHeight: 1.28,
    whiteSpace: 'pre-wrap',
    textShadow: props.outline !== false ? '0 2px 8px #0009, 0 0 2px #0007' : 'none',
    opacity: sentence ? frame.fade : 0,
    visibility: ready ? 'visible' : 'hidden',
  }
  // `pop` shows one word at a time — the Reels caption look — so the rest of
  // the sentence is not laid out at all.
  const shown = sentence
    ? styleKind === 'pop'
      ? sentence.words.slice(Math.max(0, frame.k), Math.max(0, frame.k) + 1)
      : sentence.words
    : []

  return (
    <div ref={host} className={`kara-stage-host${props.className ? ` ${props.className}` : ''}`} style={props.style}>
      <div ref={root} className="kara-stage" style={{ width: FRAME_W, height: FRAME_H }}>
        {sentence ? (
          <div className={`kara-block${styleKind === 'pop' ? ' pop' : ''}`} style={blockStyle}>
            {shown.map(w => {
              const token = tokens[w.i]
              if (!token) return null
              const index = sentence.words.indexOf(w)
              const state = states[index] || 'next'
              const sweeping = styleKind === 'sweep' && state === 'now'
              const colour = state === 'next' ? props.colour : fill
              return (
                <span
                  key={w.i}
                  className={`kara-word kara-${state}${props.underline ? ' u' : ''}`}
                  style={sweeping
                    ? { backgroundImage: `linear-gradient(90deg, ${fill} ${(frame.u * 100).toFixed(1)}%, ${props.colour} ${(frame.u * 100).toFixed(1)}%)`, WebkitTextFillColor: 'transparent', backgroundClip: 'text' }
                    : { color: colour }}
                >
                  {token.w}
                </span>
              )
            })}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/** What the timing lane is doing, in the words the legend uses. */
export function karaokeStyleLabel(karaoke: KaraokeData | undefined): string {
  switch (karaoke?.style) {
    case 'word': return 'the sung word flips colour'
    case 'reveal': return 'words appear as they are sung'
    case 'pop': return 'one word at a time'
    default: return 'words fill in as they are sung'
  }
}
