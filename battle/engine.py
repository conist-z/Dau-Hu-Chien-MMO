from __future__ import annotations

import random
from typing import List, Optional

from battle.actions import Flee, UseSkill
from battle.rules import ElementChart, build_default_chart
from battle.skills import (BattleConfig, SkillDef, can_use, get_skill,
                           resolve_skill, spend_usage)
from battle.state import BattleEvent, BattleState, Unit


class BattleEngine:
    """Resolves rounds deterministically. Pure logic — no discord, no IO.

    Usage:
        state = BattleState("b1"); ... add_unit ...
        engine = BattleEngine(state)
        engine.submit("player", UseSkill("u1", "slash"))
        engine.submit("enemy", UseSkill("u2", "strike"))
        events = engine.run_round()
    """

    def __init__(self, state: BattleState, chart: ElementChart = None,
                 config: BattleConfig = None, rng: random.Random = None):
        self.state = state
        self.chart = chart or build_default_chart()
        self.config = config or BattleConfig()
        self.rng = rng or random.Random()

    # -- input ------------------------------------------------------------

    def submit(self, side: str, action) -> Optional[str]:
        """Queue a side's action for the next round. Returns error or None."""
        if self.state.phase != "ongoing":
            return "battle_ended"
        unit = self.state.units.get(getattr(action, "unit_id", ""))
        if unit is None or unit.side != side:
            return "wrong_side"
        if isinstance(action, UseSkill):
            skill = get_skill(action.skill_id)
            if skill is None:
                return "unknown_skill"
            if not can_use(unit, skill):
                return "skill_unusable"
        self.state.pending[side] = action
        return None

    # -- resolution -------------------------------------------------------

    def run_round(self) -> List[BattleEvent]:
        if self.state.phase != "ongoing":
            return [BattleEvent("invalid", "Battle already ended.")]
        self.state.round_number += 1
        events: List[BattleEvent] = []

        # Flee is resolved first, before anyone acts.
        for side, action in sorted(self.state.pending.items()):
            if isinstance(action, Flee):
                events.append(BattleEvent("flee", f"{action.unit_id} fled!",
                                          {"unit_id": action.unit_id}))
                self.state.phase = "ended"
                self.state.winner = None
                self.state.pending.clear()
                return events

        for unit in self._turn_order():
            if self.state.phase == "ended" or not unit.alive:
                continue
            action = self.state.pending.get(unit.side)
            if isinstance(action, UseSkill) and action.unit_id == unit.unit_id:
                skill = get_skill(action.skill_id)
            else:
                skill = None
            self._act(unit, skill, events)

        self.state.pending.clear()
        if self.state.phase == "ongoing":
            events.append(BattleEvent("round_end",
                                      f"Round {self.state.round_number} over.",
                                      {"round": self.state.round_number}))
        self._check_end(events)
        return events

    # -- internals --------------------------------------------------------

    def _turn_order(self) -> List[Unit]:
        """Speed order; pending skill priority boosts; ties by unit_id."""
        def key(u: Unit):
            action = self.state.pending.get(u.side)
            prio = 0
            if isinstance(action, UseSkill) and action.unit_id == u.unit_id:
                skill = get_skill(action.skill_id)
                prio = skill.priority if skill else 0
            return (-prio, -u.spd, u.unit_id)
        return sorted([u for u in self.state.units.values() if u.alive], key=key)

    def _act(self, unit: Unit, skill: Optional[SkillDef],
             events: List[BattleEvent]) -> None:
        if skill is None or not can_use(unit, skill):
            skill = get_skill(self.config.fallback_skill_id)
        if skill is None:  # misconfigured registry: fail loudly, not silently
            raise ValueError(f"fallback skill '{self.config.fallback_skill_id}' missing")

        target = self._pick_target(unit)
        if target is None:
            return

        unit.mana = max(0, unit.mana - skill.mana_cost)
        spend_usage(unit, skill)
        res = resolve_skill(unit, target, skill, self.chart, self.config, self.rng)

        if not res.hit:
            events.append(BattleEvent("miss", f"{unit.name}'s {skill.name} missed!",
                                      {"unit_id": unit.unit_id, "skill_id": skill.id}))
            return
        if res.healed:
            events.append(BattleEvent("heal",
                                      f"{unit.name} healed {res.healed} HP ({skill.name}).",
                                      {"unit_id": unit.unit_id, "amount": res.healed}))
            return
        if res.damage <= 0:
            events.append(BattleEvent("damage",
                                      f"{skill.name} has no effect on {target.name}!",
                                      {"unit_id": unit.unit_id, "damage": 0}))
        else:
            note = " Critical!" if res.crit else ""
            events.append(BattleEvent(
                "damage",
                f"{unit.name} used {skill.name}: {res.damage} damage to {target.name}.{note}",
                {"unit_id": unit.unit_id, "target_id": target.unit_id,
                 "damage": res.damage, "crit": res.crit,
                 "effectiveness": res.effectiveness}))
        if not target.alive:
            events.append(BattleEvent("ko", f"{target.name} is down!",
                                      {"unit_id": target.unit_id}))
        self._check_end(events)

    def _pick_target(self, attacker: Unit) -> Optional[Unit]:
        enemies = [u for u in self.state.units.values()
                   if u.side != attacker.side and u.alive]
        return enemies[0] if enemies else None

    def _check_end(self, events: List[BattleEvent]) -> None:
        if self.state.phase == "ended":
            return
        for side in self.state.sides():
            if not self.state.side_alive(side):
                others = [s for s in self.state.sides() if s != side]
                self.state.phase = "ended"
                self.state.winner = others[0] if others else None
                events.append(BattleEvent("ended",
                                          f"Side '{side}' defeated. Winner: {self.state.winner}",
                                          {"winner": self.state.winner}))
                return

