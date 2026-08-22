# Slideshow

A self-hosted photo and video slideshow maker for Synology NAS. Projects are edited in a browser, persisted completely in SQLite, and rendered to MP4 by FFmpeg.

## Features

- Safe server-side browsing inside read-only `/photos`, `/videos`, and `/music` mounts
- Mixed photos, videos, generated text frames, drag ordering, multi-selection, and multi-line timelines
- One-click "New project" that starts a completely blank project without touching saved ones
- FFmpeg xfade catalogue, per-transition timing, random/bulk assignment, and GLSL-to-dissolve portability fallback
- Ken Burns controls with selected-item and random bulk assignment
- Timed captions, appear/disappear transitions, draggable title placement, typography, and frame backgrounds
- Multiple ordered MP3 tracks, volume/fade policies, AAC output, and looping/trimming
- Resilient media validation: 0-byte cloud-synced files are retried before failing a render, and slow NAS volumes get a generous (retryable) ffprobe timeout
- Real 480p proxy previews streamed from the backend
- 480p, 720p, 1080p, and 4K output presets with CPU/x264 and Intel Quick Sync selection
- Output overwrite protection: rendering asks for acknowledgement before replacing an existing MP4
- Shared timeline math between UI and renderer, so the estimated total always matches the rendered length
- Background jobs, live progress, downloadable output, persistent diagnostics, and render history
- Atomic, lossless SQLite save/load with normalized tables and a canonical full-project snapshot
- Production Docker image containing FFmpeg, fonts, Intel media drivers, frontend, API, and renderer

## Quick start on Synology

```bash
cp .env.example .env
# Edit host paths and PUID/PGID in .env
docker compose up -d
```

Open `http://NAS-IP:8080`. See [the DS918+ deployment guide](docs/synology-ds918.md) for permissions, Quick Sync, reverse proxy, backups, and troubleshooting. Use `compose.cpu.yaml` if the host has no `/dev/dri`.

## Local development

Frontend:

```bash
npm ci
npm run dev
```

Backend (FFmpeg must be installed for rendering):

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
CONFIG_DIR="$PWD/data/config" \
PHOTOS_DIR="$PWD/data/photos" VIDEOS_DIR="$PWD/data/videos" \
MUSIC_DIR="$PWD/data/music" OUTPUT_DIR="$PWD/data/output" \
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 --reload
```

Vite proxies relative `/api` requests to port 8000. The production container serves the API and built frontend together on port 8080.

Run checks:

```bash
npm run build
(cd backend && python -m unittest discover -s tests -v)
```

## API overview

- `GET /api/ping` — dependency-free liveness probe used by the container health check (safe to poll during renders)
- `GET /api/health` — FFmpeg and Quick Sync capabilities
- `GET|POST /api/projects` — list/create projects
- `GET|PUT|DELETE /api/projects/{id}` — lossless project persistence
- `GET /api/media/browse?root=photos&path=...` — constrained mounted-folder browser (`&folders=true` lists only directories; the `output` root is browsable in that folder-pick mode only)
- `GET /api/media/file?root=music&path=...` — stream a media file from a mounted root, e.g. MP3 preview playback in the browser
- `POST /api/projects/{id}/jobs` — enqueue `{ "kind": "preview" | "render" }`
- `GET /api/jobs` and `GET /api/jobs/{id}` — persistent job status
- `GET /api/jobs/{id}/file` — range-capable MP4 response
- `GET /api/jobs/{id}/log` — FFmpeg diagnostics

Interactive OpenAPI documentation is available at `/docs`.

## Persistence guarantee

Every click on **Save project** sends the complete project envelope to the backend. One `BEGIN IMMEDIATE` SQLite transaction updates the project, ordered media, captions, text timing/transitions, Ken Burns settings, ordered soundtracks, audio policy, typography, timeline layout, output settings, and the canonical `payload_json`. Unknown settings are retained losslessly. A failed child write rolls back the entire save. See [SQLite persistence](docs/sqlite-persistence.md).

SQLite uses WAL mode, foreign keys, a 30-second busy timeout, and schema migrations under the mounted `/config` volume. Render jobs and error messages are also persisted.

## Rendering notes

All media is first normalized to one resolution, frame rate, time base, SAR, and `yuv420p`, then composed through `xfade`. Text appearance/disappearance choices currently render with smooth alpha fades while their exact selected transition names remain stored for a later shader/text-animation renderer. Experimental GLSL frame transitions fall back to dissolve on the DS918+ portability path.

Transition durations are clamped so a transition can never overlap more than the remaining time of either clip it joins — the same rules drive the UI's estimated total time and the renderer, so the two always agree. Quick Sync availability is verified at startup with a short test encode; any `h264_qsv` failure (unsupported rate control, pixel format, or resolution — common on the DS918+) automatically retries the same composition with CPU/x264 instead of failing the job. Final renders refuse to overwrite an existing output file until acknowledged by the user.

The initial deployment assumes a trusted LAN and does not include authentication. Do not expose it directly to the internet.

## Container publication

`.github/workflows/container.yml` builds and tests pull requests and publishes signed `linux/amd64` images to:

```text
ghcr.io/birdy1974/slideshow:latest
```

Tags also produce matching version tags and commit-SHA tags.
