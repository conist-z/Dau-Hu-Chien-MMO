"""Kaetram-style combat roll (game/combat.py) — ported from
Kaetram packages/server/src/info/formulas.ts semantics:

- damage is rolled on a distribution skewed toward max by accuracy
- crit (10%) multiplies max damage by 1.5
- a zero roll (or the small miss window) = MISS (damage 0)
- higher weapon tier / lower target level = more accuracy = bigger hits

Deterministic via injected random.Random seeds (rules 9/27: pure + tested).
"""

import random

from game.combat import (
    CRIT_MULTIPLIER,
    DamageRoll,
    roll_damage,
    _skewed_roll,
)


def test_roll_deterministic_with_seed():
    a = roll_damage(14, target_level=1, attacker_tier=1, rng=random.Random(42))
    b = roll_damage(14, target_level=1, attacker_tier=1, rng=random.Random(42))
    assert a == b


def test_roll_shape():
    r = roll_damage(14, target_level=1, attacker_tier=1, rng=random.Random(7))
    assert isinstance(r, DamageRoll)
    assert 0 <= r.damage <= (14 + 2) * 1.25 + 5  # tier 1 bonus + player boost
    assert r.missed == (r.damage <= 0)


def test_crit_multiplies_damage():
    # Force crit with a fixed rng: find a seed where crit fires and verify the
    # max-damage ceiling is 1.5x the non-crit ceiling.
    crit_seen = False
    for seed in range(200):
        r = roll_damage(14, target_level=1, attacker_tier=1, rng=random.Random(seed))
        if r.critical:
            crit_seen = True
            assert r.damage <= ((14 + 2) * 1.25 + 5) * CRIT_MULTIPLIER
    assert crit_seen, "no crit in 200 rolls — CRIT_CHANCE broken"


def test_higher_tier_hits_harder_on_average():
    rng = random.Random(123)
    low = sum(roll_damage(14, 1, 0, rng).damage for _ in range(300))
    high = sum(roll_damage(14, 1, 3, rng).damage for _ in range(300))
    assert high > low, "tier must improve average damage (accuracy skew)"


def test_miss_possible():
    seen = False
    for seed in range(500):
        r = roll_damage(14, 1, 3, random.Random(seed))
        if r.damage == 0:
            seen = True
            break
    assert seen, "MISS window never triggers"


def test_skewed_roll_respects_bounds():
    rng = random.Random(9)
    for _ in range(500):
        v = _skewed_roll(37.5, 0.45, rng)
        assert 0 <= v <= 38
    assert _skewed_roll(0, 0.45, rng) == 0
