import os
from pathlib import Path

# Video size can be overridden from environment
VIDEO_SIZE = os.getenv("VIDEO_SIZE", "1920x1080")

# Determine ffprobe path:
# - If FFPROBE_PATH is set in env, use it directly.
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
