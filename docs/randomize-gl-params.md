# Randomizing GL transition parameters — decision & GUI options

Date: 2026-09-11 · Status: **implemented** (O1 + O4)

Request: "let the user decide if the parameters of the GL transitions also need
to be randomized when user wants to randomize all/selected transitions — give
options how to integrate with current GUI."

## Current behaviour (verified)

Both random actions pick a random transition and then set its GL parameters to
**empty — i.e. silently reset to the registry defaults**:

- **Randomize all** (bulk bar, next to the RANDOM SOURCE select) — also
  re-rolls Ken Burns effects;
- **Random** (bulk tools, TRANSITION SELECTION group) — applies to the selected
  transitions, or to all when nothing is selected (this *is* the existing
  "randomize selected").

So today the user gets neither their tuned parameters kept nor randomized
parameters: every GL pick lands on defaults and must be tuned by hand, one
popover at a time. The ask is a *decision*: randomize the parameters too
(fully wild looks) or keep defaults (predictable, curated looks).

## What "randomize parameters" means technically

The registry (`registry/transitions.json`, shared by GUI and backend) defines
the parameters of 102 of the 133 GL transitions — 269 parameters in total:
251 numeric, 16 colours, 2 text (`fromStep`/`toStep`). A `randomGLParams(label)`
helper would live next to the existing `pickRandomTransition` and reuse the
**exact bounds the editor's sliders already show** (`GLParamControls`: explicit
registry min/max/step when present, otherwise the same derived ranges):

- numeric → uniform draw inside [min…max] rounded to the step;
- colour → random hex;
- text params → keep the default (nothing sensible to randomize);
- transitions without parameters → `{}` as today.

Each randomized GL transition gets its **own** independent set (not one shared
set), and the renderer needs no change: parameters already pass through into
`transition='gl_x(…)'` untouched. With bounds respected, FFmpeg accepts every
draw the sliders could have produced.

## GUI integration options

- **O1 [recommended] — "GL parameters" checkbox in the RANDOM SOURCE group.**
  The bulk bar already holds the random source select that governs *both*
  random buttons; a small checkbox next to it ("randomize GL parameters",
  persisted to localStorage like favourites/recents) completes the decision
  pair: *from which pool* + *how deep*. Both buttons honour it; the post-action
  toast says "+ GL parameters randomized"; it greys out when the source is
  "Random xfade" (no GL in that pool).
  - Pros: one visible, always-in-context decision for both all/selected;
    zero extra clicks during use; state persists across sessions.
  - Cons: one more control in an already dense bar (it is one checkbox).
- **O2 — fold it into the RANDOM SOURCE select** (options like "Both · with
  parameters"). One control, but it conflates two orthogonal decisions and
  doubles the option list (xfade/gl/both × params/no-params); the existing
  three labels also live in tooltips of two other buttons. Not recommended.
- **O3 — split button / click-popover** on "Randomize all": clicking randomizes
  as today; a small chevron opens a menu with the checkbox (and "apply now").
  Zero new chrome, but the choice is hidden behind a discovery-killer and
  would only cover the bulk-bar button, not the selection "Random".
- **O4 — separate "Randomize GL parameters" button.** A genuinely *different*
  capability: re-roll only the parameters of the GL transitions already in
  place, leaving the chosen transition names alone (a nice power tool:
  "keep my transition plan, surprise me with the settings"). More chrome;
  best offered later as an addition in the same group rather than the answer
  to this request.

## Implemented (2026-09-11): O1 + O4

- **The decision**: a persisted **"GL params" checkbox** in the RANDOM SOURCE
  group of the bulk bar (localStorage `slideshow.randomGlParams`), greyed when
  the source is "Random xfade". It governs **both** random actions —
  "Randomize all" and the selection "Random" — and both toasts confirm with
  "+ GL parameters randomized".
- **The helper**: `randomGLParams(label)` in `src/transitionControls.tsx`
  reuses the slider bounds of `GLParamControls` (registry min/max/step, else
  the same derived ranges), draws numerics uniformly on the step grid, colours
  as `#rrggbb`, and omits everything else so the registry default applies.
  Each randomized GL transition receives its own independent set.
- **O4 as well**: a **"GL params" button** next to the selection "Random" in
  the bulk tools — re-rolls only the parameters of the GL transitions already
  in place (selection or all), leaving the transition names untouched; it
  reports clearly when there is nothing to re-roll.
- **Verified** against the real registry with a randomized simulation
  (300 draws): every numeric value inside its bounds, colours well-formed,
  no invented parameter names; frontend build clean. The renderer is
  untouched — parameters already pass through unchanged.

Out of scope, unchanged: the *text* transition randomizer (enter/exit labels
have no parameters) and the Ken Burns randomizer.
