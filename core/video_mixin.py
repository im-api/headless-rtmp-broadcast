import subprocess
import threading

from .config import VIDEO_SIZE


class VideoMixin:
    def _kill_video_unlocked(self) -> None:
        """
        Terminate the Stream B ffmpeg process if running.
        Caller must hold self.lock.
        """
        proc = getattr(self, "video_proc", None)
        if proc is not None and proc.poll() is None:
            self._append_log("Terminating video (Stream B) ffmpeg process")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._append_log("Video process did not terminate in time; killing")
                proc.kill()
            except Exception as e:
                self._append_log(f"Error while terminating video process: {e!r}")
        self.video_proc = None
        self._video_log_thread = None

    def _ffmpeg_video_log_reader_bytes(self, proc: subprocess.Popen) -> None:
        """
        Read ffmpeg stdout for Stream B as bytes and push lines into our log.
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
                    self._append_log(f"[video-ffmpeg] {line}")
        except Exception as e:
            self._append_log(f"video ffmpeg log reader error: {e!r}")

    def _start_video_unlocked(self) -> None:
        """
        Ensure Stream B (video + overlays) ffmpeg is running.

        This process:
        - Loops the configured video file.
        - Applies overlays and "Now Playing" text.
        - Sends H.264 video to a local UDP endpoint (self.video_udp_url).

        Caller must hold self.lock.
        """
        # If already alive, do nothing.
        if self.video_proc is not None and self.video_proc.poll() is None:
            return

        # Clean up any previous video process.
        self._kill_video_unlocked()

        if self.video_file is None:
            self._append_log("No video file, cannot start Stream B")
            return

        if not getattr(self, "video_udp_url", None):
            self._append_log("No video_udp_url configured, cannot start Stream B")
            return

        # Base video filter: scale to configured size and ensure yuv420p.
        vf = f"scale={VIDEO_SIZE},format=yuv420p"

        # Overlay 1: generic overlay text from a small text file that ffmpeg
        # reloads on the fly.
        overlay_file = getattr(self, "overlay_text_file", None)
        if overlay_file is not None:
            # Use just the filename so we avoid Windows drive letters and complex
            # escaping in the filter. The file is created in the project root, and
            # ffmpeg is launched from the same working directory.
            safe_overlay = overlay_file.name
            vf += (
                f",drawtext=textfile='{safe_overlay}':reload=1:"
                "x=20:y=50:fontsize=36:fontcolor=white:box=1:boxcolor=black"
            )

        # Overlay 2: dynamic "Now Playing" text.
        now_file = getattr(self, "now_playing_file", None)
        if now_file is not None:
            safe_now = now_file.name
            vf += (
                f",drawtext=textfile='{safe_now}':reload=1:"
                "x=20:y=h-80:fontsize=32:fontcolor=white:box=1:boxcolor=black"
            )

        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-analyzeduration", "10M",
            "-probesize", "10M",
            "-re",
            "-stream_loop",
            "-1",
            "-i",
            str(self.video_file),
            "-vf",
            vf,
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
            "-f",
            "mpegts",
            self.video_udp_url,
        ]

        self._append_log("Launching video encoder (Stream B): " + " ".join(map(str, cmd)))
        try:
            self.video_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except FileNotFoundError:
            self._append_log(f"ERROR: ffmpeg executable not found for video: {self.ffmpeg_path}")
            self.video_proc = None
            return
        except Exception as e:
            self._append_log(f"ERROR starting video (Stream B) ffmpeg: {e!r}")
            self.video_proc = None
            return

        if self.video_proc.stdout is not None:
            self._video_log_thread = threading.Thread(
                target=self._ffmpeg_video_log_reader_bytes,
                args=(self.video_proc,),
                daemon=True,
            )
            self._video_log_thread.start()