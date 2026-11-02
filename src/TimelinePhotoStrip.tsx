/**
 * Every picture the storyline already holds, offered as a background without a dialog.
 *
 * Two ways to reach the same list, because they answer two different moods: the media
 * browser grew a "timeline" location for someone who wants to search and preview, and
 * this strip is for the much more common case — "one of the photos I already picked".
 * Both write the very same value the browser's own *Use as background* writes: the
 * whole path of a real file, mount first (`/photos/Holiday/x.jpg`), which is what
 * the save endpoint resolves and the render opens. A project saved while a picker
 * stored only the mount-relative part is still recognised here — the mark below
 * compares the parts after the mount — so the strip cannot show a picture as unused
 * while the slide is already using it. `src/timelinePhotos.ts` builds the list, and
 * a test holds its rules.
 */
import type { TimelinePicture } from './timelinePhotos'
import { pictureReuse } from './timelinePhotos'

/** Where a picture's thumbnail comes from. App owns the media URLs — mount, path
 *  encoding, the rotation the backend applies — so the strip asks for one instead
 *  of keeping a second copy of those rules and drifting from the browser beside it. */
export type TimelinePictureUrl = (picture: TimelinePicture) => string

/** The path without the leading mount, which is how a stored background has been
 *  written in more than one shape and how `/api/media/*` wants it anyway. */
function afterMount(value: string): string {
  return String(value || '').replace(/\\/g, '/').replace(/^\/(photos|videos|music|uploads)\//, '').replace(/^\/+/, '')
}

export interface TimelinePhotoStripProps {
  pictures: TimelinePicture[]
  /** The path currently in use, so the strip shows which picture it already is. */
  current?: string
  onPick: (picture: TimelinePicture) => void
  /** Offered when the popup can also open the browser: the strip is not the only way. */
  onBrowse?: () => void
  /** What the row is for, in the popup's own words. */
  label?: string
  urlOf: TimelinePictureUrl
}

export function TimelinePhotoStrip ({ pictures, current, onPick, onBrowse, label = 'From the timeline', urlOf }: TimelinePhotoStripProps) {
  const reuse = pictureReuse(pictures)
  return (
    <div className="timeline-photos-wrap">
      <div className="timeline-photos-head">
        <span>{label}</span>
        <small>
          {pictures.length
            ? `Every picture already on the storyline — click one to lay it behind the slide (${pictures.length})`
            : 'Nothing on the timeline yet to use — pick a file from the library instead'}
        </small>
        {onBrowse && pictures.length > 0 && <button type="button" className="btn ghost small" onClick={onBrowse}>Browse…</button>}
      </div>
      {pictures.length > 0 && <div className="timeline-photos" role="group" aria-label={`${label}: pictures already on the timeline`}>
        {pictures.map(picture => {
          const used = reuse[`${picture.rootName}|${picture.path}`] || 0
          // Either shape counts as "this one": what the pickers store now, and the
          // mount-relative part a project saved before they did.
          const on = !!current && (current === picture.path || afterMount(current) === afterMount(picture.path))
          return (
            <button type="button" key={picture.key} className={on ? 'on' : ''}
                    title={`${picture.name} · ${picture.note}${used > 1 ? ` · on the timeline ${used} times` : ''}${on ? ' · this one is already the background' : ' — use it as the background'}`}
                    onClick={() => onPick(picture)}>
              <i><img src={urlOf(picture)} alt="" loading="lazy" draggable={false}/></i>
              <b>{picture.name}</b>
              <span>{picture.photo ? `${picture.slide}.${picture.photo}` : String(picture.slide)}{used > 1 ? ` ×${used}` : ''}</span>
            </button>
          )
        })}
      </div>}
    </div>
  )
}
