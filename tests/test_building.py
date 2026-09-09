import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game import blocks as blocks_mod
from game.actions import AimAction, AimResetAction, PlaceBlockAction, TurnAction
from game.blocks import BLOCK_REGISTRY, get_block
from game.collision import Collision
from game.inventory import Inventory
from game.map_loader import load_map
from game.rules import AIM_RANGE, apply_aim, apply_aim_reset, apply_place_block, apply_turn
from game.state import Direction, GameState, next_clockwise

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _builder():
    """Fresh scenario: player at (5,5) facing EAST, survival materials."""
    state = GameState(1, "test-map")
    state.add_player(10, "A", 5, 5)
    state.players[10].direction = "EAST"
    col = Collision(load_map("test-map", ASSETS), state.blocks)
    inv = Inventory()
    inv.add("stone", 10)
    return state, col, inv


def test_floor_block_is_walkable_overlay():
    assert "floor" in BLOCK_REGISTRY
    bdef = get_block("floor")
    # User rule: every placed block is solid (no walk-through), floor included.
    assert bdef.placeable is True and bdef.solid is True


def test_next_clockwise_cycles_all_8_directions():
    seen = [Direction.SOUTH]
    cur = Direction.SOUTH
    for _ in range(8):
        cur = next_clockwise(cur)
        seen.append(cur)
    assert seen[-1] == Direction.SOUTH            # full ring back to start
    assert len(set(seen[:-1])) == 8               # every direction visited once


def test_turn_action_rotates_in_place():
    state, _, _ = _builder()
    res = apply_turn(state, TurnAction(10, Direction.NORTH))
    p = state.players[10]
    assert res.success is True and res.state_changed is True
    assert p.direction == "NORTH"
    assert (p.x, p.y) == (5, 5)                   # never steps


def test_turn_same_direction_is_cheap_ack():
    state, _, _ = _builder()
    state.players[10].direction = "EAST"
    res = apply_turn(state, TurnAction(10, Direction.EAST))
    assert res.success is True and res.state_changed is False


def test_aim_moves_cursor_and_clamps_to_range():
    state, _, _ = _builder()
    p = state.players[10]
    # Walk the cursor 5 tiles east: clamped at AIM_RANGE.
    for _ in range(5):
        res = apply_aim(state, AimAction(10, 1, 0))
    assert p.aim_active is True
    assert (p.aim_dx, p.aim_dy) == (AIM_RANGE, 0)
    assert res.pos == (5 + AIM_RANGE, 5)
    # Chebyshev clamp on both axes.
    apply_aim(state, AimAction(10, 0, 10))
    assert (p.aim_dx, p.aim_dy) == (AIM_RANGE, AIM_RANGE)


def test_aim_cursor_is_relative_to_player():
    state, _, _ = _builder()
    apply_aim(state, AimAction(10, 2, 0))
    p = state.players[10]
    assert p.aim_dx == 2
    p.x = 9  # player steps; the cursor offset follows them
    assert (p.x + p.aim_dx, p.y + p.aim_dy) == (11, 5)


def test_aim_reset_hides_cursor():
    state, _, _ = _builder()
    apply_aim(state, AimAction(10, 1, 1))
    res = apply_aim_reset(state, AimResetAction(10))
    p = state.players[10]
    assert res.state_changed is True
    assert p.aim_active is False and (p.aim_dx, p.aim_dy) == (0, 0)


def test_place_at_offset_targets_cursor_tile():
    original = blocks_mod.CREATIVE_MODE
    blocks_mod.CREATIVE_MODE = False
    try:
        state, col, inv = _builder()
        # Aim two tiles north, then place: block lands on the cursor tile.
        apply_aim(state, AimAction(10, 0, -2))
        res = apply_place_block(
            state, PlaceBlockAction(10, "stone", dx=0, dy=-2), col, state.blocks, inv
        )
        assert res.success is True and res.pos == (5, 3)
        assert state.blocks.get(5, 3) == "stone"
        assert inv.count("stone") == 9
    finally:
        blocks_mod.CREATIVE_MODE = original


def test_place_offset_out_of_range_and_own_tile():
    state, col, inv = _builder()
    res = apply_place_block(
        state, PlaceBlockAction(10, "stone", dx=AIM_RANGE + 1, dy=0),
        col, state.blocks, inv,
    )
    assert res.success is False and res.reason == "out_of_range"
    res2 = apply_place_block(
        state, PlaceBlockAction(10, "stone", dx=0, dy=0), col, state.blocks, inv
    )
    assert res2.success is False and res2.reason == "own_tile"
    assert len(state.blocks) == 0 and inv.count("stone") == 10  # nothing lost


def test_break_block_targets_aim_cursor_tile():
    """🔨 break acts on the aim-cursor tile while one is active (the square
    the renderer highlights), not blindly the facing tile."""
    original = blocks_mod.CREATIVE_MODE
    blocks_mod.CREATIVE_MODE = False
    try:
        state, col, inv = _builder()
        state.blocks.place(5, 4, "stone")   # north of player
        state.blocks.place(6, 5, "floor")   # facing tile (east)
        from game.actions import BreakBlockAction
        from game.rules import apply_aim, apply_break_block

        apply_aim(state, AimAction(10, 0, -1))
        inv.add("dirt_pickaxe", 1)  # stone blocks require a pickaxe
        res = apply_break_block(state, BreakBlockAction(10), state.blocks, inv)
        assert res.success is True and res.pos == (5, 4)
        assert state.blocks.get(5, 4) is None
        assert state.blocks.get(6, 5) == "floor"  # facing tile untouched
        assert inv.count("stone") == 11           # material returned (10 + 1)
    finally:
        blocks_mod.CREATIVE_MODE = original


def test_attack_falls_back_to_breaking_block():
    """⚔️ with NO hostile nearby breaks the block on the target square."""
    from game.actions import AttackAction
    from game.rules import apply_attack

    state, _, inv = _builder()
    state.blocks.place(6, 5, "stone")  # facing tile (east of 5,5)
    res = apply_attack(state, AttackAction(10), state.blocks, inv)
    assert res.success is True and res.state_changed is True
    assert res.damage == 0 and res.block_id == "stone" and res.pos == (6, 5)
    assert state.blocks.get(6, 5) is None
    assert inv.count("stone") == 11  # 10 starter + 1 returned


def test_manager_routes_aim_and_turn_actions():
    from game.manager import GameManager

    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 5, 5)
    rt.state.players[10].direction = "EAST"

    async def main():
        _, res = await mgr.dispatch(1, AimAction(10, 2, 1))
        assert res.success is True
        assert rt.state.players[10].aim_active is True
        assert (rt.state.players[10].aim_dx, rt.state.players[10].aim_dy) == (2, 1)
        # Aim is view state: it must NOT mark the scenario dirty.
        assert rt.dirty is False
        _, res2 = await mgr.dispatch(1, TurnAction(10, Direction.SOUTH))
        assert res2.state_changed is True
        assert rt.state.players[10].direction == "SOUTH"
        _, res3 = await mgr.dispatch(1, AimResetAction(10))
        assert rt.state.players[10].aim_active is False

    asyncio.run(main())


def test_renderer_ghost_and_aim_target():
    from rendering.avatar import AvatarCache
    from rendering.renderer import Renderer

    renderer = Renderer(ASSETS, AvatarCache(ASSETS), tile_size=32)
    ghost = renderer._ghost_tile("stone")
    solid = renderer._block_tile("stone")
    assert ghost is not None and ghost.size == solid.size
    # The ghost is a faded copy of the solid tile (same RGB, much lower alpha).
    ga = ghost.getpixel((16, 16))
    sa = solid.convert("RGBA").getpixel((16, 16))
    assert ga[:3] == sa[:3]
    assert ga[3] < sa[3] * 0.6

    state, _, _ = _builder()
    assert renderer._aim_target(state, 10) is None  # aim inactive by default
    apply_aim(state, AimAction(10, 1, -2))
    tile, gimg = renderer._aim_target(state, 10)
    assert tile == (6, 3) and gimg is not None
    assert renderer._aim_target(state, None) is None


def test_map_view_build_labels_track_screen_state():
    from discord_ui.map_view import MapView
    from game.manager import PlayerScreen

    class RT:
        def __init__(self):
            self.screens = {}
            self.step_size = 1
            self.auto_running = False
            self.auto_armed = False
            self.hotbars = {}
            self.inventories = {}

    class Mgr:
        def __init__(self, rt):
            self._rt = rt

        def get_runtime(self, cid):
            return self._rt

        def get_runtime_for(self, cid, user_id=None):
            return self._rt

    rt = RT()
    rt.screens[42] = PlayerScreen(user_id=42)
    view = MapView(100, Mgr(rt), 42)
    # Default (move mode): ✅ disabled, 🧱 Build toggle unlabelled.
    assert view.b_place.disabled is True
    assert str(view.b_build.emoji) == "🧱"
    # Build Mode ON: ✅ enabled, 🧱 label flips to ON.
    rt.screens[42].build_mode = True
    view._apply_tool_labels()
    assert view.b_place.disabled is False
    assert str(view.b_place.emoji) == "✅"
    assert str(view.b_build.emoji) == "🧱"
    # Build Mode ON: 🧱 label flips to ON.
    rt.screens[42].build_mode = True
    view._apply_tool_labels()
    assert view.b_build.label == "ON"
