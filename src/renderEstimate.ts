// How long will the next render take?  Nobody can know for certain — it
// depends on the machine, the codec, the resolution and the media — so the
// answer is assembled from two sources:
//
//   1. a measurement: how long this machine's last render actually took per
//      second of finished slideshow, scaled to the resolution and encoder that
//      are selected now;
//   2. until such a measurement exists, a plain heuristic.
//
// While a job is running the same module turns the backend's progress reports
// into a countdown, which is far more trustworthy than either.

/** Encode cost of each resolution relative to 1080p. */
const RESOLUTION_COST: Record<string, number> = {
  'SD · 480p': 0.35,
  'HD · 720p': 0.6,
  'Full HD · 1080p': 1,
  '4K UHD · 2160p': 3.2,
}
/** Software encoding costs extra; hardware H.264 is the reference. */
const ENCODER_COST: Record<string, number> = {
  'Auto · Quick Sync': 1,
  'Intel Quick Sync': 1,
  'CPU · x264': 2.6,
}
/** Previews are always 640×360 · 24 fps · 2 Mbps, whatever the output settings say. */
const PREVIEW_COST = 0.18
/** Seconds of encoding per second of finished 1080p video on hardware H.264. */
const BASE_SECONDS_PER_OUTPUT_SECOND = 0.9
/** Probing, validation, concat and container write-back, once per job. */
const STARTUP_SECONDS = 8
/** Normalising one clip (decode, scale, pad) on top of its own runtime. */
const PER_ITEM_SECONDS = 1.2

const STORAGE_KEY = 'slideshow.renderRate.v1'
const MIN_RATE = 0.02
const MAX_RATE = 60

export type JobKind = 'render' | 'preview'

export type RenderRate = {
  /** Wall-clock seconds spent per second of finished slideshow. */
  secondsPerOutputSecond: number
  resolution: string
  encoder: string
  kind: JobKind
  wallSeconds: number
  timelineSeconds: number
  at: number
}

export function costFactor(resolution: string, encoder: string, kind: JobKind): number {
  if (kind === 'preview') return PREVIEW_COST
  return (RESOLUTION_COST[resolution] ?? 1) * (ENCODER_COST[encoder] ?? 1)
}

export function loadRenderRates(): Partial<Record<JobKind, RenderRate>> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    const out: Partial<Record<JobKind, RenderRate>> = {}
    for (const kind of ['render', 'preview'] as JobKind[]) {
      const rate = parsed?.[kind]
      if (rate && Number.isFinite(rate.secondsPerOutputSecond)) out[kind] = rate as RenderRate
    }
    return out
  } catch {
    return {}
  }
}

export function saveRenderRate(kind: JobKind, rate: RenderRate): Partial<Record<JobKind, RenderRate>> {
  const rates = { ...loadRenderRates(), [kind]: rate }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(rates))
  } catch {
    /* private mode, quota — an estimate we cannot remember is not fatal */
  }
  return rates
}

/**
 * Forget the learned rate: the button sits next to "Clear all" in the header.
 * Returns the remaining rates (an empty object).
 */
export function clearRenderRates(): Partial<Record<JobKind, RenderRate>> {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch { /* ignore */ }
  return {}
}

export type RenderEstimateInput = {
  timelineSeconds: number
  itemCount: number
  resolution: string
  encoder: string
  kind: JobKind
  rates?: Partial<Record<JobKind, RenderRate>> | null
}

export function estimateRenderSeconds({ timelineSeconds, itemCount, resolution, encoder, kind, rates }: RenderEstimateInput): number {
  const items = Math.max(0, Math.floor(itemCount))
  const length = Math.max(0, Number(timelineSeconds) || 0)
  const overhead = STARTUP_SECONDS + PER_ITEM_SECONDS * items
  const cost = costFactor(resolution, encoder, kind)
  const rate = rates?.[kind]
  if (!rate || !Number.isFinite(rate.secondsPerOutputSecond)) {
    return overhead + BASE_SECONDS_PER_OUTPUT_SECOND * cost * length
  }
  // Scale the remembered measurement from the settings it was taken under to
  // the ones selected now.
  const measuredUnder = costFactor(rate.resolution, rate.encoder, rate.kind) || 1
  const scaled = clamp(rate.secondsPerOutputSecond * (cost / measuredUnder), MIN_RATE, MAX_RATE)
  return overhead + scaled * length
}

/** "about 14 min", "under a minute", "about 1 h 5 min". */
export function formatEstimate(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return '—'
  if (seconds < 45) return 'under a minute'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `about ${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `about ${hours} h ${rest} min` : `about ${hours} h`
}

// ---------------------------------------------------------------------------
// Live countdown from the backend's progress reports
// ---------------------------------------------------------------------------

export type EtaSample = { progress: number; at: number; rate: number | null }

/**
 * Feed one progress report in and get the updated tracker back.  The rate is
 * an exponential moving average of progress points per second, so it follows
 * the current stage instead of averaging the whole run — the stages differ a
 * lot in speed.
 */
export function nextEtaSample(previous: EtaSample | null, progress: number, at: number): EtaSample {
  if (!previous) return { progress, at, rate: null }
  const seconds = (at - previous.at) / 1000
  const delta = progress - previous.progress
  let rate = previous.rate
  if (seconds >= 0.5 && delta > 0) {
    const instant = delta / seconds
    rate = rate === null ? instant : rate * 0.65 + instant * 0.35
  }
  return { progress, at, rate }
}

/**
 * Seconds left at `now`, or null while there is not yet enough signal to say.
 * The backend stamps `started_at` itself, so this stays right across a page
 * refresh; between polls the countdown simply keeps running down.
 */
export function remainingSeconds(sample: EtaSample | null, progress: number, now: number, startedAt: number | null): number | null {
  if (!sample || progress >= 100) return null
  let rate = sample.rate
  if ((rate === null || rate <= 0) && startedAt != null) {
    const elapsed = (now - startedAt) / 1000
    if (elapsed > 3 && progress > 1) rate = progress / elapsed
  }
  if (!rate || rate <= 0) return null
  const sinceSample = Math.max(0, (now - sample.at) / 1000)
  return Math.max(0, (100 - progress) / rate - sinceSample)
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))
