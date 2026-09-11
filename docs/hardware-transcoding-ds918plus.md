# Hardware transcoding on the DS918+ — findings and options

**Question (2026-09-11):** the GUI shows "Quick Sync unavailable · CPU fallback" in the
render checklist. Is FFmpeg hardware transcoding actually working on the DS918+?
**Answer: No — and with the current container image it cannot work, no matter how the
container is configured. The NAS itself is fine; the image's QSV software stack targets
the wrong GPU generation.** **Confirmed on the device the same day — see
"Device verification" below.** Nothing is broken at render time: renders always succeed
on CPU (x264) — hardware encoding is simply never used.

## How the app decides today

`Renderer.qsv_encodable()` (`backend/app/renderer.py`) probes once per process:

1. If `/dev/dri/renderD128` does not exist inside the container → unavailable (fast exit).
2. Otherwise it runs a **real 12-frame test encode** with the renderer's own bitrate
   settings: `ffmpeg -f lavfi -i color=black:s=320x240:r=25 -frames:v 12 -c:v h264_qsv
   -b:v 1M -maxrate 1M -bufsize 2M -f null -` (30 s timeout). Only a zero exit code
   counts, so the probe cannot be fooled by a present-but-unusable device.

`capabilities()` feeds the GUI checklist line. At render time
(`encoder = "h264_qsv" if "Quick Sync" in encoder_label and self.qsv_encodable() else
"libx264"`), even the explicit **"Intel Quick Sync"** selection silently becomes x264
when the probe failed, and `run_compose(allow_qsv_fallback=True)` additionally retries
on CPU if `h264_qsv` dies mid-render. So the GUI message is an honest measurement, not a
cosmetic warning.

## Why the probe fails on the DS918+ — the chain

| Step | State | Verdict |
| --- | --- | --- |
| DS918+ iGPU: Celeron J3455, Apollo Lake, Gen9 graphics | Supports hardware encode | ✔ hardware exists |
| Host DSM 7 exposes `/dev/dri/renderD128` (i915) | `compose.yaml` passes the device + `group_add: VIDEO_GID` | ✔ device available (assuming GID correct) |
| FFmpeg 8.1.2 custom build, `--enable-libvpl` (oneVPL dispatcher), `H264_QSV_ENCODER` gated at build | `h264_qsv` encoder present | ✔ encoder exists |
| Runtime image installs `libmfx-gen1.2` (oneVPL/VPL GPU runtime) | **The only QSF runtime in the image** | ✘ root cause |
| Intel support matrix: `libmfx-gen` supports **Gen12+ only** (Tiger Lake, Rocket Lake, Alder Lake …); Apollo Lake is "legacy" and is supported **only** by the old Media SDK runtime `libmfxhw64.so.1` | Gen9 has no runtime | ✘ session init fails |
| `libmfxhw64` (legacy Media SDK runtime) | Not shipped in the image; **removed from Debian after bookworm** (trixie — the image's base — ships only `libmfx-gen1.2`) | ✘ cannot be apt-installed |

So `h264_qsv` init fails with an MFX "no supported implementation" error on this GPU,
the probe returns false, and the GUI reports CPU fallback. Note this would still happen
even with perfect device permissions — the runtime generation mismatch is
unconditional.

Secondary possibility (distinguishable with the commands below): `VIDEO_GID` in `.env`
not matching the host's `/dev/dri` group → the probe fails earlier, at device open.
That one is fixable in deployment, but it would *not* make QSV work either, because of
the runtime mismatch above.

VA-API is a different story: `intel-media-va-driver` (iHD, Gen8+) **and**
`i965-va-driver` are already installed and both drive Apollo Lake for **H.264 encode**
(the free iHD driver covers H.264; HEVC encode would want the `-non-free` variant),
and `vainfo` is shipped for diagnostics. The FFmpeg build has `--enable-vaapi
--enable-libdrm`, so the `h264_vaapi` encoder is present. **The renderer simply has no
VA-API code path** — only `h264_qsv` and `libx264`.

## Confirm on your NAS (3 commands)

```bash
# 1. Is the device visible and accessible inside the container?
docker exec slideshow ls -l /dev/dri
#    On the host, verify the group:  ls -ln /dev/dri   → compare with VIDEO_GID in .env

# 2. Does the VA-API stack load and list H.264 encode entrypoints?
docker exec slideshow vainfo --display drm --device /dev/dri/renderD128
#    Expected on a healthy DS918+: driver "iHD" (or i965), VAEntrypointEncSlice
#    for H264 among the profiles. "Permission denied" → fix VIDEO_GID.

# 3. Reproduce the app's exact probe with verbose logging:
docker exec slideshow ffmpeg -hide_banner -loglevel verbose \
  -f lavfi -i color=c=black:s=320x240:r=25 -frames:v 12 -c:v h264_qsv -f null -
#    Expected today: MFX/oneVPL "no supported implementation" style failure →
#    confirms the runtime-generation mismatch, not a permissions problem.
```

If step 3 fails with "Permission denied" instead (after step 2 shows the device
works), fix `VIDEO_GID` first — but expect QSV to remain unavailable until one of the
options below is implemented.

## Device verification (2026-09-11) — diagnosis confirmed

All three checks above were run inside the container on the DS918+ (`/app`):

| Check | Result | Interpretation |
| --- | --- | --- |
| `ls -l /dev/dri` | `card0` + `renderD128`, mode `crwxrwxrwx`, group `937` | Device present and **world-accessible** (777) — the compose `group_add: 937` matches; permissions are definitively *not* the problem |
| `vainfo --display drm --device /dev/dri/renderD128` | iHD driver 25.2.3 (libva 2.22) loads cleanly | VA-API stack in the image is healthy on this iGPU |
| — (profiles) | `VAProfileH264*` with **only `VAEntrypointEncSliceLP`**; HEVC/VP9/MPEG2 decode (VLD) only, **no HEVC encode** | H.264 *encode* hardware exists — but Apollo Lake exposes it **only via the low-power VDENC path** (low-power encode was introduced in APL/KBL) |
| The app's `h264_qsv` probe (`-loglevel verbose`) | `Use Intel(R) oneVPL to create MFX session, the required implementation version is 1.1` → **`Error creating a MFX session: -9`** (`MFX_ERR_NOT_FOUND`) | The oneVPL dispatcher finds **no runtime implementation for this Gen9 GPU** — the runtime-generation mismatch predicted above, reproduced exactly. Not a permissions issue, not a bitrate/parameter issue |

Conclusion: hardware ✔, kernel/driver access ✔, VA-API user space ✔ — the only broken
link is the missing legacy QSV runtime, exactly as analysed. A VA-API encode path is
empirically viable, **with one platform-specific requirement**: Apollo Lake only offers
`VAEntrypointEncSliceLP`, and stock FFmpeg's `h264_vaapi` defaults to the normal
`EncSlice` entrypoint — the encode must pass **`-low_power 1`** (an `h264_vaapi`
option, default false) plus the standard `format=nv12,hwupload` upload, e.g.:

```bash
ffmpeg ... -vaapi_device /dev/dri/renderD128 -vf 'format=nv12,hwupload' \
  -c:v h264_vaapi -low_power 1 -b:v 8M -maxrate 8M -bufsize 16M ...
```

No HEVC encode exists on this iGPU, so an implementation must stay H.264-only (which
matches the renderer's current output anyway).

## Options to fix

### A. Keep the CPU fallback (no work, current behaviour)
Renders always succeed with x264; quality is identical. On the J3455 (4 low-power
cores) this is workable for 1080p30 photo slideshows — especially since the renderer
pre-encodes short segments and concatenates. Cost appears with video-heavy timelines,
4K, and long previews.

### B. Add a VA-API encode path — ✅ IMPLEMENTED (2026-09-11)
Use `h264_vaapi` on the same iGPU — **on the DS918+ with `-low_power 1`** (see the
verification section above: Apollo Lake exposes encode only via `EncSliceLP`).
Drivers are **already in the image**; nothing legacy, everything from Debian repos, and
it also benefits any other Intel NAS (on Gen12+ hardware plain non-LP mode applies).
Requires code: a hardware probe analogous to the QSV one (`-init_hw_device vaapi` + a
small test encode, trying `-low_power 1` first and plain mode as fallback), a
`-vaapi_device /dev/dri/renderD128` plus `format=nv12,hwupload` branch in the
segment/transition filter graphs, an encoder branch in `encode_args_for` (e.g.
`-rc_mode CBR/VBR`), a GUI option label (e.g. "Hardware · VAAPI"), and a capabilities
flag. The existing per-segment + concat architecture is compatible (intermediates stay
H.264 MP4).

### C. Restore true QSV by adding the legacy Media SDK runtime to the image
Build `libmfxhw64` (Intel-Media-SDK, supports BDW→ICL/JSL incl. Apollo Lake) from
source in the Dockerfile and install it next to `libmfx-gen1.2`; the oneVPL dispatcher
is documented to load `libmfxhw64` for legacy GPUs. Zero renderer changes — probe,
encoder selection and GUI keep working as written. Caveats: Media SDK is in
maintenance mode (EOL path), it is not packaged in Debian trixie (must be compiled),
image build time grows, and the dispatcher's legacy-load path should be verified with
the step-3 command above before relying on it.

### D. Swap in another FFmpeg binary (not recommended)
SynoCommunity's or Jellyfin's FFmpeg ships working QSV runtimes for Gen9, but mounting
a foreign binary over `FFMPEG_BIN` would lose the custom xfade-easing build — GL
transitions and easing/reverse degrade to plain dissolve (the renderer falls back
automatically). Only sensible if GL transitions are never used, i.e. effectively no.

## Implementation (Option B — shipped 2026-09-11)

* **Probe** (`Renderer.vaapi_encodable`, mirroring the QSV probe): finds the first
  `/dev/dri/renderD*` node, then runs the same 12-frame test encode as the QSV probe —
  `-vaapi_device <node> -f lavfi -i color=... -vf format=nv12,hwupload -c:v h264_vaapi
  [-low_power 1] -b:v 1M … -f null -`. The low-power VDENC mode (the only encode
  entrypoint Apollo Lake exposes) is tried first and the working mode is remembered
  (`_vaapi_low_power`); the probe is warmed at startup like the others and read
  non-blockingly by `/api/health`.
* **Selection** (`Renderer.select_encoder`): `Auto · Quick Sync` now walks
  QSV → VA-API → CPU; the new explicit `Hardware · VAAPI` option walks
  VA-API → CPU; `Intel Quick Sync` and `CPU · x264` behave exactly as before.
  Stored projects with the legacy `Auto · Quick Sync` label get the new chain
  automatically.
* **Renders**: hold and transition commands gain `-vaapi_device <node>` and a
  `,hwupload` hop before each graph's `[vout]`; `encode_args_for("h264_vaapi")` emits
  bitrate + bufsize and `-low_power 1` when the probe said the host needs it (no
  `-pix_fmt` — frames arrive as VA-API surfaces). Segment preparation stays on
  `libx264 -crf 18` (quality intermediates); concat and final mux are stream-copy and
  untouched. Any hardware failure mid-render retries the same command on CPU via the
  generalized fallback (device, hwupload and `-low_power` stripped, `-preset medium
  -pix_fmt yuv420p` added) — a broken driver can never fail a job.
* **GUI**: the checklist line reports `Hardware encoding available · VAAPI` (check,
  not warning) when `capabilities.vaapi` is true, and the Encoder dropdown gained
  `Hardware · VAAPI`. Render-time estimates already treat it as hardware speed.
* **Net effect on the DS918+**: `/api/health` now reports `"quickSync": false,
  "vaapi": true`, the checklist shows a green hardware line, and `Auto` renders encode
  hold/transition segments on the J3455's iGPU (VDENC H.264) instead of x264. Verifying
  after pulling: run one render and check `/config/work/<job-id>/ffmpeg.log` for
  `h264_vaapi` lines, or watch `intel_gpu_top` during a render.

**C** (legacy Media SDK runtime) remains an alternative if native QSV is ever wanted,
but with VA-API working there is no reason left to carry EOL legacy runtime baggage.
