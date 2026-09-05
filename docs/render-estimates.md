# Render estimates

Three numbers are shown while you assemble a project, plus a live badge while
FFmpeg runs.

## The Ready-to-render pane

| Figure | Where it comes from |
|---|---|
| **Estimated time to generate** | A measurement when one exists, otherwise a heuristic (below). While a job runs this switches to that job's live countdown. |
| **Estimated file size** | `(video bitrate + 0.192 Mbps for AAC) × total slideshow time`, plus 2 % container overhead. |
| **Estimated total slideshow time** | The shared timeline model: every clip's hold time plus every transition's duration — the same arithmetic the renderer uses, so the estimate matches the finished MP4. |

## The header badge

Appears only while a preview or render is running, next to *Save project*:

```
⟳ Rendering · 42%   about 3 min left   Joining timeline
```

- **Progress** is the backend's own figure, polled once a second.
- **The countdown** starts from the backend's `started_at` (its own clock, so a
  page refresh does not reset it) and then follows an exponential moving
  average of progress points per second. A moving average rather than a
  run-average because the stages differ wildly in speed — preparing clips is
  nothing like joining the timeline. Between polls the countdown keeps ticking
  down on its own. It reads *Estimating…* until there is enough signal, and
  *Finishing up…* once the predicted time is up but FFmpeg is not done.
- **The stage** is the backend's `stage` text, e.g. *Preparing item 3 of 12*.

## How the time-to-generate figure is learned

Nothing can predict an encode reliably — it depends on the machine, the codec,
the resolution and the media — so the app measures instead of guessing:

1. **Before the first render** it falls back to a heuristic:
   `8 s startup + 1.2 s per item + 0.9 × resolution × encoder × slideshow time`,
   where the resolution factor is 0.35 (480p), 0.6 (720p), 1 (1080p) or 3.2
   (2160p) and the encoder factor is 1 (Quick Sync) or 2.6 (CPU/x264). Previews
   are always 640×360, so they use a fixed 0.18 factor.
2. **After each finished render** it stores the wall-clock seconds per second of
   finished slideshow in `localStorage` (`slideshow.renderRate.v1`), together
   with the resolution and encoder it was taken under, separately for previews
   and full renders. Cancelled or failed runs are ignored, as is anything
   shorter than 5 seconds.
3. **The next estimate** reuses that measurement. If the resolution or encoder
   has changed since, the stored rate is scaled by the ratio of the two cost
   factors, so a 720p measurement still says something useful about a 4K
   render. Absurd measurements are clamped to 0.02–60× realtime.

The pane says which of the two it is showing: *first run · measured
afterwards* or *measured on this machine*. **Clear all** forgets the
measurement along with the saved projects, which is how you reset it after
moving to faster hardware.

The countdown lives in `src/renderEstimate.ts`; the wiring (polling, badge,
pane) is in `src/App.tsx`.
