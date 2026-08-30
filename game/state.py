from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Direction(Enum):
    NORTH = (0, -1)
    SOUTH = (0, 1)
    EAST = (1, 0)
    WEST = (-1, 0)
    NORTH_EAST = (1, -1)
    NORTH_WEST = (-1, -1)
    SOUTH_EAST = (1, 1)
    SOUTH_WEST = (-1, 1)

    @property
    def vector(self):
        return self.value


@dataclass
class Player:
    user_id: int
    display_name: str
    x: int = 0
    y: int = 0
    direction: str = "SOUTH"
    sprite_id: str = ""
    visible: bool = True


@dataclass
class ActionResult:
    success: bool
    reason: Optional[str] = None
    state_changed: bool = False


class GameState:
    """Pure in-memory game state. Must NOT import discord.py."""

    def __init__(self, scenario_id: int, map_id: str):
        self.scenario_id = scenario_id
        self.map_id = map_id
        self.players: Dict[int, Player] = {}

    def add_player(self, user_id: int, display_name: str, x: int = 0, y: int = 0) -> Player:
        if user_id in self.players:
            return self.players[user_id]
        p = Player(user_id=user_id, display_name=display_name, x=x, y=y)
        self.players[user_id] = p
        return p

    def remove_player(self, user_id: int) -> Optional[Player]:
        return self.players.pop(user_id, None)

    def get_player(self, user_id: int) -> Optional[Player]:
        return self.players.get(user_id)

    def get_visible_players(self) -> List[Player]:
        return [p for p in self.players.values() if p.visible]
