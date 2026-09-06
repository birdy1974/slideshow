// The storyline item: one photo, movie, or generated text frame.
// Shared by App.tsx and the media/movie editors.
export type MediaItem = {
  id: number; name: string; path: string; src: string; type: 'image' | 'video' | 'title';
  duration: number; effect: string; transition: string; transitionTime: number;
  // Extended transition config for custom ffmpeg (xfade-easing): per-clip GL params, easing and reverse
  transitionParams?: Record<string, string | number>;
  transitionEasing?: string;
  transitionReverse?: number;
  text: string; textMode: 'overlay' | 'frame';
  // Per-slide opt-out: when false the caption is kept but not drawn on the picture.
  textEnabled?: boolean;
  textStart: number; textEnd: number; textEnter: string; textExit: string;
  textEnterDuration: number; textExitDuration: number;
  textX: number; textY: number; frameBackground: string;
  fontFamily?: string; fontSize?: number; fontColor?: string;
  // Videos can replace the soundtrack with their embedded audio.
  audioSource?: 'soundtrack' | 'original';
  textBold?: boolean; textItalic?: boolean; textUnderline?: boolean;
  // Photo orientation fix in whole quarter turns (0, 90, 180, 270, clockwise).
  // Applied in every thumbnail/lightbox and by the FFmpeg renderer.
  rotation?: number;
  // Text frames: optional second background colour reached via an xfade
  // transition that starts `frameTransitionStart` seconds into the frame and
  // lasts `frameTransitionTime` seconds. The caption stays fixed on top.
  frameBackground2?: string; frameTransition?: string; frameTransitionTime?: number; frameTransitionStart?: number;
  // Movies only: use just the [trimStart, trimEnd) section of the file instead
  // of the whole recording. Both are seconds in the source file; 0 / missing
  // means "from the start" / "to the end", which is what every project saved
  // before movie trimming existed stores. The renderer honours the same pair.
  trimStart?: number; trimEnd?: number;
  // Picture look (filters/effects chosen in the preview popup): a preset id
  // from registry/picture-filters.json, its intensity (0..1) and the manual
  // sliders stacked on top. Like `rotation` this never touches the source file
  // — src/pictureFilters.ts turns it into CSS for the editor and
  // backend/app/picture_filters.py turns the same numbers into FFmpeg filters.
  filter?: string; filterAmount?: number; filterAdjust?: Record<string, number>;
  // Cut & crop from the same popup: `rect` is the kept rectangle in fractions of
  // the turned picture (x/y top-left, w/h size), `degrees` straightens it
  // (−15..15, zoomed so no corner shows), `lasso` is a polygon — in fractions of
  // the cropped view — whose inside is replaced by a blurred copy, and `feather`
  // softens that edge. src/pictureCrop.ts turns these into canvas/CSS and
  // backend/app/picture_crop.py into FFmpeg filters; absent means "whole file".
  crop?: {
    rect?: { x: number; y: number; w: number; h: number } | null;
    degrees?: number | null;
    lasso?: [number, number][] | null;
    feather?: number | null;
  } | null;
}
