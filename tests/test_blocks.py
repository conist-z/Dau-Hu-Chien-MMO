import asyncio
import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game import blocks as blocks_mod
from game.actions import BreakBlockAction, MoveAction, PlaceBlockAction
from game.blocks import (BLOCK_REGISTRY, BlockDef, BlockGrid,
                         PLACEABLE_BLOCK_IDS, get_block, next_placeable_block)
from game.collision import Collision
from game.inventory import Inventory
from game.map_loader import load_map
from game.rules import apply_break_block, apply_move, apply_place_block
from game.state import Direction, GameState
from persistence.database import Database
from persistence.migrations import migrate
from persistence.repositories import load_blocks, save_block
from rendering.renderer import block_tile_image

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _duel():
    """A fresh scenario with one player at spawn facing EAST."""
    state = GameState(1, "test-map")
    state.add_player(10, "A", 5, 5)
    state.players[10].direction = "EAST"
    return state


def test_registry_defaults():
    assert "stone" in BLOCK_REGISTRY and "torch" in BLOCK_REGISTRY
    assert get_block("stone").solid is True
    # User rule: EVERY placed block blocks movement (no walk-through).
    assert get_block("torch").solid is True
    assert "stone" in PLACEABLE_BLOCK_IDS
    assert get_block("nope") is None


def test_grid_place_remove_roundtrip():
    g = BlockGrid()
    assert len(g) == 0
    assert g.place(3, 4, "stone") is True
    assert g.place(3, 4, "wood") is False  # already covered
    assert g.get(3, 4) == "stone"
    assert g.solid_at(3, 4) is True
    assert g.remove(3, 4) == "stone"       # ground shows again
    assert g.get(3, 4) is None and not g.solid_at(3, 4)
    g.place(1, 1, "stone")
    g2 = BlockGrid.from_dict(g.to_dict())
    assert g2.get(1, 1) == "stone"
    g.clear()
    assert len(g) == 0


def test_solid_block_blocks_movement_and_torch_does_not():
    state = _duel()
    col = Collision(load_map("test-map", ASSETS), state.blocks)
    state.blocks.place(6, 5, "stone")  # facing tile (east of 5,5)
    assert col.is_walkable(6, 5) is False
    res = apply_move(state, MoveAction(10, Direction.EAST), col)
    assert res.success is False and res.reason == "blocked"
    # All blocks are solid now (user rule): torch included.
    state2 = _duel()
    col2 = Collision(load_map("test-map", ASSETS), state2.blocks)
    state2.blocks.place(6, 5, "torch")
    assert col2.is_walkable(6, 5) is False


def test_place_consumes_material_and_break_returns_it():
    original = blocks_mod.CREATIVE_MODE
    blocks_mod.CREATIVE_MODE = False  # survival mode for this test
    try:
        state = _duel()
        col = Collision(load_map("test-map", ASSETS), state.blocks)
        inv = Inventory()
        inv.add("stone", 2)

        res = apply_place_block(state, PlaceBlockAction(10, "stone"), col, state.blocks, inv)
        assert res.success and res.pos == (6, 5) and res.block_id == "stone"
        assert inv.count("stone") == 1            # material consumed
        assert state.blocks.get(6, 5) == "stone"  # covers the ground

        # Placing twice on the same tile fails.
        res2 = apply_place_block(state, PlaceBlockAction(10, "stone"), col, state.blocks, inv)
        assert not res2.success and res2.reason == "blocked_tile"
        assert inv.count("stone") == 1  # no material lost

        # Breaking removes the overlay (ground shows again) and refunds material.
        # Stone blocks require a pickaxe (dirt tier or better).
        inv.add("dirt_pickaxe", 1)
        res3 = apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        assert res3.success and res3.pos == (6, 5) and res3.block_id == "stone"
        assert state.blocks.get(6, 5) is None
        assert inv.count("stone") == 2
    finally:
        blocks_mod.CREATIVE_MODE = original


def test_survival_mode_default_places_consume_material():
    assert blocks_mod.CREATIVE_MODE is False  # default: survival (materials on)
    state = _duel()
    col = Collision(load_map("test-map", ASSETS), state.blocks)
    inv = Inventory()  # empty bag
    engine_res = apply_place_block(
        state, PlaceBlockAction(10, "stone"), col, state.blocks, inv
    )
    assert not engine_res.success and engine_res.reason == "no_material"

    # Creative flag ON: same placement ignores the bag entirely.
    inv.add("stone", 1)
    blocks_mod.CREATIVE_MODE = True
    try:
        state2 = _duel()
        col2 = Collision(load_map("test-map", ASSETS), state2.blocks)
        res = apply_place_block(
            state2, PlaceBlockAction(10, "stone"), col2, state2.blocks, Inventory()
        )
        assert res.success and state2.blocks.get(6, 5) == "stone"
        assert inv.count("stone") == 1  # nothing consumed
    finally:
        blocks_mod.CREATIVE_MODE = False


def test_place_without_material_and_break_empty_tile():
    # Survival mode (default): placing with an empty bag is refused.
    state = _duel()
    col = Collision(load_map("test-map", ASSETS), state.blocks)
    res = apply_place_block(state, PlaceBlockAction(10, "wood"), col, state.blocks, Inventory())
    assert not res.success and res.reason == "no_material"
    # Breaking a BARE tile still fails in every mode (fresh scenario, no block).
    state2 = _duel()
    res2 = apply_break_block(state2, BreakBlockAction(10), state2.blocks, Inventory())
    assert not res2.success and res2.reason == "no_block"


def test_place_rejects_ground_and_other_players():
    state = _duel()
    col = Collision(load_map("test-map", ASSETS), state.blocks)
    inv = Inventory()
    inv.add("stone", 9)
    # (0,0)-column is collision ground on test-map: face WEST -> tile (4,5)... use NORTH edge instead.
    state.players[10].x, state.players[10].y = 5, 0
    state.players[10].direction = "NORTH"  # (5,-1) out of bounds / collision row
    res = apply_place_block(state, PlaceBlockAction(10, "stone"), col, state.blocks, inv)
    assert not res.success and res.reason == "blocked_tile"
    # Another player standing on the facing tile.
    state2 = _duel()
    col2 = Collision(load_map("test-map", ASSETS), state2.blocks)
    inv2 = Inventory()
    inv2.add("stone", 1)
    state2.add_player(20, "B", 6, 5)
    res2 = apply_place_block(state2, PlaceBlockAction(10, "stone"), col2, state2.blocks, inv2)
    assert not res2.success and res2.reason == "tile_occupied"
    assert state2.blocks.get(6, 5) is None and inv2.count("stone") == 1


def test_place_out_of_bounds():
    state = _duel()
    state.players[10].x, state.players[10].y = 9, 5
    state.players[10].direction = "EAST"
    col = Collision(load_map("test-map", ASSETS), state.blocks)
    inv = Inventory()
    inv.add("stone", 1)
    res = apply_place_block(state, PlaceBlockAction(10, "stone"), col, state.blocks, inv)
    assert not res.success and res.reason == "blocked_tile"


def test_block_cycling():
    assert next_placeable_block("stone") != "stone"
    # Cycles through every placeable block and returns to the start.
    seen = ["stone"]
    cur = "stone"
    for _ in range(len(PLACEABLE_BLOCK_IDS)):
        cur = next_placeable_block(cur)
        seen.append(cur)
    assert seen[-1] == "stone" and len(set(seen)) == len(seen) - 1
    assert next_placeable_block("unknown") == PLACEABLE_BLOCK_IDS[0]


def test_block_tile_image_colors():
    bdef = BlockDef("t", "T", "🧱", (200, 100, 50))
    img = block_tile_image(bdef, 32)
    assert img.size == (32, 32)
    # Centre pixel keeps the block fill colour.
    assert img.getpixel((16, 16))[:3] == (200, 100, 50)


def test_renderer_draws_block_overlay_over_ground():
    from PIL import Image

    from rendering.avatar import AvatarCache
    from rendering.renderer import Renderer

    renderer = Renderer(ASSETS, AvatarCache(ASSETS), tile_size=32)
    ground = Image.new("RGBA", (64, 32), (10, 20, 30, 255))  # 2x1 tiles
    blocks = BlockGrid()
    blocks.place(1, 0, "stone")  # covers tile (1,0); ground shows at (0,0)
    blits = renderer._block_blits(blocks, 0, 0, 2, 1)
    comp, _ = renderer._compose_full(ground, [], blits)
    # Covered tile shows the block's actual tile image (texture sprite if
    # assets/blocks/stone.png exists, else the flat color fill).
    expected = renderer._block_tile("stone").convert("RGBA")
    assert comp.getpixel((48, 16))[:3] == expected.getpixel((16, 16))[:3]  # covered
    assert comp.getpixel((16, 16))[:3] == (10, 20, 30)              # bare ground
    # Outside the viewport nothing is drawn.
    assert renderer._block_blits(blocks, 0, 0, 1, 1) == []


def test_blocks_persist_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        db = Database(str(Path(d) / "game.db"))

        async def main():
            await db.connect()
            await migrate(db)
            await save_block(db, 1, 6, 5, "stone")
            await save_block(db, 1, 7, 5, "wood")
            rows = await load_blocks(db, 1)
            assert sorted(rows) == [(6, 5, "stone"), (7, 5, "wood")]
            from persistence.repositories import delete_block

            await delete_block(db, 1, 6, 5)
            assert await load_blocks(db, 1) == [(7, 5, "wood")]
            await db.close()

        asyncio.run(main())


def test_manager_dispatch_place_and_break_with_db():
    with tempfile.TemporaryDirectory() as d:
        from game.manager import GameManager
        from persistence.database import Database as DB

        mgr = GameManager(ASSETS)
        db = DB(str(Path(d) / "game.db"))

        async def main():
            await db.connect()
            await migrate(db)
            mgr.db = db
            rt = mgr.create_runtime(1, "test-map")
            rt.state.add_player(10, "A", 5, 5)
            rt.state.players[10].direction = "EAST"
            inv = mgr.get_inventory(1, 10)
            inv.add("wood", 1)
            _, res = await mgr.dispatch(1, PlaceBlockAction(10, "wood"))
            assert res.success and rt.state.blocks.get(6, 5) == "wood"
            assert await load_blocks(db, 1) == [(6, 5, "wood")]
            _, res2 = await mgr.dispatch(1, BreakBlockAction(10))
            assert res2.success and rt.state.blocks.get(6, 5) is None
            assert mgr.get_inventory(1, 10).count("wood") == 1  # refunded
            assert await load_blocks(db, 1) == []
            await db.close()

        asyncio.run(main())
