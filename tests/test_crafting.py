"""Tests for the crafting system (game/crafting.py + manager adapter).

Covers: recipe registry, can_craft material/table rules, do_craft stack
math, station proximity on a real map, and the end-to-end hand -> planks ->
table -> place -> axe progression.
"""

import pytest

from pathlib import Path

from game import crafting
from game.blocks import BLOCK_REGISTRY, BlockGrid
from game.crafting import (
    RECIPE_REGISTRY,
    STATION_RANGE,
    RecipeDef,
    can_craft,
    do_craft,
    get_recipe,
    nearest_station,
)
from game.inventory import Inventory
from game.map_loader import load_map
from game.state import GameState


ASSETS = Path(__file__).resolve().parent.parent / "assets" / "maps"


def _map():
    return load_map("test-map", ASSETS)


def _state():
    return GameState(1, "test-map")


# ----- registry -----


def test_recipes_registered():
    for rid in ("plank", "stick", "crafting_table", "wood_axe", "furnace"):
        assert get_recipe(rid) is not None


def test_crafting_table_block_registered():
    b = BLOCK_REGISTRY.get("crafting_table")
    assert b is not None and b.placeable and b.solid


def test_recipe_ids_unique():
    ids = [r.id for r in RECIPE_REGISTRY.values()]
    assert len(ids) == len(set(ids))


# ----- can_craft -----


def test_can_craft_missing_materials():
    r = get_recipe("plank")
    inv = Inventory()
    ok, reason = can_craft(r, inv, near_table=False)
    assert not ok and reason == "missing_materials"


def test_can_craft_plank_by_hand():
    r = get_recipe("plank")
    inv = Inventory()
    inv.add("wood", 1)
    ok, reason = can_craft(r, inv, near_table=False)
    assert ok and reason == "ok"


def test_can_craft_table_requires_only_hand():
    """The crafting table itself must be craftable WITHOUT a table."""
    r = get_recipe("crafting_table")
    inv = Inventory()
    inv.add("plank", 4)
    ok, reason = can_craft(r, inv, near_table=False)
    assert ok and reason == "ok"


def test_table_recipe_blocked_without_table():
    # Tool recipes are intentionally EMPTY for now (user 14/09) — craft is
    # impossible regardless; keep the recipe lookup alive.
    r = get_recipe("wood_axe")
    assert r is not None and r.inputs == []


def test_table_recipe_ok_near_table():
    # Empty tool recipe = locked even near the table.
    r = get_recipe("wood_axe")
    inv = Inventory()
    ok, reason = can_craft(r, inv, near_table=True)
    assert not ok and reason == "missing_materials"


# ----- do_craft -----


def test_do_craft_consumes_and_produces():
    r = get_recipe("plank")
    inv = Inventory()
    inv.add("wood", 2)
    out_id, out_qty = do_craft(r, inv)
    assert (out_id, out_qty) == ("plank", 4)
    assert inv.count("wood") == 1
    assert inv.count("plank") == 4


def test_do_craft_multi_input():
    # A real multi-input recipe (tool recipes are empty skeletons now).
    r = get_recipe("crafting_table")
    inv = Inventory()
    inv.add("plank", 5)  # 1 spare
    out_id, out_qty = do_craft(r, inv)
    assert (out_id, out_qty) == ("crafting_table", 1)
    assert inv.count("plank") == 1


# ----- station proximity (rule 17/18: no hard-coded coords) -----


class _StubPlayer:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def test_nearest_station_in_range():
    grid = BlockGrid()
    grid.place(4, 4, "crafting_table")
    assert nearest_station(grid, _StubPlayer(5, 5)) is True


def test_nearest_station_out_of_range():
    grid = BlockGrid()
    grid.place(4, 4, "crafting_table")
    assert nearest_station(grid, _StubPlayer(4 + STATION_RANGE + 1, 4)) is False


def test_nearest_station_ignores_other_blocks():
    grid = BlockGrid()
    grid.place(4, 4, "stone")
    assert nearest_station(grid, _StubPlayer(4, 4)) is False


def test_nearest_station_chebyshev_metric():
    grid = BlockGrid()
    grid.place(0, 0, "crafting_table")
    assert nearest_station(grid, _StubPlayer(2, 2)) is True  # dist 2 <= 3
    assert nearest_station(grid, _StubPlayer(3, 3)) is True  # dist 3 <= 3
    assert nearest_station(grid, _StubPlayer(4, 0)) is False  # dist 4


# ----- manager adapter (end-to-end progression) -----


class _FakeRepo:
    """Captures persistence calls; manager persists nothing without a db."""

    def __init__(self):
        self.saved = {}


async def _fake_persist(channel_id, user_id, item_id, qty):
    pass


def _manager(channel_id=1):
    from game.manager import GameManager

    m = GameManager.__new__(GameManager)  # skip discord-heavy init
    m.db = None
    m.runtimes = {}

    class _RT:
        pass

    from game.collision import Collision
    from game.manager import ScenarioRuntime

    state = _state()
    rt = ScenarioRuntime(
        channel_id=channel_id,
        message_id=None,
        state=state,
        map_data=_map(),
        collision=Collision(_map(), state.blocks),
    )
    m.runtimes[channel_id] = rt
    state.add_player(10, "Tester", *rt.map_data.spawn)
    m._persist_inventory = _fake_persist
    return m, rt


def test_full_progression_hand_to_axe():
    """The whole MVP loop: wood -> planks -> table -> place -> axe."""
    import asyncio

    async def run():
        m, rt = _manager()
        inv = m.get_inventory(1, 10)
        inv.add("wood", 2)

        # 1. Hand-craft planks (1 wood -> 4 planks).
        ok, reason, out, _ = await m.craft_item(1, 10, "plank")
        assert ok and out == "plank" and inv.count("plank") == 4

        # 2. Hand-craft the crafting table (4 planks).
        ok, reason, out, _ = await m.craft_item(1, 10, "crafting_table")
        assert ok and out == "crafting_table" and inv.count("crafting_table") == 1

        # 3. Table recipes blocked while not placed near the player.
        ok, reason, _, _ = await m.craft_item(1, 10, "wood_axe")
        assert not ok and reason == "no_station"

        # 4. Place the table within range of the player (spawn tile + 2).
        player = rt.state.get_player(10)
        rt.state.blocks.place(player.x + 2, player.y, "crafting_table")

        # 5. The axe recipe is an empty skeleton now — stays locked.
        ok, reason, _, _ = await m.craft_item(1, 10, "wood_axe")
        assert not ok and reason == "missing_materials"

    asyncio.run(run())


def test_craft_unknown_recipe_and_no_player():
    import asyncio

    async def run():
        m, rt = _manager()
        ok, reason, _, _ = await m.craft_item(1, 10, "nope")
        assert not ok and reason == "unknown_recipe"
        ok, reason, _, _ = await m.craft_item(1, 999, "plank")
        assert not ok and reason == "no_player"

    asyncio.run(run())


def test_crafted_planks_persist_and_reload():
    """Crafted output must survive the same DB round-trip as gathered loot."""
    import asyncio
    import tempfile

    from persistence.database import Database
    from persistence.migrations import migrate
    from persistence.repositories import load_all_inventory

    async def run():
        with tempfile.TemporaryDirectory() as directory:
            db = Database(str(Path(directory) / "game.db"))
            await db.connect()
            await migrate(db)
            m, rt = _manager()
            m.db = db
            # _manager() disables persistence for pure adapter tests; this
            # case intentionally restores the real manager method.
            from game.manager import GameManager
            m._persist_inventory = GameManager._persist_inventory.__get__(m)
            inv = m.get_inventory(1, 10)
            inv.add("wood", 1)
            ok, reason, out_id, out_qty = await m.craft_item(1, 10, "plank")
            assert ok and (out_id, out_qty) == ("plank", 4)
            saved = await load_all_inventory(db, 1)
            assert saved[10]["plank"] == 4
            assert saved[10].get("wood", 0) == 0
            await db.close()

    asyncio.run(run())
