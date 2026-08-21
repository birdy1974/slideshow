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
{"status":"ok","capabilities":{"ffmpeg":true,"quickSync":true,"cpuEncoding":true}}
```

`quickSync: true` means a short test encode succeeded with the renderer's bitrate settings, not merely that the render device exists. If the probe fails, the UI reports CPU fallback and the renderer uses x264 directly. Even with an explicit Quick Sync selection, any `h264_qsv` failure at render time automatically retries the same composition with CPU/x264, so a broken QSV runtime can never fail the whole job.

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
