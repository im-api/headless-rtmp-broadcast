import os
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Dict


class BaseState:
    def __init__(self) -> None:
        self.playlist: List[Path] = []
        self.current_index: int = 0

        # "playing", "paused", "stopped"
        self.status: str = "stopped"

        # position within current track (seconds) at last (re)start
        self.position_sec: float = 0.0
        self.last_start_monotonic: float = 0.0

        # configuration
        default_rtmp = os.getenv("DEFAULT_RTMP_URL", "rtmp://example.com/live/streamkey")
        self.video_file: Optional[Path] = None
        self.overlay_text: str = ""
        self.rtmp_url: str = default_rtmp

        # Small text files used by ffmpeg drawtext for overlay and "now playing"
        # text. These let the UI update text live without restarting the encoder
        # (the encoder uses drawtext=textfile=...:reload=1).
        self.overlay_text_file: Path = Path(os.path.join(os.path.dirname(__file__), "..", "overlay_text.txt")).resolve()
        self.now_playing_file: Path = Path(os.path.join(os.path.dirname(__file__), "..", "now_playing.txt")).resolve()
        try:
            self.overlay_text_file.write_text("", encoding="utf-8")
            self.now_playing_file.write_text("", encoding="utf-8")
        except Exception:
            # If the filesystem is read-only or unavailable, fail softly.
            pass
        self.ffmpeg_path: str = os.getenv("FFMPEG_PATH", "ffmpeg")
        # Video transport between Stream B (video+overlays) and Stream C (encoder).
        # Stream B pushes H.264 video to this UDP endpoint; Stream C reads from it.
        self.video_udp_url: str = os.getenv("VIDEO_UDP_URL", "udp://127.0.0.1:12345")

        # Separate ffmpeg process for Stream B (video + overlays)
        self.video_proc: Optional[subprocess.Popen] = None
        self._video_log_thread: Optional[threading.Thread] = None
        # encoder quality settings (UI-configurable)
        self.audio_bitrate: str = "320k"
        self.video_bitrate: str = "800k"
        self.maxrate: str = "800k"
        self.bufsize: str = "1600k"
        self.video_fps: int = 24

        # ffmpeg encoder process (RTMP) and log reader
        self.encoder_proc: Optional[subprocess.Popen] = None
        self._encoder_log_thread: Optional[threading.Thread] = None

        # audio decoder process (per-track) and pump thread
        # The decoder produces raw PCM samples that we pipe into the encoder's stdin,
        # so we can change tracks or seek without reconnecting RTMP.
        self.audio_proc: Optional[subprocess.Popen] = None
        self._audio_thread: Optional[threading.Thread] = None
        self._stop_audio_pump: bool = False

        # sync primitives (RLock to allow nested acquire in same thread)
        self.lock = threading.RLock()
        self.stop_flag = False

        # log buffer
        self._logs: List[str] = []
        self._log_max = 300

        # track durations (seconds) keyed by absolute string path
        self.track_durations: Dict[str, float] = {}

        # how many times ffmpeg failed in a row (non‑zero exit).
        # used to avoid hammering the RTMP server if it keeps rejecting us.
        self._consecutive_failures: int = 0
        # timestamp of last user-initiated seek (monotonic)
        self._recent_seek_time: float = 0.0


    # ---------- logging helpers ----------
