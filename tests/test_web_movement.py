"""Giai đoạn 0: continuous (float) positions + swept AABB collision.

Covers: float/int sync invariants, wall slide, blocked axes, the Discord
render-tile floor rule (a web player always shows one specific tile), speed
caps in the tick integration, and zombie lock (web players never targeted).
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.collision import Collision, FLOAT_BOX_HALF
from game.map_loader import load_map
from game.state import Player, _tile_of

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _map():
    return load_map("test-map", ASSETS)


# ----- float/int sync invariants -----

def test_tile_of_is_floor():
    assert _tile_of(5.0) == 5
    assert _tile_of(5.9) == 5
    assert _tile_of(-0.1) == -1  # floor, not truncation
    assert _tile_of(-3.7) == -4


def test_sync_float_from_int_centres_on_tile():
    p = Player(user_id=1, display_name="a", x=3, y=4)
    p.sync_float_from_int()
    assert p.x_f == 3.5 and p.y_f == 4.5
    # Round-trip: floor back gives the same tile.
    p.sync_int_from_float()
    assert p.x == 3 and p.y == 4


def test_sync_int_from_float_floors():
    p = Player(user_id=1, display_name="a")
    p.x_f, p.y_f = 7.9, 2.1
    p.sync_int_from_float()
    assert (p.x, p.y) == (7, 2)


def test_discord_default_player_has_no_float_drift():
    # A Discord-only player never touches floats: ints stay authoritative.
    p = Player(user_id=1, display_name="a", x=2, y=3)
    assert p.float_moved is False and p.is_web is False
    assert (p.x, p.y) == (2, 3)


# ----- swept AABB collision -----

def test_free_move_inside_open_tile():
    md = _map()
    c = Collision(md)
    # Spawn (5,5) area is open: small steps stay legal from the tile centre.
    nx, ny = c.can_move_float(5.5, 5.5, 0.1, 0.0)
    assert abs(nx - 5.6) < 1e-9 and ny == 5.5


def test_wall_stops_x_axis_but_y_slides():
    md = _map()
    c = Collision(md)
    # Column 0 is solid; put the box edge EXACTLY on the wall (center 1.3 ->
    # left edge 1.0) and push left+down: x must stay clamped AT the wall,
    # while the y component still applies (wall slide). The old sweep had an
    # off-by-one at exact tile boundaries (floor(edge)+1 skipped the boundary
    # column) and crept INTO the wall on the next inward step — the web
    # "đi xuyên khối" bug, reproduced by brute force (30k+ tunnels).
    nx, ny = c.can_move_float(1.3, 1.5, -0.5, 0.2)
    assert abs(nx - 1.3) < 1e-9  # clamped exactly at the wall, never inside
    assert abs(ny - 1.7) < 1e-9


def test_diagonal_into_wall_corner_keeps_free_axis():
    md = _map()
    c = Collision(md)
    # Walk east from inside tile (1,1) toward solid (0,1)/(0,0) column:
    # x blocked, y free.
    start_x = 1.5
    nx, ny = c.can_move_float(start_x, 1.5, -0.5, 0.25)
    assert nx > 1.3 - 1e-9  # stopped by the solid column at x<1.3
    assert ny == 1.75


def test_blocked_move_clamps_and_never_enters_solid():
    md = _map()
    c = Collision(md)

    def overlaps_solid(x_f, y_f):
        r = FLOAT_BOX_HALF
        for tx in range(int(x_f - r), int(x_f + r) + 1):
            for ty in range(int(y_f - r), int(y_f + r) + 1):
                if x_f - r < tx + 1 and x_f + r > tx and y_f - r < ty + 1 and y_f + r > ty:
                    if not c.is_walkable(tx, ty):
                        return True
        return False

    # Start legally inside tile (1,1); shove hard toward the solid column.
    nx, ny = c.can_move_float(1.5, 1.5, -2.0, -2.0)
    assert (nx, ny) != (1.5, 1.5)          # something moved (clamped at wall)
    assert not overlaps_solid(nx, ny)      # the box NEVER enters a solid tile
    assert nx >= 1.3 - 1e-9                # clamped exactly at the wall edge


def test_box_half_smaller_than_tile():
    # Design rule: the box must fit through 1-tile gaps.
    assert 0 < FLOAT_BOX_HALF < 0.5


# ----- tick integration speed cap (server-authoritative speed) -----

def test_web_tick_moves_player_at_capped_speed():
    from game.manager import GameManager

    gm = GameManager(ASSETS)
    rt = gm.create_runtime(1, "test-map")
    assert gm.register_web_session(1, 42, "tester")
    assert gm.web_input(1, 42, 1.0, 0.0)
    p = rt.state.get_player(42)
    start = (p.x_f, p.y_f)
    # Simulate 20 ticks of 1/20 s each (= 1 s of held-right at walk speed).
    now = iter([i * 0.05 for i in range(1, 21)])
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        for _ in range(20):
            loop.run_until_complete(
                gm._web_tick_runtime(rt, rt.web_sessions, next(now))
            )
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    moved = p.x_f - start[0]
    assert 3.0 <= moved <= 4.0 + 1e-6  # walk speed 4 tiles/s, wall clamp may reduce
    assert p.float_moved is True
    assert p.is_web is True


def test_web_tick_respects_zero_vector():
    from game.manager import GameManager

    gm = GameManager(ASSETS)
    rt = gm.create_runtime(1, "test-map")
    gm.register_web_session(1, 7, "idle")
    gm.web_input(1, 7, 0.0, 0.0)
    p = rt.state.get_player(7)
    before = (p.x_f, p.y_f)
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            gm._web_tick_runtime(rt, rt.web_sessions, 1000.0)
        )
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    assert (p.x_f, p.y_f) == before


def test_input_vector_is_clamped():
    from game.manager import GameManager

    gm = GameManager(ASSETS)
    rt = gm.create_runtime(1, "test-map")
    gm.register_web_session(1, 9, "s")
    assert gm.web_input(1, 9, 5.0, -5.0, running=True)
    sess = rt.web_sessions[9]
    assert sess.dx == 1.0 and sess.dy == -1.0 and sess.running is True


def test_register_without_scenario_fails():
    from game.manager import GameManager

    gm = GameManager(ASSETS)
    assert gm.register_web_session(999, 1, "x") is False


def test_drop_web_session_freezes_tile():
    from game.manager import GameManager

    gm = GameManager(ASSETS)
    rt = gm.create_runtime(1, "test-map")
    gm.register_web_session(1, 11, "d")
    # drop_web_session is sync (safe under a running loop in production; the
    # debounced save it schedules needs a loop, so defer it in the test).
    gm.db = None
    gm._schedule_save = lambda rt, player: None
    gm.drop_web_session(1, 11)
    p = rt.state.get_player(11)
    assert p.is_web is False
    assert (p.x, p.y) == (_tile_of(p.x_f), _tile_of(p.y_f))
    assert 11 not in rt.web_sessions


# ----- zombie lock (web players are invisible to the zombie AI) -----

def test_zombies_never_target_web_players():
    from game.zombies import _players

    p_web = Player(user_id=1, display_name="web", x=5, y=5)
    p_web.is_web = True
    p_discord = Player(user_id=2, display_name="chat", x=6, y=5)

    class _State:
        zombies = {}
        players = {1: p_web, 2: p_discord}

        def get_visible_players(self):
            return [p_web, p_discord]

    targets = _players(_State())
    assert p_web not in targets and p_discord in targets


def test_zombie_bite_skips_web_player():
    from game.zombies import advance_visible_zombies
    from game.zombies import Zombie

    z = Zombie("z1", 6, 5)
    web_p = Player(user_id=1, display_name="web", x=5, y=5)
    web_p.is_web = True
    web_p.sync_float_from_int()

    class _State:
        zombies = {"z1": z}
        players = {1: web_p}

        def get_visible_players(self):
            return [web_p]

    result = advance_visible_zombies(_State(), Collision(_map()), {1: (0, 0, 10, 10)})
    # The adjacent zombie must not bite (or even move toward) the web player.
    assert 1 not in result.damaged_player_ids
    assert 1 not in result.died_player_ids
    assert z.x == 6 and z.y == 5  # never stepped toward the web player
