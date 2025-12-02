import subprocess
import threading


class EncoderMixin:
    def _kill_encoder_unlocked(self) -> None:
        """
        Terminate the long-lived ffmpeg encoder (RTMP) process if running.
        Caller must hold self.lock.
        """
        proc = self.encoder_proc
        if proc is not None and proc.poll() is None:
            self._append_log("Terminating ffmpeg encoder process")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._append_log("Encoder did not terminate in time; killing")
                proc.kill()
            except Exception as e:
                self._append_log(f"Error while terminating encoder: {e!r}")
        self.encoder_proc = None
        self._encoder_log_thread = None

    def _ffmpeg_log_reader_bytes(self, proc: subprocess.Popen) -> None:
        """
        Read ffmpeg stdout as bytes and push lines into our log buffer.
        """
        try:
            out = proc.stdout
            if out is None:
                return
            for raw in iter(lambda: out.readline(), b""):
                if not raw:
                    break
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    line = repr(raw)
                if line:
                    self._append_log(f"[ffmpeg] {line}")
        except Exception as e:
            self._append_log(f"ffmpeg log reader error: {e!r}")

    def _start_encoder_unlocked(self) -> None:
        """
        Ensure the long-lived ffmpeg encoder (Stream C) is running.

        This process:
        - Reads raw PCM audio from stdin (fed by the audio decoder, Stream A).
        - Reads H.264 video over UDP from Stream B (self.video_udp_url).
        - Encodes audio and muxes audio+video to the configured RTMP URL.

        Caller must hold self.lock.
        """
        # If encoder is already alive, do nothing.
        if self.encoder_proc is not None and self.encoder_proc.poll() is None:
            return

        # Clean up any previous encoder process.
        self._kill_encoder_unlocked()

        if not self.rtmp_url:
            self._append_log("No RTMP URL, cannot start encoder")
            return

        if not getattr(self, "video_udp_url", None):
            self._append_log("No video_udp_url configured, cannot start encoder")
            return

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
            # Video from Stream B over UDP
            "-analyzeduration", "50M",
            "-probesize", "50M",
            "-i",
            self.video_udp_url,
            # Encoding / muxing settings
            "-c:v",
            "copy",  # video is already H.264 from Stream B
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
            # Map video from UDP and audio from stdin
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            # Output to RTMP
            "-f",
            "flv",
            self.rtmp_url,
        ]

        self._append_log("Launching ffmpeg encoder (Stream C): " + " ".join(map(str, cmd)))
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
        except Exception as e:
            self._append_log(f"ERROR starting ffmpeg encoder: {e!r}")
            self.encoder_proc = None
            self.status = "error"
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
