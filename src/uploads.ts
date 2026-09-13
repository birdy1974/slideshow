// Uploads from the GUI device into the backend's uploads volume.
//
// One file per request, via XMLHttpRequest — fetch() cannot report upload
// progress, and progress (plus cancel) is the whole point for multi-GB movies
// on Wi-Fi. The backend answer mirrors the media browser's entry shape, so
// uploaded files flow into the storyline through the exact same code path as
// files picked from a mount. An optional relative folder keeps local uploads
// organized below the writable /uploads volume.

export type UploadItem = {
  id: number; name: string; total: number; sent: number
  status: 'uploading' | 'done' | 'error'; error?: string
}

// Mirrors /api/health → uploads: can the NAS volume take files at all?
export type UploadsStatus = { path: string; writable: boolean; reason: string | null; maxMb: number }

// Same allowlist as backend/app/uploads.py (IMAGE_EXTENSIONS | VIDEO_EXTENSIONS).
// Checked by extension because browsers leave `type` empty for many camera
// files (e.g. .mov/.mkv from a folder pick) — a folder pick also brings
// sidecars (.xmp, .aae, Thumbs.db) that must be dropped quietly.
const UPLOAD_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'])

export function isUploadableFile(file: File): boolean {
  if (file.name.startsWith('.')) return false // .DS_Store, ._resource forks
  const dot = file.name.lastIndexOf('.')
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  return UPLOAD_EXTENSIONS.has(ext)
}

export type UploadHandle = {
  promise: Promise<{ added: any[]; errors: { name: string; error: string }[] }>
  cancel: () => void
}

export function uploadFile(file: File, onProgress: (sent: number, total: number) => void, folder = ''): UploadHandle {
  const xhr = new XMLHttpRequest()
  const form = new FormData()
  form.append('files', file, file.name)
  if (folder) form.append('folder', folder)
  const promise = new Promise<{ added: any[]; errors: { name: string; error: string }[] }>((resolve, reject) => {
    xhr.open('POST', '/api/media/upload')
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(event.loaded, event.total) }
    xhr.onload = () => {
      let data: any = null
      try { data = JSON.parse(xhr.responseText) } catch { /* non-JSON error page */ }
      if (xhr.status >= 200 && xhr.status < 300 && data) resolve(data)
      else reject(new Error(data?.detail || `Upload failed (${xhr.status})`))
    }
    xhr.onerror = () => reject(new Error('Could not reach the backend'))
    xhr.onabort = () => reject(new Error('aborted'))
    xhr.send(form)
  })
  return { promise, cancel: () => xhr.abort() }
}
