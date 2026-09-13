"""Smoke demo for the battle/ package (mirrors docs/turn_battle.md example)."""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from battle.actions import UseSkill
from battle.engine import BattleEngine
from battle.state import BattleState, Unit

state = BattleState("b1")
state.add_unit(Unit("u1", "Hero", "player", level=5, hp=100, max_hp=100,
                    mana=50, max_mana=50, atk=20, dfn=15, spd=12,
                    elements=["fire"], skill_uses={"slash": 10, "firebolt": 5}))
state.add_unit(Unit("u2", "Slime", "enemy", level=5, hp=80, max_hp=80,
                    atk=12, dfn=10, spd=8, elements=["nature"]))

engine = BattleEngine(state, rng=random.Random(42))
engine.submit("player", UseSkill("u1", "firebolt"))
engine.submit("enemy", UseSkill("u2", "strike"))
for ev in engine.run_round():
    print(ev.kind, "|", ev.text)
print("phase:", state.phase, "winner:", state.winner, "enemy hp:", state.units["u2"].hp)
