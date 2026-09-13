from dataclasses import dataclass, field
from typing import Dict, List, Optional

from game.blocks import BLOCK_REGISTRY
from game.tools import MATERIALS, TOOL_FAMILIES, parse_tool_id, tool_item_id


@dataclass
class ItemDef:
    id: str
    name: str
    emoji: str
    type: str  # consumable | equipment | key | material
    effect: Dict[str, int] = field(default_factory=dict)
    description: str = ""


# Minimal data-driven registry. Items are referenced by id from inventory rows
# and from NPC dialogue effects; this stays pure (no discord / no IO).
ITEM_REGISTRY: Dict[str, ItemDef] = {
    "potion_hp": ItemDef(
        "potion_hp", "Potion HP", "🧪", "consumable", {"heal_hp": 30}, "Hồi 30 HP"
    ),
    "potion_mp": ItemDef(
        "potion_mp", "Potion Mana", "🔵", "consumable", {"heal_mp": 20}, "Hồi 20 Mana"
    ),
    "key_stone": ItemDef("key_stone", "Chìa khoá đá", "🔑", "key", {}, "Mở cửa bí mật"),
    # Gathered from trees/bushes (game/resources.py); heals a little.
    "apple": ItemDef("apple", "Táo", "🍎", "consumable", {"heal_hp": 15}, "Hồi 15 HP"),
    # Crafted materials (game/crafting.py). Blocks (crafting_table) come from
    # the BLOCK fallback below.
    "plank": ItemDef("plank", "Ván gỗ", "🟫", "material", {}, "Nguyên liệu chế tạo"),
    "stick": ItemDef("stick", "Gậy", "🥢", "material", {}, "Nguyên liệu chế tạo"),
    "wood_axe": ItemDef(
        "wood_axe", "Rìu gỗ", "🪓", "material", {}, "Chặt cây nhanh hơn (sắp dùng)"
    ),
    "wood_pickaxe": ItemDef(
        "wood_pickaxe", "Cuốc gỗ", "⛏️", "material", {}, "Đập đá hiệu quả hơn (sắp dùng)"
    ),
    "rotten_flesh": ItemDef("rotten_flesh", "Thịt thối", "🥩", "material", {}, "Đồ rơi từ zombie"),
    "coin": ItemDef("coin", "Xu", "🪙", "material", {}, "Xu nhặt được từ zombie"),
    # Smelting chain (game/smelting.py): ores from mining, ingots/fuel/food
    # from the furnace, cooked meat is a real consumable.
    "iron_ore": ItemDef("iron_ore", "Quặng sắt", "🟤", "material", {}, "Nung ở lò ra thỏi sắt"),
    "coal": ItemDef("coal", "Than đá", "⚫", "material", {}, "Nhiên liệu tốt nhất cho lò nung"),
    "iron_ingot": ItemDef("iron_ingot", "Thỏi sắt", "🥈", "material", {}, "Nguyên liệu tool sắt"),
    "charcoal": ItemDef("charcoal", "Than củi", "🌑", "material", {}, "Nhiên liệu nung gỗ trong lò"),
    "raw_meat": ItemDef("raw_meat", "Thịt sống", "🍖", "material", {}, "Nấu chín ở lò mới ăn được"),
    "cooked_meat": ItemDef(
        "cooked_meat", "Thịt nướng", "🍗", "consumable", {"heal_hp": 30}, "Hồi 30 HP"
    ),
}

# --- Tool tiers (game/tools.py): dirt/wood/stone x shovel/pickaxe/axe/sword
# are registered data-driven so every tool id renders a proper name+emoji in
# the bag/hotbar/hub instead of "❓".
_FAMILY_EMOJI = {"shovel": "🥄", "pickaxe": "⛏️", "axe": "🪓", "sword": "🗡️"}
for _mat in MATERIALS:
    for _fam in TOOL_FAMILIES:
        _tid = tool_item_id(_fam, _mat)
        _td = parse_tool_id(_tid)
        if _td is not None and _tid not in ITEM_REGISTRY:
            ITEM_REGISTRY[_tid] = ItemDef(
                _tid, _td.name, _td.emoji, "material", {}, _td.description
            )

# Scoopable-grass drop (game/terrain.py + terrain_rules.py).
ITEM_REGISTRY.setdefault(
    "dirt", ItemDef("dirt", "Đất", "🟤", "material", {}, "Nguyên liệu chế tạo tool đất")
)


# Items that count as a weapon when held (full attack damage). Everything
# else (or nothing) means bare-handed punching. Every tool tier counts —
# swords carry their own damage table (game/tools.py SWORD_TIER_DAMAGE);
# axe/pickaxe fall back to the legacy weapon damage.
WEAPON_ITEM_IDS = {
    "wood_axe", "wood_pickaxe",
    *[tool_item_id(f, m) for m in MATERIALS for f in TOOL_FAMILIES],
}


def is_weapon(item_id: Optional[str]) -> bool:
    """True when the held item grants weapon-class attack damage."""
    return item_id in WEAPON_ITEM_IDS


def get_item(item_id: str) -> Optional[ItemDef]:
    it = ITEM_REGISTRY.get(item_id)
    if it is not None:
        return it
    # Blocks (stone/wood/...) live in the BLOCK registry but appear in bags
    # exactly like items — expose them as material items so the inventory
    # grid, hotbar and hub never render them as "❓".
    b = BLOCK_REGISTRY.get(item_id)
    if b is not None:
        return ItemDef(
            b.id, b.name, b.emoji, "material", {}, "Nguyên liệu xây dựng (đặt bằng hotbar)"
        )
    return None


def apply_effect(player, item_id: str):
    """Apply a consumable's effect to a Player. Pure: mutates the player only.

    Returns (changed, reason). Non-consumables are not usable here.
    """
    item = ITEM_REGISTRY.get(item_id)
    if item is None:
        return False, "unknown_item"
    if item.type != "consumable":
        return False, "not_usable"
    e = item.effect
    if "heal_hp" in e:
        player.hp = min(player.max_hp, player.hp + e["heal_hp"])
    if "heal_mp" in e:
        player.mana = min(player.max_mana, player.mana + e["heal_mp"])
    return True, "ok"
