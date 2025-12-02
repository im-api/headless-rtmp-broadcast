import time


class PipelineMixin:
    def _kill_ffmpeg_unlocked(self) -> None:
        """
        Terminate encoder + audio decoder + video process if running.
        Caller must hold self.lock.
        """
        # Audio decoder (Stream A)
        self._kill_audio_unlocked()
        # RTMP encoder (Stream C)
        self._kill_encoder_unlocked()
        # Video (Stream B)
        if hasattr(self, "_kill_video_unlocked"):
            self._kill_video_unlocked()

    def _start_pipeline_unlocked(self, start_sec: float = 0.0) -> None:
        """
        Ensure video (Stream B) and encoder (Stream C) are running and start the
        audio decoder (Stream A) from the given position.
        Caller must hold self.lock.
        """
        if not self.playlist:
            self._append_log("Cannot start pipeline: empty playlist")
            return

        # (re)start Stream B video, then Stream C encoder
        #if hasattr(self, "_start_encoder_unlocked"):
        self._start_encoder_unlocked()
        time.sleep(1000)
        self._start_video_unlocked()

        # Start or restart audio decoder for the current track
        self._start_audio_unlocked(start_sec)
        self.last_start_monotonic = time.monotonic()
        self.position_sec = max(0.0, start_sec)
        self.status = "playing"

    def _restart_full_pipeline_unlocked(self, start_sec: float = 0.0) -> None:
        """
        Restart encoder + audio decoder + video process from a given position.
        Used when changing RTMP URL or in error recovery.
        Caller must hold self.lock.
        """
        self._kill_ffmpeg_unlocked()
        self._start_pipeline_unlocked(start_sec)
