from pathlib import Path

from .config import VIDEO_SIZE


class LegacyCmdMixin:
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

