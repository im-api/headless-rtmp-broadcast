import time
from typing import List


class LoggingMixin:
    def _append_log(self, msg: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        with self.lock:
            self._logs.append(line)
            if len(self._logs) > self._log_max:
                # keep last _log_max entries
                self._logs = self._logs[-self._log_max :]

    def get_logs(self, limit: int = 200) -> List[str]:
        with self.lock:
            if limit <= 0 or limit >= len(self._logs):
                return list(self._logs)
            return self._logs[-limit:]

    # ---------- ffprobe durations ----------

