import subprocess
import threading
import time
from pathlib import Path


class AudioMixin:
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

        # Update the "Now Playing" overlay text file based on the file name.
        # Example: "firefly_120bpm.mp3" -> "firefly".
        now_file = getattr(self, "now_playing_file", None)
        if now_file is not None:
            try:
                stem = Path(audio_file).stem
                now_text = stem.split("_", 1)[0] if stem else ""
                now_file.write_text(now_text, encoding="utf-8")
            except Exception as e:
                self._append_log(f"Failed to write now_playing_file: {e!r}")

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
