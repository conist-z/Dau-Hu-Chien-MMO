"""Web API protocol constants + session registry (no discord imports).

The web client talks JSON over a WebSocket that is FRONTED by the NexNode
relay; the bot dials out to the relay and speaks the same frames on the other
end. All game mutations go through GameManager (per-runtime lock, rule 16).
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

# Client -> server (via relay)
MSG_JOIN = "join"                # {token, channel_id}
MSG_LIST = "list"                # {} (requires valid token)
MSG_INPUT = "input"              # {dx, dy, running}
MSG_ACTION = "action"            # {name: attack} (F key / left click)
MSG_INV_OP = "inventory_op"      # {op: move_to|use, item_id, slot}
MSG_CRAFT_OP = "craft_op"        # {recipe_id}
MSG_CHAT_CMD = "chat_cmd"        # {text}
MSG_PING = "ping"                # {}

# Server -> client
MSG_WELCOME = "welcome"          # map + self + hotbar + inventory + recipes
MSG_SCENARIO_LIST = "scenario_list"
MSG_SNAPSHOT = "snapshot"        # periodic world state (diff/AOI)
MSG_INV_DELTA = "inventory_delta"
MSG_CRAFT_RESULT = "craft_result"
MSG_PUSH = "push"                # one-off UI notice (toast)
MSG_ERROR = "error"              # {code, message}
MSG_PONG = "pong"

# Rate limiting: max input frames per second per session (20 Hz tick needs
# far fewer; the client only sends on change).
INPUT_FRAMES_PER_SEC = 30


@dataclass
class WebSession:
    """One authenticated web connection (transport-agnostic)."""

    user_id: int
    display_name: str
    channel_id: int
    token: str
    created_at: float = field(default_factory=time.monotonic)
    # Hotbar slot the web client currently holds (select_slot frame; UI-only).
    selected_slot: int = 0
    # Input flood control: timestamps of recent input frames.
    _input_times: list = field(default_factory=list)
    # Highest input sequence number seen from this client (input-sequence
    # reconciliation): the client tags every input frame with a monotonically
    # increasing seq; snapshots echo back (last_seq, server pos) and the
    # client rewinds + replays unacked inputs. Only a MONOTONIC ack is kept
    # here — never trust a lower seq (reordered frames, replay attacks).
    input_seq: int = -1

    def input_allowed(self) -> bool:
        now = time.monotonic()
        self._input_times = [t for t in self._input_times if now - t < 1.0]
        if len(self._input_times) >= INPUT_FRAMES_PER_SEC:
            return False
        self._input_times.append(now)
        return True


class SessionRegistry:
    """token -> WebSession. In-memory (tokens die with the process; a restart
    forces re-login, which the client treats as a normal reconnect)."""

    def __init__(self):
        self._sessions: Dict[str, WebSession] = {}

    def create(self, user_id: int, display_name: str, channel_id: int) -> WebSession:
        token = secrets.token_urlsafe(24)
        sess = WebSession(user_id, display_name, channel_id, token)
        self._sessions[token] = sess
        return sess

    def get(self, token: str) -> Optional[WebSession]:
        return self._sessions.get(token)

    def drop(self, token: str) -> None:
        self._sessions.pop(token, None)

    def drop_user(self, user_id: int) -> None:
        for tok in [t for t, s in self._sessions.items() if s.user_id == user_id]:
            self._sessions.pop(tok, None)
