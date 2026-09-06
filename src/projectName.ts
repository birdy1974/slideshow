// The project name at the top of the editor and the filename in the Output pane
// are one name: type it in either field and both follow, so the MP4 is called
// what the project is called ("and vice versa").
//
// A project name is also the caption on the pictures, so it stays free text —
// accents, commas, ampersands, a colon, all fine. A filename cannot hold
// everything a title can, so the twin that reaches the disk goes through
// `safeFilename()`: the characters that are illegal on the NAS, on a Windows
// share and on macOS become a dash, edge dots, spaces and dashes are dropped
// (Windows removes them silently, which would make the saved file impossible to
// find), and the result is capped so the ".mp4" still fits in a path. A name
// that is already filename-safe comes out unchanged, character for character.
//
// The backend takes `Path(filename).stem` before writing, so a stray separator
// can never leave the output folder; this is about names that survive a copy to
// a phone, a share or a USB stick.

/** Characters no common filesystem accepts in a file name. */
const ILLEGAL = /[\\/:*?"<>|\u0000-\u001f]/g

/** Edge characters that are lost or hidden on some filesystems. */
const EDGE = /^[\s.\-]+|[\s.\-]+$/g

/** Long enough for any title, short enough to stay a friendly path. */
export const MAX_NAME_LENGTH = 80

/** Used when nothing usable is left of a name (an empty project name). */
export const FILENAME_FALLBACK = 'slideshow'

/**
 * The filesystem-safe twin of a project name. Empty in, empty out: callers that
 * need a file decide their own fallback, so a half-typed field never jumps to
 * "slideshow" while the user is still working on the name.
 */
export function safeFilename(name: unknown): string {
  const text = typeof name === 'string' ? name : name == null ? '' : String(name)
  // Only the illegal characters and the edges are touched: a name that is
  // already filename-safe comes out byte-identical, so both fields really do
  // show the same text.
  return text
    .replace(ILLEGAL, '-')
    .replace(EDGE, '')
    .slice(0, MAX_NAME_LENGTH)
    .replace(EDGE, '')
}

/**
 * Filenames this app invented itself, before the two fields were linked. A
 * loaded project that still carries one of these inherits its project name;
 * a filename the user typed themselves is left alone.
 */
const GENERATED = ['', 'slideshow', 'movie', 'untitled', 'portugal-summer']

export function isGeneratedFilename(value: unknown): boolean {
  const text = typeof value === 'string' ? value : value == null ? '' : String(value)
  return GENERATED.includes(safeFilename(text).toLowerCase())
}
