from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import math

from game.blocks import BlockGrid
from game.inventory import Inventory


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


# Clockwise ring used by the 🔁 turn-in-place tool (8 compass steps).
CLOCKWISE_ORDER = [
    Direction.NORTH, Direction.NORTH_EAST, Direction.EAST, Direction.SOUTH_EAST,
    Direction.SOUTH, Direction.SOUTH_WEST, Direction.WEST, Direction.NORTH_WEST,
]


def next_clockwise(direction: Direction) -> Direction:
    """Next direction clockwise from ``direction`` (pure, total — any member)."""
    i = CLOCKWISE_ORDER.index(direction)
    return CLOCKWISE_ORDER[(i + 1) % len(CLOCKWISE_ORDER)]


@dataclass
class ActionResult:
    success: bool
    reason: Optional[str] = None
    state_changed: bool = False
    pos: Optional[tuple] = None
    block_id: Optional[str] = None
    moved: bool = False
    drops: Optional[List[Tuple[str, int]]] = None
    damage: int = 0
    target_id: Optional[str] = None
    target_defeated: bool = False
    # Kaetram-style hit flags for the renderer (gold crit splat / MISS text).
    critical: bool = False
    missed: bool = False
    # Chop/mine only: total swings the node needs WITH the resolved tool
    # (the client renders progress = hits/needed). None = not a harvest.
    needed: Optional[int] = None


def _tile_of(v: float) -> int:
    """The Discord-rendered tile of a float position: floor().

    A web player's continuous position always maps to exactly one grid tile
    for the chat client. Plain int() would drift one tile left/up for negative
    coords; floor matches the visual tile the sprite stands on.
    """
    return math.floor(v)


@dataclass
class Player:
    user_id: int
    display_name: str
    x: int = 0
    y: int = 0
    direction: str = "SOUTH"
    # Continuous position (float tile units) for web clients. Discord players
    # move per-tile so x_f/y_f simply mirror x/y; web players move freely and
    # their int x/y is the FLOOR of the float (what chat clients see).
    x_f: float = 0.0
    y_f: float = 0.0
    # True once the player has moved continuously (web client); the save
    # path derives int x/y from the float instead of trusting stale ints.
    float_moved: bool = False
    # True while the player is connected through the web client. Web players
    # run on a SEPARATE realtime zombie pack (state.web_zombies, float
    # positions, 20 Hz tick) — never the Discord turn-based pack, so neither
    # side lags or interferes with the other.
    is_web: bool = False
    sprite_id: str = ""
    visible: bool = True
    # RPG stats (MVP defaults; persisted as primitives in SQLite).
    hp: int = 100
    max_hp: int = 100
    mana: int = 50
    max_mana: int = 50
    level: int = 1
    xp: int = 0
    coins: int = 0
    class_id: str = "adventurer"
    learned_skills: List[str] = field(default_factory=lambda: ["slash"])
    # Discord message ids of this player's map, D-pad controls, and hub.
    # The controls id is persisted so the separate persistent View is restored
    # on restart without attaching buttons back to the image message.
    screen_message_id: Optional[int] = None
    controls_message_id: Optional[int] = None
    hub_message_id: Optional[int] = None
    # Build Mode aim cursor: offset from the player to the target tile.
    # Ephemeral UI/gameplay state — intentionally NOT persisted.
    aim_active: bool = False
    aim_dx: int = 0
    aim_dy: int = 0
    aim_block: str = "stone"
    # Death/respawn state. Dead players are hidden from the world for the
    # short respawn window; the Discord adapter shows the dead screen.
    dead_until: Optional[float] = None
    death_reason: Optional[str] = None
    # Preferred steps per move press (1/3/5). Persisted so the choice
    # survives a restart — new sessions start at the remembered setting.
    step_size: int = 1

    @property
    def alive(self) -> bool:
        return self.hp > 0 and self.dead_until is None

    def revive_if_expired(self, now: float) -> bool:
        """Lazily revive a dead player whose respawn window is over.

        ``dead_until`` is EPHEMERAL (never persisted) and the 5 s respawn is
        carried by an in-memory task. A bot restart, a runtime torn down while
        the player was dead, or a death in a side world all left ``hp == 0``
        with no deadline AND no task — and ``alive`` (hp > 0 AND no deadline)
        then stayed False forever: the player was locked in place, every
        action answered "dead" and the web overlay counted down from 0 (the
        "bị nhốt ở vòng tròn vô hình" report). Any caller can heal that; returns
        True only when it actually revived the player.
        """
        if self.alive:
            return False
        if self.dead_until is not None and self.dead_until > now:
            return False
        self.hp = self.max_hp
        self.visible = True
        self.dead_until = None
        self.death_reason = None
        return True

    def sync_int_from_float(self) -> None:
        """Derive int grid coords from the float position (floor)."""
        self.x = _tile_of(self.x_f)
        self.y = _tile_of(self.y_f)

    def sync_float_from_int(self) -> None:
        """Centre the float position on the int tile (Discord joins/teleports).

        +0.5 so a web player standing on tile (3, 4) renders centred on that
        tile and the chat client sees floor(3.5)==3.
        """
        self.x_f = float(self.x) + 0.5
        self.y_f = float(self.y) + 0.5


class GameState:
    """Pure in-memory game state. Must NOT import discord.py."""

    def __init__(self, scenario_id: int, map_id: str):
        self.scenario_id = scenario_id
        self.map_id = map_id
        self.players: Dict[int, Player] = {}
        # Zombies are transient night creatures; they respawn from the clock
        # instead of being persisted in SQLite.
        # ``zombies`` = the Discord turn-based pack (int tiles, bitten via
        # button presses). ``web_zombies`` = the SEPARATE realtime web pack
        # (float positions, 20 Hz tick) — the two never interact, so heavy web
        # combat can never stall the chat client (and vice versa).
        self.zombies: Dict[str, object] = {}
        self.zombie_seq: int = 0
        self.web_zombies: Dict[str, object] = {}
        self.web_zombie_seq: int = 0
        self.previous_positions: Dict[int, Tuple[int, int]] = {}
        # Placed-block overlay per tile (sandbox). Ground = the map itself;
        # a block covers its tile, breaking it reveals the ground again.
        self.blocks: BlockGrid = BlockGrid()
        # Runtime-scoped per-player inventory map stashed here so pure rules
        # (attack damage by held item — the hotbar is a projection of each
        # ordered bag) can read them without importing the manager.
        # Rebound by GameManager.dispatch on every call.
        self.inventories: Dict[int, Inventory] = {}
        # Ephemeral local lights such as fireflies or future carried lamps.
        # Entries are (x, y, radius_tiles, intensity, RGB) and are consumed by
        # the renderer only; they are not persisted as gameplay state.
        self.light_sources: List[tuple] = []

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
        return [
            p for p in self.players.values()
            if p.visible and p.alive
        ]
