import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from battle.actions import Flee, UseSkill
from battle.engine import BattleEngine
from battle.rules import build_default_chart
from battle.skills import (BattleConfig, SkillDef, can_use, get_skill,
                           resolve_skill)
from battle.state import BattleState, Unit


def make_duel():
    state = BattleState("b1")
    state.add_unit(Unit("u1", "Hero", "player", level=5, hp=100, max_hp=100,
                        mana=50, max_mana=50, atk=20, dfn=15, spd=12,
                        crit=0.0, elements=["fire"],
                        skill_uses={"slash": 10, "firebolt": 5, "mend": 3}))
    state.add_unit(Unit("u2", "Slime", "enemy", level=5, hp=80, max_hp=80,
                        mana=0, max_mana=0, atk=12, dfn=10, spd=8,
                        crit=0.0, elements=["nature"]))
    return state


def test_unit_alive_and_side_helpers():
    state = make_duel()
    assert state.units["u1"].alive
    assert [u.unit_id for u in state.units_of_side("player")] == ["u1"]
    assert state.side_alive("enemy")
    assert state.sides() == ["enemy", "player"]


def test_damage_is_deterministic_with_seeded_rng():
    state = make_duel()
    e1 = BattleEngine(state, rng=random.Random(42))
    d1 = resolve_skill(state.units["u1"], state.units["u2"],
                       get_skill("slash"), e1.chart, e1.config, random.Random(42))
    d2 = resolve_skill(state.units["u1"], state.units["u2"],
                       get_skill("slash"), e1.chart, e1.config, random.Random(42))
    assert d1.damage == d2.damage and d1.damage > 0
    # resolve_skill applies damage to the defender (engine relies on this);
    # both seeded rolls produced identical damage, applied twice.
    assert state.units["u2"].hp == 80 - d1.damage - d2.damage


def test_element_effectiveness_and_immunity():
    chart = build_default_chart()
    assert chart.effectiveness("fire", ["nature"]) == 2.0
    assert chart.effectiveness("fire", ["water"]) == 0.5
    assert chart.effectiveness("fire", ["nature", "water"]) == 1.0
    assert chart.effectiveness("unknown", ["nature"]) == 1.0

    state = make_duel()
    rng = random.Random(1)
    res = resolve_skill(state.units["u1"], state.units["u2"],
                        get_skill("firebolt"), chart, BattleConfig(), rng)
    # firebolt has crit_bonus 0, attacker crit 0 -> never crits.
    assert not res.crit
    assert res.effectiveness == 2.0


def test_speed_and_priority_decide_turn_order():
    state = make_duel()
    engine = BattleEngine(state, rng=random.Random(7))
    engine.submit("player", UseSkill("u1", "slash"))
    engine.submit("enemy", UseSkill("u2", "strike"))
    events = engine.run_round()
    assert "Hero used" in events[0].text  # faster unit acts first


def test_priority_overrides_speed():
    state = make_duel()
    state.units["u2"].spd = 99
    from battle.skills import DEFAULT_SKILL_REGISTRY
    DEFAULT_SKILL_REGISTRY["quickjab"] = SkillDef("quickjab", "Quick Jab",
                                                  power=10, priority=1)
    try:
        engine = BattleEngine(state, rng=random.Random(7))
        engine.submit("enemy", UseSkill("u2", "strike"))
        engine.submit("player", UseSkill("u1", "quickjab"))
        events = engine.run_round()
    finally:
        del DEFAULT_SKILL_REGISTRY["quickjab"]
    assert "Quick Jab" in events[0].text


def test_skill_uses_and_fallback_strike():
    state = make_duel()
    u1 = state.units["u1"]
    u1.skill_uses["slash"] = 1
    engine = BattleEngine(state, rng=random.Random(7))
    engine.submit("player", UseSkill("u1", "slash"))
    engine.submit("enemy", UseSkill("u2", "strike"))
    engine.run_round()
    assert u1.skill_uses["slash"] == 0
    assert not can_use(u1, get_skill("slash"))
    # With no usable skill left, submitting is rejected...
    assert engine.submit("player", UseSkill("u1", "slash")) == "skill_unusable"
    # ...but the round still auto-uses the fallback "strike".
    engine.submit("player", UseSkill("u1", "strike"))
    engine.submit("enemy", UseSkill("u2", "strike"))
    events = engine.run_round()
    assert any(ev.kind == "damage" for ev in events)


def test_mana_cost_blocks_skill():
    state = make_duel()
    state.units["u1"].mana = 0
    engine = BattleEngine(state, rng=random.Random(7))
    assert engine.submit("player", UseSkill("u1", "firebolt")) == "skill_unusable"


def test_support_skill_heals():
    state = make_duel()
    state.units["u1"].hp = 40
    engine = BattleEngine(state, rng=random.Random(7))
    engine.submit("player", UseSkill("u1", "mend"))
    engine.submit("enemy", UseSkill("u2", "strike"))
    events = engine.run_round()
    assert any(ev.kind == "heal" for ev in events)
    heal_event = next(ev for ev in events if ev.kind == "heal")
    assert heal_event.data["amount"] == 25
    assert state.units["u1"].mana == 42  # 50 - 8 mana_cost
    assert state.units["u1"].hp > 40     # healed, then hit by Slime's strike


def test_ko_ends_battle_with_winner():
    state = make_duel()
    state.units["u2"].hp = 1
    engine = BattleEngine(state, rng=random.Random(7))
    engine.submit("player", UseSkill("u1", "slash"))
    engine.submit("enemy", UseSkill("u2", "strike"))
    events = engine.run_round()
    assert engine.state.phase == "ended"
    assert engine.state.winner == "player"
    assert any(ev.kind == "ko" for ev in events)
    assert engine.submit("player", UseSkill("u1", "slash")) == "battle_ended"
    assert engine.run_round()[0].kind == "invalid"


def test_flee_ends_battle_without_winner():
    state = make_duel()
    engine = BattleEngine(state, rng=random.Random(7))
    engine.submit("player", Flee("u1"))
    engine.submit("enemy", UseSkill("u2", "strike"))
    events = engine.run_round()
    assert engine.state.phase == "ended"
    assert engine.state.winner is None
    assert events[0].kind == "flee"


def test_miss_with_zero_accuracy():
    state = make_duel()
    from battle.skills import DEFAULT_SKILL_REGISTRY
    DEFAULT_SKILL_REGISTRY["wildshot"] = SkillDef("wildshot", "Wild Shot",
                                                  power=50, accuracy=0.0)
    try:
        engine = BattleEngine(state, rng=random.Random(7))
        engine.submit("player", UseSkill("u1", "wildshot"))
        engine.submit("enemy", UseSkill("u2", "strike"))
        events = engine.run_round()
    finally:
        del DEFAULT_SKILL_REGISTRY["wildshot"]
    assert events[0].kind == "miss"
    assert state.units["u2"].hp == 80

