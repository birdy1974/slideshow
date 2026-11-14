# A slide's filter graph is one argument, and one argument has a ceiling

Reported as `Failed — [Errno 7] Argument list too long: 'ffmpeg' · 23%`.

That error is not FFmpeg's. It comes from `execve()`, before FFmpeg is running, so
nothing in the graph was parsed and no frame was decoded: the command line could not be
handed to a process at all. The percentage is how far the job got, i.e. how many slides
happened to fit.

## What the limit actually is

Linux caps **one single argument** at `MAX_ARG_STRLEN` = 32 pages = 131 072 bytes.
`ARG_MAX` (~2 MB) is the budget for the whole vector and is almost never the binding
constraint here. A render builds one slide's compositing as a single `-filter_complex`
value, so a busy slide passes 131 072 bytes while the command line as a whole is nowhere
near 2 MB — which is why the same project renders its first slides and dies on a later
one, and why "shorten the graph" is not the same fix as "raise the limit": there is no
limit left to raise.

## Where the bytes come from, measured

`collage_graph()` composites every photo through a *size ladder*: the preview quantises
zoom into buckets and bakes one still per (size, reveal-step, reveal-kind) the photo
passes through, so FFmpeg never has to evaluate a `geq` mask for a frame that shows
nothing. The baking is deliberate — it is the difference between a NAS exporting a slide
in seconds and in minutes — but it means the graph grows with the slide, not with its
photo count alone. At 1920×1080, 25 fps, one pad per photo, with a scaling entrance and a
camera move:

| photos | entrance / camera / reveal | hold | graph | levels per photo |
|---|---|---|---|---|
| 6 | swing / zoom / curtain | 5 s | 5 890 B | 1 (no ladder) |
| 6 | zoom / zoom / curtain | 5 s | 19 746 B | 15 |
| 6 | pop / telescope / iris | 8 s | 27 442 B | 24–25 |
| 12 | zoom / zoom / curtain | 12 s | 56 030 B | 15–28 |
| 9 | tumble / droste / wipe | 10 s | 68 750 B | 24–26 |

About 2 kB per level line, because each line carries the photo's whole eased motion
(`overlay=x='…'`, ~230 characters of inlined curve), its `rotate=` expression and its
`enable` windows — repeated for every level of that photo. The reported failure had
`split=82`, `split=86`: 82 levels on one photo, ~100 kB of graph, one argument over the
ceiling.

## The fix, and its limit

`app/ffmpeg_argv.py` + `Renderer._run_ffmpeg`: count the graph in **bytes**, and above
96 KiB write the same text to `work/<job>/<output-stem>-<position>.filter` and exec
`-/filter_complex <path>` instead. 96 KiB, not 128: the kernel counts the NUL, a caption's
worth of CJK is 3 bytes a character, and a threshold that decides *how* to pass a graph
should err early. The graph itself is not shortened, so the render is unchanged — see the
framemd5 test, which encodes one real collage slide both ways and requires the frames to
be identical.

Which option reads a graph from a file is a property of the binary, not of the version
string: `-filter_complex_script` was dropped by FFmpeg 9, and the generic `-/option file`
form works on the reachable builds but is documented nowhere (`-h full` lists zero `-/`
options). So the renderer asks the binary once per process, and if it answers "unrecognized
option" to both names, the command is left exactly as the builder wrote it — the render
then still fails on Linux with E2BIG, which is honest, rather than failing everywhere with
an invented option.

Covered: every render command (`_run_ffmpeg` is the one choke point, including the
hardware→CPU `cpu_variant` retries and the preview render). Not covered, because none of
them can reach the ceiling: the filmstrip, crop, text-effect and transition-preview
builders, which cap their own element counts.

## Still open: the graph should not need this

Transport is fixed; size is not. Two ways down, both of which change pixels and so need
the twin-comparison render tests as the judge:

1. **Thin the ladder under pressure** — when the graph passes the ceiling, rebuild it with
   coarser buckets (`ladder_buckets`) for the levels whose windows are one or two frames.
   A short-lived size step is invisible; 2 kB of graph per level is not.
2. **Move the ladder into `scale`** — `scale` supports `eval=frame`, so one chain could
   carry the whole size step function as a `t` expression: ~20 characters per step instead
   of ~2 kB, and `split=N` disappears. The mask still needs its baked stills, so this only
   helps the size half of each level, and it needs care to keep quantising against the
   same constant the preview's cache uses.
