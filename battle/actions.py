from dataclasses import dataclass


@dataclass
class UseSkill:
    """Player/enemy intent for one round. Thin adapters build these from
    Discord buttons; the engine only knows this shape."""

    unit_id: str
    skill_id: str


@dataclass
class Flee:
    unit_id: str
