from pathlib import Path
import time


class ControlMixin:
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

