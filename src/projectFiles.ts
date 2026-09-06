// Project files on the mounted volumes — the browser half of "Save project" and
// "Load project" as a file you pick a path and a name for.
//
// SQLite stays the working store (it restores the editor after a refresh and the
// render queue hangs off it); a project file is an extra, portable copy of the
// same snapshot. So saving writes the file *and* the database row, and loading a
// file puts it in the editor and stores it as a fresh row.
//
// `backend/app/project_files.py` is the other half. The constants below mirror
// it and the test suite reads this file to check they still agree.

/** The suffix this app writes; loading also accepts a plain `.json`. */
export const PROJECT_SUFFIX = '.slideshow.json'
/** `/photos`, `/videos` and `/music` are mounted `:ro` — only `/output` takes a save. */
export const WRITABLE_ROOTS = ['output'] as const
/** Mirrors MAX_NAME_LENGTH in both src/projectName.ts and the backend. */
export const MAX_NAME_LENGTH = 80

export type ProjectRoot = 'photos' | 'videos' | 'music' | 'output'
export const PROJECT_ROOTS: ProjectRoot[] = ['output', 'photos', 'videos', 'music']

/** One entry of `/api/media/browse?projects=true`. */
export type ProjectEntry = {
  name: string
  path: string
  relativePath: string
  kind: 'directory' | 'project' | 'image' | 'video' | 'audio'
  size: number
  empty?: boolean
  modified?: number
  accessible?: boolean
}

/** What the backend reports about a saved project file. */
export type ProjectFileInfo = {
  root: ProjectRoot
  path: string
  name: string
  projectName: string
  size: number
  modified: number
  items: number
  project: Record<string, unknown>
}

export type SavedProjectFile = {
  root: ProjectRoot
  path: string
  name: string
  folder: string
  size: number
  overwritten: boolean
  items: number
}

/** Thrown for the 409 the server answers when the file is already there. */
export class ProjectFileExists extends Error {
  constructor(readonly existingPath: string) {
    super(`${existingPath} already exists`)
    this.name = 'ProjectFileExists'
  }
}

const ILLEGAL = /[\\/:*?"<>|\u0000-\u001f]/g
const EDGE = /^[\s.-]+|[\s.-]+$/g

/**
 * The name a project file gets: the project name, minus anything a filesystem
 * objects to, plus `.slideshow.json`. The backend applies the same rules, so
 * what the picker shows is what lands on the NAS.
 */
export function projectFileName(name: string): string {
  let stem = projectFileStem(name)
  const lowered = stem.toLowerCase()
  if (lowered.endsWith(PROJECT_SUFFIX)) stem = stem.slice(0, -PROJECT_SUFFIX.length)
  else if (lowered.endsWith('.json')) stem = stem.slice(0, -'.json'.length)
  stem = stem.replace(EDGE, '')
  return `${stem || 'project'}${PROJECT_SUFFIX}`
}

/** `safe_stem()` in the backend, statement for statement. */
export function projectFileStem(name: unknown): string {
  const text = typeof name === 'string' ? name : ''
  // A pasted path keeps only its last component.
  const last = text.replace(/\\/g, '/').split('/').filter(Boolean).pop() ?? ''
  return last
    .replace(ILLEGAL, '-')
    .slice(0, MAX_NAME_LENGTH * 2)
    .replace(EDGE, '')
    .slice(0, MAX_NAME_LENGTH)
    .replace(EDGE, '')
}

export function isWritableRoot(root: ProjectRoot): boolean {
  return (WRITABLE_ROOTS as readonly string[]).includes(root)
}

/** The same error unwrap App.tsx uses; kept local so this module stays standalone. */
async function apiError(response: Response, fallback: string): Promise<string> {
  const text = await response.text()
  try {
    const parsed = JSON.parse(text)
    const detail = parsed?.detail
    if (typeof detail === 'string' && detail) return detail
    if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
    if (Array.isArray(detail)) {
      const messages = detail.map((entry: { msg?: string }) => entry?.msg).filter(Boolean)
      if (messages.length) return messages.join('; ')
    }
  } catch { /* not JSON — fall through to the raw text */ }
  return text || `${fallback} (${response.status})`
}

const query = (root: ProjectRoot, path: string) => `root=${root}&path=${path.split('/').filter(Boolean).map(encodeURIComponent).join('/')}`

/** Folders plus saved project files in one folder of one mount. */
export async function listProjectFolder(root: ProjectRoot, path: string): Promise<ProjectEntry[]> {
  const response = await fetch(`/api/media/browse?projects=true&${query(root, path)}`)
  if (!response.ok) throw new Error(await apiError(response, 'Could not open this folder'))
  const data = await response.json().catch(() => null)
  return Array.isArray(data?.entries) ? (data.entries as ProjectEntry[]) : []
}

/** Read one project file: where it lives, what it is called, and its snapshot. */
export async function readProjectFile(root: ProjectRoot, path: string): Promise<ProjectFileInfo> {
  const response = await fetch(`/api/project-files?${query(root, path)}`)
  if (!response.ok) throw new Error(await apiError(response, 'Could not read this project file'))
  return response.json() as Promise<ProjectFileInfo>
}

/**
 * Write the snapshot to `<root>/<folder>/<filename>`.
 *
 * Fails with `ProjectFileExists` when a file is already there and `overwrite`
 * was not set — the picker turns that into "Replace it?" rather than clobbering
 * something silently.
 */
export async function saveProjectFile(options: {
  root: ProjectRoot
  folder: string
  filename: string
  overwrite?: boolean
  project: Record<string, unknown>
}): Promise<SavedProjectFile> {
  const response = await fetch('/api/project-files', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      root: options.root,
      folder: options.folder,
      filename: options.filename,
      overwrite: !!options.overwrite,
      project: options.project,
    }),
  })
  if (response.status === 409) {
    const text = await response.text()
    let path = projectFileName(options.filename)
    try { const parsed = JSON.parse(text); if (parsed?.detail?.path) path = String(parsed.detail.path) } catch { /* keep the guess */ }
    throw new ProjectFileExists(path)
  }
  if (!response.ok) throw new Error(await apiError(response, 'Could not save the project file'))
  return response.json() as Promise<SavedProjectFile>
}

/** `1.4 MB` / `312 KB` — the picker shows how big a saved project is. */
export function formatProjectSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 KB'
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}
