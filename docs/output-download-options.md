# Getting the rendered MP4 onto a local device — options & GUI integration

Date: 2026-09-11 · Status: **Option 1 implemented** (sizes on job rows,
friendly preview download names, availability flag + re-render in the queue,
"MP4 ready" download row in the review panel — see below). Options 2–5 remain
advice.

Question: *"give options how output result can be made available to download to
local device, including options for gui"*. The render side is done — a finished
job is an MP4 on the NAS (`/output/<filename>` for final renders, a proxy
preview under `/config/previews` for previews). What varies is the **delivery
path** to a phone/tablet/computer and where the GUI surfaces it.

## What exists today (verified in code)

- `GET /api/jobs/{id}` returns `fileUrl: /api/jobs/{id}/file` once a job has an
  `output_path`; `job_file()` in `backend/app/main.py` serves it with Starlette
  `FileResponse` — streamed straight from disk (never buffered in memory),
  `Content-Length`, HTTP **Range** support (pause/resume, seek) on the pinned
  FastAPI, and `Content-Disposition: attachment; filename="<on-disk name>"`.
- GUI affordances: a **Download** anchor on every finished row of the Render
  queue (`RenderQueue` in `src/App.tsx`, `<a download>`), and **Download
  preview** in the FFmpeg preview modal. The review panel ("Ready to render")
  has *no* post-render CTA — after completion the user must know to look in
  the queue. The Output panel (step 04) shows only an **estimated** size.
- Lifetimes: `/output` persists (volume); **proxy previews are pruned to the
  newest one** (`Renderer._prune_previews`), and `/api/cleanup` wipes previews
  *and* job rows. So old links can 404 — clicking one today shows the
  browser's bare error page; the GUI has no "file missing" state.
- Deployment posture: trusted LAN, **no authentication** (README). The NAS
  context means the MP4 is often *already* reachable outside the app via DSM
  File Station / SMB / Synology Drive, because `/output` is a mounted share.

## Hard constraints that shape the choice

1. **Files are big.** Estimates run to tens of GB at 4K/20 Mbps. Any path that
   buffers the whole file in browser memory (fetch→blob) is a non-starter as
   the default; streaming with Range must be preserved.
2. **No auth by design.** Anything that widens reach beyond the opened browser
   tab (share links, QR codes) must be opt-in, token-scoped, expiring and
   revocable — and documented as an exception, never a new default surface.
3. **The backend does not know its public hostname** (reverse proxy on
   Synology). Absolute URLs for QR codes must be built client-side from
   `window.location.origin`.
4. **Platform quirks.** `download` attribute works well on desktop and
   Android; iOS Safari sometimes opens a player instead — a "Save to Files"
   hint belongs in the GUI. The Web Share API (`navigator.share` with files)
   is the native mobile path where available.
5. **Restart/prune semantics differ per kind**: final renders survive restarts;
   previews do not (by design). Delivery options should promise accordingly.

---

## Option 1 [IMPLEMENTED] Browser download via the jobs file route

The current `<a download href="/api/jobs/{id}/file">` path is already the
right mechanical answer: streamed, range-capable, attachment-disposition.
Cheap polish closes the rough edges:

- **Friendly names for previews**: the preview file on disk is
  `project-<id>-preview-<hash>.mp4`; pass an explicit `filename=` to
  `FileResponse` (e.g. `<project name> (preview).mp4`) so the browser's save
  dialog is meaningful. Final renders already carry the user's filename.
- **Size where it matters**: store `size_bytes` on the job row when the render
  finishes (one `stat()` in `Renderer.render`'s completion path), surface it on
  the queue row and the review panel ("MP4 ready · 1.2 GB"). Alternatively a
  HEAD endpoint, but the column is free and survives better.
- **"File missing" state**: when the anchor 404s (pruned preview, cleared
  output), mark the row and offer **Re-render** instead of a dead link. A
  `fetch(..., {method:'HEAD'})` on hover/render is enough.
- Effort: **small**. No new infrastructure.
- **Shipped as described:** `render_jobs.size_bytes` (schema migration v2, set
  from one `stat()` when a job completes), `fileAvailable` computed by
  `GET /api/jobs*` per row (a stat, cheaper and more reliable than a HEAD per
  tile — though the file route also answers `HEAD` now), previews download as
  `<project name> (preview <job>).mp4` via `Content-Disposition` (RFC 5987,
  illegal characters dashed exactly like the frontend's `safeFilename`), the
  queue row shows the real size and swaps a dead link for "File missing ·
  Re-render" (409 from the re-render still defers to the editor's overwrite
  acknowledgement), and the review panel flips to an "MP4 ready · 1.2 GB →
  Download MP4" row the moment a render finishes (also restored on reload
  from the newest completed render job).

## Option 2 [recommended] Stable per-project output link + a real completion CTA

A bookmarkable URL that always means "the latest finished render of this
project", independent of queue clutter:

- `GET /api/projects/{id}/output` — resolves the newest completed job
  (kind `render`, falling back to `preview`) and serves it with the project's
  output filename; 404 with JSON when nothing exists. ~30 lines next to
  `job_file()`, reusing the same allowed-roots check.
- The review panel becomes the home of delivery: when the polled job
  completes, the estimate card flips to a **"MP4 ready"** state — name, real
  size, date, and one primary **Save to device** button (pointing at the new
  endpoint). No digging through the queue.
- The Output panel gains a small **Last render** row (name · date · size ·
  Download) so returning to a project days later still offers the file.
- Effort: **small**. Pairs naturally with Option 1's size/missing work.

## Option 3 [recommended if phones/tablets matter] Share links + QR code

The "render on the NAS, watch on the couch" path: scan a code, the phone
downloads directly — no cables, no SMB knowledge.

- `POST /api/projects/{id}/share {ttl_hours}` → `{token, url, expires_at}`;
  `GET /api/share/{token}` streams the file **inline** (mobile browsers preview
  the video, then the user taps save; `?dl=1` forces attachment); `DELETE`
  revokes. Tokens live in a small SQLite table (survive restarts, wiped by
  neither `/api/cleanup` nor job pruning) with server-side expiry checks.
- GUI: a **"Save on a phone"** popup — QR code rendered client-side (canvas,
  one small dependency or ~40 lines of SVG), the absolute link with a copy
  button, a TTL select (1 h / 24 h / 7 d), and a revoke list. The absolute URL
  is built from `window.location.origin`, so reverse-proxy setups just work.
- **Web Share API enhancement** inside the same popup: where
  `navigator.canShare({files})` is true (iOS/Android), offer "Share via…"
  which fetches the file and hands it to the native share sheet (AirDrop,
  Messages, Save to Files). Cap it to preview-sized files — it needs the blob
  in memory.
- Security framing: a token is a capability. Opt-in per click, expiring,
  revocable, never logged in full. The README's "trusted LAN, do not expose"
  note gains one sentence: share links are the one intentional widening, and
  port-forwarding the app changes their risk profile. No auth system is
  introduced.
- Effort: **medium** (endpoint + table + popup + QR).

## Option 4 [zero code, always true on a NAS] Point at the share itself

The MP4 already sits on a Synology shared folder. Often the fastest answer for
a LAN household is the one the app already enables elsewhere:

- GUI helper card (review panel and/or Output panel): the exact path
  (`/output/<filename>.mp4`) with a **Copy path** button and one-liners per
  platform — *DSM File Station → download*, *SMB `\\NAS\output\…`*,
  *Synology Drive app*. Purely informational; the backend already knows the
  configured folder (`output.path`).
- Optionally mark it "works even while the browser is closed".
- Effort: **trivial**. Rides along with any other option.

## Option 5 [conditional enhancement] In-app download with a progress bar

`fetch` + `ReadableStream` → Blob → object URL, with a percentage in the GUI.

- Pros: in-app progress, one consistent look, works in kiosk-style browsers
  that hide download UI.
- Cons: **the whole file sits in memory** (fatal for 4K renders on a tablet),
  no pause/resume, duplicates what the browser download manager already does
  better over LAN (it uses the Range support we already serve).
- Verdict: only worth it later as an opt-in for preview-sized files, or behind
  a Service Worker. **Not the default path.**

## Not recommended

- **Email / push notifications with the link** — SMTP credentials, a config
  surface and spam risk for something a QR code solves; revisit only if asked.
- **Third-party cloud push** (Drive/Dropbox/…) — heavy dependencies against
  the self-contained posture.
- **Auto-download when the render finishes** — browsers block programmatic
  multi-GB downloads without a user gesture; a toast with a link (Option 2's
  CTA) is the honest version.

---

## GUI integration map

| Surface | Today | With Options 1+2 | With Option 3 |
|---|---|---|---|
| Review panel ("Ready to render") | estimate + render buttons only | completion state: **MP4 ready · 1.2 GB** → *Save to device* (primary), *Copy NAS path* (Option 4) | + *Save on a phone* button opening the QR popup |
| Render queue rows | Download / View log | + size & date; dead links become *file missing → Re-render* | unchanged |
| Output panel (step 04) | estimated size only | + **Last render** row (name · date · size · Download) | + Share status (active links, revoke) |
| Preview modal | Download preview | friendlier saved filename | unchanged |
| Toasts | "Render complete" | toast carries the download link | + "scan on phone" shortcut |
| New popup | — | — | QR canvas, copy link, TTL select, revoke list, Web Share where available |

## Comparison

| Option | Effort | New backend | Phone reach | Survives restart | Risk |
|---|---|---|---|---|---|
| 1 · Polish jobs route | small | none | via browser anyway | yes (renders) / no (previews, by design) | none |
| 2 · Per-project output + CTA | small | 1 endpoint | via browser anyway | yes | none |
| 3 · Share links + QR | medium | endpoints + token table | **direct** | yes (tokens in SQLite) | opt-in capability links |
| 4 · Copy-path card | trivial | none | via NAS apps | yes | none |
| 5 · In-app progress fetch | medium | none | via browser anyway | n/a | memory pressure on big files |

## Suggested phasing

1. **Option 1 polish** (friendly preview filename, `size_bytes`, missing-file
   state) — tiny, removes today's two rough edges (hash names, dead links).
2. **Option 2** (per-project endpoint + review-panel completion CTA + Output
   "Last render" row) — the visible UX win.
3. **Option 3** (QR/share popup + Web Share), with the **Option 4** copy-path
   card riding along — the phone story.

## Decisions to make before implementing

- Attachment vs inline default for final renders (recommend: attachment;
  inline only on share links, so phones can preview before saving).
- Token store (recommend: SQLite table, exempt from `/api/cleanup`) and
  default TTL (recommend 24 h, selectable).
- Whether the Output panel's *Clear output* should also warn about active
  share links (recommend: yes, one line in the confirm dialog).
