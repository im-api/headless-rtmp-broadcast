#!/usr/bin/env python3
"""
RTMP music bot with secure web UI (Flask + Tailwind + Vue).

- Config via .env (ADMIN_USERNAME, ADMIN_PASSWORD, DEFAULT_RTMP_URL, HOST, PORT,
  VIDEO_SIZE, FFMPEG_PATH, FFPROBE_PATH, UPLOAD_DIR, CONFIG_PATH)
- Username/password login -> server issues random session token (in memory)
- All control endpoints require Bearer token (session) in Authorization header
- Web UI (Tailwind + Vue) served by the same Flask app
- Supports uploading audio tracks and loop video to server-side "uploads" folder
- Keeps a console log buffer (ffmpeg output + actions)
- Persists current configuration to a simple JSON file for next run
- Exposes /play_index for jumping to a specific track
"""
import os
import json
from pathlib import Path

PROFILES_PATH = Path(__file__).with_name("rtmp_profiles.json")


def _load_rtmp_profiles():
    """Load saved RTMP profiles from disk.

    Profiles are stored as a list of dicts:
    {"name": str, "url": str, "audio_bitrate": str, "video_bitrate": str,
     "maxrate": str, "bufsize": str, "video_fps": int}
    """
    try:
        if PROFILES_PATH.exists():
            with PROFILES_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_rtmp_profiles(profiles):
    try:
        with PROFILES_PATH.open("w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2)
    except Exception:
        pass

import secrets
import threading
from typing import Set

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

from streamer_core import player_state

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change_me_now")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))
UPLOAD_AUDIO_DIR = os.path.join(UPLOAD_DIR, "audio")
UPLOAD_VIDEO_DIR = os.path.join(UPLOAD_DIR, "video")
os.makedirs(UPLOAD_AUDIO_DIR, exist_ok=True)
os.makedirs(UPLOAD_VIDEO_DIR, exist_ok=True)

CONFIG_PATH = os.getenv("CONFIG_PATH", os.path.join(BASE_DIR, "config.json"))
_config_lock = threading.Lock()

ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"}
ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}

# In-memory session tokens
ACTIVE_SESSIONS: Set[str] = set()

# Flask app setup
app = Flask(__name__, static_folder="static", static_url_path="/static")


def start_watcher():
    """
    Start ffmpeg watcher thread.
    """
    t = threading.Thread(target=player_state.watcher_loop, daemon=True)
    t.start()


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config() -> None:
    """Persist current state (rtmp, ffmpeg_path, video, overlay, playlist) as JSON."""
    with _config_lock:
        state = player_state.get_state()
        data = {
            "rtmp_url": state.get("rtmp_url"),
            "ffmpeg_path": state.get("ffmpeg_path"),
            "video_file": state.get("video_file"),
            "overlay_text": state.get("overlay_text"),
            "playlist": state.get("playlist") or [],
            "audio_bitrate": state.get("audio_bitrate"),
            "video_bitrate": state.get("video_bitrate"),
            "maxrate": state.get("maxrate"),
            "bufsize": state.get("bufsize"),
            "video_fps": state.get("video_fps"),
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Config] Error saving config: {e}")


            print(f"[Config] Error saving config: {e}")


def apply_config(cfg: dict) -> None:
    """Apply loaded config to player_state on startup."""
    if not cfg:
        return
    rtmp = cfg.get("rtmp_url")
    ffmpeg_path = cfg.get("ffmpeg_path")
    video_file = cfg.get("video_file")
    overlay = cfg.get("overlay_text")
    playlist = cfg.get("playlist") or []

    if rtmp:
        player_state.set_rtmp(rtmp)
    if ffmpeg_path:
        player_state.set_ffmpeg_path(ffmpeg_path)
    if video_file:
        player_state.set_video(video_file)
    if overlay:
        player_state.set_overlay_text(overlay)
    if playlist:
        player_state.load_playlist(playlist)

    encoder_cfg = {
        k: cfg.get(k)
        for k in ("audio_bitrate", "video_bitrate", "maxrate", "bufsize", "video_fps")
        if cfg.get(k) is not None
    }
    if encoder_cfg:
        player_state.set_encoder_settings(encoder_cfg)




@app.route("/")
def index():
    """
    Serve the main admin UI.
    """
    return send_from_directory(app.static_folder, "index.html")


def _get_token_from_header() -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return auth.split(" ", 1)[1]


def _require_session() -> bool:
    token = _get_token_from_header()
    if not token or token not in ACTIVE_SESSIONS:
        return False
    return True


def _unauthorized():
    return jsonify({"detail": "Unauthorized"}), 401


# ========== AUTH ROUTES ==========


@app.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")

    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return jsonify({"detail": "Invalid username or password"}), 401

    token = secrets.token_urlsafe(32)
    ACTIVE_SESSIONS.add(token)
    return jsonify({"token": token})


@app.post("/logout")
def logout():
    token = _get_token_from_header()
    if token:
        ACTIVE_SESSIONS.discard(token)
    return jsonify({"ok": True})


# ========== CONTROL ROUTES (SESSION REQUIRED) ==========


@app.get("/state")
def get_state():
    if not _require_session():
        return _unauthorized()
    state = player_state.get_state()
    # Attach RTMP profiles (if any)
    state["rtmp_profiles"] = _load_rtmp_profiles()
    return jsonify(state)


@app.get("/logs")
def get_logs():
    if not _require_session():
        return _unauthorized()
    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        limit = 200
    logs = player_state.get_logs(limit)
    return jsonify({"lines": logs})


@app.post("/playlist")
def set_playlist():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    files = data.get("files") or []
    if not isinstance(files, list):
        return jsonify({"detail": "files must be a list"}), 400
    player_state.load_playlist(files)
    save_config()
    return jsonify({"ok": True, "playlist": files})


@app.post("/playlist/order")
def set_playlist_order():
    """
    Accepts a list of file paths and reorders playlist accordingly.
    """
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    files = data.get("files") or []
    if not isinstance(files, list):
        return jsonify({"detail": "files must be a list"}), 400
    player_state.set_playlist_order(files)
    save_config()
    return jsonify({"ok": True, "playlist": files})


@app.post("/video")
def set_video():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"detail": "path is required"}), 400
    player_state.set_video(path)
    save_config()
    return jsonify({"ok": True, "video": path})


@app.post("/encoder_settings")
def encoder_settings():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    allowed = ("audio_bitrate", "video_bitrate", "maxrate", "bufsize", "video_fps")
    encoder_cfg = {k: data[k] for k in allowed if k in data}
    if not encoder_cfg:
        return jsonify({"detail": "no encoder settings provided"}), 400
    player_state.set_encoder_settings(encoder_cfg)
    save_config()
    return jsonify({"ok": True, **encoder_cfg})


@app.post("/overlay")
def set_overlay():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    player_state.set_overlay_text(text)
    save_config()
    return jsonify({"ok": True, "text": text})


@app.post("/rtmp")
def set_rtmp():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    url = data.get("url")
    if not url:
        return jsonify({"detail": "url is required"}), 400
    player_state.set_rtmp(url)
    save_config()
    return jsonify({"ok": True, "url": url})


@app.post("/ffmpeg")
def set_ffmpeg():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"detail": "path is required"}), 400
    player_state.set_ffmpeg_path(path)
    save_config()
    return jsonify({"ok": True, "ffmpeg_path": path})



@app.get("/profiles")
def list_profiles():
    if not _require_session():
        return _unauthorized()
    return jsonify({"profiles": _load_rtmp_profiles()})


@app.post("/profiles/save")
def save_profile():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"detail": "name is required"}), 400
    profiles = _load_rtmp_profiles()
    # Build profile using explicit values if provided, otherwise current state
    profile = {
        "name": name,
        "url": data.get("url") or player_state.rtmp_url,
        "audio_bitrate": data.get("audio_bitrate") or player_state.audio_bitrate,
        "video_bitrate": data.get("video_bitrate") or player_state.video_bitrate,
        "maxrate": data.get("maxrate") or player_state.maxrate,
        "bufsize": data.get("bufsize") or player_state.bufsize,
        "video_fps": data.get("video_fps") or player_state.video_fps,
    }
    replaced = False
    for idx, p in enumerate(profiles):
        if p.get("name") == name:
            profiles[idx] = profile
            replaced = True
            break
    if not replaced:
        profiles.append(profile)
    _save_rtmp_profiles(profiles)
    return jsonify({"ok": True, "profiles": profiles})


@app.post("/profiles/delete")
def delete_profile():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"detail": "name is required"}), 400
    profiles = [p for p in _load_rtmp_profiles() if p.get("name") != name]
    _save_rtmp_profiles(profiles)
    return jsonify({"ok": True, "profiles": profiles})


@app.post("/profiles/apply")
def apply_profile():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"detail": "name is required"}), 400
    profiles = _load_rtmp_profiles()
    profile = next((p for p in profiles if p.get("name") == name), None)
    if not profile:
        return jsonify({"detail": "profile not found"}), 404
    # Apply to player_state: RTMP URL and encoder settings
    with player_state.lock:
        url = profile.get("url")
        if url:
            player_state.set_rtmp(url)
        enc_cfg = {
            "audio_bitrate": profile.get("audio_bitrate", player_state.audio_bitrate),
            "video_bitrate": profile.get("video_bitrate", player_state.video_bitrate),
            "maxrate": profile.get("maxrate", player_state.maxrate),
            "bufsize": profile.get("bufsize", player_state.bufsize),
            "video_fps": profile.get("video_fps", player_state.video_fps),
        }
        player_state.set_encoder_settings(enc_cfg)
        save_config()
    return jsonify({"ok": True, "profile": profile})

@app.post("/play")
def play():
    if not _require_session():
        return _unauthorized()
    player_state.play()
    return jsonify({"ok": True})


@app.post("/play_index")
def play_index():
    """
    Jump to specific playlist index.
    """
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    index = data.get("index")
    try:
        index = int(index)
    except (TypeError, ValueError):
        return jsonify({"detail": "index must be an integer"}), 400
    player_state.play_index(index)
    return jsonify({"ok": True, "index": index})


@app.post("/pause")
def pause():
    if not _require_session():
        return _unauthorized()
    player_state.pause()
    return jsonify({"ok": True})


@app.post("/stop")
def stop():
    if not _require_session():
        return _unauthorized()
    player_state.stop()
    return jsonify({"ok": True})


@app.post("/skip")
def skip():
    if not _require_session():
        return _unauthorized()
    player_state.skip_next()
    save_config()
    return jsonify({"ok": True})


@app.post("/seek")
def seek():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    seconds = data.get("seconds")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return jsonify({"detail": "seconds must be a number"}), 400
    player_state.seek(seconds)
    return jsonify({"ok": True, "seconds": seconds})


# ========== FILE MANAGER ROUTES (SESSION REQUIRED) ==========


@app.get("/files/audio")
def list_audio_files():
    if not _require_session():
        return _unauthorized()
    files = []
    if os.path.exists(UPLOAD_AUDIO_DIR):
        for name in sorted(os.listdir(UPLOAD_AUDIO_DIR)):
            # hide dotfiles such as .gitignore from the UI
            if name.startswith('.'):
                continue
            full = os.path.join(UPLOAD_AUDIO_DIR, name)
            if os.path.isfile(full):
                files.append({"name": name, "path": full})
    return jsonify({"files": files})



@app.get("/files/video")
def list_video_files():
    if not _require_session():
        return _unauthorized()
    files = []
    if os.path.exists(UPLOAD_VIDEO_DIR):
        for name in sorted(os.listdir(UPLOAD_VIDEO_DIR)):
            if name.startswith('.'):
                continue
            full = os.path.join(UPLOAD_VIDEO_DIR, name)
            if os.path.isfile(full):
                files.append({"name": name, "path": full})
    return jsonify({"files": files})


@app.post("/files/audio/delete")
def delete_audio_file():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"detail": "path is required"}), 400

    base = Path(UPLOAD_AUDIO_DIR).resolve()
    target = Path(path)
    try:
        resolved = target.resolve()
    except Exception:
        return jsonify({"detail": "invalid path"}), 400

    if base not in resolved.parents:
        return jsonify({"detail": "forbidden"}), 403

    if resolved.exists() and resolved.is_file():
        resolved.unlink()
    return jsonify({"ok": True})

@app.post("/files/video/delete")
def delete_video_file():
    if not _require_session():
        return _unauthorized()
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"detail": "path is required"}), 400

    base = Path(UPLOAD_VIDEO_DIR).resolve()
    target = Path(path)
    try:
        resolved = target.resolve()
    except Exception:
        return jsonify({"detail": "invalid path"}), 400

    if base not in resolved.parents:
        return jsonify({"detail": "forbidden"}), 403

    if resolved.exists() and resolved.is_file():
        resolved.unlink()
    return jsonify({"ok": True})



# ========== UPLOAD ROUTES (SESSION REQUIRED) ==========


@app.post("/upload/audio")
def upload_audio():
    if not _require_session():
        return _unauthorized()
    if "file" not in request.files:
        return jsonify({"detail": "file is required"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"detail": "empty filename"}), 400
    filename = secure_filename(f.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXT:
        return jsonify({"detail": "unsupported audio type"}), 400
    os.makedirs(UPLOAD_AUDIO_DIR, exist_ok=True)
    dest = os.path.join(UPLOAD_AUDIO_DIR, filename)
    f.save(dest)
    return jsonify({"ok": True, "path": dest})


@app.post("/upload/video")
def upload_video():
    if not _require_session():
        return _unauthorized()
    if "file" not in request.files:
        return jsonify({"detail": "file is required"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"detail": "empty filename"}), 400
    filename = secure_filename(f.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_VIDEO_EXT:
        return jsonify({"detail": "unsupported video type"}), 400
    os.makedirs(UPLOAD_VIDEO_DIR, exist_ok=True)
    dest = os.path.join(UPLOAD_VIDEO_DIR, filename)
    f.save(dest)
    return jsonify({"ok": True, "path": dest})


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "9000"))

    # Apply persisted config if exists
    cfg = load_config()
    apply_config(cfg)

    # Start watcher thread once at startup
    start_watcher()

    print(f"[App] Starting Flask server on {host}:{port}")
    app.run(host=host, port=port, threaded=True)