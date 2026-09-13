from __future__ import annotations

from typing import Dict, Iterable, List


class ElementChart:
    """Data-driven element effectiveness matrix (rows attack, columns defend).

    Backed by plain dicts — no numpy needed. Load it from JSON to customize;
    missing pairs default to 1.0, unknown elements are neutral.
    """

    def __init__(self, multipliers: Dict[str, Dict[str, float]] = None):
        self.multipliers: Dict[str, Dict[str, float]] = multipliers or {}

    def effectiveness(self, attack_element: str,
                      defender_elements: Iterable[str]) -> float:
        eff = 1.0
        row = self.multipliers.get(attack_element)
        if not row:
            return eff
        for de in defender_elements:
            eff *= float(row.get(de, 1.0))
        return eff


def build_default_chart() -> ElementChart:
    """Tiny example chart. Replace with your own data; engine never changes."""
    return ElementChart({
        "fire": {"nature": 2.0, "water": 0.5, "fire": 0.5},
        "water": {"fire": 2.0, "nature": 0.5, "water": 0.5},
        "nature": {"water": 2.0, "fire": 0.5, "nature": 0.5},
        "neutral": {},
    })
