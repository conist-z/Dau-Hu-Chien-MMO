"""Smelting system tests — pure logic (game/smelting.py) + persistence.

Covers: fuel math, pay-upfront arming, deadline completion, output slot,
station gate, restart restore (rule 14), plus the new tool tier / drops data.
"""

import asyncio
import time

import pytest

from game import smelting
from game.blocks import BlockGrid
from game.inventory import Inventory
from game.smelting import FurnaceState, FUEL_REGISTRY, SMELT_REGISTRY
from game.state import GameState, Player


def make_furnace() -> FurnaceState:
    return FurnaceState(x=5, y=5)


# ----- registry data ----------------------------------------------------------


def test_fuel_registry_seconds_ordering():
    # Strongest fuel burns longest (plan table: coal 40 > charcoal 20 > wood 10 > plank 5 > stick 3).
    assert FUEL_REGISTRY["coal"].seconds == 40.0
    assert FUEL_REGISTRY["charcoal"].seconds == 20.0
    assert FUEL_REGISTRY["wood"].seconds == 10.0
    assert FUEL_REGISTRY["plank"].seconds == 5.0
    assert FUEL_REGISTRY["stick"].seconds == 3.0


def test_smelt_registry_inputs_outputs():
    assert SMELT_REGISTRY["smelt_iron"].input_item == "iron_ore"
    assert SMELT_REGISTRY["smelt_iron"].output_item == "iron_ingot"
    assert SMELT_REGISTRY["smelt_iron"].seconds == 10.0
    assert SMELT_REGISTRY["smelt_charcoal"].input_item == "wood"
    assert SMELT_REGISTRY["smelt_charcoal"].output_item == "charcoal"
    assert SMELT_REGISTRY["cook_meat"].input_item == "raw_meat"
    assert SMELT_REGISTRY["cook_meat"].output_item == "cooked_meat"
    assert SMELT_REGISTRY["cook_meat"].seconds == 8.0


def test_new_items_registered():
    from game.items import get_item

    for iid, name in [
        ("iron_ore", "Quặng sắt"), ("coal", "Than đá"),
        ("iron_ingot", "Thỏi sắt"), ("charcoal", "Than củi"),
        ("raw_meat", "Thịt sống"), ("cooked_meat", "Thịt nướng"),
    ]:
        item = get_item(iid)
        assert item is not None, iid
        assert item.name == name


def test_cooked_meat_is_consumable():
    from game.items import apply_effect

    p = Player(user_id=1, display_name="t")
    p.hp = 50
    ok, reason = apply_effect(p, "cooked_meat")
    assert ok, reason
    assert p.hp == 80


def test_iron_tool_tier():
    from game.tools import MATERIALS, SWORD_TIER_DAMAGE, parse_tool_id

    assert "iron" in MATERIALS
    td = parse_tool_id("iron_sword")
    assert td is not None and td.material == "iron"
    # Tier ladder: iron beats wood, steel tops the ladder (40+ one-shots).
    assert SWORD_TIER_DAMAGE["iron"] > SWORD_TIER_DAMAGE["wood"]
    assert SWORD_TIER_DAMAGE["steel"] >= 40
    # Tool recipes exist as skeletons; inputs land with the material chain.
    from game.crafting import RECIPE_REGISTRY

    r = RECIPE_REGISTRY["iron_sword"]
    assert r.requires_table and r.output == ("iron_sword", 1)


def test_ore_and_zombie_drops_extended():
    from game.resources import NODE_DEFS
    from game.zombies import ZOMBIE_DROP_TABLE

    ore_items = {d[0] for d in NODE_DEFS["ore"].drops}
    assert {"iron_ore", "coal", "stone"} <= ore_items
    zomb_items = {d[0] for d in ZOMBIE_DROP_TABLE}
    assert "raw_meat" in zomb_items


def test_torch_recipe_by_hand():
    from game.crafting import RECIPE_REGISTRY

    r = RECIPE_REGISTRY["torch"]
    assert not r.requires_table
    assert ("coal", 1) in r.inputs
    assert r.output == ("torch", 4)


# ----- put_input / add_fuel ----------------------------------------------------


def test_put_input_rejects_non_smeltable():
    f = make_furnace()
    inv = Inventory({"stone": 10})
    ok, reason = smelting.put_input(f, inv, "stone")
    assert not ok and reason == "not_smeltable"
    assert inv.count("stone") == 10  # nothing consumed


def test_put_input_moves_items_and_stacks():
    f = make_furnace()
    inv = Inventory({"iron_ore": 5})
    ok, _ = smelting.put_input(f, inv, "iron_ore", 2)
    assert ok
    assert inv.count("iron_ore") == 3
    assert f.input_item == "iron_ore" and f.input_qty == 2
    ok, _ = smelting.put_input(f, inv, "iron_ore", 1)
    assert ok and f.input_qty == 3


def test_put_input_rejects_different_item_in_slot():
    f = make_furnace()
    inv = Inventory({"iron_ore": 5, "raw_meat": 5})
    smelting.put_input(f, inv, "iron_ore", 1)
    ok, reason = smelting.put_input(f, inv, "raw_meat", 1)
    assert not ok and reason == "slot_occupied"
    assert inv.count("raw_meat") == 5


def test_add_fuel_burns_seconds_and_consumes_item():
    f = make_furnace()
    inv = Inventory({"coal": 2})
    ok, reason = smelting.add_fuel(f, inv, "coal", 2)
    assert ok, reason
    assert inv.is_empty()
    assert f.fuel_seconds == 80.0


def test_add_fuel_rejects_non_fuel():
    f = make_furnace()
    inv = Inventory({"iron_ore": 1})
    ok, reason = smelting.add_fuel(f, inv, "iron_ore")
    assert not ok and reason == "not_fuel"


# ----- arming + ticking --------------------------------------------------------


def test_ensure_burning_requires_enough_fuel_seconds():
    f = make_furnace()
    f.input_item, f.input_qty = "iron_ore", 1
    f.fuel_seconds = 5.0  # iron smelt needs 10s
    smelting.ensure_burning(f, now=1000.0)
    assert not f.busy  # cannot afford the smelt

    f.fuel_seconds = 10.0
    smelting.ensure_burning(f, now=1000.0)
    assert f.busy
    # Pay-upfront: the seconds were deducted.
    assert f.fuel_seconds == 0.0
    assert f.smelt_deadline == 1010.0


def test_full_smelt_cycle_iron():
    f = make_furnace()
    inv = Inventory({"iron_ore": 2, "coal": 1})
    smelting.put_input(f, inv, "iron_ore", 2)
    smelting.add_fuel(f, inv, "coal", 1)  # 40s = 4 smelts worth
    now = 1000.0
    smelting.ensure_burning(f, now)
    assert f.busy and f.smelt_deadline == now + 10.0

    # Not done yet: nothing lands.
    assert not smelting.tick(f, now + 9.9)
    assert f.output_qty == 0

    # Done: one ingot, next input re-arms immediately (fuel remains).
    assert smelting.tick(f, now + 10.0)
    assert f.output_item == "iron_ingot" and f.output_qty == 1
    assert f.input_qty == 1
    assert f.busy  # re-armed for the second ore
    # 40s coal: -10 (first smelt) -10 (re-arm) = 20s left.
    assert f.fuel_seconds == pytest.approx(20.0)


def test_output_slots_accumulate_same_item():
    """The output slot accumulates the SAME item; only a different output
    item (or the 64 cap) halts smelting."""
    f = make_furnace()
    f.input_item, f.input_qty = "iron_ore", 1
    f.fuel_seconds = 100.0
    f.output_item, f.output_qty = "iron_ingot", 10
    smelting.ensure_burning(f, now=0.0)
    assert f.busy  # same output item: keeps smelting, slot accumulates

    f2 = make_furnace()
    f2.input_item, f2.input_qty = "iron_ore", 1
    f2.fuel_seconds = 100.0
    f2.output_item, f2.output_qty = "cooked_meat", 1  # different item
    smelting.ensure_burning(f2, now=0.0)
    assert not f2.busy  # smelting halts until the meat is taken

    f3 = make_furnace()
    f3.input_item, f3.input_qty = "iron_ore", 1
    f3.fuel_seconds = 100.0
    f3.output_item, f3.output_qty = "iron_ingot", 64  # cap
    smelting.ensure_burning(f3, now=0.0)
    assert not f3.busy


def test_take_output_moves_to_bag():
    f = make_furnace()
    f.output_item, f.output_qty = "cooked_meat", 3
    inv = Inventory()
    ok, reason = smelting.take_output(f, inv)
    assert ok, reason
    assert inv.count("cooked_meat") == 3
    assert f.output_qty == 0 and f.output_item is None


def test_take_output_empty():
    f = make_furnace()
    ok, reason = smelting.take_output(f, Inventory())
    assert not ok and reason == "empty_output"


def test_tick_persists_deadline_through_fake_downtime():
    """A smelt armed before 'shutdown' completes after restart + elapsed time."""
    f = FurnaceState.from_dict({
        "x": 1, "y": 2, "input_item": "raw_meat", "input_qty": 1,
        "fuel_seconds": 0.0, "smelt_deadline": time.time() - 5,
    })
    assert smelting.tick(f, time.time())
    assert f.output_item == "cooked_meat" and f.output_qty == 1
    assert not f.busy


def test_furnace_dict_roundtrip():
    f = make_furnace()
    f.input_item, f.input_qty = "iron_ore", 2
    f.fuel_item, f.fuel_seconds = "coal", 12.5
    f.output_item, f.output_qty = "iron_ingot", 1
    f.smelt_deadline = 1234.5
    f2 = FurnaceState.from_dict(f.to_dict())
    assert f2 == f


# ----- station gate ------------------------------------------------------------


class _Player:
    def __init__(self, x, y):
        self.x, self.y = x, y


def test_nearest_furnace_found_in_range():
    blocks = BlockGrid()
    blocks.place(10, 10, "furnace")
    assert smelting.nearest_furnace(blocks, _Player(12, 10)) == (10, 10)


def test_nearest_furnace_out_of_range_is_none():
    blocks = BlockGrid()
    blocks.place(10, 10, "furnace")
    assert smelting.nearest_furnace(blocks, _Player(20, 20)) is None


def test_nearest_furnace_ignores_other_blocks():
    blocks = BlockGrid()
    blocks.place(10, 10, "crafting_table")
    assert smelting.nearest_furnace(blocks, _Player(10, 11)) is None


# ----- GameState store + manager adapters ---------------------------------------


class _RT:
    """Minimal ScenarioRuntime stub for the manager furnace adapters."""

    def __init__(self):
        from game.collision import Collision  # noqa: F401

        self.channel_id = 1
        self.state = GameState(scenario_id=1, map_id="t")
        self.lock = asyncio.Lock()
        self.screens = {}  # _notify_inventory_change expects the dict


def test_manager_furnace_flow_end_to_end():
    from game.manager import GameManager

    mgr = GameManager(assets_dir="assets")
    rt = _RT()
    mgr.runtimes[1] = rt
    mgr.inventories = {}

    p = rt.state.add_player(7, "tester", x=5, y=5)
    assert p is not None
    rt.state.blocks.place(6, 5, "furnace")
    inv = Inventory({"iron_ore": 3, "coal": 1})
    mgr.get_inventory = lambda cid, uid: inv

    async def run():
        # put input + fuel
        ok, reason = await mgr.furnace_put_input(1, 7, 6, 5, "iron_ore", 2)
        assert (ok, reason) == (True, "ok"), reason
        ok, reason = await mgr.furnace_add_fuel(1, 7, 6, 5, "coal", 1)
        assert (ok, reason) == (True, "ok"), reason
        f = rt.state.furnaces[(6, 5)]
        smelting.ensure_burning(f, now=1000.0)

        # not done yet
        ok, reason, item, qty = await mgr.furnace_take_output(1, 7, 6, 5)
        assert not ok and reason == "empty_output"

        # complete the smelt
        smelting.tick(f, 1010.0)
        ok, reason, item, qty = await mgr.furnace_take_output(1, 7, 6, 5)
        assert (ok, item, qty) == (True, "iron_ingot", 1), reason
        assert inv.count("iron_ingot") == 1
        assert inv.count("iron_ore") == 1  # 3 - 2

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())


def test_manager_rejects_far_furnace():
    from game.manager import GameManager

    mgr = GameManager(assets_dir="assets")
    rt = _RT()
    mgr.runtimes[1] = rt
    rt.state.add_player(7, "tester", x=0, y=0)
    rt.state.blocks.place(50, 50, "furnace")
    inv = Inventory({"iron_ore": 1})
    mgr.get_inventory = lambda cid, uid: inv

    async def run():
        ok, reason = await mgr.furnace_put_input(1, 7, 50, 50, "iron_ore", 1)
        assert not ok and reason == "too_far"

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())


# ----- persistence ---------------------------------------------------------------


def test_furnace_persistence_roundtrip():
    """save_furnace -> load_furnaces reproduces the exact state (rule 14)."""
    from persistence.database import Database
    from persistence.migrations import migrate
    from persistence.repositories import load_furnaces, save_furnace

    async def run():
        import os
        import tempfile

        tmp = tempfile.mkdtemp()
        db = Database(os.path.join(tmp, "t.db"))
        await db.connect()
        await migrate(db)
        f = FurnaceState(x=3, y=4, input_item="raw_meat", input_qty=2,
                         fuel_item="coal", fuel_seconds=7.5,
                         output_item="cooked_meat", output_qty=1,
                         smelt_deadline=999.5)
        await save_furnace(db, 42, f)
        rows = await load_furnaces(db, 42)
        assert len(rows) == 1
        f2 = FurnaceState.from_dict(rows[0])
        assert f2 == f
        # Overwrite + delete.
        f.fuel_seconds = 1.0
        await save_furnace(db, 42, f)
        rows = await load_furnaces(db, 42)
        assert FurnaceState.from_dict(rows[0]).fuel_seconds == 1.0
        from persistence.repositories import delete_furnace

        await delete_furnace(db, 42, 3, 4)
        assert await load_furnaces(db, 42) == []
        await db.close()

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())
