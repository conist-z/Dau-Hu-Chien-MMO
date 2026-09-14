"""Furnace smelting system — pure game logic, no discord (AGENTS.md rule 2).

Each placed ``furnace`` block owns a :class:`FurnaceState` keyed by its tile
coordinate on the scenario's GameState (``state.furnaces[(x, y)]``). One
furnace smelts ONE input at a time.

Fuel burns in SECONDS (Minecraft-style): adding fuel adds burn time to the
furnace's leftover ``fuel_seconds``; the furnace only CONSUMES fuel items
while an input is actively smelting (an idle furnace with leftover burn time
never eats fuel). When the smelt deadline elapses the output lands in the
furnace's output slot — the player presses "take" to move it into their bag
(the slot must be empty or matching before a new smelt can start).

Time is the caller's unix-ish clock (``time.time()`` in production, a fake in
tests), so a restart loses nothing: the persisted ``smelt_deadline`` keeps
ticking through downtime and the furnace may already be done on boot.

Station access (standing within STATION_RANGE of the furnace block) is
checked at EVERY mutating call, server-side — the UI may look near, the logic
never trusts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# Furnaces use the same Chebyshev range as the crafting table (re-exported so
# the UI layer can import STATION_RANGE from one module).
from game.crafting import STATION_RANGE  # noqa: F401


@dataclass
class FuelDef:
    """One burnable item: seconds of burn per single item."""

    item_id: str
    name: str
    seconds: float


# Data-driven fuel table (rule 10): tune burn times here, never in logic.
FUEL_REGISTRY: Dict[str, FuelDef] = {
    "coal": FuelDef("coal", "Than đá", 40.0),
    "charcoal": FuelDef("charcoal", "Than củi", 20.0),
    "wood": FuelDef("wood", "Khúc gỗ", 10.0),
    "plank": FuelDef("plank", "Ván gỗ", 5.0),
    "stick": FuelDef("stick", "Gậy", 3.0),
}


@dataclass
class SmeltDef:
    """One smelting recipe: input item -> output item over ``seconds``."""

    id: str
    name: str
    input_item: str
    output_item: str
    output_qty: int
    seconds: float


# Data-driven smelting recipes.
SMELT_REGISTRY: Dict[str, SmeltDef] = {
    "smelt_iron": SmeltDef(
        "smelt_iron", "Nung thỏi sắt", "iron_ore", "iron_ingot", 1, 10.0
    ),
    "smelt_gold": SmeltDef(
        "smelt_gold", "Nung thỏi vàng", "gold_ore", "gold_ingot", 1, 14.0
    ),
    "smelt_steel": SmeltDef(
        # Hợp kim: thỏi sắt + than (nhiên liệu ĐỒNG THỜI là nguyên liệu hợp kim
        # — hành vi Minecraft-ish, đơn giản cho MVP).
        "smelt_steel", "Nung thỏi thép", "iron_ingot", "steel_ingot", 1, 18.0
    ),
    "smelt_charcoal": SmeltDef(
        "smelt_charcoal", "Nung than củi", "wood", "charcoal", 1, 10.0
    ),
    "cook_meat": SmeltDef(
        "cook_meat", "Nấu thịt nướng", "raw_meat", "cooked_meat", 1, 8.0
    ),
}


# input item_id -> SmeltDef (each input smelts exactly one way for now).
SMELT_BY_INPUT: Dict[str, SmeltDef] = {s.input_item: s for s in SMELT_REGISTRY.values()}


@dataclass
class FurnaceState:
    """Per-furnace runtime state keyed by its block tile.

    ``input_item``/``input_qty`` is the slot being smelted (or waiting to);
    ``output_item``/``output_qty`` holds finished goods until a player takes
    them; ``fuel_seconds`` is leftover burn time (may be nonzero while idle);
    ``smelt_deadline`` (unix seconds) is set only while actively smelting.
    """

    x: int
    y: int
    input_item: Optional[str] = None
    input_qty: int = 0
    fuel_item: Optional[str] = None  # informational: last fuel item burned
    fuel_seconds: float = 0.0
    output_item: Optional[str] = None
    output_qty: int = 0
    smelt_deadline: Optional[float] = None

    @property
    def busy(self) -> bool:
        return self.smelt_deadline is not None

    @property
    def output_full(self) -> bool:
        return self.output_qty > 0

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "input_item": self.input_item,
            "input_qty": self.input_qty,
            "fuel_item": self.fuel_item,
            "fuel_seconds": self.fuel_seconds,
            "output_item": self.output_item,
            "output_qty": self.output_qty,
            "smelt_deadline": self.smelt_deadline,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FurnaceState":
        return cls(
            x=int(data["x"]),
            y=int(data["y"]),
            input_item=data.get("input_item"),
            input_qty=int(data.get("input_qty") or 0),
            fuel_item=data.get("fuel_item"),
            fuel_seconds=float(data.get("fuel_seconds") or 0.0),
            output_item=data.get("output_item"),
            output_qty=int(data.get("output_qty") or 0),
            smelt_deadline=data.get("smelt_deadline"),
        )


# ----- pure helpers -----------------------------------------------------------


def nearest_furnace(blocks, player) -> Optional[Tuple[int, int]]:
    """The nearest placed furnace block within STATION_RANGE of ``player``
    (Chebyshev), or None. ``blocks`` is a BlockGrid — duck-typed. Sparse scan
    over placed blocks only (rule 17: no hard-coded coords)."""
    px, py = player.x, player.y
    best = None
    best_d = None
    for (x, y), block_id in blocks.items():
        if block_id != "furnace":
            continue
        d = max(abs(x - px), abs(y - py))
        if d <= STATION_RANGE and (best_d is None or d < best_d):
            best, best_d = (x, y), d
    return best


def recipe_for(input_item: Optional[str]) -> Optional[SmeltDef]:
    if not input_item:
        return None
    return SMELT_BY_INPUT.get(input_item)


# ----- operations (all take ``now``; pure besides inventory mutation) ---------


def put_input(furnace: FurnaceState, inventory, item_id: str, qty: int = 1) -> Tuple[bool, str]:
    """Move up to ``qty`` of ``item_id`` from the bag into the input slot.

    Rejected when the item has no smelting recipe, the slot holds a different
    item, or the bag has none. The smelt does NOT start here — the next
    ``tick`` (or :func:`ensure_burning`) starts it when fuel allows.
    """
    if qty <= 0:
        return False, "bad_qty"
    smelt = recipe_for(item_id)
    if smelt is None:
        return False, "not_smeltable"
    if furnace.input_item is not None and furnace.input_item != item_id:
        return False, "slot_occupied"
    if inventory.count(item_id) < qty:
        return False, "missing_materials"
    if furnace.input_qty + qty > 64:
        return False, "input_full"
    inventory.remove(item_id, qty)
    furnace.input_item = item_id
    furnace.input_qty += qty
    return True, "ok"


def add_fuel(furnace: FurnaceState, inventory, item_id: str, qty: int = 1) -> Tuple[bool, str]:
    """Burn ``qty`` fuel items from the bag, adding seconds to the furnace.
    Accepted even while idle (leftover burn persists)."""
    fdef = FUEL_REGISTRY.get(item_id)
    if fdef is None:
        return False, "not_fuel"
    if qty <= 0:
        return False, "bad_qty"
    if inventory.count(item_id) < qty:
        return False, "missing_materials"
    inventory.remove(item_id, qty)
    furnace.fuel_item = item_id
    furnace.fuel_seconds += fdef.seconds * qty
    return True, "ok"


def ensure_burning(furnace: FurnaceState, now: float) -> None:
    """Start the smelt deadline when the furnace can run: input present,
    output room, and enough burn seconds left to FINISH the current smelt.

    Pay-upfront model: arming the deadline DEDUCTS the recipe's seconds from
    leftover burn — deterministic, no per-tick drain, and a smelt that has
    been paid for always finishes (fuel can never run out mid-smelt).
    Topping up the burn time for the NEXT smelt is the caller's job
    (``add_fuel``). Persisted deadline keeps ticking through downtime.

    The output slot ACCUMULATES the same item, so smelting only halts when
    the slot holds a DIFFERENT item or hits the 64-stack cap.
    """
    if furnace.busy or furnace.input_qty <= 0:
        return
    smelt = recipe_for(furnace.input_item)
    if smelt is None:
        return
    if furnace.output_item not in (None, smelt.output_item):
        return
    if furnace.output_item == smelt.output_item and furnace.output_qty >= 64:
        return
    if furnace.fuel_seconds < smelt.seconds:
        return
    furnace.fuel_seconds -= smelt.seconds
    furnace.smelt_deadline = now + smelt.seconds


def tick(furnace: FurnaceState, now: float) -> bool:
    """Advance one furnace. Returns True when its VISIBLE state changed
    (output landed).

    Deadline model: a smelt that was armed (paid for in ``ensure_burning``)
    always finishes, even across a bot restart — the persisted deadline keeps
    ticking through downtime. On completion the output lands in the slot and
    the next input (if any, and leftover fuel suffices) re-arms immediately.
    """
    changed = False
    if furnace.busy and now >= furnace.smelt_deadline:
        smelt = recipe_for(furnace.input_item)
        if smelt is not None:
            furnace.input_qty -= 1
            if furnace.input_qty <= 0:
                furnace.input_item = None
                furnace.input_qty = 0
            if furnace.output_item in (None, smelt.output_item):
                furnace.output_item = smelt.output_item
                furnace.output_qty += smelt.output_qty
            furnace.smelt_deadline = None
            changed = True
    if not furnace.busy:
        ensure_burning(furnace, now)
    return changed


def take_output(furnace: FurnaceState, inventory) -> Tuple[bool, str]:
    """Move finished output into the player's bag. Pure state + bag mutation."""
    if furnace.output_qty <= 0 or not furnace.output_item:
        return False, "empty_output"
    inventory.add(furnace.output_item, furnace.output_qty)
    furnace.output_item = None
    furnace.output_qty = 0
    return True, "ok"
