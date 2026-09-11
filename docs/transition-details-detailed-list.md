# Detailed settings of the selected transition in the detailed slide list

Date: 2026-09-11 · Status: **advice** (implementation not started)

Request: "is it possible also to show the detailed settings of the selected
transition in the detailed slide list? advise on new layout for gui in detailed
list."

**Answer: yes — every piece already exists and is only shown in the wrong
view.** The filmstrip (overall timeline) has a *timeline-inspector* bar that
appears when transitions are selected: name chip, duration stepper, GL
parameter sliders, easing select, reverse toggle, clear — edits apply to all
selected transitions. The detailed slide list has nothing equivalent: its row's
transition cell shows name + duration, and a small ⚙ popover appears **only
when easing/reverse/params are already non-default** — so a plain Fade row
offers no hint that settings exist at all, and nothing shows the values at a
glance.

## Layout options for the detailed list

- **L1 [recommended] — selected-transition inspector bar above the list.**
  Extract the filmstrip inspector's JSX into a shared `TransitionInspector`
  component and render it directly above the detailed rows (below the column
  head, sticky so it survives scrolling) whenever `selectedTransitions` is
  non-empty. Identical controls and multi-select behaviour in both views; one
  shared component, no new interaction pattern.
  - Pros: view parity with the filmstrip; multi-edit ("n transitions
    selected") for free; the rows themselves stay compact.
  - Cons: the bar is a second place to look (mitigated: it appears exactly
    when a transition is selected, with a Clear button).
- **L2 — expandable detail strip under the clicked row.** The row's ⚙ (always
  visible, not only when non-default) expands a full-width panel under that
  row with every setting plus a mini preview. Per-transition editing in
  context without selecting; but rows grow/shrink (the list jumps) and only
  one can be open at a time.
- **L3 — always-on summary in the transition column.** A second line under the
  duration stepper: `easing · reverse · 3 params` when non-default. Zero
  clicks to *see* state, but not editable and the rows are already the densest
  part of the GUI (an earlier request was precisely to make this list *less*
  long/dense).
- **L4 — hybrid [recommendation]: L1 + always-visible ⚙ + summary line.**
  The inspector bar covers "show the detailed settings of the *selected*
  transition"; making the ⚙ permanent covers quick single edits (and its
  popover can stay as-is); the tiny summary line (L3's text, shown only when
  non-default) covers at-a-glance state without adding a row of controls.

## Scope notes

- "Selected" in the detailed list means the same transition selection the
  filmstrip uses (the round select on each transition cell) — bulk tools and
  the inspector agree in both views.
- The text-transition popover/appear-disappear chips are a separate, already
  working pattern; out of scope.
- Effort: L1 alone is small (extract component + one render site + sticky
  CSS); L4 adds the permanent ⚙ and the summary line (~half a day total,
  both trivially testable via the existing stub-FFmpeg-free frontend build).
