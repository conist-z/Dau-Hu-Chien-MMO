import math

from game.blocks import BlockGrid
from game.map_loader import MapData
from game.state import Direction


# Web-player collision box, in tile units (smaller than 1 tile so free
# movement can slip through 1-tile gaps the same way Discord players do).
FLOAT_BOX_HALF = 0.3


class Collision:
    def __init__(self, map_data: MapData, blocks: BlockGrid = None,
                 resources=None):
        self.map_data = map_data
        self.blocks = blocks
        # Harvestable nodes (trees/bushes): standing sprites block movement
        # until felled; the grid reports them dynamically so a regrown node
        # becomes solid again with no extra bookkeeping here.
        self.resources = resources

    def is_walkable(self, x: int, y: int) -> bool:
        w, h = self.map_data.width, self.map_data.height
        if x < 0 or y < 0 or x >= w or y >= h:
            return False
        if self.blocks is not None and self.blocks.solid_at(x, y):
            return False
        if self.resources is not None:
            node = self.resources.node_at(x, y)
            if node is not None:
                # A FELLED node's tile is walkable: the tree is gone, the
                # player walks over the grass (the static "cây" blocker
                # must not keep the stump solid). While alive the node is
                # solid regardless of the static layers.
                return self.resources.is_chopped(node.anchor) or (
                    self.map_data.is_walkable(x, y)
                )
        return self.map_data.is_walkable(x, y)

    def can_move(self, x: int, y: int, direction: Direction) -> bool:
        dx, dy = direction.vector
        return self.is_walkable(x + dx, y + dy)

    # ----- continuous (web) movement: swept AABB per axis -----

    def _overlapped_rows(self, v_f: float) -> list:
        """Tile rows/columns the box actually overlaps on one axis."""
        r = FLOAT_BOX_HALF
        out = []
        for t in (math.floor(v_f - r), math.floor(v_f + r)):
            if t not in out and v_f - r < t + 1 and v_f + r > t:
                out.append(t)
        return out

    def _free_x(self, x_f: float, y_f: float, dx: float) -> float:
        """Movement allowed along x, clamped EXACTLY to the blocking wall so
        the player slides along it instead of stopping short."""
        if dx == 0:
            return 0.0
        r = FLOAT_BOX_HALF
        rows = self._overlapped_rows(y_f)
        if dx > 0:
            # Columns whose left edge the box's RIGHT edge crosses.
            start = math.floor(x_f + r) + 1
            end = math.floor(x_f + dx + r)
            for c in range(start, end + 1):
                if any(not self.is_walkable(c, t) for t in rows):
                    return min(dx, c - r - x_f)  # right edge touches column c
            return dx
        start = math.floor(x_f - r) - 1
        end = math.floor(x_f + dx - r)
        for c in range(start, end - 1, -1):
            if any(not self.is_walkable(c, t) for t in rows):
                return max(dx, (c + 1) + r - x_f)  # left edge touches c+1
        return dx

    def _free_y(self, x_f: float, y_f: float, dy: float) -> float:
        """Movement allowed along y (mirror of _free_x)."""
        if dy == 0:
            return 0.0
        r = FLOAT_BOX_HALF
        cols = self._overlapped_rows(x_f)
        if dy > 0:
            start = math.floor(y_f + r) + 1
            end = math.floor(y_f + dy + r)
            for t in range(start, end + 1):
                if any(not self.is_walkable(c, t) for c in cols):
                    return min(dy, t - r - y_f)
            return dy
        start = math.floor(y_f - r) - 1
        end = math.floor(y_f + dy - r)
        for t in range(start, end - 1, -1):
            if any(not self.is_walkable(c, t) for c in cols):
                return max(dy, (t + 1) + r - y_f)
        return dy

    def can_move_float(self, x_f: float, y_f: float, dx: float, dy: float) -> tuple:
        """Swept move for the continuous client: X first, then Y against the
        new x, each clamped to the wall — the player SLIDES along walls.
        Returns the new (x_f, y_f)."""
        nx = x_f + self._free_x(x_f, y_f, dx)
        ny = y_f + self._free_y(nx, y_f, dy)
        return nx, ny
