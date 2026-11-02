/**
 * Every picture the storyline already holds, in timeline order, as something you can
 * pick.
 *
 * The reason this is a list of *sources* rather than of files is the request it
 * answers: "for select photo as background, add option to select photo from overall
 * timeline". A background has to be a path the render can open, so the only thing a
 * picker may invent is a file's name — the path must come from somewhere real. Here
 * it comes from the slides themselves: a plain photo contributes its own file, a
 * collage contributes each of its photos, and a movie contributes nothing (a movie
 * is not a picture, and the media browser's background mode already refuses it).
 *
 * `movieRootAndPath` in `src/filmstrip.ts` reads the same two shapes of stored path
 * (a whole `/photos/a/b.jpg`, or a folder in `path` with the file in `name`), and the
 * rule is mirrored rather than imported for the same reason it is mirrored there:
 * this module imports nothing, so `backend/tests/timeline_photos.mjs` can run it
 * under node without a bundler to resolve mounts, React or the API client for it.
 */

export type TimelinePictureRoot = 'photos' | 'videos' | 'uploads'

/** The part of a `MediaItem` this list needs. Structural on purpose: the caller is
 *  App's own item type, and a test can hand in plain objects. */
export interface TimelinePictureSource {
  id?: number
  name?: string
  path?: string
  type?: string
  collage?: { photos?: { path?: string; name?: string }[] }
}

export interface TimelinePicture {
  /** Stable React key: the slide, the photo inside it, and the file. */
  key: string
  /** 1-based position on the timeline, for the label under a thumbnail. */
  slide: number
  /** 1-based number inside that slide's collage, or 0 for a slide's own picture. */
  photo: number
  name: string
  /** The whole path, mount first: `/photos/Holiday/x.jpg`. Not a detail — it is the
   *  shape `/api/media/list` emits for the very same file, the shape a storyline
   *  item's `path` holds, and the only shape `mounted_path` will resolve: a path
   *  that names no mount is refused with "Invalid … path" by the save endpoint, so
   *  a picker that stored the mount-relative part would break the slide it touched.
   *  `/api/media/file` wants the relative part instead, and `mediaFileUrl` strips
   *  the mount itself, so this value is right for both doors. */
  path: string
  rootName: TimelinePictureRoot
  /** What the row says under the name: where this picture came from. */
  note: string
}

/** The mount + mount-relative parts of a stored path, accepting both shapes an
 *  item's `path` has ever had (see the module comment). */
export function pictureRootAndPath (path: string, name: string): { root: TimelinePictureRoot; relative: string } {
  const full = String(path || '').replace(/\\/g, '/')
  const leaf = String(name || '').replace(/\\/g, '/')
  const last = full.split('/').filter(Boolean).pop() || ''
  const joined = last.includes('.') ? full : `${full.replace(/\/+$/, '')}/${leaf}`
  const normalized = joined.replace(/\/{2,}/g, '/')
  const root: TimelinePictureRoot = normalized.startsWith('/uploads') ? 'uploads'
    : normalized.startsWith('/videos') ? 'videos' : 'photos'
  const relative = normalized.replace(/^\/(photos|videos|uploads)\//, '').replace(/^\/+/, '')
  return { root, relative }
}

/** The path as the app stores it, from the parts `pictureRootAndPath` found. Every
 *  writer that puts a picture somewhere (`backgroundImage`, a replaced photo, a
 *  new slide) stores this shape, so a list built out of stored paths hands back
 *  exactly what a stored path looked like. */
export function mountPath (root: TimelinePictureRoot, relative: string): string {
  return `/${root}/${String(relative || '').replace(/^\/+/, '')}`
}

/** The file name a row should show, without ever inventing one: the item's own, or
 *  the last segment of its path. */
function pictureName (path: string, name: string): string {
  const given = String(name || '').trim()
  if (given) return given
  const parts = String(path || '').replace(/\\/g, '/').split('/').filter(Boolean)
  return parts[parts.length - 1] || 'picture'
}

/**
 * The pictures on the timeline, slide by slide. `media` is the storyline in order;
 * only images and collages contribute, and a slide whose collage holds twelve
 * photos contributes twelve entries — each still identified with the slide it came
 * from, because that is what a person recognises when they look for it.
 */
export function timelinePictures (media: TimelinePictureSource[] | undefined | null): TimelinePicture[] {
  const out: TimelinePicture[] = []
  const list = Array.isArray(media) ? media : []
  list.forEach((item, index) => {
    if (!item) return
    const slide = index + 1
    const photos = Array.isArray(item.collage?.photos) ? item.collage!.photos! : null
    if (photos && photos.length) {
      photos.forEach((photo, k) => {
        const raw = String(photo?.path || '')
        if (!raw) return
        const { root, relative } = pictureRootAndPath(raw, String(photo?.name || ''))
        const name = pictureName(raw, String(photo?.name || ''))
        out.push({
          key: `${item.id ?? index}:${k}:${raw}`,
          slide, photo: k + 1, name, path: mountPath(root, relative), rootName: root,
          note: `Slide ${slide} · photo ${k + 1} of ${photos.length}`,
        })
      })
      return
    }
    // A movie is not a picture; a title frame has no file; a slide with no path
    // (an upload still being written) has nothing the render could open.
    if (item.type === 'video' || item.type === 'title') return
    const raw = String(item.path || '')
    if (!raw) return
    const { root, relative } = pictureRootAndPath(raw, String(item.name || ''))
    out.push({
      key: `${item.id ?? index}:0:${raw}`,
      slide, photo: 0, name: pictureName(raw, String(item.name || '')),
      path: mountPath(root, relative), rootName: root, note: `Slide ${slide}`,
    })
  })
  return out
}

/** How many times the same file is already on the timeline. A background that is
 *  also one of the photos in front of it is usually a mistake worth naming, so the
 *  strip can say "used twice" rather than leaving it to be discovered in the MP4. */
export function pictureReuse (pictures: TimelinePicture[] | undefined | null): Record<string, number> {
  const counts: Record<string, number> = {}
  for (const p of pictures || []) {
    const key = `${p.rootName}|${p.path}`
    counts[key] = (counts[key] || 0) + 1
  }
  return counts
}
