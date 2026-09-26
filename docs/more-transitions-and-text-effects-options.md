# More slide transitions, text effects and text motions — options

Research round (2026-09-26): deep web search for anything we could still add.
**Options only — no code changed.** Every option below lists source, what it
would add, and an effort estimate. Effort legend: S = hours, M = days, L = weeks.

What we have today (baseline):

| Area | Count | Source |
| --- | --- | --- |
| Slide transitions | **191** — 58 native xfade + 133 GL | FFmpeg built-ins + scriptituk/xfade-easing port |
| Easings | 50+ incl. CSS, elastic/back/bounce, flipelastic/flipback | xfade-easing |
| Text effects | **129 stackable** (in/hold/out) + 19 presets, 15 categories | registry/text-effects.json |
| Text motion paths | 10 — straight, freehand, polyline, circle, sine, sine-vertical, star, diamond, triangle, bounce | TextMotionPathEditor |

Architecture constraint that applies to every text option: the registry v2
(`registry/text-effects.json`) is interpreted by **two engines that must agree
bit-for-bit** — `backend/app/text_motion.py` (Python → libass/ASS for the MP4)
and `src/textMotionCore.ts` (live preview). So every new effect is implemented
twice, plus the GL preview page (`docs/mockups/text-effects-stack.html`).

---

## 1. Slide transitions

### 1a. Nothing new in native xfade — we are complete
The 58 native xfade transitions we register is the **full built-in set**;
FFmpeg 7.x/8.x added no new xfade transitions (upstream lists still match ours
exactly). Sources: FFmpeg xfade docs, ayosec list, OTTVerse overview.

### 1b. Official gl-transitions collection: 1 transition missing
The official collection (gl-transitions.com) currently holds **125** GLSL
transitions. Diffed against our 133 GL ports (which include scriptituk's 15
custom shaders), only **one** is genuinely missing:

- **`coord-from-in`** (by haiyoucuv) — a pixel-coordinate morph: the outgoing
  frame's *colour values* are used as coordinates to look up the incoming
  frame, producing a liquid RGB-warp melt. ~40 lines of GLSL.
  Effort **S**. Source: transitions/coord-from-in.glsl.

The remaining diffs are deliberate duplicates of native xfade (GL `wipeLeft/
Right/Up/Down`, `CircleCrop`, `Radial`, `dissolve`, `pixelize`) — not worth
porting twice.

### 1c. Sync our ffmpeg-patch with upstream xfade-easing 3.6.x (recommended)
Upstream ([scriptituk/xfade-easing](https://github.com/scriptituk/xfade-easing),
now v3.6.6, Sep 2026) moved on while our `ffmpeg-patch/xfade-easing.h` was
snapshotted. Gains from re-syncing:

- **FFmpeg 8.1 compatibility** (altered `AVExpr` broke plotting; 8.0 logging).
- Bug fixes: out-of-bounds reads in `slideleft/slideup/revealleft/revealup` at
  progress=1 (FFmpeg issue #22273), `gl_EdgeTransition` YUV green cast,
  `squeeze[hv]` divide-by-zero, easing overshoot handling (gaps now show the
  other input instead of garbage).
- New transition **parameters** we could expose in the registry:
  - `gl_Swirl` → `clockwise`
  - `gl_Lissajous_Tiles` → `power` (transition curve)
  - `gl_Bounce` → `direction` (S/W/N/E) + `shadowColor`
  - `gl_CrossZoom` → `centerFrom/centerTo`
  - `gl_RotateScaleVanish`, `gl_StereoViewer` → **`trkMat` track-matte**
    (variable-transparency overlay — a whole new look, "Dr Who" style)
  - `gl_SimplePageCurl` with `radius=0,roll=1` = free-angle wipe byproduct.
- Performance work in the C code (LinearBlur, kaleidoscope optimisations).

Effort **M** (re-port the patch against our ffmpeg version, re-verify the 133).
No new transitions, but parameters above behave like ~10 new ones.

### 1d. Custom mask/luma wipes (classic editor feature) — new capability
Transitions defined by a **greyscale mask image** (gradient wipes, star-wipe
masks, hand-drawn reveal masks) — the one classic NLE feature neither xfade
nor gl-transitions has. Two routes:

- **GL-in-C port**: a `gl_maskwipe(file, softness, reverse)` transition in our
  patched xfade that samples a PNG uploaded via the GUI. Effort **M**.
- **Expression variant**: `xfade=transition=custom:expr=…` with a pre-baked
  mask is not practical (no texture support) — so the C route is the real one.

Bonus for slideshow use: masks drawn by hand (kid drawings, logos) — a nice
differentiator. Needs GUI: mask upload + preview like transition previews.

### 1e. Port selected OBS community transition shaders
[exeldro/obs-shaderfilter](https://github.com/exeldro/obs-shaderfilter) ships a
few dual-input *transition* shaders not in gl-transitions:
`3d_swap_transition`, `diffuse_transition`, zoom-blur transition, plus
audio-reactive examples. Each is a manual port (like 1c's glsl/ folder).
Effort **S each**. Yield: ~3–4 novel looks (3D panel swap is fun for slides).

### 1f. Optical-flow / AI morph transition (heavy — probably not)
RIFE-style frame interpolation between last frame of slide A and first frame
of B gives the "true morph" look (Apple/Google Photos style). Requires a
frame-interpolation step per transition in the render pipeline; preview would
be fake. Effort **L**, runtime cost high. Listed for completeness; not
recommended now.

### Recommendation (transitions)
1. **1c sync** (fixes + new parameters = most value, zero GUI redesign)
2. **1b port `coord-from-in`** (cheap, one nice effect)
3. **1d mask wipes** (genuinely new category, great for slideshows)
4. 1e as filler; 1f skip.

---

## 2. Text effects (new stackable effects for registry v2)

Mined from: [Jitter template library](https://jitter.video/templates/text/),
Remocn typography catalogue, RemotionUI (200 components),
GSAP (all plugins free since Apr 2025), CapCut/TikTok caption trends,
Animate.css. Filtered against our 129 existing effects — only the **new**
ideas are listed.

### 2a. Type & numbers
- **Slot machine roll** — characters roll vertically into place like a casino
  reel / rolodex flip (RemotionUI "slot-roll", Remocn "SlotMachineRoll",
  "Number Wheel", "Rolodex Flip"). Distinct from our departures-flap (flap) —
  this is continuous roll with overshoot. Effort **S–M**.
- **Counter styles** — halftone/dotted/pixel/progress-ring looks around
  count-up/countdown (Jitter counter family). Mostly *styling presets* on the
  existing count-up; effort **S**.

### 2b. Word/phrase swapping — a new content class
Currently one text string animates in/hold/out per slide. The modern trend
(Remotion "Text Transitions": PerWordCrossfade, FadeThrough, SharedAxisY/Z;
Jitter "Morph" family) is **swapping phrases inside one slide**:
- word-by-word crossfade to the next phrase
- fade-through (out ↓, in ↑, Material style)
- hard-cut staircase / scale-depth swap
- morph: lines→text, dots→text, shape→text, inflating text

Implementation: extend the `content` lane with a `swap` type (list of phrases
+ per-phrase timing). Moderate engine work (both engines), big creative win —
this is how social-media titles work today. Effort **M–L**.

### 2c. Light & colour
- **Gradient fill / animated gradient text** — colour stops sweep across
  letters (Jitter "Gradients", Locomotion "Gradient Text"). Our colour channel
  folds per-unit colours; a gradient track (two colours + moving offset) is a
  natural extension. Effort **S–M**.
- **Chromatic wave** — an RGB-split *wave* travelling through the text (we
  have static rgb-split; this is the sweeping version). Effort **S**.
- **Shadow sweep / sheen sweep** — a highlight or shadow band passes across
  (Remocn "Shadow Sweep Text", "Sheen Slide In"). We have shimmer; sweep is
  directional/spatial. Effort **S**.

### 2d. Emphasis
- **Strikethrough replace** — old word crossed out, replacement drops in
  (RemotionUI "strikethrough-replace"). Effort **S** (line channel + content).
- **Inline pill takeover** — a rounded highlight pill grows over a word and
  the word re-colours (Remocn "Inline Pill Takeover"). We already have `box`
  copies (departures-flap) — reuse with rounded growth. Effort **S–M**.

### 2e. Depth & copies
- **Bubble pop** — each character pops in inside a bubble (Remotion
  bubble-pop-text). New copy type `bubble` (circle behind char). Effort **S**.
- **Particle burst / dust** — letters explode into particles (Remotion
  particle-explosion; GSAP physics). New copy type `particles` (deterministic
  seeded scatter). Effort **M**.
- **Motion-blur streaks** — fast moves leave directional blur (Jitter
  "Motion Blur" title). Approximation: trail copies with per-copy offset+
  alpha ramp along the motion vector. Effort **S** (we have `trail` copies).
- **Gooey / liquid morph** (Jitter "Morph: Inflating Text", Remocn "Gooey
  Morph") — needs blur+contrast goo filter; **libass cannot do this well**
  (blur is uniform, not directional/contrast-shaped). Approximate only; flag
  as low fidelity. Effort **M**, risky.

### 2f. Motion & physics (new generator types)
- **Physics generator** for x/y/rz channels: gravity drop with restitution,
  throw-with-velocity, scatter, vortex — GSAP Physics2D ideas. Deterministic
  closed-form math exists (no solver needed) so preview and render stay
  identical. This one generator unlocks ~10 effect presets (fall & tumble,
  scatter, confetti letters, tornado, springy settle). Effort **M**.
- **Attention seekers** (Animate.css): rubberBand, wobble, tada, headShake —
  hold-phase flavors of existing scale/skew/rz tracks. Effort **S** (data only).
- **Credits roll / marquee** — continuous vertical scroll (movie credits) and
  infinite horizontal marquee (Jitter "Text Scroller", Remocn "Infinite /
  Perspective Marquee"). New hold `loop: scroll` mode on y/x. Effort **S–M**.

### 2g. Audio-reactive effects (new generator, unique for a slideshow app)
The backend already measures soundtrack loudness (LUFS analysis). Feed a
downsampled loudness curve into the preview + renderer as a new generator
`audio`: **pulse on beat**, shake on beat, zoom with music, colour flash on
drop. Killer feature for slideshow intros/captions; nobody in the ffmpeg
slideshow space has it. Effort **M–L** (curve extraction, twin engines, cache).

### 2h. CapCut/TikTok caption styles (presets, mostly existing channels)
Trend inventory from CapCut tutorials: **Bounce Out** word-by-word pop,
highlight-box per active word, single-line captions with negative (knockout)
style, "slight stretch" pop. We already own the building blocks
(word-by-word, overshoot, box copies, outline/fill); these are **presets +
a couple of flavours** (bounce-out easing flavor, per-word highlight box
timing). Effort **S** for 4–5 presets.

### Recommendation (text effects)
1. **2f physics generator** (one mechanism, many effects, deterministic)
2. **2b phrase swap** (biggest modern trend, new capability)
3. **2c gradient/chromatic/shadow sweeps** (cheap, high polish)
4. **2h CapCut presets + 2a slot roll + 2e bubble/trail** (quick wins)
5. **2g audio-reactive** (differentiator, do after 2f)
6. 2e gooey — prototype first or skip.

---

## 3. Text motions (path engine)

Current: straight, freehand, polyline, circle, sine, sine-vertical, star,
diamond, triangle, bounce. Candidates (all deterministic maths, both engines):

- **Spiral** (in/out, radius decay, turns) — classic; pairs with zoom.
- **Figure-8 / infinity (lemniscate)** — elegant loop for hold phase.
- **Lissajous** (x/y frequency ratio + phase) — already a GL transition
  (Lissajous Tiles); a natural motion twin.
- **Zigzag** (sawtooth horizontal traverse) — energetic.
- **Heart path** — seasonal favourite (we already generate star/diamond/
  triangle shapes; same family).
- **Pentagon/hexagon/general regular polygon** — generalises star.
- **Pendulum swing** (arc around a pivot above the text) — distinct from
  bounce (vertical) and sine.
- **Boomerang** — out-and-return with overshoot easing (path + easing combo
  preset, trivial).
- **Vortex/tornado** — spiral with increasing angular speed + upward drift.
- **Random wander** (seeded Perlin noise) — organic drift; deterministic with
  fixed seed per render, matching preview.
- **Cubic Bézier editor** — drag two control handles between Start and End;
  the *general* path editor that makes several presets above unnecessary.
  Effort **M** (UI), the maths is trivial.
- **Rotate-along-path** — letters orient to the path tangent (rz = atan2 of
  derivative). New channel coupling; makes loops (circle, lissajous) look
  dramatically better. Effort **S–M**.
- **Per-letter staggered path following** — letters follow the same path one
  after another ("snake" / Type-trail style). Path engine currently moves the
  whole block; per-unit offset along the path is a contained change. Effort **M**.
- **Projectile/gravity arc** — falls out of 2f physics for free.
- **Audio-synced bob** — gentle float whose amplitude follows loudness (2g).

Effort per simple path shape: **S**. Bézier editor, per-letter paths and
rotate-along-path: **M** each.

### Recommendation (motion)
1. Bézier editor + rotate-along-path (foundation)
2. Spiral, figure-8, lissajous, pendulum, zigzag, heart (cheap data adds)
3. Per-letter staggered paths (pairs beautifully with 2a/2e effects)
4. Audio-synced bob once 2g exists.

---

## Sources

- gl-transitions.com — official collection (125) & repo file list
- github.com/scriptituk/xfade-easing — README + CHANGELOG 3.6.0–3.6.6
- github.com/exeldro/obs-shaderfilter — releases/shader list
- jitter.video/templates/text, /video-titles, /new — text template library
- remocn.dev/docs/typography — typography effect catalogue
- remotionui.com/docs/components/browse — 200 Remotion components
- github.com/reactvideoeditor/remotion-templates — 81 templates
- gsapify.com, gsapvault.com, noqode.fr — GSAP free plugins since Apr 2025
- CapCut caption tutorials (TikTok discover, capcut.com resources)
- animate.css catalogue (kodingkhurram demo mirror)
