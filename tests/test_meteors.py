"""Meteor scheduler rules (game/meteors.py) — the user-specified odds:

- First meteor of a night: 20% per beat.
- Each meteor that already fell tonight HALVES the next chance
  (20 -> 10 -> 5 -> 2.5 ...), capped at MAX_METEORS_PER_NIGHT.
- A new night resets the counter back to 20%.
"""

import random

from game import meteors as M


def _state():
    return M.MeteorState()


def test_chance_halving_sequence():
    assert abs(M.meteor_chance(0) - 0.20) < 1e-9
    assert abs(M.meteor_chance(1) - 0.10) < 1e-9
    assert abs(M.meteor_chance(2) - 0.05) < 1e-9
    assert abs(M.meteor_chance(3) - 0.025) < 1e-9


def test_spawn_only_at_night():
    st = _state()
    landed = M.tick_meteors(st, now=100.0, second_of_day=12 * 3600,
                            is_night_now=False, pick_target=lambda: (5, 5),
                            rng=random.Random(1))
    assert landed == [] and st.active == []


def test_night_spawn_counts_and_halves():
    st = _state()
    rng = random.Random(7)
    spawned = 0
    # Force many beats: with enough 20% rolls at least one fires; after each
    # spawn the counter increments (halving) — verify directly via state.
    for i in range(400):
        M.tick_meteors(st, now=100.0 + i * M.SCHEDULER_BEAT_SECONDS + 1,
                        second_of_day=23 * 3600, is_night_now=True,
                        pick_target=lambda: (5, 5), rng=rng)
        spawned = st.felled_tonight
        if spawned >= 2:
            break
    assert spawned >= 2  # halving proven by meteor_chance tests


def test_new_night_resets_counter():
    st = _state()
    st.felled_tonight = 4
    st.last_night = M._night_id(23 * 3600)
    # tick with a DIFFERENT night id (morning): counter resets
    new_nid = M._night_id(7 * 3600)
    assert new_nid != st.last_night
    M.tick_meteors(st, now=10.0, second_of_day=7 * 3600, is_night_now=False,
                   pick_target=lambda: (1, 1), rng=random.Random(1))
    assert st.felled_tonight == 0


def test_meteor_lands_after_warning_and_expires():
    st = _state()
    now = 100.0
    m = M.summon(st, now, tx=3, ty=4, dir="left")
    assert m.impact_at == now + M.WARNING_SECONDS
    # A deterministic rng that NEVER rolls a spawn so the scheduler beat
    # cannot add extra meteors mid-test (only the summoned one exists).
    class _NoSpawn(random.Random):
        def random(self):
            return 1.0  # >= any chance: never spawns
    rng = _NoSpawn(1)
    # not yet
    landed = M.tick_meteors(st, now + 1, 23 * 3600, True,
                            lambda: (0, 0), rng)
    assert landed == []
    # after warning window: lands exactly once
    landed = M.tick_meteors(st, now + M.WARNING_SECONDS + 0.5, 23 * 3600, True,
                            lambda: (0, 0), rng)
    assert [x.id for x in landed] == [m.id]
    landed2 = M.tick_meteors(st, now + M.WARNING_SECONDS + 1.0, 23 * 3600, True,
                             lambda: (0, 0), rng)
    assert landed2 == []


def test_summon_near_random_offsets():
    st = _state()
    rng = random.Random(42)
    m = M.summon(st, 0.0, tx=10, ty=10, near_random=True, rng=rng)
    dist = ((m.tx - 10) ** 2 + (m.ty - 10) ** 2) ** 0.5
    assert 6.0 <= dist <= 12.0 + 1.5  # floor() slack


def test_shake_direction_and_falloff():
    st = _state()
    m = M.summon(st, 0.0, tx=10, ty=10, dir="left")
    # player 3 tiles east: shake points +x, magnitude < max
    sx, sy, mag = M.shake_for_player(m, px=13, py=10)
    assert sx > 0.99 and abs(sy) < 0.01
    assert 0 < mag < M.IMPACT_MAX_SHAKE
    # player far away: no shake
    _, _, mag_far = M.shake_for_player(m, px=10 + M.IMPACT_SHAKE_RADIUS + 5, py=10)
    assert mag_far == 0.0
