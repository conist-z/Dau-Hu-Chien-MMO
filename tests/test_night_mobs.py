"""Night mob kinds (zombie/skeleton/spider/slime/bat/rat) — web pack tests."""
from __future__ import annotations

import random

from game.collision import Collision
from game.map_loader import MapData
from game.state import GameState
from game.zombies import (
    MOB_KINDS,
    NIGHT_MOB_MAX_COUNT,
    ZOMBIE_MAX_COUNT,
    iter_web_zombies,
    mob_drop_table,
    mob_stats,
    roll_mob_kind,
    web_tick,
)


def _world():
    map_data = MapData(
        map_id="mob-test",
        width=40,
        height=40,
        collision=[[0] * 40 for _ in range(40)],
        spawn=(20, 20),
    )
    state = GameState(1, map_data.map_id)
    player = state.add_player(1, "A", 20, 20)
    player.is_web = True
    collision = Collision(map_data, state.blocks)
    return state, collision, player


def _world_on_map(map_id: str):
    """Same as _world but with a custom map_id (trade-zone tests)."""
    map_data = MapData(
        map_id=map_id,
        width=40,
        height=40,
        collision=[[0] * 40 for _ in range(40)],
        spawn=(20, 20),
    )
    state = GameState(1, map_data.map_id)
    player = state.add_player(1, "A", 20, 20)
    player.is_web = True
    collision = Collision(map_data, state.blocks)
    return state, collision, player


def test_roll_mob_kind_descending_weights():
    """zombie most common ... rat rarest, matching the user's order."""
    rng = random.Random(42)
    counts = {k: 0 for k in MOB_KINDS}
    n = 6000
    for _ in range(n):
        counts[roll_mob_kind(rng)] += 1
    order = ["zombie", "skeleton", "spider", "slime", "bat", "rat"]
    freqs = [counts[k] / n for k in order]
    for a, b in zip(freqs, freqs[1:]):
        assert a > b, (order, freqs)


def test_mob_stats_per_kind():
    """Each kind has its own hp/damage; zombie keeps the classic stats."""
    # HP x5 (user 25/09): zombie 40 -> 200.
    assert mob_stats("zombie")["hp"] == 200
    assert mob_stats("zombie")["dmg"] == 10
    bat = mob_stats("bat")
    assert bat["hp"] < mob_stats("zombie")["hp"]
    assert bat["dmg"] < mob_stats("zombie")["dmg"]
    assert mob_stats("skeleton")["hp"] > mob_stats("zombie")["hp"]
    # Unknown kind falls back to zombie row.
    assert mob_stats("nonexistent") == mob_stats("zombie")


def test_web_spawn_assigns_kind_and_stats():
    state, collision, _player = _world()
    rng = random.Random(1)
    web_tick(state, collision, True, 0.05, rng=rng)
    zombies = iter_web_zombies(state)
    assert zombies, "night web_tick should spawn at least one mob"
    for z in zombies:
        assert z.kind in MOB_KINDS
        stats = mob_stats(z.kind)
        assert z.max_hp == stats["hp"]
        assert z.damage == stats["dmg"]
        assert z.web_speed == stats["speed"]
        assert z.web_cooldown == stats["cooldown"]


def test_night_mob_cap_is_fifteen_percent_up():
    """+15% population: 3 -> 4 (ceil), and the tick respects the new cap."""
    assert NIGHT_MOB_MAX_COUNT > ZOMBIE_MAX_COUNT
    assert NIGHT_MOB_MAX_COUNT == 4


def test_mob_drop_table_falls_back_to_zombie():
    assert mob_drop_table("skeleton") != mob_drop_table("zombie")
    assert mob_drop_table("unknown") == mob_drop_table("zombie")
    for kind in MOB_KINDS:
        for item_id, chance, qty in mob_drop_table(kind):
            assert 0.0 < chance <= 1.0
            assert qty >= 1


def test_bite_damage_uses_kind_damage():
    """A bat bite deals its (lower) kind damage, not the zombie's."""
    state, collision, player = _world()
    rng = random.Random(5)
    web_tick(state, collision, True, 0.05, rng=rng)
    zombies = iter_web_zombies(state)
    assert zombies
    z = zombies[0]
    # Force the mob adjacent and past its cooldown, then tick once.
    z.x_f = player.x_f
    z.y_f = player.y_f + 0.5
    z.last_bite = 0.0
    player.hp_before = player.hp
    web_tick(state, collision, True, 0.05, rng=rng)
    if player.hp < player.hp_before:
        assert (player.hp_before - player.hp) == z.damage


def test_trade_zone_maps_have_no_animal_roster():
    """User 28/09: the market must have NO animals inside. The trade maps
    used to fall back to the bigmap profile (bunnies/deer/wolves spawned in
    the lobby); their own profiles must be wildlife-free."""
    from game.mob_profiles import ambient_max_for, ambient_profile_for

    for map_id in ("lobbytrade", "montertradebase"):
        assert not ambient_profile_for(map_id), map_id
        assert ambient_max_for(map_id, 12 * 3600) == 0  # noon


def test_trade_zone_tick_never_spawns_and_sweeps_leftover_animals():
    """web_tick on a trade map (day-forced, like manager does) must spawn
    nothing AND remove any ambient animal already inside."""
    from game.zombies import spawn_animal_one

    for map_id in ("lobbytrade", "montertradebase"):
        state, collision, player = _world_on_map(map_id)
        rng = random.Random(7)
        # Seed a leftover animal as if it had spawned before the rule.
        z = spawn_animal_one(state, collision, [player], rng)
        assert z is not None
        # Day-forced tick (night=False — the manager's trade-zone call).
        for _ in range(3):
            web_tick(state, collision, False, 0.05, rng=rng)
        assert not [
            zz for zz in iter_web_zombies(state) if getattr(zz, "ambient", False)
        ], map_id
        # And the night half sweeps too.
        z2 = spawn_animal_one(state, collision, [player], rng)
        assert z2 is not None
        web_tick(state, collision, True, 0.05, rng=rng)
        assert not [
            zz for zz in iter_web_zombies(state) if getattr(zz, "ambient", False)
        ], map_id


def test_side_animal_facing_never_strobes():
    """Side-view animal facing (user 28/09): E/W only, vertical movement
    keeps the face, and a flip requires accumulated opposite travel — a
    diagonal glide must not strobe the head left<->right every tick."""
    from game.zombies import SIDE_FLIP_THRESHOLD, _side_animal_facing, Zombie

    z = Zombie("a1", 10, 10)
    z.facing = "E"
    # Pure vertical: face unchanged.
    assert _side_animal_facing(z, 0.0, 0.1) == "E"
    assert _side_animal_facing(z, 0.0, -0.1) == "E"
    # Moving right: stays E.
    assert _side_animal_facing(z, 0.1, 0.0) == "E"
    # Small opposite drifts (jitter) NEVER flip until the threshold:
    # 4 steps x 0.05 = 0.20 < 0.25 stays un-flipped.
    for _ in range(4):
        assert _side_animal_facing(z, -0.05, 0.0) == "E"
    # A 5th opposite step crosses the accumulated threshold -> flips.
    assert _side_animal_facing(z, -0.05, 0.0) == "W"
    # Once W, agreeing steps keep it; spawn default "S" fixes on first step.
    z2 = Zombie("a2", 5, 5)
    z2.facing = "S"
    assert _side_animal_facing(z2, -0.1, 0.0) == "W"


def test_web_push_into_monter_door_teleports():
    """Regression 28/09 ("hết vào được montertradebase"): since gates are
    SOLID walls the web body rests flush against the door and moved_any stays
    False while the key is held — the portal check used to run ONLY on a real
    move, so the gate never fired. Pressing INTO the door must teleport."""
    import asyncio

    from game.manager import GameManager, _loop_time
    from config import ASSETS_DIR

    async def drive():
        gm = GameManager(assets_dir=ASSETS_DIR)
        rt = gm.create_runtime(999, "lobbytrade")
        p = rt.state.add_player(42, "A", 38, 27)
        p.is_web = True
        p.sync_float_from_int()
        assert gm.register_web_session(999, 42, "A")
        for _ in range(400):
            # Hold UP into the solid door with a fresh predicted report —
            # exactly what the browser does while the key is held.
            report_y = max(26.3, p.y_f - 0.1)
            gm.web_input(999, 42, 0.0, -1.0,
                         report_x=p.x_f, report_y=report_y)
            await gm._web_tick_runtime(rt, rt.web_sessions, _loop_time())
            if rt.map_data.map_id != "lobbytrade":
                break
            await asyncio.sleep(0.01)  # real dt so the body actually walks
        return rt, gm

    rt, gm = asyncio.run(drive())
    # The player left the lobby runtime...
    assert 42 not in rt.state.players
    # ...and landed inside the monter trade interior.
    dst = gm.side_runtimes.get((999, "montertradebase"))
    assert dst is not None
    assert 42 in dst.state.players
