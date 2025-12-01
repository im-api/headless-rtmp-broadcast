import subprocess
from pathlib import Path
from typing import Dict, Optional

from .config import FFPROBE_PATH


class DurationsMixin:
    def _probe_duration(self, path: Path) -> Optional[float]:
        """
        Use ffprobe to get track duration in seconds.
        Returns None if ffprobe not available or fails.
        """
        cmd = [
            FFPROBE_PATH,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ]
        try:
            out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
            val = out.strip()
            if not val:
                return None
            dur = float(val)
            if dur < 0:
                return None
            return dur
        except Exception as e:
            self._append_log(f"ffprobe failed for {path}: {e}")
            return None

    def _update_durations_for_playlist_unlocked(self) -> None:
        """
        Ensure track_durations has entries for all playlist files.
        Caller must hold self.lock.
        """
        new_map: Dict[str, float] = {}
        for p in self.playlist:
            spath = str(p)
            if spath in self.track_durations:
                new_map[spath] = self.track_durations[spath]
            else:
                dur = self._probe_duration(p)
                if dur is not None:
                    new_map[spath] = dur
        self.track_durations = new_map

    # ---------- internal helpers ----------

