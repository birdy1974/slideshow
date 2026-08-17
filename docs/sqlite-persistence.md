# SQLite persistence contract

> This contract is implemented by `backend/app/database.py`. The browser also keeps a local recovery snapshot, but SQLite is the authoritative project store.

Saving a project must happen in one SQLite transaction. A failed write must roll back the entire transaction so a project can never be partially saved.

## Tables

### `projects`

- `id`, `schema_version`, `name`
- `random_order`
- `timeline_rows`, `timeline_zoom`
- `payload_json` containing the canonical, lossless project envelope
- `revision`, `created_at`, `updated_at`

### `media_items`

One ordered row per photo, video, or generated title frame.

- identity, project foreign key, and sort position
- source name, mounted path, media type, and generated-frame background
- clip duration
- Ken Burns effect
- outgoing media transition and transition duration
- caption text and overlay/frame mode
- caption start and end times
- caption appear/disappear effects and their durations
- caption X/Y position

### `text_defaults`

- font family and size
- colour
- bold, italic, and underline flags

### `audio_tracks`

One ordered row per soundtrack.

- identity, project foreign key, and sort position
- mounted path and filename
- probed duration
- per-track settings added by later interface revisions

### `audio_settings`

- short-audio policy
- music volume
- fade-out enabled and duration

### `output_settings`

- resolution
- frame rate
- bitrate
- encoder selection and fallback policy
- output path and filename

### `render_jobs`

- preview/render kind, status, stage, and progress
- persisted output path and error message
- creation, start, and completion timestamps
- render settings snapshot

## Save behavior

1. Validate every mounted path against configured media roots.
2. Begin an immediate transaction.
3. Upsert the project and single-row settings.
4. Replace/reconcile ordered media and audio child rows.
5. Update the project timestamp.
6. Commit.
7. Return the saved revision and timestamp to the interface.

Schema changes will use numbered migrations and the database will be stored in the Compose-mounted `/config` volume. SQLite WAL mode, foreign keys, a busy timeout, and periodic backups will be enabled in the backend milestone.
