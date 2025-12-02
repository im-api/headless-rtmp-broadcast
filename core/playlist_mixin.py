from pathlib import Path
import time
from typing import List


class PlaylistMixin:
    def _advance_track_unlocked(self, *, loop_queue: bool = True) -> None:
        """
        Advance to the next track in the playlist and restart ONLY the audio decoder.

        Design goals:
        - The long‑lived encoder (RTMP, i.e. Stream C) should *not* be restarted
          when changing tracks. We only touch the audio decoder (Stream A) here.
        - When we reach the end of the playlist and ``loop_queue`` is True,
          we wrap around to the first track (queue loop).
        Caller must hold ``self.lock``.
        """
        if not self.playlist:
            self._append_log("Advance requested but playlist is empty; stopping")
            self.status = "stopped"
            return

        # Normalise current_index so it is always in a valid range
        if self.current_index < 0 or self.current_index >= len(self.playlist):
            self.current_index = 0

        last_index = len(self.playlist) - 1
        at_last = self.current_index == last_index

        if at_last and not loop_queue:
            # End of playlist and looping disabled – stop playback cleanly
            self._append_log("Reached end of playlist; stopping (no loop)")
            self._kill_audio_unlocked()
            self.status = "stopped"
            self.position_sec = 0.0
            return

        if loop_queue:
            # Wrap around to the beginning when we go past the last track.
            next_index = (self.current_index + 1) % len(self.playlist)
        else:
            # No looping: advance by one, but never beyond the last index.
            next_index = self.current_index + 1
            if next_index > last_index:
                next_index = last_index

        self.current_index = next_index
        self.position_sec = 0.0
        self._append_log(f"Advancing to next track (index {self.current_index})")

        # Make sure encoder (Stream C) is running; if it is already alive,
        # _start_encoder_unlocked() is a cheap no‑op.
        self._start_encoder_unlocked()

        # If encoder actually started, switch the audio decoder (Stream A)
        # to the new track without breaking the RTMP connection.
        if self.encoder_proc is not None and self.encoder_proc.poll() is None:
            self._start_audio_unlocked(0.0)
            self.last_start_monotonic = time.monotonic()
            self.status = "playing"
        else:
            # Encoder could not be started (e.g. missing video/RTMP); log and stop.
            self._append_log("Encoder not running while advancing; playback stopped")
            self.status = "stopped"
            self.position_sec = 0.0


    # ---------- public API (used by web handlers) ----------

    def load_playlist(self, paths: List[str]) -> None:
        with self.lock:
            self.playlist = [Path(p) for p in paths]
            self.current_index = 0
            self.position_sec = 0.0
            self._append_log(f"Playlist loaded with {len(self.playlist)} tracks")
            self._update_durations_for_playlist_unlocked()

    def set_playlist_order(self, paths: List[str]) -> None:
        """
        Explicitly set playlist order to given list of paths (drag-and-drop reorder).
        Does not reset current position by itself. If current track is not in the new list,
        index resets to 0.
        """
        with self.lock:
            new_playlist = [Path(p) for p in paths]
            old_current = (
                str(self.playlist[self.current_index]) if self.playlist else None
            )
            self.playlist = new_playlist

            # try to keep same current track if it still exists
            new_index = 0
            if old_current is not None:
                for i, p in enumerate(self.playlist):
                    if str(p) == old_current:
                        new_index = i
                        break
            self.current_index = new_index
            self.position_sec = 0.0
            self._append_log(
                f"Playlist order updated; length={len(self.playlist)}, current_index={self.current_index}"
            )
            self._update_durations_for_playlist_unlocked()
