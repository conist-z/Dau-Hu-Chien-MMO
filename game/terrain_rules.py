"""Shovel scoop rule (pure game logic, no discord/IO).

One scoop swing at the TARGET tile (the aim-cursor tile while one is active,
otherwise the facing tile). Scooping works ONLY on a bare grass tuft tile
(game/terrain.py): the tuft is removed, the bare-dirt tile shows through, and
the player collects 1 dirt.

Tool tier drives the number of presses: the shovel tier multiplier (game/
tools.py) decides how many swings one scoop takes. Placed blocks and other
players on the tile block the scoop entirely.
"""

from __future__ import annotations

import time
from typing import Optional

from game.actions import ShovelAction
from game.state import ActionResult, Direction, GameState
from game.tools import best_tool_of_family, parse_tool_id


def _target_tile(player) -> tuple:
    """The square the player aims at: the aim-cursor tile while one is active,
    otherwise the facing tile (same convention as the other tool rules)."""
    if getattr(player, "aim_active", False):
        return player.x + player.aim_dx, player.y + player.aim_dy
    dx, dy = Direction[player.direction].vector
    return player.x + dx, player.y + dy


def _tile_has_player(state: GameState, x: int, y: int, exclude: int) -> bool:
    return any(
        p.x == x and p.y == y and p.user_id != exclude
        for p in state.get_visible_players()
    )


def apply_scoop(
    state: GameState,
    action: ShovelAction,
    terrain,           # game/terrain.TerrainGrid (duck-typed)
    blocks,            # BlockGrid or None
    inventory,
    now: Optional[float] = None,
) -> ActionResult:
    """One shovel swing. Progress accumulates across presses keyed by tile;
    the final press pops the tuft, reveals the dirt and grants 1 dirt."""
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")
    if not player.visible:
        return ActionResult(False, "invisible")
    if terrain is None:
        return ActionResult(False, "no_terrain")

    # Resolve the shovel being used: an explicit hotbar tool id when given
    # (and still a shovel in stock), otherwise the best held shovel.
    tool_id = action.tool_id
    if tool_id is not None:
        td = parse_tool_id(tool_id)
        if td is None or td.family != "shovel" or inventory.count(tool_id) <= 0:
            tool_id = None
    if tool_id is None:
        tool_id = best_tool_of_family(inventory, "shovel")
    from game.tools import shovel_hits

    hits = shovel_hits(parse_tool_id(tool_id).material if tool_id else "dirt")

    tx, ty = _target_tile(player)
    if blocks is not None and blocks.solid_at(tx, ty):
        return ActionResult(False, "blocked_tile")
    if _tile_has_player(state, tx, ty, action.user_id):
        return ActionResult(False, "tile_occupied")
    if not terrain.scoopable(tx, ty):
        if terrain.is_grass(tx, ty):
            return ActionResult(False, "already_scooped")
        return ActionResult(False, "no_grass")

    # Each swing chips the tuft; ``damage`` reports swing progress so the UI
    # can show a % on the tool button (same convention as chop).
    progress_key = ("scoop", tx, ty)
    progress = getattr(state, "scoop_progress", None)
    if progress is None:
        progress = state.scoop_progress = {}
    progress[progress_key] = progress.get(progress_key, 0) + 1
    if progress[progress_key] < hits:
        return ActionResult(
            True, state_changed=True, pos=(tx, ty), block_id="grass",
            damage=progress[progress_key],
        )

    progress.pop(progress_key, None)
    terrain.scoop(tx, ty)
    inventory.add("dirt", 1)
    return ActionResult(
        True, state_changed=True, pos=(tx, ty), block_id="grass",
        damage=hits, drops=[("dirt", 1)],
    )


def scoop_progress_at(state: GameState, x: int, y: int) -> int:
    """Current swing progress on one tile (0 when untouched)."""
    progress = getattr(state, "scoop_progress", None)
    return progress.get(("scoop", x, y), 0) if progress else 0
