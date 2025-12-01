# RTMP Music Bot – Documentation

A headless 24/7 RTMP music streamer with a browser-based control room built on **Flask**, **Vue 3**, **Tailwind**, and **FFmpeg**.

The goal of this project is to make it easy to:

- Stream a continuous audio playlist to an RTMP endpoint
- Use a looped background video and optional overlay text
- Control everything from a secure web dashboard: playlist, RTMP URL, FFmpeg path, logs, etc.

---

## 1. High-Level Architecture

The project has three main layers:

### 1.1 Streaming core (`streamer_core.py`)

- Manages the **playlist** (list of audio files) and current index.
- Keeps an **approximate playback position** using monotonic time.
- Spawns and controls the **FFmpeg process**:
  - Audio input = current track
  - Video input = selected loop video (if configured)
  - Output = RTMP URL
- Automatically **advances to the next track** when the current one finishes.
- Keeps an in-memory **log buffer** with FFmpeg output and key events.
- Uses `ffprobe` (if available) to detect **track durations**, which are exposed to the UI.

A single long-lived `player_state` instance coordinates all of this and exposes methods like:

- `load_playlist`, `set_playlist_order`
- `set_video`, `set_overlay_text`, `set_rtmp`, `set_ffmpeg_path`
- `play`, `play_index`, `pause`, `stop`, `skip_next`, `seek`
- `get_state`, `get_logs`, `watcher_loop`

The `watcher_loop` runs in a background thread and monitors FFmpeg, automatically restarting or advancing the playlist as needed.

### 1.2 Web backend (`app.py`)

The Flask app is responsible for:

- Loading `.env` configuration via `python-dotenv`
- Importing and using the shared `player_state`
- Defining all HTTP routes (auth, control, file manager, uploads)
- Persisting runtime configuration to `config.json`
- Serving the static UI (`static/index.html`)

It also starts the background watcher thread at startup.

### 1.3 Frontend (`static/index.html`)

The UI is a single HTML file that uses:

- **Tailwind CSS** (via CDN) for layout and styling
- **Vue 3** (via CDN) for reactive state and API calls

From the user’s perspective, everything happens on one page:

- Login dialog
- Status header (current track, playback position, RTMP/FFmpeg status)
- Playlist table with drag-and-drop and click-to-play
- Config panels for RTMP URL, FFmpeg path, overlay text, and video
- Upload panels for audio and video
- Live “Console” view for logs

The frontend communicates with the backend using `fetch` and a Bearer token obtained from the `/login` endpoint.

---

## 2. Installation & Setup

### 2.1 Prerequisites

- **Python:** 3.10 or higher
- **FFmpeg & ffprobe:** installed on the system
  - On many Linux distros: available through the package manager
  - On Windows/macOS: download binaries and note their paths
- **RTMP endpoint:** any RTMP server or streaming provider that gives you an RTMP URL

### 2.2 Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2.3 Configure `.env`

Copy the example:

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
# Login for the web UI and API
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change_me_now

# Default RTMP URL (can be changed later from the UI)
DEFAULT_RTMP_URL=rtmp://example.com/live/streamkey

# Flask server host/port
HOST=127.0.0.1
PORT=9000

# Video size (for scaling the loop video)
VIDEO_SIZE=1920x1080

# Optional: explicit paths to ffmpeg and ffprobe
# FFMPEG_PATH=/usr/bin/ffmpeg
# FFPROBE_PATH=/usr/bin/ffprobe

# Optional: custom uploads directory
# UPLOAD_DIR=/path/to/uploads

# Optional: custom config path
# CONFIG_PATH=/path/to/rtmp_music_bot_config.json
```

### 2.4 Start the server

```bash
python app.py
```

By default the UI becomes available at:

```text
http://127.0.0.1:9000/
```

Log in with the credentials from `.env`.

---

## 3. Configuration & Persistence

The app uses two layers of configuration:

### 3.1 `.env` (static configuration)

Used for:

- `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- `DEFAULT_RTMP_URL`
- `HOST`, `PORT`
- `VIDEO_SIZE`
- `FFMPEG_PATH`, `FFPROBE_PATH`
- `UPLOAD_DIR`, `CONFIG_PATH`

You usually edit this manually before running the app.

### 3.2 `config.json` (runtime state)

On each relevant change, the app saves a JSON file (default: `config.json` next to `app.py`) with:

- `rtmp_url`
- `ffmpeg_path`
- `video_file`
- `overlay_text`
- `playlist` (list of absolute audio file paths)

On startup:

1. `load_config()` reads `config.json` (if present).
2. `apply_config()` forwards its values to the `player_state`.

This way, your previous session (playlist, RTMP URL, etc.) is restored automatically.

---

## 4. Web UI Walkthrough

### 4.1 Login

- The root route `/` serves `static/index.html`.
- The first view is a login dialog.
- On submit, the frontend calls `POST /login` with `{ "username", "password" }`.
- On success, the backend returns `{ "token" }`, which is stored in the frontend and then attached as:

  ```http
  Authorization: Bearer YOUR_SESSION_TOKEN
  ```

to every subsequent API call.

### 4.2 Status & controls

The header area typically shows:

- Current playback status (`playing`, `paused`, `stopped`, `error`)
- Current track name and index
- Elapsed and remaining time (based on `ffprobe` durations when available)
- Connection/health indicators derived from the `get_state()` response

You will also find buttons for:

- **Play / Pause / Stop**
- **Skip** (next track)
- **Seek** (jump within the current track)

### 4.3 Playlist management

The playlist pane lets you:

- View all tracks in the current queue
- See metadata such as:
  - File name
  - Duration (if detected by `ffprobe`)
  - Index and “now playing” highlight
- Reorder tracks via drag-and-drop
- Click a track to jump to it (triggers `POST /play_index`)

Behind the scenes:

- The UI calls:
  - `GET /state` to read the current playlist and index
  - `POST /playlist` to set the playlist (list of audio paths)
  - `POST /playlist/order` to reorder the playlist

### 4.4 Uploads & file manager

There are separate panels for audio and video.

#### Audio

- Upload one or more audio files (MP3, WAV, FLAC, AAC, M4A, OGG).
- Files are stored under the `UPLOAD_DIR/audio` subdirectory.
- The UI uses:
  - `GET /files/audio` to show the available files
  - `POST /upload/audio` (multipart form) to upload new ones

You can then add uploaded files to the playlist.

#### Video

- Upload a video file to serve as the looped background.
- Files are stored under `UPLOAD_DIR/video`.
- The UI uses:
  - `GET /files/video` to list video files
  - `POST /upload/video` to upload
  - `POST /video` to set the active loop video (absolute path)

### 4.5 Overlay text

- A text field in the config panel calls `POST /overlay` with `{ "text": "…" }`.
- The overlay text is stored in `player_state` and persisted in `config.json`.
- The exact FFmpeg command (with the overlay filter) is built in the streaming core.

### 4.6 Logs / console

- A “Console” pane periodically polls `GET /logs?limit=N`.
- The backend returns the latest log lines as JSON.
- The log buffer includes:
  - FFmpeg stdout/stderr
  - Major state changes (play, pause, skip, errors, etc.)

This makes it easy to debug RTMP issues and see how FFmpeg is behaving.

---

## 5. REST API Reference

All routes (except `/` and `/login`) require a Bearer token in the `Authorization` header.

### 5.1 Authentication

#### `POST /login`

**Body (JSON):**

```json
{
  "username": "admin",
  "password": "change_me_now"
}
```

**Response (200):**

```json
{
  "token": "random-session-token"
}
```

#### `POST /logout`

No body required.

Removes the current session token (if present) from the in-memory session set.

---

### 5.2 Player state & logs

#### `GET /state`

Returns a JSON snapshot of the current player state, including fields such as:

- `status`
- `rtmp_url`
- `ffmpeg_path`
- `video_file`
- `overlay_text`
- `playlist` (array of file paths)
- `current_index`
- `position` (seconds into current track, approximate)
- Per-track durations (when ffprobe data is available)

#### `GET /logs?limit=200`

**Query:**

- `limit` – maximum number of lines to return (default: 200)

**Response:**

```json
{
  "lines": [
    "[12:00:00] Starting ffmpeg…",
    "ffmpeg version …",
    "…"
  ]
}
```

---

### 5.3 Playback control

All of these accept no body unless noted, and return `{ "ok": true }` on success.

- `POST /play`
- `POST /pause`
- `POST /stop`
- `POST /skip` → advances to the next track
- `POST /seek` → body: `{ "seconds": 30 }` (float or int)

#### `POST /play_index`

**Body:**

```json
{
  "index": 3
}
```

Index is zero-based. On success returns:

```json
{
  "ok": true,
  "index": 3
}
```

---

### 5.4 Playlist & config

#### `POST /playlist`

Sets the entire playlist.

**Body:**

```json
{
  "files": [
    "/absolute/path/to/audio1.mp3",
    "/absolute/path/to/audio2.mp3"
  ]
}
```

Also triggers a `config.json` save.

#### `POST /playlist/order`

Reorders the current playlist.

**Body:**

```json
{
  "files": [
    "/absolute/path/to/audio2.mp3",
    "/absolute/path/to/audio1.mp3"
  ]
}
```

#### `POST /video`

Sets the current loop video.

```json
{
  "path": "/absolute/path/to/video.mp4"
}
```

#### `POST /overlay`

Sets overlay text:

```json
{
  "text": "24/7 Focus Music"
}
```

#### `POST /rtmp`

Sets the RTMP URL:

```json
{
  "url": "rtmp://your.server/live/streamkey"
}
```

#### `POST /ffmpeg`

Sets the FFmpeg path:

```json
{
  "path": "/usr/bin/ffmpeg"
}
```

Every one of these persists the new value to `config.json`.

---

### 5.5 File manager & uploads

#### `GET /files/audio`

Lists audio files in the audio uploads directory.

**Response:**

```json
{
  "files": [
    { "name": "track1.mp3", "path": "/absolute/path/to/track1.mp3" },
    { "name": "track2.mp3", "path": "/absolute/path/to/track2.mp3" }
  ]
}
```

#### `GET /files/video`

Same structure, but for the video uploads directory.

#### `POST /upload/audio`

Multipart form upload. Expects a file field named `file`.

- Valid extensions: `.mp3`, `.wav`, `.flac`, `.aac`, `.m4a`, `.ogg`
- Files are saved to `UPLOAD_DIR/audio`.

On success, returns JSON describing the saved file.

#### `POST /upload/video`

Same as above, but for video uploads.

- Valid extensions: `.mp4`, `.mov`, `.mkv`, `.webm`
- Files are saved to `UPLOAD_DIR/video`.

---

## 6. Deployment Notes

### 6.1 Security

- The app only uses **in-memory session tokens**. If the process restarts, all sessions are invalidated.
- There is no built-in TLS; you should place it behind an HTTPS reverse proxy.
- Restrict access to the control UI (VPN, firewall, IP allow-listing, etc.), especially if using the default admin credentials.

### 6.2 Process management

For production:

- Start the Flask app with a WSGI server (e.g. Gunicorn or uwsgi).
- Use `systemd`, `supervisord`, or another process manager to keep it running.
- Point your reverse proxy at the WSGI server.

### 6.3 Backups

- Backup the uploads directory (`UPLOAD_DIR`).
- Backup `config.json` regularly if you care about your playlist and settings.

---

## 7. Contributing

Bug reports, feature suggestions, and pull requests are welcome.

- **Bug reports:** include OS, Python version, FFmpeg version, and relevant logs.
- **Feature requests:** describe your workflow and what you are trying to achieve.
- **PRs:** please keep changes focused and documented.

---

## 8. License

See the `LICENSE` file in the main repository for licensing details.
