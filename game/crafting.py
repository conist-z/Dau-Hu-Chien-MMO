"""Crafting system — pure game logic, no discord (AGENTS.md principle 2).

One crafting station exists: the ``crafting_table`` block. The player places
it on the map from their hotbar like any other block, and stands within
``STATION_RANGE`` tiles (Chebyshev distance) to unlock the table recipes.
Recipes needing the table are simply not craftable while out of range — no
status messages, the availability itself is the signal.

Station access is checked at CRAFT time (server-side, rule 9): the UI may
show a recipe while the player is near the table, but ``apply_craft``
re-checks the block grid before consuming anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Range (Chebyshev) at which a placed station block becomes usable.
STATION_RANGE = 3

# Block ids that act as crafting stations. Data-driven: adding a new station
# is a BLOCK_REGISTRY entry + a line here (no logic changes).
STATION_BLOCK_IDS = {"crafting_table"}


@dataclass
class RecipeDef:
    """One craftable recipe. Data-driven (rule 10): tune without logic edits.

    ``requires_table`` marks recipes only craftable while the player stands
    near a placed crafting table; simple recipes (planks, the table itself)
    are craftable anywhere by hand.
    """

    id: str
    name: str
    emoji: str
    inputs: List[Tuple[str, int]]  # [(item_id, qty), ...]
    output: Tuple[str, int]        # (item_id, qty)
    requires_table: bool = False
    description: str = ""
    # UI catalog tab (web quick-craft filter): "tool" (công cụ/vũ khí),
    # "decor" (trang trí + block: bàn chế tạo, lò nung, đuốc...), "usable"
    # (dùng được: thuốc, đồ ăn...). Anything else defaults to "usable".
    group: str = "usable"


RECIPE_REGISTRY: Dict[str, RecipeDef] = {
    # --- hand (anywhere) ---
    "plank": RecipeDef(
        "plank", "Ván gỗ", "🟫",
        inputs=[("wood", 1)],
        output=("plank", 4),
        description="Nguyên liệu cơ bản từ gỗ.",
    ),
    "stick": RecipeDef(
        "stick", "Gậy", "🥢",
        inputs=[("plank", 2)],
        output=("stick", 4),
        description="Chuẩn bị cho mọi công cụ.",
    ),
    "crafting_table": RecipeDef(
        "crafting_table", "Bàn chế tạo", "🛠️",
        inputs=[("plank", 4)],
        output=("crafting_table", 1),
        group="decor",
        description="Đặt ra đất, đứng gần để mở khóa công thức phức tạp.",
    ),
    # --- near a placed crafting table ---
    "wood_axe": RecipeDef(
        "wood_axe", "Rìu gỗ", "🪓",
        inputs=[],  # tool recipes intentionally empty for now (14/09)
        output=("wood_axe", 1),
        requires_table=True,
        description="Chặt cây nhanh hơn (công thức chưa mở).",
    ),
    "wood_pickaxe": RecipeDef(
        "wood_pickaxe", "Cuốc gỗ", "⛏️",
        inputs=[],  # tool recipes intentionally empty for now (14/09)
        output=("wood_pickaxe", 1),
        requires_table=True,
        description="Đập đá hiệu quả hơn (công thức chưa mở).",
    ),
    "furnace": RecipeDef(
        "furnace", "Lò nung", "🔥",
        inputs=[("stone", 8)],
        output=("furnace", 1),
        requires_table=True,
        group="decor",
        description="Nung nguyên liệu ở nhiệt độ cao.",
    ),
    # Torch by hand: stick + coal (the light-source block already exists).
    "torch": RecipeDef(
        "torch", "Đuốc", "🕯️",
        inputs=[("stick", 1), ("coal", 1)],
        output=("torch", 4),
        group="decor",
        description="Ánh sáng giữa đêm tối.",
    ),
}


# ---- Tool recipes (game/tools.py): mỗi tool được làm từ đúng vật liệu của nó
# (gỗ / sắt / vàng / thép) và CHỈ chế được khi có bàn chế tạo gần đó (user rule).
# Bậc sau tốn nhiều nguyên liệu hơn bậc trước (data-driven, tune tại đây).
#
# RECIPES ARE INTENTIONALLY EMPTY for now (user 14/09: "công thức của bọn nó
# tạm thời để trống") — the loader below registers a skeleton entry per tool
# with NO inputs, meaning "not yet craftable". Fill ``inputs`` to enable.
def _register_tool_recipes() -> None:
    from game.tools import FAMILY_MATERIALS, TOOL_FAMILIES, tool_item_id

    names = {
        "shovel": "Xẻng",
        "pickaxe": "Cúp",
        "axe": "Rìu",
        "sword": "Kiếm",
    }
    emojis = {
        "shovel": "🥄",
        "pickaxe": "⛏️",
        "axe": "🪓",
        "sword": "🗡️",
    }
    mats = {"wood": "gỗ", "iron": "sắt", "gold": "vàng", "steel": "thép"}
    # Per-tier material cost once recipes go live (tune freely).
    material_qty = {"wood": 2, "iron": 3, "gold": 3, "steel": 4}
    stick_qty = {"wood": 2, "iron": 2, "gold": 2, "steel": 2}
    material_item = {
        "wood": "wood",
        "iron": "iron_ingot",
        "gold": "gold_ingot",   # not yet obtainable — recipe stays empty
        "steel": "steel_ingot", # not yet obtainable — recipe stays empty
    }
    for fam in TOOL_FAMILIES:
        for mat in FAMILY_MATERIALS[fam]:
            rid = tool_item_id(fam, mat)
            if rid in RECIPE_REGISTRY:
                continue
            # EMPTY inputs = skeleton only; craft is impossible until filled.
            RECIPE_REGISTRY[rid] = RecipeDef(
                rid,
                f"{names[fam]} {mats[mat]}",
                emojis[fam],
                inputs=[],  # TODO: fill when the material chain lands
                output=(rid, 1),
                requires_table=True,
                group="tool",
                description=f"Công cụ {mats[mat]} bậc {"wood iron gold steel".split().index(mat) + 1} — công thức chưa mở.",
            )


_register_tool_recipes()


def get_recipe(recipe_id: str) -> Optional[RecipeDef]:
    return RECIPE_REGISTRY.get(recipe_id)


def find_recipe_by_inputs(inputs: List[Tuple[str, int]]) -> Optional[RecipeDef]:
    """Match an exact input multiset ("place materials in the grid") to a
    recipe — the web craft-grid model: the placed materials ARE the recipe.

    A recipe matches when its input multiset equals the placed multiset
    exactly (same items, same quantities, nothing extra). Pure: no state.
    """
    want = sorted((iid, qty) for iid, qty in inputs if qty > 0)
    for recipe in RECIPE_REGISTRY.values():
        if sorted(recipe.inputs) == want:
            return recipe
    return None


def can_craft(recipe: RecipeDef, inventory, near_table: bool) -> Tuple[bool, str]:
    """Pure check: does ``inventory`` (game/inventory.Inventory) satisfy the
    recipe, and is the table requirement met? Returns (ok, reason)."""
    if recipe.requires_table and not near_table:
        return False, "no_station"
    if not recipe.inputs:
        # Skeleton recipe (tool tiers before their material chain lands):
        # structurally present, practically uncraftable.
        return False, "missing_materials"
    for item_id, qty in recipe.inputs:
        if inventory.count(item_id) < qty:
            return False, "missing_materials"
    return True, "ok"


def can_craft_table_free(recipe: RecipeDef, placed: Dict[str, int],
                         near_table: bool) -> Tuple[bool, str]:
    """Grid-model check: the materials ALREADY sit on the craft table
    (``placed`` = {item_id: qty} — the server-side material grid), so only
    the table gate and the exact-input match are verified here. The caller
    must confirm ``placed`` matches ``recipe.inputs`` first."""
    if recipe.requires_table and not near_table:
        return False, "no_station"
    if not recipe.inputs:
        # Skeleton recipe (tool tiers before their material chain lands):
        # structurally present, practically uncraftable.
        return False, "missing_materials"
    for item_id, qty in recipe.inputs:
        if placed.get(item_id, 0) < qty:
            return False, "missing_materials"
    return True, "ok"


def do_craft(recipe: RecipeDef, inventory) -> Tuple[str, int]:
    """Consume inputs and add the output. Pure state mutation; the caller
    must have validated with :func:`can_craft` first."""
    for item_id, qty in recipe.inputs:
        inventory.remove(item_id, qty)
    out_id, out_qty = recipe.output
    inventory.add(out_id, out_qty)
    return out_id, out_qty


def nearest_station(blocks, player) -> bool:
    """True if any station block sits within STATION_RANGE of ``player``
    (Chebyshev, same metric as CHOP_RANGE). ``blocks`` is a BlockGrid —
    duck-typed so tests can pass a stub. Rule 17: no hard-coded coords; the
    grid is scanned sparsely so cost scales with placed blocks only."""
    px, py = player.x, player.y
    for (x, y), block_id in blocks.items():
        if block_id in STATION_BLOCK_IDS:
            if max(abs(x - px), abs(y - py)) <= STATION_RANGE:
                return True
    return False
