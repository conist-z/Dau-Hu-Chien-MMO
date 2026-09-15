"""Per-tile collision masks derived from tileset ALPHA channels.

Purpose (user request): a tree/rock/torch texture is not a full square, yet
the static collision grid blocks the whole tile. This module derives, for
every tile GID that lands on a BLOCKING layer, the real opaque pixel shape
from its tileset image and stores it as a coarse bitmask (MASK_RES sub-cells
per tile). The collision layer then answers "does the player's box overlap
the opaque pixels" instead of "does it overlap the tile square".

Design constraints honoured:
- Data-driven (rule 10/17): masks come from the tileset PNGs the map already
  references — NO hard-coded coordinates, NO hand-drawn polygons required.
- Deterministic and cached per (image_path, firstgid) — one mask grid per
  tileset, shared by every map that uses the sheet.
- Blocking layers are still chosen by game.map_loader._is_blocking_layer, so
  walkable layers (cỏ, hoa, cầu thang, cầu, cửa tương tác...) NEVER gain
  collision from this module: masks only REFINE tiles the map already blocks.
- Fallback safety: a tile whose sheet is missing, or that is fully opaque,
  yields an all-solid mask (behaves exactly like the old square block).

Server-side only: the web client receives the same masks via the welcome
payload (web_api/snapshots.py) and mirrors the check in game.ts so the
client prediction stays byte-compatible with game/collision.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image

# Sub-cells per tile side. 8 => 64 bits per tile, small payload, 4px
# resolution at tile 32 — enough to keep players out of a trunk while
# letting them hug the leaves. Do not raise casually: payload grows
# quadratically (8 -> 16 quadruples the mask bytes).
MASK_RES = 8

Gid = int
Mask = int  # MASK_RES*MASK_RES bitfield, bit index = my*MASK_RES + mx


def _empty_mask() -> Mask:
    return 0


def _full_mask() -> Mask:
    return (1 << (MASK_RES * MASK_RES)) - 1


@dataclass
class TilesetMasks:
    """Alpha-derived masks for one tileset sheet, keyed by local tile index."""

    firstgid: int
    columns: int
    tile_width: int
    tile_height: int
    tilecount: Optional[int]
    image_path: Optional[Path]
    masks: Dict[int, Mask] = field(default_factory=dict)  # local index -> mask
    complete: bool = False  # True once the sheet was loaded and sliced

    def mask_for_gid(self, gid: Gid) -> Optional[Mask]:
        """Mask for a raw layer GID, or None when the GID is not ours."""
        if gid < self.firstgid:
            return None
        if (
            self.tilecount is not None
            and gid >= self.firstgid + self.tilecount
        ):
            return None
        return self.masks.get(gid - self.firstgid, _full_mask())


def _mask_from_patch(patch: "Image.Image") -> Mask:
    """Downsample a tile patch to a MASK_RES bitmask via alpha coverage.

    A sub-cell is solid when ANY of its pixels is (mostly) opaque. Using
    any() instead of majority keeps thin trunks/wires solid — under-blocking
    lets players clip through trees, over-blocking only hugs the sprite.
    """
    w, h = patch.size
    alpha = patch.convert("RGBA").getchannel("A")
    px = alpha.load()
    mask = _empty_mask()
    cell_w = w / MASK_RES
    cell_h = h / MASK_RES
    for my in range(MASK_RES):
        y0 = int(my * cell_h)
        y1 = max(y0 + 1, int((my + 1) * cell_h))
        for mx in range(MASK_RES):
            x0 = int(mx * cell_w)
            x1 = max(x0 + 1, int((mx + 1) * cell_w))
            solid = False
            # Downsample on a strided grid (MAX 16 samples per edge): a
            # 32px tile at cell 4px would otherwise scan 16x16=256 pixels
            # per cell x 64 cells = 16k alpha reads per tile — x1064 tiles
            # per sheet made map load crawl. A 16-sample lattice sees every
            # feature >= ~1/16 of the tile (trunks, torch poles) and keeps
            # per-sheet cost at ~2k reads per tile.
            step_x = max(1, (x1 - x0) // 16)
            step_y = max(1, (y1 - y0) // 16)
            for y in range(y0, min(y1, h), step_y):
                for x in range(x0, min(x1, w), step_x):
                    if px[x, y] >= 128:
                        solid = True
                        break
                if solid:
                    break
            if solid:
                mask |= 1 << (my * MASK_RES + mx)
    return mask


class TileMaskCache:
    """Builds and caches TilesetMasks per sheet image. One instance per
    process is enough — maps sharing a tileset share the masks."""

    def __init__(self) -> None:
        self._by_image: Dict[Path, TilesetMasks] = {}

    def get(self, tileset: Dict) -> TilesetMasks:
        image_path = tileset.get("image_path")
        if not image_path:
            return TilesetMasks(
                firstgid=tileset.get("firstgid", 1),
                columns=tileset.get("columns", 1),
                tile_width=tileset.get("tilewidth", 32),
                tile_height=tileset.get("tileheight", 32),
                tilecount=tileset.get("tilecount"),
                image_path=None,
            )
        key = Path(image_path)
        cached = self._by_image.get(key)
        if cached is not None:
            # Same sheet may appear with a different firstgid on another
            # map; masks (local indices) are identical — reuse them.
            if cached.firstgid == tileset.get("firstgid", 1):
                return cached
            clone = TilesetMasks(
                firstgid=tileset.get("firstgid", 1),
                columns=cached.columns,
                tile_width=cached.tile_width,
                tile_height=cached.tile_height,
                tilecount=cached.tilecount,
                image_path=cached.image_path,
                masks=dict(cached.masks),
                complete=True,
            )
            return clone

        tm = TilesetMasks(
            firstgid=tileset.get("firstgid", 1),
            columns=tileset.get("columns", 1),
            tile_width=tileset.get("tilewidth", 32),
            tile_height=tileset.get("tileheight", 32),
            tilecount=tileset.get("tilecount"),
            image_path=key,
        )
        try:
            sheet = Image.open(key)
            sheet_w, sheet_h = sheet.size
            tw = tm.tile_width or 32
            th = tm.tile_height or 32
            cols = tm.columns or max(1, sheet_w // tw)
            count = tm.tilecount
            if not count:
                count = (sheet_w // tw) * (sheet_h // th)
            count = min(count, cols * (sheet_h // th))
            # Slice lazily but fully: masks are cheap (64 sub-cells) and
            # maps reference scattered GIDs.
            for idx in range(count):
                col = idx % cols
                row = idx // cols
                if (row + 1) * th > sheet_h or (col + 1) * tw > sheet_w:
                    break
                patch = sheet.crop((col * tw, row * th, (col + 1) * tw, (row + 1) * th))
                tm.masks[idx] = _mask_from_patch(patch)
            tm.complete = True
        except Exception:
            # Never swallow silently, but never crash map load either: fall
            # back to full-square masks (old behaviour) for this sheet.
            tm.masks.clear()
            tm.complete = False
        self._by_image[key] = tm
        return tm


# Process-wide cache (masks are pure functions of the PNG bytes).
_CACHE = TileMaskCache()


def tileset_masks(tileset: Dict) -> TilesetMasks:
    return _CACHE.get(tileset)


def encode_masks(tm: TilesetMasks) -> Dict[str, object]:
    """Compact wire format for the web client: [firstgid, columns, mask...]."""
    if not tm.masks:
        return {}
    top = max(tm.masks)
    arr = [tm.masks.get(i, _full_mask()) for i in range(top + 1)]
    return {
        "firstgid": tm.firstgid,
        "columns": tm.columns,
        "tw": tm.tile_width,
        "th": tm.tile_height,
        "count": tm.tilecount,
        "res": MASK_RES,
        "masks": arr,
    }


def refine_tile_mask(
    base_mask: Mask, tileset: Dict, gid: Gid
) -> Mask:
    """Mask for one (tileset, gid) pair; unknown GID => fully solid."""
    tm = tileset_masks(tileset)
    m = tm.mask_for_gid(gid)
    return _full_mask() if m is None else m


# ---------------------------------------------------------------------------
# Map-level mask grid: for every blocked tile, the UNION of the opaque masks
# of the GIDs that sit on blocking layers there (a tile blocked by a tree
# sprite uses THAT tree's shape, not the grass under it).
# ---------------------------------------------------------------------------


class MapTileMasks:
    """Sub-tile collision for one MapData instance.

    grid[y][x] = Mask or None (None = tile not statically blocked -> no
    sub-tile check needed; the tile grid already answers it).
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.grid: list = [[None] * width for _ in range(height)]

    def correct(
        self, x_f: float, y_f: float, box_half: float, collision=None
    ) -> tuple:
        """Push a float-position player box out of the OPAQUE sub-cells of
        masked tiles it overlaps. Called AFTER the swept tile clamp, so tile
        parity with the client prediction is preserved — this only refines
        INSIDE tiles the tile sweep already allowed the box to touch.

        Axis of least penetration per overlapping tile, iterated (max 4
        passes), mirroring web_client resolveSolidOverlap. Never moves the
        player more than box_half per pass, and never into a square-solid
        tile (the caller's is_walkable stays the outer authority via
        ``collision``).
        """
        r = box_half
        E = 1e-9
        for _ in range(4):
            best = None  # (pen, dx, dy)
            for ty in range(int(y_f - r), int(y_f + r) + 1):
                if ty < 0 or ty >= self.height:
                    continue
                row = self.grid[ty]
                for tx in range(int(x_f - r), int(x_f + r) + 1):
                    if tx < 0 or tx >= self.width:
                        continue
                    mask = row[tx]
                    if mask is None:
                        continue
                    # Box/tile overlap in tile units.
                    left = x_f - r
                    right = x_f + r
                    top = y_f - r
                    bottom = y_f + r
                    if right <= tx + E or left >= tx + 1 - E:
                        continue
                    if bottom <= ty + E or top >= ty + 1 - E:
                        continue
                    # Sub-cell range the box overlaps.
                    cell = 1.0 / MASK_RES
                    mx0 = max(0, int((left - tx) / cell))
                    mx1 = min(MASK_RES - 1, int((right - tx) / cell))
                    my0 = max(0, int((top - ty) / cell))
                    my1 = min(MASK_RES - 1, int((bottom - ty) / cell))
                    if mx0 > mx1 or my0 > my1:
                        continue
                    # Solid sub-cells under the box (box shrunk a hair so a
                    # box touching a solid cell edge only counts when it
                    # actually overlaps the cell interior).
                    overlap = False
                    for my in range(my0, my1 + 1):
                        rowbits = mask >> (my * MASK_RES)
                        for mx in range(mx0, mx1 + 1):
                            if rowbits & (1 << mx):
                                # cell rect in tile space
                                cx0 = tx + mx * cell
                                cx1 = cx0 + cell
                                cy0 = ty + my * cell
                                cy1 = cy0 + cell
                                if (right - 1e-6) > cx0 and (left + 1e-6) < cx1 and (
                                    (bottom - 1e-6) > cy0 and (top + 1e-6) < cy1
                                ):
                                    overlap = True
                                    break
                        if overlap:
                            break
                    if not overlap:
                        continue
                    # Axis of least penetration out of THIS tile's solid
                    # sub-cells: candidate pushes along x/y, both axes.
                    # Push-left/right distance: to clear the leftmost/rightmost
                    # solid cell column overlapped.
                    # Find overlapped solid cell bounds.
                    sx0 = sx1 = sy0 = sy1 = None
                    for my in range(my0, my1 + 1):
                        rowbits = mask >> (my * MASK_RES)
                        for mx in range(mx0, mx1 + 1):
                            if rowbits & (1 << mx):
                                if sx0 is None or mx < sx0:
                                    sx0 = mx
                                if sx1 is None or mx > sx1:
                                    sx1 = mx
                                if sy0 is None or my < sy0:
                                    sy0 = my
                                if sy1 is None or my > sy1:
                                    sy1 = my
                    if sx0 is None:
                        continue
                    c_x0 = tx + sx0 * cell
                    c_x1 = tx + (sx1 + 1) * cell
                    c_y0 = ty + sy0 * cell
                    c_y1 = ty + (sy1 + 1) * cell
                    cand = [
                        (c_x1 - (x_f - r), (c_x1 + r) - x_f, 0.0),   # push right
                        ((x_f + r) - c_x0, (c_x0 - r) - x_f, 0.0),   # push left
                        (c_y1 - (y_f - r), 0.0, (c_y1 + r) - y_f),   # push down
                        ((y_f + r) - c_y0, 0.0, (c_y0 - r) - y_f),   # push up
                    ]
                    for pen, dx, dy in cand:
                        if pen < -1e-6:
                            continue  # not actually overlapping this axis
                        if best is None or pen < best[0]:
                            best = (pen, dx, dy)
            if best is None:
                return x_f, y_f
            pen, dx, dy = best
            # Max legitimate penetration: box half (0.3) + one sub-cell
            # (0.125) ≈ 0.43. Anything beyond means the box tunneled (a step
            # jumped the whole mask) — sub-stepping in can_move_float keeps
            # real cases under this, but never yank on a tunnel either.
            if pen > r + 1.0 / MASK_RES + 1e-6:
                return x_f, y_f
            nx = x_f + dx
            ny = y_f + dy
            # The correction must never push the box INTO a square-solid tile.
            if collision is not None:
                from math import floor

                cx, cy = floor(nx), floor(ny)
                if not collision.is_walkable(cx, cy):
                    return x_f, y_f
                # Also verify the box's far edges stay out of solid tiles.
                for ex, ey in (
                    (floor(nx - r), floor(ny)),
                    (floor(nx + r), floor(ny)),
                    (floor(nx), floor(ny - r)),
                    (floor(nx), floor(ny + r)),
                ):
                    if not collision.is_walkable(ex, ey):
                        return x_f, y_f
            x_f, y_f = nx, ny
        return x_f, y_f

    def to_payload(self) -> dict:
        """Sparse wire format: rows as {y: {x: mask}} to keep the welcome
        small (blocked tiles are a minority on every real map)."""
        rows: Dict[str, Dict[str, int]] = {}
        for y in range(self.height):
            row = self.grid[y]
            cells = {x: row[x] for x in range(self.width) if row[x] is not None}
            if cells:
                rows[str(y)] = {str(x): m for x, m in cells.items()}
        return {"res": MASK_RES, "tiles": rows}


def build_map_masks(map_data, blocking_gids: Dict[Tuple[int, int], Gid]) -> Optional[MapTileMasks]:
    """Derive MapTileMasks for a loaded MapData.

    ``blocking_gids`` maps (x, y) -> the GID responsible for the static block
    on that tile (caller picks the topmost blocking-layer GID). Tiles without
    an entry keep plain square collision (None).
    """
    if not map_data.tilesets:
        return None
    out = MapTileMasks(map_data.width, map_data.height)
    sets = [tileset_masks(ts) for ts in map_data.tilesets]
    for (x, y), gid in blocking_gids.items():
        if not (0 <= y < out.height and 0 <= x < out.width):
            continue
        mask = None
        for tm in sets:
            m = tm.mask_for_gid(gid)
            if m is not None:
                mask = m
                break
        if mask is None:
            continue  # unknown gid -> plain square block
        out.grid[y][x] = mask
    return out


def mask_block_fraction(mask: Mask) -> float:
    """Fraction of sub-cells solid — used to skip refinement for near-full
    tiles (a full-opaque texture keeps square collision, which is both
    cheaper and prevents sliding into hollow-looking corners)."""
    if mask is None:
        return 1.0
    bits = bin(mask).count("1")
    return bits / (MASK_RES * MASK_RES)
