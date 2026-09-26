import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  Activity, AlertTriangle, ArrowDown, ArrowUp, Check, ChevronDown, ChevronLeft, ChevronRight, CircleHelp,
  Clock3, Cpu, Download, Eraser, Eye, EyeOff, Film, FolderOpen, GripVertical, Image as ImageIcon,
  ImageOff, Info, LayoutGrid, List, ListVideo, Music2, Pause, Pencil, Play, Plus, RefreshCw, RotateCcw, RotateCw, Save,
  Scissors, Settings2, Shuffle, Sparkles, Square, Trash2, Video, X, Zap, ZoomIn, ZoomOut, Type, Move, Palette,
  Timer, HardDrive, Crop as CropIcon, FileJson, Upload, HardDriveUpload, PanelRight, PanelBottom, FolderUp, FolderPlus,
  Route,
} from 'lucide-react'
import { FieldLabel, Select, TimeField } from './ui'
import { formatClock, formatClockPrecise, formatTimecode, parseClock } from './time'
import {
  clearRenderRates, estimateRenderSeconds, formatEstimate, loadRenderRates, nextEtaSample, remainingSeconds, saveRenderRate,
} from './renderEstimate'
import type { EtaSample, JobKind, RenderRate } from './renderEstimate'
import type { MediaItem } from './mediaItem'
import { MovieEditor, movieIsTrimmed, movieKeptLabel } from './MovieEditor'
import { FILMSTRIP_CELLS, captureFilmstrip, movieFilmstripUrl, moviePreviewUrl, serverMovieDuration } from './filmstrip'
import { PictureLookDefs, PictureLookEditor } from './PictureLookEditor'
import { CropSpriteVideo } from './PictureCropEditor'
import { LOOK_GROUPS, LOOK_PRESETS, hasLook, lookLabel, lookSummary, pictureFilterStyle, type Lookish } from './pictureFilters'
import { cropLabel, cropSummary, hasCrop, type CropRect } from './pictureCrop'
import { useCroppedSource } from './usePictureCrop'
import { usePictureLook } from './usePictureLook'
import { FILENAME_FALLBACK, isGeneratedFilename, safeFilename } from './projectName'
import { ProjectFileBrowser, ProjectFilePanel } from './ProjectFileBrowser'
import type { ProjectFileInfo, ProjectRoot } from './projectFiles'
import { TextMotionPathEditor } from './TextMotionPathEditor'
import { backdropBlurPx, useFrameScale } from './useFrameScale'
import { TransitionGallery } from './TransitionGallery'
import { totalTransitionCount } from './transitionCatalog'
import { TransitionChip } from './TransitionPicker'
import { normalizeTextEffect } from './textEffects'
import {
  EFFECTS, PRESETS, defaultTextFx, hasMotionPath, laneChip, layersOf, legacyToStack, migrateLegacyTextFx, newLayerId, normalizeTextFx, withIds,
  type MotionPreset, type TextFxLayer,
} from './textFx'
import { TextMotionEditor, TextMotionTimeline, presetForRandom } from './TextMotionEditor'
import { TextEffectBrowser, type BrowserCaption } from './TextEffectBrowser'
import { MotionStage, useMotionClock } from './TextMotionStage'
import { FRAME_H, FRAME_W, type Prepared, type SceneInput } from './textMotionScene'
import type { BgChange } from './textMotionCore'
import { FontPicker } from './FontPicker'
import { FONT_GROUPS, FONTS_WITHOUT_BOLD, FONTS_WITHOUT_ITALIC, fontStack } from './fonts'
import { EasingSelect, GLParamControls, RandomScopeSelect, pickRandomTransition, randomScopeLabels, randomTransitionSettings } from './transitionControls'
import type { RandomScope } from './transitionControls'
import { EASING_DEFAULT, getGLParams, isGLTransition, transitionPreviewUrl, transitionSymbol } from './transitionCatalog'
import { uploadFile, isUploadableFile, type UploadItem, type UploadsStatus } from './uploads'

type MediaRoot = 'photos' | 'videos' | 'music' | 'uploads'
type PreviewMode = 'fast' | 'standard'

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

/**
 * Ask FFmpeg to measure the black bars of a file (the crop editor's
 * "Black bars" tool). The rectangle comes back in fractions of the *turned*
 * picture, which is the space the editor works in.
 */
async function serverCropDetect(root: MediaRoot, serverPath: string, rotation: number, seconds: number) {
  const url = `/api/media/cropdetect?root=${root}&path=${encodeMediaRelative(mediaRelativePath(root, serverPath))}`
    + `&rotation=${Math.round(rotation)}&seconds=${seconds}`
  const response = await fetch(url)
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error((data && (data.detail || data.message)) || `The backend could not measure this file (${response.status})`)
  }
  const rect = data?.rect
  if (!rect || typeof rect !== 'object') throw new Error('FFmpeg returned no crop for this file')
  return { rect: rect as CropRect, bars: !!data?.bars }
}

async function serverVideoDuration(root: MediaRoot, serverPath: string) {
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
  if (p === '/uploads' || p.startsWith('/uploads/')) return 'uploads'
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

// Detailed settings of the selected transition(s): one shared bar for the
// overall timeline and the detailed slide list. Edits apply to every
// selected transition via onPatch/onTime.
function TransitionInspector({ count, first, onPatch, onTime, onClear, onOpenGallery }: {
  count: number; first: MediaItem | undefined
  onPatch: (patch: Partial<MediaItem>) => void; onTime: (value: number) => void
  onClear: () => void; onOpenGallery?: () => void
}) {
  if (!first) return null
  const isGL = isGLTransition(first.transition)
  return <div className="timeline-inspector with-gl">
    <span>{count} transition{count > 1 ? 's' : ''} selected</span>
    <TransitionChip value={first.transition || 'Fade'} onChange={v => onPatch({ transition: v, transitionParams: isGLTransition(v) ? (first.transitionParams || {}) : undefined })} onOpenGallery={onOpenGallery}/>
    <NumberStepper value={first.transitionTime ?? DEFAULT_TRANSITION_SECONDS} min={MIN_TRANSITION_SECONDS} step={0.1} suffix="sec" ariaLabel="Selected transition time" onChange={onTime}/>
    {isGL && <GLParamControls transition={first.transition} params={(first.transitionParams as Record<string,string|number>)||{}} onChange={next => onPatch({ transitionParams: next })}/>}
    <div className="transition-meta">
      <EasingSelect value={first.transitionEasing||EASING_DEFAULT} onChange={v => onPatch({ transitionEasing: v })}/>
      <label className="check-label"><input type="checkbox" checked={Boolean(first.transitionReverse)} onChange={e => onPatch({ transitionReverse: e.target.checked ? 1 : 0 })}/><span><Check size={11}/></span> Reverse</label>
    </div>
    <button onClick={onClear}><X size={13}/> Clear</button>
  </div>
}

// One-click select/deselect of every slide. Shown above the overall timeline
// and in the detailed list's column head; shows the partial selection count
// so the state is readable at a glance.
function SelectAllSlides({ allSelected, selectedCount, totalCount, onToggle }: {
  allSelected: boolean; selectedCount: number; totalCount: number; onToggle: () => void
}) {
  const partial = selectedCount > 0 && !allSelected
  return <label className={`select-all ${allSelected ? 'on' : ''} ${partial ? 'partial' : ''} ${totalCount ? '' : 'empty'}`}
    title={`${allSelected ? 'Deselect every slide' : 'Select every slide'} — photos, videos and text frames${partial ? ` · ${selectedCount} of ${totalCount} selected` : ''}`}>
    <input type="checkbox" checked={allSelected} disabled={!totalCount} onChange={onToggle} aria-label={allSelected ? 'Deselect all slides' : 'Select all slides'}/>
    <span><Check size={9}/></span>
    <em>{partial ? `${selectedCount}/${totalCount}` : allSelected ? 'None' : 'All'}</em>
  </label>
}

type LightboxTarget = { title: string; src: string; kind: 'image' | 'video' | 'audio' | 'title' }

function MediaLightbox({ title, src, kind, onClose, onPrev, onNext, onDelete, onEdit, onEditFrame, titleFrame, position, rotation, onRotate, suspended, lookItem, onLook, onCrop }: LightboxTarget & {
  onClose: () => void;
  // Storyline bindings: when present, the lightbox can walk the storyline
  // (prev/next), show the current position, and delete the shown item.
  onPrev?: () => void; onNext?: () => void; onDelete?: () => void; position?: string;
  // Movies only: opens the cut/crop editor, which is stacked on top of this
  // lightbox and returns here when it closes.
  onEdit?: () => void;
  // Text frames only: opens the text frame editor, stacked on top of this
  // lightbox the same way; the frame behind keeps updating live while edit.
  onEditFrame?: () => void; titleFrame?: MediaItem | null;
  // Photo orientation: current quarter-turn rotation and a handler receiving
  // +90 (clockwise) or -90 (counter-clockwise). Only offered for photos.
  rotation?: number; onRotate?: (delta: 90 | -90) => void;
  // True while a stacked editor is open: keyboard shortcuts and the backdrop
  // click belong to that editor, not to this lightbox.
  suspended?: boolean;
  // Picture look and cut/crop: the item being shown, so the lightbox wears the
  // same CSS filter and the same crop FFmpeg will apply, plus the buttons
  // that open the stacked editor popup. Movies get the Filters button too —
  // only audio and text frames have no picture to grade.
  lookItem?: Lookish | MediaItem | null; onLook?: () => void; onCrop?: () => void;
}) {
  const [failed, setFailed] = useState(false)
  const [usePreview, setUsePreview] = useState(false)
  // Title-frame caption size: frame pixels × boxHeight/1080, matching drawtext.
  // The stage only exists for title frames, so a callback ref is required.
  const { setRef: titleStageRef } = useFrameScale<HTMLDivElement>()
  const previewSrc = (() => {
    if (kind !== 'video') return src
    // When the storyline provides the real item, build the preview from it;
    // otherwise derive it from the file URL (browser preview without an item).
    if (lookItem && (lookItem as any).path != null) {
      try { return moviePreviewUrl(lookItem as any, 960) } catch { /* fallback to URL parse */ }
    }
    try {
      const url = new URL(src, window.location.origin)
      const root = url.searchParams.get('root')
      const pathParam = url.searchParams.get('path')
      if (root && pathParam != null) return `/api/media/preview?root=${root}&path=${pathParam.split('/').map(encodeURIComponent).join('/')}&width=960`
    } catch { /* ignore */ }
    return src
  })()
  const videoSrc = usePreview ? previewSrc : src
  useEffect(() => { setFailed(false); setUsePreview(false) }, [src, previewSrc])
  const isVideo = kind === 'video'
  // Crop first, look on top — the renderer's order. A cropped *photo* is shown
  // from one canvas copy; a cropped *movie* keeps playing its own file through a
  // CSS sprite (CropSpriteVideo), because a JPEG copy would freeze it — so no
  // copy is built for movies here.
  const cropped = useCroppedSource(src, isVideo ? null : lookItem, 'stage', false)
  const lookView = usePictureLook(cropped.ready ? cropped.src : src, lookItem, false, !isVideo, cropped.rotationApplied)
  // Keyboard: ← / → walk the storyline, Escape closes. Only wired when the
  // lightbox is bound to storyline items (browser previews pass no handlers).
  useEffect(() => {
    if (!onPrev && !onNext && !onDelete && !onRotate) return
    if (suspended) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') onPrev?.()
      else if (event.key === 'ArrowRight') onNext?.()
      else if (event.key === 'Escape') onClose()
      else if ((event.key === 'r' || event.key === 'R') && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); onRotate?.(event.shiftKey ? -90 : 90) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onPrev, onNext, onDelete, onRotate, onClose, suspended])
  const turn = normalizeRotation(rotation)
  const canRotate = kind === 'image' && !!onRotate
  const canEditMovie = kind === 'video' && !!onEdit
  const canEditFrame = kind === 'title' && !!onEditFrame
  // Movies wear looks exactly like photos (same editor, same renderer path);
  // only the pixelate proxy stays photo-only, so a movie keeps playing.
  const canLook = (kind === 'image' || kind === 'video') && !!onLook
  const canCrop = kind === 'image' && !!onCrop
  // Text frames preview as the frame itself: background colours (with the
  // A→B change looping like in the editor) and the caption at its position.
  const turnedByProxy = cropped.rotationApplied || lookView.rotationBaked
  const cropNote = hasCrop(lookItem) ? cropSummary(lookItem) : '' 
  // Storyline navigation lives inside the picture: the whole left half steps
  // back, the whole right half steps forward. Movies keep their centre and
  // their control bar clickable, and audio has no picture to click on.
  const navLayer = kind !== 'audio' && (onPrev || onNext) ? <div className={`lightbox-nav-layer${kind === 'video' ? ' movie' : ''}`}>
    {onPrev && <button type="button" className="lightbox-nav prev" title="Previous media (←) · the whole left half of the picture" aria-label="Previous media" onClick={onPrev}><span className="nav-disc"><ChevronLeft size={26}/></span></button>}
    {onNext && <button type="button" className="lightbox-nav next" title="Next media (→) · the whole right half of the picture" aria-label="Next media" onClick={onNext}><span className="nav-disc"><ChevronRight size={26}/></span></button>}
  </div> : null
  return <div className="modal-backdrop dark-backdrop" onMouseDown={suspended ? undefined : onClose}>
    <div className="media-lightbox" onMouseDown={e => e.stopPropagation()}>
      <div className="preview-top"><div><strong>{title}</strong><span>{kind === 'video' ? 'VIDEO' : kind === 'audio' ? 'AUDIO' : kind === 'title' ? 'TEXT FRAME' : 'PHOTO'}</span></div><div className="lightbox-actions">{position && <em className="lightbox-position">{position}</em>}{canEditMovie && <button type="button" className="lightbox-edit" title="Cut this movie — choose which section to use" onClick={onEdit}><Scissors size={17}/> Cut</button>}
        {canEditFrame && <button type="button" className="lightbox-edit" title="Edit this text frame — text, font, colours, position and timing" onClick={onEditFrame}><Type size={17}/> Edit frame</button>}
        {canLook && <button type="button" className={`lightbox-edit look-button ${hasLook(lookItem) ? 'on' : ''}`} title={hasLook(lookItem) ? `${isVideo ? 'Movie' : 'Picture'} look: ${lookSummary(lookItem)} — click to change` : `Filters & effects for this ${isVideo ? 'movie' : 'picture'}`} onClick={onLook}><Sparkles size={17}/> {lookSummary(lookItem) || 'Filters'}</button>}
        {canCrop && <button type="button" className={`lightbox-edit look-button ${hasCrop(lookItem) ? 'on' : ''}`} title={cropNote ? `Cut & crop: ${cropNote} — click to change` : 'Cut and crop parts of this picture'} onClick={onCrop}><CropIcon size={17}/> {cropNote ? cropLabel(lookItem) : 'Crop'}</button>}
        {canRotate && <span className="lightbox-rotate"><button type="button" className={`lightbox-edit look-button ${turn ? 'on' : ''}`} title={turn ? `Picture turned ${turn}° clockwise — click to turn it 90° more. R turns clockwise, Shift+R counter-clockwise. The turn is applied in the rendered slideshow.` : 'Rotate the picture 90° clockwise — R turns clockwise, Shift+R counter-clockwise. The turn is applied in the rendered slideshow.'} onClick={() => onRotate!(90)}><RotateCw size={17}/> {turn ? `Rotate · ${turn}°` : 'Rotate'}</button><button type="button" className="lightbox-rotate-ccw" title="Rotate 90° counter-clockwise (Shift+R)" aria-label="Rotate counter-clockwise" onClick={() => onRotate!(-90)}><RotateCcw size={18}/></button></span>}{onDelete && <button type="button" className="lightbox-delete" title="Remove from storyline" aria-label="Remove from storyline" onClick={onDelete}><Trash2 size={18}/></button>}<button type="button" onClick={onClose} aria-label="Close preview"><X size={20}/></button></div></div>
      <div className="lightbox-body">
      {failed ? <div className="lightbox-error"><ImageOff size={30}/><strong>This file could not be previewed</strong><span>{kind === 'video' ? 'Your browser may not decode this format (including camera AVI). It can still be imported and rendered by FFmpeg.' : 'It is empty, missing, or unreadable on the mounted volume.'}</span></div>
        : kind === 'video' ? <CropSpriteVideo item={lookItem} className="lightbox-media" src={videoSrc} style={lookView.style} controls autoPlay onError={() => { if (!usePreview && previewSrc !== src) setUsePreview(true); else setFailed(true) }} />
        : kind === 'audio' ? <audio className="lightbox-audio" src={src} controls autoPlay onError={() => setFailed(true)} />
        : kind === 'title' && titleFrame ? <div className="lightbox-stage title-frame-stage" ref={titleStageRef} style={frameBackgroundStyle(titleFrame)}>
            <FrameMotionPreview item={titleFrame} playing={!suspended} />
          </div>
        : <div className="lightbox-stage"><img className={`lightbox-media lightbox-photo ${!turnedByProxy && (turn === 90 || turn === 270) ? 'turned' : ''}`} style={{ ...(turnedByProxy ? undefined : rotationStyle(turn)), ...lookView.style }} src={lookView.src} alt={title} onError={() => setFailed(true)} /></div>}
      {lookView.vignette && <i className="look-vignette" style={lookView.vignette}/>}
      {navLayer}
      </div>
    </div>
  </div>
}

// Renders a media thumbnail (image or video) with a graceful placeholder when
// the backend reports the file unreadable — a 0-byte or missing file would
// otherwise show as a silently broken image in the timeline and filmstrip.
// Module-level cache so the storyline does not re-capture a movie's frames on
// every re-render or reorder: keyed by the stream URL, holds the sprite src.
const movieStripCache = new Map<string, { src: string; total: number } | null>()

/** Real frames of a movie laid edge to edge — the same strip the movie editor
 * draws, sized for a storyline clip. Cut (trimmed-away) sections are shaded so
 * the clip shows exactly the part of the movie that will play. */
function MovieStrip({ item, onClick, onPointerDown }: { item: MediaItem; onClick?: React.MouseEventHandler; onPointerDown?: React.PointerEventHandler }) {
  const src = itemThumbUrl(item)
  const [strip, setStrip] = useState<{ src: string; total: number } | null>(() => movieStripCache.get(src) ?? null)
  const [failed, setFailed] = useState(() => movieStripCache.has(src) && movieStripCache.get(src) === null)
  useEffect(() => {
    if (!src) return
    if (movieStripCache.has(src)) { setStrip(movieStripCache.get(src) ?? null); setFailed(movieStripCache.get(src) === null); return }
    let cancelled = false
    void (async () => {
      // For camera AVI/WMV/MPEG-PS/AVCHD the browser cannot decode the video,
      // so probing and canvas capture would only waste ~8 s. Go straight to
      // the server probe and the server-rendered sprite.
      const serverOnly = /\.(avi|wmv|asf|mpg|mpeg|ts|mts|m2ts|flv|f4v|3gp|3gpp|vob|dav|mxf|mod|tod|divx|mkv)$/i.test(item.name || '')
      let total = 0
      if (!serverOnly) {
        try {
          total = await new Promise<number>((resolve) => {
            const v = document.createElement('video'); v.preload = 'metadata'; v.muted = true; v.src = src
            const done = (n: number) => { v.removeAttribute('src'); v.load(); resolve(n) }
            v.onloadedmetadata = () => done(Number.isFinite(v.duration) ? v.duration : 0)
            v.onerror = () => done(0)
            window.setTimeout(() => done(0), 8000)
          })
        } catch { total = 0 }
      }
      if (!total) total = await serverMovieDuration(item)
      if (cancelled) return
      let captured: string | null = null
      if (!serverOnly && total > 0) captured = await captureFilmstrip(src, FILMSTRIP_CELLS, total)
      if (cancelled) return
      let result: { src: string; total: number } | null = captured ? { src: captured, total } : null
      if (!result && total > 0) {
        const url = movieFilmstripUrl(item)
        const ok = await new Promise<boolean>(resolve => { const img = new Image(); img.onload = () => resolve(true); img.onerror = () => resolve(false); img.src = url })
        if (ok) result = { src: url, total }
      }
      if (cancelled) return
      movieStripCache.set(src, result)
      setStrip(result); setFailed(!result)
    })()
    return () => { cancelled = true }
  }, [src, item.path, item.name])
  if (!src || failed || !strip) return <MediaThumb item={item} onClick={onClick} onPointerDown={onPointerDown} />
  const total = strip.total
  const start = Math.max(0, Math.min(Number(item.trimStart) || 0, total))
  const end = Math.min(total, (Number(item.trimEnd) || 0) > 0 ? Number(item.trimEnd) : total)
  const pct = (t: number) => `${total ? Math.min(100, Math.max(0, t / total * 100)) : 0}%`
  return <div className="clip-strip" onClick={onClick} onPointerDown={onPointerDown} title={movieIsTrimmed(item) ? `Movie frames · using ${movieKeptLabel(item)} — click to view` : 'Movie frames — click to view'}>
    <img src={strip.src} alt={item.name} draggable={false} style={pictureFilterStyle(item)} />
    {start > 0.01 && <i className="clip-cut left" style={{ width: pct(start) }} />}
    {end < total - 0.01 && <i className="clip-cut right" style={{ left: pct(end) }} />}
  </div>
}

function MediaThumb({ item, className, muted, preload, onClick, onPointerDown, style }: {
  item: MediaItem; className?: string; muted?: boolean; preload?: 'metadata' | 'auto' | 'none';
  onClick?: React.MouseEventHandler; onPointerDown?: React.PointerEventHandler; style?: React.CSSProperties;
}) {
  const [failed, setFailed] = useState(false)
  const [usePreview, setUsePreview] = useState(false)
  const origSrc = itemThumbUrl(item)
  const previewSrc = item.type === 'video' ? moviePreviewUrl(item, 480) : origSrc
  const src = usePreview ? previewSrc : origSrc
  // A cropped clip is shown from one small canvas copy — the only way a bare
  // <img>/<video> can display a sub-rectangle — so every thumbnail surface
  // (storyline, compact grid, detailed list, filmstrip, media browser) shows
  // the crop without a single extra CSS rule.
  const cropped = useCroppedSource(src, item, 'thumb', item.type === 'video')
  useEffect(() => { setFailed(false); setUsePreview(false) }, [origSrc, previewSrc])
  if (!src) return null
  if (failed && usePreview) return <span className="thumb-fallback"><ImageOff size={14}/><small>unavailable</small></span>
  const handleError = () => {
    if (item.type === 'video' && !usePreview) setUsePreview(true)
    else setFailed(true)
  }
  // Every thumbnail wears the item's picture look and crop, so the storyline
  // shows the same picture the render will produce.
  // draggable=false: the thumbnail must never start its own native drag —
  // the draggable card around it is the drag source, so reordering works
  // from anywhere on the slide, including the middle of the picture.
  const common = { src: cropped.ready ? cropped.src : src, className, onClick, onPointerDown, onError: handleError, draggable: false } as const
  const look = { ...style, ...pictureFilterStyle(item) }
  if (item.type === 'video' && !cropped.ready) return <video {...common} muted={muted ?? true} preload={preload ?? 'metadata'} style={look} />
  // The copy already carries the quarter turn; only the bare file needs CSS to turn it.
  return <img {...common} style={cropped.rotationApplied ? look : rotationStyle(item.rotation, look)} alt={item.name} />
}

// Thumbnail inside the media picker, with a fallback when the file is empty
// or unreadable (the backend answers 422 for 0-byte files, so onError fires).
function BrowserThumb({ root, file }: { root: MediaRoot, file: any }) {
  const [failed, setFailed] = useState(false)
  const [usePreview, setUsePreview] = useState(false)
  const src = mediaFileUrl(root, file.path)
  const previewSrc = `/api/media/preview?root=${root}&path=${file.path.split('/').map(encodeURIComponent).join('/')}&width=480`
  const videoSrc = usePreview ? previewSrc : src
  useEffect(() => { setFailed(false); setUsePreview(false) }, [src, previewSrc])
  if (file.kind === 'directory') return <FolderOpen size={34}/>
  if (file.kind === 'audio') return <Music2 size={34}/>
  if (failed && !usePreview) {
    // first failure was native; preview will be tried once
  }
  if (failed && usePreview) return <span className="file-thumb-fallback"><ImageOff size={20}/></span>
  if (file.kind === 'video') return <><video src={videoSrc} muted preload="metadata" onError={() => { if (!usePreview) setUsePreview(true); else setFailed(true) }}/><span className="video-tag"><Video size={10}/> video</span></>
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
const initialMedia: MediaItem[] = []

// --- Shared timing rules ---------------------------------------------------
// These mirror the backend renderer exactly: every clip runs at least 0.2 s
// and every transition is additional timeline time, rather than time borrowed
// from its neighbouring clips. Keeping the two sides in sync means the
// estimated total shown in the UI always equals the rendered MP4 length,
// even after extreme transition/duration edits.
const MIN_CLIP_SECONDS = 0.2
const MIN_TRANSITION_SECONDS = 0.05

function clampPctMotion(v: number) { return Math.max(0, Math.min(100, v)) }
function generateCirclePointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, turns: number, num = 64): [number, number][] {
  let cx: number, cy: number, r: number
  if (radius != null && radius > 0) { cx = fromX; cy = fromY; r = radius }
  else { cx = (fromX + toX) / 2; cy = (fromY + toY) / 2; const d = Math.hypot(toX - fromX, toY - fromY); r = d / 2; if (r < 1) { r = 15; cx = fromX; cy = fromY } }
  turns = Math.max(0.1, Math.min(4, turns))
  const pts: [number, number][] = []
  let startAng = 0
  if (radius == null || radius <= 0) startAng = Math.atan2(fromY - cy, fromX - cx)
  for (let i = 0; i <= num; i++) {
    const ang = startAng + (i / num) * turns * 2 * Math.PI
    pts.push([clampPctMotion(cx + r * Math.cos(ang)), clampPctMotion(cy + r * Math.sin(ang))])
  }
  return pts
}
function generateSinePointsMotion(fromX: number, fromY: number, toX: number, toY: number, amplitude: number, frequency: number, num = 80): [number, number][] {
  const amp = Math.max(0, Math.min(40, amplitude))
  const freq = Math.max(0.1, Math.min(10, frequency))
  const dx = toX - fromX, dy = toY - fromY
  const len = Math.hypot(dx, dy)
  if (len < 1e-6) {
    const pts: [number, number][] = []
    for (let i = 0; i <= num; i++) { const p = i / num; pts.push([clampPctMotion(fromX + amp * Math.sin(freq * 2 * Math.PI * p)), clampPctMotion(fromY + p*20)]) }
    return pts
  }
  const ux = dx / len, uy = dy / len
  const px = -uy, py = ux
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const bx = fromX + dx * p, by = fromY + dy * p
    const off = amp * Math.sin(freq * 2 * Math.PI * p)
    pts.push([clampPctMotion(bx + px * off), clampPctMotion(by + py * off)])
  }
  return pts
}
function generateSineVerticalPointsMotion(fromX: number, fromY: number, toX: number, toY: number, amplitude: number, frequency: number, num = 80): [number, number][] {
  const amp = Math.max(0, Math.min(30, amplitude))
  const freq = Math.max(0.1, Math.min(10, frequency))
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) { const p = i / num; const bx = fromX + (toX - fromX)*p; const by = fromY + (toY - fromY)*p; const off = amp * Math.sin(freq*2*Math.PI*p); pts.push([clampPctMotion(bx), clampPctMotion(by+off)]) }
  return pts
}
function generateStarPointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, points: number, innerRatio: number, rotation: number, numPerSeg = 12): [number, number][] {
  const n = Math.max(3, Math.min(10, Math.round(points||5)))
  const ratio = Math.max(0.2, Math.min(0.85, innerRatio ?? 0.45))
  let cx:number, cy:number, r:number
  if (radius!=null && radius>2) { cx=fromX; cy=fromY; r=radius } else { cx=(fromX+toX)/2; cy=(fromY+toY)/2; const d=Math.hypot(toX-fromX,toY-fromY); r=Math.max(8,d*0.45) }
  const rot=(rotation||0)*Math.PI/180
  const vertices: [number,number][]=[]
  const step=Math.PI/n
  for(let i=0;i<n*2;i++){ const ang=rot - Math.PI/2 + i*step; const rad=i%2===0?r:r*ratio; vertices.push([clampPctMotion(cx+rad*Math.cos(ang)), clampPctMotion(cy+rad*Math.sin(ang))])}
  vertices.push(vertices[0])
  const pts:[number,number][]=[]
  for(let i=0;i<vertices.length-1;i++){ const a=vertices[i], b=vertices[i+1]; for(let k=0;k<numPerSeg;k++){ const t=k/numPerSeg; pts.push([clampPctMotion(a[0]+(b[0]-a[0])*t), clampPctMotion(a[1]+(b[1]-a[1])*t)]) } }
  pts.push(vertices[vertices.length-1])
  return pts
}
function generateDiamondPointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, rotation: number): [number, number][] {
  let cx:number, cy:number, r:number
  if (radius!=null && radius>2) { cx=fromX; cy=fromY; r=radius } else { cx=(fromX+toX)/2; cy=(fromY+toY)/2; const d=Math.hypot(toX-fromX,toY-fromY); r=Math.max(10,d*0.5) }
  const rot=(rotation||0)*Math.PI/180
  const base:[number,number][] = [[cx,cy-r],[cx+r,cy],[cx,cy+r],[cx-r,cy]].map(([x,y])=>{ const dx=x-cx, dy=y-cy; return [clampPctMotion(cx+dx*Math.cos(rot)-dy*Math.sin(rot)), clampPctMotion(cy+dx*Math.sin(rot)+dy*Math.cos(rot))] as [number,number] })
  base.push(base[0]); const pts:[number,number][]=[]; const perSeg=20; for(let i=0;i<base.length-1;i++){ const a=base[i], b=base[i+1]; for(let k=0;k<perSeg;k++){ const t=k/perSeg; pts.push([clampPctMotion(a[0]+(b[0]-a[0])*t), clampPctMotion(a[1]+(b[1]-a[1])*t)]) } } pts.push(base[base.length-1]); return pts
}
function generateTrianglePointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, rotation: number): [number, number][] {
  let cx:number, cy:number, r:number
  if (radius!=null && radius>2) { cx=fromX; cy=fromY; r=radius } else { cx=(fromX+toX)/2; cy=(fromY+toY)/2; const d=Math.hypot(toX-fromX,toY-fromY); r=Math.max(10,d*0.55) }
  const rot=(rotation||0)*Math.PI/180
  const vertices:[number,number][]=[]; for(let i=0;i<3;i++){ const ang=rot - Math.PI/2 + i*(2*Math.PI/3); vertices.push([clampPctMotion(cx+r*Math.cos(ang)), clampPctMotion(cy+r*Math.sin(ang))]) } vertices.push(vertices[0]); const pts:[number,number][]=[]; const perSeg=24; for(let i=0;i<vertices.length-1;i++){ const a=vertices[i], b=vertices[i+1]; for(let k=0;k<perSeg;k++){ const t=k/perSeg; pts.push([clampPctMotion(a[0]+(b[0]-a[0])*t), clampPctMotion(a[1]+(b[1]-a[1])*t)]) } } pts.push(vertices[vertices.length-1]); return pts
}
function generateBouncePointsMotion(fromX: number, fromY: number, toX: number, toY: number, height: number, bounces: number, damping: number, numPerBounce = 28): [number, number][] {
  const h = Math.max(0, Math.min(30, height ?? 14))
  const n = Math.max(1, Math.min(8, Math.round(bounces ?? 4)))
  const d = Math.max(0, Math.min(0.9, damping ?? 0.35))
  const pts: [number, number][] = []
  for (let i = 0; i < n; i++) {
    const amp = h * Math.pow(1 - d, i)
    const segStart = i / n
    const segEnd = (i+1)/n
    for (let k = 0; k < numPerBounce; k++) {
      const tSeg = k / numPerBounce
      const p = segStart + tSeg * (segEnd - segStart)
      const bx = fromX + (toX - fromX) * p
      const byBase = fromY + (toY - fromY) * p
      const parabola = 4 * tSeg * (1 - tSeg)
      const off = -amp * parabola
      pts.push([clampPctMotion(bx), clampPctMotion(byBase + off)])
    }
  }
  pts.push([clampPctMotion(toX), clampPctMotion(toY)])
  return pts
}
// ---- New path shapes (2026-09 round). Each has a bit-identical twin in
// backend/app/text_effects.py (the renderer) and in TextMotionPathEditor.tsx
// (the editor canvas) — same formulas, same sample counts, so preview and
// MP4 travel exactly the same curve. ----
function generateSpiralPointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, turns: number, num = 96): [number, number][] {
  // With an explicit radius the spiral collapses into the start point;
  // otherwise it starts at the start handle and winds into the end handle.
  const d = Math.hypot(toX - fromX, toY - fromY)
  let cx: number, cy: number, r0: number, startAng: number
  if (radius != null && radius > 2) { cx = fromX; cy = fromY; r0 = radius; startAng = -Math.PI / 2 }
  else if (d > 0.5) { cx = toX; cy = toY; r0 = d; startAng = Math.atan2(fromY - cy, fromX - cx) }
  else { cx = fromX; cy = fromY; r0 = 15; startAng = -Math.PI / 2 }
  const t = Math.max(0.2, Math.min(6, turns))
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const ang = startAng + p * t * 2 * Math.PI
    const r = r0 * (1 - p)
    pts.push([clampPctMotion(cx + r * Math.cos(ang)), clampPctMotion(cy + r * Math.sin(ang))])
  }
  pts.push([clampPctMotion(cx), clampPctMotion(cy)])
  return pts
}
function generateFigure8PointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, num = 120): [number, number][] {
  // Lemniscate of Bernoulli: x = a·cos t/(1+sin²t), y = a·sin t·cos t/(1+sin²t)
  const d = Math.hypot(toX - fromX, toY - fromY)
  const explicit = radius != null && radius > 2
  const cx = explicit ? fromX : (fromX + toX) / 2
  const cy = explicit ? fromY : (fromY + toY) / 2
  const a = explicit ? radius as number : Math.max(10, d * 0.4)
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) {
    const t = (i / num) * 2 * Math.PI
    const s = Math.sin(t), c = Math.cos(t)
    const den = 1 + s * s
    pts.push([clampPctMotion(cx + a * c / den), clampPctMotion(cy + a * s * c / den)])
  }
  return pts
}
function generateLissajousPointsMotion(fromX: number, fromY: number, toX: number, toY: number, amp: number, freqX: number, freqY: number, num = 140): [number, number][] {
  const ax = Math.max(2, Math.min(40, amp || 14))
  const ay = ax * 0.7
  const f1 = Math.max(0.5, Math.min(8, freqX || 3))
  const f2 = Math.max(0.5, Math.min(8, freqY || 2))
  const cx = fromX, cy = fromY
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) {
    const t = i / num
    pts.push([clampPctMotion(cx + ax * Math.sin(2 * Math.PI * f1 * t + Math.PI / 2)), clampPctMotion(cy + ay * Math.sin(2 * Math.PI * f2 * t))])
  }
  return pts
}
function generateZigzagPointsMotion(fromX: number, fromY: number, toX: number, toY: number, amplitude: number, frequency: number, num = 96): [number, number][] {
  const amp = Math.max(0, Math.min(40, amplitude ?? 10))
  const freq = Math.max(0.5, Math.min(10, frequency ?? 3))
  const dx = toX - fromX, dy = toY - fromY
  const len = Math.hypot(dx, dy)
  const ux = len < 1e-6 ? 1 : dx / len, uy = len < 1e-6 ? 0 : dy / len
  const px = -uy, py = ux
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) {
    const p = i / num
    const s = p * freq
    const f = s - Math.floor(s)
    const tri = f < 0.5 ? f * 4 - 1 : 3 - f * 4
    const off = amp * tri
    pts.push([clampPctMotion(fromX + dx * p + px * off), clampPctMotion(fromY + dy * p + py * off)])
  }
  return pts
}
function generateHeartPointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, rotation: number, num = 120): [number, number][] {
  const d = Math.hypot(toX - fromX, toY - fromY)
  const explicit = radius != null && radius > 2
  const cx = explicit ? fromX : (fromX + toX) / 2
  const cy = explicit ? fromY : (fromY + toY) / 2
  const r = explicit ? radius as number : Math.max(10, d * 0.4)
  const s = r / 16
  const rot = (rotation || 0) * Math.PI / 180
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) {
    const t = (i / num) * 2 * Math.PI
    const hx = 16 * Math.pow(Math.sin(t), 3)
    const hy = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)
    const x = s * hx, y = -s * hy
    pts.push([clampPctMotion(cx + x * Math.cos(rot) - y * Math.sin(rot)), clampPctMotion(cy + x * Math.sin(rot) + y * Math.cos(rot))])
  }
  return pts
}
function generatePolygonPointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, sides: number, rotation: number, numPerSeg = 24): [number, number][] {
  // Regular n-gon — generalises diamond (4) and triangle (3).
  const n = Math.max(3, Math.min(10, Math.round(sides || 5)))
  const d = Math.hypot(toX - fromX, toY - fromY)
  let cx: number, cy: number, r: number
  if (radius != null && radius > 2) { cx = fromX; cy = fromY; r = radius }
  else { cx = (fromX + toX) / 2; cy = (fromY + toY) / 2; r = Math.max(10, d * 0.5) }
  const rot = (rotation || 0) * Math.PI / 180
  const vertices: [number, number][] = []
  for (let i = 0; i < n; i++) {
    const ang = rot - Math.PI / 2 + i * (2 * Math.PI / n)
    vertices.push([clampPctMotion(cx + r * Math.cos(ang)), clampPctMotion(cy + r * Math.sin(ang))])
  }
  vertices.push(vertices[0])
  const pts: [number, number][] = []
  for (let i = 0; i < vertices.length - 1; i++) {
    const a = vertices[i], b = vertices[i + 1]
    for (let k = 0; k < numPerSeg; k++) {
      const t = k / numPerSeg
      pts.push([clampPctMotion(a[0] + (b[0] - a[0]) * t), clampPctMotion(a[1] + (b[1] - a[1]) * t)])
    }
  }
  pts.push(vertices[vertices.length - 1])
  return pts
}
function generatePendulumPointsMotion(fromX: number, fromY: number, toX: number, toY: number, radius: number | undefined, num = 60): [number, number][] {
  // Arc under a pivot above the midpoint; the sweep always passes through the
  // lowest point, whichever side the handles are on.
  const arm = radius != null && radius > 2 ? radius : 20
  const px = (fromX + toX) / 2, py = (fromY + toY) / 2 - arm
  const d0 = Math.hypot(fromX - px, fromY - py)
  const d1 = Math.hypot(toX - px, toY - py)
  const r = Math.max(2, (d0 + d1) / 2)
  let a0 = Math.atan2(fromY - py, fromX - px)
  let a1 = Math.atan2(toY - py, toX - px)
  if (d0 < 0.5 && d1 < 0.5) { a0 = Math.PI / 2 - 0.5; a1 = Math.PI / 2 + 0.5 }
  const norm2pi = (a: number) => { let x = a % (2 * Math.PI); if (x < 0) x += 2 * Math.PI; return x }
  const crosses = (start: number, delta: number, target: number) => {
    const t = norm2pi(target - start)
    const span = Math.abs(delta)
    return delta >= 0 ? t <= span + 1e-9 : (2 * Math.PI - t) <= span + 1e-9
  }
  let cw = norm2pi(a1 - a0)
  if (cw < 1e-9) cw = 2 * Math.PI
  const ccw = cw - 2 * Math.PI
  const cwPasses = crosses(a0, cw, Math.PI / 2)
  const ccwPasses = crosses(a0, ccw, Math.PI / 2)
  let delta: number
  if (cwPasses && ccwPasses) delta = Math.abs(cw) <= Math.abs(ccw) ? cw : ccw
  else if (cwPasses) delta = cw
  else if (ccwPasses) delta = ccw
  else delta = Math.abs(cw) <= Math.abs(ccw) ? cw : ccw
  const pts: [number, number][] = []
  for (let i = 0; i <= num; i++) {
    const ang = a0 + (i / num) * delta
    pts.push([clampPctMotion(px + r * Math.cos(ang)), clampPctMotion(py + r * Math.sin(ang))])
  }
  return pts
}
function bouncyOffset(progress: number, height: number, bounces: number, damping: number): number {  const h = Math.max(0, Math.min(30, height ?? 12))
  const n = Math.max(1, Math.min(8, Math.round(bounces ?? 3)))
  const d = Math.max(0, Math.min(0.95, damping ?? 0.35))
  if (h < 0.2) return 0
  const p = Math.max(0, Math.min(1, progress))
  const bounceIdx = Math.min(n-1, Math.floor(p * n))
  const segT = (p * n) % 1
  const amp = h * Math.pow(1 - d, bounceIdx)
  const parabola = 4 * segT * (1 - segT)
  return -amp * parabola
}
function applySinusUpDownMotion(points: [number,number][], enabled: boolean, amplitude: number, frequency: number): [number,number][] {
  if (!enabled || points.length<2) return points
  const amp=Math.max(0,Math.min(20, amplitude ?? 6)); if (amp<0.2) return points
  const freq=Math.max(0.1,Math.min(10, frequency ?? 2))
  let total=0; for(let i=1;i<points.length;i++) total+=Math.hypot(points[i][0]-points[i-1][0], points[i][1]-points[i-1][1])
  if (total<1e-6) return points
  const out:[number,number][]=[]; const num=Math.max(points.length,80)
  const pointAlong=(pts:[number,number][], prog:number):[number,number]=>{ if(!pts.length) return [50,50]; if(pts.length===1) return pts[0]; let t=0; for(let i=1;i<pts.length;i++) t+=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]); let target=t*Math.max(0,Math.min(1,prog)); for(let i=1;i<pts.length;i++){ const seg=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]); if(target<=seg){ const tt=seg===0?0:target/seg; return [pts[i-1][0]+(pts[i][0]-pts[i-1][0])*tt, pts[i-1][1]+(pts[i][1]-pts[i-1][1])*tt] } target-=seg } return pts[pts.length-1] }
  for(let i=0;i<=num;i++){ const p=i/num; const b=pointAlong(points,p); const off=amp*Math.sin(freq*2*Math.PI*p); out.push([clampPctMotion(b[0]), clampPctMotion(b[1]+off)]) }
  return out
}
function effectiveMotionPoints(fromX: number, fromY: number, toX: number, toY: number, path: [number, number][] | undefined, pathType: string, circleRadius: number | undefined, circleTurns: number, sineAmp: number, sineFreq: number, starPoints?: number, starInnerRatio?: number, symbolRotation?: number, sinusEnabled?: boolean, sinusAmp?: number, sinusFreq?: number, bounceHeight?: number, bounceCount?: number, bounceDamping?: number, lissajousFreqY?: number): [number, number][] {
  let base: [number, number][]
  if ((pathType === 'freehand' || pathType === 'polyline') && path && path.length >= 2) base = path
  else if (pathType === 'circle') base = generateCirclePointsMotion(fromX, fromY, toX, toY, circleRadius, circleTurns)
  else if (pathType === 'sine') base = generateSinePointsMotion(fromX, fromY, toX, toY, sineAmp, sineFreq)
  else if (pathType === 'sine-vertical') base = generateSineVerticalPointsMotion(fromX, fromY, toX, toY, sineAmp, sineFreq)
  else if (pathType === 'star') base = generateStarPointsMotion(fromX, fromY, toX, toY, circleRadius, starPoints ?? 5, starInnerRatio ?? 0.45, symbolRotation ?? 0)
  else if (pathType === 'diamond') base = generateDiamondPointsMotion(fromX, fromY, toX, toY, circleRadius, symbolRotation ?? 0)
  else if (pathType === 'triangle') base = generateTrianglePointsMotion(fromX, fromY, toX, toY, circleRadius, symbolRotation ?? 0)
  else if (pathType === 'bounce') base = generateBouncePointsMotion(fromX, fromY, toX, toY, bounceHeight ?? 14, bounceCount ?? 4, bounceDamping ?? 0.35)
  else if (pathType === 'spiral') base = generateSpiralPointsMotion(fromX, fromY, toX, toY, circleRadius, circleTurns)
  else if (pathType === 'figure-8') base = generateFigure8PointsMotion(fromX, fromY, toX, toY, circleRadius)
  else if (pathType === 'lissajous') base = generateLissajousPointsMotion(fromX, fromY, toX, toY, sineAmp, sineFreq, lissajousFreqY ?? 2)
  else if (pathType === 'zigzag') base = generateZigzagPointsMotion(fromX, fromY, toX, toY, sineAmp, sineFreq)
  else if (pathType === 'heart') base = generateHeartPointsMotion(fromX, fromY, toX, toY, circleRadius, symbolRotation ?? 0)
  else if (pathType === 'polygon') base = generatePolygonPointsMotion(fromX, fromY, toX, toY, circleRadius, starPoints ?? 5, symbolRotation ?? 0)
  else if (pathType === 'pendulum') base = generatePendulumPointsMotion(fromX, fromY, toX, toY, circleRadius)
  else if (Math.abs(fromX - toX) < 0.01 && Math.abs(fromY - toY) < 0.01) base = [[fromX, fromY]]
  else base = [[fromX, fromY], [toX, toY]]
  if (sinusEnabled) base = applySinusUpDownMotion(base, true, sinusAmp ?? 6, sinusFreq ?? 2)
  return base
}


const MIN_TEXT_SECONDS = 0.1
// Default duration of every new transition (and fallback for legacy items
// that were saved before transitionTime existed). Mirrored by the renderer.
const DEFAULT_TRANSITION_SECONDS = 5
// Default hold time of every new photo and text frame. The "Slide default"
// control in the bulk bar changes it per project and can push it onto every
// existing slide in one go. Videos are never touched by it: their hold is
// their native runtime (the renderer never cuts a movie short).
const DEFAULT_SLIDE_SECONDS = 5
const MAX_DEFAULT_SLIDE_SECONDS = 600
const MAX_DEFAULT_TRANSITION_SECONDS = 30
const clampNumber = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max))
const safeDuration = (value: number) => Math.max(MIN_CLIP_SECONDS, Number.isFinite(value) ? value : DEFAULT_SLIDE_SECONDS)
// Keep the text lane and the renderer on the same side of the outgoing
// transition: a text window is always relative to the slide's visible hold,
// never to the extra transition handle that follows it. This also repairs old
// projects whose saved values were outside the current draggable range.
function normalizedTextTiming(item: Pick<MediaItem, 'duration' | 'textStart' | 'textEnd' | 'type' | 'textSteadySeconds'>) {
  const duration = safeDuration((item as any).duration)
  // Text frames always show text for whole slide
  if ((item as any).type === 'title') {
    return { duration, textStart: 0, textEnd: duration }
  }
  const minimum = Math.min(MIN_TEXT_SECONDS, duration)
  const rawStart = Number(item.textStart)
  const rawEnd = Number(item.textEnd)
  const textStart = clampNumber(Number.isFinite(rawStart) ? rawStart : 0, 0, Math.max(0, duration - minimum))
  const textEnd = clampNumber(Number.isFinite(rawEnd) ? rawEnd : duration, textStart + minimum, duration)
  return { duration, textStart, textEnd }
}
function normalizeItemTextTiming(item: MediaItem): MediaItem {
  const base = { ...item, ...normalizedTextTiming(item as any) }
  // keep steady tail within hold for title frames (and any item)
  if (Number.isFinite(Number((item as any).textSteadySeconds))) {
    const hold = Math.max(0.2, base.textEnd - base.textStart || base.duration || 1)
    const steady = Math.max(0, Math.min(Number((item as any).textSteadySeconds), Math.max(0, hold - 0.05)))
    ;(base as any).textSteadySeconds = steady
  }
  return base
}
// Sanitise the project-wide defaults (typed by the user or read from a saved
// project) so a bad value can never produce a zero-length clip or transition.
const clampSlideDefault = (value: unknown, fallback = DEFAULT_SLIDE_SECONDS) => { const n = Number(value); return Number.isFinite(n) && n > 0 ? clampNumber(n, MIN_CLIP_SECONDS, MAX_DEFAULT_SLIDE_SECONDS) : fallback }
const clampTransitionDefault = (value: unknown, fallback = DEFAULT_TRANSITION_SECONDS) => { const n = Number(value); return Number.isFinite(n) && n > 0 ? clampNumber(n, 0.1, MAX_DEFAULT_TRANSITION_SECONDS) : fallback }
// Change a clip's hold time while keeping its caption timing valid. A caption
// that ran until the end of the clip keeps doing so (that is the default
// timing of every caption); any other caption window is clamped inside the
// new length.
function resizeClip(item: MediaItem, seconds: number): MediaItem {
  const duration = safeDuration(seconds)
  const previous = safeDuration(item.duration)
  const rawEnd = Number(item.textEnd)
  const ranToEnd = !Number.isFinite(rawEnd) || rawEnd >= previous - 1e-6
  const textEnd = (item as any).type === 'title' ? duration : (ranToEnd ? duration : clampNumber(rawEnd, MIN_TEXT_SECONDS, duration))
  const rawStart = Number(item.textStart)
  const textStart = (item as any).type === 'title' ? 0 : clampNumber(Number.isFinite(rawStart) ? rawStart : 0, 0, Math.max(0, textEnd - MIN_TEXT_SECONDS))
  const steadyRaw = Number((item as any).textSteadySeconds || 0)
  const hold = Math.max(0.2, (item as any).type === 'title' ? duration : (textEnd - textStart))
  const steady = Number.isFinite(steadyRaw) ? Math.max(0, Math.min(steadyRaw, Math.max(0, hold - 0.05))) : 0
  return { ...item, duration, textStart, textEnd, textSteadySeconds: steady }
}
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

// Bundled fonts: registry/fonts.json via src/fonts.ts (FONT_GROUPS,
// FONTS_WITHOUT_ITALIC, FONTS_WITHOUT_BOLD) — the same catalogue the renderer
// resolves, so the preview and the MP4 always use the same file.
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

const effects = ['None', 'Ken Burns · Zoom in', 'Ken Burns · Zoom out', 'Ken Burns · Pan left', 'Ken Burns · Pan right', 'Ken Burns · Pan up', 'Ken Burns · Pan down', 'Original motion']
// Per-slide Ken Burns strength bounds — mirrored by KEN_BURNS_MIN/MAX_STRENGTH
// in backend/app/renderer.py. 1.12 is the default every slide had before the
// setting existed, so untouched projects render exactly as they did.
const KEN_BURNS_DEFAULT_ZOOM = 1.12, KEN_BURNS_MIN_ZOOM = 1.02, KEN_BURNS_MAX_ZOOM_UI = 1.35
const KEN_BURNS_PRESETS: { label: string; zoom: number }[] = [{ label: 'Subtle', zoom: 1.06 }, { label: 'Normal', zoom: 1.12 }, { label: 'Strong', zoom: 1.2 }, { label: 'Dramatic', zoom: 1.3 }]
const kenBurnsZoomOf = (item: MediaItem) => Math.min(KEN_BURNS_MAX_ZOOM_UI, Math.max(KEN_BURNS_MIN_ZOOM, Number(item.kenBurnsZoom) || KEN_BURNS_DEFAULT_ZOOM))
const kenBurnsFocusOf = (item: MediaItem) => ({ x: Math.min(100, Math.max(0, Number.isFinite(Number(item.kenBurnsX)) && item.kenBurnsX !== undefined ? Number(item.kenBurnsX) : 50)), y: Math.min(100, Math.max(0, Number.isFinite(Number(item.kenBurnsY)) && item.kenBurnsY !== undefined ? Number(item.kenBurnsY) : 50)) })
const isKenBurns = (effect: string) => effect.startsWith('Ken Burns')
const isKenBurnsZoom = (effect: string) => isKenBurns(effect) && effect.includes('Zoom')
// One-line summary for the thumbnail chip / tooltips: "Zoom in · 12 %" or "Pan left · 6 %".
const kenBurnsSummary = (item: MediaItem) => {
  if (!isKenBurns(item.effect)) return shortEffect(item.effect)
  const pct = Math.round((kenBurnsZoomOf(item) - 1) * 100)
  const f = kenBurnsFocusOf(item)
  const focus = isKenBurnsZoom(item.effect) && (Math.round(f.x) !== 50 || Math.round(f.y) !== 50) ? ` · focus ${Math.round(f.x)}/${Math.round(f.y)}` : ''
  return `${shortEffect(item.effect)} · ${pct} %${focus}`
}

// Closed-state label for the row's motion chip: the "Ken Burns · " prefix is
// identical on every option and would only waste width in a thumbnail chip.
const shortEffect = (effect: string) => effect.startsWith('Ken Burns · ') ? effect.slice('Ken Burns · '.length) : effect === 'Original motion' ? 'Original' : effect

// Recorded example from the cached transition catalogue. The detailed list can
// contain many rows, so only examples near the viewport start a video request;
// the symbol remains a useful fallback when a catalogue clip is not cached yet.
function RecordedTransitionExample({ transition, empty = false, onClick }: { transition: string; empty?: boolean; onClick?: () => void }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const [visible, setVisible] = useState(false)
  const [failed, setFailed] = useState(false)
  const url = empty ? '' : transitionPreviewUrl(transition)

  useEffect(() => {
    if (empty) return
    setVisible(false)
    setFailed(false)
    const element = hostRef.current
    if (!element) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) {
        setVisible(true)
        observer.disconnect()
      }
    }, { rootMargin: '120px' })
    observer.observe(element)
    return () => observer.disconnect()
  }, [empty, transition])

  const handleClick = () => {
    if (empty) return
    if (onClick) {
      onClick()
      return
    }
    // Fallback: try to find the sibling transition-chip button and click it
    const host = hostRef.current
    if (!host) return
    // Next sibling is transition-cell, find its chip button
    const cell = host.nextElementSibling
    const chip = cell?.querySelector('button.transition-chip') as HTMLButtonElement | null
    if (chip) chip.click()
    else {
      // Also try within same timeline-item
      const row = host.closest('.timeline-item')
      const chip2 = row?.querySelector('button.transition-chip') as HTMLButtonElement | null
      if (chip2) chip2.click()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (empty) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleClick()
    }
  }

  return <div
    ref={hostRef}
    className={`recorded-transition-example${empty ? ' empty' : ''}${empty ? '' : ' clickable'}`}
    aria-label={empty ? undefined : `Recorded example of ${transition}`}
    title={empty ? undefined : `Recorded example · ${transition} · click to change transition`}
    role={empty ? undefined : 'button'}
    tabIndex={empty ? undefined : 0}
    onClick={empty ? undefined : handleClick}
    onKeyDown={empty ? undefined : handleKeyDown}
  >
    {!empty && visible && !failed && <video key={url} src={url} muted loop autoPlay playsInline preload="metadata" onError={() => setFailed(true)} />}
    {!empty && <span className="recorded-transition-fallback" aria-hidden>{transitionSymbol(transition)}</span>}
    {!empty && <small>EXAMPLE</small>}
  </div>
}

// `inline`: show easing / reverse / GL parameters directly in the cell (used
// by detailed rows whose caption is hidden — the freed width goes to the
// transition) instead of behind the ⚙ popover.
function TransitionCell({ item, onPatch, onDuration, onOpenGallery, inline = false }: { item: MediaItem; onPatch: (patch: Partial<MediaItem>)=>void; onDuration: (v: number)=>void; onOpenGallery?: () => void; inline?: boolean }) {
  const [open, setOpen] = useState(false)
  const isGL = isGLTransition(item.transition)
  const params = (item.transitionParams as Record<string,string|number>) || {}
  const easing = item.transitionEasing || EASING_DEFAULT
  const reverse = item.transitionReverse || 0
  // ensure transitionTime clamped
  const max = 3600
  return <div className={`transition-cell ${inline ? 'inline-settings' : ''}`}>
    <div className="clip-duration cell-duration transition-duration"><NumberStepper value={item.transitionTime ?? DEFAULT_TRANSITION_SECONDS} min={MIN_TRANSITION_SECONDS} max={max} step={0.1} ariaLabel={`${item.name} transition time`} onChange={v => onPatch({ transitionTime: v })} /><span>sec</span></div>
    <TransitionChip ariaLabel={`${item.name} transition`} className="cell-chip"
      title={`${item.transition}${item.transitionEasing && item.transitionEasing!==EASING_DEFAULT ? ' · '+item.transitionEasing : ''}${item.transitionReverse ? ' · reverse':''} · click to browse all transitions`}
      value={item.transition} onChange={v => {
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
    }} onOpenGallery={onOpenGallery} />
    {!inline && <button type="button" className={`icon-button small ${open?'active':''}`} title={isGL ? 'Edit GL parameters, easing and reverse' : 'Edit easing and reverse — values not shown here are at their defaults'} onClick={()=>setOpen(o=>!o)}><Settings2 size={13}/></button>}
    {inline && <div className="transition-inline">
      <label>Easing <EasingSelect value={easing} onChange={v=>onPatch({transitionEasing: v})}/></label>
      <label className="check-label"><input type="checkbox" checked={Boolean(reverse)} onChange={e=>onPatch({transitionReverse: e.target.checked?1:0})}/><span><Check size={11}/></span> Reverse</label>
      {isGL && <div className="transition-inline-params"><GLParamControls transition={item.transition} params={params} onChange={next=>onPatch({transitionParams: next})}/></div>}
    </div>}
    {!inline && (easing !== EASING_DEFAULT || reverse || (isGL && Object.keys(params).length > 0)) && <em className="cell-summary" title="Non-default transition settings">{easing !== EASING_DEFAULT ? easing : ''}{reverse ? `${easing !== EASING_DEFAULT ? ' · ' : ''}reverse` : ''}{isGL && Object.keys(params).length > 0 ? `${easing !== EASING_DEFAULT || reverse ? ' · ' : ''}${Object.keys(params).length} param${Object.keys(params).length === 1 ? '' : 's'}` : ''}</em>}
    {!inline && open && <div className="transition-popover">
      {isGL && <><FieldLabel>GL parameters <small>{item.transition}</small></FieldLabel><GLParamControls transition={item.transition} params={params} onChange={next=>onPatch({transitionParams: next})}/></>}
      <div className="transition-meta">
        <label>Easing <EasingSelect value={easing} onChange={v=>onPatch({transitionEasing: v})}/></label>
        <label className="check-label"><input type="checkbox" checked={Boolean(reverse)} onChange={e=>onPatch({transitionReverse: e.target.checked?1:0})}/><span><Check size={11}/></span> Reverse</label>
      </div>
      <button className="btn ghost small" onClick={()=>setOpen(false)}><X size={12}/> Close</button>
    </div>}
  </div>
}

// Ruler underneath every storyline row. Timestamps are printed as h:mm:ss so
// a long slideshow reads like a timecode instead of a bare number of seconds.
function TimelineRuler({ start, duration, zoom, audioLength }: { start:number, duration:number, zoom:number, audioLength?: string }) {
  const end = start + duration
  return <div className="line-time-ruler">{[0,.25,.5,.75,1].map(f=>{
    const at = start + duration * f
    // The row end time is printed by the badge on the right, so the last tick
    // only draws its mark instead of repeating the same timecode.
    return <span key={f} style={{left:`${f*100}%`}} title={f === 1 ? undefined : `${formatTimecode(at)} · ${Math.round(at)} s from the start`}><i/>{f === 1 ? '' : formatTimecode(at)}</span>
  })}<b title={`This row ends at ${formatTimecode(end)}`}>{formatTimecode(end)}</b><em title="Timeline zoom">{Math.round(zoom*100)}%</em>{audioLength && <span className="audio-length-indicator" title="Total soundtrack time"><Music2 size={10}/> {audioLength}</span>}</div>
}

// Enter / Exit lanes as one chip: the storyline, the detailed list and the
// multi-select inspector set the lane's first effect (the rest of the lane
// stays) and the duration of all its layers.
function setLaneEffect(stack: TextFxLayer[], phase: 'in' | 'out', effectId: string): TextFxLayer[] {
  const idx = stack.findIndex(l => EFFECTS[l.effect]?.phase === phase)
  if (idx >= 0) return stack.map((l, i) => (i === idx ? { id: l.id, effect: effectId } : l))
  const layer: TextFxLayer = { id: newLayerId(), effect: effectId }
  return phase === 'in' ? [layer, ...stack] : [...stack, layer]
}
function setLaneDuration(stack: TextFxLayer[], phase: 'in' | 'out', seconds: number): TextFxLayer[] {
  return stack.map(l => (EFFECTS[l.effect]?.phase === phase ? { ...l, duration: Math.max(0.05, seconds) } : l))
}
function browserCaptionFor(item: MediaItem): BrowserCaption {
  const change = frameColourChange(item)
  return {
    text: item.text || 'Text', family: item.fontFamily || 'Montserrat', bold: item.textBold ?? true, italic: item.textItalic ?? false,
    colour: item.fontColor || '#ffffff', isFrame: item.type === 'title', background: item.type === 'title' ? item.frameBackground : undefined,
    bgChange: change ? { colourB: change.to, transition: change.transition } : null,
  }
}
function LaneEffectChip({ item, phase, onPick, onStack }: { item: MediaItem; phase: 'in' | 'out'; onPick: (effectId: string) => void; onStack?: (stack: TextFxLayer[]) => void }) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null)
  const chip = laneChip(item.textFx, phase)
  return <>
    <button type="button" className={`transition-chip lane-chip${anchor ? ' open' : ''}`} title={chip.title} onClick={e => setAnchor(e.currentTarget.getBoundingClientRect())}>
      <i className="chip-symbol">{chip.symbol}</i><span className="chip-name">{chip.label}</span>{chip.more > 0 && <i className="chip-kind">+{chip.more}</i>}<ChevronDown size={12} />
    </button>
    {anchor && <TextEffectBrowser request={{ mode: 'add', phase }} anchor={anchor} stack={item.textFx || []} caption={browserCaptionFor(item)}
      onPick={id => { onPick(id); setAnchor(null) }} onPreset={p => { onStack?.(withIds(p.layers)); setAnchor(null) }} onClose={() => setAnchor(null)} />}
  </>
}

function TimelineTextBox({ item, update, selected, onSelect, onEdit }: { item: MediaItem, update: (change: Partial<MediaItem>) => void, selected: string[], onSelect: (edge:'enter'|'exit')=>void, onEdit?: () => void }) {
  const { duration, textStart, textEnd } = normalizedTextTiming(item)
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
  if (item.type !== 'title' && item.textEnabled === false) {
    if (!onEdit) return null
    return <div className="timed-text text-disabled clickable" style={{ left: '0%', width: '100%' }} onClick={e => { e.preventDefault(); e.stopPropagation(); onEdit() }} title="Text is hidden — click to edit text settings, visibility and timing">
    </div>
  }
  const left = textStart / duration * 100
  const width = Math.max(3, (textEnd - textStart) / duration * 100)
  return <div className="timed-text clickable" style={{left:`${left}%`,width:`${width}%`}} onClick={e => { if (e.target === e.currentTarget) { e.preventDefault(); e.stopPropagation(); onEdit?.() } }} title={item.type === 'title' ? `Click to edit text frame · “${item.text}”` : `Click to edit text settings — drag handles to change start/stop timing`}>
    {(() => { const chip = laneChip(item.textFx, 'in'); return <button className={`text-transition enter ${selected.includes(`${item.id}-enter`)?'selected':''}`} title={`Text animation — ${chip.title}`} onClick={e => { e.preventDefault(); e.stopPropagation(); onSelect('enter') }} onPointerDown={e => e.stopPropagation()}>{chip.symbol}{chip.more > 0 && <sup>+{chip.more}</sup>}</button> })()}
    <i className="timing-handle left" title={`Appears at ${textStart.toFixed(1)}s`} onPointerDown={e=>changeTiming('start',e)} onClick={e => e.stopPropagation()}/>
    <input value={item.text} placeholder="+ Add text" onChange={e=>update({text:e.target.value})} onPointerDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()} onDoubleClick={e => { e.preventDefault(); e.stopPropagation(); onEdit?.() }} title={item.type === 'title' ? `Edit text: double-click for full settings` : `Edit text on picture: double-click for style, position and motion`}/>
    <i className="timing-handle right" title={`Disappears at ${textEnd.toFixed(1)}s`} onPointerDown={e=>changeTiming('end',e)} onClick={e => e.stopPropagation()}/>
    {(() => { const chip = laneChip(item.textFx, 'out'); return <button className={`text-transition exit ${selected.includes(`${item.id}-exit`)?'selected':''}`} title={`Text animation — ${chip.title}`} onClick={e => { e.preventDefault(); e.stopPropagation(); onSelect('exit') }} onPointerDown={e => e.stopPropagation()}>{chip.symbol}{chip.more > 0 && <sup>+{chip.more}</sup>}</button> })()}
  </div>
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

// A fresh editor starts with this demo name; "New project" wipes to BLANK_NAME.
// The output filename is always `safeFilename()` of whichever name is showing.
const STARTER_NAME = 'Portugal summer'
const BLANK_NAME = 'Untitled'

function App() {
  const [media, setMedia] = useState(initialMedia)
  const [projectName, setProjectName] = useState(STARTER_NAME)
  const [projectId, setProjectId] = useState<number|null>(null)
  const [backendOnline, setBackendOnline] = useState(false)
  const [capabilities, setCapabilities] = useState({ffmpeg:false,quickSync:false,vaapi:false,vaapiError:'',cpuEncoding:false,hasGL:true,hasEasing:true})
  // Uploads volume status from /api/health — the picker warns before a file is chosen.
  const [uploadsStatus, setUploadsStatus] = useState<UploadsStatus | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string|null>(null)
  // Fast mode is the default diagnostic preview: it keeps text-bearing holds
  // and every configured transition, while omitting static picture holds and
  // soundtrack work. Standard mode remains available when the whole selected
  // sequence needs to be watched.
  const [previewMode, setPreviewMode] = useState<PreviewMode>(() => {
    try { return window.localStorage.getItem('slideshow.previewMode') === 'standard' ? 'standard' : 'fast' }
    catch { return 'fast' }
  })
  useEffect(() => {
    try { window.localStorage.setItem('slideshow.previewMode', previewMode) } catch { /* private mode */ }
  }, [previewMode])
  // What the last preview covered: 'all' or the number of selected slides.
  const [previewScope, setPreviewScope] = useState<number|'all'>('all')
  const [previewRunMode, setPreviewRunMode] = useState<PreviewMode>('fast')
  const [activeTab, setActiveTab] = useState<'editor' | 'renders'>('editor')
  const [showBrowser, setShowBrowser] = useState(false)
  const [showAudioBrowser, setShowAudioBrowser] = useState(false)
  const [showPreview, setShowPreview] = useState(false)
  const [isPlaying, setPlaying] = useState(false)
  const [toast, setToast] = useState('')
  const [globalDuration, setGlobalDuration] = useState(DEFAULT_TRANSITION_SECONDS)
  // Project-wide default hold time for photos/text frames (see DEFAULT_SLIDE_SECONDS).
  const [globalSlideDuration, setGlobalSlideDuration] = useState(DEFAULT_SLIDE_SECONDS)
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
  const [outputFilename, setOutputFilename] = useState(safeFilename(STARTER_NAME))
  const [rendering, setRendering] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [progress, setProgress] = useState(0)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  // The newest finished MP4 of this project (size + file route), so the
  // review panel can offer "Download" the moment a render completes —
  // Option 1 of docs/output-download-options.md.
  const [finishedRender, setFinishedRender] = useState<{ url: string; name: string; bytes: number | null } | null>(null)
  // Live render feedback: which stage FFmpeg is in, when the backend started it
  // (its own clock, so a refresh does not reset the countdown) and the smoothed
  // progress rate the countdown is derived from.
  const [jobStage, setJobStage] = useState('')
  const [jobStartedAt, setJobStartedAt] = useState<number | null>(null)
  const [etaSample, setEtaSample] = useState<EtaSample | null>(null)
  const [etaTick, setEtaTick] = useState(0)
  const [renderRates, setRenderRates] = useState<Partial<Record<JobKind, RenderRate>>>(() => loadRenderRates())
  // What the job looked like when it was started, plus the backend's start
  // stamp.  These live in refs, not state: the handler that records the
  // measurement runs long after the click, and a closure over state would still
  // hold the values from the render that started the job (all of them empty).
  const jobBaseline = useRef<{ timelineSeconds: number; itemCount: number; resolution: string; encoder: string } | null>(null)
  const jobStartedRef = useRef<number | null>(null)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [selectedTransitions, setSelectedTransitions] = useState<number[]>([])
  const [transitionPreviewId, setTransitionPreviewId] = useState<number | null>(null)
  const [selectedTextTransitions, setSelectedTextTransitions] = useState<string[]>([])
  const [detailTextEditor, setDetailTextEditor] = useState<{id:number,edge:'enter'|'exit'}|null>(null)
  // Per-picture/video caption editor. Title frames use editingTextFrame and
  // never enter this flow.
  const [editingPictureText, setEditingPictureText] = useState<number | null>(null)
  const [effectPicker, setEffectPicker] = useState<number | null>(null)
  // Default bulk effect is None: applying motion to every photo must be a
  // deliberate choice, not something the dropdown pre-selects.
  const [bulkEffect, setBulkEffect] = useState('None')
  // Bulk picture look ('none' = back to the original).
  const [bulkFilter, setBulkFilter] = useState('none')
  const [bulkTransition, setBulkTransition] = useState('Dissolve')
  const [randomScope, setRandomScope] = useState<RandomScope>('both')
  // Whether randomizing transitions also draws fresh values for every
  // transition parameter (easing, reverse, and GL-specific values). Persisted
  // like favourites/recents so the decision survives a reload. Read the old
  // GL-only key as a migration path for existing users.
  const [randomizeParams, setRandomizeParams] = useState<boolean>(() => {
    try {
      const current = window.localStorage.getItem('slideshow.randomTransitionParams')
      return (current ?? window.localStorage.getItem('slideshow.randomGlParams')) === '1'
    } catch { return false }
  })
  useEffect(() => {
    try { window.localStorage.setItem('slideshow.randomTransitionParams', randomizeParams ? '1' : '0') } catch { /* private mode */ }
  }, [randomizeParams])
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
  // Dark outline + shadow behind picture captions (Default text style). On
  // unless switched off: white text on a bright photo is unreadable without it.
  const [textOutline, setTextOutline] = useState(true)
  const [defaultTextX, setDefaultTextX] = useState(50)
  // Default text animation for new captions and text frames (saved with the
  // project like the rest of the "Default text style" settings).
  // Project default text animation (a stack of effects) for new captions.
  const [defaultStack, setDefaultStack] = useState<TextFxLayer[]>(() => defaultTextFx())
  const [defaultTextY, setDefaultTextY] = useState(72)
  const [showTextStyles, setShowTextStyles] = useState(false)
  const [editingTextFrame, setEditingTextFrame] = useState<number | null>(null)
  // Id of a text frame created by "Add text frame" that has not been saved
  // yet: Cancel/close removes it again, only Done keeps it in the storyline.
  const [pendingTextFrame, setPendingTextFrame] = useState<number | null>(null)
  // Files uploading from this device into the NAS uploads volume ("Upload
  // from this device" in the media picker and drag & drop onto the storyline).
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const uploadCancelers = useRef<Map<number, () => void>>(new Map())
  const uploadIdRef = useRef(1)
  const [storyDrop, setStoryDrop] = useState(false)
  // Bumped after an upload batch so an open media picker re-reads the uploads root.
  const [browserReloadKey, setBrowserReloadKey] = useState(0)
  const [audioTracks, setAudioTracks] = useState<AudioTrack[]>([])
  const [draggedAudioId, setDraggedAudioId] = useState<number | null>(null)
  const [showFolderPicker, setShowFolderPicker] = useState(false)
  const [showProjectLoader, setShowProjectLoader] = useState(false)
  // "Save project" opens a browse popup: pick the volume, the folder and the
  // filename. The last destination is remembered so saving twice in a row does
  // not mean walking the tree again.
  const [showProjectFileSave, setShowProjectFileSave] = useState(false)
  const [projectFileFolder, setProjectFileFolder] = useState<{ root: ProjectRoot, folder: string }>({ root: 'output', folder: '' })
  const [showNewProjectConfirm, setShowNewProjectConfirm] = useState(false)
  const [showRenderConfirm, setShowRenderConfirm] = useState(false)
  const [overwritePath, setOverwritePath] = useState<string | null>(null)
  // Covers the short window between clicking Preview/Render and receiving the
  // backend job id, so Stop all also cancels a submission that is still saving.
  const jobCancelRequested = useRef(false)
  // Storyline preview lightbox: it tracks the previewed item id (not a frozen
  // URL) so the popup can walk the storyline with prev/next and delete the
  // shown item directly, always reflecting the live media list.
  const [storyPreviewId, setStoryPreviewId] = useState<number | null>(null)
  // Movie cut/crop editor, stacked on top of the media lightbox.
  const [editingMovieId, setEditingMovieId] = useState<number | null>(null)
  // Which clip's "Edit picture" (filters) popup is open, if any.
  const [lookItemId, setLookItemId] = useState<number | null>(null)
  // The "Edit picture" popup has two tabs; the lightbox button you pressed
  // decides which one opens.
  const [lookTab, setLookTab] = useState<'filters' | 'crop'>('filters')
  const openLookEditor = (item: MediaItem, tab: 'filters' | 'crop' = 'filters') => { setLookTab(tab); setLookItemId(item.id) }
  // Standalone transition gallery (browse every example without picking one).
  const [showTransitionGallery, setShowTransitionGallery] = useState(false)
  const openMediaLightbox = (item: MediaItem) => {
    // Text frames preview without a source file (colours + caption are drawn
    // live); photos and movies still need a readable file.
    if (item.type !== 'title' && !itemThumbUrl(item)) return
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
  // Storyline lightbox navigation walks the media in storyline order. Text
  // frames are part of it: they preview as their rendered frame (colours +
  // caption) and carry the same stacked "Edit frame" button movies have.
  const previewItems = useMemo(() => media, [media])
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
    if(saved.project){setProjectName(saved.project.name)}
    const savedTextDefaults = {
      fontFamily: String(saved.textDefaults?.fontFamily || 'Montserrat'),
      fontSize: Number(saved.textDefaults?.fontSize) || 48,
      fontColor: String(saved.textDefaults?.fontColor || '#ffffff'),
      bold: saved.textDefaults?.bold !== false,
      italic: saved.textDefaults?.italic === true,
      underline: saved.textDefaults?.underline === true,
      outline: saved.textDefaults?.outline !== false,
      textX: Number.isFinite(Number(saved.textDefaults?.textX)) ? Number(saved.textDefaults.textX) : 50,
      textY: Number.isFinite(Number(saved.textDefaults?.textY)) ? Number(saved.textDefaults.textY) : 72,
      fxEnter: normalizeTextEffect(saved.textDefaults?.textFxEnter, 'enter'),
      fxWhile: normalizeTextEffect(saved.textDefaults?.textFxWhile, 'while'),
      fxExit: normalizeTextEffect(saved.textDefaults?.textFxExit, 'exit'),
      fxWhileSpeed: Number(saved.textDefaults?.textFxWhileSpeed) || 2,
    }
    // Default text animation: a stack (textDefaults.textFx); projects saved
    // before stacks existed carry three default slot labels instead.
    const savedDefaultStack: TextFxLayer[] = Array.isArray(saved.textDefaults?.textFx)
      ? normalizeTextFx(saved.textDefaults.textFx)
      : withIds(legacyToStack({ type: 'image' }, savedTextDefaults))
    if(Array.isArray(saved.media)){
      const normalized = saved.media.map((m0:any)=> {
        let m: any = m0
        if (m.transition) m.transition = normalizeTransition(m.transition)
        if (m.frameTransition) m.frameTransition = normalizeTransition(m.frameTransition)
        // ensure transitionParams is object
        if (typeof m.transitionParams === 'string') { try{ m.transitionParams = JSON.parse(m.transitionParams)}catch{ m.transitionParams = {}}}
        if (!m.transitionEasing) m.transitionEasing = EASING_DEFAULT
        if (m.transitionReverse == null) m.transitionReverse = 0
        // Materialise a picture caption's first style from the saved project
        // defaults. This is the migration for legacy items: captions keep the
        // look they had before per-item editing existed, but can now be changed
        // independently without borrowing a second timing/style state.
        if (m.type !== 'title') {
          if (!m.fontFamily) m.fontFamily = savedTextDefaults.fontFamily
          if (!Number.isFinite(Number(m.fontSize))) m.fontSize = savedTextDefaults.fontSize
          if (!m.fontColor) m.fontColor = savedTextDefaults.fontColor
          if (m.textBold == null) m.textBold = savedTextDefaults.bold
          if (m.textItalic == null) m.textItalic = savedTextDefaults.italic
          if (m.textUnderline == null) m.textUnderline = savedTextDefaults.underline
          if (m.textOutline == null) m.textOutline = savedTextDefaults.outline
          if (!Number.isFinite(Number(m.textX))) m.textX = savedTextDefaults.textX
          if (!Number.isFinite(Number(m.textY))) m.textY = savedTextDefaults.textY
          if (m.textEnabled == null) m.textEnabled = true
        }
        // Stacked text effects: items saved before the stack existed carry the
        // v1 slots (enter / while / exit labels) and the animation toggles.
        // They become textFx layers here, the v1 fields are dropped (only
        // textFx is written from now on) — backend legacy_to_stack() maps the
        // same way for projects rendered without being opened.
        m = migrateLegacyTextFx(m, savedTextDefaults)
        // Older projects may contain a caption window that reaches into the
        // following transition. Normalize it on load so the timeline display,
        // persisted snapshot, and renderer all use the same hold boundary.
        return normalizeItemTextTiming(m as MediaItem)
      })
      setMedia(normalized)
    }
    if(saved.textDefaults){
      setFontFamily(savedTextDefaults.fontFamily); setFontSize(String(savedTextDefaults.fontSize)); setFontColor(savedTextDefaults.fontColor)
      setTextBold(savedTextDefaults.bold); setTextItalic(savedTextDefaults.italic); setTextUnderline(savedTextDefaults.underline); setTextOutline(savedTextDefaults.outline)
      setDefaultTextX(savedTextDefaults.textX); setDefaultTextY(savedTextDefaults.textY); setDefaultStack(savedDefaultStack)
    } else {
      // Projects from before text defaults existed use the same built-ins as a
      // fresh project instead of inheriting the defaults of the project loaded
      // immediately before them.
      setFontFamily('Montserrat'); setFontSize('48'); setFontColor('#ffffff'); setTextBold(true); setTextItalic(false); setTextUnderline(false); setTextOutline(true)
      setDefaultTextX(50); setDefaultTextY(72); setDefaultStack(defaultTextFx())
    }
    if(saved.soundtrack){setAudioTracks(saved.soundtrack.tracks||[]);setAudioPolicy(saved.soundtrack.policy);setAudioVolume(saved.soundtrack.volume);setAudioFade(saved.soundtrack.fadeOut);setAudioFadeDuration(clampFade(saved.soundtrack.fadeDuration,2));setAudioFadeTail(clampFade(saved.soundtrack.fadeTail,0));setAudioNormalize(saved.soundtrack.normalize!==false);setAudioNormalizeTarget(clampLufs(saved.soundtrack.normalizeTarget))}
    if(saved.output){setResolution(saved.output.resolution);setFrameRate(saved.output.frameRate);setBitrate(saved.output.bitrate);setEncoder(saved.output.encoder);setOutputPath(saved.output.path)
      // A project saved before the two fields were linked usually still carries
      // the generated filename ('slideshow', 'movie', 'Portugal-summer'); that
      // one now inherits the project name. A filename the user chose themselves
      // is kept — the Output pane says so, and the first edit links them again.
      const savedName = String(saved.project?.name ?? '')
      const savedFilename = String(saved.output.filename ?? '')
      setOutputFilename(isGeneratedFilename(savedFilename) ? safeFilename(savedName) || FILENAME_FALLBACK : savedFilename)}
    if(saved.timeline){setTimelineRows(saved.timeline.rows);setTimelineZoom(saved.timeline.zoom)}
    // Projects saved before the defaults existed simply keep the built-in values.
    setGlobalSlideDuration(clampSlideDefault(saved.defaults?.slideSeconds))
    setGlobalDuration(clampTransitionDefault(saved.defaults?.transitionSeconds))
  }
  useEffect(()=>{
    const restore=async()=>{try{
      const health=await fetch('/api/health');if(!health.ok)throw new Error();const healthData=await health.json();setCapabilities(healthData.capabilities);setUploadsStatus(healthData.uploads||null);setBackendOnline(true)
      const list=await fetch('/api/projects').then(r=>r.json())
      if(list.length){const saved=await fetch(`/api/projects/${list[0].id}`).then(r=>r.json());applySavedProject(saved);await resumeActiveJob(list[0].id);return}
    }catch{setBackendOnline(false)}
      try{const raw=localStorage.getItem('slideshow.project.mock');if(raw)applySavedProject(JSON.parse(raw))}catch{localStorage.removeItem('slideshow.project.mock')}
    };void restore()
  },[])

  const notify = (message: string) => { setToast(message); window.setTimeout(() => setToast(''), message.includes('\n') ? 12000 : 3500) }
  const audioPreview = useAudioPreview(message => notify(message))
  /**
   * The project name and the output filename are one name. Typing in the header
   * renames the file too (through `safeFilename`, which only touches characters
   * a filename cannot hold); typing in the Output pane renames the project.
   */
  const renameProject = (value: string) => { setProjectName(value); setOutputFilename(safeFilename(value)) }
  // The filename field keeps what you type while you type it — sanitising on
  // every keystroke would swallow a colon mid-word — and settles on blur.
  const renameOutputFile = (value: string) => { setOutputFilename(value); setProjectName(value) }
  const commitOutputFile = () => { const clean = safeFilename(outputFilename); setOutputFilename(clean); setProjectName(clean) }
  // True only for a project loaded from before the two fields were linked.
  const nameAndFileDiffer = safeFilename(projectName) !== safeFilename(outputFilename)

  const projectSnapshot = () => ({
    schemaVersion: 1, project: { name: projectName }, media,
    textDefaults: { fontFamily, fontSize:Number(fontSize), fontColor, bold:textBold, italic:textItalic, underline:textUnderline, outline:textOutline, textX: defaultTextX, textY: defaultTextY, textFx: defaultStack.map(({ id, ...l }) => l) },
    soundtrack: { tracks:audioTracks, policy:audioPolicy, volume:audioVolume, fadeOut:audioFade, fadeDuration:audioFadeDuration, fadeTail:audioFadeTail, normalize:audioNormalize, normalizeTarget:audioNormalizeTarget },
    // Sanitised here as well as on blur, so a render started straight after
    // typing can never be handed a name the filesystem would reject.
    output: { resolution, frameRate, bitrate, encoder, path:outputPath, filename: safeFilename(outputFilename) || FILENAME_FALLBACK },
    timeline: { rows:timelineRows, zoom:timelineZoom },
    // Project-wide defaults for new slides/transitions (the two bulk-bar steppers).
    defaults: { slideSeconds: clampSlideDefault(globalSlideDuration), transitionSeconds: clampTransitionDefault(globalDuration) },
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
    schemaVersion: 1, project: { name: BLANK_NAME }, media: [],
    textDefaults: { fontFamily: 'Montserrat', fontSize: 48, fontColor: '#ffffff', bold: true, italic: false, underline: false, outline: true, textX: 50, textY: 72, textFx: defaultTextFx().map(({ id, ...l }) => l) },
    soundtrack: { tracks: [], policy: 'Loop & trim', volume: 78, fadeOut: true, fadeDuration: 2, fadeTail: 0, normalize: true, normalizeTarget: -14 },
    output: { resolution: 'Full HD · 1080p', frameRate: '30 fps', bitrate: '8 Mbps · High', encoder: 'Auto · Quick Sync', path: '/output', filename: safeFilename(BLANK_NAME) },
    timeline: { rows: 'auto', zoom: 1 },
    defaults: { slideSeconds: DEFAULT_SLIDE_SECONDS, transitionSeconds: DEFAULT_TRANSITION_SECONDS },
  })
  // Wipe the editor back to a completely blank project. New project also
  // clears every render work file and proxy preview, but leaves final MP4s in
  // the selected output folder untouched.
  const startNewProject = async () => {
    setProjectId(null)
    setProjectName(BLANK_NAME)
    setMedia([])
    setAudioTracks([])
    setAudioPolicy('Loop & trim'); setAudioVolume(78); setAudioFade(true); setAudioFadeDuration(2); setAudioFadeTail(0); setAudioNormalize(true); setAudioNormalizeTarget(-14)
    setResolution('Full HD · 1080p'); setFrameRate('30 fps'); setBitrate('8 Mbps · High'); setEncoder('Auto · Quick Sync')
    setOutputPath('/output'); setOutputFilename(safeFilename(BLANK_NAME))
    setFontFamily('Montserrat'); setFontSize('48'); setFontColor('#ffffff'); setTextBold(true); setTextItalic(false); setTextUnderline(false); setTextOutline(true); setDefaultTextX(50); setDefaultTextY(72); setDefaultStack(defaultTextFx())
    setTimelineRows('auto'); setTimelineZoom(1)
    setGlobalSlideDuration(DEFAULT_SLIDE_SECONDS); setGlobalDuration(DEFAULT_TRANSITION_SECONDS)
    setSelectedIds([]); setSelectedTransitions([]); setSelectedTextTransitions([])
    setDetailTextEditor(null); setEditingPictureText(null); setEditingTextFrame(null)
    setShowNewProjectConfirm(false); setShowProjectLoader(false); setShowRenderConfirm(false)
    setPreviewUrl(null); setShowPreview(false); setActiveJobId(null); setRendering(false); setPreviewing(false); setProgress(0); setJobStage(''); setEtaSample(null); jobCancelRequested.current = true
    let cleanupMessage = 'render files could not be cleared'
    try {
      const response = await fetch('/api/cleanup', { method: 'POST' })
      if (!response.ok) throw new Error(await readApiError(response, 'Cleanup failed'))
      const result = await response.json()
      cleanupMessage = `cleared ${result.deleted_files} temporary file${result.deleted_files === 1 ? '' : 's'} and ${result.deleted_dirs} render director${result.deleted_dirs === 1 ? 'y' : 'ies'}`
    } catch { /* the blank project is still useful while the backend is offline */ }
    try {
      await persistSnapshot(blankProjectSnapshot(), true, true)
      notify(`Started a new blank project · ${cleanupMessage}`)
    } catch {
      notify(`Started a new blank project — ${cleanupMessage}; save it once the backend is back`)
    }
  }
  const requestNewProject = () => {
    setShowProjectLoader(false)
    if (media.length || audioTracks.length) setShowNewProjectConfirm(true)
    else startNewProject()
  }
  const saveProject = async () => {
    try{await persistProject()}catch(error){setBackendOnline(false);notify(`SQLite save failed: ${error instanceof Error?error.message:'Unknown error'}`)}
  }
  /**
   * Load a project from a file on one of the mounts. The snapshot goes through
   * the same code path a SQLite row uses, and is then stored as a *fresh* row —
   * the file is a copy, not the project the editor is working on, so a refresh
   * keeps what you just opened instead of dropping back to the previous one.
   */
  const loadProjectFile = async (file: ProjectFileInfo) => {
    setShowProjectLoader(false)
    applySavedProject(file.project as any)
    setProjectId(null)
    const label = file.projectName || file.name
    try {
      const id = await persistSnapshot(file.project, true, true)
      setBackendOnline(true)
      notify(`Project “${label}” loaded from ${file.path} · stored as project #${id}`)
    } catch {
      notify(`Project “${label}” loaded from ${file.path} — SQLite is offline, so save it once the backend is back`)
    }
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
  /**
   * "Remove black bars" for the crop editor: the backend runs FFmpeg's
   * cropdetect on the real file and returns the kept rectangle in fractions of
   * the turned picture. Files that only exist in the browser (data/blob URLs)
   * have no server path, so the tool is hidden there.
   */
  const detectBars = async (target: MediaItem) => {
    const full = mediaItemPath(target)
    if (!full || /^(https?:|data:|blob:)/.test(full)) return null
    const root = mediaRootFromPath(full, target.type === 'video' ? 'videos' : 'photos')
    if (root === 'music') return null
    return serverCropDetect(root, full, normalizeRotation(target.rotation), target.type === 'video' ? 4 : 1)
  }

  const patch = (id: number, update: Partial<MediaItem>) => setMedia(items => items.map(item => {
    if (item.id !== id) return item
    const next = { ...item, ...update }
    // Title-frame edits and timing-handle edits go through the same hold-bound
    // rule as project loading. A transition is never allowed to cover a title
    // text event merely because a stale value was patched into the item.
    return next.type === 'title' || 'textStart' in update || 'textEnd' in update
      ? normalizeItemTextTiming(next)
      : next
  }))
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
    next[index] = resizeClip(next[index], value)
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
      setRenderRates(clearRenderRates())
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
  // One-click select/deselect of every slide (photos, videos and text
  // frames) — offered above the overall timeline and in the detailed list's
  // column head.
  const allSlidesSelected = media.length > 0 && selectedIds.length === media.length
  const toggleAllSlides = () => setSelectedIds(allSlidesSelected ? [] : media.map(x => x.id))
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
  // Turn browsed or uploaded entries into storyline items: probe each video's
  // native length so the timeline hold covers the complete movie before the
  // transition to the next picture. Images use the project's slide default; a
  // failed probe falls back to 10 s. Returns how many items were added.
  const addFilesToStoryline = async (files: any[]) => {
    const slideSeconds = clampSlideDefault(globalSlideDuration)
    const transitionSeconds = clampTransitionDefault(globalDuration)
    const additions: MediaItem[] = []
    for (let index = 0; index < files.length; index++) {
      const file = files[index]
      const isVideo = file.kind === 'video'
      // Every playable file is accepted — photos and videos alike — no
      // matter which location it came from. The stream root follows the
      // file's real mount (/photos, /videos or /uploads), never its kind.
      const root = mediaRootFromPath(file.path, isVideo ? 'videos' : 'photos')
      const src = mediaFileUrl(root, file.path)
      let duration = isVideo ? 10 : slideSeconds
      if (isVideo) {
        const needsServerOnly = /\.(avi|wmv|asf|mpg|mpeg|ts|mts|m2ts|flv|f4v|3gp|3gpp|vob|dav|mxf|mod|tod|divx|mkv)$/i.test(file.name)
        try {
          if (!needsServerOnly) {
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
          } else {
            duration = 0
          }
          // AVI/WMV/MPEG-PS/AVCHD (e.g. 640×480 29.97 fps 3800 kbps mono) from cameras
          // and Windows are renderable by FFmpeg but generally not decodable by
          // HTMLVideoElement. Use the server ffprobe directly to avoid an 8 s timeout.
          if (duration <= 0) duration = await serverVideoDuration(root, file.path)
          duration = duration > 0 ? Math.max(MIN_CLIP_SECONDS, duration) : 10
        } catch { duration = 10 }
      }
      additions.push({
        id: Date.now() + index, name: file.name, path: file.path, src,
        type: file.kind as 'image' | 'video', duration,
        effect: 'None',
        transition: 'Fade', transitionTime: transitionSeconds,
        // Movies keep their own sound by default; the soundtrack ducks around them.
        audioSource: isVideo ? 'original' : undefined,
        text: '', textMode: 'overlay', textEnabled: false, textStart: 0, textEnd: duration,
        // Text animation: a copy of the project's default stack.
        textFx: withIds(defaultStack),
        // New picture captions start as a copy of the current project style.
        // Saving the per-picture dialog then keeps these fields independent.
        fontFamily, fontSize: Number(fontSize) || 48, fontColor, textBold, textItalic, textUnderline, textOutline,
        textX: defaultTextX, textY: defaultTextY, frameBackground: '#30382a',
      })
    }
    setMedia(items => [...items, ...additions])
    return additions.length
  }
  // Upload photos/movies from this device to the NAS uploads volume, one
  // request per file with progress and cancel; every completed file joins
  // the storyline immediately, exactly like a file picked from a mount.
  const startUploads = (fileList: File[], folder = '') => {
    const accepted = fileList.filter(isUploadableFile)
    const ignored = fileList.length - accepted.length
    if (!accepted.length) { notify('No photos or movies in that selection — uploads accept pictures and videos only'); return }
    const base = uploadIdRef.current
    uploadIdRef.current += accepted.length
    const items: UploadItem[] = accepted.map((file, index) => ({ id: base + index, name: file.name, total: file.size, sent: 0, status: 'uploading' as const }))
    setUploads(current => [...current, ...items])
    void (async () => {
      let ok = 0
      const failures: string[] = []
      for (let index = 0; index < accepted.length; index++) {
        const item = items[index]
        const { promise, cancel } = uploadFile(accepted[index], (sent, total) => {
          setUploads(current => current.map(u => u.id === item.id ? { ...u, sent, total } : u))
        }, folder)
        uploadCancelers.current.set(item.id, cancel)
        const finish = (status: UploadItem['status'], error?: string) => setUploads(current => current.map(u => u.id === item.id ? { ...u, status, error, sent: status === 'done' ? u.total : u.sent } : u))
        try {
          const data = await promise
          const entry = data.added?.[0]
          const error = data.errors?.[0]?.error
          if (entry) {
            ok++
            finish('done')
            await addFilesToStoryline([entry])
          } else {
            failures.push(`${item.name}: ${error || 'rejected'}`)
            finish('error', error || 'rejected')
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Upload failed'
          if (/aborted/i.test(message)) setUploads(current => current.filter(u => u.id !== item.id))
          else { failures.push(`${item.name}: ${message}`); finish('error', message) }
        } finally {
          uploadCancelers.current.delete(item.id)
        }
      }
      if (ignored) failures.push(`${ignored} file${ignored === 1 ? '' : 's'} ignored (photos and movies only)`)
      if (ok) notify(`Uploaded ${ok} file${ok === 1 ? '' : 's'} to /uploads${folder ? `/${folder}` : ''} and added ${ok === 1 ? 'it' : 'them'} to the storyline`)
      if (failures.length) notify(`Upload problem — ${failures[0]}${failures.length > 1 ? ` · +${failures.length - 1} more` : ''}\nThe reasons stay listed in the upload tray (bottom right).`)
      setBrowserReloadKey(key => key + 1)
      // Successful rows fade out on their own; failed rows stay until the
      // user clears the tray, so the reason can actually be read.
      window.setTimeout(() => setUploads(current => current.filter(u => u.status !== 'done')), 6000)
    })()
  }
  const addTitleFrame = () => {
    const id = Date.now()
    const duration = clampSlideDefault(globalSlideDuration)
    const transitionTime = clampTransitionDefault(globalDuration)
    setMedia(items => [...items, { id, name: 'Text frame', path: 'Generated frame', src: '', type: 'title', duration, effect: 'None', transition: 'Fade', transitionTime, text: 'Your title here', textMode: 'frame', textStart: 0, textEnd: duration, textFx: withIds(defaultStack), textX: defaultTextX, textY: defaultTextY, frameBackground: '#30382a', fontFamily, fontSize: Number(fontSize) || 48, fontColor, textBold, textItalic, textUnderline, textSteadySeconds: 0 }])
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
  // Selected Enter / Exit edges (storyline text lane): set the lane's first
  // effect and/or the duration of every layer in that lane.
  const updateSelectedTextTransitions = (effectId?: string, duration?: number) => setMedia(items => items.map(item => {
    let stack = item.textFx || []
    if (selectedTextTransitions.includes(`${item.id}-enter`)) {
      if (effectId !== undefined) stack = setLaneEffect(stack, 'in', effectId)
      if (duration !== undefined) stack = setLaneDuration(stack, 'in', duration)
    }
    if (selectedTextTransitions.includes(`${item.id}-exit`)) {
      if (effectId !== undefined) stack = setLaneEffect(stack, 'out', effectId)
      if (duration !== undefined) stack = setLaneDuration(stack, 'out', duration)
    }
    return stack === item.textFx ? item : { ...item, textFx: stack }
  }))
  // Same scope as the Ken Burns buttons it sits beside: the selected clips, or
  // every photo when nothing is selected.
  const randomizeTextTransitions = () => {
    // Randomize picks curated presets (whole stacks), so a random caption
    // always looks intentional. Fonts and frame colours stay as they are;
    // colour-change looks only land on text frames that have a colour B.
    const ids = selectedIds.length ? selectedIds : media.filter(x => x.type === 'image').map(x => x.id)
    if (!ids.length) { notify('No photos to update · add photos or select clips first'); return }
    const picked: string[] = []
    setMedia(items=>items.map(item=>{
      if (!ids.includes(item.id)) return item
      const isFrame = item.type === 'title'
      const hasBg = isFrame && /^#[0-9a-f]{6}$/i.test(String(item.frameBackground2 || '')) && String(item.frameBackground2).toLowerCase() !== String(item.frameBackground).toLowerCase()
      const preset = presetForRandom(isFrame, hasBg, PRESETS.filter(p => !p.frame || hasBg))
      if (!preset) return item
      picked.push(preset.label)
      return { ...item, textFx: withIds(preset.layers) }
    }))
    notify(`Text effects randomized from ${PRESETS.length} presets · ${ids.length} item${ids.length===1?'':'s'}`)
  }
  const applyBulkEffect = () => {
    const ids = selectedIds.length ? selectedIds : media.filter(x => x.type === 'image').map(x => x.id)
    setMedia(items => items.map(item => ids.includes(item.id) && item.type === 'image' ? { ...item, effect: bulkEffect } : item))
    notify(`${bulkEffect} applied to ${ids.length} photo${ids.length === 1 ? '' : 's'}`)
  }
  // Same selection rules as the Ken Burns apply: the clips you selected, or
  // every photo and movie when nothing is selected. Text frames are generated,
  // so they never take a filter. A fresh preset resets intensity and sliders.
  const applyBulkFilter = () => {
    const ids = selectedIds.length ? selectedIds : media.filter(x => x.type !== 'title').map(x => x.id)
    if (!ids.length) { notify('Nothing to filter · add media or select clips first'); return }
    setMedia(items => items.map(item => ids.includes(item.id) && item.type !== 'title' ? { ...item, filter: bulkFilter, filterAmount: 1, filterAdjust: {} } : item))
    notify(`${lookLabel({ filter: bulkFilter })} applied to ${ids.length} clip${ids.length === 1 ? '' : 's'}`)
  }
  const randomizeBulkEffect = () => {
    const ids = selectedIds.length ? selectedIds : media.filter(x=>x.type==='image').map(x=>x.id)
    const kenBurns = effects.filter(x=>x.startsWith('Ken Burns'))
    setMedia(items=>items.map(item=>ids.includes(item.id)&&item.type==='image'?{...item,effect:kenBurns[Math.floor(Math.random()*kenBurns.length)]}:item))
    notify(`Random Ken Burns effects applied to ${ids.filter(id=>media.find(x=>x.id===id)?.type==='image').length} selected photos`)
  }
  const applyBulkTransition = () => {
    const eligible = media.slice(0, -1).map(x => x.id)
    const ids = selectedTransitions.length ? selectedTransitions : eligible
    setMedia(items => items.map(item => ids.includes(item.id)
      ? { ...item, transition: bulkTransition, transitionParams: isGLTransition(bulkTransition) ? (item.transitionParams || {}) : undefined }
      : item))
    notify(`${bulkTransition} applied to ${ids.length} transition${ids.length === 1 ? '' : 's'}`)
  }
  // "Randomize all": transitions only — Ken Burns motion (effect) and
  // transitionTime are left untouched. When enabled, every supported
  // parameter is randomized alongside the transition name.
  const randomize = () => setMedia(items => items.map((item, index) => {
    if (index >= items.length - 1) return item
    const picked = pickRandomTransition(randomScope)
    const settings = randomizeParams
      ? randomTransitionSettings(picked)
      : { transitionParams: isGLTransition(picked) ? {} : undefined }
    return { ...item, transition: picked, ...settings }
  }))
  // Randomize only the transitions that lead from one selected slide to the
  // next selected slide. A transition belongs to the clip it leaves, so a
  // pair counts only when the clip AND its neighbour are both selected —
  // a selection with gaps leaves the gap's transitions untouched. The
  // random source and the params checkbox above apply here too.
  const randomizeSelectedTransitions = () => {
    if (selectedIds.length < 2) { notify('Select at least two slides — this randomizes the transitions between neighbouring selected slides'); return }
    const selected = new Set(selectedIds)
    const pairs = new Set(media.filter((item, index) => {
      const next = media[index + 1]
      return next && selected.has(item.id) && selected.has(next.id)
    }).map(x => x.id))
    if (!pairs.size) { notify('No adjacent selected slides — only neighbouring slides in the selection share a transition'); return }
    setMedia(items => items.map(item => {
      if (!pairs.has(item.id)) return item
      const picked = pickRandomTransition(randomScope)
      const settings = randomizeParams
        ? randomTransitionSettings(picked)
        : { transitionParams: isGLTransition(picked) ? {} : undefined }
      return { ...item, transition: picked, ...settings }
    }))
    notify(`Randomized ${pairs.size} transition${pairs.size === 1 ? '' : 's'} between ${selectedIds.length} selected slides · ${randomScopeLabels[randomScope]}${randomizeParams ? ' · transition parameters randomized' : ''}`)
  }
  const applyDuration = () => {
    const value = clampTransitionDefault(globalDuration)
    // Only the clips that actually lead into another clip carry a transition;
    // the final clip is deliberately left untouched (it has no "next").
    setMedia(items => items.map((item, index) => index < items.length - 1 ? { ...item, transitionTime: clampTransitionFor(items, index, value) } : item))
    notify(`Applied ${value.toFixed(1)}s to all transitions`)
  }
  // Push the slide default onto every photo and text frame in one go. Videos
  // are skipped on purpose: their hold is their native runtime, and the
  // renderer would only pad a longer value with a frozen last frame.
  const applySlideDuration = () => {
    const value = clampSlideDefault(globalSlideDuration)
    const slides = media.filter(item => item.type !== 'video').length
    const videos = media.length - slides
    if (!slides) { notify(videos ? 'No photos or text frames to update · videos keep their own length' : 'No slides in the storyline yet'); return }
    setMedia(items => items.map(item => item.type === 'video' ? item : resizeClip(item, value)))
    notify(`Applied ${value.toFixed(1)}s to ${slides} slide${slides === 1 ? '' : 's'}${videos ? ` · ${videos} video${videos === 1 ? '' : 's'} kept their own length` : ''}`)
  }
  // Progress only arrives once a second; tick in between so the countdown in the
  // header keeps moving instead of freezing between polls.
  useEffect(() => {
    if (!rendering && !previewing) return
    const id = window.setInterval(() => setEtaTick(tick => tick + 1), 1000)
    return () => window.clearInterval(id)
  }, [rendering, previewing])

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
      // 503 = transient SQLite lock; the render is fine, just retry. New
      // project/cleanup removes the job row after requesting cancellation; in
      // that deliberate case a missing row is the same as cancelled.
      if (response.status === 503 || response.status === 429) continue
      if (response.status === 404 && jobCancelRequested.current) return { cancelled: true }
      if (!response.ok) throw new Error('Could not read render status')
      const job=await response.json()
      const now=Date.now()
      setProgress(Math.round(job.progress||0))
      setJobStage(typeof job.stage==='string'?job.stage:'')
      const started=job.started_at?Date.parse(job.started_at):NaN
      const startedMs=Number.isFinite(started)?started:null
      if(startedMs)jobStartedRef.current=startedMs
      setJobStartedAt(startedMs)
      setEtaSample(previous => nextEtaSample(previous, Number(job.progress)||0, now))
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
      rememberRenderRate(kind)
      if(kind==='preview'){setPreviewUrl(`${completed.fileUrl}?v=${Date.now()}`);setShowPreview(true);notify('Real FFmpeg preview is ready')}
      else{
        notify(`MP4 render complete · ${outputFilename}.mp4`)
        setFinishedRender({ url: completed.fileUrl || `/api/jobs/${jobId}/file`, name: `${outputFilename}.mp4`, bytes: Number(completed.size_bytes) || null })
      }
    }catch(error){notify(`${kind==='preview'?'Preview':'Render'} failed: ${error instanceof Error?error.message:'Unknown error'}`)}
    finally{
      kind==='preview'?setPreviewing(false):setRendering(false)
      setActiveJobId(id => id === jobId ? null : id)
      setEtaSample(null); setJobStage(''); setJobStartedAt(null); jobBaseline.current=null; jobStartedRef.current=null
    }
  }
  // Store how long this machine needed, so the next estimate is a measurement
  // rather than a guess.  Cancelled runs are not representative and are skipped.
  const rememberRenderRate = (kind:'preview'|'render') => {
    const baseline=jobBaseline.current, started=jobStartedRef.current
    if(!baseline||!started)return
    const wallSeconds=(Date.now()-started)/1000
    const timelineSeconds=Math.max(1, baseline.timelineSeconds)
    if(wallSeconds<5)return
    const judge:JobKind=kind
    setRenderRates(saveRenderRate(judge, {
      secondsPerOutputSecond: Math.min(60, Math.max(0.02, wallSeconds/timelineSeconds)),
      resolution: baseline.resolution, encoder: baseline.encoder, kind: judge,
      wallSeconds, timelineSeconds, at: Date.now(),
    }))
  }
  // A preview with slides selected covers only those slides (story order is
  // kept); with nothing selected it covers the whole movie. Renders always
  // produce the complete output.
  const selectedPreviewItems = media.filter(item => selectedIds.includes(item.id))
  const previewSubset = selectedIds.length > 0 && selectedPreviewItems.length > 0 && selectedPreviewItems.length < media.length ? selectedPreviewItems : null
  const startJob = async (kind:'preview'|'render', overwrite=false) => {
    jobCancelRequested.current = false
    kind==='preview'?setPreviewing(true):setRendering(true);setProgress(1);setEtaSample(null);setJobStage('');setJobStartedAt(null)
    if(kind==='render')setFinishedRender(null)
    // Provisional until the backend's own started_at arrives with the first poll.
    jobStartedRef.current=Date.now()
    const subset = kind==='preview' ? previewSubset : null
    if(kind==='preview'){setPreviewScope(subset?subset.length:'all');setPreviewRunMode(previewMode)}
    jobBaseline.current={ timelineSeconds: subset ? timelineModel(subset).total : total, itemCount: subset ? subset.length : media.length, resolution, encoder }
    try{
      const id=await persistProject(true)
      // Stop all also covers this save/submit gap: do not create a new backend
      // job after the user has already cancelled the requested run.
      if (jobCancelRequested.current) {
        kind==='preview'?setPreviewing(false):setRendering(false)
        setActiveJobId(null)
        return
      }
      const response=await fetch(`/api/projects/${id}/jobs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind,overwrite,...(subset?{mediaIds:subset.map(m=>m.id)}:{}),...(kind==='preview'?{previewMode}:{})})})
      if(!response.ok){
        const text=await response.text()
        // The backend refuses to overwrite an existing output file until the
        // user acknowledges it; surface the confirmation instead of failing.
        if(response.status===409&&kind==='render'){
          try{const detail=JSON.parse(text);if(detail?.code==='output_exists'){setOverwritePath(String(detail.path||''));setRendering(false);return}}catch{/* not the overwrite signal */}}
        throw new Error(text)
      }
      const created=await response.json()
      if (jobCancelRequested.current) {
        try { await fetch(`/api/jobs/${created.id}/cancel`, { method: 'POST' }) } catch { /* status polling will report the result */ }
        kind==='preview'?setPreviewing(false):setRendering(false)
        setActiveJobId(null)
        return
      }
      await trackJob(created.id,kind)
    }catch(error){notify(`${kind==='preview'?'Preview':'Render'} failed: ${error instanceof Error?error.message:'Unknown error'}`);kind==='preview'?setPreviewing(false):setRendering(false)}
  }
  const stopActiveJob = async () => {
    // The editor can have more than one queued/running diagnostic or final
    // render (for example after returning from the queue). Stop all of them,
    // not just the one whose progress happens to be shown in this panel.
    jobCancelRequested.current = true
    const ids = new Set<string>()
    if (activeJobId) ids.add(activeJobId)
    try {
      const response = await fetch(projectId != null ? `/api/jobs?project_id=${projectId}` : '/api/jobs')
      if (response.ok) {
        const jobs = await response.json()
        for (const job of jobs) {
          if (['queued', 'running', 'cancelling'].includes(job.status) && job.id) ids.add(String(job.id))
        }
      }
    } catch { /* the active id below can still be cancelled */ }
    if (!ids.size) {
      setRendering(false); setPreviewing(false); setActiveJobId(null)
      notify('No render job was running')
      return
    }
    try {
      const results = await Promise.all(Array.from(ids).map(async id => {
        const response = await fetch(`/api/jobs/${id}/cancel`, { method: 'POST' })
        if (!response.ok && response.status !== 409) throw new Error(await readApiError(response, 'Could not stop'))
        return id
      }))
      notify(`Stopping ${results.length} render job${results.length === 1 ? '' : 's'}…`)
    } catch (error) {
      notify(`Could not stop all renders: ${error instanceof Error ? error.message : 'Unknown error'}`)
    }
  }
  // The backend keeps rendering after a page refresh; re-attach to a still-active
  // job so the Preview/Render buttons show its live progress again.
  const resumeActiveJob = async (id:number) => {
    try{
      const jobs=await fetch(`/api/jobs?project_id=${id}`).then(r=>r.ok?r.json():[])
      const active=jobs.find((job:any)=>['queued','running','cancelling'].includes(job.status))
      // No active job, but a finished render? Keep its download at hand.
      if(!active){
        const done=jobs.find((job:any)=>job.status==='complete'&&job.kind==='render'&&job.fileUrl&&job.fileAvailable!==false)
        if(done)setFinishedRender({ url: done.fileUrl, name: String(done.output_path||'').split('/').pop() || 'movie.mp4', bytes: Number(done.size_bytes)||null })
        return
      }
      const kind:'preview'|'render'=active.kind==='preview'?'preview':'render'
      if(kind==='preview')setPreviewing(true);else setRendering(true)
      setProgress(Math.max(1,Math.round(active.progress||0)))
      setJobStage(typeof active.stage==='string'?active.stage:'')
      notify(`${kind==='preview'?'Preview':'MP4 render'} is still running — progress restored`)
      void trackJob(active.id,kind)
    }catch{/* job list unavailable; nothing to resume */}
  }
  // Preview is an immediate diagnostic render. A final MP4 is deliberately
  // gated by an acknowledgement so it cannot be started accidentally.
  const requestRender = () => {
    if (rendering || previewing || !capabilities.ffmpeg || media.length === 0) return
    setShowRenderConfirm(true)
  }
  const confirmRender = () => {
    setShowRenderConfirm(false)
    void startJob('render')
  }
  const generatePreview = () => void startJob('preview')
  const jumpTo = (id: string) => {
    setActiveTab('editor')
    window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 40)
  }

  // --- generation estimates -------------------------------------------------
  const jobRunning = rendering || previewing
  // etaTick is in the dependency list on purpose: the countdown re-reads the
  // clock once a second, even when the backend sends no new progress.
  const liveRemaining = useMemo(
    () => remainingSeconds(etaSample, progress, Date.now(), jobStartedAt),
    [etaSample, progress, jobStartedAt, etaTick])
  const predictedRender = media.length
    ? estimateRenderSeconds({ timelineSeconds: total, itemCount: media.length, resolution, encoder, kind: 'render', rates: renderRates })
    : null
  const previewEstimateSource = previewSubset || media
  const previewEstimateItems = previewMode === 'fast' && previewEstimateSource.length > 1
    ? previewEstimateSource.map(item => ({
        ...item,
        duration: item.type === 'title' || (item.text.trim() !== '' && item.textEnabled !== false) ? item.duration : 0,
      }))
    : previewEstimateSource
  const predictedPreview = media.length
    ? estimateRenderSeconds({ timelineSeconds: timelineModel(previewEstimateItems).total, itemCount: previewEstimateItems.length, resolution, encoder, kind: 'preview', rates: renderRates })
    : null
  const estimatedBytes = estimateOutputBytes(total, bitrate, soundProgramSeconds > 0)
  // A countdown that has run out but whose job has not finished yet means the
  // last stage is taking longer than predicted, not that it is done.
  const countdownLabel = liveRemaining === null ? 'Estimating…' : liveRemaining <= 0 ? 'Finishing up…' : `${formatEstimate(liveRemaining)} left`
  const liveEstimateLabel = liveRemaining === null ? 'Estimating…' : liveRemaining <= 0 ? 'almost done' : formatEstimate(liveRemaining)
  const estimateBasis = jobRunning
    ? 'measured while it runs'
    : renderRates.render
      ? 'measured on this machine'
      : 'first run · measured afterwards'

  return <div className="app-shell">
    {/* SVG feColorMatrix definitions the CSS filters point at for warmth; they
        must be mounted app-wide because thumbnails reference them too. */}
    <PictureLookDefs/>
    <header className="topbar">
      <div className="brand"><div className="brand-mark"><Film size={21} /></div><div><strong>slideshow</strong><span>PHOTO & VIDEO STUDIO</span></div></div>
      <nav>
        <button className={activeTab === 'editor' ? 'active' : ''} onClick={() => setActiveTab('editor')}><LayoutGrid size={16}/> Editor</button>
        <button type="button" onClick={() => jumpTo('section-storyline')}>Storyline</button>
        <button type="button" onClick={() => jumpTo('section-transitions')} title="Jump to the slide list and timeline where each slide's transition to the next is set">Transitions</button>
        <button type="button" onClick={() => jumpTo('section-soundtrack')}>Soundtrack</button>
        <button type="button" onClick={() => jumpTo('section-output')}>Output</button>
        <button type="button" onClick={() => jumpTo('section-render')}>Ready to generate</button>
        <button className={activeTab === 'renders' ? 'active' : ''} onClick={() => setActiveTab('renders')}><ListVideo size={16}/> Render queue <span className="count">1</span></button>
      </nav>
      <div className="top-actions"><span className={`system-ok ${backendOnline?'':'offline'}`}><i/> {backendOnline?'Backend ready':'Backend offline'}</span><button className="icon-button" title="Help"><CircleHelp size={18}/></button></div>
    </header>

    {activeTab === 'renders' ? <RenderQueue projectId={projectId} onBack={() => setActiveTab('editor')} /> : <main>
      <section className="project-heading">
        <div>
          <div className="eyebrow-line">
            <div className="eyebrow">PROJECT / {(projectName || 'UNTITLED').toUpperCase()}</div>
          </div>
          <input value={projectName} onChange={e=>renameProject(e.target.value)} aria-label="Project name" title="Project name — the filename in the Output pane follows it"/>
          <p>Assemble your media, shape the motion, and export a finished story.</p>
        </div>
        <div className="heading-actions"><button className="btn ghost" disabled={!backendOnline} title={backendOnline?'Load a project — from the SQLite list or from a project file on any mounted volume':'Backend is offline'} onClick={()=>setShowProjectLoader(true)}><FolderOpen size={16}/> Load project</button><button className="btn ghost" title="Delete every saved project and temporary file, and forget the measured render speed" onClick={() => setShowClearAllConfirm(true)}><Trash2 size={16}/> Clear all</button><button className="btn ghost" onClick={()=>setShowProjectFileSave(true)} title="Choose the folder and filename to save this project to — it is stored in SQLite as well"><Save size={16}/> Save project</button>{jobRunning && <div className="job-status" title={`${rendering?'MP4 render':'Preview'} · ${progress}%${jobStage?` · ${jobStage}`:''}`}><RefreshCw className="spin" size={14}/><div><span>{rendering?'Rendering':'Preview'} · {progress}%</span><strong>{countdownLabel}</strong></div>{jobStage && <em title={jobStage} style={{maxWidth:'38ch',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'inline-block',verticalAlign:'middle'}}>{jobStage}</em>}</div>}<button className="btn dark" disabled={previewing||rendering||!capabilities.ffmpeg||media.length===0} onClick={generatePreview}>{previewing?<RefreshCw className="spin" size={15}/>:<Play size={15} fill="currentColor"/>} {previewing?`Building ${progress}%`:'Preview'}</button>{(previewing||rendering)&&<button className="btn ghost stop-job" title="Stop all running preview and final-render jobs for this project" onClick={() => void stopActiveJob()}><Square size={13} fill="currentColor"/> Stop all</button>}</div>
      </section>

      <div className="workspace">
        <div className="left-column">
          <section className={`panel timeline-panel${storyDrop ? ' drop-target' : ''}`} id="section-storyline"
            onDragOver={e => { if (Array.from(e.dataTransfer.types).includes('Files')) { e.preventDefault(); setStoryDrop(true) } }}
            onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setStoryDrop(false) }}
            onDrop={e => { if (Array.from(e.dataTransfer.types).includes('Files')) { e.preventDefault(); setStoryDrop(false); const files = Array.from(e.dataTransfer.files); if (files.length) startUploads(files) } }}>
            <div className="drop-overlay"><Upload size={22}/><span>Drop photos or movies to upload them to the NAS and add them here</span></div>
            <div className="panel-title"><div><span className="step">01</span><div><h2>Storyline</h2><p>{media.length} items · {Math.floor(total / 60)}m {Math.floor(total % 60)}s estimated</p></div></div><div className="toolbar"><button className="btn soft" onClick={() => setShowBrowser(true)}><Plus size={16}/> Add media</button><button className="btn soft" onClick={addTitleFrame}><Plus size={15}/> Text frame</button><button className="btn soft" disabled={selectedIds.length === 0} onClick={() => setShowDeleteConfirm(true)}><Trash2 size={15}/> Delete selected</button><button className="btn soft" title="Start a completely new blank project" onClick={requestNewProject}><Plus size={15}/> New project</button></div></div>
            <div className="bulk-tools"><button className="btn soft default-text-style-bulk" onClick={()=>setShowTextStyles(true)}><Type size={15}/> Default text style</button><div><span>PHOTO SELECTION</span><strong>{selectedIds.length ? `${selectedIds.length} selected` : 'All photos'}</strong></div><Select value={bulkEffect} onChange={setBulkEffect}>{effects.filter(x => x !== 'Original motion').map(x => <option key={x}>{x}</option>)}</Select><button onClick={applyBulkEffect} title="Apply the selected effect to the selection — or to every photo when nothing is selected. “None” removes the Ken Burns motion.">Apply effect</button><button className="random-button" onClick={randomizeBulkEffect}><Shuffle size={13}/> Random</button><button className="random-button text-trans-random" onClick={randomizeTextTransitions} title="Give these photos a random text animation: each gets a curated preset (a whole stack of enter / while / exit effects) · every photo when nothing is selected"><Shuffle size={13}/> Text effects</button><Select value={bulkFilter} onChange={setBulkFilter} ariaLabel="Picture filter">{LOOK_GROUPS.map(group => <optgroup key={group} label={group}>{LOOK_PRESETS.filter(preset => preset.group === group).map(preset => <option key={preset.id} value={preset.id}>{preset.label}</option>)}</optgroup>)}</Select><button onClick={applyBulkFilter} title="Apply this filter to the selection — or to every photo and movie when nothing is selected"><Sparkles size={12}/> Apply filter</button><i/><div><span>MOVE SELECTED</span><strong>{selectedIds.length ? `${selectedIds.length} item${selectedIds.length === 1 ? '' : 's'}` : 'Select items first'}</strong></div><div className="move-to"><label>to <input type="number" min={1} max={media.length} value={bulkPosition} disabled={!selectedIds.length} onChange={e => setBulkPosition(Number(e.target.value))} onKeyDown={e => { if (e.key === 'Enter') moveItemsToPosition(selectedIds, bulkPosition) }} aria-label="Target position"/></label><button disabled={!selectedIds.length} onClick={() => moveItemsToPosition(selectedIds, bulkPosition)} title="Insert the selection at this position; other items shift">Move</button><button disabled={!selectedIds.length} onClick={() => moveItemsToPosition(selectedIds, 1)} title="Move selection to the start"><ArrowUp size={12}/> Start</button><button disabled={!selectedIds.length} onClick={() => moveItemsToPosition(selectedIds, media.length)} title="Move selection to the end"><ArrowDown size={12}/> End</button></div><i/><div><span>TRANSITION SELECTION</span><strong>{selectedTransitions.length ? `${selectedTransitions.length} selected` : 'All transitions'}</strong></div><TransitionChip value={bulkTransition} onChange={setBulkTransition} onOpenGallery={() => setShowTransitionGallery(true)} /><button onClick={applyBulkTransition}>Apply effect</button></div>

            <div className="bulk-bar"><span title="Hold time of every new photo and text frame · videos always keep their own length">SLIDE DEFAULT</span><NumberStepper value={globalSlideDuration} min={MIN_CLIP_SECONDS} max={MAX_DEFAULT_SLIDE_SECONDS} step={0.5} suffix="sec" ariaLabel="Default slide duration" onChange={setGlobalSlideDuration} /><button onClick={applySlideDuration} title="Set every photo and text frame to this length · videos keep their native runtime">Apply to all</button><em className="bulk-divider"/><span title="Duration of every new transition">TRANSITION DEFAULT</span><NumberStepper value={globalDuration} min={0.1} max={MAX_DEFAULT_TRANSITION_SECONDS} step={0.1} suffix="sec" ariaLabel="Default transition duration" onChange={setGlobalDuration} /><button onClick={applyDuration} title="Set every transition to this duration">Apply to all</button><i/><span className="random-scope-label">RANDOM SOURCE</span><RandomScopeSelect value={randomScope} onChange={setRandomScope}/><label className="check-label random-params-toggle" title="When set, randomizing also draws fresh values for every transition parameter — easing and the selected transition's size, zoom, colour, smoothness and other registry values (reverse is always left unchecked). Transition durations are never changed."><input type="checkbox" checked={randomizeParams} onChange={e => setRandomizeParams(e.target.checked)}/><span><Check size={11}/></span> params</label><button className="random-button" title={`Randomize every transition using: ${randomScopeLabels[randomScope]}${randomizeParams ? ' · all transition parameters are randomized too' : ''} · durations and Ken Burns motion are left untouched`} onClick={() => { randomize(); notify(`Transitions randomized · ${randomScopeLabels[randomScope]}${randomizeParams ? ' · all transition parameters randomized' : ''}`) }}><Shuffle size={14}/> Randomize all</button><button className="random-button" title={`Randomize only the transitions between neighbouring selected slides · ${randomScopeLabels[randomScope]}${randomizeParams ? ' · all transition parameters are randomized too' : ''}`} onClick={randomizeSelectedTransitions}><Shuffle size={14}/> Randomize selected</button><i/><button className="gallery-button" title={`Open a full-screen gallery with a small example of every transition`} onClick={() => setShowTransitionGallery(true)}><LayoutGrid size={14}/> Browse all {totalTransitionCount}</button></div>

            <div className="overview-head"><div><strong>OVERALL TIMELINE</strong><span>Drag selected clips as a group · edit text above each clip · click transitions · videos keep their own rows in story order</span></div><SelectAllSlides allSelected={allSlidesSelected} selectedCount={selectedIds.length} totalCount={media.length} onToggle={toggleAllSlides}/><div className="story-layout"><label>Lines</label><div className="line-count-control"><NumberStepper value={visibleRows} min={1} max={99} step={1} ariaLabel="Timeline lines" onChange={value => setTimelineRows(String(Math.round(value)))} /><button type="button" className={`line-auto ${timelineRows === 'auto' ? 'active' : ''}`} aria-pressed={timelineRows === 'auto'} title={`Use automatic line count · ${autoLineCount} line${autoLineCount === 1 ? '' : 's'}`} onClick={() => setTimelineRows('auto')}>Auto</button></div></div><div className="zoom-controls"><button onClick={() => setTimelineZoom(z => Math.max(.6, +(z - .2).toFixed(1)))} title="Zoom out"><ZoomOut size={14}/></button><input className="zoom-slider" type="range" min={0.6} max={2.4} step={0.1} value={timelineZoom} aria-label="Timeline zoom" onChange={e => setTimelineZoom(Number(e.target.value))}/><span>{Math.round(timelineZoom * 100)}%</span><button onClick={() => setTimelineZoom(z => Math.min(2.4, +(z + .2).toFixed(1)))} title="Zoom in"><ZoomIn size={14}/></button><button className="fit-button" onClick={() => setTimelineZoom(1)} title="Reset zoom to show complete timeline">Fit</button></div></div>
            <div className="timeline-overview">{media.length===0&&<button className="empty-story" onClick={()=>setShowBrowser(true)}><FolderOpen size={22}/><strong>Your storyline is empty</strong><span>Browse the mounted /photos and /videos folders to begin.</span></button>}{timelineLines.map((line, lineIndex) => {
              const firstIndex = media.findIndex(x => x.id === line.items[0]?.id)
              const lastIndex = media.findIndex(x => x.id === line.items[line.items.length - 1]?.id)
              const lineStart = timeline.starts[firstIndex] ?? 0
              const lineEnd = (timeline.starts[lastIndex] ?? 0) + (timeline.durations[lastIndex] ?? 0)
              const lineDuration = lineEnd - lineStart
              return <div className={`timeline-line ${line.video ? 'video-line' : ''}`} key={lineIndex}><div className={`line-number ${line.video ? 'video' : ''}`} title={line.video ? 'Video row — movies are kept on their own row in story order' : undefined}>{lineIndex + 1}{line.video && <Video size={10}/>}</div><div className="line-content" style={{width: `calc(${timelineZoom * 100}% - 18px)`}}><div className="text-track">{line.items.map(item => <div className="text-lane" key={item.id} style={{flexGrow:item.duration}}><TimelineTextBox item={item} update={change=>patch(item.id,change)} selected={selectedTextTransitions} onSelect={edge=>toggleTextTransition(item.id,edge)} onEdit={item.type === 'title' ? () => setEditingTextFrame(item.id) : () => setEditingPictureText(item.id)}/></div>)}</div><div className="overview-track">{line.items.map(item => { const index=media.findIndex(x => x.id===item.id); const thumb = itemThumbUrl(item); return <div className="overview-segment-wrap" key={item.id} style={{flexGrow: item.duration}}><div draggable onDragStart={() => setDraggedId(item.id)} onDragEnd={() => setDraggedId(null)} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); dropOn(item.id); }} onDoubleClick={() => item.type === 'title' && setEditingTextFrame(item.id)} className={`overview-clip ${draggedId === item.id ? 'dragging' : ''} ${selectedIds.includes(item.id) ? 'selected' : ''} ${item.type === 'title' ? 'title-clip' : ''} ${item.type === 'video' ? 'movie-clip' : ''}`} style={item.type==='title'?frameBackgroundStyle(item):undefined}>{item.type === 'video' ? <MovieStrip item={item} onClick={e => { e.stopPropagation(); openMediaLightbox(item) }} onPointerDown={e => e.stopPropagation()} /> : <MediaThumb item={item} onClick={e => { e.stopPropagation(); openMediaLightbox(item) }} onPointerDown={e => e.stopPropagation()} />}{item.type === 'title' && <button type="button" className="clip-frame-edit" title={`Edit text frame · “${item.text}” · ${item.duration}s`} aria-label="Edit text frame" onClick={e => { e.preventDefault(); e.stopPropagation(); setEditingTextFrame(item.id) }} onPointerDown={e => e.stopPropagation()}><Pencil size={11}/></button>}{item.type !== 'title' && thumb ? <button type="button" className="clip-zoom" title="View" onClick={e => { e.preventDefault(); e.stopPropagation(); openMediaLightbox(item) }} onPointerDown={e => e.stopPropagation()}><ZoomIn size={11}/></button> : null}<button className="clip-select" title="Select clip" onClick={() => toggleSelected(item.id)}><span>{selectedIds.includes(item.id) && <Check size={10}/>}</span></button>{item.type !== 'title' && item.text.trim() !== '' && <button type="button" className={`clip-text-toggle ${item.textEnabled === false ? 'off' : ''}`} title={item.textEnabled === false ? 'Text is hidden on this picture — click to show it' : 'Text is shown on this picture — click to hide it'} onClick={e => { e.preventDefault(); e.stopPropagation(); patch(item.id, { textEnabled: item.textEnabled === false }) }} onPointerDown={e => e.stopPropagation()}>{item.textEnabled === false ? <EyeOff size={11}/> : <Eye size={11}/>}</button>}<span>{String(index + 1).padStart(2,'0')} · {item.name}</span><small>{item.duration}s</small></div>{index < media.length - 1 && <button title={`${item.transition}${item.transitionEasing && item.transitionEasing!=='linear' ? ' · '+item.transitionEasing : ''}${item.transitionReverse ? ' · reverse':''} · ${item.transitionTime}s`} onClick={() => setTransitionPreviewId(item.id)} className={`transition-marker ${selectedTransitions.includes(item.id) ? 'selected' : ''} ${isGLTransition(item.transition)?'gl':''}`}><i>{transitionSymbol(item.transition)}</i><strong>{timelineZoom >= 1 ? item.transition.replace('GL · ','').replace('GLSL · ','') : ''}</strong><b>{item.transitionTime}s</b>{item.transitionEasing && item.transitionEasing!=='linear' ? <em>{item.transitionEasing}</em>:null}</button>}</div>})}</div><TimelineRuler start={lineStart} duration={lineDuration} zoom={timelineZoom} audioLength={lineIndex === timelineLines.length - 1 && audioTracks.length > 0 ? formatTimecode(audioTotalSeconds) : undefined}/></div></div>
            })}</div>

            {selectedTransitions.length > 0 && <TransitionInspector count={selectedTransitions.length} first={media.find(x => x.id === selectedTransitions[0])} onPatch={inspectorPatch => setMedia(items => items.map(item => selectedTransitions.includes(item.id) ? { ...item, ...inspectorPatch } : item))} onTime={updateSelectedTransitionTimes} onClear={() => setSelectedTransitions([])} onOpenGallery={() => setShowTransitionGallery(true)}/>}
            {selectedTextTransitions.length > 0 && (() => {
              const [firstId, firstEdge] = selectedTextTransitions[0].split('-')
              const firstItem = media.find(x => x.id === Number(firstId))
              const phase = firstEdge === 'enter' ? 'in' : 'out'
              const firstLayer = firstItem ? layersOf(firstItem.textFx, phase)[0] : undefined
              return <div className="timeline-inspector text-inspector"><span>{selectedTextTransitions.length} text {selectedTextTransitions.length > 1 ? 'lanes' : 'lane'} selected</span>
                {firstItem && <LaneEffectChip item={firstItem} phase={phase} onPick={id => updateSelectedTextTransitions(id, undefined)} />}
                <NumberStepper value={firstLayer?.duration ?? EFFECTS[firstLayer?.effect || '']?.duration ?? .5} min={0.1} step={0.1} suffix="sec" ariaLabel="Selected text transition time" onChange={v=>updateSelectedTextTransitions(undefined,v)} />
                <button onClick={()=>setSelectedTextTransitions([])}><X size={13}/> Clear</button></div>
            })()}

            <div className="media-view-bar" id="section-transitions"><span className="view-label">VIEW</span><div className="mode-toggle"><button className={!compactMediaView ? 'active' : ''} onClick={() => setCompactMediaView(false)} title="Show the full detail list"><List size={14}/> List</button><button className={compactMediaView ? 'active' : ''} onClick={() => setCompactMediaView(true)} title="Show a compact thumbnail grid with quick multi-selection"><LayoutGrid size={14}/> Compact</button></div>{compactMediaView && <div className="zoom-controls compact-zoom"><button onClick={() => setCompactZoom(z => Math.max(.6, +(z - .2).toFixed(1)))} title="Zoom out — smaller thumbnails"><ZoomOut size={14}/></button><input className="zoom-slider" type="range" min={0.6} max={5} step={0.1} value={compactZoom} aria-label="Compact thumbnail zoom" onChange={e => setCompactZoom(Number(e.target.value))}/><span>{Math.round(compactZoom * 100)}%</span><button onClick={() => setCompactZoom(z => Math.min(5, +(z + .2).toFixed(1)))} title="Zoom in — bigger thumbnails"><ZoomIn size={14}/></button></div>}<span className="view-hint">Select frames with the check marks · Shift-click for a range · “Select all” grabs every frame in one go · drag to reorder · click a picture to view it</span></div>
            {compactMediaView && <div className="compact-actions"><button className="btn soft" disabled={!media.length} onClick={() => setSelectedIds(media.map(x => x.id))} title="Select every frame in one go"><Check size={14}/> Select all</button><button className="btn soft" disabled={!selectedIds.length} onClick={() => setSelectedIds([])}>Clear selection</button><button className="btn soft" disabled={!selectedIds.length} onClick={() => setShowDeleteConfirm(true)}><Trash2 size={14}/> Delete selected</button><span className="compact-count">{selectedIds.length} of {media.length} frame{media.length === 1 ? '' : 's'} selected</span></div>}
            {!compactMediaView && selectedTransitions.length > 0 && <TransitionInspector count={selectedTransitions.length} first={media.find(x => x.id === selectedTransitions[0])} onPatch={inspectorPatch => setMedia(items => items.map(item => selectedTransitions.includes(item.id) ? { ...item, ...inspectorPatch } : item))} onTime={updateSelectedTransitionTimes} onClear={() => setSelectedTransitions([])} onOpenGallery={() => setShowTransitionGallery(true)}/>}
            {!compactMediaView && <div className="timeline-head"><span className="head-select"><SelectAllSlides allSelected={allSlidesSelected} selectedCount={selectedIds.length} totalCount={media.length} onToggle={toggleAllSlides}/></span><span>MEDIA</span><span>SLIDE / CLIP</span><span>EXAMPLE</span><span>DURATION · TRANSITION TO NEXT</span><span></span></div>}
            {compactMediaView ? <div className="compact-grid" style={{ '--compactSize': compactZoom } as React.CSSProperties}>{media.map((item, index) => <div className={`compact-card ${draggedId === item.id ? 'dragging' : ''} ${selectedIds.includes(item.id) ? 'selected' : ''} ${flashIds.includes(item.id) ? 'just-moved' : ''}`} data-item-id={item.id} key={item.id} draggable onDragStart={e => { setDraggedId(item.id); e.dataTransfer.setData('text/plain', String(item.id)); e.dataTransfer.effectAllowed = 'move' }} onDragEnd={() => setDraggedId(null)} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); dropOn(item.id); }}><div className={`compact-thumb ${item.type === 'title' ? 'title-thumb' : ''}`} style={item.type === 'title' ? frameBackgroundStyle(item) : undefined} onClick={e => { e.stopPropagation(); openMediaLightbox(item) }} title={item.type === 'title' ? 'Preview this text frame' : 'View'}>{item.type === 'title' ? <span className="title-symbol">T</span> : <MediaThumb item={item} onPointerDown={e => e.stopPropagation()} />}{item.type === 'title' && <button type="button" className="compact-edit" title={`Edit text frame · “${item.text}” · ${item.duration}s`} aria-label="Edit text frame" onPointerDown={e => e.stopPropagation()} onClick={e => { e.preventDefault(); e.stopPropagation(); setEditingTextFrame(item.id) }}><Pencil size={13}/></button>}<button type="button" className="compact-preview" title={item.type === 'title' ? `Preview this text frame · “${item.text}”` : `Preview · ${item.name}`} aria-label={`Open preview of ${item.name}`} onPointerDown={e => e.stopPropagation()} onClick={e => { e.preventDefault(); e.stopPropagation(); openMediaLightbox(item) }}><ZoomIn size={13}/></button><PositionBadge index={index} count={media.length} onMove={pos => moveItemsToPosition([item.id], pos)} /></div><button className="compact-select" title="Select frame · Shift-click for a range" aria-label={selectedIds.includes(item.id) ? `Deselect ${item.name}` : `Select ${item.name}`} aria-pressed={selectedIds.includes(item.id)} onClick={e => { e.stopPropagation(); selectCompactRange(index, e.shiftKey) }}><span>{selectedIds.includes(item.id) && <Check size={11}/>}</span></button><button className="compact-delete" title={`Remove ${item.name}`} onClick={() => setMedia(m => m.filter(x => x.id !== item.id))}><Trash2 size={14}/></button></div>)}</div> : <div className="timeline-list">
              {media.map((item, index) => {
                const thumb = itemThumbUrl(item)
                const textHidden = item.type !== 'title' && item.textEnabled === false
                return <div className={`timeline-item wide-transition ${item.type === 'video' ? 'movie-row' : item.type === 'title' ? 'title-row' : ''} ${textHidden ? 'text-hidden-row' : ''} ${draggedId === item.id ? 'dragging' : ''} ${selectedIds.includes(item.id) ? 'selected-row' : ''} ${flashIds.includes(item.id) ? 'just-moved' : ''}`} data-item-id={item.id} key={item.id} draggable onDragStart={() => setDraggedId(item.id)} onDragEnd={() => setDraggedId(null)} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); dropOn(item.id); }}>
                  <div className="row-select"><GripVertical className="grip" size={16}/><label title="Select for bulk changes"><input type="checkbox" checked={selectedIds.includes(item.id)} onChange={() => toggleSelected(item.id)}/><span><Check size={9}/></span></label></div>
                  <div className="thumb-column">
                  <div className={`thumb ${item.type === 'title' ? 'title-thumb' : ''} ${item.type !== 'title' && thumb ? 'thumb-open' : ''}`} style={item.type==='title'?frameBackgroundStyle(item):undefined} onClick={e => { e.stopPropagation(); openMediaLightbox(item) }} title={item.type === 'title' ? 'Preview this text frame' : 'View'}>{item.type === 'title' ? <span className="title-symbol">T</span> : <MediaThumb item={item} />}{item.type === 'image' && item.effect === 'None' && <button type="button" className="thumb-effect off" title="No motion on this photo — click to add a Ken Burns effect" onClick={e => { e.preventDefault(); e.stopPropagation(); setEffectPicker(effectPicker === item.id ? null : item.id) }} onPointerDown={e => e.stopPropagation()}><Move size={10}/></button>}{item.type === 'image' && item.effect !== 'None' && <button type="button" className={`thumb-effect ${isKenBurns(item.effect) && Math.abs(kenBurnsZoomOf(item) - KEN_BURNS_DEFAULT_ZOOM) > 0.001 ? 'custom' : ''}`} title={`Motion: ${item.effect}${isKenBurns(item.effect) ? ` · strength ${Math.round((kenBurnsZoomOf(item) - 1) * 100)} %` : ''} — click to change`} onClick={e => { e.preventDefault(); e.stopPropagation(); setEffectPicker(effectPicker === item.id ? null : item.id) }} onPointerDown={e => e.stopPropagation()}><Move size={10}/><span>{shortEffect(item.effect)}</span></button>}{item.type === 'video' && <span><Video size={12}/> {formatClock(item.duration)}</span>}{item.type === 'title' && <button type="button" className="thumb-edit" title={`Edit text frame · “${item.text}” · ${item.duration}s`} aria-label="Edit text frame" onClick={e => { e.preventDefault(); e.stopPropagation(); setEditingTextFrame(item.id) }} onPointerDown={e => e.stopPropagation()}><Pencil size={10}/><span>Edit</span></button>}{item.type !== 'title' && <button type="button" className={`thumb-look ${hasLook(item) ? 'on' : ''}`} title={hasLook(item) ? `Picture look: ${lookSummary(item)} — click to change` : 'Add a filter or effect to this clip'} onClick={e => { e.preventDefault(); e.stopPropagation(); openLookEditor(item, 'filters') }} onPointerDown={e => e.stopPropagation()}><Sparkles size={10}/><span>{hasLook(item) ? lookLabel(item) : 'Filter'}</span></button>}{item.type !== 'title' && hasCrop(item) && <button type="button" className="thumb-look on" title={`Cut & crop: ${cropSummary(item)} — click to change`} onClick={e => { e.preventDefault(); e.stopPropagation(); openLookEditor(item, 'crop') }} onPointerDown={e => e.stopPropagation()}><CropIcon size={10}/><span>{cropLabel(item)}</span></button>}<PositionBadge index={index} count={media.length} onMove={pos => moveItemsToPosition([item.id], pos)} /></div>
                  </div>
                  <div className="media-info"><strong>{item.name}</strong><span className="media-path">{item.path}{item.type === 'image' ? ' · photo' : item.type === 'video' ? ' · video' : ' · generated text frame'}</span>{item.type === 'image' && <div className="motion-inline" title={isKenBurns(item.effect) ? `Ken Burns: ${kenBurnsSummary(item)} — ⚙ opens strength and focus` : 'Ken Burns motion for this photo (default none)'}><Move size={10}/><select aria-label={`${item.name} Ken Burns motion`} className={isKenBurns(item.effect) ? 'on' : ''} value={item.effect} onChange={e => patch(item.id, { effect: e.target.value })}>{effects.filter(x => x !== 'Original motion').map(x => <option key={x} value={x}>{x === 'None' ? 'None' : shortEffect(x)}</option>)}</select>{isKenBurns(item.effect) && <button type="button" aria-label="Ken Burns strength and focus" title={`Strength ${Math.round((kenBurnsZoomOf(item) - 1) * 100)} % — click for strength and focus`} onClick={() => setEffectPicker(effectPicker === item.id ? null : item.id)}><Settings2 size={10}/>{Math.round((kenBurnsZoomOf(item) - 1) * 100)}%</button>}</div>}<div className="item-text-edit">{item.type !== 'title' && <button type="button" className={`text-toggle ${item.textEnabled === false ? 'off' : ''}`} title={item.textEnabled === false ? 'Text is hidden on this picture — click to show it and edit it here' : 'Text is shown on this picture — click to hide it'} onClick={() => patch(item.id, { textEnabled: item.textEnabled === false })}>{item.textEnabled === false ? <EyeOff size={13}/> : <Eye size={13}/>}</button>}{item.type !== 'title' && <button type="button" className="edit-picture-text-button" title={`Edit this picture's text style, visibility and timing · ${formatClock(normalizedTextTiming(item).textStart)}–${formatClock(normalizedTextTiming(item).textEnd)}`} onClick={event => { event.preventDefault(); event.stopPropagation(); setEditingPictureText(item.id) }} onPointerDown={event => event.stopPropagation()}><Pencil size={11}/> Edit</button>}{item.type !== 'title' && item.textEnabled === false ? null : <>{(() => { const chip = laneChip(item.textFx, 'in'); return <button className={`text-detail-transition ${detailTextEditor?.id===item.id&&detailTextEditor.edge==='enter'?'selected':''}`} title={`Text appears — ${chip.title}`} onClick={()=>setDetailTextEditor({id:item.id,edge:'enter'})}>{chip.symbol}{chip.more > 0 && <sup>+{chip.more}</sup>}</button> })()}<input value={item.text} placeholder="Add text…" onChange={e => patch(item.id,{text:e.target.value})}/>{(() => { const chip = laneChip(item.textFx, 'out'); return <button className={`text-detail-transition ${detailTextEditor?.id===item.id&&detailTextEditor.edge==='exit'?'selected':''}`} title={`Text disappears — ${chip.title}`} onClick={()=>setDetailTextEditor({id:item.id,edge:'exit'})}>{chip.symbol}{chip.more > 0 && <sup>+{chip.more}</sup>}</button> })()}</>}{item.type==='title'&&<button type="button" className="edit-frame-button" title={`Edit this text frame · “${item.text}” — colours, font, position and timing`} onClick={()=>setEditingTextFrame(item.id)}>Edit frame</button>}</div><div className="item-duration" title="How long this slide stays on screen before the next one · default 5 sec"><Clock3 size={11}/><span>Slide duration</span><div className="clip-duration slide-duration"><NumberStepper value={item.duration} min={MIN_CLIP_SECONDS} step={0.5} ariaLabel={`${item.name} slide duration`} onChange={v => updateDuration(item.id, v)} /><span>sec</span></div></div>{detailTextEditor?.id===item.id&&item.textEnabled!==false&&(() => {
                    const phase = detailTextEditor.edge === 'enter' ? 'in' : 'out'
                    const lane = layersOf(item.textFx, phase)
                    return <div className="detail-transition-popover"><strong>{detailTextEditor.edge==='enter'?'Text appears':'Text disappears'}</strong>
                      <LaneEffectChip item={item} phase={phase} onPick={id=>patch(item.id,{textFx:setLaneEffect(item.textFx||[], phase, id)})} onStack={stack=>patch(item.id,{textFx:stack})} />
                      {lane.length > 0 && <NumberStepper value={lane[0].duration ?? EFFECTS[lane[0].effect]?.duration ?? .5} min={0.1} step={0.1} suffix="s" ariaLabel="Text transition duration" onChange={v=>patch(item.id,{textFx:setLaneDuration(item.textFx||[], phase, v)})} />}
                      <button type="button" className="btn ghost small" title="Open the text editor: all lanes, presets, timeline" onClick={()=>{ setDetailTextEditor(null); if (item.type === 'title') setEditingTextFrame(item.id); else setEditingPictureText(item.id) }}>All effects…</button>
                      <button onClick={()=>setDetailTextEditor(null)}><X size={13}/></button></div>
                  })()}{effectPicker===item.id && item.type !== 'title' && <KenBurnsPanel item={item} thumb={thumb} onPatch={p => patch(item.id, p)} onClose={() => setEffectPicker(null)}/>}{item.type === 'video' && <div className="movie-audio"><Select ariaLabel={`${item.name} audio`} value={item.audioSource || 'soundtrack'} onChange={v => patch(item.id, { audioSource: v as 'soundtrack' | 'original' })}><option value="soundtrack">Soundtrack</option><option value="original">Original audio</option></Select><small>{item.audioSource === 'original' ? 'crossfades with the soundtrack' : 'the soundtrack keeps playing'}</small></div>}{((item.type !== 'title' && item.textEnabled === false && item.text.trim() !== '') || (item.type === 'video' && movieIsTrimmed(item))) && <div className="settings-chips">{item.textEnabled === false && item.text.trim() !== '' && <button type="button" className="settings-chip" title="Text is hidden on this picture — click to show it again" onClick={() => patch(item.id, { textEnabled: true })}><EyeOff size={10}/> Text hidden</button>}{item.type === 'video' && movieIsTrimmed(item) && <button type="button" className="settings-chip" title={`Using ${movieKeptLabel(item)} of the original movie — click to trim`} onClick={e => { e.stopPropagation(); openMediaLightbox(item) }}><Scissors size={10}/> {movieKeptLabel(item)}</button>}</div>}</div>
                  {index < media.length - 1 ? <RecordedTransitionExample transition={item.transition} /> : <RecordedTransitionExample transition="" empty />}
                  {index < media.length - 1 ? <TransitionCell item={item} inline onPatch={patch_ => patch(item.id, patch_)} onDuration={v => updateDuration(item.id, v)} onOpenGallery={() => setShowTransitionGallery(true)} /> : <div className="transition-cell last-cell"><div className="end-card"><Check size={13}/> End of story</div></div>}
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
            <div className="form-grid two"><div><FieldLabel>Video bitrate</FieldLabel><Select value={bitrate} onChange={setBitrate}><option>4 Mbps · Standard</option><option>8 Mbps · High</option><option>12 Mbps · Very high</option><option>20 Mbps · Maximum</option></Select></div><div><FieldLabel>Encoder</FieldLabel><Select value={encoder} onChange={setEncoder}><option>Auto · Quick Sync</option><option>Intel Quick Sync</option><option>Hardware · VAAPI</option><option>CPU · x264</option></Select></div></div>
            <div><FieldLabel>Output folder</FieldLabel><div className="path-field"><FolderOpen size={15}/><input value={outputPath} onChange={e=>setOutputPath(e.target.value)}/><button onClick={()=>setShowFolderPicker(true)} title="Browse the mounted /output volume">Browse</button></div></div>
            <div><FieldLabel hint="same as the project name">Filename</FieldLabel><div className="filename"><input value={outputFilename} onChange={e=>renameOutputFile(e.target.value)} onBlur={commitOutputFile} onKeyDown={e=>{ if(e.key==='Enter'){e.preventDefault();(e.target as HTMLInputElement).blur()} }} aria-label="Output filename" title="Editing this renames the project at the top as well"/><span>.mp4</span></div>{nameAndFileDiffer && <em className="fade-hint filename-link"><AlertTriangle size={11}/> This project was saved with its own filename — edit either field and the two are linked again</em>}</div>
            <div className="estimate"><div><Activity size={15}/><span>ESTIMATED OUTPUT</span></div><strong>~{formatFileSize(estimateOutputBytes(total, bitrate, soundProgramSeconds > 0))}</strong><small>H.264{soundProgramSeconds ? ' · AAC stereo' : ''} · {formatClock(total)} · {parsePresetNumber(bitrate, 8)} Mbps</small></div>
          </section>

          <section className="panel review-panel" id="section-render"><div className="review-title"><Sparkles size={18}/><div><h3>{rendering||previewing?'Working…':'Ready to render'}</h3><p>{rendering||previewing?`${progress}% · you can stop at any time`:'All checks passed'}</p></div><span>{rendering||previewing?<RefreshCw className="spin" size={14}/>:<Check size={14}/>}</span></div><ul><li><Check size={13}/> {media.length} media items are ready</li><li><Check size={13}/> Output folder is writable</li><li className={capabilities.ffmpeg?'':'warning'}>{capabilities.ffmpeg?<Check size={13}/>:<AlertTriangle size={13}/>} {capabilities.ffmpeg?'FFmpeg backend is available':'FFmpeg is unavailable'}</li><li className={capabilities.quickSync||capabilities.vaapi?'':'warning'} title={capabilities.quickSync||capabilities.vaapi?undefined:(capabilities.vaapiError||undefined)}>{capabilities.quickSync||capabilities.vaapi?<Check size={13}/>:<AlertTriangle size={13}/>} {capabilities.quickSync?'Intel Quick Sync is available':capabilities.vaapi?'Hardware encoding available · VAAPI':<>{'Quick Sync and VAAPI unavailable · CPU fallback'}{capabilities.vaapiError&&<small className="cap-note">{capabilities.vaapiError}</small>}</>}</li>{(() => {
              // GL transitions are C ports of the gl-transitions shaders running
              // inside xfade: always computed on the CPU, by design, on every
              // machine. The GPU (when present) still encodes every part. The only
              // real "fallback" is a stock FFmpeg without the xfade-easing patch.
              const usesGL = media.some(m => isGLTransition(m.transition))
              if (capabilities.ffmpeg && capabilities.hasGL === false) return <li className={usesGL ? 'warning' : ''} title="This FFmpeg build lacks the xfade-easing patch: GL transitions and easing/reverse render as a plain dissolve.">{usesGL ? <AlertTriangle size={13}/> : <Check size={13}/>} {usesGL ? 'This FFmpeg has no GL transitions · the ones in this project fall back to dissolve' : 'This FFmpeg has no GL transitions (none used in this project)'}</li>
              const hw = capabilities.quickSync || capabilities.vaapi
              return <li title="GL transitions are computed on the CPU inside FFmpeg's xfade filter on every system — there is no GPU shader path. Hardware encoding still applies to the transition clips."><Check size={13}/> {hw ? 'GL transitions computed on CPU (by design) · clips encoded on the GPU' : 'GL transitions computed on CPU (by design) · CPU encoding'}</li>
            })()}{audioFadeTooLong && <li className="warning"><AlertTriangle size={13}/> Soundtrack fade ({audioFadeDuration.toFixed(1)}s + {audioFadeTail.toFixed(1)}s silence) exceeds the slideshow · it will be clamped</li>}</ul><div className="estimate-row"><div><Timer size={14}/><span>ESTIMATED TIME TO GENERATE</span><strong>{jobRunning?liveEstimateLabel:predictedRender===null?'—':formatEstimate(predictedRender)}</strong><small>{estimateBasis}{!jobRunning && predictedPreview!==null?` · preview ${formatEstimate(predictedPreview)}`:''}</small></div><div><HardDrive size={14}/><span>ESTIMATED FILE SIZE</span><strong>{media.length?`~${formatFileSize(estimatedBytes)}`:'—'}</strong><small>{parsePresetNumber(bitrate,8)} Mbps · {resolution.replace(/ · .*/,'')}{soundProgramSeconds>0?' · AAC':''}</small></div><div><Clock3 size={14}/><span>ESTIMATED TOTAL SLIDESHOW TIME</span><strong>{formatClock(total)}</strong><small>{media.length} item{media.length===1?'':'s'} · {timeline.transitions.length} transition{timeline.transitions.length===1?'':'s'}</small></div></div><div className="preview-options"><div><FieldLabel>PREVIEW DETAIL <span>{previewMode === 'fast' ? 'faster diagnostic' : 'complete selected sequence'}</span></FieldLabel><Select value={previewMode} onChange={value => setPreviewMode(value as PreviewMode)} ariaLabel="Preview detail"><option value="fast">Fast · text + transitions</option><option value="standard">Standard · all selected slides</option></Select></div><p><Info size={12}/> Fast mode skips static holds without text and omits the soundtrack; transitions and text timing remain rendered by FFmpeg.</p></div><button className="btn preview-btn" disabled={previewing||rendering||!capabilities.ffmpeg||media.length===0} title={previewSubset?`Low-resolution preview of the ${previewSubset.length} selected slide${previewSubset.length===1?'':'s'} only (${formatClock(timelineModel(previewSubset).total)}) — clear the selection to preview the whole movie`:'Low-resolution preview of the whole movie — select slides in the storyline to preview only those'} onClick={generatePreview}>{previewing?<RefreshCw className="spin" size={16}/>:<Play size={16}/>} {previewing?`Generating preview ${progress}%`:previewSubset?`Preview ${previewSubset.length} selected`:'Generate preview'}</button><button className="btn render-btn" disabled={rendering||previewing||!capabilities.ffmpeg||media.length===0} onClick={requestRender}>{rendering ? <><RefreshCw className="spin" size={16}/> Rendering… {progress}%</> : <><Zap size={16}/> Render MP4</>}</button><button type="button" className="btn ghost stop-job wide" disabled={!rendering && !previewing} title="Stop all running preview and final-render jobs for this project" onClick={() => void stopActiveJob()}><Square size={14} fill="currentColor"/> Stop all</button>{!rendering&&!previewing&&finishedRender&&<div className="render-ready-row"><Download size={15}/><div className="render-ready-info"><strong>MP4 ready</strong><small>{finishedRender.name}{finishedRender.bytes!==null?` · ${formatFileSize(finishedRender.bytes)}`:''}</small></div><a className="btn soft" href={finishedRender.url} download title="Save the finished MP4 to this device"><Download size={14}/> Download MP4</a></div>}{(rendering||previewing) && <><div className="progress"><i style={{width: `${progress}%`}}/></div><p className="render-stage" title={jobStage || 'Waiting for the backend to report the first stage'}><RefreshCw className="spin" size={12}/><span>{jobStage || 'Starting…'}</span></p></>}<p className="render-note"><Info size={13}/> FFmpeg jobs run in the backend; progress and logs are stored in SQLite. Stop all cancels every queued/running preview and final-render job for this project. Intermediate segments and stale proxy previews are cleaned up automatically after each render.</p></section>
        </div>
        </div>
      </div>
    </main>}

    {editingTrackId != null && (() => { const track = audioTracks.find(x => x.id === editingTrackId); return track ? <SoundtrackEditor track={track} onChange={change => setAudioTracks(items => items.map(x => x.id === track.id ? { ...x, ...change } : x))} onClose={() => setEditingTrackId(null)} /> : null })()}
    {showTransitionGallery && <TransitionGallery onClose={() => setShowTransitionGallery(false)} />}
    {editingMovieId != null && (() => { const movie = media.find(x => x.id === editingMovieId); return movie && movie.type === 'video'
      ? <MovieEditor item={movie} src={itemThumbUrl(movie) || ''} onChange={change => patch(movie.id, change)} onClose={() => setEditingMovieId(null)} />
      : null })()}
    {lookItemId != null && (() => { const target = media.find(x => x.id === lookItemId); return target && target.type !== 'title'
      ? <PictureLookEditor item={target} src={itemThumbUrl(target) || ''} initialTab={lookTab} detectBars={detectBars} onChange={change => patch(target.id, change)} onClose={() => setLookItemId(null)} />
      : null })()}
    {editingPictureText != null && (() => { const target = media.find(x => x.id === editingPictureText); return target && target.type !== 'title'
      ? <TextEditor mode="picture" item={target} src={itemThumbUrl(target) || ''} defaults={{ fontFamily, fontSize: Number(fontSize) || 48, fontColor, bold: textBold, italic: textItalic, underline: textUnderline, outline: textOutline, textX: defaultTextX, textY: defaultTextY, textFx: defaultStack }} onSave={change => { patch(target.id, change); setEditingPictureText(null) }} onClose={() => setEditingPictureText(null)} />
      : null })()}
    {showTextStyles && <TextStyleModal fontFamily={fontFamily} setFontFamily={setFontFamily} fontSize={fontSize} setFontSize={setFontSize} fontColor={fontColor} setFontColor={setFontColor} bold={textBold} setBold={setTextBold} italic={textItalic} setItalic={setTextItalic} underline={textUnderline} setUnderline={setTextUnderline} outline={textOutline} setOutline={setTextOutline} textX={defaultTextX} setTextX={setDefaultTextX} textY={defaultTextY} setTextY={setDefaultTextY} defaultStack={defaultStack} setDefaultStack={setDefaultStack} onClose={()=>setShowTextStyles(false)}/>} 
    {editingTextFrame !== null && media.find(x=>x.id===editingTextFrame) && <TextEditor mode="frame" item={media.find(x=>x.id===editingTextFrame)!} isNew={editingTextFrame===pendingTextFrame} stacked={storyPreviewId !== null} livePatch={change=>patch(editingTextFrame,change)} onSave={()=>closeTextFrameEditor(true)} onClose={()=>closeTextFrameEditor(false)} onOpenGallery={()=>setShowTransitionGallery(true)}/>} 
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
    {showBrowser && <MediaBrowser onClose={() => setShowBrowser(false)} reloadKey={browserReloadKey} onUploadFiles={startUploads} uploadsStatus={uploadsStatus} onAdd={(files:any[]) => {
      void addFilesToStoryline(files).then(added => notify(`${added} mounted media file${added === 1 ? '' : 's'} added`))
      setShowBrowser(false)
    }}/>} 
    {transitionPreviewId != null && (() => { const index = media.findIndex(x => x.id === transitionPreviewId); return index >= 0 && index < media.length - 1 ? <TransitionPreview outgoing={media[index]} incoming={media[index + 1]} onClose={() => setTransitionPreviewId(null)} onOpenGallery={() => setShowTransitionGallery(true)} onApply={(patchData) => { patch(media[index].id, patchData); setTransitionPreviewId(null); notify(`Applied ${patchData.transition} transition`) }} /> : null })()}
    {showPreview && <Preview media={media} projectName={projectName} previewUrl={previewUrl} previewScope={previewScope} previewMode={previewRunMode} captionDefaults={{ fontFamily, fontSize: Number(fontSize) || 48, fontColor, bold: textBold, italic: textItalic, underline: textUnderline, outline: textOutline, textX: defaultTextX, textY: defaultTextY, textFx: defaultStack }} playing={isPlaying} setPlaying={setPlaying} onClose={() => {setShowPreview(false); setPlaying(false)}}/>}
    {showProjectFileSave && <ProjectFileBrowser
      projectName={projectName}
      snapshot={projectSnapshot}
      initialRoot={projectFileFolder.root}
      initialFolder={projectFileFolder.folder}
      sqliteLabel={projectId ? `Also kept as project #${projectId} in SQLite.` : 'Also stored in SQLite as a new project.'}
      onSqliteOnly={() => { setShowProjectFileSave(false); void saveProject() }}
      onSaved={file => {
        setShowProjectFileSave(false)
        setProjectFileFolder({ root: file.root, folder: file.folder.replace(/^\/(photos|videos|music|output)\/?/, '') })
        // The file and the database row are both "the save": write the file
        // first, then persist, and say honestly which of the two worked.
        void persistProject(true)
          .then(() => notify(`Project saved to ${file.path}${file.overwritten ? ' (replaced)' : ''} · and to SQLite`))
          .catch(() => notify(`Project saved to ${file.path} — the SQLite save failed, so save again once the backend is back`))
      }}
      onClose={() => setShowProjectFileSave(false)} />}
    {showFolderPicker && <FolderPicker current={outputPath} onSelect={p=>{setOutputPath(p);notify(`Output folder set to ${p}`)}} onClose={()=>setShowFolderPicker(false)}/>}
    {showProjectLoader && <ProjectLoader onPick={id=>void loadProject(id)} onLoadFile={file=>void loadProjectFile(file)} onNew={requestNewProject} onClose={()=>setShowProjectLoader(false)} currentProjectId={projectId} onNotify={notify} onDeleted={id=>{ if(id===projectId){ setProjectId(null); localStorage.removeItem('slideshow.project.mock'); notify(`Project #${id} deleted — editor detached`)} }} onDeleteAll={()=>{ setProjectId(null); localStorage.removeItem('slideshow.project.mock'); setPreviewUrl(null); setShowPreview(false); setActiveJobId(null); setRendering(false); setPreviewing(false); setProgress(0); }}/>}
    {showNewProjectConfirm && <ConfirmDialog title="Start a new blank project?" message="This clears the current storyline, soundtracks and settings from the editor. Projects already saved in SQLite are not affected." confirmLabel="New project" onConfirm={startNewProject} onCancel={()=>setShowNewProjectConfirm(false)}/>}
    {showRenderConfirm && <ConfirmDialog title="Start the final MP4 render?" message="This starts the full final render using the current project settings. It may take a while and will write the finished MP4 to the selected output folder. Preview renders remain available immediately without this confirmation." confirmLabel="Render MP4" onConfirm={confirmRender} onCancel={()=>setShowRenderConfirm(false)}/>}
    {showDeleteConfirm && <ConfirmDialog title="Delete selected items?" message={`Are you sure you want to delete ${selectedIds.length} selected item${selectedIds.length > 1 ? 's' : ''}? This action cannot be undone.`} confirmLabel="Delete" onConfirm={deleteSelectedItems} onCancel={()=>setShowDeleteConfirm(false)}/>}
    {showClearAllConfirm && <ConfirmDialog title="Clear all projects?" message="Are you sure you want to delete ALL saved projects and temporary files? The measured render speed is forgotten too, so the next estimate falls back to a guess. This action cannot be undone." confirmLabel="Clear all" onConfirm={clearAllProjects} onCancel={()=>setShowClearAllConfirm(false)}/>}
    {showClearOutputConfirm && <ConfirmDialog title="Clear output directory?" message={`Are you sure you want to delete all files in ${outputPath || '/output'}? This action cannot be undone.`} confirmLabel="Clear output" onConfirm={clearOutputDirectory} onCancel={()=>setShowClearOutputConfirm(false)}/>}
    {showCleanTempConfirm && <ConfirmDialog title="Clean temporary files?" message={`This deletes every intermediate render segment, soundtrack cache and proxy preview (the work and preview folders), and clears the render history. Rendered MP4 files in ${outputPath || '/output'} and your saved projects are kept. This cannot be undone.`} confirmLabel="Clean temp files" onConfirm={cleanTempFiles} onCancel={()=>setShowCleanTempConfirm(false)}/>}
    {overwritePath && <ConfirmDialog title="Output file already exists" message={`${overwritePath} already exists. Rendering again will replace it with the new video.`} confirmLabel="Overwrite & render" onConfirm={()=>{const path=overwritePath;setOverwritePath(null);void startJob('render',true)}} onCancel={()=>setOverwritePath(null)}/>}
    {previewedItem && <MediaLightbox title={previewedItem.name} src={itemThumbUrl(previewedItem) || ''} kind={previewedItem.type === 'video' ? 'video' : previewedItem.type === 'title' ? 'title' : 'image'} titleFrame={previewedItem.type === 'title' ? previewedItem : undefined} onEditFrame={previewedItem.type === 'title' ? () => setEditingTextFrame(previewedItem.id) : undefined} position={`${previewIndex + 1} / ${previewItems.length}`} onPrev={previewIndex > 0 ? () => setStoryPreviewId(previewItems[previewIndex - 1].id) : undefined} onNext={previewIndex + 1 < previewItems.length ? () => setStoryPreviewId(previewItems[previewIndex + 1].id) : undefined} onDelete={deletePreviewedItem} onEdit={previewedItem.type === 'video' ? () => setEditingMovieId(previewedItem.id) : undefined} lookItem={previewedItem.type === 'title' ? null : previewedItem} onLook={() => openLookEditor(previewedItem, 'filters')} onCrop={() => openLookEditor(previewedItem, 'crop')} suspended={editingMovieId != null || lookItemId != null || editingTextFrame != null || editingPictureText != null} rotation={previewedItem.rotation} onRotate={previewedItem.type === 'image' ? rotatePreviewedItem : undefined} onClose={() => setStoryPreviewId(null)} />}
    {uploads.length > 0 && <UploadTray items={uploads} onCancel={id => uploadCancelers.current.get(id)?.()} onClear={() => setUploads([])}/>}
    {toast && <div className="toast"><Check size={16}/>{toast}</div>}
  </div>
}

/** Quick-pick colour swatches for the text colour and the frame background
 *  pickers: vivid basics by default (red, orange, yellow, green, cyan, blue,
 *  purple, magenta, pink, white, grey, black) and a one-click Pastel switch
 *  for soft tones. The mode is shared by every swatch row in the app, so one
 *  click flips them all; the native colour input covers anything else. */
const VIVID_SWATCHES = ['#ff0000', '#ff7f00', '#ffff00', '#00cc00', '#00ffff', '#0066ff', '#8000ff', '#ff00ff', '#ff4d9e', '#ffffff', '#808080', '#000000']
const PASTEL_SWATCHES = ['#ffb3b3', '#ffcc99', '#ffffb3', '#b3e6b3', '#b3ffff', '#a8c8ff', '#cc99ff', '#ffccff', '#ffb3d1', '#ffffff', '#d9d9d9', '#4d4d4d']

// One vivid/pastel choice for every swatch row (tiny pub/sub, no context).
let swatchesPastel = false
const swatchModeListeners = new Set<(pastel: boolean) => void>()
const setSwatchesPastel = (pastel: boolean) => { swatchesPastel = pastel; swatchModeListeners.forEach(l => l(pastel)) }
function useSwatchesPastel() {
  const [pastel, setPastel] = useState(swatchesPastel)
  useEffect(() => { swatchModeListeners.add(setPastel); return () => { swatchModeListeners.delete(setPastel) } }, [])
  return pastel
}

function ColorSwatchPicker({ value, onChange, disabled = false }: { value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const pastel = useSwatchesPastel()
  const colours = pastel ? PASTEL_SWATCHES : VIVID_SWATCHES
  const current = typeof value === 'string' && value ? value : '#ffffff'
  const hex = isHex(current) ? current : '#ffffff'
  return <div className={`swatch-picker${disabled ? ' disabled' : ''}`}>
    <div className="swatch-row">{colours.map(c => <button key={c} type="button" disabled={disabled} className={`swatch${current.toLowerCase() === c.toLowerCase() ? ' active' : ''}`} style={{ background: c }} title={c.toUpperCase()} aria-label={`Colour ${c.toUpperCase()}`} onClick={() => onChange(c)} />)}</div>
    <div className="swatch-foot">
      <div className="swatch-mode" role="group" aria-label="Swatch colours">
        <button type="button" className={pastel ? '' : 'active'} aria-pressed={!pastel} disabled={disabled} title="Vivid basic colours" onClick={() => setSwatchesPastel(false)}>Vivid</button>
        <button type="button" className={pastel ? 'active' : ''} aria-pressed={pastel} disabled={disabled} title="Soft pastel colours" onClick={() => setSwatchesPastel(true)}>Pastel</button>
      </div>
      <span className="swatch-custom" title="Pick any colour"><Palette size={13}/><input type="color" disabled={disabled} value={hex} aria-label="Custom colour" onChange={e => onChange(e.target.value)}/><b>{current.toUpperCase()}</b></span>
    </div>
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
    <div><FieldLabel>Font family <small>{Object.values(FONT_GROUPS).flat().length} fonts · incl. handwriting</small></FieldLabel><FontPicker value={fontFamily} onChange={setFontFamily} sample={sample} /><div className="font-sample" style={{ fontFamily: fontStack(fontFamily), fontWeight: bold && !FONTS_WITHOUT_BOLD.has(fontFamily) ? 700 : 400, fontStyle: italic && !FONTS_WITHOUT_ITALIC.has(fontFamily) ? 'italic' : 'normal', textDecoration: underline ? 'underline' : 'none' }} title="Live sample in the selected font">{sample || FONT_SAMPLE}</div></div>
    <div><FieldLabel>Font size</FieldLabel><NumberStepper value={fontSize} min={8} max={350} step={1} suffix="px" ariaLabel="Font size" onChange={setFontSize} /></div>
    <div><FieldLabel>Text colour</FieldLabel><ColorSwatchPicker value={fontColor} onChange={setFontColor} /></div>
    <div><FieldLabel>Formatting</FieldLabel><div className="style-buttons"><button type="button" className={bold && !FONTS_WITHOUT_BOLD.has(fontFamily) ? 'active' : ''} disabled={FONTS_WITHOUT_BOLD.has(fontFamily)} title={FONTS_WITHOUT_BOLD.has(fontFamily) ? `${fontFamily} has a single weight` : 'Bold'} onClick={() => setBold(!bold)}><b>B</b></button><button type="button" className={italic && !FONTS_WITHOUT_ITALIC.has(fontFamily) ? 'active' : ''} disabled={FONTS_WITHOUT_ITALIC.has(fontFamily)} title={FONTS_WITHOUT_ITALIC.has(fontFamily) ? `${fontFamily} has no italic style` : 'Italic'} onClick={() => setItalic(!italic)}><i>I</i></button><button type="button" className={underline ? 'active' : ''} onClick={() => setUnderline(!underline)}><u>U</u></button></div></div>
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

/** CSS twin of the renderer's caption outline (borderw ≈ size/16 + a 2 px shadow). */
function captionShadow(outline: boolean, sizePx: number): string {
  const shadow = '2px 2px 2px rgba(0,0,0,.55)'
  if (!outline) return shadow
  const w = Math.max(1, Math.round(sizePx / 16))
  return `0 0 ${w}px rgba(0,0,0,.85), 0 0 ${w}px rgba(0,0,0,.85), 0 0 ${w * 2}px rgba(0,0,0,.6), ${shadow}`
}

function TextStyleModal({fontFamily,setFontFamily,fontSize,setFontSize,fontColor,setFontColor,bold,setBold,italic,setItalic,underline,setUnderline,outline=true,setOutline,textX=50,setTextX,textY=72,setTextY,defaultStack,setDefaultStack,onClose}: any) {
  const stack: TextFxLayer[] = defaultStack || []
  const caption: BrowserCaption = { text: 'Summer, slowly.', family: fontFamily, bold, italic, colour: fontColor, isFrame: false }
  const clock = useMotionClock(4.2)
  const input: SceneInput = {
    text: 'Summer, slowly.', stack, family: fontFamily, bold, italic, underline, colour: fontColor, fontSize: Number(fontSize) || 48,
    x: textX, y: textY, align: 'center', outline, start: 0.2, end: 4, steady: 0, bg: null, motion: null,
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><div className="text-style-modal wide-style-modal" onMouseDown={e=>e.stopPropagation()}>
    <div className="modal-head"><div><span className="eyebrow">PROJECT DEFAULTS</span><h2>Default text style</h2></div><button className="icon-button" onClick={onClose}><X size={19}/></button></div>
    <div className="style-modal-body">
      <p>These defaults apply to captions drawn on photos and videos. Standalone text frames keep their own font, size and position.</p>
      <TypeControls fontFamily={fontFamily} setFontFamily={setFontFamily} fontSize={Number(fontSize) || 48} setFontSize={v => setFontSize(String(v))} fontColor={fontColor} setFontColor={setFontColor} bold={bold} setBold={setBold} italic={italic} setItalic={setItalic} underline={underline} setUnderline={setUnderline} />
      <label className="check-label caption-outline-toggle" title="Draws a thin dark outline and a soft shadow behind captions on photos and videos so light text stays readable on bright pictures. Text frames are not affected.">
        <input type="checkbox" checked={outline} onChange={e => setOutline?.(e.target.checked)}/><span><Check size={11}/></span> Outline &amp; shadow behind captions <small>recommended</small>
      </label>
      <TextMotionEditor stack={stack} onChange={next => setDefaultStack(next)} caption={caption} windowSeconds={3.8} wordCount={2} light compact />
      <div className="frame-canvas default-position-stage" style={{background:'#30362d'}}>
        <MotionStage className="stage-fill" input={input} clock={clock}>
          <div className="motion-drag" title="Drag to set the default position" onPointerDown={e => dragOnStage(e, (x, y) => { setTextX(x); setTextY(y) })} style={{ left: `${textX}%`, top: `${textY}%` }}><Move size={14}/></div>
        </MotionStage>
      </div>
      <div className="position-readout light-readout"><Move size={14}/><span>Default position</span><strong>X {Math.round(textX)}% · Y {Math.round(textY)}%</strong></div>
    </div>
    <div className="modal-foot"><span>Changes apply to new text</span><button className="btn ghost" onClick={() => { setTextX(50); setTextY(72) }}>Reset position</button><button className="btn dark" onClick={onClose}><Check size={15}/> Save defaults</button></div>
  </div></div>
}

type CaptionDefaults = {
  fontFamily: string; fontSize: number; fontColor: string; bold: boolean; italic: boolean;
  underline: boolean; outline: boolean; textX: number; textY: number; textFx: TextFxLayer[];
}

/** The frame's colour A → B change on the caption clock (seconds into the hold). */
function bgChangeFor(item: MediaItem): BgChange | null {
  const change = frameColourChange(item)
  return change ? { colourA: change.from, colourB: change.to, transition: change.transition, start: change.start, time: change.time } : null
}

/** Motion path of the canvas editor (textMove* fields), % of the frame. */
function motionPathFor(item: MediaItem) {
  const x = Number.isFinite(Number(item.textX)) ? Number(item.textX) : 50
  const y = Number.isFinite(Number(item.textY)) ? Number(item.textY) : 50
  const fromX = Number.isFinite(Number(item.textMoveFromX)) ? Number(item.textMoveFromX) : x
  const fromY = Number.isFinite(Number(item.textMoveFromY)) ? Number(item.textMoveFromY) : y
  const toX = Number.isFinite(Number(item.textMoveToX)) ? Number(item.textMoveToX) : fromX
  const toY = Number.isFinite(Number(item.textMoveToY)) ? Number(item.textMoveToY) : fromY
  const it: any = item
  const points = effectiveMotionPoints(fromX, fromY, toX, toY, it.textMovePath, it.textMovePathType || (it.textMovePath && it.textMovePath.length >= 2 ? 'freehand' : 'straight'),
    it.textMoveCircleRadius, it.textMoveCircleTurns ?? 1, it.textMoveSineAmplitude ?? 8, it.textMoveSineFrequency ?? 2, it.textMoveStarPoints, it.textMoveStarInnerRatio,
    it.textMoveSymbolRotation, it.textMoveSinusUpDownEnabled, it.textMoveSinusAmplitude, it.textMoveSinusFrequency, it.textMoveBounceHeight, it.textMoveBounceCount, it.textMoveBounceDamping, it.textMoveLissajousFreqY)
  return { points, fromX, fromY, toX, toY }
}

/** The caption of a media item as the preview engine sees it — the same
 * resolution as caption_for_item() in backend/app/text_motion.py. */
function sceneInputFor(item: MediaItem, defaults?: Partial<CaptionDefaults> | null, stack?: TextFxLayer[]): SceneInput {
  const title = item.type === 'title'
  const d = defaults || {}
  const pick = <T,>(value: T | undefined | null, dflt: T | undefined, fallback: T): T => (value !== undefined && value !== null ? value : title ? fallback : (dflt ?? fallback))
  const layers = stack ?? item.textFx ?? []
  const timing = normalizedTextTiming(item)
  const x = Number(pick(item.textX, d.textX, 50))
  const y = Number(pick(item.textY, d.textY, title ? 50 : 72))
  const motion = hasMotionPath(layers) ? { points: motionPathFor(item).points as [number, number][], easing: (item.textMoveEasing as string) || 'linear', rotateAlong: !!(item as any).textMoveRotateAlongPath } : null
  return {
    text: item.text || '', stack: layers,
    family: String(pick(item.fontFamily || undefined, d.fontFamily, 'Montserrat')),
    bold: Boolean(pick(item.textBold, d.bold, true)), italic: Boolean(pick(item.textItalic, d.italic, false)),
    underline: Boolean(pick(item.textUnderline, d.underline, false)),
    colour: String(pick(item.fontColor || undefined, d.fontColor, '#ffffff')),
    fontSize: Number(pick(Number.isFinite(Number(item.fontSize)) ? Number(item.fontSize) : undefined, d.fontSize, 48)),
    x: Math.max(0, Math.min(100, x)), y: Math.max(0, Math.min(100, y)),
    align: title && (item as any).textCentered !== true ? 'left' : 'center',
    outline: title ? (item as any).textOutline === true : (item.textOutline !== undefined ? item.textOutline !== false : d.outline !== false),
    start: timing.textStart, end: timing.textEnd, steady: Math.max(0, Number((item as any).textSteadySeconds) || 0),
    bg: title ? bgChangeFor(item) : null, motion,
  }
}

/** A text frame (or caption) playing with its effects: lightbox and preview. */
function FrameMotionPreview({ item, defaults, playing = true, className = '' }: { item: MediaItem; defaults?: Partial<CaptionDefaults> | null; playing?: boolean; className?: string }) {
  const clock = useMotionClock(Math.max(0.2, Number(item.duration) || 5), playing)
  const input = useMemo(() => sceneInputFor(item, defaults), [item, defaults])
  return <MotionStage className={`stage-fill ${className}`} input={input} clock={clock} background={item.type === 'title' ? item.frameBackground : undefined} bg={item.type === 'title' ? bgChangeFor(item) : null} />
}

// One editor for both caption styles — "Edit picture text" (the overlay drawn
// on a photo/video) and text frames ("New text frame" / "Text frame editor").
// The layout is shared: a 16:9 preview canvas with the mini timeline below it
// (left, or on top in the "below" layout) plus the same control sections in
// the same order. The canvas is drawn by the preview engine — the JavaScript
// twin of the renderer — so what plays here is what the MP4 shows: the stack
// of text effects, and for text frames the colour A → B background change
// with FFmpeg's exact transition geometry, both on one clock. Hovering an
// effect in the browser previews it on top of the stack.
// Picture mode keeps a local draft and writes on "Save"; frame mode patches the
// storyline item live (so a stacked lightbox behind the editor updates as you
// type) and "Cancel" reverts to the original.
function TextEditor({ mode, item, defaults, src, isNew = false, stacked = false, livePatch, onSave, onClose, onOpenGallery }: {
  mode: 'picture' | 'frame'
  item: MediaItem
  defaults?: CaptionDefaults
  src?: string
  isNew?: boolean
  stacked?: boolean
  livePatch?: (change: Partial<MediaItem>) => void
  onSave: (change: Partial<MediaItem>) => void
  onClose: () => void
  onOpenGallery?: () => void
}) {
  const isFrame = mode === 'frame'
  const dd: CaptionDefaults = defaults ?? { fontFamily: 'Montserrat', fontSize: 48, fontColor: '#ffffff', bold: true, italic: false, underline: false, outline: true, textX: 50, textY: 72, textFx: defaultTextFx() }
  const [draft, setDraft] = useState<MediaItem>(() => isFrame ? { ...item, textFx: Array.isArray(item.textFx) ? item.textFx : withIds(dd.textFx) } : (() => {
    const initialTiming = normalizedTextTiming(item)
    const x = Number.isFinite(Number(item.textX)) ? Number(item.textX) : dd.textX
    const y = Number.isFinite(Number(item.textY)) ? Number(item.textY) : dd.textY
    return {
      ...item,
      textEnabled: item.textEnabled !== false,
      fontFamily: item.fontFamily || dd.fontFamily,
      fontSize: Number.isFinite(Number(item.fontSize)) ? Number(item.fontSize) : dd.fontSize,
      fontColor: item.fontColor || dd.fontColor,
      textBold: item.textBold ?? dd.bold,
      textItalic: item.textItalic ?? dd.italic,
      textUnderline: item.textUnderline ?? dd.underline,
      textOutline: item.textOutline ?? dd.outline,
      textX: x,
      textY: y,
      textFx: Array.isArray(item.textFx) ? item.textFx : withIds(dd.textFx),
      textStart: initialTiming.textStart,
      textEnd: initialTiming.textEnd,
      textMoveFromX: Number.isFinite(Number(item.textMoveFromX)) ? Number(item.textMoveFromX) : x,
      textMoveFromY: Number.isFinite(Number(item.textMoveFromY)) ? Number(item.textMoveFromY) : y,
      textMoveToX: Number.isFinite(Number(item.textMoveToX)) ? Number(item.textMoveToX) : x,
      textMoveToY: Number.isFinite(Number(item.textMoveToY)) ? Number(item.textMoveToY) : y,
      textMovePathType: item.textMovePathType || (item.textMovePath && item.textMovePath.length >= 2 ? 'freehand' : 'straight'),
      textMoveEasing: item.textMoveEasing || 'linear',
      textMoveCircleTurns: item.textMoveCircleTurns ?? 1,
      textMoveSineAmplitude: item.textMoveSineAmplitude ?? 8,
      textMoveSineFrequency: item.textMoveSineFrequency ?? 2,
    }
  })())
  const [layout, setLayout] = useState<'sidebar' | 'below'>(() => {
    try { return localStorage.getItem('textFrameLayout') === 'below' ? 'below' : 'sidebar' } catch { return 'sidebar' }
  })
  useEffect(() => { try { localStorage.setItem('textFrameLayout', layout) } catch { /* ignore */ } }, [layout])
  const { setRef: canvasRef, scale: frameScale } = useFrameScale<HTMLDivElement>()
  // Picture mode: the canvas background must be the picture as the render sees
  // it — turned and cropped. The lightbox shares the same cached copy.
  const canvasPhoto = useCroppedSource(src || '', isFrame || item.type === 'video' ? null : item, 'stage', false)
  // Frame mode only: the item as it was when the editor opened (for "Cancel").
  const original = useRef(item)
  const pathRef = useRef<HTMLDivElement | null>(null)

  const apply = (change: Partial<MediaItem>) => {
    setDraft(current => ({ ...current, ...change }))
    if (isFrame) livePatch?.(change)
  }
  const close = () => {
    if (isFrame && !isNew) {
      setDraft(original.current)
      livePatch?.(original.current as Partial<MediaItem>)
    }
    onClose()
  }
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const family = draft.fontFamily || (isFrame ? 'Montserrat' : dd.fontFamily)
  const size = Number(draft.fontSize) || (isFrame ? 48 : dd.fontSize)
  const color = draft.fontColor || (isFrame ? '#ffffff' : dd.fontColor)
  const bold = draft.textBold ?? (isFrame ? true : dd.bold)
  const italic = draft.textItalic ?? (isFrame ? false : dd.italic)
  const underline = draft.textUnderline ?? (isFrame ? false : dd.underline)
  const centeredText = Boolean((draft as any).textCentered)

  // ---- the stack and the shared clock ----
  const stack = draft.textFx || []
  const setStack = (next: TextFxLayer[]) => apply({ textFx: next })
  const [candidate, setCandidate] = useState<{ stack: TextFxLayer[]; flag: string | null } | null>(null)
  const [playing, setPlaying] = useState(true)
  const clipDuration = Math.max(0.2, Number(draft.duration) || 5)
  const clock = useMotionClock(clipDuration, playing)
  const timing = normalizedTextTiming(draft)
  const bg = isFrame ? bgChangeFor(draft) : null
  const motionEnabled = hasMotionPath(stack)
  const motion = motionPathFor(draft)
  const [prep, setPrep] = useState<Prepared | null>(null)
  const [stackPrep, setStackPrep] = useState<Prepared | null>(null)
  const sceneDefaults = isFrame ? null : dd
  const input = useMemo(() => sceneInputFor(draft, sceneDefaults, candidate ? candidate.stack : stack), [draft, candidate, stack])
  const browserCaption: BrowserCaption = {
    text: draft.text || 'Text', family, bold, italic, colour: color, isFrame, background: isFrame ? draft.frameBackground : undefined,
    bgChange: bg ? { colourB: bg.colourB, transition: bg.transition } : null,
  }
  const wordCount = useMemo(() => String(draft.text || '').split(/\s+/).filter(Boolean).length, [draft.text])
  const sceneFor = (s: TextFxLayer[]) => ({ input: sceneInputFor(draft, sceneDefaults, s), background: isFrame ? draft.frameBackground : '#1b1e19', bg, duration: clipDuration })
  const presetExtras = (p: MotionPreset) => {
    const change: Partial<MediaItem> = {}
    if (p.font) Object.assign(change, { fontFamily: p.font.family, ...(p.font.bold !== undefined ? { textBold: p.font.bold } : {}), textItalic: p.font.italic ?? false })
    if (p.frame && isFrame) Object.assign(change, { frameBackground: p.frame.background, frameBackground2: p.frame.background2, frameTransition: p.frame.transition, frameTransitionTime: p.frame.time, frameTransitionStart: p.frame.start })
    if (Object.keys(change).length) apply(change)
  }
  // The motion-path editor's own toggle adds / removes the Motion path layer.
  const pathChange = (change: Partial<MediaItem>) => {
    const { textMoveEnabled, ...rest } = change as any
    let next: Partial<MediaItem> = rest
    if (textMoveEnabled === true && !motionEnabled) next = { ...rest, textFx: [...stack.filter(l => l.effect !== 'motion-path'), { id: newLayerId(), effect: 'motion-path' }] }
    if (textMoveEnabled === false) next = { ...rest, textFx: stack.filter(l => l.effect !== 'motion-path') }
    apply(next)
  }
  const layerParam = (effect: string, name: string) => {
    const l = stack.find(x => x.effect === effect && !x.muted)
    if (!l) return undefined
    const v = l.params?.[name] ?? EFFECTS[effect]?.params?.find(p => p.name === name)?.default
    return Number.isFinite(Number(v)) ? Number(v) : undefined
  }

  // Picture mode only: the caption window (the storyline text-lane handles).
  const minimum = Math.min(MIN_TEXT_SECONDS, timing.duration)
  const setTextStart = (value: number) => apply({ textStart: Math.min(Math.max(0, value), timing.textEnd - minimum) })
  const setTextEnd = (value: number) => apply({ textEnd: Math.max(Math.min(timing.duration, value), timing.textStart + minimum) })

  const save = () => {
    if (isFrame) { onSave({}); return }
    const d: any = draft
    onSave({
      text: draft.text, textEnabled: draft.textEnabled !== false,
      fontFamily: draft.fontFamily, fontSize: draft.fontSize, fontColor: draft.fontColor,
      textBold: draft.textBold, textItalic: draft.textItalic, textUnderline: draft.textUnderline, textOutline: draft.textOutline,
      textX: draft.textX, textY: draft.textY, textFx: normalizeTextFx(stack),
      textStart: timing.textStart, textEnd: timing.textEnd,
      textMoveFromX: d.textMoveFromX, textMoveFromY: d.textMoveFromY, textMoveToX: d.textMoveToX, textMoveToY: d.textMoveToY,
      textMovePath: d.textMovePath, textMovePathType: d.textMovePathType, textMoveEasing: d.textMoveEasing,
      textMoveCircleRadius: d.textMoveCircleRadius, textMoveCircleTurns: d.textMoveCircleTurns,
      textMoveSineAmplitude: d.textMoveSineAmplitude, textMoveSineFrequency: d.textMoveSineFrequency,
      textMoveStarPoints: d.textMoveStarPoints, textMoveStarInnerRatio: d.textMoveStarInnerRatio, textMoveSymbolRotation: d.textMoveSymbolRotation,
      textMoveSinusUpDownEnabled: d.textMoveSinusUpDownEnabled, textMoveSinusAmplitude: d.textMoveSinusAmplitude, textMoveSinusFrequency: d.textMoveSinusFrequency,
      textMoveBounceHeight: d.textMoveBounceHeight, textMoveBounceCount: d.textMoveBounceCount, textMoveBounceDamping: d.textMoveBounceDamping,
      textSteadySeconds: d.textSteadySeconds,
    } as Partial<MediaItem>)
  }

  // ---- colour A / B (text frames) ----
  const sameAsA = isFrame && !isHex(draft.frameBackground2)
  const colourB = isFrame ? (draft.frameBackground2 || draft.frameBackground) : '#30382a'
  const bTime = Math.min(clipDuration, Math.max(0.2, Number(draft.frameTransitionTime) || 1))
  const bStart = Math.min(Math.max(0, clipDuration - bTime), Math.max(0, Number(draft.frameTransitionStart) || 0))
  const bTransition = draft.frameTransition || 'Fade'
  const bSame = !sameAsA && String(colourB).toLowerCase() === String(draft.frameBackground).toLowerCase()
  const followsBg = stack.some(l => !l.muted && (EFFECTS[l.effect]?.bg || l.sync === 'bg' || EFFECTS[l.effect]?.sync === 'bg'))
  const headline = isFrame ? (isNew ? 'New text frame' : 'Text frame editor') : 'Edit picture text'
  const subline = isFrame ? 'DRAG THE TEXT TO POSITION IT · THE PREVIEW IS THE RENDER ENGINE' : 'THIS PICTURE / VIDEO · DRAG THE CAPTION TO POSITION IT'

  // drag handle over the text block (frame px -> % of the canvas)
  const box = prep?.ctx.units.text[0]
  const handleStyle: React.CSSProperties = box
    ? { left: `${draft.textX}%`, top: `${draft.textY}%`, width: `${Math.max(4, (box.w + prep!.em) / FRAME_W * 100)}%`, height: `${Math.max(6, (box.h + prep!.em * 0.4) / FRAME_H * 100)}%` }
    : { left: `${draft.textX}%`, top: `${draft.textY}%` }

  return <div className={`modal-backdrop dark-backdrop${stacked ? ' stacked' : ''}`}><div className={`frame-editor${layout === 'below' ? ' layout-below' : ''}`}>
    <div className="preview-top"><div><strong>{headline}</strong><span>{subline}</span></div><div className="frame-head-actions"><div className="frame-layout-toggle" role="group" aria-label="Editor layout"><button type="button" className={layout === 'sidebar' ? 'active' : ''} title="Sidebar layout — controls in a column on the right" onClick={() => setLayout('sidebar')}><PanelRight size={14}/><span>Sidebar</span></button><button type="button" className={layout === 'below' ? 'active' : ''} title="Below layout — bigger preview with the controls arranged in the space beneath the picture" onClick={() => setLayout('below')}><PanelBottom size={14}/><span>Below</span></button></div><button onClick={close} title={isFrame && isNew ? 'Discard this text frame' : 'Discard changes and close'}><X size={20}/></button></div></div>
    <div className="frame-editor-body">
      <div className="frame-left">
        <div className={`frame-canvas${isFrame ? '' : ' photo'}`} ref={canvasRef} style={{ background: isFrame ? draft.frameBackground : '#000' }}>
          {!isFrame && src && <div className="stage-blur" style={{ backgroundImage: `url(${canvasPhoto.src})`, filter: `blur(${backdropBlurPx(frameScale).toFixed(1)}px) brightness(0.88) saturate(1.2)` }} />}
          {!isFrame && src && (item.type === 'video' ? <video src={src} muted playsInline autoPlay loop /> : <img src={canvasPhoto.src} alt="" draggable={false} />)}
          <MotionStage className="stage-fill" input={input} clock={clock} background={isFrame ? draft.frameBackground : undefined} bg={bg} flag={candidate?.flag}
            onPrepared={p => { setPrep(p); if (!candidate) setStackPrep(p) }}>
            {motionEnabled && motion.points.length > 1 && <svg className="frame-motion-overlay" viewBox="0 0 100 100" preserveAspectRatio="none"><path d={motion.points.map((pt, i) => `${i === 0 ? 'M' : 'L'} ${pt[0]} ${pt[1]}`).join(' ')} fill="none" stroke="rgba(145,169,107,0.85)" strokeWidth="0.6" strokeDasharray={(draft.textMovePathType === 'straight' || !draft.textMovePathType) ? '1.2 1.2' : undefined} /></svg>}
            {motionEnabled && <><span className="motion-handle from small frame-handle" style={{ left: `${motion.fromX}%`, top: `${motion.fromY}%` }}><b>S</b></span><span className="motion-handle to small frame-handle" style={{ left: `${motion.toX}%`, top: `${motion.toY}%` }}><b>E</b></span></>}
            <div className="motion-drag" title="Drag to position the text" style={handleStyle}
              onPointerDown={e => dragOnStage(e, (x, y) => apply({ textX: x, textY: y, ...(motionEnabled ? { textMoveFromX: x, textMoveFromY: y } : {}) }))}><Move size={14}/></div>
          </MotionStage>
          {!isFrame && draft.textEnabled === false && <span className="picture-text-disabled-badge"><EyeOff size={12}/> Hidden</span>}
        </div>
        <TextMotionTimeline stack={stack} onChange={setStack} clock={clock} duration={clipDuration} start={timing.textStart} end={timing.textEnd}
          bg={bg ? { start: bg.start, time: bg.time, from: bg.colourA, to: bg.colourB, transition: bTransition } : null} playing={playing} onPlaying={setPlaying} />
      </div>
      <aside>
        <div className="below-grid">
          <div><FieldLabel>{isFrame ? 'Frame text' : 'Caption'}</FieldLabel><textarea value={draft.text} placeholder={isFrame ? 'Add title text…' : 'Add a caption…'} onChange={e => apply({ text: e.target.value })}/></div>
          {isFrame && <label className="check-label" title="Centre the whole text block on its position and centre every line on the others — at position 50 / 50 the text sits in the middle of the frame"><input type="checkbox" checked={centeredText} onChange={e => apply({ textCentered: e.target.checked } as any)}/><span><Check size={11}/></span> Centre text in frame <small style={{ marginLeft: 6, opacity: .7 }}>lines &amp; block centre on the position</small></label>}
          {!isFrame && <label className="check-label picture-text-enabled"><input type="checkbox" checked={draft.textEnabled !== false} onChange={e => apply({ textEnabled: e.target.checked })}/><span>{draft.textEnabled === false ? <EyeOff size={11}/> : <Eye size={11}/>}</span>Show text on this picture</label>}
          <TypeControls fontFamily={family} setFontFamily={v => apply({ fontFamily: v })} fontSize={size} setFontSize={v => apply({ fontSize: v })} fontColor={color} setFontColor={v => apply({ fontColor: v })} bold={bold} setBold={v => apply({ textBold: v })} italic={italic} setItalic={v => apply({ textItalic: v })} underline={underline} setUnderline={v => apply({ textUnderline: v })} sample={isFrame ? draft.text.split('\n')[0] : (draft.text || 'Caption sample')} />
          <TextMotionEditor stack={stack} onChange={setStack} caption={browserCaption} windowSeconds={Math.max(0.1, timing.textEnd - timing.textStart)} wordCount={wordCount}
            conflicts={stackPrep?.ctx.conflicts} warnings={stackPrep?.ctx.warnings}
            onCandidate={(s, flag) => setCandidate(s ? { stack: s, flag } : null)} sceneFor={sceneFor} onPresetExtras={presetExtras}
            onEditPath={() => pathRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })} />
          <div ref={pathRef}>
            <TextMotionPathEditor
              enabled={motionEnabled}
              fromX={motion.fromX} fromY={motion.fromY} toX={motion.toX} toY={motion.toY}
              path={draft.textMovePath as any}
              pathType={draft.textMovePathType as any}
              easing={draft.textMoveEasing as any}
              circleRadius={draft.textMoveCircleRadius}
              circleTurns={draft.textMoveCircleTurns}
              sineAmplitude={draft.textMoveSineAmplitude}
              sineFrequency={draft.textMoveSineFrequency}
              starPoints={(draft as any).textMoveStarPoints}
              starInnerRatio={(draft as any).textMoveStarInnerRatio}
              symbolRotation={(draft as any).textMoveSymbolRotation}
              sinusEnabled={(draft as any).textMoveSinusUpDownEnabled}
              sinusAmplitude={(draft as any).textMoveSinusAmplitude}
              sinusFrequency={(draft as any).textMoveSinusFrequency}
              bounceHeight={(draft as any).textMoveBounceHeight}
              bounceCount={(draft as any).textMoveBounceCount}
              bounceDamping={(draft as any).textMoveBounceDamping}
              lissajousFreqY={(draft as any).textMoveLissajousFreqY}
              rotateAlong={!!(draft as any).textMoveRotateAlongPath}
              rotateFrom={layerParam('rotate', 'from')}
              rotateTo={layerParam('rotate', 'to')}
              squishFrom={layerParam('squash', 'from')}
              squishTo={layerParam('squash', 'to')}
              onChange={pathChange}
              src={isFrame ? undefined : src}
              isVideo={item.type === 'video'}
              background={isFrame ? draft.frameBackground : undefined}
              caption={draft.text || (isFrame ? 'Title' : 'Add a caption')}
              captionStyle={{ fontFamily: fontStack(family), fontSize: `${size}px`, color, fontWeight: bold && !FONTS_WITHOUT_BOLD.has(family) ? 700 : 400, fontStyle: italic && !FONTS_WITHOUT_ITALIC.has(family) ? 'italic' : 'normal', textDecoration: underline ? 'underline' : 'none' }}
            />
          </div>
          <div className="frame-steady-row">
            <FieldLabel>Hold steady at end <small>once-effects (motion path, grow, rotate, colour) finish X seconds early</small></FieldLabel>
            <div className="motion-params" style={{ marginTop: 4, alignItems: 'center' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>Steady tail <NumberStepper value={Number((draft as any).textSteadySeconds || 0)} min={0} max={Math.max(0, clipDuration - 0.2)} step={0.1} suffix="s" ariaLabel="Hold steady at end" onChange={v => apply({ textSteadySeconds: Math.max(0, Math.min(v, Math.max(0, clipDuration - 0.2))) } as any)} /></label>
            </div>
          </div>
          {isFrame && <>
            <div className="bg-columns">
              <div><FieldLabel>Colour A</FieldLabel><ColorSwatchPicker value={draft.frameBackground} onChange={v => apply({ frameBackground: v })} /></div>
              <div className={sameAsA ? 'dimmed' : ''}><FieldLabel>Colour B</FieldLabel><ColorSwatchPicker value={colourB} onChange={v => apply({ frameBackground2: v })} disabled={sameAsA} />
                <label className="check-label dark" title="Untick to give the frame a second colour: colour B starts as a copy of colour A, pick the colour you want"><input type="checkbox" checked={sameAsA} onChange={e => apply(e.target.checked ? { frameBackground2: undefined } : { frameBackground2: draft.frameBackground, frameTransition: draft.frameTransition || 'Fade', frameTransitionTime: draft.frameTransitionTime || 1, frameTransitionStart: draft.frameTransitionStart ?? Math.max(0, (clipDuration - 1) / 2) })}/><span><Check size={11}/></span>Same as A</label></div>
            </div>
            {!sameAsA && <div className="bg-transition">
              <div className="ab-chip"><i style={{ background: draft.frameBackground }}/><ChevronRight size={12}/><i style={{ background: colourB }}/><span>{bSame ? 'Pick colour B to start the change' : `${bTransition} · starts ${bStart.toFixed(1)}s · ${bTime.toFixed(1)}s`}</span><button type="button" className={`icon-button ${playing ? 'playing' : ''}`} title={playing ? 'Pause the preview' : 'Play the preview (background and text on one clock)'} onClick={() => setPlaying(p => !p)}>{playing ? <Pause size={13}/> : <Play size={13}/>}</button></div>
              <div><FieldLabel>Transition A → B</FieldLabel><TransitionChip value={bTransition} onChange={v => apply({ frameTransition: v })} onOpenGallery={onOpenGallery}/></div>
              <div><FieldLabel>Start at <span>{bStart.toFixed(1)}s</span></FieldLabel><input className="range" type="range" min={0} max={Math.max(0, clipDuration - bTime)} step={0.1} value={bStart} onChange={e => apply({ frameTransitionStart: Number(e.target.value) })}/></div>
              <div><FieldLabel>Duration <span>{bTime.toFixed(1)}s</span></FieldLabel><input className="range" type="range" min={0.2} max={clipDuration} step={0.1} value={bTime} onChange={e => { const t = Number(e.target.value); apply({ frameTransitionTime: t, frameTransitionStart: Math.min(bStart, Math.max(0, clipDuration - t)) }) }}/></div>
              <div className="bg-timeline" title="Frame timeline: A · transition · B"><i style={{ background: draft.frameBackground, flex: bStart }}/><i className="mix" style={{ background: `linear-gradient(90deg,${draft.frameBackground},${colourB})`, flex: bTime }}/><i style={{ background: colourB, flex: Math.max(0, clipDuration - bStart - bTime) }}/></div>
              <div className="bg-sync-hint">
                <Sparkles size={12}/><span>{followsBg ? 'The text reacts to this colour change (see the A→B badges in the lanes).' : 'Let the text react: follow the new colour with the same wipe, or time any layer to the change.'}</span>
                {!followsBg && !bSame && <button type="button" className="btn ghost small" onClick={() => setStack([...stack, { id: newLayerId(), effect: 'bg-follow' }])}>Text follows colour</button>}
              </div>
            </div>}
          </>}
          {!isFrame && <>
            <label className="check-label caption-outline-toggle picture-caption-outline">
              <input type="checkbox" checked={draft.textOutline !== false} onChange={e => apply({ textOutline: e.target.checked })}/><span><Check size={11}/></span>
              Outline &amp; shadow behind this caption
            </label>
            <div className="picture-text-timing">
              <div className="picture-text-timing-head"><FieldLabel>Caption timing on this picture</FieldLabel><span>{formatClock(timing.textStart)} – {formatClock(timing.textEnd)} of {formatClock(timing.duration)}</span></div>
              <div className="picture-text-time-fields"><TimeField label="Starts at" value={timing.textStart} min={0} max={Math.max(0, timing.textEnd - minimum)} onCommit={setTextStart}/><TimeField label="Ends at" value={timing.textEnd} min={Math.min(timing.duration, timing.textStart + minimum)} max={timing.duration} onCommit={setTextEnd}/></div>
              <small>These values are the same caption window controlled by the handles in the storyline text lane.</small>
            </div>
          </>}
          <div className="position-readout"><Move size={14}/><span>Position</span><strong>X {Math.round(draft.textX)}% · Y {Math.round(draft.textY)}%</strong>{stack.length > 0 && <span style={{ marginLeft: 8, opacity: .7 }}>{stack.filter(l => !l.muted).length} effect{stack.filter(l => !l.muted).length === 1 ? '' : 's'} stacked</span>}</div>
          <p><Info size={13}/> {isFrame ? 'Drag the title on the preview. The preview runs the render engine itself: effects stack, and with a colour B the background changes with the exact transition shape. Hover an effect in the browser to try it on top of the stack.' : 'Drag the caption on the preview. The picture is shown whole, letterboxed over a blurred copy exactly as rendered, and the caption plays with its stacked effects as in the MP4.'}</p>
        </div>
      </aside>
    </div>
    <div className="modal-foot"><span>{isFrame ? `Frame duration: ${draft.duration}s` : draft.textEnabled === false ? 'Caption hidden · settings kept' : 'Per-picture settings'}</span><button className="btn ghost" onClick={() => apply({ textX: 50, textY: 50, textMoveFromX: 50, textMoveFromY: 50 })}>Reset position</button><button className="btn ghost" onClick={close}>{isFrame && isNew ? 'Discard' : 'Cancel'}</button><button className="btn dark" onClick={save}><Check size={15}/> {isFrame ? (isNew ? 'Add to storyline' : 'Save') : 'Save picture text'}</button></div>
  </div></div>
}

function KenBurnsPanel({ item, thumb, onPatch, onClose }: { item: MediaItem; thumb: string | null | undefined; onPatch: (patch: Partial<MediaItem>) => void; onClose: () => void }) {
  const kb = isKenBurns(item.effect)
  const zoom = kenBurnsZoomOf(item)
  const focus = kenBurnsFocusOf(item)
  const pct = Math.round((zoom - 1) * 100)
  const zoomMotion = isKenBurnsZoom(item.effect)
  const [replay, setReplay] = useState(0)
  const options = item.type === 'video' ? ['None', ...(item.effect === 'Original motion' ? ['Original motion'] : [])] : effects.filter(x => x !== 'Original motion')
  const setFocus = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = Math.round(Math.min(100, Math.max(0, (e.clientX - rect.left) / rect.width * 100)))
    const y = Math.round(Math.min(100, Math.max(0, (e.clientY - rect.top) / rect.height * 100)))
    onPatch({ kenBurnsX: x, kenBurnsY: y }); setReplay(n => n + 1)
  }
  // Preview animation: zooms scale around the focus point; pans slide an
  // over-scaled copy edge to edge — the same geometry the renderer uses.
  const dir = item.effect.includes('Pan left') ? 'left' : item.effect.includes('Pan right') ? 'right' : item.effect.includes('Pan up') ? 'up' : item.effect.includes('Pan down') ? 'down' : item.effect.includes('Zoom out') ? 'out' : 'in'
  const previewStyle = { '--kbZoom': zoom, '--kbX': `${focus.x}%`, '--kbY': `${focus.y}%`, '--kbSeconds': `${Math.max(1.5, Math.min(8, item.duration || 5))}s` } as React.CSSProperties
  return <div className="kb-panel" onPointerDown={e => e.stopPropagation()} onClick={e => e.stopPropagation()}>
    <div className="kb-head"><Move size={12}/><strong>Motion on this slide</strong><span>{kenBurnsSummary(item)}</span><button type="button" title="Close" aria-label="Close motion settings" onClick={onClose}><X size={13}/></button></div>
    <div className="kb-body">
      <div className={`kb-preview ${kb ? `kb-${dir}` : ''} ${zoomMotion ? 'focusable' : ''}`} key={`${replay}-${item.effect}-${zoom}`} style={previewStyle} onClick={zoomMotion ? setFocus : () => setReplay(n => n + 1)} title={zoomMotion ? 'Click where the zoom should centre on' : kb ? 'Click to replay' : 'Choose a motion to preview it'}>
        {thumb ? <img src={thumb} alt="" draggable={false} style={rotationStyle(item.rotation)}/> : <span className="kb-noimg"><ImageIcon size={18}/></span>}
        {zoomMotion && <i className="kb-focus" style={{ left: `${focus.x}%`, top: `${focus.y}%` }}/>}
        <small>{zoomMotion ? 'click to set focus' : kb ? 'click to replay' : ''}</small>
      </div>
      <div className="kb-controls">
        <label className="kb-row"><span>Motion</span><Select ariaLabel={`${item.name} motion effect`} value={item.effect} onChange={v => onPatch({ effect: v })}>{options.map(x => <option key={x}>{x}</option>)}</Select></label>
        {kb && <>
          <div className="kb-row"><span>Strength</span><div className="kb-presets">{KEN_BURNS_PRESETS.map(p => <button type="button" key={p.label} className={Math.abs(p.zoom - zoom) < 0.005 ? 'active' : ''} title={`${Math.round((p.zoom - 1) * 100)} % ${zoomMotion ? 'zoom' : 'travel'}`} onClick={() => { onPatch({ kenBurnsZoom: p.zoom }); setReplay(n => n + 1) }}>{p.label}</button>)}</div></div>
          <div className="kb-row"><span/><div className="kb-slider"><input type="range" min={Math.round((KEN_BURNS_MIN_ZOOM - 1) * 100)} max={Math.round((KEN_BURNS_MAX_ZOOM_UI - 1) * 100)} step={1} value={pct} aria-label="Ken Burns strength" onChange={e => onPatch({ kenBurnsZoom: Number((1 + Number(e.target.value) / 100).toFixed(2)) })} onPointerUp={() => setReplay(n => n + 1)} onKeyUp={() => setReplay(n => n + 1)}/><b>{pct} %</b></div></div>
          {zoomMotion && <div className="kb-row"><span>Focus</span><div className="kb-focus-readout"><b>{Math.round(focus.x)} % · {Math.round(focus.y)} %</b><button type="button" disabled={Math.round(focus.x) === 50 && Math.round(focus.y) === 50} onClick={() => { onPatch({ kenBurnsX: 50, kenBurnsY: 50 }); setReplay(n => n + 1) }}>Centre</button></div></div>}
          <div className="kb-row"><span/><small className="kb-note">{zoomMotion ? `The picture ${dir === 'in' ? 'zooms in towards' : 'zooms out from'} the focus point over the slide's ${item.duration}s. Strength is how far it zooms; the picture never leaves the frame.` : `The picture glides ${dir} across the frame over the slide's ${item.duration}s at a constant ${pct} % zoom. Focus does not apply to pans.`}</small></div>
          {Math.abs(zoom - KEN_BURNS_DEFAULT_ZOOM) > 0.001 || (zoomMotion && (Math.round(focus.x) !== 50 || Math.round(focus.y) !== 50)) ? <div className="kb-row"><span/><button type="button" className="kb-reset" onClick={() => { onPatch({ kenBurnsZoom: undefined, kenBurnsX: undefined, kenBurnsY: undefined }); setReplay(n => n + 1) }}><RotateCcw size={11}/> Reset to default (12 %, centre)</button></div> : null}
        </>}
      </div>
    </div>
  </div>
}

// Live status for files uploading from this device into the NAS uploads
// volume. Sits bottom-right so it survives picker/drag contexts.
function UploadTray({ items, onCancel, onClear }: { items: UploadItem[], onCancel: (id: number) => void, onClear: () => void }) {
  const busy = items.filter(item => item.status === 'uploading')
  return <div className="upload-tray" role="status" aria-label="Upload progress">
    <div className="upload-tray-head"><Upload size={13}/><strong>{busy.length ? `Uploading ${busy.length} file${busy.length === 1 ? '' : 's'}` : 'Uploads finished'}</strong>{!busy.length && <button type="button" onClick={onClear} aria-label="Clear upload list"><X size={13}/></button>}</div>
    {items.map(item => <div className={`upload-item ${item.status}`} key={item.id} title={item.error || item.name}>
      {item.status === 'error' ? <AlertTriangle size={13}/> : item.status === 'done' ? <Check size={13}/> : <RefreshCw size={13} className="spin"/>}
      <span className="upload-name">{item.name}</span>
      <span className="upload-size">{item.status === 'done' ? 'added' : item.status === 'error' ? 'failed' : `${(item.sent / 1048576).toFixed(1)} / ${(item.total / 1048576).toFixed(1)} MB`}</span>
      {item.status === 'uploading' && <button type="button" className="upload-cancel" aria-label={`Cancel ${item.name}`} title="Cancel this upload" onClick={() => onCancel(item.id)}><X size={11}/></button>}
      <span className="upload-bar"><i style={{ width: `${item.total ? Math.min(100, Math.round(item.sent / item.total * 100)) : 0}%` }}/></span>
      {item.status === 'error' && item.error && <span className="upload-reason">{item.error}</span>}
    </div>)}
  </div>
}


function MediaBrowser({ onClose, onAdd, onUploadFiles, uploadsStatus=null, reloadKey = 0, audioOnly=false }: { onClose: () => void, onAdd: (files:any[]) => void, onUploadFiles?: (files: File[], folder?: string) => void, uploadsStatus?: UploadsStatus|null, reloadKey?: number, audioOnly?:boolean }) {
  const [root,setRoot]=useState<MediaRoot>(audioOnly?'music':'photos')
  const [allMedia,setAllMedia]=useState(!audioOnly)
  const [path,setPath]=useState('');const [entries,setEntries]=useState<any[]>([]);const [selected,setSelected]=useState<any[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
  const [lightbox,setLightbox]=useState<LightboxTarget|null>(null)
  const preview=useAudioPreview(message=>setError(message))
  const uploadInputRef=useRef<HTMLInputElement|null>(null)
  const folderInputRef=useRef<HTMLInputElement|null>(null)
  const [staged,setStaged]=useState<File[]>([])
  const [stagedSkipped,setStagedSkipped]=useState(0)
  const [newFolderOpen,setNewFolderOpen]=useState(false)
  const [newFolderName,setNewFolderName]=useState('')
  const [folderError,setFolderError]=useState('')
  const [creatingFolder,setCreatingFolder]=useState(false)
  // Delete handling for uploads
  const [deleteSelection,setDeleteSelection]=useState<any[]>([])
  const [deleting,setDeleting]=useState(false)
  const [showDeleteConfirm,setShowDeleteConfirm]=useState(false)
  const [deleteError,setDeleteError]=useState('')
  const stageFiles=(picked:File[])=>{
    const accepted=picked.filter(isUploadableFile)
    setStagedSkipped(n=>n+(picked.length-accepted.length))
    setStaged(current=>{
      const seen=new Set(current.map(f=>`${f.name}|${f.size}|${f.lastModified}`))
      return [...current,...accepted.filter(f=>!seen.has(`${f.name}|${f.size}|${f.lastModified}`))]
    })
  }
  const stagedBytes=staged.reduce((sum,f)=>sum+f.size,0)
  const tooLarge=uploadsStatus?staged.filter(f=>f.size>uploadsStatus.maxMb*1048576):[]
  const uploadFolder=!audioOnly&&!allMedia&&root==='uploads'?path:''
  const createFolder=async(event:FormEvent<HTMLFormElement>)=>{
    event.preventDefault()
    const name=newFolderName.trim()
    if(!name){setFolderError('Enter a folder name');return}
    setCreatingFolder(true);setFolderError('');setError('')
    try{
      const response=await fetch('/api/media/folders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({root:'uploads',path,name})})
      if(!response.ok)throw new Error(await readApiError(response,'Could not create folder'))
      const data=await response.json()
      const nextPath=String(data?.entry?.relativePath||[path,name].filter(Boolean).join('/'))
      setNewFolderOpen(false);setNewFolderName('');setPath(nextPath);setSelected([]);setDeleteSelection([])
    }catch(cause){setFolderError(cause instanceof Error?cause.message:'Could not create folder')}
    finally{setCreatingFolder(false)}
  }
  const uploadStaged=()=>{
    const files=staged.filter(f=>!tooLarge.includes(f))
    if(!files.length)return
    onUploadFiles?.(files,uploadFolder)
    setStaged([]);setStagedSkipped(0)
    if(uploadFolder){setAllMedia(false);setRoot('uploads');setPath(uploadFolder);setSelected([]);setDeleteSelection([])}
    else chooseRoot('uploads')
  }
  useEffect(()=>{const el=folderInputRef.current;if(el){el.setAttribute('webkitdirectory','');el.setAttribute('directory','')}},[audioOnly])
  useEffect(()=>{
    let cancelled=false
    setLoading(true);setError('')
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
  },[root,path,allMedia,audioOnly,reloadKey])
  const chooseRoot=(value:MediaRoot)=>{setAllMedia(false);setRoot(value);setPath('');setSelected([]);setDeleteSelection([]);setNewFolderOpen(false);setFolderError('');setNewFolderName('');setDeleteError('')}
  const showAllMedia=()=>{setAllMedia(true);setPath('');setSelected([]);setDeleteSelection([]);setNewFolderOpen(false);setFolderError('');setNewFolderName('')}
  const fileRoot=(entry:any):MediaRoot=>(entry.rootName as MediaRoot)||mediaRootFromPath(entry.path,root)
  const open=(entry:any)=>{if(entry.kind==='directory'){if(entry.accessible===false){setError(`No permission to open “${entry.name}”. The container user cannot read this folder — check DSM share/ACL permissions and the PUID/PGID in your compose file.`);return}setPath(entry.relativePath);setDeleteSelection([])}else setSelected(items=>items.some(x=>x.path===entry.path)?items.filter(x=>x.path!==entry.path):[...items,entry])}
  const viewFile=(entry:any)=>{
    const kind: LightboxTarget['kind'] = entry.kind==='video'?'video':entry.kind==='audio'?'audio':'image'
    setLightbox({ title: entry.name, src: mediaFileUrl(fileRoot(entry), entry.path), kind })
  }
  const skippedEmpty = selected.filter((f:any)=>f.empty).length
  const addable = selected.filter((f:any)=>!f.empty)
  const isUploadsView = !audioOnly && !allMedia && root==='uploads'
  const toggleDeleteSelect=(entry:any)=>{
    setDeleteError('')
    setDeleteSelection(items=>items.some(x=>x.path===entry.path)?items.filter(x=>x.path!==entry.path):[...items,entry])
  }
  const deleteSingle=(entry:any)=>{
    setDeleteSelection([entry])
    setDeleteError('')
    setShowDeleteConfirm(true)
  }
  const confirmDelete=async()=>{
    if(!deleteSelection.length)return
    setDeleting(true);setDeleteError('');setError('')
    try{
      const payload={root:'uploads',paths:deleteSelection.map((e:any)=>e.relativePath)}
      const res=await fetch('/api/media/delete-batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
      if(!res.ok)throw new Error(await readApiError(res,'Could not delete'))
      const data=await res.json()
      if(data.errors&&data.errors.length){
        const deletedSet=new Set((data.deleted as string[])||[])
        setEntries(cur=>cur.filter((e:any)=>!deletedSet.has(e.relativePath)))
        setSelected(cur=>cur.filter((e:any)=>!deletedSet.has(e.relativePath)))
        setDeleteSelection(cur=>cur.filter((e:any)=>!deletedSet.has(e.relativePath)))
        if(data.errors.length) setDeleteError(data.errors.map((x:any)=>`${x.path}: ${x.error}`).join(' · '))
        if(deletedSet.size===0) throw new Error(deleteError||'Delete failed')
      }else{
        const deletedSet=new Set((data.deleted as string[])||deleteSelection.map((e:any)=>e.relativePath))
        setEntries(cur=>cur.filter((e:any)=>!deletedSet.has(e.relativePath)))
        setSelected(cur=>cur.filter((e:any)=>!deletedSet.has(e.relativePath)))
        setDeleteSelection([])
      }
      setShowDeleteConfirm(false)
    }catch(cause){
      setDeleteError(cause instanceof Error?cause.message:'Could not delete')
    }finally{
      setDeleting(false)
    }
  }
  return <div className="modal-backdrop" onMouseDown={onClose}><div className="browser-modal" onMouseDown={e=>e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">DOCKER-MOUNTED MEDIA</span><h2>{audioOnly?'Select MP3 soundtracks':'Select photos & videos'}</h2></div><button className="icon-button" onClick={onClose}><X size={19}/></button></div><div className="browser-body"><div className="folder-tree"><strong>LOCATIONS</strong>{audioOnly?<button className="active" onClick={()=>chooseRoot('music')}><Music2 size={16}/> music</button>:<><button className={allMedia?'active':''} onClick={showAllMedia} title="List the photos and videos mounts together — every playable file, mixed"><Film size={16}/> All media</button><button className={!allMedia&&root==='photos'?'active':''} onClick={()=>chooseRoot('photos')} title="Browse the /photos mount (photos and videos inside it)"><ImageIcon size={16}/> photos</button><button className={!allMedia&&root==='videos'?'active':''} onClick={()=>chooseRoot('videos')} title="Browse the /videos mount (videos and photos inside it)"><Video size={16}/> videos</button><button className={!allMedia&&root==='uploads'?'active':''} onClick={()=>chooseRoot('uploads')} title="Files uploaded from this device — stored on the NAS in the uploads volume"><HardDriveUpload size={16}/> uploads</button><hr/><strong>UPLOAD FROM THIS DEVICE</strong><button type="button" className="upload-location" onClick={()=>uploadInputRef.current?.click()} title="Pick one or more photos or movies on this device (Ctrl/Cmd-click or Shift-click for several)"><Upload size={16}/> Choose files…</button><button type="button" className="upload-location" onClick={()=>folderInputRef.current?.click()} title="Pick a whole folder on this device — every photo and movie inside it (subfolders included) is uploaded"><FolderUp size={16}/> Choose folder…</button><input ref={uploadInputRef} type="file" accept="image/*,video/*,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff,.mp4,.mov,.mkv,.avi,.webm,.m4v,.wmv,.asf,.mpg,.mpeg,.ts,.mts,.m2ts,.flv,.f4v,.3gp,.3gpp,.vob,.dav,.mxf,.mod,.tod,.divx" multiple hidden onChange={e=>{const files=Array.from(e.target.files||[]); e.target.value=''; if(files.length) stageFiles(files)}}/><input ref={folderInputRef} type="file" multiple hidden onChange={e=>{const files=Array.from(e.target.files||[]); e.target.value=''; if(files.length) stageFiles(files)}}/>{uploadsStatus&&!uploadsStatus.writable&&<p className="upload-warning"><AlertTriangle size={11}/> {uploadsStatus.reason}</p>}{(staged.length>0||stagedSkipped>0)&&<div className="upload-stage"><div className="upload-stage-head"><strong>{staged.length} file{staged.length===1?'':'s'} · {(stagedBytes/1048576).toFixed(1)} MB</strong><button type="button" onClick={()=>{setStaged([]);setStagedSkipped(0)}} aria-label="Clear selection"><X size={12}/></button></div><ul>{staged.slice(0,8).map(f=><li key={`${f.name}|${f.size}|${f.lastModified}`} className={tooLarge.includes(f)?'too-large':''} title={(f as any).webkitRelativePath||f.name}><span>{f.name}</span><small>{(f.size/1048576).toFixed(1)} MB</small><button type="button" aria-label={`Remove ${f.name}`} onClick={()=>setStaged(c=>c.filter(x=>x!==f))}><X size={10}/></button></li>)}{staged.length>8&&<li className="more">… and {staged.length-8} more</li>}</ul>{stagedSkipped>0&&<small className="stage-note">{stagedSkipped} file{stagedSkipped===1?'':'s'} skipped — not a photo or movie</small>}{tooLarge.length>0&&<small className="stage-note warn">{tooLarge.length} file{tooLarge.length===1?'':'s'} over the {uploadsStatus?.maxMb} MB limit will be skipped</small>}<button type="button" className="btn dark upload-go" disabled={staged.length-tooLarge.length===0||uploadsStatus?.writable===false} onClick={uploadStaged}><Upload size={14}/> Upload {staged.length-tooLarge.length} file{staged.length-tooLarge.length===1?'':'s'}{uploadFolder?` to /uploads/${uploadFolder}`:' to /uploads'}</button><small className="stage-note">Files upload to the current /uploads folder. Browse there or create a new folder before pressing Upload.</small></div>}</>}<hr/><strong>SECURITY</strong><p>Only configured mounts are accessible: photos, videos and music are read-only, uploads is the writable volume files from this device land in. Folders the container user cannot read stay listed but cannot be opened. Spaces and punctuation in file names are allowed.</p><p>All playable formats are accepted everywhere — a video found under /photos and a photo found under /videos are both added with the mount they really live in.</p></div><div className="file-area"><div className="breadcrumbs"><button disabled={!path} onClick={()=>setPath(path.split('/').slice(0,-1).join('/'))}>← Parent</button><span>/{allMedia&&!audioOnly?'photos & videos':root}/{path}</span><button onClick={()=>setSelected(entries.filter(x=>x.kind!=='directory'&&!x.empty&&x.accessible!==false))}>Select visible files</button></div>{isUploadsView&&<div className="upload-folder-toolbar"><span><Upload size={12}/> Upload destination: <b>/uploads{path?`/${path}`:''}</b></span><button type="button" disabled={uploadsStatus?.writable===false} title={uploadsStatus?.writable===false?'The uploads volume is not writable':'Create a folder for local uploads'} onClick={()=>{setNewFolderOpen(true);setFolderError('')}}><FolderPlus size={13}/> New folder</button></div>}{newFolderOpen&&isUploadsView&&<form className="new-folder-form" onSubmit={createFolder}><FolderPlus size={15}/><input autoFocus value={newFolderName} aria-label="New folder name" placeholder="Folder name" onChange={event=>setNewFolderName(event.target.value)} disabled={creatingFolder}/><button type="submit" className="btn dark" disabled={creatingFolder||uploadsStatus?.writable===false}>{creatingFolder?<RefreshCw className="spin" size={13}/>:<Check size={13}/>} Create</button><button type="button" className="btn ghost" onClick={()=>{setNewFolderOpen(false);setFolderError('')}} disabled={creatingFolder}>Cancel</button>{folderError&&<small className="new-folder-error">{folderError}</small>}</form>}{isUploadsView&&<div className="upload-delete-toolbar"><div className="udt-left"><Trash2 size={13}/><strong>{deleteSelection.length?`${deleteSelection.length} selected for deletion`:'Select files/folders to delete'}</strong>{deleteSelection.length>0&&<><button type="button" className="btn ghost small" onClick={()=>setDeleteSelection([])}>Clear</button><button type="button" className="btn dark small delete-btn" disabled={deleting||uploadsStatus?.writable===false} onClick={()=>setShowDeleteConfirm(true)}>{deleting?<RefreshCw className="spin" size={12}/>:<Trash2 size={12}/>} Delete selected</button></>}</div><div className="udt-right"><button type="button" className="btn ghost small" disabled={!entries.length} onClick={()=>setDeleteSelection(entries)} title="Select every file and folder in this folder for deletion">Select all</button><button type="button" className="btn ghost small" disabled={!entries.length} onClick={()=>setDeleteSelection(entries.filter((e:any)=>e.kind!=='directory'))} title="Select only files, not folders">Select files</button></div></div>}{loading&&<div className="browser-info"><RefreshCw className="spin" size={15}/> Reading mounted folder…</div>}{error&&<div className="notice amber"><AlertTriangle size={15}/><span>{error}</span></div>}{deleteError&&<div className="notice red"><AlertTriangle size={15}/><span>{deleteError}</span></div>}<div className="file-grid">{entries.map(file=>{
  const isDelSelected=deleteSelection.some((x:any)=>x.path===file.path)
  const isSel=selected.some((x:any)=>x.path===file.path)
  return <div className={`file-card ${isSel?'selected':''} ${isDelSelected?'delete-selected':''} ${file.empty?'empty':''} ${file.accessible===false?'inaccessible':''}`} key={file.path}>
    {isUploadsView&&<button type="button" className={`delete-check ${isDelSelected?'checked':''}`} disabled={uploadsStatus?.writable===false} onClick={(e)=>{e.stopPropagation();toggleDeleteSelect(file)}} title={isDelSelected?'Deselect for deletion':'Select for deletion'} aria-label={isDelSelected?`Deselect ${file.name} for deletion`:`Select ${file.name} for deletion`}>{isDelSelected&&<Check size={12}/>}</button>}
    <button type="button" className="file-thumb" onClick={()=>file.kind==='directory'?open(file):file.kind==='image'||file.kind==='video'?viewFile(file):open(file)} title={file.kind==='directory'?(file.accessible===false?'No permission to open this folder':'Open folder'):file.kind==='image'||file.kind==='video'?'View':file.name}>
      {file.kind==='audio'&&<span className={`audio-hover-play ${preview.playingKey===file.path?'playing':''}`} title={preview.playingKey===file.path?'Stop preview':'Play preview'} onClick={e=>{e.stopPropagation();preview.toggle(file.path,mediaFileUrl(fileRoot(file),file.path),file.name)}}>{preview.playingKey===file.path?<Pause size={14}/>:<Play size={13}/>}</span>}
      {file.kind==='audio'&&preview.playingKey===file.path ? <span className="card-player" onClick={e=>e.stopPropagation()}><AudioSeekBar bars={32} seed={3} color="#58703a" current={preview.progress.current} duration={preview.progress.duration} onSeek={preview.seek} className="compact"/><AudioTimeReadout current={preview.progress.current} duration={preview.progress.duration}/></span> : <BrowserThumb root={fileRoot(file)} file={file}/>}
      {file.empty&&<span className="empty-badge"><AlertTriangle size={10}/> EMPTY · 0 B</span>}
      {file.kind==='directory'&&file.accessible===false&&<span className="empty-badge"><AlertTriangle size={10}/> NO ACCESS</span>}
      {(file.kind==='image'||file.kind==='video')&&!file.empty&&<span className="thumb-zoom"><ZoomIn size={13}/></span>}
      {isSel&&<span className="selected-check"><Check size={13}/></span>}
    </button>
    <button type="button" className="file-card-meta" onClick={()=>file.empty?undefined:open(file)}><strong>{file.name}</strong><small>{file.kind==='directory'?(file.accessible===false?'No permission':'Folder'):file.empty?'0 B — unreadable':`${allMedia&&!audioOnly&&file.rootName?`${file.rootName} · `:''}${(file.size/1024/1024).toFixed(1)} MB`}</small></button>
    {isUploadsView&&<button type="button" className="file-card-delete" title={`Delete ${file.name}`} aria-label={`Delete ${file.name}`} disabled={uploadsStatus?.writable===false} onClick={(e)=>{e.stopPropagation();deleteSingle(file)}}><Trash2 size={13}/></button>}
  </div>
})}</div><div className="browser-info"><Info size={15}/> Click a photo or video to preview it. Click the name to select it for the storyline — pictures and videos can be mixed freely. Empty (0-byte) files are marked and skipped automatically. File names may include spaces, dashes and punctuation. {isUploadsView&&<>Use the checkboxes to select files/folders, then Delete selected. Deleting a folder removes everything inside it.</>}</div></div></div><div className="modal-foot"><span>{selected.length} files selected{skippedEmpty?` · ${skippedEmpty} empty file${skippedEmpty>1?'s':''} skipped`:''}{deleteSelection.length?` · ${deleteSelection.length} marked for deletion`:''}</span><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn dark" disabled={!addable.length} onClick={()=>onAdd(addable)}><Plus size={15}/> Add to storyline</button></div></div>{lightbox&&<MediaLightbox title={lightbox.title} src={lightbox.src} kind={lightbox.kind} onClose={()=>setLightbox(null)}/>}{showDeleteConfirm&&<div className="modal-backdrop" onMouseDown={()=>!deleting&&setShowDeleteConfirm(false)}><div className="confirm-modal" onMouseDown={e=>e.stopPropagation()}><div className="confirm-icon"><AlertTriangle size={24}/></div><h2>Delete from /uploads?</h2><p>{deleteSelection.length===1?`Are you sure you want to delete “${deleteSelection[0]?.name}”? ${deleteSelection[0]?.kind==='directory'?'The folder and everything inside it will be removed.':''} This cannot be undone.`:`Are you sure you want to delete ${deleteSelection.length} items from /uploads${path?`/${path}`:''}? ${deleteSelection.some((e:any)=>e.kind==='directory')?'Folders will be removed recursively.':''} This cannot be undone.`}</p>{deleteSelection.length>1&&deleteSelection.length<=12&&<ul className="delete-list">{deleteSelection.map((e:any)=><li key={e.path}>{e.kind==='directory'?'📁 ':'📄 '}{e.name}</li>)}</ul>}{deleteSelection.length>12&&<p><small>First 12: {deleteSelection.slice(0,12).map((e:any)=>e.name).join(', ')} …</small></p>}<div className="confirm-actions"><button className="btn ghost" disabled={deleting} onClick={()=>setShowDeleteConfirm(false)}>Cancel</button><button className="btn dark" disabled={deleting} onClick={()=>void confirmDelete()}>{deleting?<RefreshCw className="spin" size={14}/>:<Trash2 size={14}/>} {deleting?'Deleting…':'Delete'}</button></div></div></div>}</div>
}


function FolderPicker({ current, onSelect, onClose }: { current: string, onSelect: (path: string) => void, onClose: () => void }) {
  const [path,setPath]=useState(()=>current.replace(/^\/output\/?/,''));const [entries,setEntries]=useState<any[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
  useEffect(()=>{setLoading(true);setError('');fetch(`/api/media/browse?root=output&folders=true&path=${encodeURIComponent(path)}`).then(async r=>{if(!r.ok)throw new Error(await readApiError(r,'Could not open folder'));return r.json()}).then(data=>setEntries(data.entries||[])).catch(e=>{setEntries([]);setError(e.message)}).finally(()=>setLoading(false))},[path])
  const chosen=path?`/output/${path}`:'/output'
  return <div className="modal-backdrop" onMouseDown={onClose}><div className="browser-modal folder-picker" onMouseDown={e=>e.stopPropagation()}><div className="modal-head"><div><span className="eyebrow">OUTPUT DESTINATION</span><h2>Choose output folder</h2></div><button className="icon-button" onClick={onClose}><X size={19}/></button></div><div className="picker-body"><div className="breadcrumbs"><button disabled={!path} onClick={()=>setPath(path.split('/').slice(0,-1).join('/'))}>← Parent</button><span>{chosen}</span><button disabled={!path} onClick={()=>setPath('')}>Root</button></div>{loading&&<div className="browser-info"><RefreshCw className="spin" size={15}/> Reading output volume…</div>}{error&&<div className="notice amber"><AlertTriangle size={15}/><span>{error}</span></div>}<div className="file-grid">{entries.map(dir=><button className={`file-card ${dir.accessible===false?'inaccessible':''}`} key={dir.relativePath} onClick={()=>{if(dir.accessible===false){setError(`No permission to open “${dir.name}”.`);return}setPath(dir.relativePath)}}><div className="server-file-icon"><FolderOpen size={34}/></div><strong>{dir.name}</strong><small>{dir.accessible===false?'No permission':'Folder'}</small></button>)}</div>{!loading&&!error&&entries.length===0&&<div className="browser-info"><Info size={15}/> No subfolders here. Keep this folder or navigate back with “← Parent”.</div>}<div className="browser-info"><Info size={15}/> Renders are written into the selected folder on the mounted /output volume. New subfolders typed manually are created at render time.</div></div><div className="modal-foot"><span>Selected: {chosen}</span><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn dark" onClick={()=>{onSelect(chosen);onClose()}}><Check size={15}/> Use this folder</button></div></div></div>
}

// Lists projects persisted in SQLite and loads the chosen one's full config
// (media, captions, soundtrack, output, timeline) into the editor.
// Now includes a delete button per entry and a single "Delete all" control.
function ProjectLoader({ onPick, onNew, onClose, currentProjectId, onDeleted, onDeleteAll, onNotify, onLoadFile }: {
  onPick: (id: number) => void, onNew?: () => void, onClose: () => void,
  currentProjectId?: number | null, onDeleted?: (id: number) => void, onDeleteAll?: () => void, onNotify?: (msg: string)=>void,
  // Second tab: open a project file from any mounted volume.
  onLoadFile?: (file: ProjectFileInfo) => void
}) {
  const [projects,setProjects]=useState<any[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(false)
  const [tab,setTab]=useState<'sqlite'|'files'>('sqlite')
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
    <div className="modal-head"><div><span className="eyebrow">{tab==='sqlite'?'SAVED IN SQLITE':'PROJECT FILES ON THE MOUNTED VOLUMES'}</span><h2>Load project</h2></div>
      <div className="project-loader-actions">
        {tab==='sqlite' && projects.length>0 && <button type="button" className="btn ghost delete-all-btn" disabled={deletingAll||loading} title="Delete every saved project" onClick={()=>setShowDeleteAllConfirm(true)}><Trash2 size={14}/> Delete all</button>}
        <button className="icon-button" onClick={onClose}><X size={19}/></button>
      </div>
    </div>
    <div className="picker-tabs" role="tablist">
      <button type="button" role="tab" aria-selected={tab==='sqlite'} className={tab==='sqlite'?'active':''} onClick={()=>setTab('sqlite')}><FolderOpen size={13}/> Saved in SQLite</button>
      <button type="button" role="tab" aria-selected={tab==='files'} className={tab==='files'?'active':''} onClick={()=>setTab('files')} title="Browse /output and the read-only media mounts for a .slideshow.json project file"><FileJson size={13}/> Browse files</button>
    </div>
    {tab==='files'
      ? <><ProjectFilePanel mode="load" onLoaded={file=>onLoadFile?.(file)}/>
          <div className="modal-foot">
            <span>Click a project file to open it — loading replaces the current editor contents.</span>
            {onNew&&<button className="btn ghost" onClick={onNew}><Plus size={15}/> New blank project</button>}
            <button className="btn ghost" onClick={onClose}>Cancel</button>
          </div></>
      : <><div className="picker-body project-list">{loading&&<div className="browser-info"><RefreshCw className="spin" size={15}/> Reading saved projects…</div>}{error&&<div className="notice amber"><AlertTriangle size={15}/><span>{error}</span></div>}{!loading&&!error&&projects.length===0&&<div className="browser-info"><Info size={15}/> No saved projects yet. Use “Save project” to store the current editor contents.</div>}
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
    </div></>}
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

export function TransitionPreview({ outgoing, incoming, onClose, onApply, onOpenGallery }: { outgoing: MediaItem; incoming: MediaItem; onClose: () => void; onApply: (patch: Partial<MediaItem>) => void; onOpenGallery?: () => void }) {
  const [choice, setChoice] = useState(outgoing.transition)
  const [duration, setDuration] = useState(outgoing.transitionTime ?? DEFAULT_TRANSITION_SECONDS)
  const [params, setParams] = useState<Record<string,string|number>>((outgoing.transitionParams as Record<string,string|number>)||{})
  const [easing, setEasing] = useState(outgoing.transitionEasing || EASING_DEFAULT)
  const [reverse, setReverse] = useState(outgoing.transitionReverse || 0)
  const [accurateUrl, setAccurateUrl] = useState<string | null>(null)
  const [rendering, setRendering] = useState(false)
  const [error, setError] = useState('')
  const [loopKey, setLoopKey] = useState(0)
  // The stored example clip (rendered once per transition, cached on the NAS)
  // plays immediately while the accurate 360p render runs in the background.
  const [storedState, setStoredState] = useState<'loading' | 'ready' | 'broken'>('loading')
  const storedUrl = transitionPreviewUrl(choice)
  // when choice changes, sync tab, reset params if needed and re-arm the stored clip
  useEffect(()=>{ if(isGLTransition(choice)){ const defs=getGLParams(choice); const next:Record<string,string>={}; for(const d of defs) next[d.name]= String(params[d.name] ?? d.default); if(Object.keys(next).length) setParams(next)} }, [choice]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setStoredState('loading') }, [storedUrl])
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
  return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}><div className="transition-preview-modal wide" onMouseDown={e=>e.stopPropagation()}>
    <div className="preview-top"><div><strong>Transition preview</strong><span>{outgoing.name} → {incoming.name}</span></div><button onClick={onClose}><X size={20}/></button></div>
    <div className="transition-preview-body"><div className="transition-preview-stage">
      {/* The stored example clip plays instantly so the popup is never blank;
          the accurate FFmpeg render builds in the background and takes over
          when ready. The stored clip is a real FFmpeg render (two example
          frames) — not a CSS approximation — so motion and easing are honest,
          only the content and exact timing come from the accurate pass. */}
      {accurateUrl
        ? <video key={`${accurateUrl}-${loopKey}`} src={accurateUrl} controls autoPlay loop />
        : <>
            {storedState !== 'broken' && <video key={storedUrl} src={storedUrl} muted loop autoPlay playsInline preload="auto"
              onLoadedData={() => setStoredState('ready')} onError={() => setStoredState('broken')} />}
            {storedState !== 'ready' && <div className="preview-waiting"><RefreshCw className="spin" size={22}/><span>{rendering ? 'Rendering the transition at 360p…' : 'Preparing…'}</span></div>}
            {storedState === 'ready' && !error && <span className="preview-rendering-note"><RefreshCw size={12} className="spin"/> Rendering accurate 360p in the background…</span>}
          </>}
      <span className="preview-quality">{accurateUrl ? 'ACCURATE FFMPEG · 360P' : storedState === 'ready' ? 'STORED PREVIEW · RENDERING 360P…' : 'RENDERING ACCURATE FFMPEG · 360P…'}</span>
      {error && <div className="transition-preview-error"><AlertTriangle size={15}/>{error}</div>}
      <button className="btn ghost replay-transition" onClick={()=>setLoopKey(x=>x+1)}><Play size={13}/> Replay</button>
    </div><aside>
      <TransitionChip value={choice} onChange={setChoice} onOpenGallery={onOpenGallery} />
      {isGLTransition(choice) && <div className="gl-preview-params"><FieldLabel>GL parameters — {choice}</FieldLabel><GLParamControls transition={choice} params={params} onChange={setParams}/></div>}
      <label>Duration</label><NumberStepper value={duration} min={MIN_TRANSITION_SECONDS} max={10} step={.1} suffix="s" ariaLabel="Preview transition duration" onChange={setDuration}/>
      <label>Easing</label><EasingSelect value={easing} onChange={setEasing}/>
      <label className="check-label"><input type="checkbox" checked={Boolean(reverse)} onChange={e=>setReverse(e.target.checked?1:0)}/><span><Check size={11}/></span> Reverse</label>
    </aside></div>
    <div className="modal-foot"><span>Uses /api/transitions/preview — accurate 360p FFmpeg render with your params, easing and reverse. The sample is the transition alone, so it runs for exactly the duration you set.</span><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn dark" onClick={()=>onApply({transition: choice, transitionTime: duration, transitionParams: params, transitionEasing: easing, transitionReverse: reverse})}>Apply transition</button></div>
  </div></div>
}

function Preview({ media, projectName, previewUrl, previewScope = 'all', previewMode = 'standard', captionDefaults, playing, setPlaying, onClose }: { media: MediaItem[], projectName: string, previewUrl:string|null, previewScope?: number|'all', previewMode?: PreviewMode, captionDefaults?: Partial<CaptionDefaults>, playing: boolean, setPlaying: (x: boolean) => void, onClose: () => void }) {
  const [current, setCurrent] = useState(0)
  const [stageFailed, setStageFailed] = useState(false)

  useEffect(() => {
    if (!playing || media.length === 0) return
    const item = media[current]
    if (!item) return
    if (item.type === 'video') {
      return
    }
    const currentDuration = Math.max(MIN_CLIP_SECONDS, item.duration || 5) * 1000
    const timer = setTimeout(() => {
      setCurrent(c => (c + 1) % media.length)
    }, currentDuration)
    return () => clearTimeout(timer)
  }, [playing, current, media])
  useEffect(() => setStageFailed(false), [current])


  const currentItem = media[current]
  const origUrl = currentItem ? itemThumbUrl(currentItem) : ''
  const previewStageUrl = currentItem?.type === 'video' && currentItem.path ? (() => { try { return moviePreviewUrl(currentItem, 640) } catch { return origUrl } })() : origUrl
  const [stageUsePreview, setStageUsePreview] = useState(false)
  useEffect(() => { setStageUsePreview(false); setStageFailed(false) }, [origUrl, previewStageUrl])
  const currentUrl = stageUsePreview ? previewStageUrl : origUrl
  const stageIsVideo = currentItem?.type === 'video'
  const stageCrop = useCroppedSource(currentUrl, stageIsVideo ? null : currentItem, 'stage', false)
  const stageLook = usePictureLook(stageCrop.ready ? stageCrop.src : currentUrl, currentItem, false, !stageIsVideo, stageCrop.rotationApplied)
  const stageTurned = stageCrop.rotationApplied || stageLook.rotationBaked
  // Captions and text frames play with their stacked effects through the
  // preview engine (the JavaScript twin of the renderer), text frames with
  // their colour A -> B background change.
  const defaults = captionDefaults || null
  const showCaption = currentItem && (currentItem.type === 'title' || (currentItem.textEnabled !== false && String(currentItem.text || '').trim() !== ''))

  if(previewUrl)return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}><div className="preview-modal" onMouseDown={e=>e.stopPropagation()}><div className="preview-top"><div><strong>FFmpeg preview{previewScope !== 'all' ? ` · ${previewScope} selected slide${previewScope === 1 ? '' : 's'}` : ''}</strong><span>REAL PROXY RENDER · 640 × 360{previewScope !== 'all' ? ' · SELECTION ONLY' : ''}{previewMode === 'fast' ? ' · FAST TEXT + TRANSITIONS' : ''}</span></div><button type="button" onClick={onClose} aria-label="Close preview"><X size={20}/></button></div><video className="real-preview-video" src={previewUrl} controls autoPlay/><div className="preview-note"><Info size={14}/> {previewMode === 'fast' ? 'Fast diagnostic: text-bearing holds and configured transitions are rendered; static holds without text and soundtrack are skipped.' : 'This file is streamed through the backend project API from the mounted preview volume.'}<a className="btn dark" href={previewUrl} download>Download preview</a></div></div></div>

  const advance = () => setCurrent(c => (c + 1) % Math.max(1, media.length))

  return <div className="modal-backdrop dark-backdrop" onMouseDown={onClose}><div className="preview-modal" onMouseDown={e=>e.stopPropagation()}><div className="preview-top"><div><strong>{projectName || 'Untitled'}</strong><span>PREVIEW · LOW RESOLUTION</span></div><button type="button" onClick={onClose} aria-label="Close preview"><X size={20}/></button></div><div className={`video-stage ${currentItem?.type === 'title' ? 'title-stage' : ''}`} style={currentItem?.type==='title'?{background:currentItem.frameBackground}:undefined}>{stageFailed ? <div className="stage-fallback"><ImageOff size={28}/><span>This file is empty or unreadable — remove or replace it.</span></div> : currentUrl ? (currentItem?.type === 'video' ? <CropSpriteVideo item={currentItem} key={`${currentItem.id}-${stageUsePreview ? 'preview' : 'orig'}`} className={hasCrop(currentItem) ? '' : playing ? 'slow-zoom' : ''} windowClassName={playing ? 'slow-zoom' : ''} src={currentUrl} style={stageLook.style} autoPlay={playing} muted playsInline onEnded={() => { if (playing) advance() }} onError={() => { if (!stageUsePreview && previewStageUrl !== origUrl) setStageUsePreview(true); else setStageFailed(true) }} /> : <img className={playing ? 'slow-zoom' : ''} style={{ ...(stageTurned ? undefined : rotationStyle(currentItem?.rotation)), ...stageLook.style }} src={stageLook.src} alt={currentItem?.name || 'Preview'} onError={() => setStageFailed(true)}/>) : null}{stageLook.vignette && <i className="look-vignette" style={stageLook.vignette}/>}<div className="stage-shade"/>{showCaption && currentItem && <FrameMotionPreview key={`${currentItem.id}-${current}`} item={currentItem} defaults={defaults} playing={playing} />}<span className="preview-eyebrow">{currentItem?.type === 'title' ? 'TITLE FRAME' : (projectName ? projectName.toUpperCase() : 'SLIDESHOW')}</span><button type="button" className="stage-play" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? <Pause size={25} fill="currentColor"/> : <Play size={25} fill="currentColor"/>}</button></div><div className="preview-controls"><button type="button" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>{playing ? <Pause size={17}/> : <Play size={17}/>}</button><span>{formatClock(timelineModel(media).starts[current] || 0)}</span><div className="scrubber"><i style={{width: `${media.length ? ((current + 1) / media.length * 100) : 0}%`}}/><b style={{left: `${media.length ? ((current + 1) / media.length * 100) : 0}%`}}/></div><span>{formatClock(timelineModel(media).total)}</span><Select value="720p"><option>360p</option><option>720p</option></Select></div><div className="preview-filmstrip">{media.map((m,i) => { const thumb = itemThumbUrl(m); return <button type="button" className={`${current === i ? 'active' : ''} ${m.type === 'title' ? 'title-clip' : ''}`} onClick={() => { setCurrent(i); setStageFailed(false) }} key={m.id} style={m.type==='title'?{background:m.frameBackground}:undefined}>{m.type === 'title' ? <span className="title-symbol">T</span> : <MediaThumb item={m} />}<span>{i+1}</span></button> })}</div><div className="preview-note"><Info size={14}/> Videos play to the end before the next picture. Preview approximates effects; the final render may differ slightly.<button type="button" className="btn dark" onClick={onClose}>Done</button></div></div></div>
}


function RenderQueue({ projectId,onBack }: { projectId:number|null,onBack: () => void }) {
  const [jobs,setJobs]=useState<any[]>([])
  const [rerendering,setRerendering]=useState<string|null>(null)
  const [rerenderNote,setRerenderNote]=useState<string|null>(null)
  useEffect(()=>{let active=true;const load=()=>fetch(`/api/jobs${projectId?`?project_id=${projectId}`:''}`).then(r=>r.ok?r.json():[]).then(x=>active&&setJobs(x)).catch(()=>{});load();const timer=setInterval(load,2000);return()=>{active=false;clearInterval(timer)}},[projectId])
  const stopJob = (id: string) => { void fetch(`/api/jobs/${id}/cancel`, { method: 'POST' }) }
  // A finished row whose file vanished (pruned proxy preview, cleared output)
  // offers a re-render instead of a dead link. Overwrite stays opt-in: the
  // 409 "output exists" answer sends the user to the editor, where the
  // acknowledgement dialog lives.
  const rerenderJob = async (job:any) => {
    setRerendering(job.id); setRerenderNote(null)
    try{
      const response=await fetch(`/api/projects/${job.project_id}/jobs`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:job.kind,overwrite:false})})
      if(response.status===409)setRerenderNote('The output file exists again — open the editor to overwrite it')
      else if(!response.ok)setRerenderNote(`Could not re-render: ${(await response.text()).slice(0,140)}`)
      else setRerenderNote(job.kind==='preview'?'Preview queued — it appears below while it runs':'Render queued — it appears below while it runs')
    }catch{setRerenderNote('Could not reach the backend')}
    finally{setRerendering(null)}
  }
  const live = (status: string) => ['queued', 'running', 'cancelling'].includes(status)
  return <main className="queue-page"><div className="project-heading"><div><div className="eyebrow">ACTIVITY</div><h1>Render queue</h1><p>FFmpeg jobs and diagnostic history persisted in SQLite.</p></div><button className="btn dark" onClick={onBack}><Plus size={16}/> Back to editor</button></div>{rerenderNote&&<div className="notice queue-note"><Info size={16}/><span>{rerenderNote}</span></div>}{jobs.length===0&&<div className="notice"><Info size={16}/><span>No render jobs yet. Save the project, then generate a preview or MP4.</span></div>}{jobs.map(job=>{
    const failed = job.status==='failed'
    const cancelled = job.status==='cancelled'
    return <section className={`panel queue-card ${failed?'is-failed':''} ${cancelled?'is-cancelled':''} ${live(job.status)?'is-live':''}`} key={job.id}>
      <div className="queue-thumb"><img src="/media/coast.jpg"/><span>{live(job.status)?<RefreshCw className="spin" size={15}/>:failed?<AlertTriangle size={15}/>:<Download size={15}/>}</span></div>
      <div><strong>{job.kind==='preview'?'Proxy preview':'MP4 render'} · {job.id.slice(0,8)}</strong><p>{job.stage} · {Math.round(job.progress)}%</p><small>{new Date(job.created_at).toLocaleString()}{job.size_bytes?` · ${formatFileSize(job.size_bytes)}`:''}</small>{job.error_message ? <pre className="queue-error" style={{whiteSpace:'pre-wrap',wordBreak:'break-word',margin:'4px 0 0',fontSize:'12px',lineHeight:'1.35'}}>{job.error_message}</pre> : null}</div>
      <span className={`status-pill ${job.status}`}>{failed||cancelled?<AlertTriangle size={13}/>:<Check size={13}/>} {job.status}</span>
      {live(job.status)
        ? <button type="button" className="btn soft stop-job" disabled={job.status==='cancelling'} onClick={()=>stopJob(job.id)}><Square size={13} fill="currentColor"/> {job.status==='cancelling'?'Stopping…':'Stop'}</button>
        : job.output_path
          ? job.fileAvailable===false
            ? <span className="queue-missing" title="The file was pruned or deleted — re-render to restore it"><AlertTriangle size={13}/> File missing <button type="button" className="btn soft" disabled={rerendering===job.id} onClick={()=>rerenderJob(job)}>{rerendering===job.id?<RefreshCw className="spin" size={13}/>:<RefreshCw size={13}/>} Re-render</button></span>
            : <a className="btn soft" href={`/api/jobs/${job.id}/file`} download title={job.size_bytes?`Download · ${formatFileSize(job.size_bytes)}`:'Download'}><Download size={15}/> Download</a>
          : <a className="btn soft" href={`/api/jobs/${job.id}/log`} target="_blank">View log</a>}
    </section>
  })}</main>
}

export default App
