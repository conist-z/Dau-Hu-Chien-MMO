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
    # ALL resolved tilesets (multi-sheet maps like lobbytrade). Empty list =
    # single-sheet/simple map; renderers map GIDs by firstgid range.
    tilesets: List[Dict] = field(default_factory=list)
    # Ordered tile layers: (name, grid[y][x] of tile GIDs).
    tile_layers: List[Tuple[str, List[List[int]]]] = field(default_factory=list)
    # Tiles carved walkable by "stairs" layers (mountain staircases); up and
    # down share the same carved path. Empty for maps without stairs.
    stair_walkable: Tuple[Tuple[int, int], ...] = ()
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
    # Tiled JSON is the source format (AGENTS.md); the Godot-wrapped .js form
    # is only a legacy fallback for maps that have not been re-exported yet.
    for ext in (".json", ".js"):
        path = assets_dir / f"{map_id}{ext}"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return _unwrap_godot(text)
    raise FileNotFoundError(f"No map file for {map_id!r}")


def _spawn_from_layers(
    layers: List[Tuple[str, List[List[int]]]],
) -> Optional[tuple]:
    """First tile of a layer whose name contains "spawn" (ASCII-folded).

    The lobbytrade map marks its /khutraodoi arrival tile with a layer named
    "spawn(nơi spanwn player khi họ vào lobby)" — data-driven spawn (rule 10).
    Returns map-space coords BEFORE the bbox origin shift (caller re-shifts).
    """
    for name, grid in layers:
        nl = _normalize_layer_name(name)
        if "spawn" not in nl:
            continue
        for y, row in enumerate(grid):
            for x, gid in enumerate(row):
                if gid:
                    return (x, y)
    return None


def _resolve_tilesets(tilesets, assets_dir: Path) -> List[Dict]:
    """Resolve EVERY Tiled tileset entry (multi-tileset maps like lobbytrade
    use one sheet per art pack). GID -> sheet lookup happens at render time by
    firstgid range."""
    resolved: List[Dict] = []
    for ts in tilesets or []:
        img = ts.get("image")
        if not img:
            continue
        rel = Path(img)
        candidates = [
            assets_dir / rel,
            assets_dir / rel.name,
            assets_dir / "tilesets" / rel.name,
            # Converted maps ship tileset paths relative to the PROJECT root
            # ("assets/tilesets/<name>.png") while ``assets_dir`` is
            # assets/maps — try every ancestor up to the root too.
            *[(base / rel) for base in assets_dir.parents],
        ]
        resolved_path = next((c for c in candidates if c.exists()), None)
        if resolved_path is None:
            # Missing sheet: keep the entry so GID ranges stay correct, but
            # with no image the renderer falls back to gray blocks for it.
            resolved.append(
                {
                    "image_path": None,
                    "firstgid": ts.get("firstgid", 1),
                    "columns": ts.get("columns", 1),
                    "tilewidth": ts.get("tilewidth", 32),
                }
            )
            continue
        resolved.append(
            {
                "image_path": resolved_path,
                "firstgid": ts.get("firstgid", 1),
                "columns": ts.get("columns", 1),
                "tilewidth": ts.get("tilewidth", 32),
            }
        )
    return resolved


def _resolve_tileset(tilesets, assets_dir: Path) -> Optional[Dict]:
    if not tilesets:
        return None
    resolved = _resolve_tilesets(tilesets, assets_dir)
    return resolved[0] if resolved else None


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


def _is_blocking_layer(nl: str) -> bool:
    """Data-driven layer-name match for blocking layers (ASCII-folded).

    - explicit "collision"/"va cham" layers;
    - "tường"/"wall" (the bigmap's hill/mountain blocker);
    - "tảng đá"/"rock" (boulders block movement like walls do);
    - "cây"/"tree" (the lobbytrade forest — tree sprites are solid);
    # NOTE: "cây chết" (bigmap dead trees) stays walkable — the map test pins
    # that behavior and the bigmap was balanced around it.
    - "building" (the lobbytrade house walls);
    - "water"/"nuoc" EXCEPT rain puddles ("vung nuoc" stays walkable).
    """
    if "vung nuoc" in nl:  # bigmap rain puddles must stay walkable
        return False
    if "tham dat" in nl or ("tham" in nl and "ra vao" in nl):
        # "thảm đất nơi cửa ra vào" (the interior arrival mat) is a FLOOR,
        # not a wall — players spawn on it and step off it.
        return False
    if "cua" in nl and "tuong tac" in nl:
        # "cửa ...(tương tác được)" layers are DOORS: portal triggers, not
        # walls (players teleport when stepping on them).
        return False
    return (
        "collision" in nl
        or "va cham" in nl
        or "vacham" in nl
        or "tuong" in nl  # "tường(...)" normalises to "tuong(...)"
        or "wall" in nl
        or "tang đa" in nl  # "tảng đá(...)" ASCII-folds to "tang đa(...)"
        or "rock" in nl
        or "cây" in nl  # "cây(...)" layer (lobbytrade trees)
        # bigmap "cây" folds to "cay" (no diacritics survive NFKD for this
        # word) — the old diacritic-only check let players walk through every
        # bigmap tree. Dead trees ("cây chết") and the bridge ("cây cầu")
        # stay walkable, matching the pinned map-test behavior.
        or ("cay" in nl and "cay chet" not in nl and "cay cau" not in nl)
        or nl.startswith("tree")
        or nl.startswith("building")
        or "nuoc" in nl  # "nước"/"water" (lobbytrade lake)
        or "water" in nl
    )


def _collision_from_layers(
    layers: List[Tuple[str, List[List[int]]]], width: int, height: int
) -> List[List[int]]:
    # UNION of every blocking layer (collision/tường/tảng đá): several layers
    # may each contribute blocked tiles, so they are OR-ed together instead of
    # returning the first match. A map with NO blocking layer stays fully
    # walkable (the old "first layer is solid" heuristic would wall off the
    # whole ground layer — never do that).
    union = [[0 for _ in range(width)] for _ in range(height)]
    found = False
    for name, grid in layers:
        nl = _normalize_layer_name(name)
        if not _is_blocking_layer(nl):
            continue
        found = True
        for y, row in enumerate(grid):
            if y >= height:
                break
            for x, c in enumerate(row):
                if x < width and c != 0:
                    union[y][x] = 1
    return union if found else [[0 for _ in range(width)] for _ in range(height)]


def _normalize_layer_name(name: str) -> str:
    """ASCII-fold a Tiled layer name so diacritic matching is data-driven."""
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", name or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def _walkable_overrides(
    layers: List[Tuple[str, List[List[int]]]], width: int, height: int
) -> List[Tuple[int, int]]:
    """Every walk-through carve: staircases ("cau thang"/"stair") AND
    bridges ("cay cau"/"bridge" — the lobbytrade lake crossing, named
    "cây cầu(đi qua được)"). Each override tile itself is carved; stairs
    additionally carve the tiles straight above/below so rungs are always
    enterable from both sides.
    """
    overrides: List[Tuple[int, int]] = []
    seen = set()
    for name, grid in layers:
        nl = _normalize_layer_name(name)
        is_stair = "cau thang" in nl or "stair" in nl
        is_bridge = "cay cau" in nl or "bridge" in nl
        # Doors ("cửa ...(tương tác được)") must be STEPPABLE: they are
        # portal triggers, not walls (players teleport through them).
        is_door = "cua" in nl and "tuong tac" in nl
        if not is_stair and not is_bridge and not is_door:
            continue
        if is_door:
            # Carve exactly the door tiles (no above/below spread).
            carve_y = False
        else:
            carve_y = not is_bridge
        for y, row in enumerate(grid):
            for x, gid in enumerate(row):
                if not gid:
                    continue
                if is_door:
                    carve = ((x, y),)
                elif is_bridge:
                    carve = ((x, y),)
                else:
                    carve = ((x, y), (x, y - 1), (x, y + 1))
                for tx, ty in carve:
                    if 0 <= tx < width and 0 <= ty < height and (tx, ty) not in seen:
                        seen.add((tx, ty))
                        overrides.append((tx, ty))
    return overrides


def _stairs_walkable_overrides(
    layers: List[Tuple[str, List[List[int]]]], width: int, height: int
) -> List[Tuple[int, int]]:
    """Backwards-compatible alias: stair/bridge walkable carves."""
    return _walkable_overrides(layers, width, height)


def _apply_walkable_overrides(
    collision: List[List[int]], overrides: List[Tuple[int, int]]
) -> None:
    """Carve stair tiles (in place) as walkable through wall layers."""
    for x, y in overrides:
        if 0 <= y < len(collision) and 0 <= x < len(collision[0]):
            collision[y][x] = 0


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
    # Staircase layers carve walkable paths through the mountain walls so the
    # climb works in BOTH directions (up and down the same rungs).
    stair_overrides = _walkable_overrides(tile_layers, width, height)
    _apply_walkable_overrides(collision, stair_overrides)
    tilesets = _resolve_tilesets(data.get("tilesets"), assets_dir)
    tileset = tilesets[0] if tilesets else None
    spawn_raw = data.get("spawn")
    if isinstance(spawn_raw, dict):
        sx = spawn_raw.get("x", 0)
        sy = spawn_raw.get("y", 0)
        spawn = (sx - ox, sy - oy) if bbox is not None else (sx, sy)
    else:
        spawn = None
    if spawn is None:
        # Data-driven spawn layer ("spawn(...)" tiles), bbox-shifted.
        layer_spawn = _spawn_from_layers(tile_layers)
        if layer_spawn is not None:
            spawn = (layer_spawn[0] - ox, layer_spawn[1] - oy)
    if spawn is None:
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
        tilesets=tilesets,
        tile_layers=tile_layers,
        stair_walkable=tuple(stair_overrides),
        display_name=data.get("name", map_id),
    )
