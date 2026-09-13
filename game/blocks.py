from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple


@dataclass
class BlockDef:
    """Data-driven block definition. Add blocks without touching engine code.

    A placed block is an OVERLAY on top of the map ground: placing covers the
    tile, breaking removes the overlay and the ground shows again.
    """

    id: str
    name: str
    emoji: str
    color: Tuple[int, int, int]  # RGB fill used by the renderer
    solid: bool = True  # True = blocks movement
    placeable: bool = True  # False = world-only decoration
    # Hits (with the RIGHT tool) needed to break. Bare hands / the wrong
    # tool deal HALF damage (rounded down, min 1) — holding the correct
    # tool is a real speed-up, and every block cracks progressively.
    hardness: int = 1
    # Optional local light emitted by this object (radius is in tiles).
    light_radius: float = 0.0
    light_intensity: float = 0.0
    light_color: Tuple[int, int, int] = (255, 190, 92)


# Starter set. Replace/extend freely; the grid/collision/renderer never change.
BLOCK_REGISTRY: Dict[str, BlockDef] = {
# hardness: số hit (đúng tool) để đập vỡ — tay không/tool sai gây nửa damage
# (min 1/hit) → bare-hand hits = hardness*2 cho gỗ (4 -> 8 phát tay không).
# Tuned 13/09: toàn bộ khối được nâng độ bền (gỗ phải chịu 4 hit đúng tool).
    "stone": BlockDef("stone", "Đá", "🪨", (128, 128, 136), solid=True, hardness=6),
    "wood": BlockDef("wood", "Gỗ", "🪵", (150, 104, 58), solid=True, hardness=4),
    "leaves": BlockDef("leaves", "Lá", "🌿", (72, 150, 74), solid=True, hardness=2),
    "torch": BlockDef(
        "torch", "Đuốc", "🕯️", (255, 196, 84), solid=True, hardness=1,
        light_radius=4.0, light_intensity=0.95, light_color=(255, 168, 64),
    ),
    # Floor overlay — user rule: EVERY placed block blocks movement (no
    # walk-through), so the floor is solid like everything else now.
    "floor": BlockDef("floor", "Sàn gỗ", "🟫", (176, 128, 80), solid=True, hardness=4),
    # Crafting station: placed like any block; standing within STATION_RANGE
    # unlocks the table recipes in game/crafting.py (checked at craft time).
    "crafting_table": BlockDef(
        "crafting_table", "Bàn chế tạo", "🛠️", (150, 110, 60), solid=True, hardness=5
    ),
    # Smelting station: crafted from the table (stone x8); placeable like any
    # block. Rendering/smelting UI hook up separately — the block exists so
    # the recipe output has a real placed form.
    "furnace": BlockDef("furnace", "Lò nung", "🔥", (110, 110, 116), solid=True, hardness=6),
}

PLACEABLE_BLOCK_IDS: List[str] = [b.id for b in BLOCK_REGISTRY.values() if b.placeable]

# Creative mode: True = đặt/phá không tốn/hoàn nguyên liệu (khối vô tận).
# Đặt False để quay lại chế độ sinh tồn (đặt trừ 1 nguyên liệu, phá hoàn 1).
# Hiện tại: SINH TỒN — đặt khối trừ nguyên liệu trong túi, phá hoàn lại.
CREATIVE_MODE = False


def get_block(block_id: str) -> Optional[BlockDef]:
    return BLOCK_REGISTRY.get(block_id)


def next_placeable_block(block_id: str) -> str:
    """Cycle the player's selected block through the placeable registry."""
    if block_id not in PLACEABLE_BLOCK_IDS:
        return PLACEABLE_BLOCK_IDS[0]
    i = PLACEABLE_BLOCK_IDS.index(block_id)
    return PLACEABLE_BLOCK_IDS[(i + 1) % len(PLACEABLE_BLOCK_IDS)]


class BlockGrid:
    """Per-scenario overlay grid: (x, y) -> block_id. Pure state, no IO.

    Only *placed* tiles are stored (sparse), so an untouched map costs nothing
    and a broken block simply disappears — the ground underneath is the map's.
    """

    def __init__(self):
        self._cells: Dict[Tuple[int, int], str] = {}
        # Progressive break damage: (x, y) -> accumulated damage. A block
        # breaks when damage >= its hardness; heal-to-full on regrow/reset.
        self.damage: Dict[Tuple[int, int], int] = {}

    def __len__(self) -> int:
        return len(self._cells)

    def items(self) -> Iterator[Tuple[Tuple[int, int], str]]:
        return iter(self._cells.items())

    def get(self, x: int, y: int) -> Optional[str]:
        return self._cells.get((x, y))

    def place(self, x: int, y: int, block_id: str) -> bool:
        """Cover the tile. False if a block is already there."""
        if (x, y) in self._cells:
            return False
        self._cells[(x, y)] = block_id
        self.damage.pop((x, y), None)
        return True

    def remove(self, x: int, y: int) -> Optional[str]:
        """Break the block; the ground shows again. None if the tile is bare."""
        self.damage.pop((x, y), None)
        return self._cells.pop((x, y), None)

    def add_damage(self, x: int, y: int, amount: int) -> int:
        """Accumulate break damage; returns the new total."""
        if (x, y) not in self._cells:
            return 0
        self.damage[(x, y)] = self.damage.get((x, y), 0) + max(0, amount)
        return self.damage[(x, y)]

    def damage_of(self, x: int, y: int) -> int:
        return self.damage.get((x, y), 0)

    def hardness_of(self, x: int, y: int) -> int:
        """Hardness of the block at (x, y); 0 when the tile is bare."""
        bid = self._cells.get((x, y))
        if bid is None:
            return 0
        bdef = BLOCK_REGISTRY.get(bid)
        return bdef.hardness if bdef is not None else 1

    def clear(self) -> None:
        """Remove every placed block (map reset). Keeps identity for
        Collision/manager references."""
        self._cells.clear()
        self.damage.clear()

    def solid_at(self, x: int, y: int) -> bool:
        bid = self._cells.get((x, y))
        if bid is None:
            return False
        bdef = BLOCK_REGISTRY.get(bid)
        return bool(bdef and bdef.solid)

    def to_dict(self) -> dict:
        return {f"{x},{y}": bid for (x, y), bid in self._cells.items()}

    @classmethod
    def from_dict(cls, data: dict) -> "BlockGrid":
        grid = cls()
        for key, bid in (data or {}).items():
            x_s, _, y_s = str(key).partition(",")
            grid._cells[(int(x_s), int(y_s))] = str(bid)
        return grid
