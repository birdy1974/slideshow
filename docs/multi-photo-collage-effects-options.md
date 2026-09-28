# Multi-photo / overlapping-picture effects — research & options

Research round (2026-09-26, second pass). The two URLs that started this
(acchou animate.css cheat sheet, codewithfaraz swinging gallery) were treated
as examples; this doc is the wider internet sweep of the whole "multiple
pictures on screen / overlapping pictures" class. **Options and advice only —
no code changed.** Effort legend as before: S = hours, M = days, L = weeks.

Companion doc: `more-transitions-and-text-effects-options.md` (transitions,
text effects, motion paths — largely implemented as of 2026-09-26).

---

## 1. The format catalog (what the internet actually does)

Ordered roughly by current popularity / demand signal.

| # | Family | What it looks like | Demand evidence |
| --- | --- | --- | --- |
| 1 | **Photo dump / photo pile** | Photos drop in one-by-one from the top (or pop in) at random positions and tilts, building a loose pile; final "reveal" photo lands on the beat drop. Often beat-synced. | The TikTok/IG trend: #photodump 359M+ views; CapCut templates with 18–52 photo slots ("2025 recap dump"); TikTok shipped a native "multi picture frames" feature (2025). CapCut tutorial "pile of photos reveal" (lesson 375). |
| 2 | **Grid / mosaic pop-in** | Photos in a regular grid; tiles appear staggered or beat-synced; masonry/Pinterest variant with mixed sizes. | Videohive best-sellers ("Photo Grid" 690 sales, "Mosaic Photo Gallery", "Grid Gallery"); CapCut "grid video dump" templates; Remotion `gallery-grid`, `masonry-gallery` (MIT). |
| 3 | **Polaroid / instant stack** | Overlapping polaroids (white mat, caption strip, drop shadow) at seeded random tilts; drop-in, swing, or deck-cycle through the pile. | Remotion `photo-stack`, `polaroid-frame` (MIT); transloadit `polaroid-stack` (seeded, deterministic); the codewithfaraz/Wakana K. swinging pinned gallery; countless Videohive polaroid galleries; CSS-tricks "infinite polaroid slider". |
| 4 | **Corkboard / scatter with pins & tape** | Photos stuck on a board with drawing pins or washi tape, slight spring overshoot on landing, gentle idle sway; handwriting captions. | Pinterest/scrabook aesthetic; AE "scattered photos" templates; the codewithfaraz gallery is exactly this (pin at top, `transform-origin: center 0.22rem`, ±angle swing, odd/even alternate-reverse, every 7th a different duration). |
| 5 | **Wall gallery / photo wall** | A virtual camera pans across a wall of framed photos, then zooms into one frame, which grows to become the full-screen slide. Bridges collage → single photo. | Videohive "Wall Gallery" / "Wall Slideshow" free favourites (wedding videography staple). |
| 6 | **Multiscreen / split screen / PiP** | 2×2 or N-panel layouts; panels slide in to meet, spotlight wipes between them; picture-in-picture. | Videohive "Multiscreen Spotlight", "Fashion Split Screen", "Grid Multiscreen"; Remotion `split-screen`, `picture-in-picture`. |
| 7 | **Carousel / filmstrip** | A horizontal (or vertical) band of photos scrolls past like a conveyor / filmstrip with sprocket holes; centre photo in focus. | Remotion `image-carousel`; Videohive "Digital Film Strip" (18 placeholders); classic Photo Booth strip. |
| 8 | **3D forms — cube, columns, fan/deck** | Photos on the faces of a rotating 3D cube; vertical columns spinning; a hand of cards that fans out with spring physics. | BlogGIF 3D photo cube; Videohive "Spinning Columns"; GSAP "Elastic Stack Cards" (fan-out with springs). |
| 9 | **Paper: origami / fold / book** | Photos fold like paper (Apple's Origami theme), page-curl, book flip. | Apple Photos themes (Classic, Ken Burns, Magazine, **Origami**); we already own gl_Fold / gl_BookFlip / gl_GridFlip / gl_SimplePageCurl as 2-input transitions. |
| 10 | **Infinite zoom / telescope** | Camera zooms endlessly into a photo, which contains the next photo, and so on (Droste). | Videohive "Modern Zoom Telescopic Slideshow". |
| 11 | **Ken Burns over collage** | A static collage where each cell has its own subtle pan/zoom — the "Memories" look. | Apple Photos Memories / Google Photos: the collage is the slide, motion lives inside each cell. |

Motion vocabulary the web community converges on (useful for us regardless
of layout): staggered entrances, `forward/reverse/center/random` orders,
spring/overshoot settles, damped idle sway, depth cues (arriving cards push
previous ones back: scale down + dim + tilt, per GSAP "Scroll Stack Cards"),
and audio-cue timing ("the transition lands on the beat" — per trend
analyses, audio sync is the #1 reason these formats go viral).

---

## 2. Gap analysis — what we have, what we are missing

**We have (a lot, and it transfers):**
- 191 two-slide transitions (58 native xfade + 133 GL, incl. fold/flip/curl
  for the paper look) — but **every one of them blends exactly two
  neighbouring slides**.
- Per-slide Ken Burns (pan/zoom/focus point, clamped strength).
- A rich, twin-verified choreography vocabulary in the text engines:
  stagger, order (forward/reverse/center/edges/random), loops (loop/
  pingpong/once), easing incl. elastic/back/bounce, a physics generator,
  deterministic per-unit `rand:[a,b]` with seeds, and motion paths
  (17 shapes incl. spiral/pendulum, rotate-along-path).
- Generated "Text frame" slides (solid background + optional colour change +
  libass text overlay) — the natural template for a new composite slide kind.
- EBU R128 loudness measurement endpoint (audio analysis exists, though not
  yet beat/onset detection).
- The discipline that matters most: every visual feature is data + two
  agreeing engines (preview TS / render Python) — proven three times over
  (transitions, text effects, motion paths).

**We are missing (the entire class):**
1. **No slide can show more than one photo.** `MediaItem` carries a single
   `path`; the renderer composites one media stream + optional text overlay
   per slide. Every format in §1 needs N photos on screen simultaneously.
2. **No photo mats/frames/shadows/pins** — polaroid borders, tape, pin, drop
   shadow are not renderable today (text boxes aside).
3. **No per-photo transform animation in the renderer** (rotate/scale/pos
   over time for image streams).
4. **No collage layouts** (stack, grid, masonry, scatter, filmstrip, fan) —
   and no seeded-deterministic placement so preview and render agree.
5. **No virtual camera** (pan/zoom across a composed scene — needed for wall
   gallery and telescope).
6. **No beat/onset detection** for beat-synced pop-ins (loudness ≠ beats).

---

## 3. How it can be integrated

### 3a. The core enabler: a `collage` slide type (Option B from the earlier advice)

Extend the proven "generated frame" concept rather than inventing a new
pipeline stage:

```
item: { type: 'collage', duration, transition, transitionTime,
        photos: [{ path, delay?, size? }, …]  // references into the photo library; delay = seconds
                                           // after the PREVIOUS photo appears (photo 0: after the
                                           // hold starts). Missing = auto stagger.
                                           // size = 0.5..1.5 multiplier on the layout's base mat
                                           // width (missing / junk → 1); the mat grows around its
                                           // anchor, it never moves. Editor stepper 50–150 %.
        layout: 'stack' | 'grid' | 'masonry' | 'scatter' | 'filmstrip' | 'fan',
        animation: 'drop' | 'pop' | 'swing' | 'deal' | 'none',
        background: '#hex' | 'none',
        backgroundImage: '/photos/x.jpg',   // optional library picture behind the photos
        backgroundBlur: 0..1,                // blur strength for that picture
        hold: number,                        // seconds the finished collage stays after the last photo
        seed: number,                        // deterministic placement — the transloadit lesson
        beatSync: boolean,                   // snap photo arrivals to the music's onsets
        beats: number[],                     // onset times mapped into the slide's hold clock
                                             // (the editor fetches /api/media/beats and stores the
                                             //  result — the render uses the stored list, no audio
                                             //  analysis at render time; several photos may share
                                             //  a beat: the "pile lands on the drop" reveal)
        depth: boolean,                      // push-back: photos already landed shrink + dim as
                                             // each new one lands (drop / pop)
        caption fields… }                    // existing text stack works on top, unchanged
        // duration is DERIVED: Σ delays + entrance length + hold (implemented;
        //   the editor recomputes the slide duration on every timing change)
```

A collage behaves like any other slide in the timeline: normal 2-input
transitions in and out (fade, push, even gl_Fold for a paper exit). The
multi-photo choreography happens *inside* the slide, so the transition
pipeline stays untouched — this is the key architectural decision.

### 3b. Rendering (FFmpeg, no new dependencies)

Per photo a chain like (all stock filters, confirmed viable via
StackOverflow 49733467 / 42770315 / 24330):

```
[i:v] scale → pad (polaroid mat) → format=rgba
      → rotate='angle_expr(t)'            // time-expression rotation, transparent fill
      → fade=in:st=…:d=…
[bgr][pi] overlay=x='x_expr(t)':y='y_expr(t)':enable='between(t,t0,∞)'
```

- Drop shadows: pre-compose a soft-shadow PNG per mat once per render (Pillow
  is already in the venv) instead of fighting ffmpeg blur chains.
- Expressions are generated from the same seeded layout/animation math as the
  preview — the twin-engine rule applied to photos (a new small
  `collageCore` module: layout + animation curves, TS + Python, like
  `textMotionCore`).
- Virtual camera (wall gallery): one `zoompan` over the finished composite —
  cheap, since it operates on the composed stream. **As built** (see
  `camera_filter()` in `backend/app/collage.py`): the scene is supersampled
  first (`scale=iw*S:ih*S`, S = 4 at 720p, 3 at 1080p, 2 at 1440p, 1 at 4K)
  and converted to 4:4:4, then `zoompan` (driven by its input time `it`)
  follows `camera_state()`'s smoothstep — zoompan snaps its window to whole
  input pixels (even-numbered ones on 4:2:0), which is what made the first
  version bumpy; on the S× grid it moves in 1/S-pixel steps. Do **not** use
  `crop` with a time-dependent `w`/`h` for this: crop evaluates its size once,
  at init, with `t = NaN`, so such a camera silently renders a static frame
  (`tests/test_collage_camera_ffmpeg.py` renders the real chain and checks
  both the motion and its smoothness).
- Perf: 6–10 photos × (scale+rotate+overlay) per collage slide is well within
  our current per-slide segment encoding budget.
- 3D cube/columns and true infinite zoom are **L** (need GL or manual
  perspective math) — defer.

### 3c. Preview (the easy half)

The React stage already composes absolutely-positioned DOM layers with CSS
transforms for the text scenes. Photos in a collage are the same story:
`<img>` + `transform: translate/rotate/scale`, `transform-origin` for
pins/swings, CSS `box-shadow`/`filter` for mats and shadows. Same seeded
layout math ⇒ preview matches the MP4 (no libass involved).

### 3d. Choreography vocabulary — mostly reuse

Map the web patterns onto concepts our text engines already prove out:

| Web pattern | Our equivalent concept |
| --- | --- |
| Staggered drop-in / pop-in | `stagger` + `order` (forward/center/random) |
| Spring settle / overshoot | `outBack`/`outElastic` easing (already in EASE maps) |
| Damped idle swing | physics generator (damped oscillation) or sine + decay envelope |
| Seeded random tilt/position | deterministic `hash01(seed, index)` — exactly the text-effect trick |
| Arriving card pushes the pile back | per-photo `depth`-style scale/dim track keyed to arrival time |
| Beat-synced pops | new: onset list from the music track → stagger timings |
| Swing from a pin | `transform-origin: top` (CSS) / rotate around anchor (ffmpeg `rotate` about center + offset compensation) |

### 3e. Beat sync (small new capability, high trend value)

Trend analyses are explicit: "the audio cues the transitions perfectly" is
what makes the photo-dump format work. We already decode audio for loudness;
onset detection (energy-based, in Python — no new deps) gives beat times; a
collage with `beatSync: on` snaps photo arrivals to the next onset after its
nominal stagger time. Also useful later for text effects (word-pop on beat).

---

## 4. Phasing & effort

> **Status — Phase 1 implemented** (branch `arena/01a0de4d-slideshow`): collage
> slides with stack/grid/scatter, drop/pop/swing/none, photo shape 4:3 /
> square / 3:4, seeded determinism, polaroid mats + soft shadows, per-photo
> FFmpeg expression graphs, the TS/Python twin
> (`src/collageCore.ts` ↔ `backend/app/collage.py`, cross-checked at 1e-9 by
> `backend/tests/test_collage_twin.py`), the editor with photo picker +
> DOM-transform preview, and captions riding the existing text stack.
> **Timing update:** per-photo appearance waits (`delay`) with a hold after
> the last photo — the slide duration is derived from them
> (`collageDuration`, auto-synced by the editor). **Size update:** each
> photo can scale its mat (50–150 %, `photos[].size`) — the anchor stays
> put. **Phase 2 update (this round):** three new layouts — **filmstrip**
> (a band of overlapping frames), **fan** (cards fanned from below the
> frame) and **masonry** (Pinterest columns with seeded size variety) —
> plus **beat sync** (energy-based onset detection at
> `GET /api/media/beats`; the editor maps the onsets into the slide's hold
> clock and stores them as `beats`, both engines snap arrivals to the next
> stored beat) and **depth push-back** (`depth: true` — landed photos
> shrink 6 % and dim per later arrival, capped at 4, drop/pop only; the
> FFmpeg twin tracks the shrink in the overlay anchors and dims through a
> per-photo `sendcmd` ladder driving `eq` — eq expressions evaluate only
> once, so the dim rides per-frame commands whose multiplicative
> contrast/saturation/brightness triple is the CSS `brightness()` the
> preview applies). Deal/shuffle & sweep-out exits and per-photo
> replace/reorder from the Phase 2 row remain future work.
> **Background update:** an
> optional library picture behind the photos with a blur strength
> (`backgroundImage` / `backgroundBlur`; replaces the colour bed and its
> change). The editor round-trips items as title frames with a `collage`
> spec; the backend also accepts the top-level `type: 'collage'` shape below.

| Phase | Contents | Effort |
| --- | --- | --- |
| **1 — Collage core** | `collage` slide type; layouts **stack**, **grid**, **scatter**; animations **drop-in**, **pop-in**, **swing**; seeded determinism; polaroid mat + shadow + pin/tape assets; collageCore twin (TS + Python); editor UI (photo picker + layout/anim choice, reuse detail list) | **M** |
| **2 — Choreography** | beat sync (onset detection), depth push-back, deal/shuffle & sweep-out exits, **filmstrip**, **fan**, **masonry**; per-photo replace/reorder | **S–M** |
| **3 — Camera moves** | wall-gallery pan + zoom-into-frame (bridges to the next slide), telescopic zoom | **S–M** |
| **4 — 3D & paper (stretch)** | photo cube, spinning columns, origami-fold entrance reusing gl_Fold inside a collage, Droste zoom | **M–L** |

Risks, honestly: (1) the biggest cost in phase 1 is editor UX, not rendering —
picking/ordering photos must be frictionless (the trend works because
"drop in whatever photos you have" takes 2 minutes); (2) ffmpeg expression
graphs for 10 photos get long — generate them, never hand-write; (3) keep the
collageCore twin rule strict from day one or preview/render drift will creep
in; (4) aspect-ratio handling inside mats (portrait vs landscape mixes).

## 5. Recommendation

1. **Do Phase 1.** It delivers the three highest-demand formats of the
   moment (photo dump/pile, polaroid stack, corkboard scatter — families 1,
   3, 4) in one milestone, reuses our strongest assets (generated frames,
   twin-engine discipline, stagger/easing vocabulary), and leaves the
   transition pipeline untouched.
2. Phase 2 next, with **beat sync as its headline feature** — it is the
   single biggest "looks like CapCut" differentiator and benefits text
   effects too.
3. Phases 3–4 as demand warrants; skip 4 unless users ask.

Separate small batch (from the previous advice, unchanged): the animate.css
completion round — `steps(n)` easings, flash, directional bounceIn/Out,
rotateIn corners, flip quartet, hinge (**S**). Independent of the collage
work.

## Sources

- Trend: accio.com photo-dump guide (359M+ views); lilachbullock.com CapCut
  reel trend analysis (audio cues drive virality); TikTok discover dumps
  (18–52 slot templates, native multi-picture feature 2025); CapCut "pile of
  photos reveal" tutorial #375.
- Templates: reactvideoeditor/remotion-templates (81 MIT templates:
  gallery-grid, masonry-gallery, photo-stack, polaroid-frame,
  image-carousel, split-screen, picture-in-picture); Videohive marketplace
  best-sellers (Photo Grid 690 sales, Grid Gallery, Mosaic Photo Gallery,
  Multiscreen Spotlight, Fashion Split Screen, Wall Gallery, Modern Zoom
  Telescopic, Spinning Columns); Speckyboy 80+ free AE templates (polaroid
  gallery, wall gallery, filmstrip, tile 3D).
- Techniques: transloadit polaroid-stack (seeded deterministic collage);
  CSS-tricks infinite polaroid slider (z-index deck cycling); GSAP/gsapvault
  (Elastic Stack Cards, Scroll Stack Cards depth push-back, Zoom Portal);
  codewithfaraz/Wakana K. swinging pinned gallery mechanics; StackOverflow
  ffmpeg animated-overlay expressions (49733467, 42770315, 24330).
- Consumer apps: Apple Photos themes (Classic, Ken Burns, Magazine,
  Origami); Canva element animations + drag-to-draw motion paths; SmartSHOW
  3D ("animated collages": up to 10 photos per slide, per-layer masks/
  borders/shadows, 3D camera); BlogGIF (3D photo cube).
