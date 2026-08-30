import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image


@dataclass
class MapData:
    map_id: str
    width: int
    height: int
    tile_width: int = 32
    tile_height: int = 32
    collision: List[List[int]] = field(default_factory=list)
    spawn: tuple = (0, 0)
    image_path: Optional[Path] = None
    # Tiled tileset (sliced at render time); None for simple/baked maps.
    tileset: Optional[Dict] = None
    # Ordered tile layers: (name, grid[y][x] of tile GIDs).
    tile_layers: List[Tuple[str, List[List[int]]]] = field(default_factory=list)
    display_name: str = ""

    def is_walkable(self, x: int, y: int) -> bool:
        if not self.collision:
            return True
        if y < 0 or y >= len(self.collision) or x < 0 or x >= len(self.collision[0]):
            return False
        return self.collision[y][x] == 0


def _unwrap_godot(text: str) -> dict:
    """The Godot Tiled exporter wraps Tiled JSON in (function(...){...})(name, DATA)."""
    m = re.search(
        r"\(\s*[^,()]+,\s*(\{.*\})\s*\)\s*;?\s*$",
        text,
        re.DOTALL,
    )
    if not m:
        raise ValueError("Not a recognisable Godot/Tiled wrapper")
    return json.loads(m.group(1))


def _read_raw(assets_dir: Path, map_id: str):
    for ext in (".js", ".json"):
        path = assets_dir / f"{map_id}{ext}"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return _unwrap_godot(text)
    raise FileNotFoundError(f"No map file for {map_id!r}")


def _resolve_tileset(tilesets, assets_dir: Path) -> Optional[Dict]:
    if not tilesets:
        return None
    ts = tilesets[0]
    img = ts.get("image")
    if not img:
        return None
    rel = Path(img)
    candidates = [
        assets_dir / rel,
        assets_dir / rel.name,
        assets_dir / "tilesets" / rel.name,
    ]
    resolved = next((c for c in candidates if c.exists()), None)
    return {
        "image_path": resolved,
        "firstgid": ts.get("firstgid", 1),
        "columns": ts.get("columns", 1),
        "tilewidth": ts.get("tilewidth", 32),
    }


def _layers_from_tiled(data: dict) -> List[Tuple[str, List[List[int]]]]:
    out = []
    for layer in data.get("layers", []):
        if layer.get("type") != "tilelayer":
            continue
        w = layer.get("width", 0)
        h = layer.get("height", 0)
        flat = layer.get("data", [])
        grid = [flat[r * w : (r + 1) * w] for r in range(h)]
        out.append((layer.get("name", ""), grid))
    return out


def _bbox_from_layers(layers: List[Tuple[str, List[List[int]]]]):
    """True extent of the map: bounding box of every placed tile.

    A Tiled canvas is often larger than the drawn region; per the project's
    rule the real map size equals the spread of placed assets, so empty
    canvas cells are not part of the map.
    """
    minx = miny = 10 ** 9
    maxx = maxy = -1
    for _name, grid in layers:
        for y, row in enumerate(grid):
            for x, c in enumerate(row):
                if c != 0:
                    if x < minx:
                        minx = x
                    if x > maxx:
                        maxx = x
                    if y < miny:
                        miny = y
                    if y > maxy:
                        maxy = y
    if maxx < 0:
        return None
    return (minx, miny, maxx, maxy)


def _remap_layers(
    layers: List[Tuple[str, List[List[int]]]], ox: int, oy: int, w: int, h: int
) -> List[Tuple[str, List[List[int]]]]:
    """Shift every layer so the bounding-box origin becomes map (0, 0)."""
    out = []
    for name, grid in layers:
        gh = len(grid)
        gw = len(grid[0]) if gh else 0
        new_grid = [[0] * w for _ in range(h)]
        for ny in range(h):
            sy = ny + oy
            if sy < 0 or sy >= gh:
                continue
            src_row = grid[sy]
            for nx in range(w):
                sx = nx + ox
                if sx < 0 or sx >= gw:
                    continue
                new_grid[ny][nx] = src_row[sx]
        out.append((name, new_grid))
    return out


def _collision_from_layers(
    layers: List[Tuple[str, List[List[int]]]], width: int, height: int
) -> List[List[int]]:
    # Prefer an explicit collision layer; otherwise the map is fully walkable.
    # A "first layer is solid" heuristic is unsafe: a base/ground layer often
    # covers the whole drawn region, which would make the entire map a wall.
    for name, grid in layers:
        nl = (name or "").lower()
        if "collision" in nl or "va cham" in nl or "vacham" in nl:
            return [[1 if c != 0 else 0 for c in row] for row in grid]
    return [[0 for _ in range(width)] for _ in range(height)]


def _first_walkable(collision: List[List[int]]) -> tuple:
    for y, row in enumerate(collision):
        for x, v in enumerate(row):
            if v == 0:
                return (x, y)
    return (0, 0)


def _walkable_at(collision: List[List[int]], x: int, y: int) -> bool:
    if not collision:
        return True
    if y < 0 or y >= len(collision) or x < 0 or x >= len(collision[0]):
        return False
    return collision[y][x] == 0


def load_map(map_id: str, assets_dir: Path) -> MapData:
    data = _read_raw(assets_dir, map_id)
    is_tiled = "layers" in data and "tilesets" in data

    if not is_tiled:
        # Simple format (legacy test-map.json): width/height/collision/spawn.
        width = data.get("width", 10)
        height = data.get("height", 10)
        tile_width = data.get("tilewidth", 32)
        tile_height = data.get("tileheight", 32)
        collision = data.get("collision") or [
            [0 for _ in range(width)] for _ in range(height)
        ]
        spawn_raw = data.get("spawn", {"x": 0, "y": 0})
        spawn = (spawn_raw["x"], spawn_raw["y"]) if isinstance(spawn_raw, dict) else tuple(spawn_raw)
        image_path = assets_dir / f"{map_id}.png"
        if not image_path.exists():
            image_path = None
        return MapData(
            map_id=map_id,
            width=width,
            height=height,
            tile_width=tile_width,
            tile_height=tile_height,
            collision=collision,
            spawn=spawn,
            image_path=image_path,
            display_name=data.get("name", map_id),
        )

    # Tiled format. The real map size equals the spread of placed assets:
    # remap every layer to the bounding box of non-empty tiles so the canvas
    # padding (empty cells) is not treated as part of the map.
    width = data.get("width", 10)
    height = data.get("height", 10)
    tile_width = data.get("tilewidth", 32)
    tile_height = data.get("tileheight", 32)
    tile_layers = _layers_from_tiled(data)
    bbox = _bbox_from_layers(tile_layers)
    ox = oy = 0
    if bbox is not None:
        ox, oy, mx, my = bbox
        width = mx - ox + 1
        height = my - oy + 1
        tile_layers = _remap_layers(tile_layers, ox, oy, width, height)
    collision = _collision_from_layers(tile_layers, width, height)
    tileset = _resolve_tileset(data.get("tilesets"), assets_dir)
    spawn_raw = data.get("spawn")
    if isinstance(spawn_raw, dict):
        sx = spawn_raw.get("x", 0)
        sy = spawn_raw.get("y", 0)
        spawn = (sx - ox, sy - oy) if bbox is not None else (sx, sy)
    else:
        spawn = _first_walkable(collision)
    if not _walkable_at(collision, spawn[0], spawn[1]):
        spawn = _first_walkable(collision)
    image_path = assets_dir / f"{map_id}.png"
    if not image_path.exists():
        image_path = None
    return MapData(
        map_id=map_id,
        width=width,
        height=height,
        tile_width=tile_width,
        tile_height=tile_height,
        collision=collision,
        spawn=spawn,
        image_path=image_path,
        tileset=tileset,
        tile_layers=tile_layers,
        display_name=data.get("name", map_id),
    )
