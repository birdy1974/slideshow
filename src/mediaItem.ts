// The storyline item: one photo, movie, or generated text frame.
// Shared by App.tsx and the media/movie editors.
import type { TextFxLayer } from './textMotionCore'

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
  // Seconds inside the item's visible hold. The outgoing slide transition is
  // additional timeline time and is never part of this text window.
  textStart: number; textEnd: number;
  // Stacked text effects (docs/text-motion-references.md): an ordered list of
  // layers, each one effect of registry/text-motion.json (by id) plus its
  // overrides. The effect decides the lane (Enter / While shown / Exit);
  // layers combine. src/textMotionCore.ts previews them, backend/app/
  // text_motion.py renders the same numbers with libass.
  textFx?: TextFxLayer[];
  // v1 text animation (three single-effect slots). Items saved before the
  // stack existed carry these; src/textFx.ts migrateLegacyTextFx() turns them
  // into textFx when a project is opened and they are not written any more.
  textEnter?: string; textExit?: string;
  textEnterDuration?: number; textExitDuration?: number;
  textFxEnter?: string; textFxWhile?: string; textFxExit?: string;
  textFxWhileSpeed?: number; textFxParams?: Record<string, string>;
  textX: number; textY: number; frameBackground: string;
  fontFamily?: string; fontSize?: number; fontColor?: string;
  // Videos can replace the soundtrack with their embedded audio.
  audioSource?: 'soundtrack' | 'original';
  textBold?: boolean; textItalic?: boolean; textUnderline?: boolean;
  // Per-picture caption outline/shadow. Missing means inherit the project default,
  // which keeps legacy items rendering exactly as they did before per-item styles.
  textOutline?: boolean;
  // Photo orientation fix in whole quarter turns (0, 90, 180, 270, clockwise).
  // Applied in every thumbnail/lightbox and by the FFmpeg renderer.
  rotation?: number;
  // Per-slide Ken Burns settings (photos with a "Ken Burns · …" effect only).
  // kenBurnsZoom = strength as the zoom factor reached at the end of the hold
  // (default 1.12); kenBurnsX/Y = focus point of a zoom in percent of the
  // picture (default 50/50 = centre). Pans always travel edge to edge.
  // Mirrored by ken_burns_settings() in backend/app/renderer.py.
  kenBurnsZoom?: number; kenBurnsX?: number; kenBurnsY?: number;
  // Text motion path: when enabled the caption moves from start to end
  // location during its visible window (textStart → textEnd). The optional
  // path is a freehand polyline drawn by the user in percent coordinates
  // (0-100). For title frames textX/Y is the start; for picture captions
  // textX/Y is also the start unless textMoveFrom* is set. End position is
  // textMoveToX/Y. When textMovePath is present and has ≥2 points it is used
  // instead of the straight line. Predefined paths: straight, freehand,
  // circle, sine. Easing fades speed in/out.
  textMoveEnabled?: boolean;
  textMoveFromX?: number; textMoveFromY?: number;
  textMoveToX?: number; textMoveToY?: number;
  textMovePath?: [number, number][];
  textMovePathType?: 'straight' | 'freehand' | 'circle' | 'sine' | 'star' | 'diamond' | 'triangle' | 'polyline' | 'sine-vertical' | 'bounce';
  textMoveEasing?: 'linear' | 'ease-in' | 'ease-out' | 'ease-in-out' | 'smooth';
  textMoveCircleRadius?: number;
  textMoveCircleTurns?: number;
  textMoveSineAmplitude?: number;
  textMoveSineFrequency?: number;
  // Symbol paths: reuse circleRadius as size, add star inner ratio / rotation
  textMoveStarPoints?: number;
  textMoveStarInnerRatio?: number;
  textMoveSymbolRotation?: number;
  // Polyline / multi-point: stored in textMovePath, type 'polyline' enables waypoint editing
  // Sinus up/down: vertical sine wave over the hold
  textMoveSinusUpDownEnabled?: boolean;
  textMoveSinusAmplitude?: number;
  textMoveSinusFrequency?: number;
  // Bounce trajectory (parabolic arcs, moves from start to end while bouncing)
  textMoveBounceHeight?: number;
  textMoveBounceCount?: number;
  textMoveBounceDamping?: number;
  // Text scale (grow / shrink) animation over text window
  textScaleEnabled?: boolean;
  textScaleFrom?: number;
  textScaleTo?: number;
  // Text rotation (tilt) animation over text window, degrees clockwise.
  // The text turns around its own centre; 0 = flat. The MP4 draws the
  // caption on a transparent layer and rotates it, so it works with any
  // font size (drawtext itself cannot rotate glyphs).
  textRotateEnabled?: boolean;
  textRotateFrom?: number;
  textRotateTo?: number;
  // Seconds the tilt takes to travel from → to. Missing/0 = the whole text
  // window (the default); shorter finishes early and holds, longer is still
  // travelling when the caption leaves.
  textRotateSpeed?: number;
  // Text squash animation over text window. Factor 1 = normal, < 1 squashes
  // flat and wide (height shrinks, width compensates), > 1 stretches tall.
  textSquishEnabled?: boolean;
  textSquishFrom?: number;
  textSquishTo?: number;
  // Text colour transition from colour1 to colour2 over text window
  textColorAnimEnabled?: boolean;
  textColorFrom?: string;
  textColorTo?: string;
  // In-place bouncy text (vertical bounce at its position, decays with damping)
  textBouncyEnabled?: boolean;
  textBouncyHeight?: number;
  textBouncyBounces?: number;
  textBouncyDamping?: number;
  textBouncyFrequency?: number;
  // Text frame: centre the whole text block on its position and centre every line on the others — at 50/50 the text sits in the middle of the frame
  textCentered?: boolean;
  // Text frame: keep text steady (not moving) for X seconds at the end of the frame — movement finishes early and holds final position; total text window equals frame duration
  textSteadySeconds?: number;

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
