# Synology DS918+ deployment

The DS918+ uses an Intel Celeron J3455 (`linux/amd64`) with Intel Quick Sync. DSM 7 Container Manager can deploy the supplied Compose project.

## 1. Prepare folders

Using File Station, create:

```text
/volume1/docker/slideshow/config
/volume1/video/slideshows
```

Your existing shared folders can be used for photos, videos, and music. Input mounts are read-only inside the container. Give the deployment user read access to inputs and read/write access to `config` and `slideshows`.

If SSH is enabled, determine IDs with:

```bash
id YOUR_DSM_USERNAME
ls -ln /dev/dri
```

The common DSM group is `100`, but use the values reported by your NAS. The user must be able to access `/dev/dri/renderD128` for Quick Sync. If device permissions prevent access, use CPU encoding or grant the selected account access to the device's group.

## 2. Configure Compose

Download `compose.yaml` and copy `.env.example` to `.env`. Edit all `/volume1/...` paths and `PUID`/`PGID`.

In Container Manager:

1. Open **Project** → **Create**.
2. Select a folder containing `compose.yaml` and `.env`.
3. Build the project.
4. Open `http://NAS-IP:8080`.

CLI equivalent:

```bash
docker compose pull
docker compose up -d
docker compose logs -f slideshow
```

If `/dev/dri` is unavailable, deploy `compose.cpu.yaml` instead.

## 3. Verify capabilities

Open `http://NAS-IP:8080/api/health`. A DS918+ configured for hardware encoding should report:

```json
{"status":"ok","capabilities":{"ffmpeg":true,"quickSync":true,"vaapi":true,"cpuEncoding":true}}
```

`quickSync: true` means a short test encode succeeded with the renderer's bitrate settings, not merely that the render device exists. If the probe fails, the UI reports CPU fallback and the renderer uses x264 directly. Even with an explicit Quick Sync selection, any `h264_qsv` failure at render time automatically retries the same composition with CPU/x264, so a broken QSV runtime can never fail the whole job.

> **DS918+ note:** `quickSync` will be `false` here — the image's oneVPL runtime only
> supports Gen12+ GPUs, while the J3455's Gen9 iGPU has no QSV runtime. Since
> 2026-09-11 this is not the end of hardware encoding: the app probes VA-API on the
> same device (Apollo Lake's H.264 VDENC path) and `Auto` encoding uses it, so the
> health endpoint reports `"quickSync": false, "vaapi": true` and the GUI shows
> `Hardware encoding available · VAAPI`. See
> [hardware-transcoding-ds918plus.md](hardware-transcoding-ds918plus.md) for the full
> analysis and the implementation details.

## Storage and backups

- SQLite database: `/config/slideshow.db`
- SQLite WAL files: `/config/slideshow.db-wal` and `-shm`
- Proxy previews: `/config/previews`
- Per-job FFmpeg diagnostics: `/config/work/<job-id>/ffmpeg.log`
- Final MP4 files: `/output`

Back up the entire host `CONFIG_PATH`. Stop the container or use SQLite's online backup API before copying an active database. Never put the database on an SMB/NFS mount; use a local Btrfs volume.

## Performance recommendations

- Keep `RENDER_WORKERS=1`; the J3455 has four low-power cores.
- Use 1080p, 30 fps and 8 Mbps as the normal preset.
- Preview renders are fixed at 854×480, 24 fps and 2 Mbps.
- 4K CPU rendering can be very slow and memory-intensive.
- Large source photos are normalized by FFmpeg; pre-resizing unusually large scans reduces render time.

## Container shows "unhealthy" in Docker/Portainer

The health check polls `GET /api/ping`, which does nothing but prove the
server is alive. Older images polled `/api/health`, which runs an
`ffmpeg -version` call and can wait behind the one-time Quick Sync test
encode — during renders or the first seconds after boot that exceeded the
probe budget three times in a row, and Docker marked a fully working
container **unhealthy**. If you run an older image, either update it or
override the health check when recreating the container (Portainer cannot
edit a running container's health check) to:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/ping', timeout=5)"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 30s
```

`unhealthy` now genuinely means the API stopped answering — check
`docker logs slideshow` for a crash or a blocked event loop in that case.

## A new release does not appear in the browser

Every file Vite rebuilds is named after a hash of its contents
(`assets/index-8f2c1a4b.js`), so a new image *cannot* collide with an old bundle —
but `index.html` is the file that lists those names, its URL never changes, and
its contents do with every release. Static files used to be served without a
`Cache-Control` header at all, which leaves freshness to the browser's own
guessing: a heuristic like "10 % of how old the file looked when I cached it" can
keep an old shell in place across several deploys, and the app you see is then
the previous build — same URL, same looks, none of the changes. The shell and the
unhashed files the fallback serves (`/fonts/*.ttf`, the icons) now answer
`Cache-Control: no-cache`, which means *ask before using it*: the reply is
usually a `304`, and the file is a few kilobytes.

So the update procedure is only the update:

```bash
docker compose pull && docker compose up -d
```

…plus an ordinary reload of the page. If a feature still looks missing after
that, the image is the thing to check, not the browser — `:latest` is published
from `main` on every merge, so:

```bash
docker inspect --format '{{.Image}} {{.Created}}' slideshow
docker logs slideshow 2>&1 | head -3
```

An image created before the merge you expected is a registry or a `pull`
problem; a container started from the newer image but still showing the old UI
means something in between is caching — a reverse proxy that was told to cache
HTML is the usual suspect, and it must pass `Cache-Control` through (or not
cache `/` at all).

## The picture still looks wrong after a fix

The shell's caching trick has a sibling: a *video* URL that never changes while its file
is replaced underneath it — a gallery example clip, the scrub proxy for a movie, a
filmstrip, a finished render. Browsers answer such a URL from their own arithmetic
(roughly "a tenth of the age I saw when I cached it"), so the app can serve new bytes and
still show you the old ones. Those responses now carry `Cache-Control: no-cache` too:
ask first, take the 304, keep the range requests.

The app's own caches changed in the same direction. A generated MP4 used to be trusted
because its name matched and it was bigger than a kilobyte; now FFmpeg is asked whether
the file decodes to the end, and a truncated one is deleted and rebuilt — because a
truncated MP4 is a valid-looking header followed by unrecoverable mosaic, and once it
was on the volume it was served forever. So "I re-rendered and got exactly the same
broken result" was a real failure mode, not an impression.

### The GPU encoder that reports success and delivers confetti

If the **editor's own preview looks right but the rendered MP4 does not**, the difference
is that the preview is drawn by your browser and the file is encoded by your GPU. A VA-API
or Quick Sync driver can encode *garbage while exiting 0*: FFmpeg complains about nothing,
the file has a valid header and a plausible size, and every frame is mosaic. That is
indistinguishable from a working encoder if the only evidence a program collects is the
exit code — which is all the capability probe used to collect, so a NAS could be marked
"hardware encoding verified" while it ruined every render.

The probe no longer asks FFmpeg whether it was happy. It encodes a small SMPTE-bar
pattern, decodes the file back, and compares the pixels with the same pattern encoded by
libx264. Wrong pixels, a flat field, or an output that cannot be decoded at all means the
encoder is **not** used: the render runs on libx264, and `/api/health` and `/api/diagnose`
report which encoder was rejected and by how much (`encoderCheck`, e.g.
`h264_vaapi: rejected — encoded without error but its pixels are wrong: mean luma differs
from libx264 by 127.3 (limit 10)`

That first check was still too easy, and a real report proved it: `verified — pixels
match libx264 within 0.4` on a box whose 1080p renders were confetti. Flat bars carry no
detail to lose, and a 320-pixel frame gives a broken scaler nothing to get wrong. So the
probe now encodes **the render's own canvas, with the render's own flags** (profile,
level, GOP, colour stamps, rate control) and **moving, high-entropy detail**, and it
measures two things: how far the decoded frames sit from what went in, and how *blocky*
they came back — because a mosaic is nearly right in value and structurally wrecked,
which only a block-shaped measurement sees. Verdicts are remembered per encoder *and*
size, since that is the level at which they are true.). A render that finishes is also asked the same
question as its whole file: if the finished MP4 does not decode, it is rewritten once on
the CPU, and only if that still fails does the job report an error — rather than handing
you a broken file and calling it Complete.

### Why a "verified" encoder can still deliver confetti

An encoder being faithful and a render being correct are different claims, and the gap
between them is where this bug lived for a long time. VA-API takes frames as **NV12** —
one luma plane plus a half-resolution *interleaved* chroma plane — while the software
pipeline works in planar **YUV420P**. Hand the encoder planar frames and it does not
complain: it reads the same bytes as if they were interleaved, so every chroma sample
lands in the wrong place. The result is full-frame colour mosaic, and every check that
looks at the *container* passes — `yuv420p(tv, bt709)`, correct size, `complete: true`.
That is why a report can read "the file is valid, the metadata is right, and the picture
is garbage", and it is the shape of the low-power VDENC entrypoint on Apollo Lake
(`-low_power 1`), which accepts the upload and mis-reads it.

The rule this leaves behind: **a probe must run the graph the render runs.** The
capability check verified `format=nv12,hwupload` while the render uploaded
`format=yuv420p,hwupload`, so "verified" was true about a code path nothing executes.
Both now come from one constant, `HW_UPLOAD_GRAPH`, and a test refuses either side a
literal of its own.

To compare by hand, render twice — once with **Output → encoder = CPU (libx264)** and once
with the hardware choice. If only the hardware one is scrambled, the driver is the cause,
and leaving the encoder on CPU is the fix; the container needs no GPU passthrough at all.

One URL answers the rest without a shell:

The container has no `curl` and no `wget`, so do not look for them: the answer is a
URL, and a browser is enough. Open these three (port from your Compose mapping):

```text
http://<nas-address>:8080/api/diagnose
http://<nas-address>:8080/api/diagnose?root=output&path=movie.mp4
http://<nas-address>:8080/api/diagnose?root=videos&path=holiday.mp4
```

From a shell on the NAS, `python` is in the image (the container's own healthcheck uses
it), so the same URL works there too:

```bash
docker compose exec slideshow python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/api/diagnose').read().decode())"
```

`revision` also says which build is running, so it settles whether an update arrived at
all: if the first URL returns 404, the container is still on the previous image.

The first reports the running revision, which FFmpeg and which encoders it verified, the
font directory, free space, and what each cache holds (including `.part` files left by a
killed transcode). The others ask FFmpeg itself about one file: its streams, its colour
description, and whether it decodes to the end. Paste those three into a report and the
cause — build, encoder, volume, cache, or source — is usually visible without anyone
guessing. Until the image you are running has that route, the same facts come from
`docker exec <container> ffmpeg -i /output/movie.mp4`, which must read
`yuv420p(tv, bt709/…)`.

## Folder permissions

The container only sees what `PUID`/`PGID` can read. A shared folder can appear
in the media browser while a subfolder (often one owned by another DSM user, or
protected by an ACL) returns **No permission**. That is not a crash: use **←
Parent** and pick another directory, or grant the deployment user read access
in DSM **Control Panel → Shared Folder → Permissions** (and matching POSIX
permissions if you use SSH). After changing IDs, recreate the container so it
picks up the new `PUID`/`PGID`.

## Network safety

The initial release assumes a trusted LAN and has no built-in login. Do not expose port 8080 directly to the internet. If remote access is needed, place it behind Synology's reverse proxy with HTTPS and authentication or access it through a VPN.

For a reverse proxy, forward one origin to `http://127.0.0.1:8080`. MP4 files support normal HTTP range delivery through FastAPI, so no second preview service or browser-facing localhost URL is needed.
