"""Repro for the "result slot never empties" craft bug (user 29/09).

Scenario: craft a stick, DON'T collect it, craft something else, then come
back and collect the parked stick — the result slot must empty exactly once
and the bag must gain exactly the crafted quantity (no infinite minting).
"""
from __future__ import annotations

import asyncio

from game.manager import GameManager
from game.map_loader import load_map
from game.state import GameState
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "maps"


def _manager_and_rt():
    from game.collision import Collision
    from game.blocks import BlockGrid

    map_data = load_map("test-map", ASSETS)
    state = GameState(1, map_data.map_id)
    player = state.add_player(1, "A", 5, 5)
    player.is_web = True
    rt = GameManager.create_runtime.__wrapped__(None, None) if False else None
    # Build a minimal manager without the Discord machinery.
    mgr = GameManager.__new__(GameManager)
    from game.manager import ScenarioRuntime
    from game.resources import ResourceGrid
    from game.terrain import TerrainGrid

    rt = ScenarioRuntime(
        channel_id=1,
        message_id=None,
        state=state,
        map_data=map_data,
        collision=Collision(map_data, state.blocks),
    )
    mgr.runtimes = {1: rt}
    mgr.side_runtimes = {}
    mgr.db = None
    # Give the player wood + stone and a nearby crafting table recipe need.
    inv = mgr.get_inventory(1, 1)
    inv.add("plank", 10)
    inv.add("stone", 10)
    inv.add("wood", 10)
    return mgr, rt, player, inv


def test_craft_without_collecting_then_craft_again_then_collect():
    async def run():
        mgr, rt, player, inv = _manager_and_rt()
        # Stick recipe needs planks; no table needed for stick (verify).
        from game.crafting import get_recipe

        stick = get_recipe("stick")
        assert stick is not None

        # 1) craft the stick -> parked
        res = await mgr.craft_from_inputs(1, 1, [("plank", 2)])
        assert res["ok"], res
        parked = rt.craft_results[1]
        assert parked == {"id": "stick", "qty": 4}, parked

        # 2) craft a DIFFERENT item without collecting: furnace needs a
        #    station on this bare map (no_station), so use a recipe with no
        #    table gate but a DIFFERENT output — plank from wood.
        res2 = await mgr.craft_from_inputs(1, 1, [("wood", 1)])
        from game.crafting import find_recipe_by_inputs

        r_plank = find_recipe_by_inputs([("wood", 1)])
        assert r_plank is not None and r_plank.output[0] == "plank"
        assert not res2["ok"] and res2["reason"] == "result_slot_occupied", res2
        assert rt.craft_results[1] == {"id": "stick", "qty": 4}
        # Same-output craft (stick again): stacks on the parked result.
        res3 = await mgr.craft_from_inputs(1, 1, [("plank", 2)])
        assert res3["ok"], res3
        assert rt.craft_results[1] == {"id": "stick", "qty": 8}, rt.craft_results

        # 3) collect ONCE -> whole stack into the bag, slot cleared.
        res4 = await mgr.craft_collect(1, 1, slot=None)
        assert res4["ok"], res4
        assert 1 not in rt.craft_results, rt.craft_results
        assert inv.count("stick") == 8, inv.items

        # 4) A SECOND collect is empty_result — no minting, slot stays empty.
        res5 = await mgr.craft_collect(1, 1, slot=None)
        assert not res5["ok"] and res5["reason"] == "empty_result", res5
        assert inv.count("stick") == 8, inv.items
        assert 1 not in rt.craft_results

    asyncio.run(run())
