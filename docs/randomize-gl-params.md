# Randomizing transition parameters — decision & GUI options

Date: 2026-09-13 · Status: **implemented**

Request: "change the GL params control to params and randomize all parameters
of all xfade and GL transitions without changing duration."

## Current behaviour (verified)

The RANDOM SOURCE bar has a `params` checkbox. When it is enabled, both random
name actions draw a fresh settings set for every transition they touch:

- **Randomize all** (bulk bar, next to the RANDOM SOURCE select);
- **Random** (bulk tools, TRANSITION SELECTION group), for the selected
  transitions or all transitions when nothing is selected.

The separate `params` button re-rolls parameters on the existing transitions
without changing their names. In every case `transitionTime` is preserved.
With the checkbox off, the name actions randomize only the transition names;
the explicit `params` button always randomizes parameters.

## What "randomize parameters" means technically

The registry (`registry/transitions.json`, shared by GUI and backend) defines
the parameters of 102 of the 133 GL transitions — 269 parameters in total:
251 numeric, 16 colour-like values (including packed colour steps such as
`fromStep`/`toStep`), and no arbitrary text values. `randomGLParams(label)`
reuses the **exact bounds the editor's sliders show** (`GLParamControls`:
explicit registry min/max/step when present, otherwise the same derived ranges):

- numeric → uniform draw inside [min…max] rounded to the step;
- colour → random hex;
- transitions without extra GL parameters → `{}`;
- xfade and GL transitions also receive a random easing and reverse flag.

Each randomized GL transition gets its **own** independent set (not one shared
set), and the renderer needs no change: parameters already pass through into
`transition='gl_x(…)'` untouched. With bounds respected, FFmpeg accepts every
draw the sliders could have produced.

## GUI integration options

- **O1 [implemented] — "params" checkbox in the RANDOM SOURCE group.**
  The bulk bar holds the random source select that governs *both* random name
  actions; the checkbox next to it (persisted to localStorage) completes the
  decision pair: *from which pool* + *whether to randomize settings*. It stays
  enabled for `Random xfade`, because easing and reverse are parameters there
  too. Both name actions honour it.
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
- **O4 [implemented] — separate "params" button.** A genuinely *different*
  capability: re-roll only the parameters of every xfade and GL transition
  already in place, leaving names and durations alone ("keep my transition
  plan, surprise me with the settings").

## Implemented (2026-09-13)

- The persisted checkbox next to RANDOM SOURCE is labelled **"params"** and
  applies to xfade and GL transitions alike. It reads the old
  `slideshow.randomGlParams` key once for backwards compatibility and stores
  the new preference as `slideshow.randomTransitionParams`.
- `randomTransitionSettings(label)` randomizes easing, reverse, and (for GL
  labels) the registry-defined parameters. `randomGLParams(label)` uses the
  same slider bounds as `GLParamControls`, including packed colour values.
  Each transition receives its own independent set.
- The `params` button next to the selection Random action re-rolls every
  supported parameter on existing xfade and GL transitions, keeping both the
  transition names and `transitionTime` unchanged.
- The random name actions preserve `transitionTime`; when the checkbox is on,
  they also apply the complete parameter set to every newly chosen transition.
- The renderer already passes these stored settings through unchanged.

The text transition randomizer (enter/exit labels) and Ken Burns randomizer
remain separate because they are not visual transition parameters.
