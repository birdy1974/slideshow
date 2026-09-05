# Transition browser & cached previews

The editor offers **191 transitions** — 58 native FFmpeg `xfade` effects and 133
GL transitions ported from [gl-transitions.com](https://gl-transitions.com/) —
and a name like `GL · Angular` tells you nothing about what it looks like. This
document describes the picker built for that catalogue and the preview cache
that backs it.

## The picker

Every place that used to render a flat 191-entry `<select>` now renders a
**chip** that opens one shared browser popover:

| Location | What changed |
|---|---|
| Detailed slide list row (`TransitionCell`) | select → chip (spans the old symbol column) |
| Text appear/disappear popover in that row | select → chip |
| Timeline multi-select inspector | select → chip |
| Text-transition inspector | select → chip |
| Bulk “TRANSITION SELECTION” apply | select → chip |
| Frame background colour-change editor | select → chip |
| Transition preview modal | tabs + select + text list → single chip |

Two doors lead into the same catalogue:

- **The chip picker** — one per transition setting (see the table below).
- **The standalone gallery** — a full-screen window for *browsing* rather than
  picking, opened with **Browse all 191** in the transitions bar or **Open full
  gallery** in any picker's footer. It has a large stage: hover a tile to see
  it move, click one to park it there and study it. There is deliberately no
  "apply" — choosing a transition for a slide stays with the chips.

The browser popover is `position: fixed` and rendered through a portal on
`document.body`, because `.panel` uses `overflow: hidden` and would otherwise
clip it. It contains:

- **Search** — filters all 191 by name as you type.
- **Scope tabs** — All / XFade (58) / GL (133) / ★ Favourites / Recent.
  Clicking a tab clears the search so it can never dead-end on an empty grid.
- **Category rail** — the 16 catalogue groups, with counts.
- **Tile grid** — one tile per transition, with a looping preview on hover.
- **Footer** — filtered count, cache status, autoplay toggle, a link to the
  full gallery, and a “Render all *n* missing” button.

Keyboard: `↓` from the chip opens it, the search field takes focus, `↑ ↓ ← →`
move the active tile, `Enter`/`Space` picks it, `Escape` closes. Recents and
favourites live in `localStorage` (`slideshow.transitions.recent`,
`slideshow.transitions.favourites`) and are per browser, not per project.

## The preview cache

Rendering 191 clips on demand is far too slow to do while someone scrolls, so
each transition is rendered **exactly once** — between two tiny synthetic
example frames — and stored on the config volume.

```
<config>/transition-previews/
    manifest.json          status per transition slug (ready/failed/unsupported)
    src/a.png, src/b.png   the two synthetic 640×360 example frames
    fade.mp4               one ~1.2 s H.264 clip per transition, named by slug
    gl_angular.mp4
```

- **Keyed by label, not by FFmpeg id.** The frontend only ever knows the
  friendly label, so the cache key is a slug of it (`GL · Angular` →
  `gl-angular`). The backend resolves label → filter itself, which keeps the two
  sides from drifting.
- **Rendered with the real filter.** The cache calls the renderer's own
  `build_transition_xfade()`, so a preview is the same `xfade` invocation a real
  render would use, including the registry defaults for GL parameters.
- **Honest about fallbacks.** If the installed FFmpeg cannot run a transition
  (stock builds have no GL support), the entry is recorded as `unsupported` and
  *no* clip is rendered — that would just be 133 identical dissolve clips. The
  tile shows a `fallback` badge and the CSS approximation instead.
- **Never renders twice.** `ensure()` checks the file first, then takes a
  per-slug lock, then re-checks. Concurrent requests for the same transition
  share one render.

### HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/transition-previews/status` | counts + per-slug status; the picker calls this once when it opens |
| `GET` | `/api/transition-previews/{slug}.mp4` | the clip, rendered on first request if missing |
| `POST` | `/api/transition-previews/build` | start a background pass over every missing clip |
| `DELETE` | `/api/transition-previews` | drop the cache so it can be rebuilt |

`POST /api/transitions/preview` (the preview inside the modal) is a separate,
on-demand render: it uses the *real* outgoing/incoming media with the exact
duration, easing, reverse and GL parameters of that one transition. The modal
shows nothing but this clip — there is no CSS approximation behind it — and the
sample is the transition alone. The renderer drops the hold of both clips
(they carry `previewTrim`), so a 5-second transition yields a 5-second clip
that opens and closes on the crossfade instead of sitting between two static
pictures.

### Symbols

Every transition carries a glyph (`transitionSymbol`), shown on the timeline
markers, on the chip and on each gallery tile. Native xfade names are read
literally — a direction becomes an arrow, a dissolve becomes `░`.

The 133 GL transitions used to collapse to a single `✦`, which made the timeline
useless for telling two of them apart. They now run through the same kind of
name-derived lookup (`GL_SYMBOL_RULES` in `src/transitionCatalog.ts`): the most
specific phrase wins — *water drop* → `≋`, *cube* → `▣`, *film burn* → `▲` — and
anything no rule recognises falls back to its group's glyph, so a GL wipe can
never read like a GL glitch. That gives 80 distinct glyphs across the catalogue.
Closely related variants still share one, exactly as *Wipe left* and *Slide
left* both read `←`.

### Progressive enhancement

The picker never waits on the backend:

1. `status` marks a transition `ready` → the tile streams the cached MP4.
2. `pending` → the tile shows the CSS approximation and only fetches on hover.
3. `unsupported`, `failed`, no FFmpeg, or backend offline → CSS approximation
   (two coloured A/B frames animated with the existing `quick-transition`
   keyframes), which stays paused until hover so a 191-tile grid does not
   animate at once.

Bumping `CACHE_VERSION` in `backend/app/transition_previews.py` invalidates
every cached clip and redraws the example frames.

## Housekeeping

The cache sits inside `CONFIG_DIR` (not `OUTPUT_DIR`), so it is covered by the
documented `/config` backup and never mixes with the user's rendered MP4s.
`Clear all` is not wired to it — use `DELETE /api/transition-previews` to force
a rebuild. Typical total size is a few megabytes for the full catalogue.
