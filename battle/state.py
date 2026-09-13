from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Unit:
    """A battle participant. Pure data: no discord, no IO.

    Stats are plain ints/floats so they can be persisted or rebuilt from any
    data source (Player stats, NPC defs, JSON templates...).
    """

    unit_id: str
    name: str
    side: str  # e.g. "player" | "enemy"
    level: int = 1
    hp: int = 100
    max_hp: int = 100
    mana: int = 50
    max_mana: int = 50
    atk: int = 10
    dfn: int = 10
    spd: int = 10
    crit: float = 0.05  # base crit chance 0..1
    elements: List[str] = field(default_factory=list)
    # skill_id -> remaining uses (None meaning unbounded is encoded by the
    # SkillDef.max_uses, not here; a missing skill_id here means infinite).
    skill_uses: Dict[str, int] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class BattleEvent:
    """One line of battle outcome. Renderer turns these into text later."""

    kind: str  # damage | miss | heal | ko | flee | round_end | invalid
    text: str
    data: dict = field(default_factory=dict)


class BattleState:
    """Pure in-memory battle state. Must NOT import discord.py."""

    def __init__(self, battle_id: str):
        self.battle_id = battle_id
        self.units: Dict[str, Unit] = {}
        self.round_number: int = 0
        self.phase: str = "ongoing"  # ongoing | ended
        self.winner: Optional[str] = None  # side id, or None if fled/draw
        self.pending: Dict[str, object] = {}  # side -> submitted action

    def add_unit(self, unit: Unit) -> Unit:
        if unit.unit_id in self.units:
            return self.units[unit.unit_id]
        self.units[unit.unit_id] = unit
        return unit

    def get_unit(self, unit_id: str) -> Optional[Unit]:
        return self.units.get(unit_id)

    def units_of_side(self, side: str) -> List[Unit]:
        return [u for u in self.units.values() if u.side == side]

    def side_alive(self, side: str) -> bool:
        return any(u.alive for u in self.units_of_side(side))

    def sides(self) -> List[str]:
        return sorted({u.side for u in self.units.values()})
