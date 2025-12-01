#!/usr/bin/env python3
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Dict

# Video size can be overridden from environment
VIDEO_SIZE = os.getenv("VIDEO_SIZE", "1920x1080")

# Determine ffprobe path:
# - If FFPROBE_PATH is set in env, use it.
# - Otherwise, if FFMPEG_PATH is an absolute/explicit path, try to derive ffprobe
#   from the same directory (ffprobe.exe or ffprobe).
_ffmpeg_env = os.getenv("FFMPEG_PATH", "ffmpeg")
_ffprobe_env = os.getenv("FFPROBE_PATH")

if _ffprobe_env:
    FFPROBE_PATH = _ffprobe_env
else:
    try:
        ff = Path(_ffmpeg_env)
        if ff.is_absolute():
            # On Windows this will typically map ffmpeg.exe -> ffprobe.exe
            probe_name = "ffprobe" + (ff.suffix if ff.suffix else "")
            FFPROBE_PATH = str(ff.with_name(probe_name))
        else:
            FFPROBE_PATH = "ffprobe"
    except Exception:
        FFPROBE_PATH = "ffprobe"


class PlayerState:
    """
    Core streaming state and control logic.

    Responsibilities:
    - Maintain playlist and current track index
    - Track playback position (approximate, using monotonic time)
    - Launch / restart ffmpeg with correct arguments
    - Automatically advance to next track when ffmpeg exits
    - Keep an in-memory log buffer (for UI)
    - Store track durations via ffprobe for better UI (time bar)
    """

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
        self.ffmpeg_path: str = os.getenv("FFMPEG_PATH", "ffmpeg")
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

    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        with self.lock:
            self._logs.append(line)
            if len(self._logs) > self._log_max:
                # keep last _log_max entries
                self._logs = self._logs[-self._log_max :]

    def get_logs(self, limit: int = 200) -> List[str]:
        with self.lock:
            if limit <= 0 or limit >= len(self._logs):
                return list(self._logs)
            return self._logs[-limit:]

    # ---------- ffprobe durations ----------

    def _probe_duration(self, path: Path) -> Optional[float]:
        """
        Use ffprobe to get track duration in seconds.
        Returns None if ffprobe not available or fails.
        """
        cmd = [
            FFPROBE_PATH,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ]
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
            val = out.strip()
            if not val:
                return None
            dur = float(val)
            if dur < 0:
                return None
            return dur
        except Exception as e:
            self._append_log(f"ffprobe failed for {path}: {e}")
            return None

    def _update_durations_for_playlist_unlocked(self) -> None:
        """
        Ensure track_durations has entries for all playlist files.
        Caller must hold self.lock.
        """
        new_map: Dict[str, float] = {}
        for p in self.playlist:
            spath = str(p)
            if spath in self.track_durations:
                new_map[spath] = self.track_durations[spath]
            else:
                dur = self._probe_duration(p)
                if dur is not None:
                    new_map[spath] = dur
        self.track_durations = new_map

    # ---------- internal helpers ----------

    def _build_ffmpeg_cmd(self, audio_file: Path, start_sec: float) -> list:
        """
        Build ffmpeg command for current config.
        """
        vf = f"scale={VIDEO_SIZE},format=yuv420p"
        if self.overlay_text:
            # Very basic overlay; for complex text, escape properly.
            safe_text = (
                self.overlay_text.replace(":", r"\\:")
                .replace("'", r"\\'")
                .replace("%", r"\\%")
            )
            vf += (
                f",drawtext=text='{safe_text}':"
                "x=20:y=50:fontsize=48:fontcolor=white:box=1:boxcolor=black@0.5"
            )

        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            # Audio (playlist track)
            "-re",
            "-ss",
            str(start_sec),
            "-i",
            str(audio_file),
            # Video (looped)
            "-stream_loop",
            "-1",
            "-i",
            str(self.video_file),
            # Video encoding
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            # Audio encoding
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            # Bitrate control
            "-b:v",
            "800k",
            "-maxrate",
            "800k",
            "-bufsize",
            "1600k",
            "-threads",
            "1",
            # Mapping
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            "-vf",
            vf,
            # End when audio ends (for auto-next logic)
            "-shortest",
            # Output
            "-f",
            "flv",
            self.rtmp_url,
        ]
        return cmd

    def _kill_encoder_unlocked(self) -> None:
        """
        Terminate the long-lived ffmpeg encoder (RTMP) process if running.
        Caller must hold self.lock.
        """
        if self.encoder_proc and self.encoder_proc.poll() is None:
            self._append_log("Terminating ffmpeg encoder process")
            try:
                self.encoder_proc.terminate()
                self.encoder_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._append_log("Encoder did not terminate in time; killing")
                self.encoder_proc.kill()
        self.encoder_proc = None
        self._encoder_log_thread = None

    def _kill_audio_unlocked(self) -> None:
        """
        Terminate the per-track audio decoder process and stop the pump thread.
        Caller must hold self.lock.
        """
        # Signal the pump thread to stop
        self._stop_audio_pump = True
        if self.audio_proc and self.audio_proc.poll() is None:
            self._append_log("Terminating audio decoder process")
            try:
                self.audio_proc.terminate()
                self.audio_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._append_log("Audio decoder did not terminate in time; killing")
                self.audio_proc.kill()
        self.audio_proc = None
        # The pump thread is daemon=True and will exit shortly after _stop_audio_pump.

    def _kill_ffmpeg_unlocked(self) -> None:
        """
        Terminate encoder + audio decoder if running. Caller must hold self.lock.
        """
        self._kill_audio_unlocked()
        self._kill_encoder_unlocked()


    def _ffmpeg_log_reader(self, proc: subprocess.Popen) -> None:
        """
        Read ffmpeg stdout/stderr and append to log buffer.
        """
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                self._append_log(f"[ffmpeg] {line}")
    def _ffmpeg_log_reader_bytes(self, proc: subprocess.Popen) -> None:
        """
        Read ffmpeg stdout/stderr (bytes) and append to log buffer.
        Used by the long-lived encoder process where stdin is binary.
        """
        assert proc.stdout is not None
        for raw in iter(lambda: proc.stdout.readline(), b""):
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self._append_log(f"[ffmpeg] {line}")

    def _start_encoder_unlocked(self) -> None:
        """
        Ensure the long-lived ffmpeg encoder (RTMP) process is running.
        It takes raw PCM on stdin and the looped video as input.
        Caller must hold self.lock.
        """
        # If already running, nothing to do
        if self.encoder_proc and self.encoder_proc.poll() is None:
            return

        # Any previous encoder state is cleared
        self._kill_encoder_unlocked()

        if self.video_file is None:
            self._append_log("No video file, cannot start encoder")
            return
        if not self.rtmp_url:
            self._append_log("No RTMP URL, cannot start encoder")
            return

        vf = f"scale={VIDEO_SIZE},format=yuv420p"
        if self.overlay_text:
            # Basic escaping for drawtext
            safe_text = (
                self.overlay_text.replace(":", r"\\:")
                .replace("'", r"\\'")
                .replace("%", r"\\%")
            )
            vf += (
                f",drawtext=text='{safe_text}':"
                "x=20:y=50:fontsize=48:fontcolor=white:box=1:boxcolor=black@0.5"
            )

        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            # Audio from stdin (raw PCM)
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-i",
            "pipe:0",
            # Video loop
            "-stream_loop",
            "-1",
            "-i",
            str(self.video_file),
            # Encoding settings
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
                        "-pix_fmt",
            "yuv420p",
            "-r",
            str(self.video_fps),
            "-c:a",
            "aac",
            "-b:a",
            self.audio_bitrate,
            "-b:v",
            self.video_bitrate,
            "-maxrate",
            self.maxrate,
            "-bufsize",
            self.bufsize,
            
"-threads",
            "1",
            # Mapping
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            "-vf",
            vf,
            # Output
            "-f",
            "flv",
            self.rtmp_url,
        ]
        self._append_log("Launching encoder ffmpeg: " + " ".join(map(str, cmd)))
        try:
            self.encoder_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except FileNotFoundError:
            self._append_log(f"ERROR: ffmpeg executable not found: {self.ffmpeg_path}")
            self.encoder_proc = None
            self.status = "stopped"
            return

        # start log reader thread for encoder
        if self.encoder_proc.stdout is not None:
            self._encoder_log_thread = threading.Thread(
                target=self._ffmpeg_log_reader_bytes,
                args=(self.encoder_proc,),
                daemon=True,
            )
            self._encoder_log_thread.start()

        # reset failure counter each time encoder successfully starts
        self._consecutive_failures = 0

    def _start_audio_unlocked(self, start_sec: float = 0.0) -> None:
        """
        Start/restart the per-track audio decoder and pump thread.
        Caller must hold self.lock.
        """
        if not self.playlist:
            self._append_log("No playlist, cannot start audio decoder")
            return
        if self.current_index < 0 or self.current_index >= len(self.playlist):
            self.current_index = 0

        audio_file = self.playlist[self.current_index]

        if self.encoder_proc is None or self.encoder_proc.poll() is not None:
            self._append_log("Encoder is not running; cannot start audio decoder")
            return

        # Stop any previous decoder
        self._kill_audio_unlocked()

        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            # Throttle decoder to real-time; encoder will just consume as we feed
            "-re",
            "-ss",
            str(max(0.0, start_sec)),
            "-i",
            str(audio_file),
            "-vn",
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "pipe:1",
        ]
        self._append_log("Launching audio decoder: " + " ".join(map(str, cmd)))
        try:
            self.audio_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except FileNotFoundError:
            self._append_log(f"ERROR: ffmpeg executable not found: {self.ffmpeg_path}")
            self.audio_proc = None
            self.status = "stopped"
            return

        self._stop_audio_pump = False

        if self.audio_proc.stdout is not None and self.encoder_proc and self.encoder_proc.stdin:
            self._audio_thread = threading.Thread(
                target=self._pump_audio_loop,
                daemon=True,
            )
            self._audio_thread.start()

    def _pump_audio_loop(self) -> None:
        """Pump raw PCM from the audio decoder into the encoder stdin.
        This runs in a daemon thread.
        """
        while True:
            with self.lock:
                dec = self.audio_proc
                enc = self.encoder_proc
                stop_flag = self._stop_audio_pump
            if stop_flag or dec is None or enc is None:
                break

            out = dec.stdout
            inn = enc.stdin
            if out is None or inn is None:
                break

            try:
                chunk = out.read(4096)
            except Exception:
                break

            if not chunk:
                # Decoder finished (end of track or failure)
                with self.lock:
                    # If EOF happens very shortly after a seek, treat it as
                    # a failed seek rather than a natural end-of-track.
                    recent_seek = False
                    if getattr(self, "_recent_seek_time", 0.0):
                        if time.monotonic() - self._recent_seek_time < 2.0:
                            recent_seek = True
                    natural_end = (
                        not self._stop_audio_pump
                        and self.status == "playing"
                        and self.playlist
                        and not recent_seek
                    )
                if natural_end:
                    with self.lock:
                        self._advance_track_unlocked(loop_queue=True)
                # whether natural or not, stop this pump loop; a new one
                # will be started by the pipeline if needed
                break

            try:
                inn.write(chunk)
                inn.flush()
            except BrokenPipeError:
                # Encoder died or RTMP closed
                break
            except BrokenPipeError:
                    # Encoder died or RTMP closed
                    break

    def _start_pipeline_unlocked(self, start_sec: float = 0.0) -> None:
        """
        Ensure encoder is running and start audio decoder from given position.
        Caller must hold self.lock.
        """
        if not self.playlist:
            self._append_log("No playlist, cannot start pipeline")
            self.status = "stopped"
            return
        if self.video_file is None:
            self._append_log("No video file, cannot start pipeline")
            self.status = "stopped"
            return

        # Start or reuse encoder
        self._start_encoder_unlocked()
        if self.encoder_proc is None or self.encoder_proc.poll() is not None:
            # Encoder failed to start
            self.status = "error"
            return

        # Start audio decoder for the current track
        self._start_audio_unlocked(start_sec)

        # Update timing for UI position
        self.last_start_monotonic = time.monotonic()
        self.position_sec = max(0.0, start_sec)
        self.status = "playing"

    def _restart_full_pipeline_unlocked(self, start_sec: float = 0.0) -> None:
        """
        Restart encoder + audio decoder from a given position.
        Used when changing RTMP URL, video loop, or overlay text –
        operations that require a fresh ffmpeg encoder.
        Caller must hold self.lock.
        """
        self._kill_ffmpeg_unlocked()
        self._start_pipeline_unlocked(start_sec)


    def _start_ffmpeg_unlocked(self, start_sec: float = 0.0) -> None:
        """
        Compatibility wrapper kept for older call sites.
        In the new architecture this simply (re)starts the encoder+audio
        pipeline without reconnecting RTMP when possible.
        Caller must hold self.lock.
        """
        self._start_pipeline_unlocked(start_sec)


    
    def _advance_track_unlocked(self, *, loop_queue: bool = True) -> None:
        """
        Advance to the next track in the playlist and restart ONLY the audio decoder.

        Design goals:
        - The long‑lived encoder (RTMP, i.e. Stream C) should *not* be restarted
          when changing tracks. We only touch the audio decoder (Stream A) here.
        - When we reach the end of the playlist and ``loop_queue`` is True,
          we wrap around to the first track (queue loop).
        Caller must hold ``self.lock``.
        """
        if not self.playlist:
            self._append_log("Advance requested but playlist is empty; stopping")
            self.status = "stopped"
            return

        # Normalise current_index so it is always in a valid range
        if self.current_index < 0 or self.current_index >= len(self.playlist):
            self.current_index = 0

        last_index = len(self.playlist) - 1
        if self.current_index == last_index:
            if not loop_queue:
                # End of playlist and looping disabled – stop playback cleanly
                self._append_log("Reached end of playlist; stopping (no loop)")
                self._kill_audio_unlocked()
                self.status = "stopped"
                self.position_sec = 0.0
                return
            next_index = 0
        else:
            next_index = self.current_index + 1

        self.current_index = next_index
        self.position_sec = 0.0
        self._append_log(f"Advancing to next track (index {self.current_index})")

        # Make sure encoder (Stream C) is running; if it is already alive,
        # _start_encoder_unlocked() is a cheap no‑op.
        self._start_encoder_unlocked()

        # If encoder actually started, switch the audio decoder (Stream A)
        # to the new track without breaking the RTMP connection.
        if self.encoder_proc is not None and self.encoder_proc.poll() is None:
            self._start_audio_unlocked(0.0)
            self.last_start_monotonic = time.monotonic()
            self.status = "playing"
        else:
            # Encoder could not be started (e.g. missing video/RTMP); log and stop.
            self._append_log("Encoder not running while advancing; playback stopped")
            self.status = "stopped"
            self.position_sec = 0.0


    # ---------- public API (used by web handlers) ----------

    def load_playlist(self, paths: List[str]) -> None:
        with self.lock:
            self.playlist = [Path(p) for p in paths]
            self.current_index = 0
            self.position_sec = 0.0
            self._append_log(f"Playlist loaded with {len(self.playlist)} tracks")
            self._update_durations_for_playlist_unlocked()

    def set_playlist_order(self, paths: List[str]) -> None:
        """
        Explicitly set playlist order to given list of paths (drag-and-drop reorder).
        Does not reset current position by itself. If current track is not in the new list,
        index resets to 0.
        """
        with self.lock:
            new_playlist = [Path(p) for p in paths]
            old_current = (
                str(self.playlist[self.current_index]) if self.playlist else None
            )
            self.playlist = new_playlist

            # try to keep same current track if it still exists
            new_index = 0
            if old_current is not None:
                for i, p in enumerate(self.playlist):
                    if str(p) == old_current:
                        new_index = i
                        break
            self.current_index = new_index
            self.position_sec = 0.0
            self._append_log(
                f"Playlist order updated; length={len(self.playlist)}, current_index={self.current_index}"
            )
            self._update_durations_for_playlist_unlocked()

    def set_video(self, path: str) -> None:
        with self.lock:
            self.video_file = Path(path)
            self._append_log(f"Video set to {self.video_file}")
            # Changing the video loop requires restarting the encoder.
            if self.status == "playing":
                pos = self._get_position_unlocked()
                self._restart_full_pipeline_unlocked(pos)

    def set_overlay_text(self, text: str) -> None:
        with self.lock:
            self.overlay_text = text
            self._append_log(f"Overlay text set to: {self.overlay_text!r}")
            # Updating overlay also requires restarting the encoder.
            if self.status == "playing":
                pos = self._get_position_unlocked()
                self._restart_full_pipeline_unlocked(pos)

    def set_rtmp(self, url: str) -> None:
        with self.lock:
            self.rtmp_url = url
            self._append_log(f"RTMP URL set to: {self.rtmp_url}")
            # Changing RTMP target requires a fresh encoder.
            if self.status == "playing":
                pos = self._get_position_unlocked()
                self._restart_full_pipeline_unlocked(pos)
            if self.status == "playing":
                self._start_ffmpeg_unlocked(self._get_position_unlocked())

    def set_ffmpeg_path(self, path: str) -> None:
        with self.lock:
            self.ffmpeg_path = path
            self._append_log(f"FFMPEG path set to: {self.ffmpeg_path}")
            # If we are playing we need to restart with the new ffmpeg binary.
            if self.status == "playing":
                pos = self._get_position_unlocked()
                self._restart_full_pipeline_unlocked(pos)

    def play(self) -> None:
        with self.lock:
            self._append_log("Play requested")
            if self.status in ("stopped", "paused", "error"):
                self._start_pipeline_unlocked(self.position_sec)
            elif self.status == "playing":
                # No-op; keep stream running
                self._append_log("Play ignored: already playing")

    def play_index(self, index: int) -> None:
        """
        Jump to a specific playlist index and start playing from 0 seconds.
        """
        with self.lock:
            if not self.playlist:
                self._append_log("play_index requested but playlist is empty")
                return
            if index < 0 or index >= len(self.playlist):
                self._append_log(f"play_index out of range: {index}")
                return
            self.current_index = index
            self.position_sec = 0.0
            self._append_log(f"play_index requested: {index}")
            self._start_pipeline_unlocked(0.0)

    def pause(self) -> None:
        with self.lock:
            self._append_log("Pause requested")
            # store current approximate position
            self.position_sec = self._get_position_unlocked()
            self._kill_ffmpeg_unlocked()
            self.status = "paused"

    def stop(self) -> None:
        with self.lock:
            self._append_log("Stop requested")
            self._kill_ffmpeg_unlocked()
            self.status = "stopped"
            self.position_sec = 0.0

    def skip_next(self) -> None:
        with self.lock:
            self._append_log("Skip next requested")
            self._advance_track_unlocked()

    def seek(self, seconds: float) -> None:
        if seconds < 0:
            seconds = 0.0
        with self.lock:
            # Clamp to known duration of current track (if available)
            duration = None
            if self.playlist and 0 <= self.current_index < len(self.playlist):
                key = str(self.playlist[self.current_index])
                d = self.track_durations.get(key)
                if isinstance(d, (int, float)) and d > 0:
                    duration = float(d)
            if duration is not None and seconds >= duration:
                # Avoid seeking past EOF which would cause the decoder to finish immediately
                seconds = max(duration - 1.0, 0.0)

            self._append_log(f"Seek requested to {seconds} seconds")
            self.position_sec = seconds
            # Mark for pump loop so first EOF after a seek isn't treated as natural end
            self._recent_seek_time = time.monotonic()
            if self.status == "playing":
                self._start_pipeline_unlocked(seconds)
            # if paused/stopped, we just store new position


    def _get_position_unlocked(self) -> float:
        """
        Approximate playback position based on time since last start.
        Caller must hold self.lock.
        """
        if self.status == "playing":
            dt = time.monotonic() - self.last_start_monotonic
            return self.position_sec + dt
        return self.position_sec

    def get_state(self) -> dict:
        with self.lock:
            pos = self._get_position_unlocked()
            current_track = (
                str(self.playlist[self.current_index]) if self.playlist else None
            )
            playlist_paths = [str(p) for p in self.playlist]
            durations = [
                float(self.track_durations.get(str(p))) if str(p) in self.track_durations else None
                for p in self.playlist
            ]
            return {
                "status": self.status,
                "current_track_index": self.current_index,
                "current_track": current_track,
                "position_sec": pos,
                "playlist": playlist_paths,
                "track_durations": durations,
                "video_file": str(self.video_file) if self.video_file else None,
                "overlay_text": self.overlay_text,
                "rtmp_url": self.rtmp_url,
                "ffmpeg_path": self.ffmpeg_path,
                "audio_bitrate": self.audio_bitrate,
                "video_bitrate": self.video_bitrate,
                "maxrate": self.maxrate,
                "bufsize": self.bufsize,
                "video_fps": self.video_fps,
            }

    def set_encoder_settings(self, cfg: dict) -> None:
    # Update encoder quality settings from a dict.
        with self.lock:
            val = cfg.get("audio_bitrate")
            if val:
                self.audio_bitrate = str(val)

            val = cfg.get("video_bitrate")
            if val:
                self.video_bitrate = str(val)

            val = cfg.get("maxrate")
            if val:
                self.maxrate = str(val)

            val = cfg.get("bufsize")
            if val:
                self.bufsize = str(val)

            if "video_fps" in cfg:
                try:
                    self.video_fps = int(cfg["video_fps"])
                except (TypeError, ValueError):
                    pass

            self._append_log(
                f"Encoder settings updated: "
                f"audio={self.audio_bitrate}, video={self.video_bitrate}, "
                f"maxrate={self.maxrate}, bufsize={self.bufsize}, "
                f"fps={self.video_fps}"
            )

    def watcher_loop(self) -> None:
        """Background loop watching the encoder process.
        If ffmpeg dies while playing, try to restart the pipeline
        automatically from the current position instead of staying in error.
        """
        self._append_log("Watcher loop started")
        while not self.stop_flag:
            with self.lock:
                proc = self.encoder_proc
                status = self.status
            if proc is not None and status == "playing":
                ret = proc.poll()
                if ret is not None:
                    self._append_log(f"ffmpeg encoder exited with code {ret}")
                    with self.lock:
                        # Clean up the audio decoder as well
                        self._kill_audio_unlocked()
                        self.encoder_proc = None
                        self._encoder_log_thread = None

                        if ret == 0:
                            # clean exit => stop
                            self.status = "stopped"
                        else:
                            has_playlist = bool(self.playlist)
                            if has_playlist and not self.stop_flag:
                                start_sec = self._get_position_unlocked()
                                self._append_log(
                                    "Encoder failed; attempting automatic restart "
                                    f"from ~{start_sec:.1f}s"
                                )
                                try:
                                    self._restart_full_pipeline_unlocked(start_sec)
                                    self.status = "playing"
                                except Exception as e:
                                    self._append_log(
                                        f"Automatic restart failed: {e!r}"
                                    )
                                    self.status = "error"
                            else:
                                self.status = "error"
            time.sleep(1.0)



player_state = PlayerState()
