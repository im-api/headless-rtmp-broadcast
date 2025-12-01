import time


class PipelineMixin:
    def _kill_ffmpeg_unlocked(self) -> None:
        """
        Terminate encoder + audio decoder if running. Caller must hold self.lock.
        """
        self._kill_audio_unlocked()
        self._kill_encoder_unlocked()


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


