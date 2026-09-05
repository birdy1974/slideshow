import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowDown, ArrowUp, Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp,
  Clock3, Cpu, Download, Eraser, Eye, EyeOff, Film, FolderOpen, GripVertical, Image as ImageIcon,
  ImageOff, Info, LayoutGrid, List, ListVideo, Music2, Pause, Play, Plus, RefreshCw, RotateCcw, RotateCw, Save,
  Scissors, Settings2, Shuffle, Sparkles, Square, Trash2, Video, X, Zap, ZoomIn, ZoomOut, Type, Move, Palette,
} from 'lucide-react'
import glRegistryData from '../registry/transitions.json'

type MediaRoot = 'photos' | 'videos' | 'music'

// Encode each path segment so spaces, dashes, parentheses and unicode survive
// the query string, while leaving `/` as a real separator (some proxies reject %2F).
function encodeMediaRelative(relative: string) {
  return relative.split('/').map(part => encodeURIComponent(part)).join('/')
}

async function readApiError(response: Response, fallback = 'Request failed') {
  const text = await response.text()
  try {
    const parsed = JSON.parse(text)
    const detail = parsed?.detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail)) {
      const msgs = detail.map((d: {msg?: string}) => d?.msg).filter(Boolean)
      if (msgs.length) return msgs.join('; ')
    }
  } catch { /* keep raw text */ }
  return text || `${fallback} (${response.status})`
}

// Streams a file from a mounted root through the backend (thumbnails, lightbox, MP3).
function mediaRelativePath(root: MediaRoot, serverPath: string) {
  let relative = (serverPath || '').split('\\').join('/')
  const prefix = '/' + root
  if (relative === prefix || relative.startsWith(prefix + '/')) relative = relative.slice(prefix.length)
  return relative.startsWith('/') ? relative.slice(1) : relative
}

function mediaFileUrl(root: MediaRoot, serverPath: string) {
  return `/api/media/file?root=${root}&path=${encodeMediaRelative(mediaRelativePath(root, serverPath))}`
}

async function serverVideoDuration(root: 'photos' | 'videos', serverPath: string) {
  const url = `/api/media/probe?root=${root}&path=${encodeMediaRelative(mediaRelativePath(root, serverPath))}`
  const response = await fetch(url)
  if (!response.ok) return 0
  const data = await response.json()
  return Number.isFinite(data.duration) ? Number(data.duration) : 0
}


// Projects historically stored `path` as the parent folder and `name` as the
// filename. Newer items store the full file path in `path`. Rebuild a file
// path that `/api/media/file` and the renderer can both open.
function mediaItemPath(item: { path: string; name: string }) {
  const path = (item.path || '').split('\\').join('/')
  const name = item.name || ''
  const last = path.split('/').filter(Boolean).pop() || ''
  if (name && last === name) return path
  if (/\.[A-Za-z0-9]{2,5}$/.test(last)) return path
  if (path && name) return `${path.replace(/\/$/, '')}/${name}`
  return path || name
}

function mediaRootFromPath(fullPath: string, fallback: MediaRoot = 'photos'): MediaRoot {
  const p = fullPath.replace(/\\/g, '/')
  if (p === '/videos' || p.startsWith('/videos/')) return 'videos'
  if (p === '/music' || p.startsWith('/music/')) return 'music'
  if (p === '/photos' || p.startsWith('/photos/')) return 'photos'
  return fallback
}

// Combine directory listings of the photos and videos mounts into one view.
// Folders that share a relative path become a single entry (opening it enters
// that folder in both mounts); files keep their full path so each one stays
// addressable inside its own mount. Sorting mirrors the backend: folders
// first, then names case-insensitively.
function mergeBrowsedEntries(flat: any[]) {
  const merged = new Map<string, any>()
  for (const entry of flat) {
    if (entry.kind === 'directory') {
      const key = 'dir:' + entry.relativePath
      const existing = merged.get(key)
      if (!existing || (existing.accessible === false && entry.accessible !== false)) merged.set(key, entry)
    } else {
      merged.set('file:' + entry.path, entry)
    }
  }
  return Array.from(merged.values()).sort((a, b) => {
    const ad = a.kind === 'directory' ? 0 : 1
    const bd = b.kind === 'directory' ? 0 : 1
    if (ad !== bd) return ad - bd
    return String(a.name).toLowerCase().localeCompare(String(b.name).toLowerCase())
  })
}

function itemThumbUrl(item?: MediaItem | null) {
  if (!item || item.type === 'title') return ''
  const full = mediaItemPath(item)
  if (!full) return item.src || ''
  if (full.startsWith('http://') || full.startsWith('https://') || full.startsWith('data:') || full.startsWith('blob:') || full.startsWith('/media/')) {
    return full
  }
  if (item.src && (item.src.startsWith('http://') || item.src.startsWith('https://') || item.src.startsWith('data:') || item.src.startsWith('blob:') || item.src.startsWith('/media/'))) {
    return item.src
  }
  // Stream from the mount the file really lives in: a video stored under
  // /photos must be served from the photos root (and an image under /videos
  // from the videos root). The kind only picks the fallback for legacy
  // paths that carry no mount prefix at all.
  const root = mediaRootFromPath(full, item.type === 'video' ? 'videos' : 'photos')
  return mediaFileUrl(root, full)
}

type LightboxTarget = { title: string; src: string; kind: 'image' | 'video' | 'audio' }

function MediaLightbox({ title, src, kind, onClose, onPrev, onNext, onDelete, position, rotation, onRotate }: LightboxTarget & {
  onClose: () => void;
  // Storyline bindings: when present, the lightbox can walk the storyline
  // (prev/next), show the current position, and delete the shown item.
  onPrev?: () => void; onNext?: () => void; onDelete?: () => void; position?: string;
  // Photo orientation: current quarter-turn rotation and a handler receiving
  // +90 (clockwise) or -90 (counter-clockwise). Only offered for photos.
  rotation?: number; onRotate?: (delta: 90 | -90) => void;
}) {
  const [failed, setFailed] = useState(false)
  useEffect(() => setFailed(false), [src])
  // Keyboard: ← / → walk the storyline, Escape closes. Only wired when the
  // lightbox is bound to storyline items (browser previews pass no handlers).
  useEffect(() => {
    if (!onPrev && !onNext && !onDelete && !onRotate) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') onPrev?.()
      else if (event.key === 'ArrowRight') onNext?.()
      else if (event.key === 'Escape') onClose()
      else if ((event.key === 'r' || event.key === 'R') && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); onRotate?.(event.shiftKey ? -90 : 90) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onPrev, onNext, onDelete, onRotate, onClose])
  const turn = normalizeRotation(rotation)
  const canRotate = kind === 'image' && !!onRotate
  return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}>
    {onPrev && <button type="button" className="lightbox-nav prev" title="Previous media (←)" aria-label="Previous media" onMouseDown={e => e.stopPropagation()} onClick={onPrev}><ChevronLeft size={26}/></button>}
    {onNext && <button type="button" className="lightbox-nav next" title="Next media (→)" aria-label="Next media" onMouseDown={e => e.stopPropagation()} onClick={onNext}><ChevronRight size={26}/></button>}
    <div className="media-lightbox" onMouseDown={e => e.stopPropagation()}>
      <div className="preview-top"><div><strong>{title}</strong><span>{kind === 'video' ? 'VIDEO' : kind === 'audio' ? 'AUDIO' : 'PHOTO'}</span></div><div className="lightbox-actions">{position && <em className="lightbox-position">{position}</em>}{canRotate && <span className="lightbox-rotate"><button type="button" title="Rotate 90° counter-clockwise (Shift+R)" aria-label="Rotate counter-clockwise" onClick={() => onRotate!(-90)}><RotateCcw size={18}/></button><button type="button" title="Rotate 90° clockwise (R)" aria-label="Rotate clockwise" onClick={() => onRotate!(90)}><RotateCw size={18}/></button>{turn ? <b title="Rotation applied in the rendered slideshow">{turn}°</b> : null}</span>}{onDelete && <button type="button" className="lightbox-delete" title="Remove from storyline" aria-label="Remove from storyline" onClick={onDelete}><Trash2 size={18}/></button>}<button type="button" onClick={onClose} aria-label="Close preview"><X size={20}/></button></div></div>
      {failed ? <div className="lightbox-error"><ImageOff size={30}/><strong>This file could not be previewed</strong><span>{kind === 'video' ? 'Your browser may not decode this format (including camera AVI). It can still be imported and rendered by FFmpeg.' : 'It is empty, missing, or unreadable on the mounted volume.'}</span></div>
        : kind === 'video' ? <video className="lightbox-media" src={src} controls autoPlay onError={() => setFailed(true)} />
        : kind === 'audio' ? <audio className="lightbox-audio" src={src} controls autoPlay onError={() => setFailed(true)} />
        : <div className="lightbox-stage"><img className={`lightbox-media lightbox-photo ${turn === 90 || turn === 270 ? 'turned' : ''}`} style={rotationStyle(turn)} src={src} alt={title} onError={() => setFailed(true)} /></div>}
    </div>
  </div>
}

// Renders a media thumbnail (image or video) with a graceful placeholder when
// the backend reports the file unreadable — a 0-byte or missing file would
// otherwise show as a silently broken image in the timeline and filmstrip.
function MediaThumb({ item, className, muted, preload, onClick, onPointerDown, style }: {
  item: MediaItem; className?: string; muted?: boolean; preload?: 'metadata' | 'auto' | 'none';
  onClick?: React.MouseEventHandler; onPointerDown?: React.PointerEventHandler; style?: React.CSSProperties;
}) {
  const [failed, setFailed] = useState(false)
  const src = itemThumbUrl(item)
  useEffect(() => setFailed(false), [src])
  if (!src) return null
  if (failed) return <span className="thumb-fallback"><ImageOff size={14}/><small>unavailable</small></span>
  const common = { src, className, onClick, onPointerDown, onError: () => setFailed(true), style } as const
  if (item.type === 'video') return <video {...common} muted={muted ?? true} preload={preload ?? 'metadata'} />
  return <img {...common} style={rotationStyle(item.rotation, style)} alt={item.name} />
}

// Thumbnail inside the media picker, with a fallback when the file is empty
// or unreadable (the backend answers 422 for 0-byte files, so onError fires).
function BrowserThumb({ root, file }: { root: MediaRoot, file: any }) {
  const [failed, setFailed] = useState(false)
  const src = mediaFileUrl(root, file.path)
  useEffect(() => setFailed(false), [src])
  if (file.kind === 'directory') return <FolderOpen size={34}/>
  if (file.kind === 'audio') return <Music2 size={34}/>
  // AVI (notably Motion JPEG from a Casio EX-Z11) is renderable by FFmpeg but
  // generally not decodable by browser video elements. Avoid a broken preview.
  if (file.kind === 'video' && /\.avi$/i.test(file.name)) return <><Film size={34}/><span className="video-tag"><Video size={10}/> AVI</span></>
  if (failed) return <span className="file-thumb-fallback"><ImageOff size={20}/></span>
  if (file.kind === 'video') return <><video src={src} muted preload="metadata" onError={() => setFailed(true)}/><span className="video-tag"><Video size={10}/> video</span></>
  if (file.kind === 'image') return <img src={src} alt={file.name} onError={() => setFailed(true)}/>
  return <ImageIcon size={34}/>
}

// One shared <audio> element at a time; returns the key currently playing, a
// toggle, live playback progress and a seek function for the time bar.
function useAudioPreview(onError: (message: string) => void) {
  const [playingKey, setPlayingKey] = useState<string | null>(null)
  const [progress, setProgress] = useState({ current: 0, duration: 0 })
  const playerRef = useRef<HTMLAudioElement | null>(null)
  useEffect(() => () => { playerRef.current?.pause(); playerRef.current = null }, [])
  const toggle = (key: string, src: string, label: string) => {
    if (playingKey === key) { playerRef.current?.pause(); playerRef.current = null; setPlayingKey(null); setProgress({ current: 0, duration: 0 }); return }
    playerRef.current?.pause()
    const player = new Audio(src)
    playerRef.current = player
    const stop = () => { if (playerRef.current === player) { playerRef.current = null; setPlayingKey(null); setProgress({ current: 0, duration: 0 }) } }
    const sync = () => { if (playerRef.current === player) setProgress({ current: player.currentTime, duration: Number.isFinite(player.duration) ? player.duration : 0 }) }
    player.onended = stop
    player.onerror = () => { stop(); onError(`Could not play ${label}`) }
    player.ontimeupdate = sync
    player.onloadedmetadata = sync
    player.ondurationchange = sync
    player.onseeked = sync
    setProgress({ current: 0, duration: 0 })
    setPlayingKey(key)
    player.play().catch(() => { stop(); onError(`Could not play ${label}`) })
  }
  // Jump to a position (seconds) in the track that is currently playing.
  const seek = (seconds: number) => {
    const player = playerRef.current
    if (!player || !Number.isFinite(player.duration)) return
    player.currentTime = Math.min(Math.max(0, seconds), Math.max(0, player.duration - 0.05))
    setProgress({ current: player.currentTime, duration: player.duration })
  }
  return { playingKey, toggle, progress, seek }
}

// Waveform-styled seek bar: bars left of the playhead are lit in the track
// colour; click or drag anywhere on it to fast-forward / rewind the preview.
// Pointer capture keeps the drag alive when the cursor leaves the bar.
function AudioSeekBar({ bars = 55, seed = 0, color, current, duration, onSeek, className = '' }: {
  bars?: number; seed?: number; color: string; current: number; duration: number; onSeek: (seconds: number) => void; className?: string;
}) {
  const barRef = useRef<HTMLDivElement | null>(null)
  const [dragging, setDragging] = useState(false)
  const [hoverPct, setHoverPct] = useState<number | null>(null)
  const pctFromEvent = (event: React.PointerEvent) => {
    const rect = barRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return 0
    return Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
  }
  const seekTo = (event: React.PointerEvent) => { if (duration > 0) onSeek(pctFromEvent(event) * duration) }
  const playedPct = duration > 0 ? Math.min(1, current / duration) : 0
  const label = duration > 0 ? `${formatClock(current)} / -${formatClock(duration - current)} / ${formatClock(duration)}` : 'Loading…'
  return <div ref={barRef} className={`audio-seek ${className} ${dragging ? 'dragging' : ''} ${duration > 0 ? '' : 'disabled'}`} role="slider" aria-label="Seek" aria-valuemin={0} aria-valuemax={Math.round(duration)} aria-valuenow={Math.round(current)} aria-valuetext={label} title={label}
    onPointerDown={e => { if (duration <= 0) return; e.preventDefault(); e.stopPropagation(); (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); setDragging(true); seekTo(e) }}
    onPointerMove={e => { setHoverPct(pctFromEvent(e)); if (dragging) seekTo(e) }}
    onPointerUp={e => { if (dragging) { seekTo(e); setDragging(false) } }}
    onPointerCancel={() => setDragging(false)}
    onPointerLeave={() => setHoverPct(null)}
    onClick={e => e.stopPropagation()}>
    {Array.from({ length: bars }).map((_, i) => {
      const played = (i + 0.5) / bars <= playedPct
      return <i key={i} style={{ height: `${8 + ((i * 17 + seed * 7) % 23)}px`, background: color, opacity: played ? 1 : 0.32 }} />
    })}
    {duration > 0 && <span className="audio-playhead" style={{ left: `${playedPct * 100}%` }} />}
    {hoverPct != null && duration > 0 && !dragging && <span className="audio-hover-time" style={{ left: `${hoverPct * 100}%` }}>{formatClock(hoverPct * duration)}</span>}
  </div>
}

function AudioTimeReadout({ current, duration }: { current: number; duration: number }) {
  if (duration <= 0) return <span className="audio-time">…</span>
  return <span className="audio-time"><b>{formatClock(current)}</b> / <em>-{formatClock(duration - current)}</em> / {formatClock(duration)}</span>
}

type MediaItem = {
  id: number; name: string; path: string; src: string; type: 'image' | 'video' | 'title';
  duration: number; effect: string; transition: string; transitionTime: number;
  // Extended transition config for custom ffmpeg (xfade-easing): per-clip GL params, easing and reverse
  transitionParams?: Record<string, string | number>;
  transitionEasing?: string;
  transitionReverse?: number;
  text: string; textMode: 'overlay' | 'frame';
  // Per-slide opt-out: when false the caption is kept but not drawn on the picture.
  textEnabled?: boolean;
  textStart: number; textEnd: number; textEnter: string; textExit: string;
  textEnterDuration: number; textExitDuration: number;
  textX: number; textY: number; frameBackground: string;
  fontFamily?: string; fontSize?: number; fontColor?: string;
  // Videos can replace the soundtrack with their embedded audio.
  audioSource?: 'soundtrack' | 'original';
  textBold?: boolean; textItalic?: boolean; textUnderline?: boolean;
  // Photo orientation fix in whole quarter turns (0, 90, 180, 270, clockwise).
  // Applied in every thumbnail/lightbox and by the FFmpeg renderer.
  rotation?: number;
  // Text frames: optional second background colour reached via an xfade
  // transition that starts `frameTransitionStart` seconds into the frame and
  // lasts `frameTransitionTime` seconds. The caption stays fixed on top.
  frameBackground2?: string; frameTransition?: string; frameTransitionTime?: number; frameTransitionStart?: number;
}

const isHex = (v: unknown): v is string => typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v)
// Effective two-colour settings for a text frame (null when single colour).
function frameColourChange(item: MediaItem) {
  if (item.type !== 'title' || !isHex(item.frameBackground2) || item.frameBackground2.toLowerCase() === String(item.frameBackground).toLowerCase()) return null
  const hold = Math.max(0.2, Number(item.duration) || 0)
  const time = Math.min(hold, Math.max(0.2, Number(item.frameTransitionTime) || 1))
  const start = Math.min(Math.max(0, hold - time), Math.max(0, Number(item.frameTransitionStart) || 0))
  return { from: item.frameBackground, to: item.frameBackground2, transition: item.frameTransition || 'Fade', time, start, hold }
}
// CSS approximation of the FFmpeg transition for editor/thumbnail previews.
function quickTransitionClass(name: string) {
  return /left/i.test(name) ? 'from-left' : /right/i.test(name) ? 'from-right' : /up/i.test(name) ? 'from-up' : /down/i.test(name) ? 'from-down' : /circle|radial/i.test(name) ? 'from-circle' : 'fade'
}
// Static gradient chip for thumbnails: A on the left, B on the right.
function frameBackgroundStyle(item: MediaItem): React.CSSProperties {
  const change = frameColourChange(item)
  return change ? { background: `linear-gradient(100deg, ${change.from} 0 46%, ${change.to} 54% 100%)` } : { background: item.frameBackground }
}

type Rotation = 0 | 90 | 180 | 270
function normalizeRotation(value: unknown): Rotation {
  const n = Math.round(Number(value) || 0)
  return ((((n % 360) + 360) % 360) as Rotation)
}
function rotationStyle(rotation: number | undefined, base?: React.CSSProperties): React.CSSProperties | undefined {
  const r = normalizeRotation(rotation)
  if (!r) return base
  // The standalone `rotate` property composes with existing transforms and
  // CSS animations (Ken Burns style slow-zoom) instead of overriding them.
  return { ...base, rotate: `${r}deg` }
}

type AudioTrack = {
  id: number; name: string; path: string; duration: string; color: string;
  // Per-track edit (seconds): keep only [trimStart, trimEnd) of the file and
  // ramp the volume at the kept region's edges. All optional; missing = whole file.
  trimStart?: number; trimEnd?: number; fadeIn?: number; fadeOut?: number;
  // Measured integrated loudness of the kept region (LUFS), from 'Analyse levels'.
  loudness?: number; truePeak?: number;
}

// Real audio contribution of a track: the kept region, not the file length.
function trackSourceSeconds(track: AudioTrack) { return parseClock(track.duration) }
function trackKeptRange(track: AudioTrack): { start: number; end: number } {
  const total = trackSourceSeconds(track)
  const start = Math.max(0, Math.min(Number(track.trimStart) || 0, total || Infinity))
  const rawEnd = Number(track.trimEnd)
  const end = total > 0 ? Math.min(total, rawEnd > 0 ? rawEnd : total) : (rawEnd > 0 ? rawEnd : 0)
  return { start, end: Math.max(start, end) }
}
function trackKeptSeconds(track: AudioTrack) { const r = trackKeptRange(track); return Math.max(0, r.end - r.start) }
function trackIsEdited(track: AudioTrack) {
  const total = trackSourceSeconds(track); const r = trackKeptRange(track)
  return r.start > 0.01 || (total > 0 && r.end < total - 0.01) || (Number(track.fadeIn) || 0) > 0 || (Number(track.fadeOut) || 0) > 0
}
const formatClockPrecise = (seconds: number) => {
  const s = Math.max(0, Number(seconds) || 0)
  const m = Math.floor(s / 60); const rest = s - m * 60
  return `${m}:${rest < 10 ? '0' : ''}${rest.toFixed(1)}`
}

const initialMedia: MediaItem[] = []

// --- Shared timing rules ---------------------------------------------------
// These mirror the backend renderer exactly: every clip runs at least 0.2 s
// and every transition is additional timeline time, rather than time borrowed
// from its neighbouring clips. Keeping the two sides in sync means the
// estimated total shown in the UI always equals the rendered MP4 length,
// even after extreme transition/duration edits.
const MIN_CLIP_SECONDS = 0.2
const MIN_TRANSITION_SECONDS = 0.05
const MIN_TEXT_SECONDS = 0.1
// Default duration of every new transition (and fallback for legacy items
// that were saved before transitionTime existed). Mirrored by the renderer.
const DEFAULT_TRANSITION_SECONDS = 5
const clampNumber = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max))
const safeDuration = (value: number) => Math.max(MIN_CLIP_SECONDS, Number.isFinite(value) ? value : 5)
function timelineModel(items: MediaItem[]) {
  const durations = items.map(item => safeDuration(item.duration))
  const starts: number[] = [0]
  const transitions: number[] = []
  for (let i = 1; i < items.length; i++) {
    const transition = Math.max(MIN_TRANSITION_SECONDS, Number.isFinite(items[i - 1].transitionTime) ? items[i - 1].transitionTime : DEFAULT_TRANSITION_SECONDS)
    transitions.push(transition)
    starts.push(starts[i - 1] + durations[i - 1] + transition)
  }
  const total = items.length ? starts[items.length - 1] + durations[items.length - 1] : 0
  return { durations, starts, transitions, total }
}
const clampLufs = (value: unknown) => { const n = Number(value); return Number.isFinite(n) ? Math.min(-8, Math.max(-24, Math.round(n))) : -14 }
const clampFade = (value: unknown, fallback: number) => { const n = Number(value); return Number.isFinite(n) ? Math.min(30, Math.max(0, Math.round(n * 2) / 2)) : fallback }

const formatClock = (seconds: number) => {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

// Split the storyline into overall-timeline rows. Videos always land on rows
// of their own so a long movie can never squeeze the caption boxes of the
// photos around it. Rows stay consecutive slices of the storyline (runs of
// the same kind, wrapped when a run outgrows the target row size), so reading
// the rows top to bottom is still the exact storyline order, and every row's
// ruler keeps true timestamps from the shared timeline model.
function buildTimelineLines(items: MediaItem[], targetRows: number) {
  const perLine = Math.max(1, Math.ceil(items.length / Math.max(1, targetRows)))
  const lines: { items: MediaItem[]; video: boolean }[] = []
  let current: MediaItem[] = []
  let currentVideo = false
  const flush = () => {
    if (!current.length) return
    lines.push({ items: current, video: currentVideo })
    current = []
  }
  for (const item of items) {
    const video = item.type === 'video'
    // Switching between photos/text frames and videos starts a new row.
    if (current.length && video !== currentVideo) flush()
    currentVideo = video
    current.push(item)
    // Long runs of one kind still wrap like the previous fixed-size rows.
    if (current.length >= perLine) flush()
  }
  flush()
  return lines
}

function parseClock(value: string | number | undefined): number {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.max(0, value)
  const raw = String(value || '').trim()
  if (!raw || raw === 'unknown') return 0
  if (/^\d+(\.\d+)?$/.test(raw)) return Number(raw)
  const parts = raw.split(':').map(Number)
  if (parts.some(n => !Number.isFinite(n))) return 0
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
  if (parts.length === 2) return parts[0] * 60 + parts[1]
  return 0
}

function probeMediaDuration(src: string, kind: 'audio' | 'video' = 'audio'): Promise<number> {
  return new Promise(resolve => {
    const el = document.createElement(kind)
    el.preload = 'metadata'
    const done = (value: number) => { el.removeAttribute('src'); el.load(); resolve(value) }
    el.onloadedmetadata = () => done(Number.isFinite(el.duration) && el.duration > 0 ? el.duration : 0)
    el.onerror = () => done(0)
    window.setTimeout(() => done(0), 8000)
    el.src = src
  })
}

// Bundled fonts (public/fonts, OFL/Apache licensed). Keep in sync with
// FONT_FILES in backend/app/renderer.py so the render matches the preview.
const FONT_GROUPS: Record<string, string[]> = {
  "Sans-serif": [
    "Montserrat",
    "Open Sans",
    "Roboto",
    "Lato",
    "Poppins",
    "Raleway",
    "Nunito",
    "Source Sans 3",
    "Oswald",
    "DejaVu Sans"
  ],
  "Serif": [
    "Playfair Display",
    "Merriweather",
    "Lora",
    "Cormorant Garamond"
  ],
  "Display & script": [
    "Bebas Neue",
    "Anton",
    "Pacifico",
    "Dancing Script",
    "Caveat",
    "Great Vibes"
  ]
}
const FONT_FAMILIES = Object.values(FONT_GROUPS).flat()
// Families with no italic cut: the browser would synthesise a slant that FFmpeg
// cannot, so the italic toggle is disabled for them.
const FONTS_WITHOUT_ITALIC = new Set(["Oswald", "Bebas Neue", "Anton", "Pacifico", "Dancing Script", "Caveat", "Great Vibes"])
const FONT_SAMPLE = 'The quick brown fox · Zomer 2026 · 0123456789'

function parsePresetNumber(label: string, fallback: number) {
  const match = String(label || '').match(/([\d.]+)/)
  return match ? Number(match[1]) : fallback
}

function formatFileSize(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 MB'
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  const mb = bytes / (1024 * 1024)
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
  const gb = mb / 1024
  return `${gb < 10 ? gb.toFixed(1) : Math.round(gb)} GB`
}

function estimateOutputBytes(durationSeconds: number, bitrateLabel: string, hasAudio: boolean) {
  const videoMbps = parsePresetNumber(bitrateLabel, 8)
  const audioMbps = hasAudio ? 0.192 : 0
  return ((videoMbps + audioMbps) * 1_000_000 / 8) * Math.max(0, durationSeconds) * 1.02
}

function dragOnStage(event: React.PointerEvent<HTMLElement>, onMove: (x: number, y: number) => void) {
  event.preventDefault()
  const stage = event.currentTarget.parentElement
  if (!stage) return
  const rect = stage.getBoundingClientRect()
  const move = (e: PointerEvent) => onMove(
    Math.max(5, Math.min(95, (e.clientX - rect.left) / rect.width * 100)),
    Math.max(8, Math.min(92, (e.clientY - rect.top) / rect.height * 100)),
  )
  const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop) }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
}

// 1) Native xfade catalogue (FFmpeg built-in) — 58 transitions, unchanged from ayosec/FFmpeg docs
const nativeTransitionGroups: Record<string, string[]> = {
  'Fades & blends': ['Fade', 'Fade black', 'Fade white', 'Fade grays', 'Fade fast', 'Fade slow', 'Dissolve', 'Distance', 'Pixelize', 'H blur'],
  'Wipes': ['Wipe left', 'Wipe right', 'Wipe up', 'Wipe down', 'Wipe top-left', 'Wipe top-right', 'Wipe bottom-left', 'Wipe bottom-right'],
  'Slides & smooth': ['Slide left', 'Slide right', 'Slide up', 'Slide down', 'Smooth left', 'Smooth right', 'Smooth up', 'Smooth down'],
  'Shapes': ['Circle crop', 'Rectangle crop', 'Circle open', 'Circle close', 'Vertical open', 'Vertical close', 'Horizontal open', 'Horizontal close', 'Radial'],
  'Slices': ['Diagonal top-left', 'Diagonal top-right', 'Diagonal bottom-left', 'Diagonal bottom-right', 'Horizontal left slice', 'Horizontal right slice', 'Vertical up slice', 'Vertical down slice'],
  'Squeeze, wind & zoom': ['Squeeze horizontal', 'Squeeze vertical', 'Zoom in', 'Horizontal left wind', 'Horizontal right wind', 'Vertical up wind', 'Vertical down wind'],
  'Cover & reveal': ['Cover left', 'Cover right', 'Cover up', 'Cover down', 'Reveal left', 'Reveal right', 'Reveal up', 'Reveal down'],
}
// 2) GL Transitions — ported GLSL from https://github.com/scriptituk/xfade-easing#ported-glsl-transitions and https://gl-transitions.com/ (64)
// Custom FFmpeg (xfade-easing) exposes them as gl_* C implementations with easing/reverse support.
// GL transitions (gl-transitions.com catalogue) and their parameters come from the
// shared registry — the exact same JSON the backend reads — so the pickers and
// ffmpeg can never drift. Entry order defines the visual group order; labels are
// what saved projects store.
interface GLParamDef { name: string; default: string; min?: string; max?: string; step?: string; hint?: string }
interface GLEntry { id: string; label: string; group: string; author?: string; params: GLParamDef[] }
const glEntries = (glRegistryData as { gl: GLEntry[] }).gl
const glTransitionGroups: Record<string, string[]> = {}
const glParams: Record<string, GLParamDef[]> = {}
for (const e of glEntries) {
  (glTransitionGroups[e.group] = glTransitionGroups[e.group] || []).push(e.label)
  glParams[e.label] = e.params || []
}
// Keep legacy key for code that still imports transitionGroups (combines both for global search / random)
const transitionGroups: Record<string, string[]> = { ...nativeTransitionGroups, ...glTransitionGroups }
const transitions = Object.values(transitionGroups).flat()
const nativeTransitions = Object.values(nativeTransitionGroups).flat()
const glTransitions = Object.values(glTransitionGroups).flat();

// Easing catalogue for the custom xfade-easing build (native-like + CSS + extra)
const easingGroups: Record<string,string[]> = {
  'Linear': ['linear'],
  'Standard (in/out/in-out)': ['quadratic','quadratic-in','quadratic-out','quadratic-in-out','cubic','cubic-in','cubic-out','cubic-in-out','quartic','quartic-in','quartic-out','quartic-in-out','quintic','quintic-in','quintic-out','quintic-in-out','sinusoidal','sinusoidal-in','sinusoidal-out','sinusoidal-in-out','exponential','exponential-in','exponential-out','exponential-in-out','circular','circular-in','circular-out','circular-in-out'],
  'Elastic / Back / Bounce': ['elastic','elastic-in','elastic-out','elastic-in-out','back','back-in','back-out','back-in-out','bounce','bounce-in','bounce-out','bounce-in-out','squareroot','cuberoot','flipelastic','flipback'],
  'CSS': ['ease','ease-in','ease-out','ease-in-out','cubic-bezier(0.42,0,0.58,1)','cubic-bezier(0.25,0.1,0.25,1)','step-start','step-end'],
};
const easings = Object.values(easingGroups).flat()
const EASING_DEFAULT = 'linear'
const effects = ['None', 'Ken Burns · Zoom in', 'Ken Burns · Zoom out', 'Ken Burns · Pan left', 'Ken Burns · Pan right', 'Original motion']

function TransitionOptions() {
  return <>
    {Object.entries(nativeTransitionGroups).map(([group, options]) => <optgroup label={group} key={group}>{options.map(x => <option key={x}>{x}</option>)}</optgroup>)}
    {Object.entries(glTransitionGroups).map(([group, options]) => <optgroup label={group} key={group}>{options.map(x => <option key={x}>{x}</option>)}</optgroup>)}
  </>
}
function NativeTransitionOptions() {
  return <>{Object.entries(nativeTransitionGroups).map(([group, options]) => <optgroup label={group} key={group}>{options.map(x => <option key={x}>{x}</option>)}</optgroup>)}</>
}
function GLTransitionOptions() {
  return <>{Object.entries(glTransitionGroups).map(([group, options]) => <optgroup label={group} key={group}>{options.map(x => <option key={x}>{x}</option>)}</optgroup>)}</>
}
function CombinedTransitionOptions() {
  return <TransitionOptions/>
}

function transitionSymbol(name: string) {
  const n = name.toLowerCase()
  if (n.startsWith('gl')) return '✦'
  if (n.includes('left')) return '←'
  if (n.includes('right')) return '→'
  if (n.includes('up')) return '↑'
  if (n.includes('down')) return '↓'
  if (n.includes('circle') || n.includes('radial')) return '◉'
  if (n.includes('zoom')) return '⊕'
  if (n.includes('dissolve') || n.includes('pixel')) return '░'
  if (n.includes('fade')) return '◐'
  return '◇'
}
function isGLTransition(name: string) {
  return name.startsWith('GL ·') || name.startsWith('gl_')
}

// Which catalogue a "Random" action is allowed to draw from.
// 'xfade' = native FFmpeg xfade only, 'gl' = ported GL transitions only,
// 'both' = the whole combined catalogue.
type RandomScope = 'xfade' | 'gl' | 'both'
const randomScopeLabels: Record<RandomScope, string> = {
  xfade: `Random xfade (${nativeTransitions.length})`,
  gl: `Random GL (${glTransitions.length})`,
  both: `Random both (${transitions.length})`,
}
function randomPoolFor(scope: RandomScope): string[] {
  return scope === 'xfade' ? nativeTransitions : scope === 'gl' ? glTransitions : transitions
}
function pickRandomTransition(scope: RandomScope): string {
  const pool = randomPoolFor(scope)
  return pool[Math.floor(Math.random() * pool.length)]
}
function RandomScopeSelect({ value, onChange }: { value: RandomScope; onChange: (v: RandomScope) => void }) {
  return <Select ariaLabel="Random transition source" value={value} onChange={v => onChange(v as RandomScope)}>
    {(Object.keys(randomScopeLabels) as RandomScope[]).map(k => <option key={k} value={k}>{randomScopeLabels[k]}</option>)}
  </Select>
}
function getGLParams(name: string): GLParamDef[] {
  return glParams[name] || []
}

function GLParamControls({ transition, params, onChange }: { transition: string; params: Record<string,string|number>; onChange: (next: Record<string,string|number>)=>void }) {
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
        {isColor ? <div className="color-control compact"><input type="color" value={String(value).startsWith('#')?String(value):'#30382a'} onChange={e=>{ const next={...params, [def.name]: e.target.value }; onChange(next)}}/><input type="text" value={String(value)} onChange={e=>{ const next={...params, [def.name]: e.target.value }; onChange(next)}} placeholder={def.default}/></div>
        : isNumeric ? <div className="gl-slider"><input type="range" min={min} max={max} step={step} value={Number(value) || 0} onChange={e=>{ const next={...params, [def.name]: e.target.value }; onChange(next)}}/><input type="text" value={String(value)} onChange={e=>{ const next={...params, [def.name]: e.target.value }; onChange(next)}} placeholder={def.default}/></div>
        : <input type="text" value={String(value)} onChange={e=>{ const next={...params, [def.name]: e.target.value }; onChange(next)}} placeholder={def.default}/>}
      </label>
    })}
  </div>
}

function EasingSelect({ value, onChange }: { value: string; onChange: (v:string)=>void }) {
  const v = value && value.trim() ? value : EASING_DEFAULT
  return <Select value={v} onChange={onChange}>{Object.entries(easingGroups).map(([g, opts])=> <optgroup label={g} key={g}>{opts.map(o=> <option key={o} value={o}>{o}</option>)}</optgroup>)}</Select>
}

function TransitionPicker({ value, params, easing, reverse, onChange }: { value: string; params?: Record<string,string|number>; easing?: string; reverse?: number; onChange: (next:{transition?:string, transitionParams?:Record<string,string|number>, transitionEasing?:string, transitionReverse?:number})=>void }) {
  const isGL = isGLTransition(value)
  return <div className="transition-picker">
    <div className="picker-tabs"><button className={!isGL?'active':''} onClick={()=>onChange({transition: nativeTransitions[0]})}>XFade</button><button className={isGL?'active':''} onClick={()=>onChange({transition: glTransitions[0]})}>GL Transitions</button></div>
    {!isGL ? <Select value={value} onChange={v=>onChange({transition:v})}><NativeTransitionOptions/></Select>
    : <Select value={value} onChange={v=>onChange({transition:v})}><GLTransitionOptions/></Select>}
    {isGL && <GLParamControls transition={value} params={params||{}} onChange={next=>onChange({transitionParams: next})}/>}
    <div className="transition-meta">
      <label>Easing <EasingSelect value={easing||EASING_DEFAULT} onChange={v=>onChange({transitionEasing:v})}/></label>
      <label className="check-label"><input type="checkbox" checked={Boolean(reverse)} onChange={e=>onChange({transitionReverse: e.target.checked?1:0})}/><span><Check size={11}/></span> Reverse</label>
    </div>
  </div>
}

function TransitionCell({ item, onPatch }: { item: MediaItem; onPatch: (patch: Partial<MediaItem>)=>void }) {
  const [open, setOpen] = useState(false)
  const isGL = isGLTransition(item.transition)
  const params = (item.transitionParams as Record<string,string|number>) || {}
  const easing = item.transitionEasing || EASING_DEFAULT
  const reverse = item.transitionReverse || 0
  // ensure transitionTime clamped
  const max = 3600
  return <div className="transition-cell">
    <Select ariaLabel={`${item.name} transition`} value={item.transition} onChange={v => {
      // when switching type, clear params if moving to native, keep but reset to defaults if to GL?
      if (isGLTransition(v)) {
        const defs = getGLParams(v)
        const nextParams: Record<string,string> = {}
        // keep existing keys that overlap, else default
        for (const d of defs) nextParams[d.name] = String(params[d.name] ?? d.default)
        onPatch({ transition: v, transitionParams: nextParams, transitionEasing: easing, transitionReverse: reverse })
      } else {
        onPatch({ transition: v })
      }
    }}><TransitionOptions/></Select>
    <NumberStepper value={item.transitionTime ?? DEFAULT_TRANSITION_SECONDS} min={MIN_TRANSITION_SECONDS} max={max} step={0.1} suffix="s" ariaLabel={`${item.name} transition time`} onChange={v => onPatch({ transitionTime: v })} />
    {(isGL || easing !== EASING_DEFAULT || reverse) && <button type="button" className={`icon-button small ${open?'active':''}`} title={isGL ? 'Edit GL parameters, easing and reverse' : 'Edit easing and reverse'} onClick={()=>setOpen(o=>!o)}><Settings2 size={13}/></button>}
    {open && <div className="transition-popover">
      {isGL && <><FieldLabel>GL parameters <small>{item.transition}</small></FieldLabel><GLParamControls transition={item.transition} params={params} onChange={next=>onPatch({transitionParams: next})}/></>}
      <div className="transition-meta">
        <label>Easing <EasingSelect value={easing} onChange={v=>onPatch({transitionEasing: v})}/></label>
        <label className="check-label"><input type="checkbox" checked={Boolean(reverse)} onChange={e=>onPatch({transitionReverse: e.target.checked?1:0})}/><span><Check size={11}/></span> Reverse</label>
      </div>
      <button className="btn ghost small" onClick={()=>setOpen(false)}><X size={12}/> Close</button>
    </div>}
  </div>
}

function TimelineRuler({ start, duration, zoom, audioLength }: { start:number, duration:number, zoom:number, audioLength?: string }) {
  return <div className="line-time-ruler">{[0,.25,.5,.75,1].map(f=><span key={f} style={{left:`${f*100}%`}}><i/>{Math.round(start+duration*f)}s</span>)}<b>{Math.floor((start+duration)/60)}:{String(Math.round((start+duration)%60)).padStart(2,'0')}</b><em>{Math.round(zoom*100)}%</em>{audioLength && <span className="audio-length-indicator"><Music2 size={10}/> {audioLength}</span>}</div>
}

function TimelineTextBox({ item, update, selected, onSelect }: { item: MediaItem, update: (change: Partial<MediaItem>) => void, selected: string[], onSelect: (edge:'enter'|'exit')=>void }) {
  // Caption timing is always kept inside the clip, even for projects saved
  // before the current time rules existed.
  const duration = safeDuration(item.duration)
  const textEnd = clampNumber(Number.isFinite(item.textEnd) ? item.textEnd : duration, MIN_TEXT_SECONDS, duration)
  const textStart = clampNumber(item.textStart, 0, Math.max(0, textEnd - MIN_TEXT_SECONDS))
  const changeTiming = (edge: 'start'|'end', event: React.PointerEvent) => {
    event.preventDefault(); event.stopPropagation()
    const lane = event.currentTarget.parentElement?.parentElement
    if (!lane) return
    const rect = lane.getBoundingClientRect()
    const move = (e: PointerEvent) => {
      const seconds = Math.max(0, Math.min(duration, ((e.clientX - rect.left) / rect.width) * duration))
      if (edge === 'start') update({ textStart: Math.min(seconds, textEnd - MIN_TEXT_SECONDS) })
      else update({ textEnd: Math.max(seconds, textStart + MIN_TEXT_SECONDS) })
    }
    const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop)
  }
  const left = textStart / duration * 100
  const width = Math.max(3, (textEnd - textStart) / duration * 100)
  const hidden = item.type !== 'title' && item.textEnabled === false && item.text.trim() !== ''
  return <div className={`timed-text ${hidden ? 'off' : ''}`} style={{left:`${left}%`,width:`${width}%`}}>
    <button className={`text-transition enter ${selected.includes(`${item.id}-enter`)?'selected':''}`} title={`Appear: ${item.textEnter} · ${item.textEnterDuration}s`} onClick={()=>onSelect('enter')}>{transitionSymbol(item.textEnter)}</button>
    <i className="timing-handle left" title={`Appears at ${textStart.toFixed(1)}s`} onPointerDown={e=>changeTiming('start',e)}/>
    <input value={item.text} placeholder="+ Add text" onChange={e=>update({text:e.target.value})}/>
    <i className="timing-handle right" title={`Disappears at ${textEnd.toFixed(1)}s`} onPointerDown={e=>changeTiming('end',e)}/>
    <button className={`text-transition exit ${selected.includes(`${item.id}-exit`)?'selected':''}`} title={`Disappear: ${item.textExit} · ${item.textExitDuration}s`} onClick={()=>onSelect('exit')}>{transitionSymbol(item.textExit)}</button>
  </div>
}

function FieldLabel({ children, hint }: { children: React.ReactNode, hint?: string }) {
  return <label className="field-label">{children}{hint && <span>{hint}</span>}</label>
}

function Select({ value, onChange, children, ariaLabel }: { value: string, onChange?: (v: string) => void, children: React.ReactNode, ariaLabel?: string }) {
  return <div className="select-wrap"><select aria-label={ariaLabel} value={value} onChange={e => onChange?.(e.target.value)}>{children}</select><ChevronDown size={14} /></div>
}

// Rounds a number to at most 3 decimals for display, returning '' for NaN.
const round3 = (n: number) => (Number.isFinite(n) ? String(Math.round(n * 1000) / 1000) : '')

// A numeric input with − / + stepper buttons. It keeps a local text buffer so
// typing a value never fights the controlled prop (the old `type="number"`
// inputs reverted mid-keystroke, making it impossible to type decimals), and
// commits/clamps only on blur, Enter, or the arrow keys/buttons.
function NumberStepper({ value, onChange, min, max, step = 0.1, suffix = '', ariaLabel }: {
  value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; suffix?: string; ariaLabel?: string;
}) {
  const [text, setText] = useState(() => round3(value))
  const [focused, setFocused] = useState(false)
  const valueRef = useRef(value)
  valueRef.current = value
  const clamp = (n: number) => { let v = n; if (min !== undefined) v = Math.max(min, v); if (max !== undefined) v = Math.min(max, v); return v }
  useEffect(() => { if (!focused) setText(round3(value)) }, [value, focused])
  const commit = () => {
    setFocused(false)
    const n = Number(text)
    if (text.trim() !== '' && Number.isFinite(n)) {
      const next = clamp(n)
      if (next !== valueRef.current) onChange(next)
      setText(round3(next))
    } else {
      setText(round3(valueRef.current))
    }
  }
  const nudge = (dir: number) => {
    const parsed = Number(text)
    const base = text.trim() !== '' && Number.isFinite(parsed) ? parsed : (Number.isFinite(valueRef.current) ? valueRef.current : (min ?? 0))
    const next = clamp(Math.round((base + dir * step) * 1000) / 1000)
    onChange(next)
    setText(round3(next))
  }
  return <div className="number-stepper">
    <button type="button" className="step-btn" tabIndex={-1} onMouseDown={e => e.preventDefault()} onClick={() => nudge(-1)} aria-label={`Decrease ${ariaLabel || 'value'}`}>−</button>
    <input aria-label={ariaLabel} type="text" inputMode="decimal" value={text} onFocus={() => setFocused(true)} onBlur={commit} onChange={e => setText(e.target.value)}
      onKeyDown={e => { if (e.key === 'Enter') { commit(); (e.target as HTMLInputElement).blur() } else if (e.key === 'ArrowUp') { e.preventDefault(); nudge(1) } else if (e.key === 'ArrowDown') { e.preventDefault(); nudge(-1) } }} />
    <button type="button" className="step-btn" tabIndex={-1} onMouseDown={e => e.preventDefault()} onClick={() => nudge(1)} aria-label={`Increase ${ariaLabel || 'value'}`}>+</button>
    {suffix && <span className="step-suffix">{suffix}</span>}
  </div>
}

function App() {
  const [media, setMedia] = useState(initialMedia)
  const [projectName, setProjectName] = useState('Portugal summer')
  const [projectId, setProjectId] = useState<number|null>(null)
  const [backendOnline, setBackendOnline] = useState(false)
  const [capabilities, setCapabilities] = useState({ffmpeg:false,quickSync:false,cpuEncoding:false})
  const [previewUrl, setPreviewUrl] = useState<string|null>(null)
  const [activeTab, setActiveTab] = useState<'editor' | 'renders'>('editor')
  const [showBrowser, setShowBrowser] = useState(false)
  const [showAudioBrowser, setShowAudioBrowser] = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  const [isPlaying, setPlaying] = useState(false)
  const [toast, setToast] = useState('')
  const [globalDuration, setGlobalDuration] = useState(DEFAULT_TRANSITION_SECONDS)
  const [audioPolicy, setAudioPolicy] = useState('Loop & trim')
  const [audioVolume, setAudioVolume] = useState(78)
  const [audioFade, setAudioFade] = useState(true)
  // Soundtrack fade-out at the end of the last photo: how long the fade takes
  // and how much silence is left before the final frame.
  const [audioFadeDuration, setAudioFadeDuration] = useState(2)
  const [editingTrackId, setEditingTrackId] = useState<number | null>(null)
  const [audioFadeTail, setAudioFadeTail] = useState(0)
  // Loudness normalisation (EBU R128): per-track matching + final mix pass.
  const [audioNormalize, setAudioNormalize] = useState(true)
  const [audioNormalizeTarget, setAudioNormalizeTarget] = useState(-14)
  const [analysingLevels, setAnalysingLevels] = useState(false)
  const [resolution, setResolution] = useState('Full HD · 1080p')
  const [frameRate, setFrameRate] = useState('30 fps')
  const [bitrate, setBitrate] = useState('8 Mbps · High')
  const [encoder, setEncoder] = useState('Auto · Quick Sync')
  const [outputPath, setOutputPath] = useState('/output')
  const [outputFilename, setOutputFilename] = useState('Portugal-summer')
  const [rendering, setRendering] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [randomOrder, setRandomOrder] = useState(false)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [selectedTransitions, setSelectedTransitions] = useState<number[]>([])
  const [transitionPreviewId, setTransitionPreviewId] = useState<number | null>(null)
  const [selectedTextTransitions, setSelectedTextTransitions] = useState<string[]>([])
  const [detailTextEditor, setDetailTextEditor] = useState<{id:number,edge:'enter'|'exit'}|null>(null)
  const [bulkEffect, setBulkEffect] = useState('Ken Burns · Zoom in')
  const [bulkTransition, setBulkTransition] = useState('Dissolve')
  const [randomScope, setRandomScope] = useState<RandomScope>('both')
  const [timelineZoom, setTimelineZoom] = useState(1)
  const [timelineRows, setTimelineRows] = useState('auto')
  const [compactMediaView, setCompactMediaView] = useState(false)
  const [compactZoom, setCompactZoom] = useState(1)
  const [draggedId, setDraggedId] = useState<number | null>(null)
  const lastCompactSelect = useRef<number | null>(null)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showClearAllConfirm, setShowClearAllConfirm] = useState(false)
  const [showClearOutputConfirm, setShowClearOutputConfirm] = useState(false)
  const [showCleanTempConfirm, setShowCleanTempConfirm] = useState(false)
  const [fontFamily, setFontFamily] = useState('Montserrat')
  const [fontSize, setFontSize] = useState('48')
  const [fontColor, setFontColor] = useState('#ffffff')
  const [textBold, setTextBold] = useState(true)
  const [textItalic, setTextItalic] = useState(false)
  const [textUnderline, setTextUnderline] = useState(false)
  const [defaultTextX, setDefaultTextX] = useState(50)
  const [defaultTextY, setDefaultTextY] = useState(72)
  const [showTextStyles, setShowTextStyles] = useState(false)
  const [editingTextFrame, setEditingTextFrame] = useState<number | null>(null)
  // Id of a text frame created by "Add text frame" that has not been saved
  // yet: Cancel/close removes it again, only Done keeps it in the storyline.
  const [pendingTextFrame, setPendingTextFrame] = useState<number | null>(null)
  const [audioTracks, setAudioTracks] = useState<AudioTrack[]>([])
  const [draggedAudioId, setDraggedAudioId] = useState<number | null>(null)
  const [showFolderPicker, setShowFolderPicker] = useState(false)
  const [showProjectLoader, setShowProjectLoader] = useState(false)
  const [showNewProjectConfirm, setShowNewProjectConfirm] = useState(false)
  const [overwritePath, setOverwritePath] = useState<string | null>(null)
  // Storyline preview lightbox: it tracks the previewed item id (not a frozen
  // URL) so the popup can walk the storyline with prev/next and delete the
  // shown item directly, always reflecting the live media list.
  const [storyPreviewId, setStoryPreviewId] = useState<number | null>(null)
  const openMediaLightbox = (item: MediaItem) => {
    if (item.type === 'title') return
    if (!itemThumbUrl(item)) return
    setStoryPreviewId(item.id)
  }

  // Estimated timeline using the same clamped transition rules the renderer
  // applies, so the on-screen total can never drift or go negative.
  const timeline = useMemo(() => timelineModel(media), [media])
  const total = timeline.total
  const audioFadeTooLong = audioFade && audioTracks.length > 0 && total > 0 && audioFadeDuration + audioFadeTail > total
  const audioTotalSeconds = useMemo(() => audioTracks.reduce((sum, track) => sum + trackKeptSeconds(track), 0), [audioTracks])
  // Measure every soundtrack's loudness server-side so the rows can show
  // which songs are louder/quieter than the others (and than the target).
  const analyseLevels = async () => {
    if (!audioTracks.length || analysingLevels) return
    setAnalysingLevels(true)
    let measured = 0
    try {
      for (const track of audioTracks) {
        const range = trackKeptRange(track)
        const q = `root=music&path=${encodeMediaRelative(mediaRelativePath('music', mediaItemPath(track)))}&start=${range.start}&end=${range.end}`
        try {
          const response = await fetch(`/api/media/loudness?${q}`)
          if (!response.ok) throw new Error(await readApiError(response, 'Loudness analysis failed'))
          const data = await response.json()
          setAudioTracks(items => items.map(x => x.id === track.id ? { ...x, loudness: Number(data.integrated), truePeak: Number(data.truePeak) } : x))
          measured++
        } catch (error) { notify(`${track.name}: ${error instanceof Error ? error.message : 'could not measure'}`) }
      }
      if (measured) notify(`Measured ${measured} track${measured === 1 ? '' : 's'}`)
    } finally { setAnalysingLevels(false) }
  }
  const loudnessSpread = useMemo(() => {
    const values = audioTracks.map(t => t.loudness).filter((v): v is number => Number.isFinite(v))
    return values.length > 1 ? Math.max(...values) - Math.min(...values) : 0
  }, [audioTracks])
  const hasOriginalMovieAudio = useMemo(() => media.some(item => item.type === 'video' && item.audioSource === 'original'), [media])
  // The final program has sound even without a music track when a movie uses
  // its embedded audio; use this for output-size and duration reporting.
  const soundProgramSeconds = audioTracks.length || hasOriginalMovieAudio ? total : 0
  useEffect(() => {
    // Older projects stored duration as "unknown"; fill in real lengths once.
    const missing = audioTracks.filter(track => parseClock(track.duration) <= 0 && track.path)
    if (!missing.length) return
    let cancelled = false
    void (async () => {
      const updates = new Map<number, string>()
      for (const track of missing) {
        const seconds = await probeMediaDuration(mediaFileUrl('music', mediaItemPath(track)), 'audio')
        if (seconds > 0) updates.set(track.id, formatClock(seconds))
      }
      if (cancelled || !updates.size) return
      setAudioTracks(items => items.map(track => updates.has(track.id) ? { ...track, duration: updates.get(track.id)! } : track))
    })()
    return () => { cancelled = true }
  }, [audioTracks])
  const estimatedRows = Math.max(1, Math.ceil(media.length / 6))
  const visibleRows = timelineRows === 'auto' ? estimatedRows : Number(timelineRows)
  // Videos get rows of their own (buildTimelineLines); rows are consecutive
  // slices of the storyline, so order and ruler timestamps stay exact.
  const timelineLines = useMemo(() => buildTimelineLines(media, visibleRows), [media, visibleRows])
  const autoLineCount = useMemo(() => buildTimelineLines(media, estimatedRows).length, [media, estimatedRows])
  // Storyline lightbox navigation walks the media in storyline order,
  // skipping text frames (they have nothing to preview).
  const previewItems = useMemo(() => media.filter(x => x.type !== 'title'), [media])
  const previewIndex = storyPreviewId == null ? -1 : previewItems.findIndex(x => x.id === storyPreviewId)
  const previewedItem = previewIndex >= 0 ? previewItems[previewIndex] : null
  const rotatePreviewedItem = (delta: 90 | -90) => {
    if (!previewedItem || previewedItem.type !== 'image') return
    patch(previewedItem.id, { rotation: normalizeRotation((previewedItem.rotation || 0) + delta) })
  }
  const deletePreviewedItem = () => {
    if (!previewedItem) return
    // After deleting, continue with the item that follows (or the one before
    // when the last item was removed); close the popup when nothing is left.
    const next = previewItems[previewIndex + 1] ?? previewItems[previewIndex - 1]
    setMedia(items => items.filter(x => x.id !== previewedItem.id))
    setSelectedIds(ids => ids.filter(id => id !== previewedItem.id))
    setSelectedTransitions(ids => ids.filter(id => id !== previewedItem.id))
    setStoryPreviewId(next ? next.id : null)
    notify(`Removed ${previewedItem.name} from the storyline`)
  }

  const normalizeTransition = (t:string) => {
    if (!t) return t
    if (t === 'GLSL · Dreamy') return 'GL · Dreamy'
    if (t === 'GLSL · Cube') return 'GL · Cube'
    if (t.startsWith('GLSL')) return t.replace('GLSL','GL')
    return t
  }
  const applySavedProject=(saved:any)=>{
    if(saved.id)setProjectId(saved.id)
    if(saved.project){setProjectName(saved.project.name);setRandomOrder(Boolean(saved.project.randomOrder))}
    if(Array.isArray(saved.media)){
      const normalized = saved.media.map((m:any)=> {
        if (m.transition) m.transition = normalizeTransition(m.transition)
        if (m.frameTransition) m.frameTransition = normalizeTransition(m.frameTransition)
        // ensure transitionParams is object
        if (typeof m.transitionParams === 'string') { try{ m.transitionParams = JSON.parse(m.transitionParams)}catch{ m.transitionParams = {}}}
        if (!m.transitionEasing) m.transitionEasing = EASING_DEFAULT
        if (m.transitionReverse == null) m.transitionReverse = 0
        return m
      })
      setMedia(normalized)
    }
    if(saved.textDefaults){setFontFamily(saved.textDefaults.fontFamily);setFontSize(String(saved.textDefaults.fontSize));setFontColor(saved.textDefaults.fontColor);setTextBold(saved.textDefaults.bold);setTextItalic(saved.textDefaults.italic);setTextUnderline(saved.textDefaults.underline);setDefaultTextX(saved.textDefaults.textX ?? 50);setDefaultTextY(saved.textDefaults.textY ?? 72)}
    if(saved.soundtrack){setAudioTracks(saved.soundtrack.tracks||[]);setAudioPolicy(saved.soundtrack.policy);setAudioVolume(saved.soundtrack.volume);setAudioFade(saved.soundtrack.fadeOut);setAudioFadeDuration(clampFade(saved.soundtrack.fadeDuration,2));setAudioFadeTail(clampFade(saved.soundtrack.fadeTail,0));setAudioNormalize(saved.soundtrack.normalize!==false);setAudioNormalizeTarget(clampLufs(saved.soundtrack.normalizeTarget))}
    if(saved.output){setResolution(saved.output.resolution);setFrameRate(saved.output.frameRate);setBitrate(saved.output.bitrate);setEncoder(saved.output.encoder);setOutputPath(saved.output.path);setOutputFilename(saved.output.filename)}
    if(saved.timeline){setTimelineRows(saved.timeline.rows);setTimelineZoom(saved.timeline.zoom)}
  }
  useEffect(()=>{
    const restore=async()=>{try{
      const health=await fetch('/api/health');if(!health.ok)throw new Error();const healthData=await health.json();setCapabilities(healthData.capabilities);setBackendOnline(true)
      const list=await fetch('/api/projects').then(r=>r.json())
      if(list.length){const saved=await fetch(`/api/projects/${list[0].id}`).then(r=>r.json());applySavedProject(saved);await resumeActiveJob(list[0].id);return}
    }catch{setBackendOnline(false)}
      try{const raw=localStorage.getItem('slideshow.project.mock');if(raw)applySavedProject(JSON.parse(raw))}catch{localStorage.removeItem('slideshow.project.mock')}
    };void restore()
  },[])

  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(''), message.includes('\n') ? 12000 : 3500) }
  const audioPreview = useAudioPreview(message => notify(message))
  const projectSnapshot = () => ({
    schemaVersion: 1, project: { name: projectName, randomOrder }, media,
    textDefaults: { fontFamily, fontSize:Number(fontSize), fontColor, bold:textBold, italic:textItalic, underline:textUnderline, textX: defaultTextX, textY: defaultTextY },
    soundtrack: { tracks:audioTracks, policy:audioPolicy, volume:audioVolume, fadeOut:audioFade, fadeDuration:audioFadeDuration, fadeTail:audioFadeTail, normalize:audioNormalize, normalizeTarget:audioNormalizeTarget },
    output: { resolution, frameRate, bitrate, encoder, path:outputPath, filename:outputFilename },
    timeline: { rows:timelineRows, zoom:timelineZoom },
  })
  const persistSnapshot = async (snapshot:any, silent=false, createNew=false):Promise<number> => {
    localStorage.setItem('slideshow.project.mock',JSON.stringify(snapshot))
    // createNew forces a fresh project row (used by "New project", whose
    // state reset has not been applied to this closure yet).
    const id=createNew?null:projectId
    const send=(method:string,url:string)=>fetch(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(snapshot)})
    let response=await (id?send('PUT',`/api/projects/${id}`):send('POST','/api/projects'))
    if(response.status===404&&id){
      // The saved row vanished (e.g. "Clear all" or a wipe from another
      // client). Recreate it instead of failing every save/preview/render.
      response=await send('POST','/api/projects')
    }
    if(!response.ok){
      let detail=await response.text()
      try{const parsed=JSON.parse(detail);if(parsed?.detail)detail=String(parsed.detail)}catch{/* not JSON; keep raw text */}
      throw new Error(detail||`Save failed (${response.status})`)
    }
    const saved=await response.json();setProjectId(saved.id);setBackendOnline(true)
    if(!silent)notify(`Project saved to SQLite · revision ${saved.revision}`)
    return saved.id
  }
  const persistProject = async (silent=false):Promise<number> => persistSnapshot(projectSnapshot(), silent)
  const blankProjectSnapshot = () => ({
    schemaVersion: 1, project: { name: 'Untitled', randomOrder: false }, media: [],
    textDefaults: { fontFamily: 'Montserrat', fontSize: 48, fontColor: '#ffffff', bold: true, italic: false, underline: false, textX: 50, textY: 72 },
    soundtrack: { tracks: [], policy: 'Loop & trim', volume: 78, fadeOut: true, fadeDuration: 2, fadeTail: 0, normalize: true, normalizeTarget: -14 },
    output: { resolution: 'Full HD · 1080p', frameRate: '30 fps', bitrate: '8 Mbps · High', encoder: 'Auto · Quick Sync', path: '/output', filename: 'slideshow' },
    timeline: { rows: 'auto', zoom: 1 },
  })
  // Wipe the editor back to a completely blank project. The blank project is
  // persisted right away so a refresh does not resurrect the previous one.
  const startNewProject = () => {
    setProjectId(null)
    setProjectName('Untitled')
    setMedia([])
    setRandomOrder(false)
    setAudioTracks([])
    setAudioPolicy('Loop & trim'); setAudioVolume(78); setAudioFade(true); setAudioFadeDuration(2); setAudioFadeTail(0); setAudioNormalize(true); setAudioNormalizeTarget(-14)
    setResolution('Full HD · 1080p'); setFrameRate('30 fps'); setBitrate('8 Mbps · High'); setEncoder('Auto · Quick Sync')
    setOutputPath('/output'); setOutputFilename('slideshow')
    setFontFamily('Montserrat'); setFontSize('48'); setFontColor('#ffffff'); setTextBold(true); setTextItalic(false); setTextUnderline(false); setDefaultTextX(50); setDefaultTextY(72)
    setTimelineRows('auto'); setTimelineZoom(1)
    setSelectedIds([]); setSelectedTransitions([]); setSelectedTextTransitions([])
    setDetailTextEditor(null); setEditingTextFrame(null)
    setShowNewProjectConfirm(false); setShowProjectLoader(false)
    void persistSnapshot(blankProjectSnapshot(), true, true).then(() => notify('Started a new blank project')).catch(() => notify('Started a new blank project — save it once the backend is back'))
  }
  const requestNewProject = () => {
    setShowProjectLoader(false)
    if (media.length || audioTracks.length) setShowNewProjectConfirm(true)
    else startNewProject()
  }
  const saveProject = async () => {
    try{await persistProject()}catch(error){setBackendOnline(false);notify(`SQLite save failed: ${error instanceof Error?error.message:'Unknown error'}`)}
  }
  const loadProject = async (id:number) => {
    try{
      const response=await fetch(`/api/projects/${id}`)
      if(!response.ok)throw new Error(await response.text()||`Load failed (${response.status})`)
      const saved=await response.json()
      applySavedProject(saved)
      setShowProjectLoader(false)
      setBackendOnline(true)
      notify(`Project “${saved.project?.name||`#${id}`}” loaded · revision ${saved.revision}`)
    }catch(error){notify(`Load failed: ${error instanceof Error?error.message:'Unknown error'}`)}
  }
  const patch = (id: number, update: Partial<MediaItem>) => setMedia(items => items.map(item => item.id === id ? { ...item, ...update } : item))
  // Transition time is extra timeline time, so it is not limited by either
  // neighbouring clip.  Keep only a practical upper bound and xfade's minimum.
  const transitionMaxFor = (items: MediaItem[], index: number) => index < 0 || index >= items.length - 1 ? MIN_TRANSITION_SECONDS : 3600
  const clampTransitionFor = (items: MediaItem[], index: number, value: number) => {
    if (index < 0 || index >= items.length - 1) return value
    return clampNumber(value, MIN_TRANSITION_SECONDS, transitionMaxFor(items, index))
  }
  const updateTransition = (id: number, value: number) => setMedia(items => items.map((item, index) => item.id === id && Number.isFinite(value) ? { ...item, transitionTime: clampTransitionFor(items, index, value) } : item))
  // Changing a clip's length also keeps its caption timing inside the clip and
  // re-clamps the transitions on both sides of it.
  const updateDuration = (id: number, value: number) => setMedia(items => {
    const index = items.findIndex(item => item.id === id)
    if (index < 0 || !Number.isFinite(value)) return items
    const next = items.map(item => ({ ...item }))
    const duration = safeDuration(value)
    const item = next[index]
    const textEnd = clampNumber(Number.isFinite(item.textEnd) ? item.textEnd : duration, MIN_TEXT_SECONDS, duration)
    const textStart = clampNumber(item.textStart, 0, Math.max(0, textEnd - MIN_TEXT_SECONDS))
    next[index] = { ...item, duration, textStart, textEnd }
    if (index > 0) next[index - 1] = { ...next[index - 1], transitionTime: clampTransitionFor(next, index - 1, next[index - 1].transitionTime ?? DEFAULT_TRANSITION_SECONDS) }
    if (index < next.length - 1) next[index] = { ...next[index], transitionTime: clampTransitionFor(next, index, next[index].transitionTime ?? DEFAULT_TRANSITION_SECONDS) }
    return next
  })
  const updateSelectedTransitionTimes = (value: number) => setMedia(items => items.map((item, index) => selectedTransitions.includes(item.id) && Number.isFinite(value) ? { ...item, transitionTime: clampTransitionFor(items, index, value) } : item))
  const move = (index: number, direction: -1 | 1) => setMedia(items => {
    const next = [...items]; const target = index + direction
    if (target < 0 || target >= next.length) return next
    ;[next[index], next[target]] = [next[target], next[index]]
    return next
  })
  const dropOn = (targetId: number) => {
    if (draggedId === null) return
    setMedia(items => {
      // If the dragged clip is selected, move the complete selection as one
      // stable group. Otherwise only move the clip under the pointer.
      const movingIds = selectedIds.includes(draggedId) ? selectedIds : [draggedId]
      if (movingIds.includes(targetId)) return items
      const moving = items.filter(x => movingIds.includes(x.id))
      const remaining = items.filter(x => !movingIds.includes(x.id))
      const target = remaining.findIndex(x => x.id === targetId)
      remaining.splice(target < 0 ? remaining.length : target, 0, ...moving)
      return remaining
    })
    setDraggedId(null)
  }
  // Push item(s) to a 1-based slot in the storyline: the moved items are
  // inserted there (keeping their relative order) and everything else shifts.
  const [flashIds, setFlashIds] = useState<number[]>([])
  const highlightItems = (ids: number[]) => {
    setFlashIds(ids)
    window.setTimeout(() => {
      document.querySelector(`[data-item-id="${ids[0]}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 30)
    window.setTimeout(() => setFlashIds(current => current === ids ? [] : current), 1800)
  }
  const moveItemsToPosition = (ids: number[], position: number) => {
    if (!ids.length) return
    const moving = media.filter(x => ids.includes(x.id))
    if (!moving.length) return
    const remaining = media.filter(x => !ids.includes(x.id))
    const slot = Math.min(Math.max(1, Math.round(position)), remaining.length + 1) - 1
    const next = [...remaining.slice(0, slot), ...moving, ...remaining.slice(slot)]
    if (next.every((x, i) => x.id === media[i].id)) return
    setMedia(next)
    highlightItems(moving.map(x => x.id))
    notify(moving.length === 1 ? `Moved ${moving[0].name} to position ${slot + 1}` : `Moved ${moving.length} items to positions ${slot + 1}–${slot + moving.length}`)
  }
  const [bulkPosition, setBulkPosition] = useState(1)
  const deleteSelectedItems = () => {
    if (selectedIds.length === 0) return
    setMedia(items => {
      // Remove selected items and their transitions (transitions are on the previous item)
      const idsToRemove = new Set(selectedIds)
      const newItems = []
      for (let i = 0; i < items.length; i++) {
        if (!idsToRemove.has(items[i].id)) {
          // If the next item is being removed, clear its transition
          const nextItem = items[i + 1]
          if (nextItem && idsToRemove.has(nextItem.id)) {
            newItems.push({ ...items[i], transition: 'Fade', transitionTime: DEFAULT_TRANSITION_SECONDS })
          } else {
            newItems.push(items[i])
          }
        }
      }
      return newItems
    })
    setSelectedIds([])
    setSelectedTransitions([])
    setShowDeleteConfirm(false)
    notify(`Deleted ${selectedIds.length} item${selectedIds.length > 1 ? 's' : ''}`)
  }
  const clearOutputDirectory = async () => {
    try {
      const response = await fetch(`/api/output/clear?path=${encodeURIComponent(outputPath || '/output')}`, { method: 'POST' })
      if (response.ok) {
        const result = await response.json()
        notify(`Output directory cleared (${result.deleted_files} file${result.deleted_files === 1 ? '' : 's'}, ${result.deleted_dirs} folder${result.deleted_dirs === 1 ? '' : 's'})`)
      } else {
        let errorMsg = 'Failed to clear output directory'
        try {
          const data = await response.json()
          if (data.detail) errorMsg = data.detail
        } catch {}
        notify(errorMsg)
      }
    } catch (error) {
      notify(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally {
      setShowClearOutputConfirm(false)
    }
  }
  const cleanTempFiles = async () => {
    try {
      const response = await fetch('/api/cleanup', { method: 'POST' })
      if (response.ok) {
        const result = await response.json()
        notify(`Temporary files cleaned (${result.deleted_files} file${result.deleted_files === 1 ? '' : 's'}, ${result.deleted_dirs} folder${result.deleted_dirs === 1 ? '' : 's'})`)
      } else {
        let errorMsg = 'Failed to clean temporary files'
        try {
          const data = await response.json()
          if (data.detail) errorMsg = data.detail
        } catch {}
        notify(errorMsg)
      }
    } catch (error) {
      notify(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`)
    } finally {
      // The cleanup removed the preview/job we were tracking; detach the UI
      // from them so nothing keeps pointing at a freshly deleted file.
      setPreviewUrl(null)
      setShowPreview(false)
      setActiveJobId(null)
      setRendering(false)
      setPreviewing(false)
      setProgress(0)
      setShowCleanTempConfirm(false)
    }
  }
  const clearAllProjects = async () => {
    try {
      // Delete all projects from database
      const response = await fetch('/api/projects', { method: 'DELETE' })
      if (response.ok) {
        // Also clean up temporary files
        const cleanupResponse = await fetch('/api/cleanup', { method: 'POST' })
        if (cleanupResponse.ok) {
          const result = await cleanupResponse.json()
          notify(`All saved projects and temporary files deleted (${result.deleted_files} files, ${result.deleted_dirs} folders)`)
        } else {
          notify('Projects deleted but failed to clean temporary files')
        }
      } else {
        notify('Failed to delete all projects')
      }
    } catch (error) {
      notify(`Error: ${error instanceof Error ? error.message : 'Unknown error'}`)
    }
    // The row this editor was bound to is gone; drop the stale id so the next
    // save POSTs a fresh project instead of PUTting to a deleted one, and
    // detach the UI from the previews/jobs the cleanup just wiped.
    setProjectId(null)
    // The localStorage fallback would resurrect the deleted project on a
    // refresh, so clear it too.
    localStorage.removeItem('slideshow.project.mock')
    setPreviewUrl(null)
    setRendering(false)
    setPreviewing(false)
    setProgress(0)
    setShowClearAllConfirm(false)
  }
  const toggleSelected = (id: number) => setSelectedIds(ids => ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id])
  const selectCompactRange = (index: number, additive: boolean) => {
    const id = media[index]?.id
    if (id == null) return
    const anchor = lastCompactSelect.current
    lastCompactSelect.current = index
    if (additive && anchor != null) {
      const from = Math.min(anchor, index)
      const to = Math.max(anchor, index)
      const range = media.slice(from, to + 1).map(item => item.id)
      setSelectedIds(ids => Array.from(new Set([...ids, ...range])))
      return
    }
    toggleSelected(id)
  }
  const addTitleFrame = () => {
    const id = Date.now()
    setMedia(items => [...items, { id, name: 'Text frame', path: 'Generated frame', src: '', type: 'title', duration: 4, effect: 'None', transition: 'Fade', transitionTime: DEFAULT_TRANSITION_SECONDS, text: 'Your title here', textMode: 'frame', textStart: 0, textEnd: 4, textEnter: 'Fade', textExit: 'Fade', textEnterDuration: .5, textExitDuration: .5, textX: defaultTextX, textY: defaultTextY, frameBackground: '#30382a', fontFamily, fontSize: Number(fontSize) || 48, fontColor, textBold, textItalic, textUnderline }])
    setPendingTextFrame(id)
    setEditingTextFrame(id)
  }
  const closeTextFrameEditor = (save: boolean) => {
    if (!save && editingTextFrame !== null && editingTextFrame === pendingTextFrame) {
      setMedia(items => items.filter(x => x.id !== editingTextFrame))
      setSelectedIds(ids => ids.filter(id => id !== editingTextFrame))
    }
    setPendingTextFrame(null)
    setEditingTextFrame(null)
  }
  const dropAudioOn = (targetId: number) => {
    if (draggedAudioId === null || draggedAudioId === targetId) return setDraggedAudioId(null)
    setAudioTracks(items => { const next = [...items]; const from = next.findIndex(x => x.id === draggedAudioId); const to = next.findIndex(x => x.id === targetId); const [track] = next.splice(from, 1); next.splice(to, 0, track); return next })
    setDraggedAudioId(null)
  }
  const toggleTransition = (id: number) => setSelectedTransitions(ids => ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id])
  const toggleTextTransition = (id: number, edge: 'enter'|'exit') => {
    const key = `${id}-${edge}`
    setSelectedTextTransitions(keys => keys.includes(key) ? keys.filter(x=>x!==key) : [...keys,key])
  }
  const updateSelectedTextTransitions = (value?: string, duration?: number) => setMedia(items => items.map(item => {
    const enter = selectedTextTransitions.includes(`${item.id}-enter`)
    const exit = selectedTextTransitions.includes(`${item.id}-exit`)
    return {...item,
      ...(enter && value !== undefined ? {textEnter:value} : {}), ...(exit && value !== undefined ? {textExit:value} : {}),
      ...(enter && duration !== undefined ? {textEnterDuration:duration} : {}), ...(exit && duration !== undefined ? {textExitDuration:duration} : {})}
  }))
  const randomizeTextTransitions = () => {
    const enterOptions=['Fade','Slide up','Zoom in','Dissolve'], exitOptions=['Fade','Slide left','Dissolve']
    setMedia(items=>items.map(item=>{
      const useAll=selectedTextTransitions.length===0
      return {...item,
        ...((useAll||selectedTextTransitions.includes(`${item.id}-enter`))?{textEnter:enterOptions[Math.floor(Math.random()*enterOptions.length)]}:{}),
        ...((useAll||selectedTextTransitions.includes(`${item.id}-exit`))?{textExit:exitOptions[Math.floor(Math.random()*exitOptions.length)]}:{})}
    }))
    notify(`Text transitions randomized${selectedTextTransitions.length?` for ${selectedTextTransitions.length} selected boxes`:''}`)
  }
  const applyBulkEffect = () => {
    const ids = selectedIds.length ? selectedIds : media.filter(x => x.type === 'image').map(x => x.id)
    setMedia(items => items.map(item => ids.includes(item.id) && item.type === 'image' ? { ...item, effect: bulkEffect } : item))
    notify(`${bulkEffect} applied to ${ids.length} photo${ids.length === 1 ? '' : 's'}`)
  }
  const randomizeBulkEffect = () => {
    const ids = selectedIds.length ? selectedIds : media.filter(x=>x.type==='image').map(x=>x.id)
    const kenBurns = effects.filter(x=>x.startsWith('Ken Burns'))
    setMedia(items=>items.map(item=>ids.includes(item.id)&&item.type==='image'?{...item,effect:kenBurns[Math.floor(Math.random()*kenBurns.length)]}:item))
    notify(`Random Ken Burns effects applied to ${ids.filter(id=>media.find(x=>x.id===id)?.type==='image').length} selected photos`)
  }
  const applyBulkTransition = (random = false) => {
    const eligible = media.slice(0, -1).map(x => x.id)
    const ids = selectedTransitions.length ? selectedTransitions : eligible
    setMedia(items => items.map(item => {
      if (!ids.includes(item.id)) return item
      if (!random) return { ...item, transition: bulkTransition, transitionParams: isGLTransition(bulkTransition) ? (item.transitionParams || {}) : undefined }
      const picked = pickRandomTransition(randomScope)
      return { ...item, transition: picked, transitionParams: isGLTransition(picked) ? {} : undefined }
    }))
    notify(`${random ? randomScopeLabels[randomScope] : bulkTransition} applied to ${ids.length} transition${ids.length === 1 ? '' : 's'}`)
  }
  const randomize = () => setMedia(items => items.map(item => {
    const picked = pickRandomTransition(randomScope)
    return {
      ...item,
      transition: picked,
      transitionParams: isGLTransition(picked) ? {} : undefined,
      effect: item.type === 'video' ? 'Original motion' : effects[1 + Math.floor(Math.random() * (effects.length - 2))],
    }
  }))
  const applyDuration = () => {
    const value = clampNumber(Number(globalDuration) || DEFAULT_TRANSITION_SECONDS, 0.1, 30)
    // Only the clips that actually lead into another clip carry a transition;
    // the final clip is deliberately left untouched (it has no "next").
    setMedia(items => items.map((item, index) => index < items.length - 1 ? { ...item, transitionTime: clampTransitionFor(items, index, value) } : item))
    notify(`Applied ${value.toFixed(1)}s to all transitions`)
  }
  const waitForJob = async (jobId:string) => {
    for(;;){
      await new Promise(resolve=>setTimeout(resolve,1000))
      let response: Response
      try {
        response = await fetch(`/api/jobs/${jobId}`)
      } catch {
        // Network blip while the render is still running — keep polling.
        continue
      }
      // 503 = transient SQLite lock; the render is fine, just retry.
      if (response.status === 503 || response.status === 429) continue
      if (!response.ok) throw new Error('Could not read render status')
      const job=await response.json();setProgress(Math.round(job.progress||0))
      if(job.status==='complete')return job
      if(job.status==='cancelled')return { ...job, cancelled: true }
      if(job.status==='failed')throw new Error(job.error_message||'Job failed')
    }
  }
  const trackJob = async (jobId:string, kind:'preview'|'render') => {
    setActiveJobId(jobId)
    try{
      const completed=await waitForJob(jobId)
      if(completed.cancelled){notify(`${kind==='preview'?'Preview':'Render'} stopped`);return}
      if(kind==='preview'){setPreviewUrl(`${completed.fileUrl}?v=${Date.now()}`);setShowPreview(true);notify('Real FFmpeg preview is ready')}
      else notify(`MP4 render complete · ${outputFilename}.mp4`)
    }catch(error){notify(`${kind==='preview'?'Preview':'Render'} failed: ${error instanceof Error?error.message:'Unknown error'}`)}
    finally{kind==='preview'?setPreviewing(false):setRendering(false);setActiveJobId(id => id === jobId ? null : id)}
  }
  const startJob = async (kind:'preview'|'render', overwrite=false) => {
    kind==='preview'?setPreviewing(true):setRendering(true);setProgress(1)
    try{
      const id=await persistProject(true)
      const response=await fetch(`/api/projects/${id}/jobs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,overwrite})})
      if(!response.ok){
        const text=await response.text()
        // The backend refuses to overwrite an existing output file until the
        // user acknowledges it; surface the confirmation instead of failing.
        if(response.status===409&&kind==='render'){
          try{const detail=JSON.parse(text);if(detail?.code==='output_exists'){setOverwritePath(String(detail.path||''));setRendering(false);return}}catch{/* not the overwrite signal */}}
        throw new Error(text)
      }
      const created=await response.json()
      await trackJob(created.id,kind)
    }catch(error){notify(`${kind==='preview'?'Preview':'Render'} failed: ${error instanceof Error?error.message:'Unknown error'}`);kind==='preview'?setPreviewing(false):setRendering(false)}
  }
  const stopActiveJob = async () => {
    if (!activeJobId) return
    try {
      const response = await fetch(`/api/jobs/${activeJobId}/cancel`, { method: 'POST' })
      if (!response.ok && response.status !== 409) throw new Error(await readApiError(response, 'Could not stop'))
      notify('Stopping FFmpeg…')
    } catch (error) {
      notify(`Could not stop: ${error instanceof Error ? error.message : 'Unknown error'}`)
    }
  }
  // The backend keeps rendering after a page refresh; re-attach to a still-active
  // job so the Preview/Render buttons show its live progress again.
  const resumeActiveJob = async (id:number) => {
    try{
      const jobs=await fetch(`/api/jobs?project_id=${id}`).then(r=>r.ok?r.json():[])
      const active=jobs.find((job:any)=>['queued','running','cancelling'].includes(job.status))
      if(!active)return
      const kind:'preview'|'render'=active.kind==='preview'?'preview':'render'
      if(kind==='preview')setPreviewing(true);else setRendering(true)
      setProgress(Math.max(1,Math.round(active.progress||0)))
      notify(`${kind==='preview'?'Preview':'MP4 render'} is still running — progress restored`)
      void trackJob(active.id,kind)
    }catch{/* job list unavailable; nothing to resume */}
  }
  const startRender = () => void startJob('render')
  const generatePreview = () => void startJob('preview')
  const jumpTo = (id: string) => {
    setActiveTab('editor')
    window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 40)
  }

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><div className="brand-mark"><Film size={21} /></div><div><strong>slideshow</strong><span>PHOTO & VIDEO STUDIO</span></div></div>
      <nav>
        <button className={activeTab === 'editor' ? 'active' : ''} onClick={() => setActiveTab('editor')}><LayoutGrid size={16}/> Editor</button>
        <button type="button" onClick={() => jumpTo('section-storyline')}>Storyline</button>
        <button type="button" onClick={() => jumpTo('section-transitions')} title="Jump to the transition tools at the bottom of the storyline">Transitions</button>
        <button type="button" onClick={() => jumpTo('section-soundtrack')}>Soundtrack</button>
        <button type="button" onClick={() => jumpTo('section-output')}>Output</button>
        <button type="button" onClick={() => jumpTo('section-render')}>Ready to generate</button>
        <button className={activeTab === 'renders' ? 'active' : ''} onClick={() => setActiveTab('renders')}><ListVideo size={16}/> Render queue <span className="count">1</span></button>
      </nav>
      <div className="top-actions"><span className={`system-ok ${backendOnline?'':'offline'}`}><i/> {backendOnline?'Backend ready':'Backend offline'}</span><button className="icon-button" title="Settings"><Settings2 size={18}/></button><button className="icon-button" title="Help"><CircleHelp size={18}/></button></div>
    </header>

    {activeTab === 'renders' ? <RenderQueue projectId={projectId} onBack={() => setActiveTab('editor')} /> : <main>
      <section className="project-heading">
        <div>
          <div className="eyebrow-line">
            <div className="eyebrow">PROJECT / {(projectName || 'UNTITLED').toUpperCase()}</div>
          </div>
          <input value={projectName} onChange={e=>setProjectName(e.target.value)} aria-label="Project name"/>
          <p>Assemble your media, shape the motion, and export a finished story.</p>
        </div>
        <div className="heading-actions"><button className="btn ghost" disabled={!backendOnline} title={backendOnline?'Load a saved project from SQLite':'Backend is offline'} onClick={()=>setShowProjectLoader(true)}><FolderOpen size={16}/> Load project</button><button className="btn ghost" title="Delete every saved project and temporary file" onClick={() => setShowClearAllConfirm(true)}><Trash2 size={16}/> Clear all</button><button className="btn ghost" onClick={saveProject}><Save size={16}/> Save project</button><button className="btn dark" disabled={previewing||rendering||!capabilities.ffmpeg||media.length===0} onClick={generatePreview}>{previewing?<RefreshCw className="spin" size={15}/>:<Play size={15} fill="currentColor"/>} {previewing?`Building ${progress}%`:'Preview'}</button>{(previewing||rendering)&&<button className="btn ghost stop-job" title="Stop FFmpeg" onClick={() => void stopActiveJob()}><Square size={13} fill="currentColor"/> Stop</button>}</div>
      </section>

      <div className="workspace">
        <div className="left-column">
          <section className="panel timeline-panel" id="section-storyline">
            <div className="panel-title"><div><span className="step">01</span><div><h2>Storyline</h2><p>{media.length} items · {Math.floor(total / 60)}m {Math.floor(total % 60)}s estimated</p></div></div><div className="toolbar"><label className="switch-label"><input type="checkbox" checked={randomOrder} onChange={e => setRandomOrder(e.target.checked)}/><span className="switch"/>Random order</label><button className="btn soft" onClick={addTitleFrame}><Plus size={15}/> Text frame</button><button className="btn soft" onClick={()=>setShowTextStyles(true)}><Type size={15}/> Default text style</button><button className="btn soft" onClick={() => setShowBrowser(true)}><Plus size={16}/> Add media</button><button className="btn soft" disabled={selectedIds.length === 0} onClick={() => setShowDeleteConfirm(true)}><Trash2 size={15}/> Delete selected</button><button className="btn soft" title="Start a completely new blank project" onClick={requestNewProject}><Plus size={15}/> New project</button></div></div>
            {randomOrder && <div className="notice amber"><Shuffle size={16}/><span><strong>Random order enabled.</strong> A new order will be chosen at render time. The arrangement below remains unchanged.</span></div>}

            <div className="overview-head"><div><strong>OVERALL TIMELINE</strong><span>Drag selected clips as a group · edit text above each clip · click transitions · videos keep their own rows in story order</span></div><div className="story-layout"><label>Lines</label><Select value={timelineRows} onChange={setTimelineRows}><option value="auto">Auto ({autoLineCount})</option>{[1,2,3,4,5,6].map(x => <option value={x} key={x}>{x}</option>)}</Select></div><button className="text-random" onClick={randomizeTextTransitions}><Shuffle size={12}/> Text transitions</button><div className="zoom-controls"><button onClick={() => setTimelineZoom(z => Math.max(.6, +(z - .2).toFixed(1)))} title="Zoom out"><ZoomOut size={14}/></button><input className="zoom-slider" type="range" min={0.6} max={2.4} step={0.1} value={timelineZoom} aria-label="Timeline zoom" onChange={e => setTimelineZoom(Number(e.target.value))}/><span>{Math.round(timelineZoom * 100)}%</span><button onClick={() => setTimelineZoom(z => Math.min(2.4, +(z + .2).toFixed(1)))} title="Zoom in"><ZoomIn size={14}/></button><button className="fit-button" onClick={() => setTimelineZoom(1)} title="Reset zoom to show complete timeline">Fit</button></div></div>
            <div className="timeline-overview">{media.length===0&&<button className="empty-story" onClick={()=>setShowBrowser(true)}><FolderOpen size={22}/><strong>Your storyline is empty</strong><span>Browse the mounted /photos and /videos folders to begin.</span></button>}{timelineLines.map((line, lineIndex) => {
              const firstIndex = media.findIndex(x => x.id === line.items[0]?.id)
              const lastIndex = media.findIndex(x => x.id === line.items[line.items.length - 1]?.id)
              const lineStart = timeline.starts[firstIndex] ?? 0
              const lineEnd = (timeline.starts[lastIndex] ?? 0) + (timeline.durations[lastIndex] ?? 0)
              const lineDuration = lineEnd - lineStart
              return <div className={`timeline-line ${line.video ? 'video-line' : ''}`} key={lineIndex}><div className={`line-number ${line.video ? 'video' : ''}`} title={line.video ? 'Video row — movies are kept on their own row in story order' : undefined}>{lineIndex + 1}{line.video && <Video size={10}/>}</div><div className="line-content" style={{width: `${timelineZoom * 100}%`}}><div className="text-track">{line.items.map(item => <div className="text-lane" key={item.id} style={{flexGrow:item.duration}}><TimelineTextBox item={item} update={change=>patch(item.id,change)} selected={selectedTextTransitions} onSelect={edge=>toggleTextTransition(item.id,edge)}/></div>)}</div><div className="overview-track">{line.items.map(item => { const index=media.findIndex(x => x.id===item.id); const thumb = itemThumbUrl(item); return <div className="overview-segment-wrap" key={item.id} style={{flexGrow: item.duration}}><div draggable onDragStart={() => setDraggedId(item.id)} onDragEnd={() => setDraggedId(null)} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); dropOn(item.id); }} onDoubleClick={() => item.type === 'title' && setEditingTextFrame(item.id)} className={`overview-clip ${draggedId === item.id ? 'dragging' : ''} ${selectedIds.includes(item.id) ? 'selected' : ''} ${item.type === 'title' ? 'title-clip' : ''}`} style={item.type==='title'?frameBackgroundStyle(item):undefined}><MediaThumb item={item} onClick={e => { e.stopPropagation(); openMediaLightbox(item) }} onPointerDown={e => e.stopPropagation()} />{item.type !== 'title' && thumb ? <button type="button" className="clip-zoom" title="View" onClick={e => { e.preventDefault(); e.stopPropagation(); openMediaLightbox(item) }} onPointerDown={e => e.stopPropagation()}><ZoomIn size={11}/></button> : null}<button className="clip-select" title="Select clip" onClick={() => toggleSelected(item.id)}><span>{selectedIds.includes(item.id) && <Check size={10}/>}</span></button>{item.type !== 'title' && item.text.trim() !== '' && <button type="button" className={`clip-text-toggle ${item.textEnabled === false ? 'off' : ''}`} title={item.textEnabled === false ? 'Text is hidden on this picture — click to show it' : 'Text is shown on this picture — click to hide it'} onClick={e => { e.preventDefault(); e.stopPropagation(); patch(item.id, { textEnabled: item.textEnabled === false }) }} onPointerDown={e => e.stopPropagation()}>{item.textEnabled === false ? <EyeOff size={11}/> : <Eye size={11}/>}</button>}<span>{String(index + 1).padStart(2,'0')} · {item.name}</span><small>{item.duration}s</small></div>{index < media.length - 1 && <button title={`${item.transition}${item.transitionEasing && item.transitionEasing!=='linear' ? ' · '+item.transitionEasing : ''}${item.transitionReverse ? ' · reverse':''} · ${item.transitionTime}s`} onClick={() => setTransitionPreviewId(item.id)} className={`transition-marker ${selectedTransitions.includes(item.id) ? 'selected' : ''} ${isGLTransition(item.transition)?'gl':''}`}><i>{transitionSymbol(item.transition)}</i><strong>{timelineZoom >= 1 ? item.transition.replace('GL · ','').replace('GLSL · ','') : ''}</strong><b>{item.transitionTime}s</b>{item.transitionEasing && item.transitionEasing!=='linear' ? <em>{item.transitionEasing}</em>:null}</button>}</div>})}</div><TimelineRuler start={lineStart} duration={lineDuration} zoom={timelineZoom} audioLength={lineIndex === timelineLines.length - 1 && audioTracks.length > 0 ? formatClock(audioTotalSeconds) : undefined}/></div></div>
            })}</div>

            {selectedTransitions.length > 0 && (()=>{ const first = media.find(x => x.id === selectedTransitions[0]); const isGL = first && isGLTransition(first.transition); return <div className="timeline-inspector with-gl"><span>{selectedTransitions.length} transition{selectedTransitions.length > 1 ? 's' : ''} selected</span><Select value={first?.transition || 'Fade'} onChange={v => setMedia(items => items.map(item => selectedTransitions.includes(item.id) ? {...item, transition:v, transitionParams: isGLTransition(v) ? (item.transitionParams||{}) : undefined} : item))}><TransitionOptions/></Select><NumberStepper value={first?.transitionTime ?? DEFAULT_TRANSITION_SECONDS} min={MIN_TRANSITION_SECONDS} step={0.1} suffix="sec" ariaLabel="Selected transition time" onChange={updateSelectedTransitionTimes} />{isGL && first && <GLParamControls transition={first.transition} params={(first.transitionParams as Record<string,string|number>)||{}} onChange={next=>setMedia(items=>items.map(item=>selectedTransitions.includes(item.id)?{...item, transitionParams: next}:item))}/>} <div className="transition-meta"><EasingSelect value={first?.transitionEasing||EASING_DEFAULT} onChange={v=>setMedia(items=>items.map(item=>selectedTransitions.includes(item.id)?{...item, transitionEasing:v}:item))}/><label className="check-label"><input type="checkbox" checked={Boolean(first?.transitionReverse)} onChange={e=>setMedia(items=>items.map(item=>selectedTransitions.includes(item.id)?{...item, transitionReverse: e.target.checked?1:0}:item))}/><span><Check size={11}/></span> Reverse</label></div><button onClick={() => setSelectedTransitions([])}><X size={13}/> Clear</button></div>})()}
            {selectedTextTransitions.length > 0 && <div className="timeline-inspector text-inspector"><span>{selectedTextTransitions.length} text transition{selectedTextTransitions.length>1?'s':''} selected</span><Select value={(()=>{const [id,edge]=selectedTextTransitions[0].split('-');const item=media.find(x=>x.id===Number(id));return edge==='enter'?item?.textEnter||'Fade':item?.textExit||'Fade'})()} onChange={v=>updateSelectedTextTransitions(v,undefined)}><TransitionOptions/></Select><NumberStepper value={(()=>{const [id,edge]=selectedTextTransitions[0].split('-');const item=media.find(x=>x.id===Number(id));return edge==='enter'?item?.textEnterDuration??.5:item?.textExitDuration??.5})()} min={0.1} step={0.1} suffix="sec" ariaLabel="Selected text transition time" onChange={v=>updateSelectedTextTransitions(undefined,v)} /><button onClick={()=>setSelectedTextTransitions([])}><X size={13}/> Clear</button></div>}

            <div className="bulk-tools"><div><span>PHOTO SELECTION</span><strong>{selectedIds.length ? `${selectedIds.length} selected` : 'All photos'}</strong></div><Select value={bulkEffect} onChange={setBulkEffect}>{effects.filter(x => x !== 'Original motion').map(x => <option key={x}>{x}</option>)}</Select><button onClick={applyBulkEffect}>Apply Ken Burns</button><button className="random-button" onClick={randomizeBulkEffect}><Shuffle size={13}/> Random</button><i/><div><span>MOVE SELECTED</span><strong>{selectedIds.length ? `${selectedIds.length} item${selectedIds.length === 1 ? '' : 's'}` : 'Select items first'}</strong></div><div className="move-to"><label>to <input type="number" min={1} max={media.length} value={bulkPosition} disabled={!selectedIds.length} onChange={e => setBulkPosition(Number(e.target.value))} onKeyDown={e => { if (e.key === 'Enter') moveItemsToPosition(selectedIds, bulkPosition) }} aria-label="Target position"/></label><button disabled={!selectedIds.length} onClick={() => moveItemsToPosition(selectedIds, bulkPosition)} title="Insert the selection at this position; other items shift">Move</button><button disabled={!selectedIds.length} onClick={() => moveItemsToPosition(selectedIds, 1)} title="Move selection to the start"><ArrowUp size={12}/> Start</button><button disabled={!selectedIds.length} onClick={() => moveItemsToPosition(selectedIds, media.length)} title="Move selection to the end"><ArrowDown size={12}/> End</button></div><i/><div><span>TRANSITION SELECTION</span><strong>{selectedTransitions.length ? `${selectedTransitions.length} selected` : 'All transitions'}</strong></div><Select value={bulkTransition} onChange={setBulkTransition}><TransitionOptions/></Select><button onClick={() => applyBulkTransition(false)}>Apply effect</button><RandomScopeSelect value={randomScope} onChange={setRandomScope}/><button className="random-button" title={`Assign a random transition from: ${randomScopeLabels[randomScope]}`} onClick={() => applyBulkTransition(true)}><Shuffle size={13}/> Random</button></div>

            <div className="bulk-bar" id="section-transitions"><span>TRANSITION DEFAULT</span><NumberStepper value={globalDuration} min={0.1} max={30} step={0.1} suffix="sec" ariaLabel="Default transition duration" onChange={setGlobalDuration} /><button onClick={applyDuration}>Apply to all</button><i/><span className="random-scope-label">RANDOM SOURCE</span><RandomScopeSelect value={randomScope} onChange={setRandomScope}/><button className="random-button" title={`Randomize every clip using: ${randomScopeLabels[randomScope]}`} onClick={() => { randomize(); notify(`Effects and transitions randomized · ${randomScopeLabels[randomScope]}`) }}><Shuffle size={14}/> Randomize all</button></div>
            <div className="media-view-bar"><span className="view-label">VIEW</span><div className="mode-toggle"><button className={!compactMediaView ? 'active' : ''} onClick={() => setCompactMediaView(false)} title="Show the full detail list"><List size={14}/> List</button><button className={compactMediaView ? 'active' : ''} onClick={() => setCompactMediaView(true)} title="Show a compact thumbnail grid with quick multi-selection"><LayoutGrid size={14}/> Compact</button></div>{compactMediaView && <div className="zoom-controls compact-zoom"><button onClick={() => setCompactZoom(z => Math.max(.6, +(z - .2).toFixed(1)))} title="Zoom out — smaller thumbnails"><ZoomOut size={14}/></button><input className="zoom-slider" type="range" min={0.6} max={1.6} step={0.1} value={compactZoom} aria-label="Compact thumbnail zoom" onChange={e => setCompactZoom(Number(e.target.value))}/><span>{Math.round(compactZoom * 100)}%</span><button onClick={() => setCompactZoom(z => Math.min(1.6, +(z + .2).toFixed(1)))} title="Zoom in — bigger thumbnails"><ZoomIn size={14}/></button></div>}<span className="view-hint">Select frames with the check marks · Shift-click for a range · “Select all” grabs every frame in one go · drag to reorder · click a picture to view it</span></div>
            {compactMediaView && <div className="compact-actions"><button className="btn soft" disabled={!media.length} onClick={() => setSelectedIds(media.map(x => x.id))} title="Select every frame in one go"><Check size={14}/> Select all</button><button className="btn soft" disabled={!selectedIds.length} onClick={() => setSelectedIds([])}>Clear selection</button><button className="btn soft" disabled={!selectedIds.length} onClick={() => setShowDeleteConfirm(true)}><Trash2 size={14}/> Delete selected</button><span className="compact-count">{selectedIds.length} of {media.length} frame{media.length === 1 ? '' : 's'} selected</span></div>}
            {!compactMediaView && <div className="timeline-head"><span>MEDIA</span><span>SLIDE / CLIP</span><span>EFFECT</span><span>TRANSITION TO NEXT</span><span>AUDIO</span><span></span></div>}
            {compactMediaView ? <div className="compact-grid" style={{ '--compactSize': compactZoom } as React.CSSProperties}>{media.map((item, index) => <div className={`compact-card ${draggedId === item.id ? 'dragging' : ''} ${selectedIds.includes(item.id) ? 'selected' : ''} ${flashIds.includes(item.id) ? 'just-moved' : ''}`} data-item-id={item.id} key={item.id} draggable onDragStart={() => setDraggedId(item.id)} onDragEnd={() => setDraggedId(null)} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); dropOn(item.id); }}><div className={`compact-thumb ${item.type === 'title' ? 'title-thumb' : ''}`} style={item.type === 'title' ? frameBackgroundStyle(item) : undefined} onClick={e => { if (item.type === 'title') setEditingTextFrame(item.id); else { e.stopPropagation(); openMediaLightbox(item) } }} title={item.type === 'title' ? 'Edit text frame' : 'View'}>{item.type === 'title' ? <span className="title-symbol">T</span> : <MediaThumb item={item} onPointerDown={e => e.stopPropagation()} />}<PositionBadge index={index} count={media.length} onMove={pos => moveItemsToPosition([item.id], pos)} /></div><button className="compact-select" title="Select frame · Shift-click for a range" aria-label={selectedIds.includes(item.id) ? `Deselect ${item.name}` : `Select ${item.name}`} aria-pressed={selectedIds.includes(item.id)} onClick={e => { e.stopPropagation(); selectCompactRange(index, e.shiftKey) }}><span>{selectedIds.includes(item.id) && <Check size={11}/>}</span></button><button className="compact-delete" title={`Remove ${item.name}`} onClick={() => setMedia(m => m.filter(x => x.id !== item.id))}><Trash2 size={14}/></button></div>)}</div> : <div className="timeline-list">
              {media.map((item, index) => {
                const thumb = itemThumbUrl(item)
                return <div className={`timeline-item ${draggedId === item.id ? 'dragging' : ''} ${selectedIds.includes(item.id) ? 'selected-row' : ''} ${flashIds.includes(item.id) ? 'just-moved' : ''}`} data-item-id={item.id} key={item.id} draggable onDragStart={() => setDraggedId(item.id)} onDragEnd={() => setDraggedId(null)} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); dropOn(item.id); }}>
                  <div className="row-select"><GripVertical className="grip" size={16}/><label title="Select for bulk changes"><input type="checkbox" checked={selectedIds.includes(item.id)} onChange={() => toggleSelected(item.id)}/><span><Check size={9}/></span></label></div>
                  <div className={`thumb ${item.type === 'title' ? 'title-thumb' : ''} ${item.type !== 'title' && thumb ? 'thumb-open' : ''}`} style={item.type==='title'?frameBackgroundStyle(item):undefined} onClick={e => { if (item.type !== 'title') { e.stopPropagation(); openMediaLightbox(item) } }} title={item.type !== 'title' ? 'View' : undefined}>{item.type === 'title' ? <span className="title-symbol">T</span> : <MediaThumb item={item} />}{item.type === 'video' && <span><Video size={12}/> {formatClock(item.duration)}</span>}<PositionBadge index={index} count={media.length} onMove={pos => moveItemsToPosition([item.id], pos)} /></div>
                  <div className="media-info"><strong>{item.name}</strong><span>{item.path}</span><small>{item.type === 'image' ? '6000 × 4000 · JPG' : item.type === 'video' ? '1920 × 1080 · H.264' : 'Generated text frame'}</small><div className={`item-text-edit ${item.textEnabled === false ? 'off' : ''}`}>{item.type !== 'title' && <button type="button" className={`text-toggle ${item.textEnabled === false ? 'off' : ''}`} title={item.textEnabled === false ? 'Text is hidden on this picture — click to show it' : 'Text is shown on this picture — click to hide it'} onClick={() => patch(item.id, { textEnabled: item.textEnabled === false })}>{item.textEnabled === false ? <EyeOff size={13}/> : <Eye size={13}/>}</button>}<button className={`text-detail-transition ${detailTextEditor?.id===item.id&&detailTextEditor.edge==='enter'?'selected':''}`} title={`Text appears with ${item.textEnter} · ${item.textEnterDuration}s`} onClick={()=>setDetailTextEditor({id:item.id,edge:'enter'})}>{transitionSymbol(item.textEnter)}</button><input value={item.text} placeholder="Add text…" onChange={e => patch(item.id,{text:e.target.value})}/><button className={`text-detail-transition ${detailTextEditor?.id===item.id&&detailTextEditor.edge==='exit'?'selected':''}`} title={`Text disappears with ${item.textExit} · ${item.textExitDuration}s`} onClick={()=>setDetailTextEditor({id:item.id,edge:'exit'})}>{transitionSymbol(item.textExit)}</button><Select value={item.textMode} onChange={v => patch(item.id,{textMode:v as 'overlay'|'frame'})}><option value="overlay">On picture</option><option value="frame">New frame</option></Select>{item.type==='title'&&<button className="edit-frame-button" onClick={()=>setEditingTextFrame(item.id)}>Edit frame</button>}</div>{detailTextEditor?.id===item.id&&<div className="detail-transition-popover"><strong>{detailTextEditor.edge==='enter'?'Text appears':'Text disappears'}</strong><Select value={detailTextEditor.edge==='enter'?item.textEnter:item.textExit} onChange={v=>patch(item.id,detailTextEditor.edge==='enter'?{textEnter:v}:{textExit:v})}><TransitionOptions/></Select><NumberStepper value={detailTextEditor.edge==='enter'?(item.textEnterDuration ?? .5):(item.textExitDuration ?? .5)} min={0.1} step={0.1} suffix="s" ariaLabel="Text transition duration" onChange={v=>patch(item.id,detailTextEditor.edge==='enter'?{textEnterDuration:v}:{textExitDuration:v})} /><button onClick={()=>setDetailTextEditor(null)}><X size={13}/></button></div>}</div>
                  <div className="clip-duration"><NumberStepper value={item.duration} min={MIN_CLIP_SECONDS} step={0.5} ariaLabel={`${item.name} duration`} onChange={v => updateDuration(item.id, v)} /><span>sec</span></div>
                  <Select ariaLabel={`${item.name} effect`} value={item.effect} onChange={v => patch(item.id, { effect: v })}>{effects.map(x => <option key={x}>{x}</option>)}</Select>
                  {index < media.length - 1 ? <TransitionCell item={item} onPatch={patch_ => patch(item.id, patch_)} /> : <div className="end-card"><Check size={13}/> End of story</div>}
                  {item.type === 'video' ? <div className="movie-audio"><Select ariaLabel={`${item.name} audio`} value={item.audioSource || 'soundtrack'} onChange={v => patch(item.id, { audioSource: v as 'soundtrack' | 'original' })}><option value="soundtrack">Soundtrack</option><option value="original">Original movie audio</option></Select><small>{item.audioSource === 'original' ? 'Crossfades with soundtrack' : 'Keeps soundtrack playing'}</small></div> : <div className="movie-audio muted">—</div>}
                  <div className="row-actions"><button disabled={index === 0} onClick={() => move(index, -1)} title="Move up"><ArrowUp size={14}/></button><button disabled={index === media.length - 1} onClick={() => move(index, 1)} title="Move down"><ArrowDown size={14}/></button><button onClick={() => setMedia(m => m.filter(x => x.id !== item.id))} title="Remove"><Trash2 size={14}/></button></div>
                </div>
              })}
            </div>}
            <button className="add-strip" onClick={() => setShowBrowser(true)}><Plus size={17}/> Add photos or videos from mounted folders</button>
            <div className="story-total"><Clock3 size={15}/><div><span>ESTIMATED TOTAL SLIDESHOW TIME</span><strong>{formatClock(total)}</strong></div><small>Includes media durations and transitions{hasOriginalMovieAudio ? ' · original movie audio crossfades are included' : ''}</small></div>
          </section>

          <section className="panel audio-panel" id="section-soundtrack">
            <div className="panel-title compact"><div><span className="step">03</span><div><h2>Soundtracks</h2><p>Add multiple MP3 files and drag to set their play order.</p></div></div><div className="audio-total"><Clock3 size={14}/><span>Total soundtrack time</span><strong>{formatClock(audioTotalSeconds)}</strong></div><button className="btn soft" onClick={()=>setShowAudioBrowser(true)}><Plus size={14}/> Add MP3</button></div>
            <div className="audio-list">{audioTracks.map((track,index)=><div className={`audio-track ${draggedAudioId===track.id?'dragging':''}`} key={track.id} draggable onDragStart={()=>setDraggedAudioId(track.id)} onDragEnd={()=>setDraggedAudioId(null)} onDragOver={e=>e.preventDefault()} onDrop={()=>dropAudioOn(track.id)}><div className="audio-order"><GripVertical size={15}/><b>{index+1}</b></div><div className="music-icon" style={{background:`${track.color}33`,color:track.color}}><Music2 size={21}/></div><div className="audio-name"><strong>{track.name}</strong><span>{track.path} · {track.duration} · MP3 320 kbps</span>{trackIsEdited(track) && <em className="trim-badge" title="This track is edited · click the scissors to change"><Scissors size={10}/> {formatClock(trackKeptRange(track).start)}–{formatClock(trackKeptRange(track).end)} · {formatClock(trackKeptSeconds(track))}{(Number(track.fadeIn)||0) > 0 && <i title={`Fade in ${track.fadeIn}s`}>⟋{track.fadeIn}s</i>}{(Number(track.fadeOut)||0) > 0 && <i title={`Fade out ${track.fadeOut}s`}>⟍{track.fadeOut}s</i>}</em>}{Number.isFinite(track.loudness) && <LoudnessMeter loudness={track.loudness!} target={audioNormalizeTarget} normalized={audioNormalize}/>}</div>{audioPreview.playingKey===String(track.id) ? <div className="waveform-player"><AudioSeekBar seed={index} color={track.color} current={audioPreview.progress.current} duration={audioPreview.progress.duration} onSeek={audioPreview.seek}/><AudioTimeReadout current={audioPreview.progress.current} duration={audioPreview.progress.duration}/></div> : <div className="waveform">{Array.from({length: 55}).map((_, i) => <i key={i} style={{height: `${8 + ((i * 17+index*7) % 23)}px`,background:track.color}}/> )}</div>}<button className={`icon-button audio-play ${audioPreview.playingKey===String(track.id)?'playing':''}`} title={audioPreview.playingKey===String(track.id)?'Stop preview':'Play preview'} onClick={()=>audioPreview.toggle(String(track.id),mediaFileUrl('music', mediaItemPath(track)),track.name)}>{audioPreview.playingKey===String(track.id)?<Pause size={15}/>:<Play size={15}/>}</button><button className={`icon-button ${trackIsEdited(track)?'edited':''}`} title="Cut, crop and fade this track" aria-label="Edit track" onClick={()=>{ if (audioPreview.playingKey) audioPreview.toggle(audioPreview.playingKey, '', ''); setEditingTrackId(track.id) }}><Scissors size={15}/></button><button className="icon-button" onClick={()=>setAudioTracks(a=>a.filter(x=>x.id!==track.id))}><X size={16}/></button></div>)}</div>
            <div className="audio-settings"><div><FieldLabel>When audio is shorter than the video</FieldLabel><Select value={audioPolicy} onChange={setAudioPolicy}><option>Loop & trim</option><option>Play once, then silence</option><option>Fit slideshow to audio</option></Select></div><div><FieldLabel>Music volume <span>{audioVolume}%</span></FieldLabel><input className="range" type="range" value={audioVolume} onChange={e=>setAudioVolume(Number(e.target.value))}/></div><div className="fade-settings"><label className="check-label"><input type="checkbox" checked={audioFade} onChange={e=>setAudioFade(e.target.checked)}/><span><Check size={11}/></span>Fade out soundtrack at the end{audioFade && <small>{audioFadeDuration.toFixed(1)}s</small>}</label>{audioFade && <><input className="range" type="range" min={0.5} max={15} step={0.5} value={audioFadeDuration} onChange={e=>setAudioFadeDuration(Number(e.target.value))} title="Fade-out duration"/><FieldLabel>Silence before the final frame <span>{audioFadeTail.toFixed(1)}s</span></FieldLabel><input className="range" type="range" min={0} max={10} step={0.5} value={audioFadeTail} onChange={e=>setAudioFadeTail(Number(e.target.value))} title="Seconds of silence kept after the fade, before the slideshow ends"/>{audioFadeTooLong && <em className="fade-hint"><AlertTriangle size={11}/> Longer than the slideshow ({formatClock(total)}) · clamped when rendering</em>}</>}</div><button className="btn soft" onClick={()=>setShowAudioBrowser(true)}><FolderOpen size={15}/> Add soundtrack</button></div>
            <div className="normalize-settings"><label className="check-label"><input type="checkbox" checked={audioNormalize} onChange={e=>setAudioNormalize(e.target.checked)}/><span><Check size={11}/></span>Normalise soundtrack levels<small title="EBU R128: each song is matched to the target, then the whole mix gets a final pass">{audioNormalize ? `${audioNormalizeTarget} LUFS` : 'off'}</small></label>{audioNormalize && <div className="normalize-slider"><span>Quiet · −24</span><input className="range" type="range" min={-24} max={-8} step={1} value={audioNormalizeTarget} onChange={e=>setAudioNormalizeTarget(Number(e.target.value))} title="Target loudness (−14 LUFS = streaming standard, −23 = TV, −11 = loud)"/><span>−8 · Loud</span><em className="normalize-preset">{audioNormalizeTarget <= -22 ? 'TV / broadcast' : audioNormalizeTarget <= -16 ? 'Quiet / podcast' : audioNormalizeTarget <= -12 ? 'Streaming standard' : 'Loud'}</em></div>}<button type="button" className="btn ghost" disabled={!audioTracks.length || analysingLevels || !backendOnline} title="Measure each track's loudness with FFmpeg" onClick={() => void analyseLevels()}>{analysingLevels ? <RefreshCw className="spin" size={14}/> : <Activity size={14}/>} {analysingLevels ? 'Analysing…' : 'Analyse levels'}</button>{loudnessSpread >= 3 && !audioNormalize && <em className="fade-hint"><AlertTriangle size={11}/> Tracks differ by {loudnessSpread.toFixed(1)} dB · enable normalisation to match them</em>}</div>
          </section>
        <div className="export-row">
          <section className="panel output-panel" id="section-output"><div className="panel-title compact"><div><span className="step">04</span><div><h2>Output</h2><p>Choose quality and destination.</p></div></div><div className="panel-actions"><button type="button" className="btn soft" disabled={rendering||previewing} title="Delete interim segments, soundtrack caches and proxy previews. Rendered MP4s and saved projects are kept." onClick={() => setShowCleanTempConfirm(true)}><Eraser size={14}/> Clean temp files</button><button type="button" className="btn soft" title="Clear all files in the output directory" onClick={() => setShowClearOutputConfirm(true)}><Trash2 size={14}/> Clear output</button></div></div>
            <div className="form-grid two"><div><FieldLabel>Resolution</FieldLabel><Select value={resolution} onChange={setResolution}><option>4K UHD · 2160p</option><option>Full HD · 1080p</option><option>HD · 720p</option><option>SD · 480p</option></Select></div><div><FieldLabel>Frame rate</FieldLabel><Select value={frameRate} onChange={setFrameRate}><option>24 fps</option><option>25 fps</option><option>30 fps</option><option>50 fps</option><option>60 fps</option></Select></div></div>
            <div className="form-grid two"><div><FieldLabel>Video bitrate</FieldLabel><Select value={bitrate} onChange={setBitrate}><option>4 Mbps · Standard</option><option>8 Mbps · High</option><option>12 Mbps · Very high</option><option>20 Mbps · Maximum</option></Select></div><div><FieldLabel>Encoder</FieldLabel><Select value={encoder} onChange={setEncoder}><option>Auto · Quick Sync</option><option>Intel Quick Sync</option><option>CPU · x264</option></Select></div></div>
            <div><FieldLabel>Output folder</FieldLabel><div className="path-field"><FolderOpen size={15}/><input value={outputPath} onChange={e=>setOutputPath(e.target.value)}/><button onClick={()=>setShowFolderPicker(true)} title="Browse the mounted /output volume">Browse</button></div></div>
            <div><FieldLabel>Filename</FieldLabel><div className="filename"><input value={outputFilename} onChange={e=>setOutputFilename(e.target.value)}/><span>.mp4</span></div></div>
            <div className="estimate"><div><Activity size={15}/><span>ESTIMATED OUTPUT</span></div><strong>~{formatFileSize(estimateOutputBytes(total, bitrate, soundProgramSeconds > 0))}</strong><small>H.264{soundProgramSeconds ? ' · AAC stereo' : ''} · {formatClock(total)} · {parsePresetNumber(bitrate, 8)} Mbps</small></div>
          </section>

          <section className="panel review-panel"><div className="review-title"><Sparkles size={18}/><div><h3>{rendering||previewing?'Working…':'Ready to render'}</h3><p>{rendering||previewing?`${progress}% · you can stop at any time`:'All checks passed'}</p></div><span>{rendering||previewing?<RefreshCw className="spin" size={14}/>:<Check size={14}/>}</span></div><ul><li><Check size={13}/> {media.length} media items are ready</li><li><Check size={13}/> Output folder is writable</li><li className={capabilities.ffmpeg?'':'warning'}>{capabilities.ffmpeg?<Check size={13}/>:<AlertTriangle size={13}/>} {capabilities.ffmpeg?'FFmpeg backend is available':'FFmpeg is unavailable'}</li><li className={capabilities.quickSync?'':'warning'}>{capabilities.quickSync?<Check size={13}/>:<AlertTriangle size={13}/>} {capabilities.quickSync?'Intel Quick Sync is available':'Quick Sync unavailable · CPU fallback'}</li><li className="warning"><AlertTriangle size={13}/> GLSL transitions may use CPU fallback</li>{audioFadeTooLong && <li className="warning"><AlertTriangle size={13}/> Soundtrack fade ({audioFadeDuration.toFixed(1)}s + {audioFadeTail.toFixed(1)}s silence) exceeds the slideshow · it will be clamped</li>}</ul><button className="btn preview-btn" disabled={previewing||rendering||!capabilities.ffmpeg||media.length===0} onClick={generatePreview}>{previewing?<RefreshCw className="spin" size={16}/>:<Play size={16}/>} {previewing?`Generating preview ${progress}%`:'Generate preview'}</button><button className="btn render-btn" disabled={rendering||previewing||!capabilities.ffmpeg||media.length===0} onClick={startRender}>{rendering ? <><RefreshCw className="spin" size={16}/> Rendering… {progress}%</> : <><Zap size={16}/> Render MP4</>}</button><button type="button" className="btn ghost stop-job wide" disabled={!rendering && !previewing} title="Stop the running FFmpeg process" onClick={() => void stopActiveJob()}><Square size={14} fill="currentColor"/> Stop {rendering?'render':previewing?'preview':'job'}</button>{(rendering||previewing) && <div className="progress"><i style={{width: `${progress}%`}}/></div>}<p className="render-note"><Info size={13}/> FFmpeg jobs run in the backend; progress and logs are stored in SQLite. Stop kills the current FFmpeg process. Intermediate segments and stale proxy previews are cleaned up automatically after each render.</p></section>
        </div>
        </div>
      </div>
    </main>}

    {editingTrackId != null && (() => { const track = audioTracks.find(x => x.id === editingTrackId); return track ? <SoundtrackEditor track={track} onChange={change => setAudioTracks(items => items.map(x => x.id === track.id ? { ...x, ...change } : x))} onClose={() => setEditingTrackId(null)} /> : null })()}
    {showTextStyles && <TextStyleModal fontFamily={fontFamily} setFontFamily={setFontFamily} fontSize={fontSize} setFontSize={setFontSize} fontColor={fontColor} setFontColor={setFontColor} bold={textBold} setBold={setTextBold} italic={textItalic} setItalic={setTextItalic} underline={textUnderline} setUnderline={setTextUnderline} textX={defaultTextX} setTextX={setDefaultTextX} textY={defaultTextY} setTextY={setDefaultTextY} onClose={()=>setShowTextStyles(false)}/>} 
    {editingTextFrame !== null && media.find(x=>x.id===editingTextFrame) && <TextFrameEditor item={media.find(x=>x.id===editingTextFrame)!} isNew={editingTextFrame===pendingTextFrame} update={change=>patch(editingTextFrame,change)} onSave={()=>closeTextFrameEditor(true)} onCancel={()=>closeTextFrameEditor(false)}/>} 
    {showAudioBrowser && <MediaBrowser audioOnly onClose={()=>setShowAudioBrowser(false)} onAdd={(files:any[])=>{
      void (async () => {
        const additions: AudioTrack[] = []
        for (let index = 0; index < files.length; index++) {
          const file = files[index]
          const seconds = await probeMediaDuration(mediaFileUrl('music', file.path), 'audio')
          additions.push({
            id: Date.now() + index, name: file.name, path: file.path,
            duration: seconds > 0 ? formatClock(seconds) : '0:00',
            color: ['#91a96b', '#7898aa', '#b78670'][index % 3],
          })
        }
        setAudioTracks(items => [...items, ...additions])
        setShowAudioBrowser(false)
        notify(`${files.length} soundtrack${files.length === 1 ? '' : 's'} added`)
      })()
    }}/>}
    {showBrowser && <MediaBrowser onClose={() => setShowBrowser(false)} onAdd={(files:any[]) => {
      // Probe each video's native length so the timeline hold covers the
      // complete movie before the transition to the next picture. Images keep
      // the 5 s default; a failed probe falls back to 10 s.
      void (async () => {
        const additions: MediaItem[] = []
        for (let index = 0; index < files.length; index++) {
          const file = files[index]
          const isVideo = file.kind === 'video'
          // Every playable file is accepted — photos and videos alike — no
          // matter which location was browsed. The stream root follows the
          // file's real mount (/photos or /videos), never its kind.
          const root = mediaRootFromPath(file.path, isVideo ? 'videos' : 'photos')
          const src = mediaFileUrl(root, file.path)
          let duration = isVideo ? 10 : 5
          if (isVideo) {
            try {
              duration = await new Promise<number>((resolve) => {
                const el = document.createElement('video')
                el.preload = 'metadata'
                const done = (value: number) => { el.removeAttribute('src'); el.load(); resolve(value) }
                el.onloadedmetadata = () => done(Number.isFinite(el.duration) && el.duration > 0 ? Math.max(MIN_CLIP_SECONDS, el.duration) : 10)
                el.onerror = () => done(0)
                // Some mounts never fire metadata; don't block the add forever.
                window.setTimeout(() => done(0), 8000)
                el.src = src
              })
              // AVI from cameras such as the Casio EX-Z11 commonly contains
              // Motion JPEG and PCM. Browsers cannot probe it, while FFmpeg can.
              if (duration <= 0) duration = await serverVideoDuration(root as 'photos' | 'videos', file.path)
              duration = duration > 0 ? Math.max(MIN_CLIP_SECONDS, duration) : 10
            } catch { duration = 10 }
          }
          additions.push({
            id: Date.now() + index, name: file.name, path: file.path, src,
            type: file.kind as 'image' | 'video', duration,
            effect: isVideo ? 'Original motion' : 'None',
            transition: 'Fade', transitionTime: DEFAULT_TRANSITION_SECONDS,
            audioSource: isVideo ? 'soundtrack' : undefined,
            text: '', textMode: 'overlay', textStart: 0, textEnd: duration,
            textEnter: 'Fade', textExit: 'Fade', textEnterDuration: .5, textExitDuration: .5,
            textX: 50, textY: 72, frameBackground: '#30382a',
          })
        }
        setMedia(items => [...items, ...additions])
        setShowBrowser(false)
        notify(`${files.length} mounted media file${files.length === 1 ? '' : 's'} added`)
      })()
    }}/>} 
    {transitionPreviewId != null && (() => { const index = media.findIndex(x => x.id === transitionPreviewId); return index >= 0 && index < media.length - 1 ? <TransitionPreview outgoing={media[index]} incoming={media[index + 1]} onClose={() => setTransitionPreviewId(null)} onApply={(patchData) => { patch(media[index].id, patchData); setTransitionPreviewId(null); notify(`Applied ${patchData.transition} transition`) }} /> : null })()}
    {showPreview && <Preview media={media} projectName={projectName} previewUrl={previewUrl} playing={isPlaying} setPlaying={setPlaying} onClose={() => {setShowPreview(false); setPlaying(false)}}/>}
    {showFolderPicker && <FolderPicker current={outputPath} onSelect={p=>{setOutputPath(p);notify(`Output folder set to ${p}`)}} onClose={()=>setShowFolderPicker(false)}/>}
    {showProjectLoader && <ProjectLoader onPick={id=>void loadProject(id)} onNew={requestNewProject} onClose={()=>setShowProjectLoader(false)} currentProjectId={projectId} onNotify={notify} onDeleted={id=>{ if(id===projectId){ setProjectId(null); localStorage.removeItem('slideshow.project.mock'); notify(`Project #${id} deleted — editor detached`)} }} onDeleteAll={()=>{ setProjectId(null); localStorage.removeItem('slideshow.project.mock'); setPreviewUrl(null); setShowPreview(false); setActiveJobId(null); setRendering(false); setPreviewing(false); setProgress(0); }}/>}
    {showNewProjectConfirm && <ConfirmDialog title="Start a new blank project?" message="This clears the current storyline, soundtracks and settings from the editor. Projects already saved in SQLite are not affected." confirmLabel="New project" onConfirm={startNewProject} onCancel={()=>setShowNewProjectConfirm(false)}/>}
    {showDeleteConfirm && <ConfirmDialog title="Delete selected items?" message={`Are you sure you want to delete ${selectedIds.length} selected item${selectedIds.length > 1 ? 's' : ''}? This action cannot be undone.`} confirmLabel="Delete" onConfirm={deleteSelectedItems} onCancel={()=>setShowDeleteConfirm(false)}/>}
    {showClearAllConfirm && <ConfirmDialog title="Clear all projects?" message="Are you sure you want to delete ALL saved projects and temporary files? This action cannot be undone." confirmLabel="Clear all" onConfirm={clearAllProjects} onCancel={()=>setShowClearAllConfirm(false)}/>}
    {showClearOutputConfirm && <ConfirmDialog title="Clear output directory?" message={`Are you sure you want to delete all files in ${outputPath || '/output'}? This action cannot be undone.`} confirmLabel="Clear output" onConfirm={clearOutputDirectory} onCancel={()=>setShowClearOutputConfirm(false)}/>}
    {showCleanTempConfirm && <ConfirmDialog title="Clean temporary files?" message={`This deletes every intermediate render segment, soundtrack cache and proxy preview (the work and preview folders), and clears the render history. Rendered MP4 files in ${outputPath || '/output'} and your saved projects are kept. This cannot be undone.`} confirmLabel="Clean temp files" onConfirm={cleanTempFiles} onCancel={()=>setShowCleanTempConfirm(false)}/>}
    {overwritePath && <ConfirmDialog title="Output file already exists" message={`${overwritePath} already exists. Rendering again will replace it with the new video.`} confirmLabel="Overwrite & render" onConfirm={()=>{const path=overwritePath;setOverwritePath(null);void startJob('render',true)}} onCancel={()=>setOverwritePath(null)}/>}
    {previewedItem && <MediaLightbox title={previewedItem.name} src={itemThumbUrl(previewedItem) || ''} kind={previewedItem.type === 'video' ? 'video' : 'image'} position={`${previewIndex + 1} / ${previewItems.length}`} onPrev={previewIndex > 0 ? () => setStoryPreviewId(previewItems[previewIndex - 1].id) : undefined} onNext={previewIndex + 1 < previewItems.length ? () => setStoryPreviewId(previewItems[previewIndex + 1].id) : undefined} onDelete={deletePreviewedItem} rotation={previewedItem.rotation} onRotate={previewedItem.type === 'image' ? rotatePreviewedItem : undefined} onClose={() => setStoryPreviewId(null)} />}
    {toast && <div className="toast"><Check size={16}/>{toast}</div>}
  </div>
}

function TypeControls({ fontFamily, setFontFamily, fontSize, setFontSize, fontColor, setFontColor, bold, setBold, italic, setItalic, underline, setUnderline, sample }: {
  fontFamily: string; setFontFamily: (v: string) => void;
  fontSize: number; setFontSize: (v: number) => void;
  fontColor: string; setFontColor: (v: string) => void;
  bold: boolean; setBold: (v: boolean) => void;
  italic: boolean; setItalic: (v: boolean) => void;
  underline: boolean; setUnderline: (v: boolean) => void;
  sample?: string;
}) {
  return <div className="type-controls-stack">
    <div><FieldLabel>Font family</FieldLabel><Select value={fontFamily} onChange={setFontFamily}>{Object.entries(FONT_GROUPS).map(([group, names]) => <optgroup key={group} label={group}>{names.map(f => <option key={f} value={f}>{f}</option>)}</optgroup>)}</Select><div className="font-sample" style={{ fontFamily: `'${fontFamily}', sans-serif`, fontWeight: bold ? 700 : 400, fontStyle: italic && !FONTS_WITHOUT_ITALIC.has(fontFamily) ? 'italic' : 'normal', textDecoration: underline ? 'underline' : 'none' }} title="Live sample in the selected font">{sample || FONT_SAMPLE}</div></div>
    <div><FieldLabel>Font size</FieldLabel><NumberStepper value={fontSize} min={8} max={200} step={1} suffix="px" ariaLabel="Font size" onChange={setFontSize} /></div>
    <div><FieldLabel>Text colour</FieldLabel><div className="color-control"><input type="color" value={fontColor.startsWith('#') ? fontColor : '#ffffff'} onChange={e => setFontColor(e.target.value)}/><span>{fontColor.toUpperCase()}</span></div></div>
    <div><FieldLabel>Formatting</FieldLabel><div className="style-buttons"><button type="button" className={bold ? 'active' : ''} onClick={() => setBold(!bold)}><b>B</b></button><button type="button" className={italic && !FONTS_WITHOUT_ITALIC.has(fontFamily) ? 'active' : ''} disabled={FONTS_WITHOUT_ITALIC.has(fontFamily)} title={FONTS_WITHOUT_ITALIC.has(fontFamily) ? `${fontFamily} has no italic style` : 'Italic'} onClick={() => setItalic(!italic)}><i>I</i></button><button type="button" className={underline ? 'active' : ''} onClick={() => setUnderline(!underline)}><u>U</u></button></div></div>
  </div>
}

// Compact loudness readout for a soundtrack row: measured LUFS, a bar on a
// −30…−5 scale, the target marker, and the gain normalisation will apply.
function LoudnessMeter({ loudness, target, normalized }: { loudness: number; target: number; normalized: boolean }) {
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v + 30) / 25 * 100))}%`
  const delta = target - loudness
  const tone = Math.abs(delta) < 1.5 ? 'ok' : delta > 0 ? 'quiet' : 'loud'
  return <span className={`loudness-meter ${tone}`} title={`Measured ${loudness.toFixed(1)} LUFS · target ${target} LUFS${normalized ? ` · normalisation will apply ${delta >= 0 ? '+' : ''}${delta.toFixed(1)} dB` : ''}`}>
    <b>{loudness.toFixed(1)} LUFS</b>
    <span className="loudness-bar"><i style={{ width: pct(loudness) }} /><u style={{ left: pct(target) }} /></span>
    {normalized ? <small>{delta >= 0 ? '+' : ''}{delta.toFixed(1)} dB</small> : <small>{tone === 'ok' ? 'on target' : tone === 'quiet' ? 'quieter' : 'louder'}</small>}
  </span>
}

// The "01" badge on a storyline item, editable: click, type a slot number
// (↑/↓ also work) and press Enter to push the item there. Esc cancels.
function PositionBadge({ index, count, onMove, className = '' }: { index: number; count: number; onMove: (position: number) => void; className?: string }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(String(index + 1))
  useEffect(() => { if (!editing) setText(String(index + 1)) }, [index, editing])
  const commit = () => { const v = Number(text); setEditing(false); if (Number.isFinite(v) && v >= 1 && Math.round(v) !== index + 1) onMove(v) }
  if (!editing) return <b className={`position-badge ${className}`} title={`Position ${index + 1} of ${count} · click to move to another position`} onClick={e => { e.stopPropagation(); setEditing(true) }} onPointerDown={e => e.stopPropagation()}>{String(index + 1).padStart(2, '0')}</b>
  return <input className={`position-input ${className}`} autoFocus type="number" min={1} max={count} value={text} aria-label="Move to position" title={`Enter a position 1–${count} and press Enter`} onChange={e => setText(e.target.value)} onFocus={e => e.target.select()} onBlur={commit} onClick={e => e.stopPropagation()} onPointerDown={e => e.stopPropagation()} draggable={false} onKeyDown={e => { e.stopPropagation(); if (e.key === 'Enter') { e.preventDefault(); commit() } else if (e.key === 'Escape') { setText(String(index + 1)); setEditing(false) } }} />
}

// m:ss.s text field that commits on blur/Enter (module-level so it keeps its
// draft text while the editor re-renders on every playback tick).
function TimeField({ label, value, onCommit, min, max }: { label: string; value: number; onCommit: (v: number) => void; min: number; max: number }) {
  const [text, setText] = useState(formatClockPrecise(value))
  useEffect(() => setText(formatClockPrecise(value)), [value])
  const commit = () => { const v = parseClock(text); if (Number.isFinite(v) && text.trim()) onCommit(Math.min(max, Math.max(min, v))); else setText(formatClockPrecise(value)) }
  return <label className="time-field"><span>{label}</span><input value={text} onChange={e => setText(e.target.value)} onBlur={commit} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLInputElement).blur() } }} /></label>
}

// Popup editor for one soundtrack: drag IN/OUT handles on a large waveform to
// cut/crop, drag the fade corners (or use the sliders) for fade in/out, and
// preview only the kept region. Everything is stored on the track in seconds;
// the renderer applies the same trim + afade.
function SoundtrackEditor({ track, onChange, onClose }: { track: AudioTrack; onChange: (change: Partial<AudioTrack>) => void; onClose: () => void }) {
  const src = mediaFileUrl('music', mediaItemPath(track))
  const [fileSeconds, setFileSeconds] = useState(trackSourceSeconds(track))
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [drag, setDrag] = useState<null | 'in' | 'out' | 'fadeIn' | 'fadeOut' | 'seek'>(null)
  const playerRef = useRef<HTMLAudioElement | null>(null)
  const stripRef = useRef<HTMLDivElement | null>(null)
  const original = useRef<Partial<AudioTrack>>({ trimStart: track.trimStart, trimEnd: track.trimEnd, fadeIn: track.fadeIn, fadeOut: track.fadeOut })
  const total = fileSeconds
  const start = Math.max(0, Math.min(Number(track.trimStart) || 0, total))
  const end = Math.min(total, (Number(track.trimEnd) || 0) > 0 ? Number(track.trimEnd) : total)
  const kept = Math.max(0, end - start)
  const fadeIn = Math.min(Number(track.fadeIn) || 0, kept)
  const fadeOut = Math.min(Number(track.fadeOut) || 0, kept)
  const MIN_KEEP = 1

  // Audio element for previewing the kept region.
  useEffect(() => {
    const player = new Audio(src)
    playerRef.current = player
    player.preload = 'metadata'
    const sync = () => { if (Number.isFinite(player.duration) && player.duration > 0) setFileSeconds(player.duration) }
    player.onloadedmetadata = sync; player.ondurationchange = sync
    player.ontimeupdate = () => setPosition(player.currentTime)
    player.onended = () => setPlaying(false)
    return () => { player.pause(); playerRef.current = null }
  }, [src])
  // Stop at OUT point; apply live gain so the fades are audible in the preview.
  useEffect(() => {
    const player = playerRef.current
    if (!player) return
    if (position >= end - 0.02 && playing) { player.pause(); setPlaying(false); player.currentTime = start; setPosition(start) }
    const t = position - start
    let gain = 1
    if (fadeIn > 0 && t < fadeIn) gain = Math.max(0, t / fadeIn)
    if (fadeOut > 0 && (end - position) < fadeOut) gain = Math.min(gain, Math.max(0, (end - position) / fadeOut))
    player.volume = Math.min(1, Math.max(0, gain))
  }, [position, start, end, fadeIn, fadeOut, playing])
  // Persist the file length so the storyline total stays right once known.
  useEffect(() => { if (total > 0 && Math.abs(parseClock(track.duration) - total) > 0.5) onChange({ duration: formatClock(total) }) }, [total])  // eslint-disable-line react-hooks/exhaustive-deps

  const togglePlay = () => {
    const player = playerRef.current; if (!player) return
    if (playing) { player.pause(); setPlaying(false); return }
    if (position < start || position >= end - 0.05) player.currentTime = start
    player.play().then(() => setPlaying(true)).catch(() => setPlaying(false))
  }
  const seekTo = (seconds: number) => { const p = playerRef.current; const v = Math.min(Math.max(seconds, start), Math.max(start, end - 0.05)); if (p) p.currentTime = v; setPosition(v) }
  const secondsFromEvent = (event: React.PointerEvent | PointerEvent) => {
    const rect = stripRef.current?.getBoundingClientRect(); if (!rect || !total) return 0
    return Math.min(total, Math.max(0, (event.clientX - rect.left) / rect.width * total))
  }
  const round = (v: number) => Math.round(v * 10) / 10
  const setIn = (v: number) => onChange({ trimStart: round(Math.min(Math.max(0, v), end - MIN_KEEP)) })
  const setOut = (v: number) => onChange({ trimEnd: round(Math.max(Math.min(total, v), start + MIN_KEEP)) })
  const setFadeIn = (v: number) => onChange({ fadeIn: round(Math.min(Math.max(0, v), Math.max(0, kept - fadeOut))) })
  const setFadeOut = (v: number) => onChange({ fadeOut: round(Math.min(Math.max(0, v), Math.max(0, kept - fadeIn))) })
  const onStripDown = (kind: NonNullable<typeof drag>) => (e: React.PointerEvent) => {
    e.preventDefault(); e.stopPropagation(); (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); setDrag(kind)
    applyDrag(kind, secondsFromEvent(e))
  }
  const applyDrag = (kind: NonNullable<typeof drag>, seconds: number) => {
    if (kind === 'in') setIn(seconds)
    else if (kind === 'out') setOut(seconds)
    else if (kind === 'fadeIn') setFadeIn(seconds - start)
    else if (kind === 'fadeOut') setFadeOut(end - seconds)
    else seekTo(seconds)
  }
  useEffect(() => {
    if (!drag) return
    const move = (e: PointerEvent) => applyDrag(drag, secondsFromEvent(e))
    const up = () => setDrag(null)
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up)
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up) }
  })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); else if (e.key === ' ' && (e.target as HTMLElement)?.tagName !== 'INPUT') { e.preventDefault(); togglePlay() } }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  })
  const pct = (v: number) => total > 0 ? `${Math.min(100, Math.max(0, v / total * 100))}%` : '0%'
  const bars = 120
  const cancel = () => { onChange(original.current); onClose() }
  const reset = () => onChange({ trimStart: 0, trimEnd: total, fadeIn: 0, fadeOut: 0 })
  return <div className="modal-backdrop dark-backdrop" onMouseDown={cancel}><div className="soundtrack-editor" onMouseDown={e => e.stopPropagation()}>
    <div className="preview-top"><div><strong>{track.name}</strong><span>SOUNDTRACK EDITOR · CUT, CROP &amp; FADE</span></div><button type="button" onClick={cancel} aria-label="Close editor"><X size={20}/></button></div>
    <div className="editor-body">
      <div className="editor-strip-wrap">
        <div className="ruler"><span>0:00</span><span>{formatClock(total / 4)}</span><span>{formatClock(total / 2)}</span><span>{formatClock(total * 3 / 4)}</span><span>{formatClock(total)}</span></div>
        <div ref={stripRef} className={`editor-strip ${drag ? 'dragging' : ''}`} onPointerDown={onStripDown('seek')}>
          <div className="strip-bars">{Array.from({ length: bars }).map((_, i) => { const at = (i + 0.5) / bars * total; const inKeep = at >= start && at <= end; return <i key={i} style={{ height: `${18 + ((i * 29 + track.id * 7) % 61)}%`, background: track.color, opacity: inKeep ? (at <= position ? 1 : 0.7) : 0.18 }} /> })}</div>
          <div className="cut-shade left" style={{ width: pct(start) }} />
          <div className="cut-shade right" style={{ left: pct(end) }} />
          {fadeIn > 0 && <div className="fade-ramp in" style={{ left: pct(start), width: pct(fadeIn) }} />}
          {fadeOut > 0 && <div className="fade-ramp out" style={{ left: pct(end - fadeOut), width: pct(fadeOut) }} />}
          <div className="playhead" style={{ left: pct(position) }} />
          <button type="button" className="trim-handle in" style={{ left: pct(start) }} title={`IN · ${formatClockPrecise(start)} · drag to set the start`} onPointerDown={onStripDown('in')}><ChevronRight size={12}/></button>
          <button type="button" className="trim-handle out" style={{ left: pct(end) }} title={`OUT · ${formatClockPrecise(end)} · drag to set the end`} onPointerDown={onStripDown('out')}><ChevronLeft size={12}/></button>
          <button type="button" className="fade-handle in" style={{ left: pct(start + fadeIn) }} title={`Fade in ${fadeIn.toFixed(1)}s · drag to change`} onPointerDown={onStripDown('fadeIn')} />
          <button type="button" className="fade-handle out" style={{ left: pct(end - fadeOut) }} title={`Fade out ${fadeOut.toFixed(1)}s · drag to change`} onPointerDown={onStripDown('fadeOut')} />
        </div>
        <div className="strip-legend"><span><i className="swatch keep" /> kept · {formatClockPrecise(kept)}</span><span><i className="swatch cut" /> cut · {formatClockPrecise(Math.max(0, total - kept))}</span><span><i className="swatch ramp" /> fade ramps</span></div>
      </div>
      <div className="editor-controls">
        <div className="transport"><button type="button" className={`btn ${playing ? 'dark' : 'soft'}`} onClick={togglePlay} disabled={!total}>{playing ? <Pause size={15}/> : <Play size={15}/>} {playing ? 'Pause' : 'Play kept region'}</button><button type="button" className="btn ghost" onClick={() => seekTo(start)} title="Jump to IN"><ChevronLeft size={14}/> IN</button><button type="button" className="btn ghost" onClick={() => seekTo(Math.max(start, end - 5))} title="Jump to 5 s before OUT">OUT <ChevronRight size={14}/></button><AudioTimeReadout current={Math.max(0, position - start)} duration={kept} /></div>
        <div className="editor-fields">
          <TimeField label="Start (IN)" value={start} min={0} max={end - MIN_KEEP} onCommit={setIn} />
          <TimeField label="End (OUT)" value={end} min={start + MIN_KEEP} max={total} onCommit={setOut} />
          <div className="time-field static"><span>Kept length</span><b>{formatClockPrecise(kept)}</b></div>
          <div className="time-field static"><span>File length</span><b>{formatClockPrecise(total)}</b></div>
        </div>
        <div className="editor-fades">
          <div><FieldLabel>Fade in <span>{fadeIn.toFixed(1)}s</span></FieldLabel><input className="range" type="range" min={0} max={10} step={0.1} value={fadeIn} onChange={e => setFadeIn(Number(e.target.value))} /></div>
          <div><FieldLabel>Fade out <span>{fadeOut.toFixed(1)}s</span></FieldLabel><input className="range" type="range" min={0} max={10} step={0.1} value={fadeOut} onChange={e => setFadeOut(Number(e.target.value))} /></div>
        </div>
        <p className="editor-note"><Info size={13}/> Drag the green handles to cut the start and end; drag the small round handles to lengthen the fade ramps. Only the kept region counts toward the soundtrack length and is rendered.</p>
      </div>
    </div>
    <div className="modal-foot"><span>Kept {formatClockPrecise(kept)} of {formatClockPrecise(total)}</span><button className="btn ghost" onClick={reset}>Reset</button><button className="btn ghost" onClick={cancel}>Cancel</button><button className="btn dark" onClick={onClose}><Check size={15}/> Done</button></div>
  </div></div>
}

function TextStyleModal({fontFamily,setFontFamily,fontSize,setFontSize,fontColor,setFontColor,bold,setBold,italic,setItalic,underline,setUnderline,textX=50,setTextX,textY=72,setTextY,onClose}: any) {
  return <div className="modal-backdrop" onMouseDown={onClose}><div className="text-style-modal wide-style-modal" onMouseDown={e=>e.stopPropagation()}>
    <div className="modal-head"><div><span className="eyebrow">PROJECT DEFAULTS</span><h2>Default text style</h2></div><button className="icon-button" onClick={onClose}><X size={19}/></button></div>
    <div className="style-modal-body">
      <p>These defaults apply to captions drawn on photos and videos. Standalone text frames keep their own font, size and position.</p>
      <TypeControls fontFamily={fontFamily} setFontFamily={setFontFamily} fontSize={Number(fontSize) || 48} setFontSize={v => setFontSize(String(v))} fontColor={fontColor} setFontColor={setFontColor} bold={bold} setBold={setBold} italic={italic} setItalic={setItalic} underline={underline} setUnderline={setUnderline} />
      <div className="frame-canvas default-position-stage" style={{background:'#30362d'}}>
        <div className="draggable-title" onPointerDown={e => dragOnStage(e, (x, y) => { setTextX(x); setTextY(y) })} style={{left:`${textX}%`,top:`${textY}%`,fontFamily,fontSize:`${Math.min(Number(fontSize)||48,54)}px`,color:fontColor,fontWeight:bold?700:400,fontStyle:italic?'italic':'normal',textDecoration:underline?'underline':'none'}}>
          <Move size={14}/><span>Summer, slowly.</span>
        </div>
      </div>
      <div className="position-readout light-readout"><Move size={14}/><span>Default position</span><strong>X {Math.round(textX)}% · Y {Math.round(textY)}%</strong></div>
    </div>
    <div className="modal-foot"><span>Changes apply to new text</span><button className="btn ghost" onClick={() => { setTextX(50); setTextY(72) }}>Reset position</button><button className="btn dark" onClick={onClose}><Check size={15}/> Save defaults</button></div>
  </div></div>
}

// Loops the A→B background change inside the editor canvas using a CSS
// approximation of the chosen xfade (fade / wipe direction / circle). The
// exact look comes from the FFmpeg preview or render.
function ColourChangePreview({ change, playing }: { change: NonNullable<ReturnType<typeof frameColourChange>>; playing: boolean }) {
  const name = useMemo(() => `bgchange${Math.random().toString(36).slice(2, 8)}`, [])
  const cls = quickTransitionClass(change.transition)
  const p0 = Math.max(0, Math.min(100, change.start / change.hold * 100))
  const p1 = Math.max(p0, Math.min(100, (change.start + change.time) / change.hold * 100))
  const hidden = cls === 'from-left' ? 'transform:translateX(-100%)' : cls === 'from-right' ? 'transform:translateX(100%)' : cls === 'from-up' ? 'transform:translateY(-100%)' : cls === 'from-down' ? 'transform:translateY(100%)' : cls === 'from-circle' ? 'clip-path:circle(0% at 50% 50%)' : 'opacity:0'
  const shown = cls === 'from-circle' ? 'clip-path:circle(75% at 50% 50%)' : cls === 'fade' ? 'opacity:1' : 'transform:none'
  const css = `@keyframes ${name}{0%,${p0.toFixed(2)}%{${hidden}}${p1.toFixed(2)}%,100%{${shown}}}`
  return <>
    <style>{css}</style>
    <div className="bg-change-layer" style={{ background: change.to, animationName: name, animationDuration: `${change.hold}s`, animationIterationCount: 'infinite', animationTimingFunction: 'linear', animationPlayState: playing ? 'running' : 'paused' }} />
  </>
}

function TextFrameEditor({item,update,onSave,onCancel,isNew=false}:{item:MediaItem,update:(c:Partial<MediaItem>)=>void,onSave:()=>void,onCancel:()=>void,isNew?:boolean}) {
  const original = useRef(item)
  // Existing frames: Cancel restores the values from before the editor opened.
  // New frames: Cancel removes the frame entirely (handled by the caller).
  const cancel = () => { if (!isNew) update(original.current); onCancel() }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') cancel() }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  })
  // Title cards keep their own type settings. Do not inherit live project
  // defaults — those apply only to captions drawn on pictures.
  const family = item.fontFamily || 'Montserrat'
  const size = item.fontSize ?? 48
  const color = item.fontColor || '#ffffff'
  const bold = item.textBold ?? true
  const italic = item.textItalic ?? false
  const underline = item.textUnderline ?? false
  // Keep swatches to real colours: FFmpeg can render these exactly in the MP4.
  const backgrounds=['#30382a','#14213d','#6f4238','#37474f','#5b285f','#163c44']
  const change = frameColourChange(item)
  const sameAsA = !isHex(item.frameBackground2)
  const colourB = item.frameBackground2 || item.frameBackground
  const [bgPlaying, setBgPlaying] = useState(true)
  return <div className="modal-backdrop dark-backdrop"><div className="frame-editor">
    <div className="preview-top"><div><strong>{isNew ? 'New text frame' : 'Text frame editor'}</strong><span>DRAG THE TEXT TO POSITION IT</span></div><button onClick={cancel} title={isNew ? 'Discard this text frame' : 'Cancel changes'}><X size={20}/></button></div>
    <div className="frame-editor-body">
      <div className="frame-canvas" style={{background:item.frameBackground}}>
        {change && <ColourChangePreview key={`${change.from}-${change.to}-${change.transition}-${change.time}-${change.start}-${change.hold}`} change={change} playing={bgPlaying} />}
        <div className="draggable-title" onPointerDown={e => dragOnStage(e, (x, y) => update({textX:x,textY:y}))} style={{left:`${item.textX}%`,top:`${item.textY}%`,fontFamily:`'${family}', sans-serif`,fontSize:`${Math.min(size,54)}px`,color,fontWeight:bold?700:400,fontStyle:italic?'italic':'normal',textDecoration:underline?'underline':'none'}}>
          <Move size={14}/><span>{item.text}</span>
        </div>
      </div>
      <aside>
        <div><FieldLabel>Frame text</FieldLabel><textarea value={item.text} onChange={e=>update({text:e.target.value})}/></div>
        <TypeControls fontFamily={family} setFontFamily={v => update({fontFamily:v})} fontSize={size} setFontSize={v => update({fontSize:v})} fontColor={color} setFontColor={v => update({fontColor:v})} bold={bold} setBold={v => update({textBold:v})} italic={italic} setItalic={v => update({textItalic:v})} underline={underline} setUnderline={v => update({textUnderline:v})} sample={item.text} />
        <div className="bg-columns">
          <div><FieldLabel>Colour A</FieldLabel><div className="background-swatches">{backgrounds.map(bg=><button key={bg} className={item.frameBackground===bg?'active':''} style={{background:bg}} onClick={()=>update({frameBackground:bg})}/>)}</div><div className="custom-bg"><Palette size={14}/><span>Custom</span><input type="color" value={isHex(item.frameBackground)?item.frameBackground:'#30382a'} onChange={e=>update({frameBackground:e.target.value})}/></div></div>
          <div className={sameAsA?'dimmed':''}><FieldLabel>Colour B</FieldLabel><div className="background-swatches">{backgrounds.map(bg=><button key={bg} disabled={sameAsA} className={colourB===bg?'active':''} style={{background:bg}} onClick={()=>update({frameBackground2:bg})}/>)}</div><div className="custom-bg"><Palette size={14}/><span>Custom</span><input type="color" disabled={sameAsA} value={isHex(colourB)?colourB:'#30382a'} onChange={e=>update({frameBackground2:e.target.value})}/></div><label className="check-label dark"><input type="checkbox" checked={sameAsA} onChange={e=>update(e.target.checked?{frameBackground2:undefined}:{frameBackground2:backgrounds.find(b=>b!==item.frameBackground)||'#14213d',frameTransition:item.frameTransition||'Fade',frameTransitionTime:item.frameTransitionTime||1,frameTransitionStart:item.frameTransitionStart??Math.max(0,(item.duration-1)/2)})}/><span><Check size={11}/></span>Same as A</label></div>
        </div>
        {change && <div className="bg-transition">
          <div className="ab-chip"><i style={{background:change.from}}/><ChevronRight size={12}/><i style={{background:change.to}}/><span>{change.transition} · starts {change.start.toFixed(1)}s · {change.time.toFixed(1)}s</span><button type="button" className={`icon-button ${bgPlaying?'playing':''}`} title={bgPlaying?'Pause preview':'Play the colour change'} onClick={()=>setBgPlaying(p=>!p)}>{bgPlaying?<Pause size={13}/>:<Play size={13}/>}</button></div>
          <div><FieldLabel>Transition A → B</FieldLabel><Select value={change.transition} onChange={v=>update({frameTransition:v})}>{Object.entries(transitionGroups).map(([group,names])=><optgroup key={group} label={group}>{names.map(name=><option key={name}>{name}</option>)}</optgroup>)}</Select></div>
          <div><FieldLabel>Start at <span>{change.start.toFixed(1)}s</span></FieldLabel><input className="range" type="range" min={0} max={Math.max(0,item.duration-change.time)} step={0.1} value={change.start} onChange={e=>update({frameTransitionStart:Number(e.target.value)})}/></div>
          <div><FieldLabel>Duration <span>{change.time.toFixed(1)}s</span></FieldLabel><input className="range" type="range" min={0.2} max={item.duration} step={0.1} value={change.time} onChange={e=>{const t=Number(e.target.value);update({frameTransitionTime:t,frameTransitionStart:Math.min(change.start,Math.max(0,item.duration-t))})}}/></div>
          <div className="bg-timeline" title="Frame timeline: A · transition · B"><i style={{background:change.from,flex:change.start}}/><i className="mix" style={{background:`linear-gradient(90deg,${change.from},${change.to})`,flex:change.time}}/><i style={{background:change.to,flex:Math.max(0,change.hold-change.start-change.time)}}/></div>
        </div>}
        <div className="position-readout"><Move size={14}/><span>Position</span><strong>X {Math.round(item.textX)}% · Y {Math.round(item.textY)}%</strong></div>
        <p><Info size={13}/> Drag the title on the preview. Choose font, size and weight in the sidebar.</p>
      </aside>
    </div>
    <div className="modal-foot"><span>Frame duration: {item.duration}s</span><button className="btn ghost" onClick={()=>update({textX:50,textY:50})}>Reset position</button><button className="btn ghost" onClick={cancel}>{isNew ? 'Discard' : 'Cancel'}</button><button className="btn dark" onClick={onSave}><Check size={15}/> {isNew ? 'Add to storyline' : 'Save'}</button></div>
  </div></div>
}

function MediaBrowser({ onClose, onAdd, audioOnly=false }: { onClose: () => void, onAdd: (files:any[]) => void, audioOnly?:boolean }) {
  const [root,setRoot]=useState<MediaRoot>(audioOnly?'music':'photos')
  // "All media" lists the photos and videos mounts together, so pictures and
  // videos can be mixed freely no matter which location button is active.
  const [allMedia,setAllMedia]=useState(!audioOnly)
  const [path,setPath]=useState('');const [entries,setEntries]=useState<any[]>([]);const [selected,setSelected]=useState<any[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
  const [lightbox,setLightbox]=useState<LightboxTarget|null>(null)
  const preview=useAudioPreview(message=>setError(message))
  useEffect(()=>{
    let cancelled=false
    setLoading(true);setError('')
    // Each entry remembers the mount it was read from (rootName) so files
    // keep working when both mounts are listed side by side.
    const roots:MediaRoot[]=allMedia&&!audioOnly?['photos','videos']:[root]
    type BrowseResult={root:MediaRoot,entries:any[]}|{root:MediaRoot,error:string}
    void Promise.all(roots.map(current=>fetch(`/api/media/browse?root=${current}&path=${encodeMediaRelative(path)}`)
      .then(async r=>{if(!r.ok)throw new Error(await readApiError(r,'Could not open folder'));return r.json()})
      .then((data:any)=>({root:current,entries:((data.entries||[]) as any[]).map(entry=>({...entry,rootName:current}))} as BrowseResult))
      .catch((e:unknown)=>({root:current,error:e instanceof Error?e.message:'Could not open folder'} as BrowseResult))
    )).then(results=>{
      if(cancelled)return
      const loaded=results.filter((r): r is Extract<BrowseResult,{entries:any[]}> => !('error' in r))
      const failedRoots=results.filter(r=>'error' in r).map(r=>r.root)
      // A subfolder often exists in only one of the two mounts — show what
      // could be read and only fail when neither mount answered.
      if(!loaded.length){
        setEntries([])
        const first=results.find(r=>'error' in r) as {error:string}|undefined
        setError(first?.error||'Could not open folder')
        return
      }
      setEntries(mergeBrowsedEntries(loaded.flatMap(r=>r.entries)))
      if(failedRoots.length&&path)setError(`“${path}” was not found in: ${failedRoots.join(', ')} — showing the matches from ${loaded.map(r=>r.root).join(' and ')}.`)
    }).finally(()=>{if(!cancelled)setLoading(false)})
    return ()=>{cancelled=true}
  },[root,path,allMedia,audioOnly])
  const chooseRoot=(value:'photos'|'videos'|'music')=>{setAllMedia(false);setRoot(value);setPath('');setSelected([])}
  const showAllMedia=()=>{setAllMedia(true);setPath('');setSelected([])}
  // Files are streamed from the mount they really live in, never from the
  // kind of media they happen to be.
  const fileRoot=(entry:any):MediaRoot=>(entry.rootName as MediaRoot)||mediaRootFromPath(entry.path,root)
  const open=(entry:any)=>{if(entry.kind==='directory'){if(entry.accessible===false){setError(`No permission to open “${entry.name}”. The container user cannot read this folder — check DSM share/ACL permissions and the PUID/PGID in your compose file.`);return}setPath(entry.relativePath)}else setSelected(items=>items.some(x=>x.path===entry.path)?items.filter(x=>x.path!==entry.path):[...items,entry])}
  const viewFile=(entry:any)=>{
    const kind: LightboxTarget['kind'] = entry.kind==='video'?'video':entry.kind==='audio'?'audio':'image'
    setLightbox({ title: entry.name, src: mediaFileUrl(fileRoot(entry), entry.path), kind })
  }
  const skippedEmpty = selected.filter((f:any)=>f.empty).length
  const addable = selected.filter((f:any)=>!f.empty)
  return <div className="modal-backdrop" onMouseDown={onClose}><div className="browser-modal" onMouseDown={e=>e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">DOCKER-MOUNTED MEDIA</span><h2>{audioOnly?'Select MP3 soundtracks':'Select photos & videos'}</h2></div><button className="icon-button" onClick={onClose}><X size={19}/></button></div><div className="browser-body"><div className="folder-tree"><strong>LOCATIONS</strong>{audioOnly?<button className="active" onClick={()=>chooseRoot('music')}><Music2 size={16}/> music</button>:<><button className={allMedia?'active':''} onClick={showAllMedia} title="List the photos and videos mounts together — every playable file, mixed"><Film size={16}/> All media</button><button className={!allMedia&&root==='photos'?'active':''} onClick={()=>chooseRoot('photos')} title="Browse the /photos mount (photos and videos inside it)"><ImageIcon size={16}/> photos</button><button className={!allMedia&&root==='videos'?'active':''} onClick={()=>chooseRoot('videos')} title="Browse the /videos mount (videos and photos inside it)"><Video size={16}/> videos</button></>}<hr/><strong>SECURITY</strong><p>Only configured read-only mounts are accessible. Folders the container user cannot read stay listed but cannot be opened. Spaces and punctuation in file names are allowed.</p><p>All playable formats are accepted everywhere — a video found under /photos and a photo found under /videos are both added with the mount they really live in.</p></div><div className="file-area"><div className="breadcrumbs"><button disabled={!path} onClick={()=>setPath(path.split('/').slice(0,-1).join('/'))}>← Parent</button><span>/{allMedia&&!audioOnly?'photos & videos':root}/{path}</span><button onClick={()=>setSelected(entries.filter(x=>x.kind!=='directory'&&!x.empty&&x.accessible!==false))}>Select visible files</button></div>{loading&&<div className="browser-info"><RefreshCw className="spin" size={15}/> Reading mounted folder…</div>}{error&&<div className="notice amber"><AlertTriangle size={15}/><span>{error}</span></div>}<div className="file-grid">{entries.map(file=><div className={`file-card ${selected.some(x=>x.path===file.path)?'selected':''} ${file.empty?'empty':''} ${file.accessible===false?'inaccessible':''}`} key={file.path}><button type="button" className="file-thumb" onClick={()=>file.kind==='directory'?open(file):file.kind==='image'||file.kind==='video'?viewFile(file):open(file)} title={file.kind==='directory'?(file.accessible===false?'No permission to open this folder':'Open folder'):file.kind==='image'||file.kind==='video'?'View':file.name}>{file.kind==='audio'&&<span className={`audio-hover-play ${preview.playingKey===file.path?'playing':''}`} title={preview.playingKey===file.path?'Stop preview':'Play preview'} onClick={e=>{e.stopPropagation();preview.toggle(file.path,mediaFileUrl(fileRoot(file),file.path),file.name)}}>{preview.playingKey===file.path?<Pause size={14}/>:<Play size={13}/>}</span>}{file.kind==='audio'&&preview.playingKey===file.path ? <span className="card-player" onClick={e=>e.stopPropagation()}><AudioSeekBar bars={32} seed={3} color="#58703a" current={preview.progress.current} duration={preview.progress.duration} onSeek={preview.seek} className="compact"/><AudioTimeReadout current={preview.progress.current} duration={preview.progress.duration}/></span> : <BrowserThumb root={fileRoot(file)} file={file}/>}{file.empty&&<span className="empty-badge"><AlertTriangle size={10}/> EMPTY · 0 B</span>}{file.kind==='directory'&&file.accessible===false&&<span className="empty-badge"><AlertTriangle size={10}/> NO ACCESS</span>}{(file.kind==='image'||file.kind==='video')&&!file.empty&&<span className="thumb-zoom"><ZoomIn size={13}/></span>}{selected.some(x=>x.path===file.path)&&<span className="selected-check"><Check size={13}/></span>}</button><button type="button" className="file-card-meta" onClick={()=>file.empty?undefined:open(file)}><strong>{file.name}</strong><small>{file.kind==='directory'?(file.accessible===false?'No permission':'Folder'):file.empty?'0 B — unreadable':`${allMedia&&!audioOnly&&file.rootName?`${file.rootName} · `:''}${(file.size/1024/1024).toFixed(1)} MB`}</small></button></div>)}</div><div className="browser-info"><Info size={15}/> Click a photo or video to preview it. Click the name to select it for the storyline — pictures and videos can be mixed freely. Empty (0-byte) files are marked and skipped automatically. File names may include spaces, dashes and punctuation.</div></div></div><div className="modal-foot"><span>{selected.length} files selected{skippedEmpty?` · ${skippedEmpty} empty file${skippedEmpty>1?'s':''} skipped`:''}</span><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn dark" disabled={!addable.length} onClick={()=>onAdd(addable)}><Plus size={15}/> Add to storyline</button></div></div>{lightbox&&<MediaLightbox title={lightbox.title} src={lightbox.src} kind={lightbox.kind} onClose={()=>setLightbox(null)}/>}</div>
}

// Pick a destination folder inside the mounted /output volume. Folders are
// browsed with the backend's folder-only mode; files are never shown.
function FolderPicker({ current, onSelect, onClose }: { current: string, onSelect: (path: string) => void, onClose: () => void }) {
  const [path,setPath]=useState(()=>current.replace(/^\/output\/?/,''));const [entries,setEntries]=useState<any[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
  useEffect(()=>{setLoading(true);setError('');fetch(`/api/media/browse?root=output&folders=true&path=${encodeURIComponent(path)}`).then(async r=>{if(!r.ok)throw new Error(await readApiError(r,'Could not open folder'));return r.json()}).then(data=>setEntries(data.entries||[])).catch(e=>{setEntries([]);setError(e.message)}).finally(()=>setLoading(false))},[path])
  const chosen=path?`/output/${path}`:'/output'
  return <div className="modal-backdrop" onMouseDown={onClose}><div className="browser-modal folder-picker" onMouseDown={e=>e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">OUTPUT DESTINATION</span><h2>Choose output folder</h2></div><button className="icon-button" onClick={onClose}><X size={19}/></button></div><div className="picker-body"><div className="breadcrumbs"><button disabled={!path} onClick={()=>setPath(path.split('/').slice(0,-1).join('/'))}>← Parent</button><span>{chosen}</span><button disabled={!path} onClick={()=>setPath('')}>Root</button></div>{loading&&<div className="browser-info"><RefreshCw className="spin" size={15}/> Reading output volume…</div>}{error&&<div className="notice amber"><AlertTriangle size={15}/><span>{error}</span></div>}<div className="file-grid">{entries.map(dir=><button className={`file-card ${dir.accessible===false?'inaccessible':''}`} key={dir.relativePath} onClick={()=>{if(dir.accessible===false){setError(`No permission to open “${dir.name}”.`);return}setPath(dir.relativePath)}}><div className="server-file-icon"><FolderOpen size={34}/></div><strong>{dir.name}</strong><small>{dir.accessible===false?'No permission':'Folder'}</small></button>)}</div>{!loading&&!error&&entries.length===0&&<div className="browser-info"><Info size={15}/> No subfolders here. Keep this folder or navigate back with “← Parent”.</div>}<div className="browser-info"><Info size={15}/> Renders are written into the selected folder on the mounted /output volume. New subfolders typed manually are created at render time.</div></div><div className="modal-foot"><span>Selected: {chosen}</span><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn dark" onClick={()=>{onSelect(chosen);onClose()}}><Check size={15}/> Use this folder</button></div></div></div>
}

// Lists projects persisted in SQLite and loads the chosen one's full config
// (media, captions, soundtrack, output, timeline) into the editor.
// Now includes a delete button per entry and a single "Delete all" control.
function ProjectLoader({ onPick, onNew, onClose, currentProjectId, onDeleted, onDeleteAll, onNotify }: {
  onPick: (id: number) => void, onNew?: () => void, onClose: () => void,
  currentProjectId?: number | null, onDeleted?: (id: number) => void, onDeleteAll?: () => void, onNotify?: (msg: string)=>void
}) {
  const [projects,setProjects]=useState<any[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
  const [deletingId,setDeletingId]=useState<number|null>(null)
  const [confirmDeleteId,setConfirmDeleteId]=useState<number|null>(null)
  const [showDeleteAllConfirm,setShowDeleteAllConfirm]=useState(false)
  const [deletingAll,setDeletingAll]=useState(false)
  const refresh=()=>{setLoading(true);setError('');fetch('/api/projects').then(async r=>{if(!r.ok)throw new Error(await r.text());return r.json()}).then(setProjects).catch(e=>setError(e.message)).finally(()=>setLoading(false))}
  useEffect(()=>{refresh()},[])
  const handleDeleteOne=async(id:number)=>{
    setDeletingId(id);setError('')
    try{
      const res=await fetch(`/api/projects/${id}`,{method:'DELETE'})
      if(!res.ok) throw new Error(await readApiError(res,'Delete failed'))
      setProjects(items=>items.filter(p=>p.id!==id))
      onNotify?.(`Project #${id} deleted`)
      onDeleted?.(id)
    }catch(e){setError(e instanceof Error?e.message:'Delete failed')}
    finally{setDeletingId(null);setConfirmDeleteId(null)}
  }
  const handleDeleteAll=async()=>{
    setDeletingAll(true);setError('')
    try{
      const res=await fetch('/api/projects',{method:'DELETE'})
      if(!res.ok) throw new Error(await readApiError(res,'Delete all failed'))
      setProjects([])
      onNotify?.('All saved projects deleted')
      onDeleteAll?.()
    }catch(e){setError(e instanceof Error?e.message:'Delete all failed')}
    finally{setDeletingAll(false);setShowDeleteAllConfirm(false)}
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><div className="browser-modal project-loader" onMouseDown={e=>e.stopPropagation()}>
    <div className="modal-head"><div><span className="eyebrow">SAVED IN SQLITE</span><h2>Load project</h2></div>
      <div className="project-loader-actions">
        {projects.length>0 && <button type="button" className="btn ghost delete-all-btn" disabled={deletingAll||loading} title="Delete every saved project" onClick={()=>setShowDeleteAllConfirm(true)}><Trash2 size={14}/> Delete all</button>}
        <button className="icon-button" onClick={onClose}><X size={19}/></button>
      </div>
    </div>
    <div className="picker-body project-list">{loading&&<div className="browser-info"><RefreshCw className="spin" size={15}/> Reading saved projects…</div>}{error&&<div className="notice amber"><AlertTriangle size={15}/><span>{error}</span></div>}{!loading&&!error&&projects.length===0&&<div className="browser-info"><Info size={15}/> No saved projects yet. Use “Save project” to store the current editor contents.</div>}
      {projects.map(p=>{
        const isDeleting=deletingId===p.id
        const isCurrent=currentProjectId!=null && p.id===currentProjectId
        return <div className={`project-entry ${isCurrent?'current':''}`} key={p.id}>
          <button className="project-row" onClick={()=>onPick(p.id)} title={isCurrent?'Currently loaded — click to reload':`Load ${p.name||`Project #${p.id}`}`}>
            <div><strong>{p.name||`Project #${p.id}`}{isCurrent&&<span className="current-badge">current</span>}</strong><span>Project #{p.id} · revision {p.revision}</span></div>
            <small>Updated {new Date(p.updated_at).toLocaleString()}</small>
            <FolderOpen size={17}/>
          </button>
          <button type="button" className="project-delete" disabled={isDeleting} title={`Delete ${p.name||`Project #${p.id}`}`} aria-label={`Delete ${p.name||`Project #${p.id}`}`} onClick={e=>{e.stopPropagation();setConfirmDeleteId(p.id)}}>{isDeleting?<RefreshCw size={14} className="spin"/>:<Trash2 size={15}/>}</button>
        </div>
      })}
    </div>
    <div className="modal-foot">
      <span>{projects.length?`${projects.length} saved project${projects.length===1?'':'s'} — click a row to load, trash to delete.`:'Loading replaces the current editor contents.'}</span>
      {projects.length>0 && <button type="button" className="btn ghost delete-all-btn foot" disabled={deletingAll||loading} onClick={()=>setShowDeleteAllConfirm(true)}>{deletingAll?<RefreshCw size={14} className="spin"/>:<Trash2 size={14}/>} Delete all</button>}
      {onNew&&<button className="btn ghost" onClick={onNew}><Plus size={15}/> New blank project</button>}
      <button className="btn ghost" onClick={onClose}>Cancel</button>
    </div>
  </div>
  {confirmDeleteId!=null && <ConfirmDialog title="Delete this project?" message={`Are you sure you want to delete “${projects.find(p=>p.id===confirmDeleteId)?.name||`Project #${confirmDeleteId}`}”? This cannot be undone.`} confirmLabel="Delete" onConfirm={()=>handleDeleteOne(confirmDeleteId)} onCancel={()=>setConfirmDeleteId(null)}/>}
  {showDeleteAllConfirm && <ConfirmDialog title="Delete all saved projects?" message={`Are you sure you want to delete all ${projects.length} saved project${projects.length===1?'':'s'}? This cannot be undone.`} confirmLabel="Delete all" onConfirm={handleDeleteAll} onCancel={()=>setShowDeleteAllConfirm(false)}/>}
  </div>
}

// Small acknowledgement dialog for destructive actions (new project, overwriting
// an existing output file). The confirm button is the deliberate choice.
function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel }: { title: string, message: string, confirmLabel: string, onConfirm: () => void, onCancel: () => void }) {
  return <div className="modal-backdrop" onMouseDown={onCancel}><div className="confirm-modal" onMouseDown={e=>e.stopPropagation()}>
    <div className="confirm-icon"><AlertTriangle size={24}/></div>
    <h2>{title}</h2>
    <p>{message}</p>
    <div className="confirm-actions"><button className="btn ghost" onClick={onCancel}>Cancel</button><button className="btn dark" onClick={onConfirm}>{confirmLabel}</button></div>
  </div></div>
}

function TransitionPreview({ outgoing, incoming, onClose, onApply }: { outgoing: MediaItem; incoming: MediaItem; onClose: () => void; onApply: (patch: Partial<MediaItem>) => void }) {
  const [choice, setChoice] = useState(outgoing.transition)
  const [duration, setDuration] = useState(outgoing.transitionTime ?? DEFAULT_TRANSITION_SECONDS)
  const [params, setParams] = useState<Record<string,string|number>>((outgoing.transitionParams as Record<string,string|number>)||{})
  const [easing, setEasing] = useState(outgoing.transitionEasing || EASING_DEFAULT)
  const [reverse, setReverse] = useState(outgoing.transitionReverse || 0)
  const [tab, setTab] = useState<'xfade'|'gl'>(isGLTransition(choice)?'gl':'xfade')
  const [accurateUrl, setAccurateUrl] = useState<string | null>(null)
  const [rendering, setRendering] = useState(false)
  const [error, setError] = useState('')
  const [loopKey, setLoopKey] = useState(0)
  // when choice changes, sync tab and reset params if needed
  useEffect(()=>{ setTab(isGLTransition(choice)?'gl':'xfade'); if(isGLTransition(choice)){ const defs=getGLParams(choice); const next:Record<string,string>={}; for(const d of defs) next[d.name]= String(params[d.name] ?? d.default); if(Object.keys(next).length) setParams(next)} }, [choice]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const controller = new AbortController()
    let objectUrl = ''
    const timer = window.setTimeout(async () => {
      setRendering(true); setError(''); setAccurateUrl(null)
      try {
        const body:any = { outgoing, incoming, transition: choice, duration, transitionParams: params, transitionEasing: easing, transitionReverse: reverse }
        const response = await fetch('/api/transitions/preview', { method: 'POST', headers: {'Content-Type':'application/json'}, signal: controller.signal, body: JSON.stringify(body) })
        if (!response.ok) throw new Error(await readApiError(response, 'Preview failed'))
        objectUrl = URL.createObjectURL(await response.blob())
        setAccurateUrl(objectUrl)
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Preview failed') }
      finally { if (!controller.signal.aborted) setRendering(false) }
    }, 500)
    return () => { window.clearTimeout(timer); controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [choice, duration, params, easing, reverse, outgoing, incoming])
  const quickClass = /left/i.test(choice) ? 'from-left' : /right/i.test(choice) ? 'from-right' : /up/i.test(choice) ? 'from-up' : /down/i.test(choice) ? 'from-down' : 'fade'
  return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}><div className="transition-preview-modal wide" onMouseDown={e=>e.stopPropagation()}>
    <div className="preview-top"><div><strong>Transition preview</strong><span>{outgoing.name} → {incoming.name}</span></div><button onClick={onClose}><X size={20}/></button></div>
    <div className="transition-preview-body"><div className="transition-preview-stage">
      {accurateUrl ? <video key={`${accurateUrl}-${loopKey}`} src={accurateUrl} controls autoPlay loop /> : <div key={`${choice}-${duration}-${loopKey}`} className={`quick-transition ${quickClass}`} style={{'--transition-speed':`${duration}s`} as React.CSSProperties}><div><MediaThumb item={outgoing}/></div><div><MediaThumb item={incoming}/></div></div>}
      <span className="preview-quality">{accurateUrl ? 'ACCURATE FFMPEG · 360P' : rendering ? 'QUICK PREVIEW · RENDERING 360P…' : 'QUICK PREVIEW'}</span>
      {error && <div className="transition-preview-error"><AlertTriangle size={15}/>{error}</div>}
      <button className="btn ghost replay-transition" onClick={()=>setLoopKey(x=>x+1)}><Play size={13}/> Replay</button>
    </div><aside>
      <div className="picker-tabs"><button className={tab==='xfade'?'active':''} onClick={()=>{setTab('xfade'); if(isGLTransition(choice)) setChoice(nativeTransitions[0])}}>XFade ({nativeTransitions.length})</button><button className={tab==='gl'?'active':''} onClick={()=>{setTab('gl'); if(!isGLTransition(choice)) setChoice(glTransitions[0])}}>GL Transitions ({glTransitions.length})</button></div>
      {tab==='xfade' ? <Select value={choice} onChange={setChoice}><NativeTransitionOptions/></Select> : <Select value={choice} onChange={setChoice}><GLTransitionOptions/></Select>}
      {isGLTransition(choice) && <div className="gl-preview-params"><FieldLabel>GL parameters — {choice}</FieldLabel><GLParamControls transition={choice} params={params} onChange={setParams}/></div>}
      <label>Duration</label><NumberStepper value={duration} min={MIN_TRANSITION_SECONDS} max={10} step={.1} suffix="s" ariaLabel="Preview transition duration" onChange={setDuration}/>
      <label>Easing</label><EasingSelect value={easing} onChange={setEasing}/>
      <label className="check-label"><input type="checkbox" checked={Boolean(reverse)} onChange={e=>setReverse(e.target.checked?1:0)}/><span><Check size={11}/></span> Reverse</label>
      <div className="transition-choice-list">{(tab==='xfade'?Object.entries(nativeTransitionGroups):Object.entries(glTransitionGroups)).map(([group, names])=><section key={group}><strong>{group}</strong>{names.map(name=><button className={choice===name?'active':''} onClick={()=>setChoice(name)} key={name}><i>{transitionSymbol(name)}</i>{name}</button>)}</section>)}</div>
    </aside></div>
    <div className="modal-foot"><span>Uses /api/transitions/preview — accurate 360p FFmpeg render with your params, easing and reverse.</span><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn dark" onClick={()=>onApply({transition: choice, transitionTime: duration, transitionParams: params, transitionEasing: easing, transitionReverse: reverse})}>Apply transition</button></div>
  </div></div>
}

function Preview({ media, projectName, previewUrl, playing, setPlaying, onClose }: { media: MediaItem[], projectName: string, previewUrl:string|null, playing: boolean, setPlaying: (x: boolean) => void, onClose: () => void }) {
  const [current, setCurrent] = useState(0)
  const [stageFailed, setStageFailed] = useState(false)

  // Advance only after the full clip duration (and for videos, after the
  // <video> element reports it has ended) so a movie is never cut short.
  useEffect(() => {
    if (!playing || media.length === 0) return
    const item = media[current]
    if (!item) return
    if (item.type === 'video') {
      // Videos advance from the onEnded handler so the complete file plays.
      return
    }
    const currentDuration = Math.max(MIN_CLIP_SECONDS, item.duration || 5) * 1000
    const timer = setTimeout(() => {
      setCurrent(c => (c + 1) % media.length)
    }, currentDuration)
    return () => clearTimeout(timer)
  }, [playing, current, media])
  useEffect(() => setStageFailed(false), [current])

  if(previewUrl)return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}><div className="preview-modal" onMouseDown={e=>e.stopPropagation()}><div className="preview-top"><div><strong>FFmpeg preview</strong><span>REAL PROXY RENDER · 854 × 480</span></div><button type="button" onClick={onClose} aria-label="Close preview"><X size={20}/></button></div><video className="real-preview-video" src={previewUrl} controls autoPlay/><div className="preview-note"><Info size={14}/> This file is streamed through the backend project API from the mounted preview volume.<a className="btn dark" href={previewUrl} download>Download preview</a></div></div></div>

  const currentItem = media[current]
  const currentUrl = currentItem ? itemThumbUrl(currentItem) : ''
  const advance = () => setCurrent(c => (c + 1) % Math.max(1, media.length))

  return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}><div className="preview-modal" onMouseDown={e=>e.stopPropagation()}><div className="preview-top"><div><strong>{projectName || 'Untitled'}</strong><span>PREVIEW · LOW RESOLUTION</span></div><button type="button" onClick={onClose} aria-label="Close preview"><X size={20}/></button></div><div className={`video-stage ${currentItem?.type === 'title' ? 'title-stage' : ''}`} style={currentItem?.type==='title'?{background:currentItem.frameBackground}:undefined}>{stageFailed ? <div className="stage-fallback"><ImageOff size={28}/><span>This file is empty or unreadable — remove or replace it.</span></div> : currentUrl ? (currentItem?.type === 'video' ? <video key={currentItem.id} className={playing ? 'slow-zoom' : ''} src={currentUrl} autoPlay={playing} muted playsInline onEnded={() => { if (playing) advance() }} onError={() => setStageFailed(true)} /> : <img className={playing ? 'slow-zoom' : ''} style={rotationStyle(currentItem?.rotation)} src={currentUrl} alt={currentItem?.name || 'Preview'} onError={() => setStageFailed(true)}/>) : null}<div className="stage-shade"/><div className="preview-caption" style={currentItem?.type==='title'?{left:`${currentItem.textX}%`,top:`${currentItem.textY}%`,bottom:'auto',transform:'translate(-50%,-50%)'}:undefined}><span>{currentItem?.textMode === 'frame' ? 'TITLE FRAME' : (projectName ? projectName.toUpperCase() : 'SLIDESHOW')}</span><strong>{currentItem && currentItem.type !== 'title' && currentItem.textEnabled === false ? '' : (currentItem?.text || '')}</strong></div><button type="button" className="stage-play" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? <Pause size={25} fill="currentColor"/> : <Play size={25} fill="currentColor"/>}</button></div><div className="preview-controls"><button type="button" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? <Pause size={17}/> : <Play size={17}/>}</button><span>{formatClock(timelineModel(media).starts[current] || 0)}</span><div className="scrubber"><i style={{width: `${media.length ? ((current + 1) / media.length * 100) : 0}%`}}/><b style={{left: `${media.length ? ((current + 1) / media.length * 100) : 0}%`}}/></div><span>{formatClock(timelineModel(media).total)}</span><Select value="720p"><option>360p</option><option>720p</option></Select></div><div className="preview-filmstrip">{media.map((m,i) => { const thumb = itemThumbUrl(m); return <button type="button" className={`${current === i ? 'active' : ''} ${m.type === 'title' ? 'title-clip' : ''}`} onClick={() => { setCurrent(i); setStageFailed(false) }} key={m.id} style={m.type==='title'?{background:m.frameBackground}:undefined}>{m.type === 'title' ? <span className="title-symbol">T</span> : <MediaThumb item={m} />}<span>{i+1}</span></button> })}</div><div className="preview-note"><Info size={14}/> Videos play to the end before the next picture. Preview approximates effects; the final render may differ slightly.<button type="button" className="btn dark" onClick={onClose}>Done</button></div></div></div>
}

function RenderQueue({ projectId,onBack }: { projectId:number|null,onBack: () => void }) {
  const [jobs,setJobs]=useState<any[]>([])
  useEffect(()=>{let active=true;const load=()=>fetch(`/api/jobs${projectId?`?project_id=${projectId}`:''}`).then(r=>r.ok?r.json():[]).then(x=>active&&setJobs(x)).catch(()=>{});load();const timer=setInterval(load,2000);return()=>{active=false;clearInterval(timer)}},[projectId])
  const stopJob = (id: string) => { void fetch(`/api/jobs/${id}/cancel`, { method: 'POST' }) }
  const live = (status: string) => ['queued', 'running', 'cancelling'].includes(status)
  return <main className="queue-page"><div className="project-heading"><div><div className="eyebrow">ACTIVITY</div><h1>Render queue</h1><p>FFmpeg jobs and diagnostic history persisted in SQLite.</p></div><button className="btn dark" onClick={onBack}><Plus size={16}/> Back to editor</button></div>{jobs.length===0&&<div className="notice"><Info size={16}/><span>No render jobs yet. Save the project, then generate a preview or MP4.</span></div>}{jobs.map(job=>{
    const failed = job.status==='failed'
    const cancelled = job.status==='cancelled'
    return <section className={`panel queue-card ${failed?'is-failed':''} ${cancelled?'is-cancelled':''} ${live(job.status)?'is-live':''}`} key={job.id}>
      <div className="queue-thumb"><img src="/media/coast.jpg"/><span>{live(job.status)?<RefreshCw className="spin" size={15}/>:failed?<AlertTriangle size={15}/>:<Download size={15}/>}</span></div>
      <div><strong>{job.kind==='preview'?'Proxy preview':'MP4 render'} · {job.id.slice(0,8)}</strong><p>{job.stage} · {Math.round(job.progress)}%</p><small>{new Date(job.created_at).toLocaleString()}{job.error_message?` · ${job.error_message}`:''}</small></div>
      <span className={`status-pill ${job.status}`}>{failed||cancelled?<AlertTriangle size={13}/>:<Check size={13}/>} {job.status}</span>
      {live(job.status)
        ? <button type="button" className="btn soft stop-job" disabled={job.status==='cancelling'} onClick={()=>stopJob(job.id)}><Square size={13} fill="currentColor"/> {job.status==='cancelling'?'Stopping…':'Stop'}</button>
        : job.output_path
          ? <a className="btn soft" href={`/api/jobs/${job.id}/file`} download><Download size={15}/> Download</a>
          : <a className="btn soft" href={`/api/jobs/${job.id}/log`} target="_blank">View log</a>}
    </section>
  })}</main>
}

export default App
