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
  `/dev/dri/renderD*` node, then runs 12-frame test encodes across four
  configurations in order — **low-power CQP first** (`-low_power 1 -rc_mode CQP -qp 23`,
  the only combination Apollo Lake's `EncSliceLP` entrypoint accepts), then plain CBR,
  low-power CBR, plain CQP — and remembers the working pair (`_vaapi_low_power` +
  `_vaapi_rc_mode`), plus a human-readable `_vaapi_error` when all four fail. The
  probe is warmed at startup like the others and read non-blockingly by `/api/health`.
* **Selection** (`Renderer.select_encoder`): `Auto · Quick Sync` now walks
  QSV → VA-API → CPU; the new explicit `Hardware · VAAPI` option walks
  VA-API → CPU; `Intel Quick Sync` and `CPU · x264` behave exactly as before.
  Stored projects with the legacy `Auto · Quick Sync` label get the new chain
  automatically.
* **Renders**: hold and transition commands gain `-vaapi_device <node>` and a
  `,hwupload` hop before each graph's `[vout]`; `encode_args_for("h264_vaapi")`
  emits either bitrate+bufsize (CBR-capable hardware) or `-rc_mode CQP -qp <n>`
  (CQP-only hardware, where the GUI bitrate preset maps to a quantizer:
  20 Mbps→18 · 12→20 · 8→23 · 4→26) plus `-low_power 1` when the probe said the
  host needs it (no `-pix_fmt` — frames arrive as VA-API surfaces). Segment
  preparation stays on `libx264 -crf 18` (quality intermediates); concat and final
  mux are stream-copy and untouched. Any hardware failure mid-render retries the
  same command on CPU via the generalized fallback (device, hwupload, `-low_power`,
  `-rc_mode` and `-qp` stripped, `-preset medium -pix_fmt yuv420p` added) — a
  broken driver can never fail a job. On CQP-only hardware the bitrate preset only
  steers quality, so the file-size estimate is approximate there.
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

## Troubleshooting: "VAAPI still unavailable" (2026-09-11)

Since the probe now records **why** it failed (surfaced as `capabilities.vaapiError`
in `/api/health`, shown inline in the render checklist and in the container log), the
first step is always to read that message — it names one of the four cases below.
Otherwise run the commands in order.

### 0. Are you actually running the new image?

The VA-API path only exists in the image built after 2026-09-11. An old image's
FFmpeg has no `h264_vaapi` (the Dockerfile only gates `H264_QSV_ENCODER`, not VAAPI),
and the probe code itself is missing, so the GUI keeps reporting the CPU fallback no
matter how the device is configured.

```bash
docker compose pull            # if using the prebuilt ghcr.io image, or
docker compose build --no-cache # if building locally
docker compose up -d
docker exec slideshow ffmpeg -hide_banner -encoders 2>/dev/null | grep -i vaapi
#   must print "h264_vaapi"; if empty → you are on the old image.
```

### 1. Is the GPU passed into the container?

```bash
# on the NAS (SSH):
ls -ln /dev/dri          # note the group number of renderD128 (e.g. 937)
# inside the container:
docker exec slideshow ls -l /dev/dri
```

- Nothing shown → the `devices:` section is missing. Use the current `compose.yaml`
  (it has `devices: [/dev/dri:/dev/dri]`) — do **not** use `compose.cpu.yaml`, and add
  `--device /dev/dri:/dev/dri` if you run `docker run` by hand.
- The group differs → set `VIDEO_GID` in `.env` to the number from `ls -ln /dev/dri`
  and recreate the container (`docker compose up -d`).

### 2. Can the container open the device (permissions)?

```bash
docker exec slideshow id                      # confirm the user/group and added VIDEO_GID
docker exec slideshow vainfo                  # or: vainfo --display drm --device /dev/dri/renderD128
```

`vainfo` prints the driver and the H.264 profiles. On a healthy DS918+ expect driver
**iHD** with `VAEntrypointEncSliceLP` (low-power VDENC) on the H.264 profiles. If it
fails with "Failed to initialise VAAPI connection: -1" or "Permission denied", fix
`VIDEO_GID` (step 1) — the container user must be able to open `renderD128`.

### 3. Reproduce the app's exact probe

The fixed probe tries four configurations in order — low-power CQP first (the
DS918+ needs it), then plain CBR, low-power CBR, plain CQP. Run the DS918+
combination directly:

```bash
docker exec slideshow ffmpeg -hide_banner -loglevel error \
  -vaapi_device /dev/dri/renderD128 \
  -f lavfi -i color=c=black:s=320x240:r=25 \
  -vf format=nv12,hwupload -frames:v 12 -c:v h264_vaapi \
  -low_power 1 -rc_mode CQP -qp 23 -f null -
```

Reading the stderr here gives the definitive reason:

- `Driver does not support any RC mode compatible with selected options
  (supported modes: CQP)` → **the DS918+ case**: the low-power VDENC entrypoint
  only supports constant-QP rate control, so `-b:v/-maxrate/-bufsize` (CBR) can
  never open the encoder. This is exactly what made the probe report "unavailable"
  before — the fix above (emit `-rc_mode CQP -qp <n>` on CQP-only hardware) is
  what makes it pass now.
- `Failed to initialise VAAPI connection: -1` / `Permission denied` → device/permission
  (step 1–2).
- `Driver does not support some wanted surface format` / `no VAEntrypointEncSlice`
  → the `-low_power 1` mode matters; check the iHD driver version
  (`vainfo` header line; the image ships Debian trixie's `intel-media-va-driver`).
- `Unknown encoder 'h264_vaapi'` → old image (step 0).
- Silence + non-zero exit → bump verbosity: rerun with `-loglevel verbose`.

### 4. Confirm the app now sees it

```bash
docker exec slideshow python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/health'))['capabilities'])"
#   expect "vaapi": true (and "quickSync": false on the DS918+)
```

The health read is non-blocking, so if it reports `vaapi: false` right after a fresh
start, wait a few seconds for the startup warm-up to finish and re-read. If `vaapi`
is false but `vaapiError` is populated, that string is the reason; if it is empty the
probe is still running (the log line `VA-API probe failed on …` will appear in the
container log when it finishes).

