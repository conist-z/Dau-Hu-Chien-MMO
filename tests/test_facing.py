import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PIL import Image

from game.actions import MoveAction
from game.blocks import BlockGrid
from game.collision import Collision
from game.map_loader import load_map
from game.rules import apply_move
from game.state import Direction, GameState
from rendering.avatar import AvatarCache
from rendering.renderer import Renderer, _draw_facing_dot

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _player_facing_east_blocked():
    """Player at (5,5) facing SOUTH; a solid stone wall sits to the EAST."""
    state = GameState(1, "test-map")
    state.add_player(10, "A", 5, 5)
    state.players[10].direction = "SOUTH"
    state.blocks.place(6, 5, "stone")
    col = Collision(load_map("test-map", ASSETS), state.blocks)
    return state, col


def test_turn_in_place_when_blocked():
    state, col = _player_facing_east_blocked()
    p = state.players[10]
    res = apply_move(state, MoveAction(10, Direction.EAST), col)
    # Blocked step, but the player still TURNS to face the wall.
    assert res.success is True and res.state_changed is True
    assert res.moved is False
    assert (p.x, p.y) == (5, 5)          # position unchanged
    assert p.direction == "EAST"         # now aiming at the wall


def test_blocked_same_direction_is_noop():
    state, col = _player_facing_east_blocked()
    state.players[10].direction = "EAST"  # already facing the wall
    res = apply_move(state, MoveAction(10, Direction.EAST), col)
    assert res.success is False and res.reason == "blocked"
    assert res.state_changed is False and res.moved is False
    assert (state.players[10].x, state.players[10].y) == (5, 5)


def test_free_move_sets_moved_flag_and_direction():
    state, col = _player_facing_east_blocked()
    state.blocks.remove(6, 5)  # clear the wall
    res = apply_move(state, MoveAction(10, Direction.EAST), col)
    assert res.success is True and res.moved is True and res.state_changed is True
    assert (state.players[10].x, state.players[10].y) == (6, 5)
    assert state.players[10].direction == "EAST"


def test_facing_dot_changes_token_east_side():
    cache = AvatarCache(ASSETS)
    base = cache.fallback("A", color=(200, 50, 50)).resize((32, 32)).convert("RGBA")
    with_dot = base.copy()
    _draw_facing_dot(with_dot, "EAST", 32)
    # The bead sits on the EAST edge of the token: that side changes.
    assert list(with_dot.getpixel((27, 16))) != list(base.getpixel((27, 16)))
    # Bead core is bright (white blended over the red token).
    assert with_dot.getpixel((27, 16))[0] > 190
    # The bead is TINY: far corners of the token are untouched.
    assert list(with_dot.getpixel((6, 16))) == list(base.getpixel((6, 16)))
    assert list(with_dot.getpixel((16, 4))) == list(base.getpixel((16, 4)))


def test_facing_dot_skips_unknown_direction():
    cache = AvatarCache(ASSETS)
    base = cache.fallback("A", color=(200, 50, 50)).resize((32, 32)).convert("RGBA")
    copy = base.copy()
    _draw_facing_dot(copy, "NOT_A_DIRECTION", 32)  # must not raise / not draw
    assert list(copy.getpixel((27, 16))) == list(base.getpixel((27, 16)))


def test_highlight_marks_facing_tile_only():
    state = GameState(1, "test-map")
    state.add_player(10, "A", 5, 5)
    state.players[10].direction = "EAST"
    renderer = Renderer(ASSETS, AvatarCache(ASSETS), tile_size=32)

    async def render(focus):
        return await renderer.render(
            state, load_map("test-map", ASSETS), members=None,
            camera=None, full=True, focus_user_id=focus,
        )

    plain = asyncio.run(render(None))
    focused = asyncio.run(render(10))
    plain, focused = plain.image, focused.image
    # Facing tile (6,5) carries the corner ticks; a far tile does not.
    probe_facing = (196, 162)  # top-left tick of the facing tile
    probe_far = (2 * 32 + 4, 2 * 32 + 4)
    assert (list(focused.getpixel(probe_facing)) !=
            list(plain.getpixel(probe_facing)))
    assert (list(focused.getpixel(probe_far)) ==
            list(plain.getpixel(probe_far)))


def test_block_blits_are_viewport_relative_when_camera_scrolls():
    """Regression: blocks are grid-anchored — in follow-camera space they must
    be pasted relative to the crop origin, not at absolute map pixels."""
    renderer = Renderer(ASSETS, AvatarCache(ASSETS), tile_size=32)
    blocks = BlockGrid()
    blocks.place(3, 1, "stone")
    # Camera window covering tiles x=2..3, y=0..1 (origin at tile (2,0)).
    blits = renderer._block_blits(blocks, 2, 0, 4, 2, ox=2, oy=0)
    assert blits == [((3 - 2) * 32, (1 - 0) * 32, blits[0][2])]

    # Full render: same block pasted at absolute map coords (origin 0,0).
    full_blits = renderer._block_blits(blocks, 0, 0, 4, 2)
    assert full_blits[0][0] == 3 * 32 and full_blits[0][1] == 1 * 32

    # End-to-end: compose a 2x2-tile viewport whose origin is tile (2,0) —
    # the base must cover tiles 2..3, so make it 4 tiles wide.
    base = Image.new("RGBA", (128, 64), (10, 20, 30, 255))
    comp, _ = renderer._compose_follow(base, 2, 0, 32, 64, 64, 1.0, [], blits)
    # Block lands exactly on its own grid cell inside the viewport (its tile
    # image — texture sprite or flat fill — covers the cell center)...
    expected = renderer._block_tile("stone").convert("RGBA").getpixel((16, 16))[:3]
    assert comp.getpixel(((3 - 2) * 32 + 16, (1 - 0) * 32 + 16))[:3] == expected
    # ...and a cell with no block keeps the bare ground.
    assert comp.getpixel((16, 16))[:3] == (10, 20, 30)


def test_indicators_fade_while_travelling():
    from PIL import Image as PILImage

    from rendering.renderer import _draw_facing_dot, _draw_tile_highlight

    # Highlight: dim ticks are visibly fainter than full ticks.
    full = PILImage.new("RGBA", (32, 32), (10, 20, 30, 255))
    dim = PILImage.new("RGBA", (32, 32), (10, 20, 30, 255))
    _draw_tile_highlight(full, 0, 0, 32)
    _draw_tile_highlight(dim, 0, 0, 32, alpha_scale=0.35)
    p_full, p_dim = full.getpixel((2, 2)), dim.getpixel((2, 2))
    assert p_full != p_dim
    assert p_dim[0] < p_full[0]  # less warm-white blended in

    # Facing bead: dim bead is fainter than the full bead.
    base = AvatarCache(ASSETS).fallback("A", color=(200, 50, 50)).resize((32, 32)).convert("RGBA")
    full_dot, dim_dot = base.copy(), base.copy()
    _draw_facing_dot(full_dot, "EAST", 32)
    _draw_facing_dot(dim_dot, "EAST", 32, alpha_scale=0.35)
    assert full_dot.getpixel((27, 16)) != dim_dot.getpixel((27, 16))
    assert dim_dot.getpixel((27, 16))[0] < full_dot.getpixel((27, 16))[0]
    # Still present (not erased) while faded.
    assert dim_dot.getpixel((27, 16)) != base.getpixel((27, 16))
