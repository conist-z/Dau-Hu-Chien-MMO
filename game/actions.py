from dataclasses import dataclass
from typing import Optional

from game.state import Direction


@dataclass
class MoveAction:
    user_id: int
    direction: Direction
    # True when this dispatch is one segment of a larger move (step-size x3/x5
    # or auto-run). Zombies only take their turn on the FINAL segment, so a
    # x5 step press never grants them five free chase/bite turns.
    intermediate: bool = False


@dataclass
class AttackAction:
    """Attack the tile the player is facing."""

    user_id: int


@dataclass
class PlaceBlockAction:
    """Place the selected block on a target tile.

    Default (dx=dy=None): the tile the player is facing. With an offset
    (Build Mode): the tile at (player + dx,dy) — validated and clamped by
    ``apply_place_block`` (range + own-tile guard live in the rules).
    """

    user_id: int
    block_id: str
    dx: Optional[int] = None
    dy: Optional[int] = None


@dataclass
class BreakBlockAction:
    """Break the block on the target tile (ground shows again).

    Default: the facing tile (or aim cursor while active). The web client
    passes dx/dy = the mouse-cursor tile offset from the player."""

    user_id: int
    dx: Optional[int] = None
    dy: Optional[int] = None


@dataclass
class ChopAction:
    """One swing at the harvestable node on the facing tile.

    The tool used (axe/pickaxe family) is resolved from the player's hotbar
    by node kind (see game/resources.py). Progress accumulates in the
    channel's ResourceGrid; the felling swing rolls the node's drop table
    into the player's bag.
    """

    user_id: int
    # Optional explicit tool item id (hotbar "use this tool" presses). When
    # None the strongest held tool of the node's family is used.
    tool_id: Optional[str] = None
    # Web client: swing at the mouse-cursor tile (offset from the player)
    # instead of the facing tile. Clamped to AIM_RANGE by apply_chop.
    dx: Optional[int] = None
    dy: Optional[int] = None


@dataclass
class ShovelAction:
    """One shovel scoop at the tile the player is facing.

    Scooping a grass tuft removes it so the bare-dirt tile shows through and
    the player collects 1 dirt (game/terrain.py).
    """

    user_id: int
    tool_id: Optional[str] = None


@dataclass
class TurnAction:
    """Rotate the player in place (no step). Used by the 🔁 build tool."""

    user_id: int
    direction: Direction


@dataclass
class AimAction:
    """Build Mode: move the aim cursor by (dx, dy) relative to its current
    offset. The rules clamp the offset to ``AIM_RANGE`` around the player."""

    user_id: int
    dx: int
    dy: int


@dataclass
class AimResetAction:
    """Build Mode: hide the aim cursor (Build Mode turned off)."""

    user_id: int
