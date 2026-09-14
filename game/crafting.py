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

    ``pattern`` (optional, Minecraft-style): the EXACT grid layout the
    materials must sit in for the recipe to match — list of (item_id, col,
    row) with col/row in a 3x3 grid (0-indexed, origin top-left). When set,
    the web craft grid must match the LAYOUT (not just the multiset), and
    the client's quick-fill auto-arranges the materials into it. Mirror
    layouts count as a match (Minecraft parity).
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
    # [(item_id, col, row), ...] — see docstring. None = multiset-only.
    pattern: Optional[List[Tuple[str, int, int]]] = None


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
#
# Minecraft-parity GRID PATTERNS (user 14/09: chép từ wiki Mine). ``pattern``
# liệt kê từng ô trong lưới 3x3 (col, row, gốc trên-trái) — craft chỉ khớp khi
# nguyên liệu nằm ĐÚNG vị trí (cho phép đặt镜像 — Minecraft parity):
#   pickaxe: M M M / . S . / . S .      axe: M M . / M S . / . S .
#   shovel:  . M . / . S . / . S .      sword: . M . / . M . / . S .
# Số lượng nguyên liệu theo bậc: wood 1 / iron, gold 3 / steel 4 (xẻng chỉ
# có gỗ, 1 nguyên liệu). Gậy luôn 2 cái xếp thành cột.

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
    mats = {"wood": "gỗ", "stone": "đá", "iron": "sắt", "gold": "vàng", "steel": "thép"}
    # Số nguyên liệu mỗi công thức (wiki Mine: cúp/rìu 3, kiếm 2, xẻng 1).
    material_qty = {"wood": 3, "stone": 3, "iron": 3, "gold": 3, "steel": 3}
    material_item = {
        "wood": "plank",         # gỗ: dùng trực tiếp ván gỗ như Minecraft
        "stone": "stone",        # đá: cục đá khai thác từ tảng đá
        "iron": "iron_ingot",
        "gold": "gold_ingot",    # nung gold_ore ở lò (node drop: sau)
        "steel": "steel_ingot",  # hợp kim nung ở lò (node drop: sau)
    }

    def _pattern(fam: str, mat_item: str) -> List[Tuple[str, int, int]]:
        """Grid layout (3x3, origin top-left) per family — wiki Mine parity."""
        S = "stick"
        if fam == "pickaxe":
            # Hàng trên đầy nguyên liệu + 2 gậy cột giữa.
            return [(mat_item, 0, 0), (mat_item, 1, 0), (mat_item, 2, 0),
                    (S, 1, 1), (S, 1, 2)]
        if fam == "axe":
            # 2 nguyên liệu hàng trên (2 ô trái) + 1 ô dưới trái + 2 gậy cột phải.
            return [(mat_item, 0, 0), (mat_item, 1, 0), (mat_item, 0, 1),
                    (S, 2, 1), (S, 2, 2)]
        if fam == "shovel":
            # 1 nguyên liệu trên + 2 gậy cột giữa.
            return [(mat_item, 1, 0), (S, 1, 1), (S, 1, 2)]
        # sword: 2 nguyên liệu + 1 gậy thành 1 cột giữa.
        return [(mat_item, 1, 0), (mat_item, 1, 1), (S, 1, 2)]

    for fam in TOOL_FAMILIES:
        for mat in FAMILY_MATERIALS[fam]:
            rid = tool_item_id(fam, mat)
            if rid in RECIPE_REGISTRY:
                continue
            m_item = material_item[mat]
            qty = material_qty[mat]
            inputs: List[Tuple[str, int]] = []
            pat: Optional[List[Tuple[str, int, int]]] = None
            if m_item == "gold_ingot" or m_item == "steel_ingot":
                # Material chain CHỜ node drop (gold/steel ore chưa có trong
                # drops) — nhưng RECIPE ĐÃ MỞ: khi nguyên liệu xuất hiện là
                # craft được ngay, không cần sửa code nữa (user 14/09).
                inputs = [(m_item, qty), ("stick", 2)]
                pat = _pattern(fam, m_item)
                desc = (f"{ {"pickaxe": "3 nguyên liệu hàng trên + 2 gậy cột giữa",
                             "axe": "2 nguyên liệu hàng trên + 1 dưới + 2 gậy cột phải",
                             "shovel": "1 nguyên liệu trên + 2 gậy cột giữa",
                             "sword": "2 nguyên liệu + 1 gậy xếp thành cột"}[fam] }"
                        f" (wiki Mine) — thỏi {mats[mat]} nung ở lò.")
            else:
                inputs = [(m_item, qty), ("stick", 2)]
                pat = _pattern(fam, m_item)
                desc = {
                    "pickaxe": "3 nguyên liệu hàng trên + 2 gậy cột giữa (wiki Mine).",
                    "axe": "2 nguyên liệu hàng trên + 1 dưới + 2 gậy cột phải (wiki Mine).",
                    "shovel": "1 nguyên liệu trên + 2 gậy cột giữa (wiki Mine).",
                    "sword": "2 nguyên liệu + 1 gậy xếp thành cột (wiki Mine).",
                }[fam]
            RECIPE_REGISTRY[rid] = RecipeDef(
                rid,
                f"{names[fam]} {mats[mat]}",
                emojis[fam],
                inputs=inputs,
                output=(rid, 1),
                requires_table=True,
                group="tool",
                description=desc,
                pattern=pat,
            )


_register_tool_recipes()


def get_recipe(recipe_id: str) -> Optional[RecipeDef]:
    return RECIPE_REGISTRY.get(recipe_id)


def find_recipe_by_inputs(inputs: List[Tuple[str, int]],
                          pattern: Optional[List[Tuple[str, int, int]]] = None,
                          ) -> Optional[RecipeDef]:
    """Match placed materials to a recipe — the web craft-grid model.

    Two modes:
    - ``pattern`` is None: multiset match (old behaviour — same items, same
      quantities, nothing extra, any layout).
    - ``pattern`` given ([(item_id, col, row), ...] on the 3x3 grid): match
      the LAYOUT exactly, mirror allowed (Minecraft parity). Extra materials
      anywhere on the grid break the match.

    Pure: no state.
    """
    if pattern is not None:
        for recipe in RECIPE_REGISTRY.values():
            if recipe.pattern and _pattern_matches(recipe.pattern, pattern):
                return recipe
        return None
    want = sorted((iid, qty) for iid, qty in inputs if qty > 0)
    for recipe in RECIPE_REGISTRY.values():
        if sorted(recipe.inputs) == want:
            return recipe
    return None


def _pattern_matches(want: List[Tuple[str, int, int]],
                     placed: List[Tuple[str, int, int]]) -> bool:
    """Exact-layout comparison of two 3x3 patterns; the mirror (cols
    flipped) counts as a match — Minecraft parity for the axe. Pure."""
    def norm(cells: List[Tuple[str, int, int]]) -> frozenset:
        return frozenset(cells)

    def mirror(cells: List[Tuple[str, int, int]]) -> List[Tuple[str, int, int]]:
        return [(iid, 2 - col, row) for (iid, col, row) in cells]

    p = norm(placed)
    return p == norm(want) or p == norm(mirror(want))


def pattern_mirror(pattern: List[Tuple[str, int, int]]) -> List[Tuple[str, int, int]]:
    """Public helper: mirror a pattern across the vertical axis (the client
    uses it to offer the mirrored quick-fill when the direct layout fails
    the bag layout). Pure."""
    return [(iid, 2 - col, row) for (iid, col, row) in pattern]


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
