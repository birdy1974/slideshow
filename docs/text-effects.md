# Dynamic text effects — implemented (Option 1: drawtext expressions + libass)

Date: 2026-09-11 · Status: **implemented** (advice history in
[text-effects-options.md](text-effects-options.md), catalogue of record in
`registry/text-effects.json`)

Every text in the app — standalone title frames and captions on pictures — has
three animation slots, saved with the project and rendered for real into the
MP4:

| Slot | Item fields | Default |
|---|---|---|
| **Enter** — how the text appears | `textFxEnter` + `textEnterDuration` | Fade |
| **While shown** — loops for the hold | `textFxWhile` + `textFxWhileSpeed` (+ `textFxParams` for Count up) | None (static) |
| **Exit** — how it disappears | `textFxExit` + `textExitDuration` | Fade out |

The catalogue (65 effects, grouped Fades / Slides / Split / Typed / Zoom /
Reveal / Lower third / Motion / Highlight / Light / Numbers) lives in
`registry/text-effects.json` and is read by **both** sides — the frontend
picker (`src/textEffects.ts`) and the renderer's engine
(`backend/app/text_effects.py`) — so labels, glyphs and defaults can never
drift. Saved projects store labels, never indexes; unknown labels degrade to
the slot default exactly like unknown transition labels degrade to Fade.

## The two render engines

`backend/app/text_effects.py` is the dispatcher the renderer's `_text_filter`
calls. Per item it picks one of:

1. **`dt` — animated drawtext expressions** (Fade, the eight Slides, Gentle
   float, Horizontal drift, plain fades). The same filter the renderer has
   always used, with `alpha`/`x`/`y` driven by `t`-expressions (damped spring
   for Rise & settle, ballistic bounce for Drop & bounce, easeOutBack for
   Slide & overshoot). When every slot is at its historic default the emitted
   filter is **byte-identical to the pre-effects renderer**, so old projects
   render exactly as they always did.
2. **`ass` — a per-clip `.ass` overlay burned in by libass**
   (`ass=filename=<work>/text-NNNN.ass:fontsdir=<fonts>`; the custom FFmpeg
   build has carried `--enable-libass` since the xfade-easing work, and the
   `fontsdir` points at the same TTFs the GUI uses, so typography parity is
   preserved). One file per item, written into the job's work dir — concurrent
   renders never collide. Anything the drawtext filter cannot express lives
   here: typewriter karaoke `\k`, per-character stagger (`split`/`wave`/
   `shimmer`), `\t` transforms (pop/zoom/flip/rotate/glow), animated `\clip`
   wipes, `\p1` vector bars (lower third), frame-sliced events (shake,
   glitch flicker, count up, scramble, typewriter delete).

**Outline & shadow.** Picture captions get a dark outline (≈ size/16 px,
`borderw`/`bordercolor` in drawtext, `Outline`/`OutlineColour` in the .ass
style) plus the historic 2 px soft shadow. It is a project default
(`textDefaults.outline`, on unless switched off in *Default text style*);
text frames never get it since they sit on their own colour bed.

**No drawtext? Everything goes through libass.** `drawtext` needs libfreetype
at FFmpeg build time and many stock binaries (distro packages, NAS builds,
the imageio wheel) ship libass *without* it. The renderer probes both filters
once (`Renderer.drawtext_filter_supported()` / `ass_filter_supported()`); when
drawtext is missing every `dt` plan — including the legacy caption fade — is
built with `force_ass=True`, which has libass twins for all of them (`\fad`
for the fades, `\move` + `\fad` for the slides/rise/drop/overshoot, `\t`
scale bounces for the springs). Previously those clips failed with
"Filter not found" and rendered with no text at all, which read as "text
effects do not work". Builds with neither filter log an error and skip the
overlay rather than failing the render.

**Fallback:** FFmpeg builds without the `ass` filter (some stock NAS packages;
the container build hard-checks libass so it is always fine) degrade every
slot to the plain fades instead of failing the render — logged once per
render. No Dockerfile change was needed.

### Composition rules

* Tag-based effects stack: enter tags + while tags + exit tags share one
  override block (e.g. Pop in + Neon glow + Wipe out left).
* The frame-sliced "while" effects (Shake, Glitch flicker, Count up) own the
  whole timeline — they draw stepped enter/exit fades themselves, so the other
  two slots demote to fades.
* The sliced enter/exit effects (Typewriter + caret, Decrypted scramble,
  Typewriter delete, Scramble out) trim the running event and append their
  slices; they compose with tag effects in the other slots.
* Typewriter + Karaoke sweep collapse to the typewriter (two `\k` streams
  cannot stack).
* Multi-line text: `\N` between lines; per-character effects keep the layout
  by rendering the full text with exactly one visible unit (the hidden-line
  trick), so everything stays centred with zero glyph measuring.

## GUI

* **Text frame editor — "Text animation" section**: three rows (Enter /
  While shown / Exit), each a chip opening the grouped, searchable effect
  popover with **real rendered example clips** (cached under
  `/config/text-effect-previews`, same pattern as the transition previews),
  a duration stepper (loop period for "While shown"), Count-up from/to
  parameters, and a play/pause toggle that also freezes the CSS preview.
* **Looping CSS approximation** on the editor canvas
  (`TextFxPreview`): keyframe tracks for enter/exit over the clip duration,
  per-char delays for the split/typed families, a second infinite animation
  for the while-loop, a live counter for Count up.
* **Storyline & detailed list**: the existing enter/exit chips now show
  text-effect symbols; the popover and the multi-selection inspector use the
  text-effect catalogue (replacing the 191-entry xfade list, whose labels
  mostly made no sense on text).
* **Default text style popup**: gains default Enter/While/Exit for new text,
  saved with `textDefaults`.
* **Bulk bar**: "Text effects" randomizes enter+exit over the catalogue for
  the selection (or every photo).
* **Legacy projects**: xfade labels stored in `textEnter`/`textExit` map onto
  their text equivalents (Slide up → Slide from bottom, Wipe left → Wipe from
  left, …); anything else degrades to Fade — matching what actually rendered
  before (everything was a fade).

## API

- `GET /api/text-effects/status` — which effects have a cached example clip
- `POST /api/text-effects/build` — render every missing example (background)
- `DELETE /api/text-effects` — drop the cache
- `GET /api/text-effects/{slug}.mp4` — render-once, then stream the clip

## Scope / later

- Picture captions already go through the same `_text_filter`, so all 65
  effects work there too — the editor's caption popover offers the same chips.
- Not implemented (deliberately): a standalone full-screen effect gallery
  (the picker popover already shows every example clip), and per-character
  WYSIWYG-exact CSS previews (the CSS loop is an approximation by design —
  the MP4 is the truth, as the editor footnote says).
- Falling letters (FallingText) remains a good future per-char `\move` effect.
