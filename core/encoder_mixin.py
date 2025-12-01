import subprocess
import threading

from .config import VIDEO_SIZE


class EncoderMixin:
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

