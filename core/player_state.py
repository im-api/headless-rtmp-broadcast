#!/usr/bin/env python3
"""
Composite PlayerState assembled from smaller mixins.

This keeps each module short and focused while preserving the original
public API and behaviour.
"""

from .base_state import BaseState
from .logging_mixin import LoggingMixin
from .durations_mixin import DurationsMixin
from .legacy_cmd_mixin import LegacyCmdMixin
from .encoder_mixin import EncoderMixin
from .audio_mixin import AudioMixin
from .pipeline_mixin import PipelineMixin
from .playlist_mixin import PlaylistMixin
from .control_mixin import ControlMixin
from .watcher_mixin import WatcherMixin


class PlayerState(
    BaseState,
    LoggingMixin,
    DurationsMixin,
    LegacyCmdMixin,
    EncoderMixin,
    AudioMixin,
    PipelineMixin,
    PlaylistMixin,
    ControlMixin,
    WatcherMixin,
):
    """Core streaming state and control logic."""


player_state = PlayerState()
