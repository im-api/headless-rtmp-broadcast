import time


class WatcherMixin:
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



