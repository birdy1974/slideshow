// Clock helpers shared by the timeline, the soundtrack editor and the movie
// editor. `m:ss` for labels, `m:ss.s` wherever a trim point needs precision.

export const formatClockPrecise = (seconds: number) => {
  const s = Math.max(0, Number(seconds) || 0)
  const m = Math.floor(s / 60); const rest = s - m * 60
  return `${m}:${rest < 10 ? '0' : ''}${rest.toFixed(1)}`
}

export const formatClock = (seconds: number) => {
  const total = Math.max(0, Math.floor(Number(seconds) || 0))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export function parseClock(value: string | number | undefined): number {
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
