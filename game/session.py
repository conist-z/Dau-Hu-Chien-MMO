"""Per-player session watchdog (pure logic — never imports discord.py).

A "session" is one player's live screen+hub pair in a channel. The tracker
only records activity timestamps; the manager's background loop asks for
sessions idle longer than the timeout and ends them through the injected
Discord adapter (``manager.session_adapter``), so game logic stays UI-free.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Canonical session-end reasons (mapped to human text in
# discord_ui/session_end.py — keep keys stable, they may appear in logs).
REASON_INACTIVITY = "inactivity"        # idle past SESSION_TIMEOUT_MINUTES
REASON_LEAVE = "leave"                  # player ran /leave-map
REASON_RESET = "reset"                  # /mapreset or /startmap map switch
REASON_MESSAGE_DELETED = "message_deleted"  # screen/hub message deleted
REASON_ERROR = "error"                  # unexpected exception ended it


@dataclass
class SessionInfo:
    channel_id: int
    user_id: int
    started_at: float      # loop-monotonic seconds
    last_activity: float   # loop-monotonic seconds


class SessionTracker:
    """In-memory activity ledger keyed by (channel_id, user_id)."""

    def __init__(self) -> None:
        self._sessions: Dict[Tuple[int, int], SessionInfo] = {}

    def touch(self, channel_id: int, user_id: int, now: float) -> None:
        """Record activity (creates the session on first touch)."""
        key = (channel_id, user_id)
        info = self._sessions.get(key)
        if info is None:
            self._sessions[key] = SessionInfo(channel_id, user_id, now, now)
        else:
            info.last_activity = now

    def discard(self, channel_id: int, user_id: int) -> Optional[SessionInfo]:
        return self._sessions.pop((channel_id, user_id), None)

    def get(self, channel_id: int, user_id: int) -> Optional[SessionInfo]:
        return self._sessions.get((channel_id, user_id))

    def due(self, now: float, timeout: float) -> List[Tuple[int, int]]:
        """Keys idle for >= ``timeout`` seconds."""
        return [
            key for key, info in self._sessions.items()
            if now - info.last_activity >= timeout
        ]

    def duration(self, channel_id: int, user_id: int, now: float) -> Optional[float]:
        info = self._sessions.get((channel_id, user_id))
        return None if info is None else max(0.0, now - info.started_at)

    def all(self) -> List[Tuple[int, int]]:
        """Live (channel_id, user_id) pairs — the real Discord-online set
        for player counts (web counts come from the session registry)."""
        return list(self._sessions.keys())

    def __len__(self) -> int:
        return len(self._sessions)
