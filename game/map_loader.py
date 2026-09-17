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
    # Per-tile alpha masks (rendering/tile_masks.py): blocked tiles get the
    # real opaque shape of their sprite instead of a full square. None =
    # map has no tileset refinement (square collision everywhere).
    tile_masks: Optional[object] = None
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


def _resolve_tilesets(tilesets, assets_dir: Path,
                      map_dir: Optional[Path] = None) -> List[Dict]:
    """Resolve EVERY Tiled tileset entry (multi-tileset maps like lobbytrade
    use one sheet per art pack). GID -> sheet lookup happens at render time by
    firstgid range. ``map_dir`` is the map file's own subfolder (e.g.
    ekonia/dungeon.json -> ekonia) — Tiled writes tileset paths relative to
    the map JSON, so try that first."""
    resolved: List[Dict] = []
    for ts in tilesets or []:
        img = ts.get("image")
        if not img:
            continue
        # Preserve hand-drawn Tile Collision Editor data: Tiled stores it as
        # ts["tiles"][i]["objectgroup"]["objects"] (rect/ellipse/polygon per
        # tile index). tile_masks rasterizes these into the sub-tile mask,
        # OVERRIDING the alpha-derived shape — manual wins, "cho chắc".
        hand_tiles = ts.get("tiles") if isinstance(ts.get("tiles"), list) else None
        hand_collision = None
        if hand_tiles:
            hand_collision = {
                t.get("id"): t["objectgroup"]
                for t in hand_tiles
                if isinstance(t, dict) and t.get("objectgroup")
            } or None
        rel = Path(img)
        candidates = [
            *([assets_dir / map_dir / rel] if map_dir else []),
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
                    "tilecount": ts.get("tilecount"),
                    "hand_collision": hand_collision,
                }
            )
            continue
        resolved.append(
            {
                "image_path": resolved_path,
                "firstgid": ts.get("firstgid", 1),
                "columns": ts.get("columns", 1),
                "tilewidth": ts.get("tilewidth", 32),
                "tilecount": ts.get("tilecount"),
                "hand_collision": hand_collision,
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
        # "cửa ...(tương tác được)" layers are DOORS: SOLID. The portal is a
        # pressure gate — touching the door tile teleports (travel.py), so
        # the tile itself must never be enterable (user 17/09: "không cho đi
        # xuyên", "1 block chặn hẳn hoi"). Carving the door walkable is what
        # let players walk through non-portal doors (the left monster door).
        return True
    if "khong di xuyen" in nl or "khong di qua duoc" in nl:
        # montertradebase names its furniture/decor layers e.g.
        # "bàn(không đi xuyên được)" / "ghế(không đi xuyên được)" —
        # the author's intent is in the NAME, so honor it data-driven.
        return True
    return (
        "collision" in nl
        or "va cham" in nl
        or "vacham" in nl
        or "tuong" in nl  # "tường(...)" normalises to "tuong(...)"
        or "wall" in nl
        or "tang da" in nl  # "tảng đá(...)" ASCII-folds to "tang da(...)"
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


def _is_wall_like_layer(nl: str) -> bool:
    """True for layers whose tiles must keep FULL SQUARE collision — walls,
    buildings, water AND furniture/decor blockers: near-solid textures where
    a mask hole reads as walking through the object. Decor-ish blockers that
    benefit from pixel-shape refinement are TREES/bushes/rocks/torches on
    tree-ish layers only."""
    return (
        "building" in nl
        or "tuong" in nl
        or "wall" in nl
        or "nuoc" in nl
        or "water" in nl
        or "khong di xuyen" in nl
        # montertradebase furniture: "bàn"/"ghế"/"vật trang trí"
        or "ban(" in nl or "ghe(" in nl
        or ("vat trang tri" in nl and "di xuyen" in nl)
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
    folded = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    # NFKD does NOT decompose "đ" — fold it manually so layer names like
    # "bàn(không đi xuyên được)" match ASCII checks data-driven.
    return folded.replace("đ", "d").replace("Đ", "d").lower()


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
        # Doors are NOT carved: they are solid (portal fires on touch).
        if not is_stair and not is_bridge:
            continue
        carve_y = not is_bridge
        for y, row in enumerate(grid):
            for x, gid in enumerate(row):
                if not gid:
                    continue
                if is_bridge:
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


def _art_mask(
    tile_layers: List[Tuple[str, List[List[int]]]], width: int, height: int
) -> Optional[List[List[bool]]]:
    """True where ANY layer paints a tile — the drawn-art region. Spawn must
    live here, never in the empty void around converted maps."""
    if not tile_layers:
        return None
    mask = [[False] * width for _ in range(height)]
    for _name, grid in tile_layers:
        for y, row in enumerate(grid):
            if y >= height:
                break
            mrow = mask[y]
            for x, gid in enumerate(row):
                if x < width and gid:
                    mrow[x] = True
    return mask


def _centered_walkable(
    collision: List[List[int]],
    has_art: Optional[List[List[bool]]] = None,
) -> tuple:
    """Walkable tile CLOSEST TO CENTER, preferring tiles that carry art.

    The old row-scan picked the FIRST collision==0 tile — on Ekonia maps that
    is row 0's void (outside the drawn art, collision empty) so players
    spawned off the map edge in the black. Center-first inside the art wins;
    falls back to any walkable tile, then (0,0).
    """
    h = len(collision)
    if h == 0:
        return (0, 0)
    w = len(collision[0])
    cx, cy = w / 2, h / 2
    best_any: tuple | None = None
    best_any_d = 1e18
    best_art: tuple | None = None
    best_art_d = 1e18
    for y in range(h):
        row = collision[y]
        art_row = has_art[y] if has_art is not None and y < len(has_art) else None
        for x in range(w):
            if row[x] != 0:
                continue
            d = (x - cx) ** 2 + (y - cy) ** 2
            if d < best_any_d:
                best_any_d = d
                best_any = (x, y)
            if art_row is not None and art_row[x]:
                if d < best_art_d:
                    best_art_d = d
                    best_art = (x, y)
    if best_art is not None:
        return best_art
    if best_any is not None:
        return best_any
    return (0, 0)


def _walkable_at(collision: List[List[int]], x: int, y: int) -> bool:
    if not collision:
        return True
    if y < 0 or y >= len(collision) or x < 0 or x >= len(collision[0]):
        return False
    return collision[y][x] == 0


def _load_solids_cells(assets_dir: Path, map_id: str) -> List[Tuple[int, int]]:
    """Read ``<map>.solids.json`` (Ekonia companion) -> absolute tile coords.

    The file maps a list of [x, y] cells (negative allowed — they mark the
    "black" region outside the drawn art, which IS solid in Ekonia). Missing
    file = no extras (Kaetram/bigmap don't ship one).
    """
    p = assets_dir / f"{map_id}.solids.json"
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        cells = d.get("solid_cells") or []
        return [(int(c[0]), int(c[1])) for c in cells]
    except Exception:
        return []  # malformed solids: never block map load


def load_map(map_id: str, assets_dir: Path) -> MapData:
    data = _read_raw(assets_dir, map_id)
    is_tiled = "layers" in data and "tilesets" in data

    # Ekonia companion solids file (<map>.solids.json): the source game's
    # authoritative per-tile blocking ("các vật thể sẽ có box chạm") — pixel
    # rects converted to tile coords, INCLUDING negative cells outside the
    # drawn art. OR it into the derived collision so author intent wins.
    solids_cells = _load_solids_cells(assets_dir, map_id)

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
    elif solids_cells:
        # A map whose art bbox is smaller than its solids (or vice versa):
        # grow the grid to cover the solids too so no blocking cell is lost.
        sx = [c[0] for c in solids_cells]
        sy = [c[1] for c in solids_cells]
        min_x, min_y = min(0, min(sx)), min(0, min(sy))
        max_x = max(width - 1, max(sx))
        max_y = max(height - 1, max(sy))
        ox, oy = min_x, min_y
        width = max_x - min_x + 1
        height = max_y - min_y + 1
        tile_layers = _remap_layers(tile_layers, ox, oy, width, height)
    collision = _collision_from_layers(tile_layers, width, height)
    # OR in the Ekonia solids (already absolute tile coords): shift by the
    # same bbox origin so grid space matches.
    for cx, cy in solids_cells:
        gx, gy = cx - ox, cy - oy
        if 0 <= gx < width and 0 <= gy < height:
            collision[gy][gx] = 1
    # Sub-tile masks: for every blocked tile, remember the blocking layer's
    # GID (topmost blocking layer wins) so tile_masks can derive the sprite's
    # real opaque shape from the tileset alpha.
    # SCOPE (user feedback): masks refine DECOR sprites only — trees, bushes,
    # boulders, torches... Wall-like layers (building/tường/water/đá xây) stay
    # FULL SQUARE blocks: their textures are (near-)solid, and any mask hole
    # there reads as walking through the wall. Square walls, pixel trees.
    blocking_gids: Dict[Tuple[int, int], int] = {}
    # Tiles that ANY wall-like layer blocks: never refined, even when a tree
    # also overlaps there — the wall body is solid regardless of the tree's
    # transparency (a mask hole in a wall reads as walking through it).
    wall_owned: set = set()
    for name, grid in tile_layers:
        nl = _normalize_layer_name(name)
        if not _is_blocking_layer(nl):
            continue
        if _is_wall_like_layer(nl):
            for gy, row in enumerate(grid):
                if gy >= height:
                    break
                for gx, gid in enumerate(row):
                    if gx < width and gid:
                        wall_owned.add((gx, gy))
            continue
        for gy, row in enumerate(grid):
            if gy >= height:
                break
            for gx, gid in enumerate(row):
                if gx < width and gid:
                    blocking_gids[(gx, gy)] = gid
    for tile in wall_owned:
        blocking_gids.pop(tile, None)
    # Staircase layers carve walkable paths through the mountain walls so the
    # climb works in BOTH directions (up and down the same rungs).
    stair_overrides = _walkable_overrides(tile_layers, width, height)
    _apply_walkable_overrides(collision, stair_overrides)
    # FORAGE CARVE (user 15/09): tiles that host a registered forage node
    # (grass/flower/mushroom art — checked against TILE_NODE_PARTS) must be
    # WALKABLE even though a blocking layer (the lobbytrade tree layer also
    # carries decor grass/flowers) covers them. The node itself decides
    # walkability while alive via Collision.is_walkable (FORAGE_KINDS =
    # walk-through) and fells to walkable — the static grid must not
    # pre-block what the node layer owns. Layer names reuse game.resources
    # RESOURCE_LAYER_NAMES via a local import (no cycle: resources imports
    # state, not map_loader).
    from game.resources import RESOURCE_LAYER_NAMES, TILE_NODE_PARTS, _FORAGE_NODE_KINDS

    for name, grid in tile_layers:
        if (name or "").strip().lower() not in RESOURCE_LAYER_NAMES:
            continue
        for gy, row in enumerate(grid):
            if gy >= height:
                break
            for gx, gid in enumerate(row):
                if not gid or gx >= width:
                    continue
                part = TILE_NODE_PARTS.get(gid)
                if part is not None and part[0] in _FORAGE_NODE_KINDS:
                    collision[gy][gx] = 0
    tilesets = _resolve_tilesets(
        data.get("tilesets"), assets_dir,
        map_dir=Path(map_id).parent if "/" in map_id or "\\" in map_id else None,
    )
    tileset = tilesets[0] if tilesets else None
    # Alpha-derived masks (rendering.tile_masks) — only meaningful once the
    # walkable carves below are applied, so build AFTER overrides: a carved
    # stair tile must not keep a tree mask (it is walkable anyway, but the
    # payload should stay honest).
    tile_masks = None
    try:
        from rendering.tile_masks import build_map_masks, mask_block_fraction, MASK_RES

        full = (1 << (MASK_RES * MASK_RES)) - 1
        md_stub = type("_MD", (), {"width": width, "height": height, "tilesets": tilesets})()
        tile_masks = build_map_masks(md_stub, blocking_gids)
        # Tiles whose sprite is (near-)opaque AND covers the whole cell keep
        # square collision (None) — refining them changes nothing visually
        # but bloats the welcome payload and slows every sweep.
        # Tiles that are fully TRANSPARENT (decor gids with no pixels on a
        # blocking layer) must NOT become walkable: the map author blocked
        # that tile deliberately — keep the square block.
        if tile_masks is not None:
            for y in range(height):
                row = tile_masks.grid[y]
                for x in range(width):
                    m = row[x]
                    if m is None:
                        continue
                    if mask_block_fraction(m) >= 0.99:
                        row[x] = None  # square block is equivalent
                    elif m == 0:
                        row[x] = None  # invisible blocker: keep square
        else:
            tile_masks = None
    except Exception:
        tile_masks = None  # square collision fallback, never block map load
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
        spawn = _centered_walkable(
            collision, _art_mask(tile_layers, width, height)
        )
    if not _walkable_at(collision, spawn[0], spawn[1]):
        spawn = _centered_walkable(
            collision, _art_mask(tile_layers, width, height)
        )
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
        tile_masks=tile_masks,
        stair_walkable=tuple(stair_overrides),
        display_name=data.get("name", map_id),
    )
