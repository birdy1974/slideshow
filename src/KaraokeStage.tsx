// The karaoke caption in the live preview — the DOM side of the twin whose
// render side is backend/app/karaoke.py.
//
// Both read the same derived plan (src/karaokeCore.ts), so what you time here
// is what the MP4 does: the same sentence windows, the same per-word colour
// hand-off, the same lead-in fade. The words themselves are laid out by the
// browser and by libass respectively — exactly the situation the caption
// preview has always had (docs/text-motion.md), where the *timing* is pinned by
// twin tests and the wrapping is left to the renderer.
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import type React from 'react'
import { effectiveFontStyle, fontMetrics } from './fonts'
import { FRAME_H, FRAME_W, ensureFont, fontCssFor } from './textMotionScene'
import { karaokeAt, karaokePaintStyle, planOf, tokenize, wordStates, type KaraokeData } from './karaokeCore'

export interface KaraokeStageProps {
  text: string
  karaoke: KaraokeData
  /** The caption's own style, so karaoke is the same text, only timed. A field
   *  of `karaoke.text` wins over the matching prop here, exactly as the render
   *  splices those overrides into the caption before it builds its style line —
   *  so the popup can make the sung words bigger, bolder or outline-free without
   *  restyling the caption itself. */
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
  // The one merge: the props are what the caption is styled with, `karaoke.text`
  // is what the user asked the sung words to look like instead. `karaokePaintStyle`
  // is the same function the popup's own preview runs through, and it validates
  // while merging, so a hand-edited project file cannot put a bad colour into CSS.
  const look = karaokePaintStyle(props, karaoke)
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
    void ensureFont(look.family, look.bold, look.italic).then(() => {
      if (alive) setReady(true)
    }).catch(() => {
      if (alive) setReady(true)
    })
    return () => {
      alive = false
    }
  }, [look.family, look.bold, look.italic])

  const px = Math.max(4, (Number(look.fontSize) || 48) * FRAME_W / 1920)
  // The line box the words are laid out in, measured from the font file rather
  // than guessed: `prepareScene` gives libass a line whose height is these win
  // metrics, so measuring here moves the preview towards the MP4 instead of
  // trading one constant for another. 1.02 em is only a floor — a face that wants
  // 1.73 (Anton) gets 1.73. It is *not* what stops the letters being cut: any box
  // a clip or an `overflow` is given clips whatever it is, which is why the reveal
  // below grows its rectangle vertically and why this element's class is its own
  // (see `kara-lyric` in styles.css and StageBoxCollisionTest).
  const faceStyle = effectiveFontStyle(look.family, look.bold, look.italic)
  const metrics = fontMetrics(look.family, faceStyle.bold, faceStyle.italic)
  const boxEm = Math.max(1.02, (metrics.winAscent + metrics.winDescent) / (metrics.upm || 1000))
  const fill = karaoke.fill
  const blockStyle: React.CSSProperties = {
    font: fontCssFor(look.family, look.bold, look.italic, px),
    color: look.colour,
    // `\an5\pos()` anchors the *centre* of the block at x,y for centred text and
    // `\an4` anchors its left edge there, so x moves the block either way. The
    // preview used to drop x whenever the caption was centred — invisible while
    // karaoke could only inherit, visible the moment `karaoke.text.x` exists.
    left: `${look.x}%`,
    top: `${look.y}%`,
    transform: look.align === 'left' ? 'translate(0, -50%)' : 'translate(-50%, -50%)',
    maxWidth: '86%',
    textAlign: look.align === 'left' ? 'left' : 'center',
    lineHeight: boxEm,
    whiteSpace: 'pre-wrap',
    textShadow: look.outline !== false ? '0 2px 8px #0009, 0 0 2px #0007' : 'none',
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
          <div className={`kara-lyric${styleKind === 'pop' ? ' pop' : ''}`} style={blockStyle}>
            {/* `kara-lyric`, not `kara-block`: the strip under the stage builds its
                rows out of `kara-block`, which is a 34 px tall flex line with
                `overflow:hidden`, a background, a border and 9 px of padding — and
                a later rule of that name beat the stage's own, so the lyric line in
                the *preview* was painted inside the editor's block chrome. That is
                what "the background of the karaoke text must fit the height of the
                text size" and "the top and bottom of the letters are cut off"
                actually were: not a mask, and not a line box measured wrong, but a
                borrowed box. The MP4 has no box around a line, so neither may the
                preview. */}
            {shown.map(w => {
              const token = tokens[w.i]
              if (!token) return null
              const index = sentence.words.indexOf(w)
              const state = states[index] || 'next'
              const sweeping = styleKind === 'sweep' && state === 'now'
              const colour = state === 'next' || sweeping ? look.colour : fill
              // The sweep is the same word painted twice: the unsung colour
              // underneath, the sung one laid over it and cut on a vertical line.
              // It used to be a gradient clipped to the text (`background-clip:text`
              // with a transparent fill), and that had the same flaw one level down:
              // *any* clip is a rectangle, and the rectangle a `clip-path` uses is
              // the element's own box. So a face whose ink is taller than its line
              // box — one whose metrics could not be read, a fallback substitution,
              // a display cut that overshoots its recorded ascent — loses its cap
              // and its descender to the reveal just as it did to the gradient,
              // worse the bigger the font. The vertical insets are therefore
              // negative: the rectangle is grown to three times the word's height,
              // which no face paints outside, and the only edge that can bite is the
              // one the sweep is made of. `word-break` never needs a clip; the
              // -webkit- aliases are gone because nothing is clipped to text any more.
              return (
                <Fragment key={w.i}>
                  {/* The gap between two words is a space the face itself measures,
                      written *between* the spans and never inside one. `tokenize`
                      matches `\S+`, so a token carries only its letters; the strip's
                      row used to be a flex line with a `gap`, and that gap was doing
                      the words' spacing for them — once the line was its own box the
                      words ran together. A `margin-right` on `.kara-word` would look
                      the same and be wrong twice: it does not scale with the font, and
                      it cannot wrap, while the render (`karaoke.py`) writes a literal
                      `" "` between the `\kf` runs. So: a text node, which the browser
                      spaces like the MP4's libass does and breaks a line on. No space
                      in front of the first word of the line — the block is centred on
                      its own text, and a leading space would shift the line sideways. */}
                  {index > 0 ? ' ' : null}
                  <span
                    className={`kara-word kara-${state}${look.underline ? ' u' : ''}`}
                    style={{ color: colour }}
                  >
                    {token.w}
                    {sweeping ? <span className="kara-word-sung" style={{ color: fill, clipPath: `inset(-100% ${(100 - Math.max(0, Math.min(1, frame.u)) * 100).toFixed(2)}% -100% 0)` }} aria-hidden="true">{token.w}</span> : null}
                  </span>
                  {token.nl ? '\n' : null}
                </Fragment>
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
