// Uploads from the GUI device into the backend's uploads volume.
//
// One file per request, via XMLHttpRequest — fetch() cannot report upload
// progress, and progress (plus cancel) is the whole point for multi-GB movies
// on Wi-Fi. The backend answer mirrors the media browser's entry shape, so
// uploaded files flow into the storyline through the exact same code path as
// files picked from a mount.

export type UploadItem = {
  id: number; name: string; total: number; sent: number
  status: 'uploading' | 'done' | 'error'; error?: string
}

export type UploadHandle = {
  promise: Promise<{ added: any[]; errors: { name: string; error: string }[] }>
  cancel: () => void
}

export function uploadFile(file: File, onProgress: (sent: number, total: number) => void): UploadHandle {
  const xhr = new XMLHttpRequest()
  const form = new FormData()
  form.append('files', file, file.name)
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
