"""Kaetram-style combat damage — ported from packages/server/src/info/formulas.ts.

The original: maxDamage = (bonus + level) * 1.25 (+5 for players, +50% crit,
style multipliers), then the FINAL damage is rolled on a weighted random
distribution skewed toward the max by the attacker's ACCURACY relative to the
target's defense (accuracy 0.45 = most accurate; higher skews low). A roll of
0 renders as "MISS" client-side.

We keep the shape (max -> skewed roll -> crit) but scale it to OUR numbers:
zombies have 40 HP and sword tiers do 14/22/32 base damage, so accuracy is
derived from the weapon tier vs zombie level instead of Kaetram's skill
levels. Pure functions only — no state, no Discord, no IO (rules 2/3).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

# --- Kaetram constants (formulas.ts / modules.ts) ---------------------------
CRIT_CHANCE = 0.10          # 10% of hits crit (Kaetram: crit rolls exist in stats)
CRIT_MULTIPLIER = 1.5       # "A critical hit boosts the damage multiplier by 1.5x"
PLAYER_DAMAGE_BOOST = 5     # "Player characters get a boost of 5 damage"
MAX_ACCURACY = 0.45         # Modules.Constants.MAX_ACCURACY — the most accurate
ACCURACY_FLOOR = 0.25       # our clamp: never fully inaccurate (Kaetram clamps too)
MISS_CHANCE_FLOOR = 0.04    # even max accuracy keeps a sliver of MISS (Kaetram: value<1 -> MISS)


@dataclass(frozen=True)
class DamageRoll:
    """One resolved attack: the numbers the renderer needs."""

    damage: int            # final damage (0 = MISS)
    critical: bool         # crit flag (client renders a gold splat)
    missed: bool           # True when damage <= 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "missed", self.damage <= 0)


def _skewed_roll(max_damage: float, accuracy: float, rng: random.Random) -> int:
    """Weighted random damage in [0, max_damage] skewed toward the max.

    Kaetram uses `randomInt(0, max, accuracy)` — an exponential-ish weight
    where LOWER accuracy flattens the distribution toward small values. We
    reproduce it: for u ~ U(0,1), damage = max * u ** weight, where
    weight grows as accuracy worsens (weight 1 = uniform, >1 skews low,
    <1 skews toward max).
    """
    max_damage = max(0.0, max_damage)
    if max_damage <= 0:
        return 0
    a = max(ACCURACY_FLOOR, min(1.0, accuracy))
    # Map accuracy 0.45 (best) .. 1.0 (worst) onto weight 0.55 .. 2.2.
    weight = 0.55 + (a - MAX_ACCURACY) * 3.0
    u = rng.random()
    return int(round(max_damage * (u ** max(0.05, weight))))


def roll_damage(
    weapon_damage: int,
    target_level: int = 1,
    attacker_tier: int = 0,
    rng: Optional[random.Random] = None,
) -> DamageRoll:
    """Resolve one melee hit, Kaetram-style.

    ``weapon_damage`` is the held weapon's max damage (tools.SWORD_TIER_DAMAGE
    or the bare-hand value). ``target_level`` scales the target's defense
    (accuracy worsens with it). ``attacker_tier`` is the weapon tier rank
    (0 = bare hand) which improves accuracy the same way Kaetram's accuracy
    bonus does.
    """
    rng = rng or random

    # --- max damage (getMaxDamage): (bonus + level) * 1.25 + player boost ---
    bonus = max(0, int(attacker_tier)) * 2  # tier -> flat bonus (Kaetram: equipment bonus)
    max_damage = (bonus + weapon_damage) * 1.25 + PLAYER_DAMAGE_BOOST

    # --- accuracy (getDamage): accuracy vs the target's defense level -------
    accuracy = MAX_ACCURACY - attacker_tier * 0.05 + target_level * 0.0175

    # --- crit roll -----------------------------------------------------------
    critical = rng.random() < CRIT_CHANCE
    if critical:
        max_damage *= CRIT_MULTIPLIER

    damage = _skewed_roll(max_damage, accuracy, rng)
    # Miss window: a 0 roll (or the explicit floor chance) whiffs entirely.
    if damage < 1 or rng.random() < MISS_CHANCE_FLOOR:
        damage = 0
    return DamageRoll(damage=damage, critical=critical, missed=damage <= 0)
