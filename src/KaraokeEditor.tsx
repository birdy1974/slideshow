// Karaoke timing editor: the caption's words, with a clock.
//
// One component, two entry points — the slide's text popup opens it for the
// words and the look, and the movie editor opens the very same strip beside its
// trim handles (docs/karaoke-text-on-movies-options.md). Four surfaces, because
// the tools people actually use for this disagree about which one wins:
//
//   strip   one block per sentence on the clip's own timeline: drag an edge,
//           Shift-click sets the start, Ctrl-click the end (Subtitle Edit's
//           mouse map), and the neighbour follows so nothing overlaps
//   tap     press in time with the words while the movie plays — nothing to aim
//           at, which is why EasyKara and Aegisub's song-timer macro both do it
//   chips   the text itself is the editor: click a word to start its sentence at
//           the playhead, toggle the break after it (Descript's transcript model)
//   wave    the strip over the movie's own audio peaks, so a consonant you can
//           hear is a line you can put a mouse on
//
// All four edit the same derived plan (src/karaokeCore.ts), and the render
// (backend/app/karaoke.py) reads that plan, so the popup cannot show a timing
// the MP4 will not do. Times are clip-local seconds like `textStart`/`textEnd`,
// which is why re-trimming the movie takes the words along with the picture.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlignLeft, Check, ChevronLeft, ChevronRight, Copy, FileMusic, Import, Pause, Play, RotateCcw, Scissors, Wand2, X, ZoomIn, ZoomOut } from 'lucide-react'
import { PopupBackdrop } from './layers'
import { TimeField } from './ui'
import { formatClockPrecise } from './time'
import { movieRootAndPath } from './filmstrip'
import type { MediaItem } from './mediaItem'
import { KaraokeStage } from './KaraokeStage'
import { FontPicker } from './FontPicker'
import {
  KARAOKE_DEFAULTS, KARAOKE_EDITORS, KARAOKE_STYLES, KARAOKE_TEXT_FIELDS, KARAOKE_ZOOM_MAX, autoTime,
  karaokeClipClock, karaokeClipWindow, karaokeFollowScroll, karaokeSpeechSpan,
  clampKaraokeZoom, followSentence, karaokeBlockFloor, karaokePaintStyle, karaokeTextStyleTouched,
  karaokeTickLabel, karaokeTickStep, karaokeTicks, makeContinuous, parseAssKaraoke, parseLrc,
  parseSubtitles, planOf, retarget, ripple, sanitizeKaraoke, snapWords, sentenceGroups,
  karaokeWordBar, karaokeWordBarCut, karaokeWordBarFraction, karaokeWordBarTime,
  timeFromImport, tokenize, wordsPerSecond,
  type KaraokeData, type KaraokeImport, type KaraokeStyle, type KaraokeTextField, type KaraokeTextFlag, type KaraokeTextStyle,
  type KaraokeToken, type KaraokeWord,
} from './karaokeCore'

const round3 = (v: number) => Math.round(v * 1000) / 1000
/**
 * A worked example of every file the importer reads, shown in the popup instead
 * of a sentence *about* the format. Each snippet below is something
 * `parseLrc` / `parseSubtitles` / `parseAssKaraoke` accept as written, because
 * the fastest way to lose an hour is to hand a parser a file you described to
 * yourself rather than one you can see.
 */
const KARAOKE_IMPORT_EXAMPLES: { id: string; label: string; hint: string; body: string; note: string }[] = [
  {
    id: 'lrc',
    label: '.lrc',
    hint: 'song lyrics — one line per sentence',
    body: `[ti:Never Gonna Give You Up]
[00:12.34]Never gonna give you up
[00:15.10]Never gonna let you down
[00:18.00][00:52.45]Give you up, give you up`,
    note: 'mm:ss.x, mm:ss:xx and mm:ss:xxx all parse, several stamps on one line repeat that line, and a bracketed tag that is not a time is dropped. No end times: a line runs until the next one starts.',
  },
  {
    id: 'srt',
    label: '.srt',
    hint: 'subtitles — sentence windows',
    body: `1
00:00:12,340 --> 00:00:15,000
Never gonna give you up

2
00:00:15,100 --> 00:00:18,000
Never gonna let you down
Run around and hurt you`,
    note: 'A blank line between blocks, the number optional, h:mm:ss,mmm or mm:ss.mmm. Every word of a block shares its start, so the sweep inside it is spread by word length: this times sentences, not syllables.',
  },
  {
    id: 'vtt',
    label: '.vtt',
    hint: 'WebVTT — same, with dots',
    body: `WEBVTT

00:00:12.340 --> 00:00:15.000
Never gonna give you up

cue-2
00:00:15.100 --> 00:00:18.000
Never gonna let you down`,
    note: 'The WEBVTT header and a cue id on its own line are ignored, <i> tags and {\k} leftovers are stripped, and the times use a dot instead of a comma.',
  },
  {
    id: 'ass',
    label: '.ass / .ssa',
    hint: 'per-word and per-syllable times',
    body: `[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:12.34,0:00:15.00,FX,,0,0,0,,{\k40}Never {\k35}gonna {\k40}give {\k25}you {\k40}up
Dialogue: 0,0:00:15.10,0:00:18.00,FX,,0,0,0,,{\kf30}N{\kf12}ev{\kf10}er {\k40}gonna`,
    note: 'The only format that times every word: \k is a word, \kf the sweep inside it, and the number is centiseconds (40 = 0.40 s). A Dialogue line needs its ten comma-separated fields. The render writes these same tags, so a file this app exported comes back almost exactly.',
  },
]

/**
 * The strip's zoom lives in `localStorage`, not in `KaraokeData`: it says how
 * wide *this person* wants the ruler, and the render reads `KaraokeData`. A view
 * setting that got saved into the slide would be a change to the project that
 * the MP4 has to ignore anyway.
 */
const ZOOM_KEY = 'slideshow.karaoke.zoom'
function readZoomPref (): number {
  try {
    if (typeof localStorage === 'undefined') return 1
    return clampKaraokeZoom(Number(localStorage.getItem(ZOOM_KEY)) || 1)
  } catch { return 1 }
}
function writeZoomPref (zoom: number): void {
  try { localStorage.setItem(ZOOM_KEY, String(zoom)) } catch { /* private mode: it just does not stick */ }
}
/** How far apart two words must be for the sweep to be visible at all. */
const MIN_WORD_GAP = 0.06

export interface KaraokeEditorProps {
  item: MediaItem
  /** The clip's own <video> url — the same stream the movie editor plays. */
  src: string
  /** Caption defaults, so a slide that never overrode the font looks right. */
  defaults?: { fontFamily?: string; fontSize?: number; fontColor?: string; bold?: boolean; italic?: boolean; outline?: boolean } | null
  onChange: (patch: Partial<MediaItem>) => void
  onClose: () => void
  /** Seconds of the kept cut: the strip's whole width. */
  kept?: number
}

type DragKind = 'seek' | 'block' | 'start' | 'end' | 'word'
interface DragState {
  kind: DragKind
  index: number
  /** Where the pointer went down, in seconds. */
  t0: number
  /** The words as they were then: a drag is always measured from the snapshot,
   *  never from the previous frame's result, so it cannot drift. */
  snapshot: KaraokeWord[]
  /** Which word of the sentence a divider drag belongs to. */
  wordIndex?: number
}

interface Notice {
  kind: 'info' | 'warn'
  text: string
}

function bootstrap(item: MediaItem, tokens: KaraokeToken[], from: number, to: number): { data: KaraokeData; fresh: boolean } {
  const existing = sanitizeKaraoke(item.karaoke, tokens.length)
  if (existing?.words.some(w => Number.isFinite(Number(w.t)))) return { data: existing, fresh: false }
  const base: KaraokeData = { ...KARAOKE_DEFAULTS, words: [] }
  if (!tokens.length) return { data: base, fresh: true }
  return { data: { ...base, words: autoTime(tokens, tokens.map(() => ({})), base.maxWords, from, to) }, fresh: true }
}

/** The karaoke popup. `item.text` owns the words; this owns when. */
export function KaraokeEditor({ item, src, defaults, onChange, onClose, kept }: KaraokeEditorProps) {
  // The window the render will really use: the caption's, widened to the tapped
  // line (same rule as `speech_span` in the backend), so the strip never times a
  // word into a second the MP4 will not show it in.
  const speech = karaokeSpeechSpan(sanitizeKaraoke(item.karaoke, tokenize(item.text || '').length))
  const windowStart = Math.max(0, Math.min(Number(item.textStart) || 0, speech ? speech[0] : Infinity))
  // Where the strip's zero is and how wide it is: the kept region of the file, not
  // the file. `MovieEditor` hands the exact length (it has the decoder's `total`);
  // opened from anywhere else the item's own IN/OUT pair says the same thing, which
  // is why the popup used to keep showing the whole movie after a cut.
  const clip = useMemo(() => karaokeClipWindow(
    { trimStart: item.trimStart, trimEnd: item.trimEnd, duration: item.duration },
    kept
  ), [item.trimStart, item.trimEnd, item.duration, kept])
  const clipSeconds = clip.span
  const windowEnd = Math.max(windowStart + 0.5, Math.min(clipSeconds, Math.max(Number(item.textEnd) || clipSeconds, speech ? speech[1] : 0)))

  const [draftText, setDraftText] = useState(item.text || '')
  const tokens = useMemo(() => tokenize(draftText), [draftText])
  const seeded = useRef<{ data: KaraokeData; fresh: boolean } | null>(null)
  if (seeded.current === null) seeded.current = bootstrap(item, tokens, windowStart, windowEnd)
  const [data, setData] = useState<KaraokeData>(seeded.current.data)

  const [selected, setSelected] = useState(0)
  /** Whether the panel is following the playhead or the user put it somewhere. */
  const [follow, setFollow] = useState(true)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(windowStart)
  const [rate, setRate] = useState(1)
  const [loopLine, setLoopLine] = useState(false)
  const [showImport, setShowImport] = useState(false)
  /** Which example the import panel is showing — the tab you last read. */
  const [importExample, setImportExample] = useState(KARAOKE_IMPORT_EXAMPLES[0].id)
  const [notice, setNotice] = useState<Notice | null>(seeded.current.fresh
    ? (tokens.length ? { kind: 'info', text: 'Spread evenly for now — press play and tap along, or drag the blocks, and it becomes yours.' } : { kind: 'warn', text: 'Nothing to time yet: write the words first.' })
    : null)
  const [peaks, setPeaks] = useState<{ values: number[]; duration: number } | null>(null)
  /** How wide the strip is drawn, × its box. 1 is "the whole clip fits". */
  const [zoom, setZoom] = useState(readZoomPref)
  const [onsets, setOnsets] = useState<number[]>([])

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const stripRef = useRef<HTMLDivElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const zoomRef = useRef(zoom)
  /** The time the pointer was on when the strip changed width: re-placed on the
   *  next paint, so zooming never slides the ruler out from under the cursor. */
  const anchor = useRef<{ t: number; at: number } | null>(null)
  const dragRef = useRef<DragState | null>(null)
  const pendingRef = useRef<KaraokeData | null>(null)
  const flushTimer = useRef<number | null>(null)
  const original = useRef({ text: item.text || '', karaoke: item.karaoke })
  const trimStart = clip.start
  const tapIndex = useRef(0)
  const [tapArmed, setTapArmed] = useState(false)
  const [tapMode, setTapMode] = useState<'line' | 'word'>('line')

  const span = Math.max(1, clipSeconds)
  useEffect(() => {
    zoomRef.current = zoom
  }, [zoom])
  const zoomTo = useCallback((value: number) => {
    const next = clampKaraokeZoom(value)
    setZoom(next)
    writeZoomPref(next)
  }, [])
  const plan = useMemo(() => planOf(draftText, data, { from: windowStart, to: windowEnd }), [draftText, data, windowStart, windowEnd])
  const groups = useMemo(() => sentenceGroups(tokens, data.words, data.maxWords), [tokens, data.words, data.maxWords])
  // What the subtitle editors do: while the movie plays, the panel and the strip
  // ride the playhead, so "Selected sentence 3 of 15" is the line you are hearing
  // (and the next one during a gap, ready to nudge). Paused, or while a block is
  // under the pointer, the selection is wherever the user left it — following is
  // turned off by clicking a line and back on by playing again.
  useEffect(() => {
    if (!playing || !follow || dragRef.current) return
    const index = followSentence(plan, position)
    if (index >= 0) setSelected(current => (current === index ? current : index))
  }, [playing, follow, position, plan])
  useEffect(() => {
    if (playing) setFollow(true)
  }, [playing])
  /** A deliberate selection stops the follow, so the panel stays on the line the
   *  user aimed at; pressing play picks the cursor back up. */
  const selectLine = (value: number | ((index: number) => number)) => {
    setFollow(false)
    setSelected(value)
  }
  const current = plan.sentences[Math.min(selected, Math.max(0, plan.sentences.length - 1))]
  const perSecond = wordsPerSecond(plan)

  // ---- commits: the popup edits a draft, the project keeps the result ----
  const flush = useCallback(() => {
    if (flushTimer.current !== null) {
      window.clearTimeout(flushTimer.current)
      flushTimer.current = null
    }
    const next = pendingRef.current
    pendingRef.current = null
    if (next) onChange({ karaoke: next })
  }, [onChange])
  /** Live edits (drags, taps) repaint the popup immediately and patch the
   *  project on the next frame, so a 200-event drag does not mean 200 saves. */
  const edit = useCallback((next: KaraokeData) => {
    setData(next)
    pendingRef.current = next
    if (flushTimer.current === null) flushTimer.current = window.setTimeout(flush, 140)
  }, [flush])
  /** A click, a key or a button commits at once — those are deliberate. */
  const apply = useCallback((next: KaraokeData, alsoText?: string) => {
    setData(next)
    pendingRef.current = null
    if (flushTimer.current !== null) {
      window.clearTimeout(flushTimer.current)
      flushTimer.current = null
    }
    onChange(alsoText === undefined ? { karaoke: next } : { text: alsoText, karaoke: next })
  }, [onChange])
  useEffect(() => () => {
    if (flushTimer.current !== null) window.clearTimeout(flushTimer.current)
  }, [])

  // ---- the clip's own clock ----
  // The movie is only ever *shown* from IN to OUT, so the clock has to say so too:
  // the element's time is offset by the IN point, and reaching the OUT point parks
  // the playhead at the end of the strip and stops the picture. Without the second
  // half the strip ended and the movie kept running — "the karaoke editor still
  // shows the complete movie", which is a bug in this mapping and not in the file.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    const onTime = () => {
      const clock = karaokeClipClock(video.currentTime, clip)
      setPosition(clock.position)
      if (!clock.ended || clip.cutAt === null) return
      video.pause()
      if (video.currentTime > clip.cutAt + 0.001) video.currentTime = clip.cutAt
    }
    const onEnded = () => { setPosition(clip.span); setPlaying(false) }
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    video.addEventListener('timeupdate', onTime)
    video.addEventListener('ended', onEnded)
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    return () => {
      video.removeEventListener('timeupdate', onTime)
      video.removeEventListener('ended', onEnded)
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
    }
  }, [clip])
  useEffect(() => {
    const video = videoRef.current
    if (video) {
      video.playbackRate = rate
      video.volume = 1
    }
  }, [rate, playing])
  const seek = useCallback((t: number) => {
    const clamped = Math.max(0, Math.min(span, t))
    setPosition(clamped)
    const video = videoRef.current
    if (video) video.currentTime = trimStart + clamped
  }, [span, trimStart])
  const togglePlay = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    if (!video.paused) { video.pause(); return }
    // Playing from the end of the cut means "again": back to the IN point rather
    // than a frame that is not in the movie at all.
    if (video.currentTime >= (clip.cutAt ?? clip.start + clip.span) - 0.05 || video.currentTime < clip.start - 0.05) {
      video.currentTime = clip.start
      setPosition(0)
    }
    void video.play().catch(() => setNotice({ kind: 'warn', text: 'The browser would not play this file — the strip, the keys and the import still time the words.' }))
  }, [clip])
  // The A–B micro-loop: "play a second and back", the verify gesture every
  // subtitle editor has, here around the whole selected sentence.
  useEffect(() => {
    if (!playing || !loopLine || !current) return
    if (position > current.end + 0.05) seek(current.start)
  }, [playing, loopLine, position, current, seek])

  // ---- audio data: onsets for the magnet, peaks for the waveform lane ----
  const { root, relative } = movieRootAndPath(item)
  const analyzable = root === 'videos' || root === 'photos'
  useEffect(() => {
    if (!analyzable) return
    let alive = true
    void (async () => {
      try {
        const response = await fetch(`/api/media/beats?root=${root}&path=${encodeURIComponent(relative)}`)
        if (!response.ok) return
        const json = await response.json()
        const list: unknown[] = Array.isArray(json?.beats) ? json.beats : []
        // The analysis is file-local; the strip runs on the kept cut.
        const times = list.filter((v): v is number => typeof v === 'number' && Number.isFinite(v)).map(v => round3(v - trimStart)).filter(v => v >= 0 && v <= span)
        if (alive && times.length) setOnsets(times)
      } catch { /* no onsets: the magnet quietly does nothing */ }
    })()
    return () => {
      alive = false
    }
  }, [analyzable, root, relative, trimStart, span])
  useEffect(() => {
    if (!analyzable || data.editor !== 'wave' || peaks) return
    let alive = true
    void (async () => {
      try {
        const response = await fetch(`/api/media/waveform?root=${root}&path=${encodeURIComponent(relative)}&buckets=700&start=${trimStart.toFixed(2)}&length=${span.toFixed(2)}`)
        if (!response.ok) return
        const json = await response.json()
        const list: number[] = Array.isArray(json?.peaks) ? json.peaks.map((v: unknown) => Number(v) || 0) : []
        if (alive) setPeaks({ values: list, duration: Number(json?.duration) || span })
      } catch { /* no waveform: the strip stays fully usable without the lane */ }
    })()
    return () => {
      alive = false
    }
  }, [analyzable, data.editor, peaks, root, relative, trimStart, span])

  // ---- the edits every surface shares ----
  const withWords = (words: KaraokeWord[], commit: boolean) => {
    const next = { ...data, words }
    if (commit) apply(next)
    else edit(next)
  }
  const setWordTime = (i: number, t: number, opts: { rippleAfter?: boolean; snap?: boolean } = {}) => {
    if (i < 0) return
    const before = Number(data.words[i]?.t)
    let value = Math.max(0, round3(t))
    if (opts.snap !== false && data.snap > 0 && onsets.length) {
      const snapped = snapWords([{ t: value }], onsets, data.snap)[0]?.t
      if (Number.isFinite(snapped)) value = Math.max(0, round3(snapped as number))
    }
    const words = data.words.map(w => ({ ...w }))
    while (words.length <= i) words.push({})
    words[i] = { ...words[i], t: value }
    let next = words
    if (opts.rippleAfter && Number.isFinite(before)) {
      const delta = value - before
      if (delta !== 0) next = ripple(words, i + 1, delta)
    }
    withWords(makeContinuous(next, MIN_WORD_GAP), false)
  }
  const setBreak = (i: number, value: 1 | 0) => {
    if (i < 0 || i >= tokens.length) return
    const words = data.words.map(w => ({ ...w }))
    if (value === 0) delete words[i].br
    else words[i] = { ...words[i], br: value }
    withWords(makeContinuous(words), true)
  }
  const nudge = (delta: number, from: number) => withWords(makeContinuous(ripple(data.words, from, delta)), true)
  const autoPass = () => {
    if (!tokens.length) return
    apply({ ...data, words: autoTime(tokens, data.words, data.maxWords, windowStart, windowEnd) })
    setNotice({ kind: 'info', text: `${tokens.length} words spread over ${groups.length || 1} sentences — drag what the guess got wrong.` })
  }
  const snapPass = () => {
    if (!onsets.length) {
      setNotice({ kind: 'warn', text: 'No onsets to snap to — this file’s audio could not be analysed. Time it by ear instead.' })
      return
    }
    apply({ ...data, words: makeContinuous(snapWords(data.words, onsets, Math.max(data.snap, 0.1))) })
    setNotice({ kind: 'info', text: `Snapped onto the nearest onset within ${Math.round(Math.max(data.snap, 0.1) * 1000)} ms (${onsets.length} onsets found).` })
  }
  const fitToWindow = () => {
    const times = data.words.map(w => Number(w.t)).filter(Number.isFinite)
    if (times.length < 2) return autoPass()
    const first = Math.min(...times)
    const last = Math.max(...times)
    const scale = last > first ? (windowEnd - windowStart) / (last - first) : 1
    apply({
      ...data,
      words: makeContinuous(data.words.map(w => (Number.isFinite(Number(w.t)) ? { ...w, t: round3(windowStart + (Number(w.t) - first) * scale) } : { ...w }))),
    })
  }
  const spaceEvenly = () => {
    const idx = data.words.map((w, i) => (Number.isFinite(Number(w.t)) ? i : -1)).filter(i => i >= 0)
    if (idx.length < 2) return
    const times = idx.map(i => Number(data.words[i].t))
    const first = Math.min(...times)
    const last = Math.max(...times)
    const step = (last - first) / (idx.length - 1)
    const words = data.words.map(w => ({ ...w }))
    idx.forEach((wi, k) => {
      words[wi] = { ...words[wi], t: round3(first + k * step) }
    })
    apply({ ...data, words: makeContinuous(words) })
  }
  const splitAtPlayhead = () => {
    let at = -1
    for (let i = 1; i < tokens.length; i++) {
      const t = Number(data.words[i]?.t)
      if (Number.isFinite(t) && t >= position) {
        at = i
        break
      }
    }
    if (at <= 0) return
    setBreak(at - 1, 1)
  }
  const dropBreak = () => {
    if (!current) return
    if (current.from > 0) setBreak(current.from - 1, 0)
    else if (current.to < tokens.length - 1) setBreak(current.to, 0)
  }
  const startLineHere = () => {
    if (!current) return
    const before = Number(data.words[current.from]?.t) || position
    const delta = position - before
    const words = makeContinuous(ripple(data.words.map(w => ({ ...w })), current.from, delta))
    apply({ ...data, words })
  }

  // ---- tap-along: the pass that needs no aiming ----
  const tapTargets = useMemo(() => (tapMode === 'line' ? groups.map(([a]) => a) : tokens.map((_, i) => i)), [tapMode, groups, tokens])
  const startTapping = () => {
    if (!tapTargets.length) return
    tapIndex.current = 0
    setTapArmed(true)
    seek(0)
    const video = videoRef.current
    if (video?.paused) void video.play().catch(() => undefined)
  }
  const tap = () => {
    const i = tapTargets[tapIndex.current]
    if (i === undefined) {
      setTapArmed(false)
      flush()
      setNotice({ kind: 'info', text: 'Skeleton in. Fine pass next: select a sentence and tap its words, or drag the dividers.' })
      return
    }
    const words = data.words.map(w => ({ ...w }))
    while (words.length <= i) words.push({})
    words[i] = { ...words[i], t: Math.max(0, round3(position)) }
    if (tapMode === 'line' && i + 1 < tokens.length && tapIndex.current + 1 < tapTargets.length) words[i] = { ...words[i] }
    tapIndex.current += 1
    setData({ ...data, words })
    pendingRef.current = { ...data, words }
    if (flushTimer.current === null) flushTimer.current = window.setTimeout(flush, 140)
    setNotice({ kind: 'info', text: `${tapIndex.current}/${tapTargets.length} — “${tokens[i]?.w ?? ''}” stamped at ${formatClockPrecise(position)}.` })
  }
  const undoTap = () => {
    if (tapIndex.current <= 0) return
    tapIndex.current -= 1
    const i = tapTargets[tapIndex.current]
    const words = data.words.map(w => ({ ...w }))
    if (i !== undefined) delete words[i].t
    apply({ ...data, words })
  }

  // ---- keyboard ----
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const el = event.target as HTMLElement | null
      const tag = el?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable) {
        if (event.key === 'Enter' && tag === 'TEXTAREA') (el as HTMLTextAreaElement).blur()
        return
      }
      const line = plan.sentences[Math.min(selected, Math.max(0, plan.sentences.length - 1))]
      const from = line?.from ?? 0
      switch (event.key) {
        case ' ':
          event.preventDefault()
          if (tapArmed) tap()
          else togglePlay()
          return
        case 'Enter':
          event.preventDefault()
          if (line) seek(Math.min(span, line.end + 0.12))
          selectLine(i => Math.min(Math.max(0, plan.sentences.length - 1), i + 1))
          return
        case 'Backspace':
          if (tapArmed) {
            event.preventDefault()
            undoTap()
          }
          return
        case 'ArrowLeft':
          event.preventDefault()
          if (event.shiftKey) nudge(-0.1, from)
          else seek(position - (event.altKey ? 1 / 25 : 0.1))
          return
        case 'ArrowRight':
          event.preventDefault()
          if (event.shiftKey) nudge(0.1, from)
          else seek(position + (event.altKey ? 1 / 25 : 0.1))
          return
        case 'ArrowUp':
          event.preventDefault()
          selectLine(i => Math.max(0, i - 1))
          return
        case 'ArrowDown':
          event.preventDefault()
          selectLine(i => Math.min(Math.max(0, plan.sentences.length - 1), i + 1))
          return
        case 'Tab':
          event.preventDefault()
          selectLine(i => (i + (event.shiftKey ? -1 : 1) + plan.sentences.length) % Math.max(1, plan.sentences.length))
          return
        case 'Delete':
          dropBreak()
          return
        case 's':
        case 'S':
          splitAtPlayhead()
          return
        case 'm':
        case 'M':
          dropBreak()
          return
        case 'i':
        case 'I':
          if (line) setWordTime(line.from, position, { rippleAfter: true })
          return
        case 'o':
        case 'O':
          if (line) setWordTime(line.to, position)
          return
        case 'f':
        case 'F':
          setRate(r => (r === 1 ? 0.75 : r === 0.75 ? 0.5 : 1))
          return
        case 'l':
        case 'L':
          setLoopLine(v => !v)
          return
        case 't':
        case 'T':
          if (tapArmed) setTapArmed(false)
          else startTapping()
          return
        case 'a':
        case 'A':
          autoPass()
          return
        case 'b':
        case 'B':
          snapPass()
          return
        case '+':
        case '=':
          zoomTo(zoomRef.current * 1.4)
          return
        case '-':
        case '_':
          zoomTo(zoomRef.current / 1.4)
          return
        case '0':
          zoomTo(1)
          return
        default:
          if (/^[1-4]$/.test(event.key)) {
            const mode = KARAOKE_EDITORS[Number(event.key) - 1]
            if (mode) apply({ ...data, editor: mode.id })
          }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  })

  // ---- strip geometry: one mapping, every lane ----
  // Zooming is only ever making that one mapping wider inside a scroller: the
  // blocks, dividers, handles and the playhead are percentages, and
  // `pointerTime` measures the wide strip's own rect, so drag maths stays exact
  // at any width and no lane needs a second coordinate system.
  const toPercent = (t: number) => `${Math.max(0, Math.min(100, (t / span) * 100))}%`
  const stripWidth = zoom > 1.001 ? { width: `${zoom * 100}%` } : undefined
  const pointerTime = (clientX: number) => {
    const rect = stripRef.current?.getBoundingClientRect()
    if (!rect || rect.width < 8) return 0
    return Math.max(0, Math.min(span, ((clientX - rect.left) / rect.width) * span))
  }
  const beginDrag = (kind: DragKind, index: number) => (event: React.PointerEvent) => {
    if (kind !== 'seek') event.stopPropagation()
    event.preventDefault()
    const t = pointerTime(event.clientX)
    const line = plan.sentences[index]
    if (kind === 'seek') {
      seek(t)
      return
    }
    dragRef.current = { kind, index, t0: t, snapshot: data.words.map(w => ({ ...w })) }
    selectLine(index)
    ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
    if (kind === 'block' && (event.shiftKey || event.ctrlKey || event.metaKey)) {
      // Modifier-click on a block: set that edge and ripple from there — the
      // gesture that keeps a fix on line 3 from breaking lines 4 to 40.
      const at = event.ctrlKey || event.metaKey ? line?.to : line?.from
      if (at !== undefined) setWordTime(at, t, { rippleAfter: !(event.ctrlKey || event.metaKey) })
      dragRef.current = null
    }
  }
  const moveDrag = (event: React.PointerEvent) => {
    const drag = dragRef.current
    if (!drag) return
    const line = plan.sentences[drag.index]
    if (!line) return
    const t = pointerTime(event.clientX)
    const words = drag.snapshot.map(w => ({ ...w }))
    if (drag.kind === 'block') {
      const delta = t - drag.t0
      if (Math.abs(delta) < 0.004) return
      withWords(makeContinuous(ripple(words, line.from, delta)), false)
      return
    }
    let target: number | undefined
    if (drag.kind === 'end') target = line.to
    else if (drag.kind === 'start') target = line.from
    else target = line.words[Math.min(drag.wordIndex ?? 0, line.words.length - 1)]?.i
    if (target === undefined) return
    const value = Math.max(0, round3(t))
    const next = drag.snapshot.map(w => ({ ...w }))
    next[target] = { ...next[target], t: value }
    // A word can never land on top of the previous one: makeContinuous keeps
    // the order, and MIN_WORD_GAP is the smallest sweep worth drawing.
    withWords(makeContinuous(next, MIN_WORD_GAP), false)
  }
  const endDrag = () => {
    if (!dragRef.current) return
    dragRef.current = null
    flush()
  }

  // ---- the word bar: one sentence across the full width of the preview ----
  //
  // The strip above owns the clip, so a sentence's words are a handful of pixels
  // and their dividers are millimetres apart. This bar owns the *selected
  // sentence*: the same seconds, stretched over the whole preview width, one
  // draggable boundary per word. Both edit `data.words`, so there is nothing to
  // keep in sync — the bar is the strip, zoomed to the line you are fixing.
  const wordBar = useMemo(() => karaokeWordBar(current, windowEnd), [current, windowEnd])
  const barRef = useRef<HTMLDivElement | null>(null)
  const barDragRef = useRef<{ cut: number; ripple: boolean; snapshot: KaraokeWord[] } | null>(null)
  const barFraction = (clientX: number) => {
    const rect = barRef.current?.getBoundingClientRect()
    if (!rect || rect.width < 8) return 0
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
  }
  /** Seconds on the clip clock under a pointer over the bar. */
  const barTime = (clientX: number) => karaokeWordBarTime(wordBar, barFraction(clientX))
  const beginBarCut = (k: number) => (event: React.PointerEvent) => {
    event.stopPropagation()
    event.preventDefault()
    // Shift takes the rest of the line with it, exactly as a modifier-click on a
    // sentence block does: fixing one boundary should not un-time its neighbours.
    barDragRef.current = { cut: k, ripple: event.shiftKey, snapshot: data.words.map(w => ({ ...w })) }
    ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  }
  const moveBar = (event: React.PointerEvent) => {
    const drag = barDragRef.current
    if (!drag || !wordBar) return
    const cut = karaokeWordBarCut(wordBar, drag.cut, barTime(event.clientX), { minGap: MIN_WORD_GAP })
    if (!cut) return
    const words = drag.snapshot.map(w => ({ ...w }))
    while (words.length <= cut.i) words.push({})
    words[cut.i] = { ...words[cut.i], t: cut.t }
    withWords(makeContinuous(drag.ripple ? ripple(words, cut.i + 1, cut.delta) : words, MIN_WORD_GAP), false)
  }
  const endBar = () => {
    if (!barDragRef.current) return
    barDragRef.current = null
    flush()
  }
  // ⌘/Ctrl+wheel over the strip is the zoom gesture every map and audio editor
  // has taught people; a plain wheel just scrolls along the clip. It goes on as a
  // native listener because React's `onWheel` is passive and so cannot keep the
  // browser from zooming the whole page instead.
  useEffect(() => {
    const box = scrollRef.current
    if (!box) return
    const onWheel = (event: WheelEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return
      event.preventDefault()
      const strip = stripRef.current
      const rect = box.getBoundingClientRect()
      const width = Math.max(1, strip?.offsetWidth || rect.width)
      anchor.current = { t: ((box.scrollLeft + event.clientX - rect.left) / width) * span, at: event.clientX - rect.left }
      zoomTo(zoomRef.current * Math.exp(-event.deltaY * 0.0016))
    }
    box.addEventListener('wheel', onWheel, { passive: false })
    return () => box.removeEventListener('wheel', onWheel)
  }, [span, zoomTo])
  // Re-place the anchored time once the strip has been redrawn at its new width.
  useEffect(() => {
    const box = scrollRef.current
    const strip = stripRef.current
    const held = anchor.current
    if (!box || !strip || !held) return
    anchor.current = null
    box.scrollLeft = Math.max(0, (held.t / span) * strip.offsetWidth - held.at)
  }, [zoom, span])
  // Zoomed in, the strip scrolls and the playhead does not: each frame of playback
  // puts the line back under the centre of the box, so what you are hearing is
  // always in the middle of your view instead of flicking between its two edges.
  // (The line is drawn inside the strip, so centring the *scroll* is what pins it;
  // at the ends of the clip the clamp lets it walk to the edge, which is the only
  // honest answer when there is no film left to slide.) Only while the movie runs:
  // a scrub drives the position from the pointer, and centring the view under a
  // pointer that is itself moving through it is a feedback loop — that case has the
  // held-offset rule above instead. Nothing fights a manual scroll either, since
  // that does not move the position. Below fit-zoom there is nothing to scroll, and
  // the line rides its own percentage as it always did.
  useEffect(() => {
    const box = scrollRef.current
    const strip = stripRef.current
    if (!box || !strip) return
    // Coming back out to fit, the answer is "nowhere to scroll": a stale offset
    // from a deeper zoom would otherwise be left where the browser's own clamp
    // happens to have landed it, which is not the same as being at the start.
    if (zoom <= 1.001) { box.scrollLeft = 0; return }
    if (!playing) return
    box.scrollLeft = karaokeFollowScroll((position / span) * strip.offsetWidth, box.clientWidth)
  }, [position, playing, zoom, span])
  /** Alt-click puts the clicked time in the middle of the box — the reach for a
   *  divider you can only hit once you are zoomed in. The seek still happens. */
  const onStripPointerDown = (event: React.PointerEvent) => {
    if (event.altKey && zoom > 1.001) {
      const box = scrollRef.current
      const strip = stripRef.current
      if (box && strip) box.scrollLeft = karaokeFollowScroll((pointerTime(event.clientX) / span) * strip.offsetWidth, box.clientWidth)
    }
    beginDrag('seek', -1)(event)
  }

  // ---- import a file that already knows the times ----
  const onImportFile = async (file: File | undefined) => {
    if (!file) return
    const body = await file.text()
    const ext = (file.name.split('.').pop() || '').toLowerCase()
    let parsed: KaraokeImport
    if (ext === 'lrc') parsed = parseLrc(body)
    else if (ext === 'ass' || ext === 'ssa') parsed = parseAssKaraoke(body)
    else if (ext === 'vtt') parsed = parseSubtitles(body, 'vtt')
    else parsed = parseSubtitles(body, 'srt')
    if (!parsed.cues.length) {
      setNotice({ kind: 'warn', text: `No timed lines could be read from ${file.name}.` })
      return
    }
    const nextText = draftText.trim() ? draftText : parsed.text
    const nextTokens = tokenize(nextText)
    const mapped = timeFromImport(nextTokens, nextText, parsed, trimStart, { maxWords: data.maxWords })
    const filled = mapped.filled > 0
    const words = filled ? mapped.words : autoTime(nextTokens, mapped.words, data.maxWords, windowStart, windowEnd)
    setDraftText(nextText)
    apply({ ...data, words: makeContinuous(words) }, nextText)
    setShowImport(false)
    setNotice({
      kind: filled ? 'info' : 'warn',
      text: filled
        ? `${file.name}: ${mapped.filled} of ${nextTokens.length} words timed${parsed.source === 'ass' ? ', read straight from the \\k tags' : ''}.`
        : `${file.name} shares no words with this caption, so its ${parsed.cues.length} line times became sentence marks and the words were spread inside them.`,
    })
  }

  const commitText = () => {
    if (draftText === (item.text || '')) return
    const nextTokens = tokenize(draftText)
    apply({ ...data, words: retarget(data, tokens, nextTokens) }, draftText)
    if (nextTokens.length !== tokens.length) setNotice({ kind: 'info', text: `Text updated: ${nextTokens.length} words, timings carried across by word.` })
  }

  const close = () => {
    flush()
    commitText()
    onClose()
  }
  const cancel = () => {
    pendingRef.current = null
    if (flushTimer.current !== null) window.clearTimeout(flushTimer.current)
    onChange({ text: original.current.text, karaoke: original.current.karaoke })
    onClose()
  }

  const stageStyle = {
    family: item.fontFamily || defaults?.fontFamily || 'Montserrat',
    bold: item.textBold ?? defaults?.bold ?? true,
    italic: item.textItalic ?? defaults?.italic ?? false,
    underline: item.textUnderline ?? false,
    colour: item.fontColor || defaults?.fontColor || '#ffffff',
    fontSize: Number(item.fontSize) || Number(defaults?.fontSize) || 48,
    x: Number.isFinite(Number(item.textX)) ? Number(item.textX) : 50,
    y: Number.isFinite(Number(item.textY)) ? Number(item.textY) : 72,
    outline: item.textOutline ?? defaults?.outline ?? true,
  }
  // ---- how the sung words look ----
  // `KARAOKE_TEXT_FIELDS` is the whole list, and it is the same nine the render
  // splices into the caption (`TEXT_STYLE` in backend/app/karaoke.py) — so every
  // control here has a promise the MP4 keeps, and there is no control for
  // something only the browser could do. Nothing set = the caption's own style,
  // which is why there is no "apply" step and no second slide to make: the text
  // stays one block of words with one clock, wearing whatever you dress it in.
  const over = data.text || {}
  const look = karaokePaintStyle(stageStyle, data)
  const touched = karaokeTextStyleTouched(data)
  const patchLook = (key: keyof KaraokeTextStyle, value: KaraokeTextStyle[keyof KaraokeTextStyle] | undefined) => {
    const next: KaraokeTextStyle = { ...over }
    if (value === undefined) delete next[key]
    else (next as Record<string, unknown>)[key] = value
    const merged: KaraokeData = { ...data }
    if (Object.keys(next).length) merged.text = next
    else delete merged.text
    apply(merged)
  }
  // A flag has three honest states: whatever the caption says, on, off. One click
  // per step, and the chip says which one you are in — the caption's own value is
  // the invisible third, so it has to be spelled out.
  const flagCycle = (key: KaraokeTextFlag) => {
    const pinned = over[key]
    patchLook(key, pinned === undefined ? !look[key] : pinned ? false : undefined)
  }
  const textField = (id: keyof KaraokeTextStyle) => KARAOKE_TEXT_FIELDS.find(field => field.id === id)
  // The typed-with-number fields, in the order the table lists them. The cast is
  // the filter's, not the model's: `kind` says which ones these are.
  const lookNumbers = KARAOKE_TEXT_FIELDS.filter(field => field.kind === 'size' || field.kind === 'pos') as
    (KaraokeTextField & { id: 'size' | 'x' | 'y' })[]
  const flagState = (key: KaraokeTextFlag) => over[key] === undefined
    ? `the caption's ${look[key] ? 'on' : 'off'}`
    : over[key] ? 'on' : 'off'
  const wavePath = useMemo(() => {
    if (!peaks || peaks.values.length < 2) return ''
    const n = peaks.values.length
    const width = 1000
    const top: string[] = []
    const bottom: string[] = []
    for (let x = 0; x < width; x++) {
      const at = Math.min(n - 1, Math.max(0, Math.round((x / (width - 1)) * (n - 1))))
      const v = Math.max(0, Math.min(1, peaks.values[at]))
      top.push(`${(50 - v * 47).toFixed(1)}`)
      bottom.unshift(`${(50 + v * 47).toFixed(1)}`)
    }
    const points = top.map((y, x) => `${x},${y}`).concat(bottom.map((y, i) => `${width - 1 - i},${y}`))
    return `M${points.join(' L')} Z`
  }, [peaks])
  // A tick every ~55 px of the *drawn* strip: what the strip's own heuristic drew
  // at zoom 1, one finer rung for each step in, down to a hundredth of a second at
  // the top of the range — which is the whole point of zooming in.
  const ticks = useMemo(() => {
    const step = karaokeTickStep(span, zoom)
    return { step, at: karaokeTicks(span, zoom) }
  }, [span, zoom])

  return <PopupBackdrop className="dark-backdrop karaoke-backdrop" onDismiss={cancel}>
    <div className="karaoke-editor" onMouseDown={event => event.stopPropagation()}>
      <div className="modal-head">
        <div><span className="eyebrow">KARAOKE TIMING</span><h2>{item.name || 'Caption'}</h2></div>
        <div className="kara-modes">
          {KARAOKE_EDITORS.map((mode, i) => <button key={mode.id} type="button" className={data.editor === mode.id ? 'active' : ''} title={`${mode.hint} (key ${i + 1})`} onClick={() => apply({ ...data, editor: mode.id })}>{mode.label}</button>)}
        </div>
        <button className="icon-button" onClick={cancel} title="Close without saving"><X size={19}/></button>
      </div>

      <div className="karaoke-body">
        <section className="kara-left">
          <div className="kara-stage-wrap">
            <video ref={videoRef} className="kara-video" src={src} onClick={togglePlay} playsInline/>
            <KaraokeStage text={draftText} karaoke={data} at={position} from={windowStart} to={windowEnd} className="kara-overlay" {...stageStyle}/>
            {tapArmed ? <div className="kara-tap-cue">
              <b>{tokens[tapTargets[tapIndex.current]]?.w ?? 'finish'}</b>
              <span>Space stamps it at {formatClockPrecise(position)} · {Math.min(tapIndex.current + 1, tapTargets.length)}/{tapTargets.length}</span>
            </div> : null}
          </div>

          <div className="kara-transport">
            <button className="icon-button" onClick={togglePlay} title="Play / pause (Space)">{playing ? <Pause size={16}/> : <Play size={16}/>}</button>
            <button className="icon-button" onClick={() => seek(position - 1)} title="Back a second"><ChevronLeft size={16}/></button>
            <button className="icon-button" onClick={() => seek(position + 1)} title="Forward a second"><ChevronRight size={16}/></button>
            <span className="transport-clock">{formatClockPrecise(position)} / {formatClockPrecise(span)}</span>
            <label className="kara-rate" title="Slow playback to catch fast words (F)">
              <span>Speed</span>
              <select value={rate} onChange={event => setRate(Number(event.target.value))}>{[0.5, 0.75, 1, 1.25].map(v => <option key={v} value={v}>{v}×</option>)}</select>
            </label>
            <button type="button" className={`btn ghost${loopLine ? ' on' : ''}`} onClick={() => setLoopLine(v => !v)} title="Loop the selected sentence (L)">Loop line</button>
            <button type="button" className={`btn ghost${tapArmed ? ' on' : ''}`} onClick={() => (tapArmed ? setTapArmed(false) : startTapping())} title="Play and tap along (T): Space stamps the next word, Backspace undoes one">
              <Play size={13}/>{tapArmed ? `Tapping ${tapIndex.current}/${tapTargets.length}` : 'Tap along'}
            </button>
            <div className="kara-scope" title="What one tap marks">
              <button type="button" className={tapMode === 'line' ? 'active' : ''} onClick={() => setTapMode('line')}>Lines</button>
              <button type="button" className={tapMode === 'word' ? 'active' : ''} onClick={() => setTapMode('word')}>Words</button>
            </div>
            <div className="kara-zoom">
              <button type="button" onClick={() => zoomTo(zoom / 1.4)} title="Zoom out — more of the clip in view"><ZoomOut size={13}/></button>
              <input type="range" min={1} max={KARAOKE_ZOOM_MAX} step={0.05} value={zoom} aria-label="Karaoke timeline zoom" onChange={event => zoomTo(Number(event.target.value))}/>
              <b>{Math.round(zoom * 100)}%</b>
              <button type="button" onClick={() => zoomTo(zoom * 1.4)} title="Zoom in — a divider per syllable"><ZoomIn size={13}/></button>
              <button type="button" className={zoom > 1.001 ? '' : 'on'} onClick={() => zoomTo(1)} title="Fit the whole clip in the box (0)">Fit</button>
            </div>
          </div>

          <div className="kara-strip-scroll" ref={scrollRef}>
          <div className={`kara-strip${data.editor === 'wave' ? ' wave' : ''}`} ref={stripRef} style={stripWidth}
               onPointerDown={onStripPointerDown} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>
            {data.editor === 'wave' ? <div className="kara-wave">
              {wavePath ? <svg viewBox="0 0 1000 100" preserveAspectRatio="none"><path d={wavePath}/></svg>
                : <span className="kara-wave-note">{analyzable ? 'reading the movie’s audio…' : 'this file has no audio to read'}</span>}
              {onsets.slice(0, 400).map((t, i) => <i key={i} style={{ left: toPercent(t) }}/>)}
            </div> : null}

            <div className="kara-ruler">{ticks.at.map(t => <b key={t} style={{ left: toPercent(t) }}><s>{karaokeTickLabel(ticks.step, t)}</s></b>)}</div>

            <div className="kara-lane">
              {plan.sentences.map((line, i) => <div
                key={`${line.from}-${i}`}
                className={`kara-block${i === selected ? ' active' : ''}${line.end >= windowEnd - 0.001 ? ' clipped' : ''}`}
                style={{ left: toPercent(line.start), width: `${Math.max(karaokeBlockFloor(zoom), ((line.end - line.start) / span) * 100)}%` }}
                onPointerDown={beginDrag('block', i)}
                title={`Sentence ${i + 1}: ${draftText.slice(tokens[line.from]?.i0 ?? 0, tokens[line.to]?.i1 ?? 0)} · ${formatClockPrecise(line.start)} → ${formatClockPrecise(line.end)} (${round3(line.end - line.start)} s) · drag to move, Shift-click sets the start, Ctrl-click the end`}
              >
                <span>{draftText.slice(tokens[line.from]?.i0 ?? 0, (tokens[line.from]?.i0 ?? 0) + 46)}</span>
                <i className="kara-handle in" onPointerDown={beginDrag('start', i)}/>
                <i className="kara-handle out" onPointerDown={beginDrag('end', i)}/>
              </div>)}
            </div>

            {current ? <div className="kara-wordrow">
              {current.words.map((w, i) => <div key={w.i} className="kara-slot" style={{ left: toPercent(w.t0) }}>
                <b className={position >= w.t1 ? 'sung' : position >= w.t0 ? 'now' : 'next'}>{draftText.slice(tokens[w.i].i0, tokens[w.i].i1)}</b>
                <i
                  className="kara-div"
                  title={`Start of “${tokens[w.i].w}” — drag it, or press I with the playhead where you want it`}
                  onPointerDown={event => {
                    event.stopPropagation()
                    event.preventDefault()
                    dragRef.current = { kind: 'word', index: selected, t0: 0, snapshot: data.words.map(x => ({ ...x })), wordIndex: i }
                    ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
                  }}
                />
              </div>)}
              <div className="kara-slot end" style={{ left: toPercent(current.words[current.words.length - 1]?.t1 ?? 0) }}>
                <i className="kara-div last" title="End of the sentence — drag it, or press O" onPointerDown={beginDrag('end', selected)}/>
              </div>
            </div> : null}

            <div className="kara-playhead" style={{ left: toPercent(position) }}/>
          </div>
          </div>

          {/* Deliberately outside `.kara-strip-scroll`: the bar's axis is one
              sentence, so the clip's zoom would only slide part of it out of
              view. It is the width of the preview, and that is the point. */}
          {wordBar && current ? <div className="kara-wordbar">
            <div className="kara-wordbar-head">
              <b>Sentence {plan.sentences.indexOf(current) + 1} of {plan.sentences.length}</b>
              <span className="kara-wordbar-text">{draftText.slice(tokens[current.from]?.i0 ?? 0, tokens[current.to]?.i1 ?? 0)}</span>
              <span>{formatClockPrecise(wordBar.from)} → {formatClockPrecise(wordBar.to)} · {round3(wordBar.dur)} s · {wordBar.words.length} words</span>
              <small>drag a divider to re-time the word after it · <kbd>⇧</kbd> drag takes the rest of the line with it · click the bar to seek</small>
            </div>
            <div className="kara-wordbar-track" ref={barRef}
                 onPointerDown={event => seek(barTime(event.clientX))}
                 onPointerMove={moveBar} onPointerUp={endBar} onPointerCancel={endBar}>
              {wordBar.words.map(w => <div key={`w-${w.i}`}
                className={`kara-wordbar-word${position >= w.t1 ? ' sung' : position >= w.t0 ? ' now' : ' next'}`}
                style={{ left: `${w.left}%`, width: `${Math.max(1.2, w.width)}%` }}
                title={`“${draftText.slice(tokens[w.i].i0, tokens[w.i].i1)}” — ${formatClockPrecise(w.t0)} → ${formatClockPrecise(w.t1)} (${round3(w.t1 - w.t0)} s) · click to put the playhead on it`}
                onPointerDown={event => { event.stopPropagation(); seek(w.t0) }}>
                <b>{draftText.slice(tokens[w.i].i0, tokens[w.i].i1)}</b>
                <i>{round3(w.t1 - w.t0)}</i>
              </div>)}
              {wordBar.words.map((w, k) => <i key={`cut-${w.i}`} className="kara-wordbar-cut" style={{ left: `${w.left}%` }}
                title={`Start of “${draftText.slice(tokens[w.i].i0, tokens[w.i].i1)}” at ${formatClockPrecise(w.t0)} — drag it, or Shift+← / Shift+→ while this word is being sung`}
                onPointerDown={beginBarCut(k)}/>)}
              <div className="kara-wordbar-playhead" style={{ left: `${karaokeWordBarFraction(wordBar, position)}%` }}/>
            </div>
          </div> : null}

          <div className="strip-legend">
            <span>{plan.sentences.length} sentences · {tokens.length} words{perSecond ? ` · ${perSecond.toFixed(2)} words/s` : ''}</span>
            <span className="kara-keys">
              <kbd>Space</kbd> tap <kbd>Enter</kbd> next <kbd>⇧←/⇧→</kbd> ±100 ms <kbd>⌥←/⌥→</kbd> ±1 frame <kbd>I</kbd>/<kbd>O</kbd> line start/end <kbd>S</kbd> split <kbd>F</kbd> speed <kbd>1-4</kbd> surface <kbd>⌘</kbd>+scroll <kbd>+/-/0</kbd> zoom
            </span>
          </div>
        </section>

        <aside className="kara-side">
          <div className="kara-field">
            <span className="field-label">The words</span>
            <textarea value={draftText} rows={5} placeholder="Type or paste the long text. Sentences are found on their own; this editor only decides when each one lands." onChange={event => setDraftText(event.target.value)} onBlur={commitText} onKeyDown={event => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                ;(event.target as HTMLTextAreaElement).blur()
              }
            }}/>
            {draftText !== (item.text || '') ? <small>Commit with Tab or a click outside — the timing is anchored to word numbers, so it rides along with the edit.</small> : null}
          </div>

          <div className="kara-field">
            <span className="field-label">Style</span>
            <div className="kara-styles">{KARAOKE_STYLES.map(style => <button key={style.id} type="button" className={data.style === style.id ? 'active' : ''} title={style.hint} onClick={() => apply({ ...data, style: style.id as KaraokeStyle })}>{style.label}</button>)}</div>
            <label className="kara-fill">
              <input type="color" value={data.fill} onChange={event => apply({ ...data, fill: event.target.value })}/>
              <span>Sung colour — the not-yet-sung side is the caption’s colour, unless you set one of your own in Text style below.</span>
            </label>
          </div>

          <div className="kara-field kara-look">
            <span className="field-label">
              Text style
              <button type="button" className="kara-look-reset" disabled={!touched} onClick={() => { const merged: KaraokeData = { ...data }; delete merged.text; apply(merged) }} title={touched ? 'Forget these and wear the caption’s own style again' : 'Nothing here yet — the words already wear the caption’s style'}>
                <RotateCcw size={12}/>{touched ? 'Caption’s style' : 'From the caption'}
              </button>
            </span>
            <div className="kara-look-font">
              <FontPicker value={look.family} onChange={family => patchLook('family', family)} sample={draftText || item.text || ''}/>
              {over.family !== undefined ? <button type="button" className="kara-unset" onClick={() => patchLook('family', undefined)} title={`Use the caption’s font instead (${stageStyle.family})`}><X size={11}/></button> : null}
            </div>
            <div className="kara-styles kara-look-flags">
              {KARAOKE_TEXT_FIELDS.filter(field => field.kind === 'flag').map(field => {
                const key = field.id as KaraokeTextFlag
                const on = look[key]
                const pinned = over[key] !== undefined
                const next = pinned ? (over[key] ? 'force off' : 'let the caption decide again') : `force ${look[key] ? 'off' : 'on'}`
                return <button key={key} type="button" className={`${on ? 'active' : ''}${pinned ? ' pinned' : ''}`} onClick={() => flagCycle(key)} title={`${field.hint} — ${flagState(key)}. Click to ${next}.`}>{field.label}</button>
              })}
            </div>
            <div className="kara-grid">
              {lookNumbers.map(field => {
                const current = field.id === 'size' ? look.fontSize : look[field.id]
                // What it would go back to — the caption's own number, not the
                // one on screen, which is the point of the little ×.
                const inherited = field.id === 'size' ? stageStyle.fontSize : stageStyle[field.id]
                const pinned = over[field.id] !== undefined
                return <label key={field.id} className={pinned ? 'set' : undefined} title={field.hint}>
                  <span>{field.label}</span>
                  <input type="number" min={field.min} max={field.max} step={field.step} value={current} onChange={event => {
                    const raw = Number(event.target.value)
                    if (!Number.isFinite(raw)) return
                    patchLook(field.id, Math.max(field.min ?? 0, Math.min(field.max ?? 100, raw)))
                  }}/>
                  {pinned ? <button type="button" className="kara-unset" onClick={() => patchLook(field.id, undefined)} title={`Back to the caption’s ${inherited}`}><X size={11}/></button> : null}
                </label>
              })}
              <label className={over.colour !== undefined ? 'set' : undefined} title={textField('colour')?.hint}>
                <span>Unsung</span>
                <input type="color" value={look.colour} onChange={event => patchLook('colour', event.target.value)}/>
                {over.colour !== undefined ? <button type="button" className="kara-unset" onClick={() => patchLook('colour', undefined)} title={`Back to the caption’s ${stageStyle.colour}`}><X size={11}/></button> : null}
              </label>
            </div>
            <small>Anything you touch here applies to the sung words only — the caption keeps its own look for every other frame. Size, X and Y mean the same as in the caption editor (px on a 1920-wide frame, percent of the picture), so nothing you set here can be a preview-only wish.</small>
          </div>

          <div className="kara-field">
            <span className="field-label">First pass</span>
            <div className="kara-actions">
              <button className="btn ghost" onClick={autoPass} title="Spread the words by length, then correct (A)"><Wand2 size={13}/>Distribute</button>
              <button className="btn ghost" onClick={snapPass} title="Pull every time onto the nearest onset in the movie’s audio (B)"><FileMusic size={13}/>Snap to onsets</button>
              <button className="btn ghost" onClick={fitToWindow} title="Stretch the timing to fill the caption window"><AlignLeft size={13}/>Fit to window</button>
              <button className="btn ghost" onClick={spaceEvenly} title="Even distance between the timed words">Space evenly</button>
              <button className="btn ghost" onClick={() => setShowImport(v => !v)} title="Take the times from a file you already have"><Import size={13}/>Import</button>
            </div>
            {showImport ? <div className="kara-import">
              <input type="file" accept=".lrc,.srt,.vtt,.ass,.ssa,text/plain" onChange={event => void onImportFile(event.target.files?.[0])}/>
              <div className="kara-import-tabs">
                {KARAOKE_IMPORT_EXAMPLES.map(ex => <button key={ex.id} type="button" className={importExample === ex.id ? 'active' : ''} title={ex.hint} onClick={() => setImportExample(ex.id)}>{ex.label}</button>)}
              </div>
              {(() => {
                const ex = KARAOKE_IMPORT_EXAMPLES.find(x => x.id === importExample) || KARAOKE_IMPORT_EXAMPLES[0]
                return <>
                  <pre className="kara-import-code"><code>{ex.body}</code>
                    <button type="button" onClick={() => {
                      // `navigator.clipboard` is missing outright over http://, and
                      // `writeText` rejects in a locked-down browser: both are the
                      // same dead end, so say so instead of throwing on undefined.
                      const copied = navigator.clipboard?.writeText(ex.body)
                      if (!copied) {
                        setNotice({ kind: 'warn', text: 'This browser has no clipboard to hand over — select the example and copy it by hand.' })
                        return
                      }
                      void copied.then(
                        () => setNotice({ kind: 'info', text: `${ex.label} example copied — save it as a file, then pick it above.` }),
                        () => setNotice({ kind: 'warn', text: 'The browser refused the clipboard — select the text and copy it by hand.' }))
                    }} title="Copy this example"><Copy size={11}/> copy</button>
                  </pre>
                  <small>{ex.note}</small>
                </>
              })()}
              <small>Times are <strong>media time</strong>, not slide time: the clip's own IN point is subtracted for you. If the file's words are exactly your text, every word gets its own time and the line breaks come from the file; otherwise each cue's first word is found by matching forward and the rest of the cue shares what is left.</small>
            </div> : null}
          </div>

          <div className="kara-field kara-grid">
            <TimeField label="Text from" value={windowStart} min={0} max={span} onCommit={v => onChange({ textStart: v })}/>
            <TimeField label="Text to" value={windowEnd} min={0} max={span} onCommit={v => onChange({ textEnd: v })}/>
            <label><span>Lead-in</span><input type="number" min={0} max={5} step={0.05} value={data.leadIn} onChange={event => apply({ ...data, leadIn: Math.max(0, Math.min(5, Number(event.target.value) || 0)) })}/></label>
            <label><span>Hold tail</span><input type="number" min={0} max={10} step={0.05} value={data.tail} onChange={event => apply({ ...data, tail: Math.max(0, Math.min(10, Number(event.target.value) || 0)) })}/></label>
            <label><span>Words per line</span><input type="number" min={1} max={80} step={1} value={data.maxWords} onChange={event => apply({ ...data, maxWords: Math.max(1, Math.min(80, Math.round(Number(event.target.value) || 12))) })}/></label>
            <label><span>Snap radius</span><input type="number" min={0} max={2000} step={10} value={Math.round(data.snap * 1000)} onChange={event => apply({ ...data, snap: Math.max(0, Math.min(2, (Number(event.target.value) || 0) / 1000)) })}/></label>
          </div>

          <div className="kara-field">
            <span className="field-label">Selected sentence{current ? ` ${selected + 1} of ${plan.sentences.length}` : ''}</span>
            {current ? <p className="kara-quote">“{draftText.slice(tokens[current.from]?.i0 ?? 0, tokens[current.to]?.i1 ?? 0)}”</p>
              : <p className="kara-quote">Nothing timed yet — press Distribute, or play and tap along.</p>}
            <div className="kara-actions">
              <button className="btn ghost" onClick={splitAtPlayhead} title="Cut the sentence at the playhead"><Scissors size={13}/>Split here</button>
              <button className="btn ghost" onClick={dropBreak} title="Remove the break, so the words rejoin their neighbour">Merge</button>
              <button className="btn ghost" onClick={startLineHere} title="Move this sentence so its first word starts at the playhead">Start here</button>
            </div>
          </div>

          {notice ? <p className={`kara-notice ${notice.kind}`}>{notice.text}</p> : null}
          {perSecond > 3.5 ? <p className="kara-notice warn">Above ~3.5 words per second a sweep reads as a strobe — split a sentence or two.</p> : null}
        </aside>
      </div>

      <div className="modal-foot">
        <span>{data.editor === 'tap' ? 'Play and tap: Space on every word or line, Backspace undoes one.' : 'Drag the blocks, or play and tap along. The MP4 does exactly this.'}</span>
        <button className="btn ghost" onClick={cancel}>Cancel</button>
        <button className="btn dark" onClick={close}><Check size={15}/> Save timing</button>
      </div>
    </div>
  </PopupBackdrop>
}
