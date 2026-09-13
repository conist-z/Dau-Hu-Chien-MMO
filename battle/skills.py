from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SkillDef:
    """Data-driven skill definition. Add new skills without touching the engine.

    power == 0 means a support skill (no direct damage) — effects are a
    free-form dict so you can invent "heal", "buff_atk", "poison", ... later.
    """

    id: str
    name: str
    element: str = "neutral"
    power: int = 0
    accuracy: float = 1.0  # 0..1
    priority: int = 0
    mana_cost: int = 0
    max_uses: Optional[int] = None  # None = unlimited
    crit_bonus: float = 0.0
    effects: Dict[str, float] = field(default_factory=dict)


@dataclass
class BattleConfig:
    """Tuning knobs. Change numbers here — never inside the engine."""

    crit_mult: float = 1.5
    variance_min: float = 0.85  # damage rolled in [variance_min, 1.0]
    # Bonus multiplier when the attacker's own element matches the skill's
    # (generic replacement of the PKM "STAB" idea; 1.0 = disabled).
    same_element_bonus: float = 1.0
    # Skill used automatically when a unit has no usable skill left.
    fallback_skill_id: str = "strike"
    flee_success: bool = True


def build_default_skills() -> Dict[str, SkillDef]:
    """Minimal starter set. Replace/extend from JSON at load time."""
    return {
        "strike": SkillDef("strike", "Strike", "neutral", power=20),
        "slash": SkillDef("slash", "Slash", "neutral", power=35, mana_cost=5, max_uses=10),
        "firebolt": SkillDef("firebolt", "Firebolt", "fire", power=40, mana_cost=10, max_uses=5),
        "mend": SkillDef("mend", "Mend", "nature", power=0, mana_cost=8, max_uses=3,
                         effects={"heal": 25}),
    }


DEFAULT_SKILL_REGISTRY: Dict[str, SkillDef] = build_default_skills()


def get_skill(skill_id: str) -> Optional[SkillDef]:
    return DEFAULT_SKILL_REGISTRY.get(skill_id)


def skill_usages_left(unit, skill: SkillDef) -> Optional[int]:
    """Remaining uses for `unit`; None = unlimited."""
    if skill.max_uses is None:
        return None
    return unit.skill_uses.get(skill.id, skill.max_uses)


def can_use(unit, skill: SkillDef) -> bool:
    if not unit.alive:
        return False
    if skill.mana_cost > unit.mana:
        return False
    left = skill_usages_left(unit, skill)
    return left is None or left > 0


def spend_usage(unit, skill: SkillDef) -> None:
    if skill.max_uses is None:
        return
    left = unit.skill_uses.get(skill.id, skill.max_uses)
    unit.skill_uses[skill.id] = max(0, left - 1)


@dataclass
class DamageResult:
    damage: int = 0
    healed: int = 0
    crit: bool = False
    effectiveness: float = 1.0
    hit: bool = True


def resolve_skill(attacker, defender, skill: SkillDef, chart, config: BattleConfig,
                  rng: random.Random) -> DamageResult:
    """Deterministic given (state, skill, chart, config, rng). Pure math only."""
    # Accuracy roll.
    if skill.accuracy < 1.0 and rng.random() > skill.accuracy:
        return DamageResult(hit=False)

    res = DamageResult()

    # Support skill: generic effect dict, no damage.
    if skill.power <= 0:
        heal = int(skill.effects.get("heal", 0))
        if heal > 0:
            before = attacker.hp
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            res.healed = attacker.hp - before
        return res

    eff = chart.effectiveness(skill.element, defender.elements)
    res.effectiveness = eff

    base = (((2 * attacker.level / 5 + 2) * skill.power * attacker.atk / max(1, defender.dfn)) / 50) + 2
    mult = config.same_element_bonus if skill.element in attacker.elements else 1.0

    crit = rng.random() < (attacker.crit + skill.crit_bonus)
    res.crit = crit
    if crit:
        mult *= config.crit_mult

    variance = rng.uniform(config.variance_min, 1.0)
    damage = int(base * mult * variance * eff)
    if eff <= 0:
        damage = 0
    res.damage = max(1, damage) if damage > 0 else 0
    defender.hp = max(0, defender.hp - res.damage)
    return res
