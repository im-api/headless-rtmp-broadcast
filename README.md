# RTMP Music Bot (Flask + Tailwind + Vue)

Headless 24/7 **RTMP music streamer** with a secure web control room.  
You run a single Python app, point it to FFmpeg, and control everything from a modern browser UI:

- Login-protected dashboard (Flask backend + Vue/Tailwind frontend)
- Drag-and-drop playlist queue with click-to-play
- Audio uploads and loop-video uploads via the browser
- Track durations via `ffprobe` and a live time bar
- Overlay text on top of the video
- Live logs from FFmpeg and the player
- Configuration persisted to a JSON file between runs

> 📚 **Full documentation** (architecture, API, and UI guide) is in the [Wiki](../../wiki).

---

## Features

- 🧩 **Secure control panel**
  - Username/password login
  - Backend issues random session tokens
  - All control routes and file operations require a valid Bearer token

- 🎵 **Playlist & playback**
  - Drag-and-drop audio queue
  - Click a track to jump to that index (`/play_index`)
  - Automatic next-track on end
  - Play / Pause / Stop / Skip / Seek controls
  - Track durations probed with `ffprobe` for an accurate progress bar

- 🎥 **Video loop background**
  - Upload a video file to be used as the looping visual
  - Adjustable video size via `VIDEO_SIZE` (e.g. `1920x1080`)

- 📝 **Overlay text**
  - Set overlay text (e.g. “Lo-Fi Beats 24/7”) from the UI
  - Persisted with the rest of the configuration

- 🧾 **Logs & observability**
  - In-memory rolling log buffer with FFmpeg output and events
  - “Console” view in the web UI

- 💾 **Config persistence**
  - On change, the app persists RTMP URL, FFmpeg path, video file, overlay text, and playlist to a JSON config file
  - On startup, the config is loaded and applied automatically

---

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** Vue 3 (CDN) + Tailwind CSS (CDN), single-page UI in `static/index.html`
- **Streaming core:** FFmpeg + ffprobe, controlled via `streamer_core.py`
- **Config:** `.env` (for secrets & paths) + `config.json` (for runtime state)

---

## Requirements

- Python **3.10+**
- `ffmpeg` and `ffprobe` installed and available
- A target RTMP endpoint (e.g. streaming server / CDN)
- Modern browser for the admin UI

Python dependencies are minimal and listed in `requirements.txt`:

```txt
flask==3.0.3
python-dotenv==1.0.1
```

Install them with:

```bash
pip install -r requirements.txt
```

---

## Quick Start

1. **Clone the repository**

   ```bash
   git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
   cd YOUR_REPO
   ```

2. **Create and activate a virtual environment (recommended)**

   **Windows (PowerShell):**

   ```powershell
   python -m venv .venv
   .\.venv\Scriptsctivate
   ```

   **macOS / Linux (bash/zsh):**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Create your `.env`**

   Copy the example file:

   ```bash
   cp .env.example .env
   ```

   Then open `.env` and set at least:

   ```dotenv
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=change_me_now

   DEFAULT_RTMP_URL=rtmp://your.rtmp.server/live/streamkey

   # Optional: absolute paths to ffmpeg / ffprobe
   # FFMPEG_PATH=/usr/bin/ffmpeg
   # FFPROBE_PATH=/usr/bin/ffprobe

   HOST=127.0.0.1
   PORT=9000
   VIDEO_SIZE=1920x1080
   ```

   You can also override:

   - `UPLOAD_DIR` – where audio/video uploads are stored (default: `./uploads`)
   - `CONFIG_PATH` – where `config.json` is written (default: `./config.json`)

5. **Run the app**

   ```bash
   python app.py
   ```

   By default the server listens on `http://127.0.0.1:9000`.

6. **Open the Control Room**

   Visit:

   ```text
   http://127.0.0.1:9000/
   ```

   Log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD` from your `.env`.

---

## Basic Usage

Once logged in:

1. **Set RTMP URL & FFmpeg path**  
   - In the configuration area, set your RTMP endpoint URL.  
   - If `ffmpeg` isn’t on your PATH, point the app to the full `ffmpeg` binary (`/ffmpeg` route via UI).

2. **Upload a loop video**  
   - Use the “Video” upload panel to upload an MP4 (or supported format).
   - Select that file as the active **video loop**.

3. **Upload audio tracks**  
   - Use the “Audio” upload panel (MP3/WAV/FLAC/AAC/M4A/OGG).
   - Add uploaded tracks to the playlist.

4. **Arrange and control playlist**  
   - Reorder tracks with drag-and-drop.
   - Click a track to jump to it.
   - Use Play / Pause / Stop / Skip / Seek buttons to control playback.

5. **Monitor logs**  
   - The “Console” panel shows FFmpeg output and system messages.
   - Useful for checking RTMP connection and debugging.

All state (RTMP URL, FFmpeg path, selected video, overlay text, and playlist) is periodically saved to `config.json` so the next run restores your previous setup.

---

## REST API (Overview)

The web UI talks to the backend via a small JSON API. The main routes are:

- `POST /login` → returns `{ "token": "…" }`
- `POST /logout`
- `GET /state` → current player state (playlist, current track, status, etc.)
- `GET /logs?limit=200` → recent log lines
- `POST /playlist` → set playlist
- `POST /playlist/order` → reorder playlist
- `POST /video` → set loop video path
- `POST /overlay` → set overlay text
- `POST /rtmp` → set RTMP URL
- `POST /ffmpeg` → set FFmpeg path
- `POST /play`, `/pause`, `/stop`, `/skip`, `/seek`
- `POST /play_index` → jump to a specific playlist index
- `GET /files/audio`, `GET /files/video` → list uploaded files
- `POST /upload/audio`, `POST /upload/video` → upload files (multipart form data)

All routes except `/`, `/login` require a valid bearer token:

```http
Authorization: Bearer YOUR_SESSION_TOKEN
```

📖 Detailed request/response examples live in the [Wiki](../../wiki).

---

## Configuration Files

- **`.env`** – static configuration and secrets (admin credentials, ports, paths).
- **`config.json`** – runtime state, automatically maintained by the app:
  - `rtmp_url`
  - `ffmpeg_path`
  - `video_file`
  - `overlay_text`
  - `playlist` (list of absolute audio paths)

You usually edit `.env` by hand and let the app manage `config.json`.

---

## Running in Production

- Put the app behind a reverse proxy (e.g. Nginx, Caddy) with HTTPS.
- Restrict access to the control UI (e.g. VPN, firewall rules).
- Run the Flask app under a process manager (e.g. `systemd`, `supervisor`, or a WSGI server like Gunicorn).

See the **Deployment** section in the [Wiki](../../wiki) for patterns and tips.

---

## Development

- UI lives in `static/index.html` and is a Vue 3 + Tailwind SPA.
- Backend lives in:
  - `app.py` – Flask app, routes, config loader
  - `streamer_core.py` – streaming core and FFmpeg orchestration

You can modify the UI directly in `static/index.html` and restart the server to see changes.

---

## License

Add your preferred open-source license (for example, MIT) as a `LICENSE` file in the repository.
