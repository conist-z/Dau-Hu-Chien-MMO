"""Tool tier system — data-driven tool definitions (pure, no discord/IO).

Every tool family (shovel / pickaxe / axe / sword) comes in five material
 tiers, strongest last: stone ("đá"), iron ("sắt"), gold ("vàng") and
 steel ("thép"). The wood tier renders the bronze sheets (legacy naming).

 VISUAL MAPPING (user rule — assets are recolours, not new names):
 ``wood`` renders the Kaetram BRONZE sheets, ``iron`` the IRON sheets,
 ``gold`` the GOLD sheets and ``steel`` the COBALT (blue-steel) sheets.
 The shovel exists only at the wood tier (one shovel total).

Item ids follow ``<material>_<tool>``: wood_pickaxe, iron_axe,
gold_sword, steel_pickaxe, wood_shovel, ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

# Tier order (weakest -> strongest). Visual assets per tier live in
# ``TIER_SHEET_STEM`` (game/appearance.py WEAPON_SHEETS uses it).
MATERIALS = ("wood", "stone", "iron", "gold", "steel")

TOOL_FAMILIES = ("shovel", "pickaxe", "axe", "sword")

# Materials each family exists in. The shovel ships ONLY at the wood tier
# (user rule 13/09: "xẻng thì chỉ có mỗi 1 loại là xẻng gỗ").
FAMILY_MATERIALS: Dict[str, tuple] = {
    "shovel": ("wood",),
    "pickaxe": MATERIALS,
    "axe": MATERIALS,
    "sword": MATERIALS,
}

# Kaetram sheet stems per tier (recycled art, renamed semantics).
TIER_SHEET_STEM = {
    "wood": {
        "sword": "bronzesword",
        "axe": "bronzeaxe",
        "pickaxe": "bronzepickaxe",
        "shovel": "spoon",  # placeholder art; the wood shovel is the spoon
    },
    "stone": {  # Kaetram sheets closest to a stone-tool look (no real
        # stone tool art exists): tin sword + bronze axe/pickaxe variants
        "sword": "tinsword",
        "axe": "bronzebattleaxe",
        "pickaxe": "bonepickaxe",
    },
    "iron": {"sword": "ironsword", "axe": "ironaxe", "pickaxe": "ironpickaxe"},
    "gold": {"sword": "goldsword", "axe": "goldaxe", "pickaxe": "goldpickaxe"},
    "steel": {"sword": "steelsword", "axe": "cobaltaxe", "pickaxe": "cobaltpickaxe"},
}

# ---- base values (tier "wood" = 1.0) --------------------------------------
# Per-tier strength multiplier applied to the family base.
TIER_MULT = {"wood": 1.0, "stone": 1.3, "iron": 1.6, "gold": 2.25, "steel": 3.2}

# Family base stats at the wood tier.
SHOVEL_BASE_HITS = 3      # swings to scoop one grass tuft
PICKAXE_BASE_HITS = 5     # swings to mine one ore node
# Swings for an AXE to fell one tree (bare hands use NODE_DEFS tree hits =
# 12 — user tune 13/09). Wood axe 9, iron ~6, gold 4, steel ~3: a real tier
# ladder below the bare-hand grind.
AXE_BASE_HITS = 9
SWORD_BASE_DAMAGE = 14    # damage per hit vs zombies

# Zombie HP lives in game/zombies.py; the damage table above (FAMILY_BASE_DAMAGE
# x TIER_MULT) is tuned so the WOOD sword kills in ~3 hits (40 HP / 14 ~= 2.9).
SWORD_TIER_DAMAGE = {
    mat: round(SWORD_BASE_DAMAGE * TIER_MULT[mat]) for mat in MATERIALS
}

# ---- melee damage (per family, per tier) -----------------------------------
# Damage of ONE hit vs a zombie, scaled by the held tool's family and material
# tier: swords hit hardest, axes next, pickaxes/shovels are weak improvises.
# Bare-hand damage lives in game/zombies.py (BARE_HAND_ATTACK_DAMAGE = 6).
AXE_BASE_DAMAGE = 11        # wood axe; zombie 40 HP -> ~4 hits
PICKAXE_BASE_DAMAGE = 8
SHOVEL_BASE_DAMAGE = 5
FAMILY_BASE_DAMAGE = {
    "sword": SWORD_BASE_DAMAGE,
    "axe": AXE_BASE_DAMAGE,
    "pickaxe": PICKAXE_BASE_DAMAGE,
    "shovel": SHOVEL_BASE_DAMAGE,
}


def tool_damage(item_id: Optional[str]) -> int:
    """Melee damage of a SPECIFIC tool id (family base x material tier);
    0 when the id is not a tool (bare hand -> caller's fallback)."""
    td = parse_tool_id(item_id)
    if td is None:
        return 0
    return round(FAMILY_BASE_DAMAGE.get(td.family, 0) * mult_of(td.material))


# ---- derived per-tier stats ------------------------------------------------

def shovel_hits(material: str = "wood") -> int:
    """Swings needed to scoop one grass tile with ``material`` shovel."""
    mult = TIER_MULT.get(material, 1.0)
    # Faster tiers: fewer presses, but never below 1.
    return max(1, round(SHOVEL_BASE_HITS / mult))


def pickaxe_hits(material: str) -> int:
    return max(1, round(PICKAXE_BASE_HITS / mult_of(material)))


def axe_hits(material: str) -> int:
    return max(1, round(AXE_BASE_HITS / mult_of(material)))


def mult_of(material: str) -> float:
    return TIER_MULT.get(material, 1.0)


# ---- item registry helpers --------------------------------------------------

@dataclass(frozen=True)
class ToolDef:
    item_id: str
    family: str      # shovel | pickaxe | axe | sword
    material: str    # wood | iron | gold | steel
    emoji: str
    name: str
    description: str


def tool_item_id(family: str, material: str) -> str:
    return f"{material}_{family}"


def parse_tool_id(item_id: Optional[str]) -> Optional[ToolDef]:
    """Parse ``<material>_<family>`` into a ToolDef; None when not a tool."""
    if not item_id or "_" not in item_id:
        return None
    material, _, family = item_id.partition("_")
    if material not in MATERIALS or family not in TOOL_FAMILIES:
        return None
    if material not in FAMILY_MATERIALS[family]:
        return None  # e.g. iron_shovel does not exist (wood-only shovel)
    names = {
        "shovel": "Xẻng",
        "pickaxe": "Cúp",
        "axe": "Rìu",
        "sword": "Kiếm",
    }
    mats = {"wood": "gỗ", "stone": "đá", "iron": "sắt", "gold": "vàng", "steel": "thép"}
    emojis = {
        ("shovel", "wood"): "🥄",
        ("pickaxe", "wood"): "⛏️",
        ("axe", "wood"): "🪓",
        ("sword", "wood"): "🗡️",
        ("pickaxe", "stone"): "⛏️",
        ("axe", "stone"): "🪓",
        ("sword", "stone"): "🗡️",
        ("pickaxe", "iron"): "⛏️",
        ("axe", "iron"): "🪓",
        ("sword", "iron"): "⚔️",
        ("pickaxe", "gold"): "⛏️",
        ("axe", "gold"): "🪓",
        ("sword", "gold"): "⚔️",
        ("pickaxe", "steel"): "⛏️",
        ("axe", "steel"): "🪓",
        ("sword", "steel"): "⚔️",
    }
    return ToolDef(
        item_id=item_id,
        family=family,
        material=material,
        emoji=emojis.get((family, material), "🔧"),
        name=f"{names[family]} {mats[material]}",
        description=f"Công cụ {mats[material]} bậc {MATERIALS.index(material) + 1}.",
    )


def all_tool_ids() -> list:
    out = []
    for fam in TOOL_FAMILIES:
        for mat in FAMILY_MATERIALS[fam]:
            out.append(tool_item_id(fam, mat))
    return out


def tier_rank(material: str) -> int:
    """Higher rank = stronger material. Unknown materials rank -1."""
    try:
        return MATERIALS.index(material)
    except ValueError:
        return -1


# ---- held-tool resolution ---------------------------------------------------

def best_tool_of_family(inventory, family: str) -> Optional[str]:
    """The strongest tool of ``family`` present (with stock) in the ordered
    bag/hotbar. Reads the bag front-to-back so the hotbar order decides which
    one is "held" when several exist."""
    if inventory is None:
        return None
    best = None
    best_rank = -1
    for _slot, iid in inventory.hotbar().items():
        td = parse_tool_id(iid)
        if td is None or td.family != family:
            continue
        if inventory.count(iid) <= 0:
            continue
        rank = tier_rank(td.material)
        if rank > best_rank:
            best, best_rank = iid, rank
    return best


def has_pickaxe_tier(inventory, min_material: str = "dirt") -> bool:
    """True when the player holds a pickaxe of at least ``min_material`` tier.
    The stone-mining gate (user rule: đá chỉ đập được bằng cúp đất trở lên)."""
    iid = best_tool_of_family(inventory, "pickaxe")
    if iid is None:
        return False
    td = parse_tool_id(iid)
    return td is not None and tier_rank(td.material) >= tier_rank(min_material)


def sword_damage(inventory) -> int:
    """Attack damage of the best held sword (bare hands fall back to 0 — the
    caller decides the bare-hand value)."""
    iid = best_tool_of_family(inventory, "sword")
    if iid is None:
        return 0
    td = parse_tool_id(iid)
    if td is None:
        return 0
    return SWORD_TIER_DAMAGE.get(td.material, SWORD_BASE_DAMAGE)
