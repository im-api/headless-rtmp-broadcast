#!/usr/bin/env python3
"""Compatibility wrapper for the streaming core.

The actual implementation now lives in :mod:`core.player_state`.
This module re-exports :class:`PlayerState` and the singleton
:data:`player_state` so existing imports keep working::

    from streamer_core import player_state

"""

from core.player_state import PlayerState, player_state

__all__ = ["PlayerState", "player_state"]
