"""Drop entity engine ("hạt linh khí") — game/drops.py.

Spawn arc, vortex magnet, collect persistence routing, despawn + cap, and
the progressive block hardness flow (crack -> break -> drop -> collect).
Deterministic via injected rng (rules 9/27).
"""

import random

from game.actions import BreakBlockAction, PlaceBlockAction
from game.blocks import BlockGrid, get_block
from game.collision import Collision
from game.drops import (
    COLLECT_RADIUS,
    DESPAWN_SECONDS,
    MAGNET_RADIUS,
    DropField,
    drops_payload,
    get_drop_field,
    spawn_drops,
    tick_drops,
)
from game.inventory import Inventory
from game.map_loader import load_map
from game.rules import apply_break_block, apply_place_block
from game.state import GameState

import os
import pathlib

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _game_state() -> GameState:
    state = GameState(1, "test-map")
    state.add_player(10, "A", 5, 5)
    state.players[10].direction = "EAST"
    return state


def _player_at(x: float, y: float, user_id: int = 10):
    """Duck-typed web player: float position + the attrs tick_drops reads."""

    class _P:
        pass

    p = _P()
    p.user_id = user_id
    p.x_f = x
    p.y_f = y
    p.alive = True
    p.visible = True
    return p


# ---------------------------------------------------------------------------
# DropField basics
# ---------------------------------------------------------------------------


def test_spawn_scatter_and_cap():
    f = DropField()
    rng = random.Random(7)
    for _ in range(250):
        f.spawn("wood", 1, 5.5, 5.5, rng=rng)
    assert len(f) <= 200  # MAX_DROPS_PER_STATE
    ids = {d.drop_id for d in f.drops.values()}
    assert len(ids) == len(f)


def test_despawn_after_ttl():
    f = DropField()
    d = f.spawn("wood", 1, 5.5, 5.5, rng=random.Random(1))
    # Fake age: rewrite born_at backwards past the TTL.
    import time as _t

    d.born_at = _t.monotonic() - DESPAWN_SECONDS - 1
    pruned = f.prune_expired(_t.monotonic())
    assert pruned == 1 and len(f) == 0


def test_drops_payload_culls_by_distance():
    f = DropField()
    f.spawn("wood", 1, 0.5, 0.5, rng=random.Random(1))
    f.spawn("wood", 1, 90.5, 90.5, rng=random.Random(2))
    rows = drops_payload.__globals__["DropField"] and f  # sanity: field exists
    # Simulate a state-like object via the payload function's signature.
    class _S:
        drop_field = f

    payload = drops_payload(_S(), 0.5, 0.5, cull_radius=24.0)
    assert len(payload) == 1
    assert payload[0][1] == "wood"
    assert payload[0][6] in ("idle", "magnet", "collected")


# ---------------------------------------------------------------------------
# Physics: magnet + collect
# ---------------------------------------------------------------------------


class _State:
    """Minimal state duck for tick_drops (drop_field holder)."""

    def __init__(self):
        self.drop_field = DropField()


def test_magnet_pulls_and_collects():
    state = _State()
    rng = random.Random(3)
    spawn_drops(state, 5, 5, [("stone", 1)], rng=rng)
    player = _player_at(6.0, 5.0)  # 1 tile away: inside MAGNET_RADIUS
    collected = []
    for _ in range(120):  # up to 6 simulated seconds
        got, _pruned = tick_drops(state, [player], 0.05, rng=rng)
        collected.extend(got)
        if collected:
            break
    assert collected == [(10, "stone", 1)]
    field_ = get_drop_field(state)
    # Collected drops linger briefly for the client burst, then prune.
    field_.prune_collected(remaining_stub := __import__("time").monotonic(), linger=0.0)
    assert len(field_) == 0


def test_no_magnet_beyond_radius():
    state = _State()
    spawn_drops(state, 5, 5, [("wood", 1)], rng=random.Random(5))
    player = _player_at(5 + MAGNET_RADIUS + 2, 5.0)  # far outside
    got, _ = tick_drops(state, [player], 0.05, rng=random.Random(6))
    assert got == []
    d = next(iter(get_drop_field(state).drops.values()))
    assert d.phase == "idle" and d.target_id is None


def test_arc_bounces_then_lands():
    state = _State()
    spawn_drops(state, 5, 5, [("wood", 1)], rng=random.Random(9))
    d = next(iter(get_drop_field(state).drops.values()))
    assert d.vz > 0.0  # launched upward
    for _ in range(60):
        tick_drops(state, [], 0.05, rng=random.Random(10))
    assert d.landed and d.z == 0.0


# ---------------------------------------------------------------------------
# Block hardness flow
# ---------------------------------------------------------------------------


def test_block_hardness_progressive_break():
    original_creative = __import__("game.blocks", fromlist=["CREATIVE_MODE"]).CREATIVE_MODE
    try:
        __import__("game.blocks", fromlist=["CREATIVE_MODE"]).CREATIVE_MODE = False
        state = _game_state()
        state.blocks.place(6, 5, "stone")
        inv = Inventory()
        inv.add("wood_pickaxe", 1)  # stone gate needs a pickaxe
        state.inventories = {10: inv}

        # Pickaxe (right family): 2 damage/hit -> stone (hardness 6) breaks
        # in 3 hits.
        r = apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        assert r.success and state.blocks.get(6, 5) == "stone"
        assert r.damage == 2 and r.needed == 6
        apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        r2 = apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        assert state.blocks.get(6, 5) is None
        assert r2.drops == [("stone", 1)]
        # Drop entity spawned, bag untouched.
        assert any(
            d.item_id == "stone" for d in get_drop_field(state).drops.values()
        )
        assert inv.count("stone") == 0
    finally:
        __import__("game.blocks", fromlist=["CREATIVE_MODE"]).CREATIVE_MODE = original_creative


def test_right_tool_breaks_faster():
    state = _game_state()
    state.blocks.place(6, 5, "wood")  # facing tile (east), hardness 4, axe-family
    inv = Inventory()
    inv.add("wood_axe", 1)
    # Hold the axe: hotbar scan finds it (bag front-to-back). Axe = right
    # family, 2 dmg/hit -> 2 of 4 hits crack, 2 more break.
    state.inventories = {10: inv}
    apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
    assert state.blocks.get(6, 5) == "wood"  # cracked 2/4, still standing
    r2 = apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
    assert r2.success and state.blocks.get(6, 5) is None  # 4 dmg >= 4
    assert r2.damage == 4


def test_wrong_tool_chips_slowly():
    state = _game_state()
    state.blocks.place(6, 5, "wood")  # facing tile; AXE is the right family
    inv = Inventory()
    inv.add("wood_pickaxe", 1)  # wrong family: 1 damage/hit
    state.inventories = {10: inv}
    for _ in range(3):
        apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        assert state.blocks.get(6, 5) == "wood"
    apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
    assert state.blocks.get(6, 5) is None  # hit 4 of hardness 4


def test_place_resets_damage():
    state = _game_state()
    state.blocks.place(7, 5, "stone")
    state.blocks.add_damage(7, 5, 3)
    assert state.blocks.damage_of(7, 5) == 3
    state.blocks.remove(7, 5)
    state.blocks.place(7, 5, "wood")
    assert state.blocks.damage_of(7, 5) == 0
    assert state.blocks.hardness_of(7, 5) == 4


# ---------------------------------------------------------------------------
# Round trip: place -> crack -> break -> drop -> magnet collect -> bag
# ---------------------------------------------------------------------------


def test_full_round_trip_block_to_bag():
    original_creative = __import__("game.blocks", fromlist=["CREATIVE_MODE"]).CREATIVE_MODE
    try:
        __import__("game.blocks", fromlist=["CREATIVE_MODE"]).CREATIVE_MODE = False
        state = _game_state()
        col = Collision(load_map("test-map", ASSETS), state.blocks)
        inv = Inventory()
        inv.add("wood", 1)

        r = apply_place_block(state, PlaceBlockAction(10, "wood"), col, state.blocks, inv)
        assert r.success and inv.count("wood") == 0

        # Break (wood hardness 4, bare hand 1 dmg/hit -> 4 hits).
        apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        r2 = apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        assert r2.drops == [("wood", 1)]
        assert state.blocks.get(6, 5) is None

        # Magnet + collect at the drop position. tick_drops grants to the
        # player OBJECT'S bag via the caller (manager) — here we apply it.
        player = _player_at(6.5, 5.5)
        collected = []
        for _ in range(120):
            got, _ = tick_drops(state, [player], 0.05, rng=random.Random(11))
            collected.extend(got)
            if collected:
                break
        assert collected == [(10, "wood", 1)]
        for _uid, iid, qty in collected:
            inv.add(iid, qty)
        assert inv.count("wood") == 1
    finally:
        __import__("game.blocks", fromlist=["CREATIVE_MODE"]).CREATIVE_MODE = original_creative
