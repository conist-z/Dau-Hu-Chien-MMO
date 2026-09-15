"""Portal gate behaviour (user 16/09: "cổng đang hơi nhạy").

The gate must fire on a DELIBERATE touch (edge: not-touching -> touching) and
must never bounce the player back into the map they just left:

* arriving on/next to the gate never re-fires (seeded contact / nudged arrival);
* standing, pushing or jittering against the door never re-fires;
* a short cooldown after any teleport covers the "vừa ra đã bị hút vào lại" case;
* stepping off the gate and walking back onto it teleports again (a door is
  still a door);
* arrivals are never placed ON the exit tile while a free neighbour exists.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.collision import FLOAT_BOX_HALF
from game.manager import GameManager
from game.travel import (
    TRADE_INTERIOR_MAP,
    TRADE_LOBBY_MAP,
    check_portal_after_move,
    free_arrival_tile,
    move_player_between_runtimes,
)

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"
DOOR_TILE = (38, 24)  # monster trade base door in the lobby (portals.json)
MAT_TILE = (8, 12)    # interior exit mat (arrival + trigger)


def _gm_with_lobby():
    gm = GameManager(ASSETS)
    gm.create_runtime(1, TRADE_LOBBY_MAP)
    return gm, gm.runtimes[1]


def _place(rt, uid, x, y):
    """Put a web-style player at a float position (collision box centred)."""
    rt.state.add_player(uid, "tester", int(x), int(y))
    p = rt.state.get_player(uid)
    p.x_f, p.y_f = x, y
    p.sync_int_from_float()
    return p


def test_arriving_on_the_gate_does_not_refire():
    """Seeded contact: standing on the mat right after arriving is silent."""
    gm, rt = _gm_with_lobby()
    p = _place(rt, 1, MAT_TILE[0] + 0.5, MAT_TILE[1] + 0.5)
    rt.on_portal_tile = {1}  # arrival seeding (move_player_between_runtimes)
    assert check_portal_after_move(rt, gm.portals, 1) is None


def test_pushing_against_the_door_never_refires_while_in_contact():
    gm, rt = _gm_with_lobby()
    _place(rt, 1, DOOR_TILE[0] - 0.4, DOOR_TILE[1] + 0.5)
    first = check_portal_after_move(rt, gm.portals, 1)
    assert first is not None, "a deliberate touch must fire"
    # The player stays pressed against the door (jitter, no real step away):
    for dx in (0.02, -0.02, 0.01):
        p = rt.state.get_player(1)
        p.x_f, p.y_f = DOOR_TILE[0] - 0.4 + dx, DOOR_TILE[1] + 0.5
        p.sync_int_from_float()
        assert check_portal_after_move(rt, gm.portals, 1) is None


def test_cooldown_blocks_an_instant_bounce_after_a_teleport():
    """Just teleported: touching the (other) gate within the grace window is
    ignored — this is the 'vừa ra khỏi khu vực đã bị đưa vào lại' case."""
    gm, rt = _gm_with_lobby()
    _place(rt, 1, DOOR_TILE[0] - 0.4, DOOR_TILE[1] + 0.5)
    assert check_portal_after_move(rt, gm.portals, 1) is not None
    # Fresh contact state in the destination world (arrival off the gate).
    rt.on_portal_tile = set()
    rt._portal_teleport_at[1] = time.monotonic()
    assert check_portal_after_move(rt, gm.portals, 1) is None


def test_stepping_off_then_back_onto_the_gate_fires_again():
    gm, rt = _gm_with_lobby()
    _place(rt, 1, DOOR_TILE[0] + 3.5, DOOR_TILE[1] + 0.5)  # clearly off the gate
    assert check_portal_after_move(rt, gm.portals, 1) is None
    p = rt.state.get_player(1)
    p.x_f, p.y_f = DOOR_TILE[0] - 0.4, DOOR_TILE[1] + 0.5  # walked back in
    p.sync_int_from_float()
    assert check_portal_after_move(rt, gm.portals, 1) is not None


def test_arrival_is_nudged_off_the_exit_tile():
    """The interior's only spawn candidate is the mat (a gate tile) — arrival
    must land on a free walkable neighbour instead of on the trigger."""
    gm = GameManager(ASSETS)
    gm.create_runtime(1, TRADE_INTERIOR_MAP)
    rt = gm.runtimes[1]
    tile = free_arrival_tile(rt, [MAT_TILE], set(), portal_cfg=gm.portals)
    assert tile != MAT_TILE
    assert rt.collision.is_walkable(*tile)
    assert gm.portals.link_at(TRADE_INTERIOR_MAP, *tile) is None


def test_free_arrival_prefers_a_non_gate_candidate():
    gm, rt = _gm_with_lobby()
    other = (DOOR_TILE[0] + 4, DOOR_TILE[1] + 4)
    tile = free_arrival_tile(
        rt, [DOOR_TILE, other], set(), portal_cfg=gm.portals,
    )
    assert tile == other


def test_move_player_between_runtimes_seeds_contact_on_arrival():
    """Arriving at the destination seeds the mover as in contact, so the very
    first check there cannot teleport them straight back."""
    gm, src = _gm_with_lobby()
    dst = gm.get_or_create_side_runtime(src, TRADE_INTERIOR_MAP)
    p = _place(src, 1, DOOR_TILE[0] + 0.5, DOOR_TILE[1] + 0.5)
    move_player_between_runtimes(src, dst, 1, MAT_TILE)
    assert 1 in getattr(dst, "on_portal_tile", set())
    assert dst.state.get_player(1) is p
    assert check_portal_after_move(dst, gm.portals, 1) is None
    # r = FLOAT_BOX_HALF is what makes the box touch a 1-tile gate; sanity check
    # the constant is still the one the gate math assumes.
    assert 0 < FLOAT_BOX_HALF < 0.5
