// The "browse for a path and a filename" popup behind **Save project** and the
// second tab of **Load project**.
//
// One component, two modes:
//   · `save` — pick a folder on a writable mount, type a filename, write the
//     project snapshot there (and, in App.tsx, into SQLite as well).
//   · `load` — walk any of the four mounts and open a saved project file.
//
// The read-only mounts are shown but refuse a save, because that is the truth of
// the container: /photos, /videos and /music are mounted `:ro` in compose.yaml.
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, FileJson, FolderOpen, Info, RefreshCw, Save, X } from 'lucide-react'
import { FieldLabel } from './ui'
import {
  PROJECT_ROOTS, PROJECT_SUFFIX, ProjectFileExists, formatProjectSize, isWritableRoot,
  listProjectFolder, projectFileName, readProjectFile, saveProjectFile,
  type ProjectEntry, type ProjectFileInfo, type ProjectRoot, type SavedProjectFile,
} from './projectFiles'

type Notice = { kind: 'info' | 'warn' | 'error'; text: string } | null

const ROOT_HINT: Record<ProjectRoot, string> = {
  output: 'Writable — renders and project files live here',
  photos: 'Read-only mount — you can open a project from here, not save one',
  videos: 'Read-only mount — you can open a project from here, not save one',
  music: 'Read-only mount — you can open a project from here, not save one',
}

export function ProjectFilePanel({ mode, projectName, snapshot, initialRoot, initialFolder, onSaved, onLoaded, onClose, onSqliteOnly, sqliteLabel }: {
  mode: 'save' | 'load'
  /** Save mode: the name the filename field starts with. */
  projectName?: string
  /** Save mode: the snapshot to write, read at click time so it is never stale. */
  snapshot?: () => Record<string, unknown>
  initialRoot?: ProjectRoot
  initialFolder?: string
  onSaved?: (file: SavedProjectFile) => void
  onLoaded?: (file: ProjectFileInfo) => void
  onClose?: () => void
  /** Save mode: the "keep it in SQLite only" escape hatch in the footer. */
  onSqliteOnly?: () => void
  sqliteLabel?: string
}) {
  const [root, setRoot] = useState<ProjectRoot>(initialRoot ?? 'output')
  const [path, setPath] = useState(initialFolder ?? '')
  const [entries, setEntries] = useState<ProjectEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState<Notice>(null)
  const [error, setError] = useState('')
  // The field holds the stem; `.slideshow.json` is printed next to it, exactly
  // like the filename field in the Output pane. The popup mounts fresh every
  // time it opens, so this always starts from the current project name.
  const [stem, setStem] = useState(() => projectFileName(projectName ?? '').slice(0, -PROJECT_SUFFIX.length))
  const [busy, setBusy] = useState(false)
  const [reading, setReading] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(''); setNotice(null)
    listProjectFolder(root, path)
      .then(found => { if (!cancelled) setEntries(found) })
      .catch(cause => { if (!cancelled) { setEntries([]); setError(cause instanceof Error ? cause.message : 'Could not open this folder') } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [root, path])

  const folders = useMemo(() => entries.filter(entry => entry.kind === 'directory'), [entries])
  const files = useMemo(() => entries.filter(entry => entry.kind === 'project'), [entries])
  const target = projectFileName(stem)
  const destination = `/${root}${path ? `/${path}` : ''}/${target}`
  const exists = files.some(entry => entry.name.toLowerCase() === target.toLowerCase())
  const writable = isWritableRoot(root)

  const save = async (overwrite: boolean) => {
    if (!snapshot || busy) return
    setBusy(true); setNotice(null); setError('')
    try {
      const saved = await saveProjectFile({ root, folder: path, filename: target, overwrite, project: snapshot() })
      onSaved?.(saved)
    } catch (cause) {
      if (cause instanceof ProjectFileExists) {
        setNotice({ kind: 'warn', text: `${cause.existingPath} is already there — press Replace to overwrite it.` })
        return
      }
      setError(cause instanceof Error ? cause.message : 'Could not save the project file')
    } finally {
      setBusy(false)
    }
  }

  const open = async (entry: ProjectEntry) => {
    if (busy || reading) return
    setReading(entry.relativePath); setNotice(null); setError('')
    try {
      const file = await readProjectFile(root, entry.relativePath)
      onLoaded?.(file)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not read that project file')
    } finally {
      setReading('')
    }
  }

  const crumbs = path ? `/${root}/${path}` : `/${root}`

  return <>
    <div className="picker-body project-files">
      <div className="pf-roots">
        {PROJECT_ROOTS.map(entry => {
          const allowed = mode === 'load' || isWritableRoot(entry)
          return <button type="button" key={entry} className={`pf-root ${root === entry ? 'active' : ''}`} disabled={!allowed}
            title={allowed ? ROOT_HINT[entry] : ROOT_HINT[entry]}
            onClick={() => { setRoot(entry); setPath('') }}>
            <FolderOpen size={13}/> /{entry}{!isWritableRoot(entry) && <em>read-only</em>}
          </button>
        })}
      </div>

      <div className="breadcrumbs">
        <button disabled={!path} onClick={() => setPath(path.split('/').slice(0, -1).filter(Boolean).join('/'))}>← Parent</button>
        <span>{crumbs}</span>
        <button disabled={!path} onClick={() => setPath('')}>Volume root</button>
      </div>

      {loading && <div className="browser-info"><RefreshCw className="spin" size={15}/> Reading /{root}…</div>}
      {error && <div className="notice amber"><AlertTriangle size={15}/><span>{error}</span></div>}
      {notice && <div className={`notice ${notice.kind === 'error' ? 'red' : notice.kind === 'warn' ? 'amber' : ''}`}><Info size={15}/><span>{notice.text}</span></div>}

      <div className="file-grid">
        {folders.map(folder => <button type="button" className={`file-card ${folder.accessible === false ? 'inaccessible' : ''}`} key={folder.relativePath}
          title={folder.accessible === false ? `No permission to open “${folder.name}”` : `Open ${folder.name}`}
          onClick={() => {
            if (folder.accessible === false) { setError(`No permission to open “${folder.name}”.`); return }
            setPath(folder.relativePath)
          }}>
          <div className="server-file-icon"><FolderOpen size={30}/></div>
          <strong>{folder.name}</strong>
          <small>{folder.accessible === false ? 'No permission' : 'Folder'}</small>
        </button>)}
        {files.map(file => <button type="button" className={`file-card project-file ${reading === file.relativePath ? 'busy' : ''}`} key={file.relativePath}
          title={mode === 'load' ? `Load ${file.name}` : `Use the name ${file.name}`}
          onClick={() => {
            if (mode === 'load') { void open(file); return }
            // Save mode: clicking a file offers its name instead of clobbering it.
            setStem(file.name.toLowerCase().endsWith(PROJECT_SUFFIX)
              ? file.name.slice(0, -PROJECT_SUFFIX.length)
              : file.name.replace(/\.json$/i, ''))
            setNotice({ kind: 'warn', text: `${file.name} is already in this folder — saving replaces it.` })
          }}>
          <div className="server-file-icon project">{reading === file.relativePath ? <RefreshCw className="spin" size={26}/> : <FileJson size={30}/>}</div>
          <strong>{file.name.toLowerCase().endsWith(PROJECT_SUFFIX) ? file.name.slice(0, -PROJECT_SUFFIX.length) : file.name}</strong>
          <small>{file.empty ? '0 B — empty' : `${formatProjectSize(file.size)}${file.modified ? ` · ${new Date(file.modified * 1000).toLocaleDateString()}` : ''}`}</small>
        </button>)}
      </div>

      {!loading && !error && folders.length === 0 && files.length === 0 &&
        <div className="browser-info"><Info size={15}/> Nothing here yet. Folders you type into the filename are created when the project is saved.</div>}

      {mode === 'save' && <div className="pf-name">
        <FieldLabel hint={`${folders.length} folder${folders.length === 1 ? '' : 's'} · ${files.length} saved project${files.length === 1 ? '' : 's'}`}>Filename</FieldLabel>
        <div className="filename">
          <input value={stem} aria-label="Project filename"
            onChange={event => setStem(event.target.value.replace(/\.slideshow\.json$/i, '').replace(/\.json$/i, ''))}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); void save(exists) } }}
            placeholder={projectName || 'project'}/>
          <span>{PROJECT_SUFFIX}</span>
        </div>
        <em className="pf-dest"><Save size={11}/> {destination}</em>
        {!writable && <em className="pf-readonly"><AlertTriangle size={11}/> /{root} is mounted read-only — switch to /output to save</em>}
      </div>}

      <div className="browser-info">
        <Info size={15}/>
        {mode === 'save'
          ? <>The project is written to <b>{destination}</b> as plain JSON, and stays in the SQLite list as well. Folders in the name are created automatically; nothing on the read-only media mounts is ever touched.</>
          : <>Project files are read from any mount, including the read-only ones. Loading replaces the current editor contents and stores the project as a new SQLite row, so a refresh keeps it.</>}
      </div>
    </div>

    {mode === 'save' && <div className="modal-foot">
      <span>{sqliteLabel || 'The SQLite copy is what restores the editor after a refresh.'}</span>
      {onSqliteOnly && <button type="button" className="btn ghost" onClick={onSqliteOnly} disabled={busy}><Save size={14}/> Save in SQLite only</button>}
      <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
      <button type="button" className="btn dark" disabled={busy || !writable || !snapshot}
        title={writable ? `Write ${target}` : 'Choose the writable /output volume first'}
        onClick={() => void save(exists)}>
        {busy ? <RefreshCw className="spin" size={15}/> : exists ? <AlertTriangle size={15}/> : <Check size={15}/>} {busy ? 'Saving…' : exists ? 'Replace file' : 'Save file'}
      </button>
    </div>}
  </>
}

/** The save popup: **Save project** in the header opens this. */
export function ProjectFileBrowser(props: {
  projectName?: string
  snapshot?: () => Record<string, unknown>
  initialRoot?: ProjectRoot
  initialFolder?: string
  sqliteLabel?: string
  onSaved: (file: SavedProjectFile) => void
  onSqliteOnly?: () => void
  onClose: () => void
}) {
  return <div className="modal-backdrop" onMouseDown={props.onClose}>
    <div className="browser-modal folder-picker project-file-browser" onMouseDown={event => event.stopPropagation()}>
      <div className="modal-head">
        <div><span className="eyebrow">SAVE PROJECT FILE</span><h2>Choose folder and filename</h2></div>
        <button className="icon-button" onClick={props.onClose} aria-label="Close"><X size={19}/></button>
      </div>
      <ProjectFilePanel mode="save" {...props} />
    </div>
  </div>
}
