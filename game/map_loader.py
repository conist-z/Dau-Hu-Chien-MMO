import json
import logging
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
    # Y-sorted cells (Ekonia/Godot parity): tiles that belong to a sprite
    # part ABOVE its base row (canopy) — drawn OVER actors by the web client.
    # Empty for maps without a y-sorted layer set (Kaetram/bigmap).
    ysort_cells: Tuple[Tuple[int, int], ...] = ()
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


log = logging.getLogger(__name__)


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


def _cell_composite_alpha(
    layers: List[Tuple[str, List[List[int]]]], width: int, height: int
) -> Optional[List[List[int]]]:
    """Per-cell ABOVE-GROUND art alpha from the baked sheet: 1 when any
    NON-GROUND layer's tile at the cell has an opaque pixel (>= 16), else 0.
    Ground/floor layers are EXCLUDED — a cell with only floor art under a
    leaked wide-prop poly is the "invisible wall" ("box chặn tường siêu
    dày"): the player sees open floor. Cells the wall art visually overlaps
    carry that overlap INSIDE the walls layer's own slices, so real wall
    faces never read as ground-only. Returns None when the baked sheet
    cannot be opened (caller skips the carve — safe square fallback).
    """
    sheet_path = Path(__file__).resolve().parents[1] / "assets" / "maps" / "ekonia" / "tiles" / "ekonia_baked.png"
    if not sheet_path.exists():
        return None
    try:
        from PIL import Image

        sheet = Image.open(sheet_path).convert("RGBA")
        alpha_by_gid: Dict[int, int] = {}
        out = [[0] * width for _ in range(height)]
        cols = 64
        for _name, grid in layers:
            _nl = _normalize_layer_name(_name)
            if "ground" in _nl or "floor" in _nl or "san" in _nl:
                continue  # floor art is not a blocker
            for gy, row in enumerate(grid):
                if gy >= height:
                    break
                for gx, gid in enumerate(row):
                    if not gid or gx >= width or out[gy][gx]:
                        continue
                    flag = alpha_by_gid.get(gid)
                    if flag is None:
                        local = gid - 1
                        sx = (local % cols) * 16
                        sy = (local // cols) * 16
                        try:
                            patch = sheet.crop((sx, sy, sx + 16, sy + 16))
                            flag = 1 if patch.getchannel("A").getextrema()[1] >= 16 else 0
                        except Exception:
                            flag = 1  # unreadable gid: assume drawn (safe)
                        alpha_by_gid[gid] = flag
                    out[gy][gx] = flag
        return out
    except Exception:
        return None


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
    return _load_ekonia_cells(assets_dir, map_id)[0]


def _load_ekonia_cells(
        assets_dir: Path, map_id: str,
) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]], List[Tuple[int, int]], list, list, List[Tuple[int, int]]]:
    """``(solid_cells, poly_cells, above_cells, poly_masks, poly_albedo,
    ysort_poly_cells)`` from ``<map>.solids.json``.

    - solid_cells: Godot-authoritative per-tile blocking (OR into collision).
    - poly_cells: cells covered by a tile's physics POLYGON (trunk/L-shape of
      big props). Blocked FULL SQUARE — alpha refinement must never punch
      holes through a trunk ("vật to mà box chặn nhỏ ở giữa" fix).
    - poly_masks: [x, y, mask] triples — the exact 8x8 sub-cell polygon
      shape; web-side tile_masks override so the player box HUGS the shape.
    - poly_albedo: [x, y, mask] triples — the SPRITE silhouette (ground-free
      composited alpha >=128). Wins over poly_masks on the web: the box
      hugs what the player SEES.
    - above_cells: y-sorted cells that belong to a sprite's part ABOVE its
      base row (canopy). The web client bakes these into the OVER-player
      canvas — the "layer lá cây đè lên player" mechanism of the source game.
    """
    p = assets_dir / f"{map_id}.solids.json"
    if not p.exists():
        return [], [], [], [], [], []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))

        def _cells(key: str) -> List[Tuple[int, int]]:
            return [(int(c[0]), int(c[1])) for c in (d.get(key) or [])]

        def _masks(key: str) -> list:
            return [
                (int(c[0]), int(c[1]), int(c[2])) for c in (d.get(key) or [])
            ]

        return (
            _cells("solid_cells"), _cells("poly_cells"),
            _cells("above_cells"), _masks("poly_masks"),
            _masks("poly_albedo"), _cells("ysort_poly_cells"),
        )
    except Exception:
        return [], [], [], [], [], []  # malformed solids: never block map load


def load_map(map_id: str, assets_dir: Path) -> MapData:
    data = _read_raw(assets_dir, map_id)
    is_tiled = "layers" in data and "tilesets" in data

    # Ekonia companion solids file (<map>.solids.json): the source game's
    # authoritative per-tile blocking ("các vật thể sẽ có box chạm") — pixel
    # rects converted to tile coords, INCLUDING negative cells outside the
    # drawn art. OR it into the derived collision so author intent wins.
    (solids_cells, poly_cells, above_cells, poly_masks, poly_albedo,
     ysort_poly_cells) = _load_ekonia_cells(assets_dir, map_id)

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
    elif solids_cells or poly_cells or above_cells:
        # A map whose art bbox is smaller than its solids (or vice versa):
        # grow the grid to cover the solids + above/canopy cells too so no
        # blocking or canopy cell is lost.
        sx = [c[0] for c in solids_cells + poly_cells + above_cells]
        sy = [c[1] for c in solids_cells + poly_cells + above_cells]
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
    # Physics-polygon cells (Godot TileSet sub-tile shapes): FULL SQUARE
    # block, recorded so the alpha refinement below never punches holes
    # through a tree trunk ("box chặn nhỏ hơn vật thể" fix).
    # Web refinement: the converter also emits the exact 8x8 sub-cell
    # polygon masks (poly_masks). These override the tile square on the WEB
    # side only (tile_masks) — the player box HUGS the authored shape (rock
    # edge, L-trunk) instead of the square: "cây thừa viền / đá box quá to"
    # fixed at the data source. Edge-graze cells (poly never reaching the
    # central 4x4) are already trimmed by the converter.
    poly_set: set = set()
    poly_exact_masks: dict = {}
    for cx, cy in poly_cells:
        gx, gy = cx - ox, cy - oy
        if 0 <= gx < width and 0 <= gy < height:
            collision[gy][gx] = 1
            poly_set.add((gx, gy))
    if poly_masks:
        for cx, cy, m in poly_masks:
            gx, gy = cx - ox, cy - oy
            if 0 <= gx < width and 0 <= gy < height and m:
                poly_exact_masks[(gx, gy)] = int(m) & ((1 << 64) - 1)
    # The SPRITE silhouette (ground-free alpha >=128) wins where present —
    # the box must hug what the player SEES; the authored poly (often a
    # full-tile rect) only fills cells the sprite silhouette misses.
    if poly_albedo:
        for cx, cy, m in poly_albedo:
            gx, gy = cx - ox, cy - oy
            if 0 <= gx < width and 0 <= gy < height and m:
                poly_exact_masks[(gx, gy)] = int(m) & ((1 << 64) - 1)
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
    # Physics-polygon cells are FULL SQUARE regardless of alpha: a tree
    # trunk's canopy is transparent at the top of its anchor cell, but the
    # trunk footprint must never gain a walkable hole (Godot polygon parity).
    # EXCEPTION ("box chặn tường dày quá mức"): on Ekonia maps the Godot
    # polys of CAVE CLIFF tiles are authored as FULL-TILE RECTS — square
    # collision there leaves the box poking out of every round corner of
    # the rock blob. Those cells get the same alpha-refinement as the Walls
    # layer below (their baked piece is ground-free, so the alpha silhouette
    # IS the visible shape). Tree/prop polys on other layers keep full
    # square — a trunk must never gain a walkable hole.
    wall_owned.update(poly_set)
    # EKONIA CAVE-WALL REFINEMENT: the converted Ekonia "Walls" layer (cave
    # cliffs) is a rounded rock BLOB — blocking it full-square leaves the
    # box poking out of every round corner ("box chặn tường hơi dày quá
    # mức"). Its baked pieces are ground-free (alpha preserved, ~2000/2.3M
    # partial pixels), so the alpha silhouette of the WALL PIECE ITSELF is a
    # valid refinement source for EVERY wall-owned cell (Walls layer AND
    # cave-cliff poly cells). Hand-drawn collision still wins: those cells
    # are re-added to blocking_gids below and build_map_masks checks the
    # manual shape FIRST (manual > alpha, "cho chắc").
    ekonia_wall_masks: dict = {}
    if Path(map_id).parent.as_posix() == "ekonia":
        _sheet_path = None
        for _ts in (data.get("tilesets") or []):
            _img = _ts.get("image") or ""
            if _img and "ekonia_baked" in Path(_img).name:
                _cand = assets_dir / "ekonia" / _img
                if _cand.exists():
                    _sheet_path = _cand
                    break
        if _sheet_path is not None:
            try:
                from PIL import Image as _PILImage
                from rendering.tile_masks import _mask_from_patch as _mfp

                _sheet = _PILImage.open(_sheet_path).convert("RGBA")
                _sheet_cache: dict[int, object] = {}
                _cols = 64  # BAKED_COLS in scripts/convert_ekonia_maps.py
                _tw = int(tile_width or 16)

                def _piece_mask(_gid: int):
                    _local = _gid - 1  # firstgid 1
                    _m = _sheet_cache.get(_local)
                    if _m is None:
                        _sx = (_local % _cols) * _tw
                        _sy = (_local // _cols) * _tw
                        try:
                            _patch = _sheet.crop(
                                (_sx, _sy, _sx + _tw, _sy + _tw)
                            )
                        except Exception:
                            return None
                        _m = _mfp(_patch)
                        _sheet_cache[_local] = _m or 1  # cache empties too
                    return None if _m == 1 else _m

                # Source gids for EVERY owned cell: the Walls layer's own
                # tile, else the poly-bearing sprite composited at that cell
                # (poly_albedo stores the same cells with their pieces).
                for _key in sorted(wall_owned):
                    _gid = None
                    for _name, _grid in tile_layers:
                        if _normalize_layer_name(_name) != "walls":
                            continue
                        _gx, _gy = _key
                        if (
                            0 <= _gy < len(_grid)
                            and 0 <= _gx < width
                            and _grid[_gy][_gx]
                        ):
                            _gid = _grid[_gy][_gx]
                            break
                    if _gid is None and _key in poly_set:
                        # Poly cell without a Walls tile: find the prop
                        # sprite on ANY blocking (non-wall-like) layer here.
                        for _name, _grid in tile_layers:
                            _nl = _normalize_layer_name(_name)
                            if not _is_blocking_layer(_nl) or _is_wall_like_layer(_nl):
                                continue
                            _gx, _gy = _key
                            if (
                                0 <= _gy < len(_grid)
                                and 0 <= _gx < width
                                and _grid[_gy][_gx]
                            ):
                                _gid = _grid[_gy][_gx]
                                break
                    if _gid is None:
                        continue
                    _m = _piece_mask(_gid)
                    if _m is not None:
                        ekonia_wall_masks[_key] = _m
            except Exception:
                ekonia_wall_masks = {}  # square fallback, never block load
    for tile in wall_owned:
        blocking_gids.pop(tile, None)
    # Physics-polygon cells are FULL SQUARE regardless of alpha: a tree
    # trunk's canopy is transparent at the top of its anchor cell, but the
    # trunk footprint must never gain a walkable hole (Godot polygon parity).
    wall_owned.update(poly_set)
    # Canopy set for the OVER-player canvas = y-sorted cells MINUS the
    # trunk/poly cells: the trunk row is the sprite's BASE row — a player
    # standing IN FRONT (south) must draw OVER it (Godot Y-sort: bigger y
    # wins), so it stays in the base bake. Only the art rows ABOVE the base
    # (crown) cover the player walking behind the tree.
    # Poly cells that the converter ALSO y-sorted (tall-sprite trunks) must
    # keep their canopy membership in a walk-through-decor world: a small
    # (<=2x2-cell) trunk sprite has no crown rows, so if we simply dropped
    # it here the sprite would flatten UNDER the player entirely. Re-union
    # them into the canopy set (the web client renders them OVER actors).
    above_cells = [c for c in above_cells if tuple(c) not in poly_set]
    above_cells.extend(ysort_poly_cells)
    # INVISIBLE-BLOCKER CARVE ("box chặn tường siêu dày"): Ekonia cave-wall
    # props ship WIDE physics polys (CaveProps polys up to 32px = 2 cells)
    # whose art is PAINTED UNDER the wall ring — the neighboring rim cells
    # end up SOLID with NO opaque pixel anywhere (ground only). Blocking
    # those reads as a wall 1-2 tiles before any rock: the player stops on
    # open floor. Carve every statically-blocked cell that (a) has NO art
    # pixel (every layer's tile at this cell is fully transparent OR absent)
    # and (b) is not needed as a wall behind wall art. The composite alpha
    # of the cell's tiles is the truth the player SEES — if nothing is drawn
    # there, nothing can block there.
    ekonia = Path(map_id).parent.as_posix() == "ekonia"
    if ekonia:
        _comp_alpha = _cell_composite_alpha(tile_layers, width, height)
        _carved = 0
        # EDGE RING ("quái spawn ra ngoài void"): the carve below opens
        # every no-art cell — including the map's outer rim, which on the
        # cave/forest conversions is the black VOID outside the drawn area.
        # Walkable void let mobs (and the float movement sweep) reach cells
        # with literally nothing drawn. The rim (2 cells) stays solid.
        _EDGE = 2
        for _cy in range(height):
            for _cx in range(width):
                if not collision[_cy][_cx]:
                    continue
                if (
                    _cx < _EDGE or _cy < _EDGE
                    or _cx >= width - _EDGE or _cy >= height - _EDGE
                ):
                    continue  # rim is never carved
                if _comp_alpha is not None and _comp_alpha[_cy][_cx] == 0:
                    collision[_cy][_cx] = 0
                    _carved += 1
        if _carved:
            print(f"  [cave] carved {_carved} invisible-blocker cells (no art)")
        # VOID SEAL: any cell with NO art anywhere on the outer ring is
        # unconditionally solid — conversions leave the sheet edge without
        # wall coverage, and walkable void was reachable from inside.
        _sealed = 0
        for _cy in range(height):
            for _cx in range(width):
                on_rim = (
                    _cx < _EDGE or _cy < _EDGE
                    or _cx >= width - _EDGE or _cy >= height - _EDGE
                )
                if not on_rim:
                    continue
                if _comp_alpha is not None and _comp_alpha[_cy][_cx] == 0:
                    if not collision[_cy][_cx]:
                        _sealed += 1
                    collision[_cy][_cx] = 1
        if _sealed:
            print(f"  [cave] sealed {_sealed} void rim cells (no art)")
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
        # Poly/albedo masks WIN where present (composited silhouette of ALL
        # sprites at the cell — better than one gid's piece); the wall piece
        # alpha fills the GAP (poly cells whose albedo is missing = the
        # "tường/đá box dày quá mức" cells).
        _extras = dict(ekonia_wall_masks)
        _extras.update(poly_exact_masks)
        tile_masks = build_map_masks(
            md_stub, blocking_gids, extra_masks=_extras or None
        )
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
    # Portal trigger tiles ("cửa hang", "miệng hang", doors...) must be
    # IMPASSABLE walls, not walkable floor: the teleport fires on box-touch,
    # and a walkable trigger let the player's box slide through/over the
    # gate art. After carve/mask refinement, force every portal tile solid
    # and strip any sub-tile mask so nothing lets the box pass through.
    try:
        from game.portals import Portals
        portal_cfg = Portals.load(assets_dir / "portals.json")
        mp = portal_cfg.for_map(map_id)
        if mp is not None and mp.trigger_tiles:
            n_forced = 0
            for (px, py) in mp.trigger_tiles:
                if 0 <= px < width and 0 <= py < height:
                    if not collision[py][px]:
                        collision[py][px] = 1
                        n_forced += 1
                    if tile_masks is not None:
                        tile_masks.clear_tile(px, py)
            if n_forced:
                log.info("[portals] %s: forced %d trigger tile(s) solid", map_id, n_forced)
        # ARRIVAL CARVE (inverse of the trigger force): tiles other maps'
        # links ARRIVE on (their ``target``) must be WALKABLE — Ekonia gate
        # art (the triangle arrows of "cửa hang(từ big map vào)") carries a
        # physics poly in <map>.solids.json, which made the arrival tile
        # solid: free_arrival_tile found nothing walkable and fell back to
        # the map spawn ("tele sai vị trí"). The author's portals.json
        # target IS the intended landing spot — honor it over the poly.
        n_freed = 0
        for other in portal_cfg.maps.values():
            for link in other.destinations.values():
                if link.map_id != map_id or link.target_is_layer:
                    continue
                for (ax, ay) in link.target:
                    # A tile that is ALSO this map's own trigger (dual-role
                    # gate: arrive on row 35, walk one more step into row 36
                    # to leave) keeps its forced solidity — freeing it would
                    # let the box pass through the gate art again.
                    if mp.trigger_tiles and (ax, ay) in mp.trigger_tiles:
                        continue
                    if 0 <= ax < width and 0 <= ay < height and collision[ay][ax]:
                        collision[ay][ax] = 0
                        n_freed += 1
                    if tile_masks is not None:
                        tile_masks.clear_tile(ax, ay)
        if n_freed:
            log.info("[portals] %s: freed %d arrival tile(s) walkable", map_id, n_freed)
    except FileNotFoundError:
        pass
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
        # Godot y-sorted canopy cells (bbox-shifted into grid space).
        ysort_cells=tuple(sorted(
            (x - ox, y - oy) for (x, y) in above_cells
            if 0 <= x - ox < width and 0 <= y - oy < height
        )),
        display_name=data.get("name", map_id),
    )
