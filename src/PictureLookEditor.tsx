// "Edit picture" popup for one photo or movie, stacked on top of the media
// lightbox exactly like the movie Cut/Crop editor is. Two tabs share one
// container: **Filters & effects** (this file) and **Cut & crop**
// (PictureCropEditor.tsx), so both edits preview on the same picture and both
// live in the same numbers-on-the-item model.
//
// Everything here is non-destructive. The source files on the read-only
// /photos and /videos mounts are never touched: the chosen look is stored as a
// preset id + intensity + a few slider values on the media item, the browser
// turns those into CSS (src/pictureFilters.ts) and the renderer turns the same
// numbers into FFmpeg filters (backend/app/picture_filters.py), so the MP4
// looks like the preview.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Crop as CropIcon, Info, RotateCcw, Sparkles, Wand2, X } from 'lucide-react'
import type { MediaItem } from './mediaItem'
import {
  ADJUST_CONTROLS, LOOK_GROUPS, LOOK_PRESETS, LOOK_RANGES, PIXELATE_DIVISOR,
  WARMTH_STEPS, cssFilter, hasLook, lookLabel, resolveLook, vignetteOverlayStyle,
  warmthFilterId, warmthMatrixValues, type LookAdjust, type LookAdjustKey, type LookPreset,
} from './pictureFilters'
import { cropLabel, hasCrop, normalizeRotation, type CropRect } from './pictureCrop'
import { useCroppedSource } from './usePictureCrop'
import { useLookProxies } from './usePictureLook'
import { CropSpriteVideo, PictureCropPanel } from './PictureCropEditor'

const round = (value: number, digits = 2) => Number(value.toFixed(digits))

/**
 * The SVG filter definitions the CSS `filter:` strings point at for warmth.
 *
 * Mounted once in the app root — not inside this editor — because thumbnails
 * all over the timeline reference `url(#look-warmth-N)` too, and a filter
 * reference that resolves to nothing makes browsers hide the element entirely.
 */
export function PictureLookDefs() {
  return <svg aria-hidden="true" className="look-defs" width={0} height={0} focusable="false">
    <defs>
      {WARMTH_STEPS.map(index => index === 0 ? null : (
        // sRGB, not the SVG default linearRGB: that is the space the CSS filter
        // functions and FFmpeg's colorchannelmixer both work in.
        <filter key={index} id={warmthFilterId(index)} colorInterpolationFilters="sRGB">
          <feColorMatrix type="matrix" values={warmthMatrixValues(index)} />
        </filter>
      ))}
    </defs>
  </svg>
}

/** One preset chip: a small copy of this very picture with the look applied. */
function LookChip({ preset, proxy, pixelProxy, active, onPick }: {
  preset: LookPreset; proxy: string; pixelProxy: string; active: boolean; onPick: () => void
}) {
  const params = resolveLook({ filter: preset.id, filterAmount: 1 })
  const filter = cssFilter(params)
  const vignette = params.vignette > 0.01 ? Math.min(1, params.vignette * 0.6) : 0
  const pixelated = params.pixelate > 0.001
  const src = pixelated ? (pixelProxy || proxy) : proxy
  const style = {
    ...(filter ? { filter } : {}),
    ...(pixelated ? { imageRendering: 'pixelated' as const } : {}),
  }
  return <button
    type="button"
    className={`look-chip ${active ? 'active' : ''}`}
    onClick={onPick}
    title={preset.hint || preset.label}
    aria-pressed={active}
  >
    <span className="look-chip-thumb">
      {src && <img src={src} alt="" style={style} />}
      {vignette > 0 && <i className="look-vignette" style={{ opacity: vignette }} />}
    </span>
    <span className="look-chip-name">{preset.label}</span>
  </button>
}

export function PictureLookEditor({ item, src, onChange, onClose, initialTab = 'filters', detectBars }: {
  item: MediaItem
  src: string
  onChange: (patch: Partial<MediaItem>) => void
  onClose: () => void
  /** Which tab the popup opens on (the lightbox has a button for each). */
  initialTab?: 'filters' | 'crop'
  /** Backend call that measures black bars; absent when it cannot work. */
  detectBars?: (item: MediaItem) => Promise<{ rect: CropRect; bars: boolean } | null>
}) {
  const [tab, setTab] = useState<'filters' | 'crop'>(initialTab)
  // Cancel puts back exactly what the item had when the popup opened — both tabs.
  const original = useRef({ filter: item.filter, filterAmount: item.filterAmount, filterAdjust: item.filterAdjust, crop: item.crop })
  const [compare, setCompare] = useState(false)

  const isMovie = item.type === 'video'
  const turn = normalizeRotation(item.rotation)
  const turnStyle = turn ? { rotate: `${turn}deg` } : {}

  // The crop happens first (that is the renderer's order too), so the filter
  // chips and stage work on a cropped copy. A movie's *stage* keeps playing the
  // real file through a CSS sprite; the copy only feeds its chips.
  const cropped = useCroppedSource(src, item, isMovie ? 'result' : 'stage', isMovie)
  const baseSrc = cropped.ready ? cropped.src : src
  const baked = cropped.rotationApplied

  const params = useMemo(() => resolveLook(item), [item.filter, item.filterAmount, item.filterAdjust])  // eslint-disable-line react-hooks/exhaustive-deps
  const filter = cssFilter(params)
  const vignette = vignetteOverlayStyle(item)
  const pixelated = params.pixelate > 0.001
  const active = hasLook(item)

  // One downscaled copy of this clip feeds every preset chip (20 chips on a
  // 24 MP JPEG would be wasted decode work), plus the 120 px copy that
  // nearest-neighbour upscaling turns into the Pixelate preview. The quarter
  // turn is baked in unless the crop copy already carried it.
  const { chip: proxy, pixel: pixelProxy } = useLookProxies(baseSrc, 320, PIXELATE_DIVISOR, isMovie, baked ? 0 : turn)

  // Hold Space to compare with the original (the same shortcut
  // photofilters.com uses). Sliders keep working while it is held. Movies keep
  // Space for their own player — they get the compare button instead.
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      if (tab !== 'filters' || isMovie || event.code !== 'Space' || event.repeat) return
      const target = event.target as HTMLElement | null
      // Space still belongs to whatever has focus: a slider, a text field or a
      // preset chip (buttons activate on Space) must not trigger the compare.
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable || target.closest('button'))) return
      event.preventDefault()
      setCompare(true)
    }
    const up = (event: KeyboardEvent) => { if (event.code === 'Space') setCompare(false) }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') cancel() }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('keydown', escape)
    return () => { window.removeEventListener('keydown', down); window.removeEventListener('keyup', up); window.removeEventListener('keydown', escape) }
  })  // eslint-disable-line react-hooks/exhaustive-deps

  // A fresh preset always starts at full intensity; the sliders stay as they were.
  const pick = (id: string) => onChange({ filter: id, filterAmount: 1 })

  const adjustValue = (key: LookAdjustKey): number => {
    if (key === 'amount') { const value = Number(item.filterAmount); return Number.isFinite(value) ? value : 1 }
    const stored = Number((item.filterAdjust as LookAdjust | undefined)?.[key])
    if (Number.isFinite(stored)) return stored
    const range = LOOK_RANGES[key]
    return range ? range.identity : key === 'brightness' || key === 'contrast' || key === 'saturation' ? 1 : 0
  }

  const setAdjust = (key: LookAdjustKey, value: number) => {
    const range = LOOK_RANGES[key]
    const next = range ? Math.min(range.max, Math.max(range.min, value)) : value
    if (key === 'amount') { onChange({ filterAmount: round(next, 2) }); return }
    // Only keep the sliders that actually differ from neutral, so a project
    // that was never adjusted stays byte-for-byte what it was.
    const identity = range ? range.identity : 1
    const adjust: Record<string, number> = { ...(item.filterAdjust || {}) }
    if (Math.abs(next - identity) < (range?.step || 0.01) / 2) delete adjust[key]
    else adjust[key] = round(next, 3)
    onChange({ filterAdjust: adjust })
  }

  const cancel = () => { onChange(original.current); onClose() }
  const resetTab = () => {
    if (tab === 'crop') onChange({ crop: undefined })
    else onChange({ filter: 'none', filterAmount: 1, filterAdjust: {} })
  }

  const lookStyle = compare ? {} : {
    ...(filter ? { filter } : {}),
    ...(pixelated ? { imageRendering: 'pixelated' as const } : {}),
  }
  // Pictures: prefer the blocky proxy for Pixelate, else the cropped copy, else
  // the file itself (which then still needs its quarter turn from CSS).
  const stageSrc = pixelated && !compare && !isMovie ? (pixelProxy || proxy || baseSrc) : baseSrc
  const stageStyle = { ...(stageSrc === src && !baked ? turnStyle : {}), ...lookStyle }

  return <div className="modal-backdrop dark-backdrop look-backdrop" onMouseDown={cancel}>
    <div className="soundtrack-editor look-editor" onMouseDown={e => e.stopPropagation()}>
      <div className="preview-top">
        <div><strong>{item.name}</strong><span>{isMovie ? 'MOVIE' : 'PICTURE'} EDITOR</span></div>
        <div className="editor-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'filters'} className={tab === 'filters' ? 'active' : ''}
            onClick={() => setTab('filters')}><Sparkles size={13}/> Filters &amp; effects</button>
          <button type="button" role="tab" aria-selected={tab === 'crop'} className={tab === 'crop' ? 'active' : ''}
            onClick={() => setTab('crop')}><CropIcon size={13}/> Cut &amp; crop</button>
        </div>
        <div className="lightbox-actions">
          <em className="lightbox-position">{tab === 'crop' ? (hasCrop(item) ? cropLabel(item) : 'Whole picture') : (active ? lookLabel(item) : 'Original')}</em>
          <button type="button" onClick={resetTab} title={tab === 'crop' ? 'Use the whole picture again' : 'Back to the untouched original'}
            aria-label={tab === 'crop' ? 'Reset crop' : 'Reset look'} disabled={tab === 'crop' ? !hasCrop(item) : !active}><RotateCcw size={17} /></button>
          <button type="button" onClick={cancel} aria-label="Close editor"><X size={20} /></button>
        </div>
      </div>

      {tab === 'crop'
        ? <PictureCropPanel item={item} src={src} onChange={onChange} onCancel={cancel} onClose={onClose} onReset={() => onChange({ crop: undefined })} detectBars={detectBars} />
        : <>
      <div className="editor-body look-body">
        <div className="look-stage">
          {isMovie
            // A movie keeps playing its real file, so its crop is a CSS sprite.
            ? <CropSpriteVideo item={item} className="look-video" src={src} style={lookStyle.filter ? { filter: lookStyle.filter } : undefined}
                controls muted playsInline preload="metadata" />
            : <img src={stageSrc} alt={item.name} style={stageStyle} draggable={false} />}
          {!compare && vignette && <i className="look-vignette" style={vignette} />}
          {compare && <em className="look-compare">ORIGINAL</em>}
          <button type="button" className="look-compare-toggle" onClick={() => setCompare(value => !value)}
            title={compare ? 'Put the filter back' : 'Look at the untouched original'}>{compare ? 'Show filter' : 'Show original'}</button>
          <span className="look-hint">{isMovie ? <>Space belongs to the player — use <b>Show original</b> to compare</> : <>Hold <b>Space</b> to compare with the original</>}</span>
        </div>

        <div className="look-panel">
          <div className="look-presets">
            {LOOK_GROUPS.map(group => <div className="look-group" key={group}>
              <strong>{group}</strong>
              <div className="look-grid">
                {LOOK_PRESETS.filter(preset => preset.group === group).map(preset => (
                  <LookChip
                    key={preset.id}
                    preset={preset}
                    proxy={proxy || baseSrc}
                    pixelProxy={pixelProxy}
                    active={(item.filter || 'none') === preset.id}
                    onPick={() => pick(preset.id)}
                  />
                ))}
              </div>
            </div>)}
          </div>

          <div className="look-adjust">
            <strong><Wand2 size={13} /> Adjustments <small>stack on top of the preset</small></strong>
            {ADJUST_CONTROLS.map(({ key, label }) => {
              const range = LOOK_RANGES[key] || { min: 0, max: 1, step: 0.05, identity: 1 }
              const value = adjustValue(key)
              const percent = key === 'amount' || key === 'vignette'
              return <label className="look-slider" key={key} title={`${label} · double-click to reset`}>
                <span>{label}</span>
                <input
                  className="range"
                  type="range"
                  min={range.min}
                  max={range.max}
                  step={range.step}
                  value={value}
                  aria-label={label}
                  onChange={e => setAdjust(key, Number(e.target.value))}
                  onDoubleClick={() => setAdjust(key, range.identity)}
                />
                <b>{percent ? `${Math.round(value * 100)} %` : round(value, 2).toFixed(2)}</b>
              </label>
            })}
          </div>
        </div>
        <p className="look-note"><Info size={13} /> Filters never modify the file on the NAS: they are stored with the project and applied by FFmpeg at render time, so the MP4 matches this preview. Soft focus, sharpen and vignette are approximated on screen — the render uses the real thing. A movie keeps playing its real file on the stage, so Pixelate shows in its chip and in the render rather than on the stage.</p>
      </div>

      <div className="modal-foot">
        <span><Sparkles size={12} /> {active ? `${lookLabel(item)} — applied to this ${isMovie ? 'movie' : 'picture'} only` : 'No filter — the original file is used as it is'}</span>
        <button className="btn ghost" onClick={resetTab} disabled={!active}>Reset</button>
        <button className="btn ghost" onClick={cancel}>Cancel</button>
        <button className="btn dark" onClick={onClose}>Done</button>
      </div>
        </>}
    </div>
  </div>
}
