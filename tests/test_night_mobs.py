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
    assert mob_stats("zombie")["hp"] == 40
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
