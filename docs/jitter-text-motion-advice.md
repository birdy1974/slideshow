# Jitter text templates — can we integrate the text motion?

Date: 2026-09-25 · Status: **advice only, nothing implemented yet**. This follows
the same pattern as [text-effects-options.md](text-effects-options.md): options
are marked **[recommended]** or alternative so we can pick one and build it
afterwards.

Request (improvements.md, 2026-09-23): *"check https://jitter.video/templates/text/
and advise if we can integrate the text motion in the application"*.

## Short answer

- **We cannot plug Jitter itself into the app.** There is no public API or
  embeddable renderer. Templates can only be edited inside Jitter's web editor
  with a Jitter account, and the result only leaves Jitter as an export
  (GIF / MP4 / MOV / WebM / Lottie). Exports are watermark-free only on paid
  plans, and transparent (alpha) exports need the Max/Ultra plan.
- **We can rebuild most of the motion *styles* in our own engine.** Most text
  templates are combinations of things we already render: scale overshoot,
  rotate, blur, slide, per-character/word stagger, clip-mask reveals and
  scramble. We can re-create around 15 of the ~70 templates as new entries in
  `registry/text-effects.json`. They would render for real in the MP4 (libass /
  drawtext) and stay editable (text, font, colour, timing) like every other
  effect. **This is the recommendation.**
- **Optionally, a "bring your own animation" overlay** would let a user drop a
  transparent WebM/MOV exported from Jitter (or any other tool) onto a slide.
  Any template then looks exactly like Jitter's version, but the text is baked
  into the clip and can't be edited in our app.

## What is on the page

The text template page lists about 70 templates. Many are text motion, but a
good share are small UI scenes (cards, menus, feature lists, chat bubbles,
product launches) or brand showcases. Each template is 2–5 s long.

They sort into these families (examples from the page):

| Family | Examples |
|---|---|
| Snap / stretch / elastic | Snappy Text Stretch, Elastic Text, Stretch, Squeeze, Bold Text Snap, Tilted Text Snap, Wow: Rotate and Scale |
| Mask reveals | Sliding Text Reveal, Sliding Title Reveal [Apple Event], Appear, Cascading Text (lines land one after the other) |
| Blur | Motion Blur (blur-in), Blurry Text Spin (rotate + blur), Blur: Text Scroller |
| Trails / repeats | Type Trail (motion-blur trail behind the text), Stretched Type Repeater, Animated Repeater, Multiply, Text Mirror Effect |
| Bouncy / physics | Zero Gravity: Bouncy Words, Bouncy Period, The Harvest: Fruit Bounce |
| Scramble / digital | Text Scramble, The Route: Departures Board (split-flap), Glitch 01/02 Text Reveal, Glitchy Text Reveal, CRT Effect |
| Numbers | Counter: 1,000 installs, Countdown: Bold |
| Blend / mask styling | Blend Modes: Type Blend / Overlay Poster / Color And Text, Design: Negative Mask Effect |
| Morphs / 3D | Morph: Lines/Dots/Shape To Text, Morph: Inflating Text, Text Extrusion |
| UI / scene templates | The Vault, The Crust, The Edit, Animated Text Messages, Feature List, Save The Date, … |

## Options

### Option A — use Jitter directly (templates / API) — not feasible

- There is no official API. Changing text in a template and exporting it
  happens in Jitter's editor. The only automation we found is a third-party
  browser-automation "skill" that drives the logged-in editor. That is fragile,
  needs a paid account for clean exports, and is probably against Jitter's terms
  when used from inside another product.
- Jitter's "free for commercial use" wording covers exporting your own edited
  template for your own video. It does not cover re-distributing their template
  catalogue inside our app.
- **Verdict: no.**

### Option B — "Overlay clip" layer (import a Jitter export) — alternative

The user makes the animation in Jitter, exports it with a transparent
background, and adds it to a slide or text frame as an overlay layer with
position, scale, start time and loop settings.

- **Render:** the renderer already builds `filter_complex` graphs with
  `overlay=`, and our FFmpeg build includes `libvpx`. A VP9 WebM with alpha
  needs `-c:v libvpx-vp9` on that input, otherwise the alpha channel is lost.
  ProRes 4444 `.mov` decodes natively. This is a moderate backend change: a new
  input plus one overlay per layer, reusing the existing timing code.
- **Preview:** a `<video>` element on top of the stage and canvas. Chrome and
  Firefox show WebM alpha. Safari does not, so it would need a fallback
  (still frame or no-alpha preview).
- **Lottie instead of video:** Lottie is free to export from Jitter, and
  `lottie-web` would give a perfect browser preview. However, getting it into
  the MP4 needs a headless Lottie renderer in the container (rlottie, or a
  headless Chromium). That is a large extra dependency on the DS918+ target, so
  not recommended as a first step.
- **Pros:** matches Jitter's look exactly, works with any template (UI scenes
  and morphs included), and also accepts clips from After Effects, Canva and
  similar tools.
- **Cons:** text is baked in (a typo means going back to Jitter). Transparent
  export needs Jitter Max/Ultra. Alpha WebM/MOV files are large, and 720p free
  exports look soft in a 1080p render.

### Option C — rebuild the motion styles natively **[recommended]**

Add the best ideas as new registry effects on the existing engines. The work
per effect is the same as for every current effect: one registry entry, the
backend expression (`backend/app/text_effects.py`), the live canvas / preview
behaviour in `src/`, and the cached preview clip. The result renders into the
MP4, keeps every text setting editable, and combines with motion paths,
rotate / squash / grow and the Enter / While / Exit slots.

What we already have (no work needed): Blur in/out, Rotate in/out, Pop in/out,
Zoom down, Wipes (8 directions), Split rise/fade by chars/words, Typewriter,
Decrypted scramble, Glitch flicker, Count up, Drop & bounce, Slide & overshoot,
Horizontal drift, Grow / Shrink, Rotate + Squash while shown, and the Bounce
motion path. Jitter's **Motion Blur, Text Scramble, Counter, Wow: Rotate and
Scale** and plain **Appear** are therefore already covered or very close.

Proposed new effects, ranked by value against effort. S / M / L is relative
effort; "ass" means libass tags, "dt" means drawtext expressions:

| # | New effect (slot) | Inspired by | How we would render it | Effort |
|---|---|---|---|---|
| 1 | **Snap stretch** (enter) | Snappy Text Stretch, Elastic Text, Stretch, Squeeze | ass `\t` chain on `\fscx`/`\fscy`: wide and flat, overshoot tall, then settle (spring easing) | S |
| 2 | **Mask slide-up reveal** (enter + matching exit) | Sliding Text Reveal, Apple Event title | ass `\clip` fixed at the text box plus `\move` from below, so the text rises out of an invisible line | S |
| 3 | **Cascade · lines** / **· words** (enter) | Cascading Text, Tilted Text Snap | the existing split stagger per *line* (new unit) with drop + overshoot; optional small tilt per line (`\frz` settle) | M |
| 4 | **Blur spin in** (enter) | Blurry Text Spin | ass `\frz` 90→0 + `\blur` 12→0 + fade, easeOut | S |
| 5 | **Motion-blur slide** (enter) | Motion Blur, Type Trail | `\move` + horizontal stretch `\fscx` 140→100 + `\blur` fall-off during the slide | S |
| 6 | **Echo trail** (while / enter) | Type Trail, Stretched Type Repeater, Multiply | 3–5 ghost copies on lower layers, time-delayed along the same motion with falling alpha (ass layers; also works on motion paths via the layered path) | M |
| 7 | **Bouncy words** (enter) | Zero Gravity: Bouncy Words, Bouncy Period | per-word drop with damped bounce and stagger (the Drop & bounce maths per word, as ass `\move` segments) | M |
| 8 | **Split-flap board** (enter) | The Route: Departures Board | per-character scramble with a left-to-right stagger and a short vertical flip (`\fscy` 100→0→100) per settle | M |
| 9 | **Marquee loop** (while) | Blur: Text Scroller, Motion Design: Looped Text | continuous wrap-around ticker (two copies, dt `x` modulo width), optional edge blur | S |
| 10 | **Mirror reflection** (style toggle) | Text Mirror Effect | layered path: text layer, `vflip`, fade gradient, overlay below the text | M |
| 11 | **RGB-split glitch reveal** (enter) | Glitch 01/02 Text Reveal, Glitchy Text Reveal | upgrade Glitch flicker: red/cyan offset copies + random horizontal slices, only during the enter | M |
| 12 | **Fake extrusion / long shadow** (style) | Text Extrusion | N stacked offset copies in a darker shade (ass layers); static, no real 3D | M |

Not recommended or out of reach with drawtext/libass:

- **Morphs** (lines / dots / shape to text, inflating text). These need vector
  outline morphing and have no sensible FFmpeg route. Option B is the way to
  get them.
- **Blend-mode / negative-mask looks** (Type Blend, Overlay Poster, Negative
  Mask). They are possible via the layered path with FFmpeg's `blend` filter,
  but the preview would need CSS `mix-blend-mode` parity on every surface.
  Revisit later if wanted.
- **CRT effect.** This is a whole-frame look (scanlines, RGB shift, vignette),
  not a text effect, so it belongs in the picture filters. It is cheap to add
  there.
- **UI scene templates** (cards, menus, chat bubbles, feature lists). They are
  mini-compositions, not text motion. Option B covers them.

## Recommendation

1. **Build Option C, first batch: effects 1, 2, 3, 4 and 5** (Snap stretch,
   Mask slide-up reveal + exit, Cascade by lines/words, Blur spin in,
   Motion-blur slide). They cover the look of about 12 of the Jitter text
   templates and fit the existing libass engine without new dependencies. Each
   one follows the checklist in [text-effects.md](text-effects.md).
2. **Second batch:** 6–9 (Echo trail, Bouncy words, Split-flap board, Marquee
   loop).
3. **Option B (overlay clip)** only if you want exact Jitter results or
   non-text scenes. It is a separate feature and can be built at any time
   without affecting the above.

The two follow-up items in improvements.md (Easy-Text-Effects-for-Unity and
the Prismic CSS text animations article) will most likely produce the same
kinds of effects (per-character stagger, elastic scale, wave, blur, glitch).
It makes sense to review them before starting batch 1, so the new registry
entries are designed once for all three sources.

## Sources

- Jitter text templates: https://jitter.video/templates/text/ (template list,
  export formats per plan)
- Template pages, e.g. https://jitter.video/template/type-trail/,
  https://jitter.video/template/stretch-snap-text/,
  https://jitter.video/template/cascading-text/,
  https://jitter.video/template/sliding-text-reveal/,
  https://jitter.video/template/tilted-text-snap/,
  https://jitter.video/template/blurry-text-spin/,
  https://jitter.video/template/zero-gravity-bouncy-words/
- Export formats and plans: https://help.jitter.video/en/articles/5369843-export-your-work
- Commercial use of templates: https://jitter.video/templates/
