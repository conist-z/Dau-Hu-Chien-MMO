"""Convert Ekonia (godot-tiny-mmo) .tscn TileMapLayer maps to Tiled JSON.

Strategy: BAKE every painted tile the way Godot's TileMapLayer::draw_tile()
renders it:
    dest = cell_center - tex_block/2 - texture_origin      (origin in PIXELS)
The texture block (size_in_atlas cells, transposed/flipped per the
alternative_tile bits) is sliced on the global 16px grid; every unique 16px
slice becomes a sequential tile in ONE shared baked tileset. This reproduces
multi-cell props (trees/cliffs with texture_origin offsets) pixel-perfectly.

Output: assets/maps/ekonia/<name>.json (+ tiles/ekonia_baked.png,
*.solids.json collision gid lists). Standard Tiled JSON.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import struct
import sys

from PIL import Image

ROOT = (r"D:/D- RE du an build game mmo discord cho server rainbow cat/"
        r"ekonia-(copy)")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "maps", "ekonia")
TS = 16  # Ekonia tile size

# Godot TileSetAtlasSource alternative_tile transform bits
FLIP_H, FLIP_V, TRANSPOSE = 0x1000, 0x2000, 0x4000

BAKED_SHEET = "ekonia_baked.png"
BAKED_COLS = 64  # tiles per row in the baked sheet


def load_tres(path: str) -> list[dict]:
    """Parse a TileSet .tres into atlas descriptors:
    [{tex, tiles:{(x,y)}, sizes:{(x,y):(w,h)}, origins:{(x,y):(px,py)},
      solids:{(x,y)}}...] indexed by Godot source id (sources/N)."""
    txt = open(path, encoding="utf-8", errors="ignore").read()
    id2path = {m.group(2): m.group(1) for m in re.finditer(
        r'\[ext_resource type="Texture2D"[^\]]*path="res://([^"]+)"'
        r'[^\]]*id="([^"]+)"\]', txt)}
    by_id: dict[str, dict] = {}
    for block in re.split(r'(?=\[sub_resource type="TileSetAtlasSource" )',
                          txt)[1:]:
        idm = re.match(r'\[sub_resource type="TileSetAtlasSource" id="([^"]+)"\]',
                       block)
        if not idm:
            continue
        texm = re.search(r'texture = ExtResource\("([^"]+)"\)', block)
        if not texm:
            continue
        texpath = id2path.get(texm.group(1))
        if not texpath:
            continue
        tiles, sizes, origins, solids = set(), {}, {}, set()
        for m in re.finditer(r"^(\d+):(\d+)/0 = 0", block, re.M):
            tiles.add((int(m.group(1)), int(m.group(2))))
        for m in re.finditer(r"^(\d+):(\d+)/0/physics", block, re.M):
            solids.add((int(m.group(1)), int(m.group(2))))
        for m in re.finditer(
                r"^(\d+):(\d+)/size_in_atlas = Vector2i\((\d+), (\d+)\)",
                block, re.M):
            sizes[(int(m.group(1)), int(m.group(2)))] = (
                int(m.group(3)), int(m.group(4)))
        for m in re.finditer(
                r"^(\d+):(\d+)/0/texture_origin = Vector2i\((-?\d+), (-?\d+)\)",
                block, re.M):
            origins[(int(m.group(1)), int(m.group(2)))] = (
                int(m.group(3)), int(m.group(4)))
        by_id[idm.group(1)] = {"tex": texpath, "tiles": tiles, "sizes": sizes,
                               "origins": origins, "solids": solids}
    # TileSet resource body maps source index -> sub_resource id
    atlases: list[dict | None] = []
    body = txt.split("[resource]", 1)[-1]
    for m in re.finditer(r'^sources/(\d+) = SubResource\("([^"]+)"\)', body,
                         re.M):
        idx = int(m.group(1))
        while len(atlases) <= idx:
            atlases.append(None)
        atlases[idx] = by_id.get(m.group(2))
    # Keep original Godot source indices (gaps possible, e.g. sources 0,1,3):
    # map cells reference sid directly, so None placeholders must stay.
    return atlases


class Baker:
    """Shared per-run baked tileset: unique 16px slices -> sequential gids."""

    def __init__(self) -> None:
        self.textures: dict[str, dict] = {}   # rel path -> {img,...}
        self.piece_index: dict[bytes, int] = {}
        self.piece_imgs: list[Image.Image] = []  # gid-1
        self.solid_gids: set[int] = set()
        self._solid_cells: set[tuple[int, int]] = set()  # absolute solid cells
        self._pending: list[Image.Image] = []          # pieces awaiting gids
        self._gid_by_piece: dict[int, int] = {}        # id(piece) -> gid

    def atlas(self, rel_tex: str) -> dict:
        t = self.textures.get(rel_tex)
        if t is None:
            img = Image.open(os.path.join(ROOT, rel_tex)).convert("RGBA")
            t = {"img": img}
            self.textures[rel_tex] = t
        return t

    def _piece_gid(self, piece: Image.Image) -> int:
        key = piece.tobytes()
        gid = self.piece_index.get(key)
        if gid is None:
            self.piece_imgs.append(piece)
            gid = len(self.piece_imgs)
            self.piece_index[key] = gid
        return gid

    def bake(self, rel_tex: str, ax: int, ay: int, alt: int,
             out: dict, sizes: dict | None = None,
             origins: dict | None = None, gox: int = 0,
             goy: int = 0) -> None:
        """Reproduce draw_tile() for one painted cell; write 16px cells into
        ``out`` {(gx,gy): gid} (grid coords relative to the anchor cell).
        ``sizes``/``origins`` come from the tileset atlas (per-tile
        size_in_atlas cells and texture_origin pixels). ``gox``/``goy`` are
        the LAYER's pixel offset: slices land on the sub-grid anchored there
        so non-tile-aligned layer positions (Roof pos 0,43) keep their exact
        pixel placement inside each 16px piece."""
        img = self.atlas(rel_tex)["img"]
        bw, bh = (sizes or {}).get((ax, ay), (1, 1))
        ox, oy = (origins or {}).get((ax, ay), (0, 0))
        block = img.crop((ax * TS, ay * TS, (ax + bw) * TS, (ay + bh) * TS))
        flip_h = bool(alt & FLIP_H)
        flip_v = bool(alt & FLIP_V)
        if alt & TRANSPOSE:
            block = block.transpose(Image.TRANSPOSE)
            bw, bh = bh, bw
        if flip_h:
            block = block.transpose(Image.FLIP_LEFT_RIGHT)
        if flip_v:
            block = block.transpose(Image.FLIP_TOP_BOTTOM)
        # dest top-left relative to the anchor cell's top-left (pixels),
        # expressed in the LAYER's sub-grid frame (origin shifted by gox/goy
        # residual pixels inside a tile)
        rx, ry = ((gox % TS) + TS) % TS, ((goy % TS) + TS) % TS
        dx = TS // 2 - (bw * TS) // 2 - ox + rx
        dy = TS // 2 - (bh * TS) // 2 - oy + ry
        w, h = block.size
        gx0 = math.floor(dx / TS)
        gy0 = math.floor(dy / TS)
        gx1 = math.floor((dx + w - 1) / TS)
        gy1 = math.floor((dy + h - 1) / TS)
        for gy in range(gy0, gy1 + 1):
            for gx in range(gx0, gx1 + 1):
                sx0, sx1 = max(dx, gx * TS), min(dx + w, (gx + 1) * TS)
                sy0, sy1 = max(dy, gy * TS), min(dy + h, (gy + 1) * TS)
                if sx0 >= sx1 or sy0 >= sy1:
                    continue
                piece = Image.new("RGBA", (TS, TS), (0, 0, 0, 0))
                piece.paste(block.crop((sx0 - dx, sy0 - dy,
                                        sx1 - dx, sy1 - dy)),
                            (sx0 - gx * TS, sy0 - gy * TS))
                if piece.getchannel("A").getextrema()[1] == 0:
                    continue  # fully transparent slice never covers art
                self._pending.append(piece)
                out[(gx, gy)] = piece

    def bake_sprite(self, rel_tex: str, rx: float, ry: float, rw: float,
                    rh: float, center_px: int, bottom_px: int,
                    out: dict) -> None:
        """Bake a Sprite2D-style object (texture region centered on
        center_px horizontally, bottom edge at bottom_px — Godot sprites sit
        on their origin unless offset) into grid cells keyed relative to
        ``out``'s absolute grid coords."""
        t = self.atlas(rel_tex)
        img = t["img"]
        x0, y0 = int(round(rx)), int(round(ry))
        x1 = min(img.width, int(round(rx + rw)))
        y1 = min(img.height, int(round(ry + rh)))
        if x0 >= x1 or y0 >= y1:
            return
        block = img.crop((x0, y0, x1, y1))
        dw, dh = block.size
        dest_x = center_px - dw // 2
        dest_y = bottom_px - dh
        gx0 = math.floor(dest_x / TS)
        gy0 = math.floor(dest_y / TS)
        gx1 = math.floor((dest_x + dw - 1) / TS)
        gy1 = math.floor((dest_y + dh - 1) / TS)
        for gy in range(gy0, gy1 + 1):
            for gx in range(gx0, gx1 + 1):
                sx0, sx1 = max(dest_x, gx * TS), min(dest_x + dw,
                                                      (gx + 1) * TS)
                sy0, sy1 = max(dest_y, gy * TS), min(dest_y + dh,
                                                      (gy + 1) * TS)
                if sx0 >= sx1 or sy0 >= sy1:
                    continue
                piece = Image.new("RGBA", (TS, TS), (0, 0, 0, 0))
                piece.paste(block.crop((sx0 - dest_x, sy0 - dest_y,
                                        sx1 - dest_x, sy1 - dest_y)),
                            (sx0 - gx * TS, sy0 - gy * TS))
                if piece.getchannel("A").getextrema()[1] == 0:
                    continue
                self._pending.append(piece)
                out[(gx, gy)] = piece

    def gid_of(self, piece: Image.Image) -> int:
        gid = self._gid_by_piece.get(id(piece))
        if gid is None:
            gid = self._piece_gid(piece)
            self._gid_by_piece[id(piece)] = gid
        return gid

    def finalize(self) -> list[dict]:
        """Write the baked sheet (gids already assigned lazily via gid_of);
        return the Tiled tilesets list."""
        sheet = Image.new("RGBA", (BAKED_COLS * TS, 1), (0, 0, 0, 0))
        rows = (len(self.piece_imgs) + BAKED_COLS - 1) // BAKED_COLS
        sheet = Image.new("RGBA", (BAKED_COLS * TS, max(1, rows) * TS),
                          (0, 0, 0, 0))
        for i, piece in enumerate(self.piece_imgs):
            sheet.paste(piece, ((i % BAKED_COLS) * TS,
                                (i // BAKED_COLS) * TS))
        os.makedirs(os.path.join(OUT, "tiles"), exist_ok=True)
        sheet.save(os.path.join(OUT, "tiles", BAKED_SHEET))
        return [{
            "firstgid": 1, "name": BAKED_SHEET,
            "image": "tiles/" + BAKED_SHEET,
            "imagewidth": sheet.width, "imageheight": sheet.height,
            "tilewidth": TS, "tileheight": TS,
            "margin": 0, "spacing": 0,
            "tilecount": len(self.piece_imgs), "columns": BAKED_COLS,
        }]


BAKER = Baker()
_TRES_CACHE: dict[str, list[dict]] = {}


def _blend(old, piece: Image.Image) -> Image.Image:
    """Alpha-composite a 16px piece over an existing one — mirrors how
    Godot paints overlapping tiles on one layer (roof ridge over roof fill).
    ``old`` may be a PIL piece (cell already painted) or None."""
    if old is None:
        return piece
    old.alpha_composite(piece)
    return old

# Ore/prop sprites: region rects read verbatim from the vein .tres files
# (mineable_nodes/*.tres AtlasTexture definitions).
ORE_SPRITES = {
    "coal_vein": ("assets/sprites/environment/props/rocks.png",
                  (0, 16, 32, 48)),
    "iron_vein": ("assets/sprites/environment/props/rocks.png",
                  (97.57414, 18.388435, 30.283089, 44.766308)),
    "copper_vein": ("assets/sprites/environment/props/rocks.png",
                    (0.9250717, 112.912254, 31.33641, 47.399605)),
    "healing_herb": ("assets/sprites/environment/green_woods/props.png",
                     (0, 32, 32, 32)),
}


BUILDING_SCENES = ("house.tscn", "house_1.tscn", "outside_building.tscn",
                   "goblin_shaman_house.tscn", "mining_vending.tscn",
                   "castle.tscn")


def expand_instance(scene_path: str, node_name: str, px: int, py: int,
                    out_layers: list) -> None:
    """Expand one instanced PackedScene at pixel (px, py):
    - nested building scenes -> bake their TileMapLayers
    - mineable_node -> bake the ore/herb sprite
    - gameplay markers (npc/warper/spawn...) -> nothing visual."""
    base = os.path.basename(scene_path)
    if base == "mineable_node.tscn":
        low = node_name.lower()
        kind = ("healing_herb" if "herb" in low else
                "coal_vein" if "coal" in low else
                "iron_vein" if "iron" in low else
                "copper_vein" if "copper" in low else None)
        if kind:
            tex, (rx, ry, rw, rh) = ORE_SPRITES[kind]
            lay = next((l for l in out_layers if l["name"] == "Objects"),
                       None)
            if lay is None:
                lay = {"name": "Objects", "cells": {}}
                out_layers.append(lay)
            grid: dict = {}
            BAKER.bake_sprite(tex, rx, ry, rw, rh, px, py + 24, grid)
            for (gx, gy), piece in grid.items():
                lay["cells"][(gx, gy)] = _blend(lay["cells"].get((gx, gy)),
                                                piece)
        return
    if base in BUILDING_SCENES:
        expand_nested_tscn(os.path.join(ROOT, scene_path), px, py, out_layers)


def expand_nested_tscn(tscn_path: str, px: int, py: int,
                       out_layers: list) -> None:
    """Recursively bake a nested building .tscn (its TileMapLayers) at pixel
    offset (px, py). Godot position is the node's origin; tilemap local cell
    (x,y) center maps to (px + (x+0.5)*16, py + (y+0.5)*16)."""
    txt = open(tscn_path, encoding="utf-8", errors="ignore").read()
    extid2tres = {eid: p for p, eid in re.findall(
        r'\[ext_resource type="TileSet"[^\]]*path="res://([^"]+)"'
        r'[^\]]*id="([^"]+)"\]', txt)}
    for part in re.split(r"(?=\[node )", txt):
        m = re.match(r'\[node name="([^"]+)" type="TileMapLayer"', part)
        if not m:
            continue
        ts = re.search(r'tile_set = ExtResource\("([^"]+)"\)', part)
        if not ts or ts.group(1) not in extid2tres:
            continue
        dm = re.search(r'tile_map_data = PackedByteArray\("([^" ]+)"\)', part)
        if not dm:
            continue
        atl = tres_for(extid2tres[ts.group(1)])
        # layer-local pixel offset (e.g. Roof pos 0,43); also accept a
        # transform/origin form some scenes use instead of `position`
        posm = re.search(r'position = Vector2\((-?[\d.]+), (-?[\d.]+)\)', part)
        if not posm:
            posm = re.search(
                r'transform = Transform2D\([\d.-]+, [\d.-]+, [\d.-]+, '
                r'[\d.-]+, (-?[\d.]+), (-?[\d.]+)\)', part)
        lpx = int(round(float(posm.group(1)))) if posm else 0
        lpy = int(round(float(posm.group(2)))) if posm else 0
        data = base64.b64decode(dm.group(1))
        off = 2
        painted = []
        while off + 12 <= len(data):
            x, y, sid, ax, ay, alt = struct.unpack_from("<hhhhhh", data, off)
            off += 12
            if sid >= len(atl) or atl[sid] is None:
                continue
            at = atl[sid]
            if (ax, ay) not in at["tiles"]:
                continue
            painted.append((x, y, at, ax, ay, alt))
        lay = next((l for l in out_layers if l["name"] == m.group(1)), None)
        if lay is None:
            lay = {"name": m.group(1), "cells": {}}
            out_layers.append(lay)
        for x, y, at, ax, ay, alt in painted:
            grid: dict = {}
            # full pixel offset (instance + nested layer) so the slice
            # sub-grid anchors exactly where Godot paints this block
            BAKER.bake(at["tex"], ax, ay, alt, grid,
                       at["sizes"], at["origins"], px + lpx, py + lpy)
            for (gx, gy), piece in grid.items():
                # local cell -> absolute pixel -> absolute grid
                axp = px + lpx + x * TS + gx * TS
                ayp = py + lpy + y * TS + gy * TS
                key = (math.floor(axp / TS), math.floor(ayp / TS))
                lay["cells"][key] = _blend(lay["cells"].get(key), piece)


def tres_for(path: str) -> list[dict]:
    if path not in _TRES_CACHE:
        _TRES_CACHE[path] = load_tres(os.path.join(ROOT, path))
    return _TRES_CACHE[path]


def convert_map(tscn_rel: str, name: str) -> tuple[str, dict] | None:
    path = os.path.join(ROOT, tscn_rel)
    txt = open(path, encoding="utf-8", errors="ignore").read()

    extid2tres = {eid: p for p, eid in re.findall(
        r'\[ext_resource type="TileSet"[^\]]*path="res://([^"]+)"'
        r'[^\]]*id="([^"]+)"\]', txt)}
    layers = []
    # parent positions: TileMapLayers nested under Area2D/Node2D nodes
    # (e.g. Questboard under QuestBoard Area2D) inherit the parent's offset.
    parent_pos: dict[str, tuple[int, int]] = {}
    for part in re.split(r"(?=\[node )", txt):
        m = re.match(r'\[node name="([^"]+)" type="(?:Area2D|Node2D)"'
                     r'(?: parent="([^"]*)")?\]', part)
        if m:
            pm = re.search(r'position = Vector2\((-?[\d.]+), (-?[\d.]+)\)',
                           part)
            if pm:
                parent_pos[m.group(1)] = (int(round(float(pm.group(1)))),
                                          int(round(float(pm.group(2)))))
    for part in re.split(r"(?=\[node )", txt):
        m = re.match(r'\[node name="([^"]+)" type="TileMapLayer"', part)
        if not m:
            continue
        tset = re.search(r'tile_set = ExtResource\("([^"]+)"\)', part)
        tres = extid2tres.get(tset.group(1)) if tset else None
        if not tres:
            continue
        datam = re.search(
            r'tile_map_data = PackedByteArray\("([^" ]+)"\)', part)
        if not datam:
            continue
        atlases = tres_for(tres)
        # layer-local pixel offset (forest Ground pos 1,0 etc.); also accept
        # a transform/origin form some scenes use instead of `position`
        posm = re.search(r'position = Vector2\((-?[\d.]+), (-?[\d.]+)\)', part)
        if not posm:
            posm = re.search(
                r'transform = Transform2D\([\d.-]+, [\d.-]+, [\d.-]+, '
                r'[\d.-]+, (-?[\d.]+), (-?[\d.]+)\)', part)
        lpx = int(round(float(posm.group(1)))) if posm else 0
        lpy = int(round(float(posm.group(2)))) if posm else 0
        # add inherited offset from a non-tilemap parent node
        pm = re.search(r'parent="([^"]+)"',
                       part[:part.find("]") + 1] if "]" in part else part)
        if pm and pm.group(1) in parent_pos:
            gx, gy = parent_pos[pm.group(1)]
            lpx += gx
            lpy += gy
        data = base64.b64decode(datam.group(1))
        cells: dict = {}
        painted = []
        off = 2
        while off + 12 <= len(data):
            x, y, sid, ax, ay, alt = struct.unpack_from("<hhhhhh", data, off)
            off += 12
            if sid >= len(atlases) or atlases[sid] is None:
                continue
            at = atlases[sid]
            if (ax, ay) not in at["tiles"]:
                continue
            painted.append((x, y, sid, ax, ay, alt))
        # deterministic order: bottom-up so upper tiles overwrite
        for x, y, sid, ax, ay, alt in sorted(painted, key=lambda c: (c[1],
                                                                     c[0])):
            at = atlases[sid]
            grid: dict = {}
            # bake slices on the layer's own sub-grid (lpx/lpy residual
            # pixels land inside each 16px piece, pixel-perfect placement)
            # bake slices on the layer's own sub-grid (lpx/lpy residual
            # pixels land inside each 16px piece, pixel-perfect placement)
            BAKER.bake(at["tex"], ax, ay, alt, grid,
                       at["sizes"], at["origins"], lpx, lpy)
            for (gx, gy), piece in grid.items():
                key = (x + math.floor(lpx / TS) + gx,
                       y + math.floor(lpy / TS) + gy)
                cells[key] = _blend(cells.get(key), piece)
            if (ax, ay) in at["solids"]:
                BAKER._solid_cells.add(
                    (x + math.floor(lpx / TS), y + math.floor(lpy / TS)))
        if cells:
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            layers.append({"name": m.group(1), "cells": cells,
                           "minx": min(xs), "miny": min(ys),
                           "maxx": max(xs), "maxy": max(ys)})
    # Instanced scenes: nested buildings -> bake their tilemaps; mineable
    # nodes -> bake ore sprites. NPCs/enemies/warpers are gameplay markers.
    extid2scene = {eid: p for p, eid in re.findall(
        r'\[ext_resource type="PackedScene"[^\]]*path="res://([^"]+)"'
        r'[^\]]*id="([^"]+)"\]', txt)}
    for part in re.split(r"(?=\[node )", txt):
        m = re.match(
            r'\[node name="([^"]+)"[^\]]*instance=ExtResource\("([^"]+)"\)',
            part)
        if not m:
            continue
        scene = extid2scene.get(m.group(2))
        if not scene:
            continue
        posm = re.search(
            r'position = Vector2\((-?[\d.]+), (-?[\d.]+)\)', part)
        if not posm:
            continue
        expand_instance(scene, m.group(1), int(round(float(posm.group(1)))),
                        int(round(float(posm.group(2)))), layers)
    for l in layers:
        l["gids"] = {k: BAKER.gid_of(v) for k, v in l["cells"].items()}
        xs = [c[0] for c in l["gids"]]
        ys = [c[1] for c in l["gids"]]
        l["minx"], l["miny"] = min(xs), min(ys)
        l["maxx"], l["maxy"] = max(xs), max(ys)
    if not layers:
        print(f"  !! no filled layers in {name}, skipped")
        return None
    parent = os.path.basename(os.path.dirname(tscn_rel))
    fname = name if name not in ("inside_map", "outside_building",
                                 "house", "house_1") else f"{parent}_{name}"
    minx = min(l["minx"] for l in layers)
    miny = min(l["miny"] for l in layers)
    w = max(l["maxx"] for l in layers) - minx + 1
    h = max(l["maxy"] for l in layers) - miny + 1
    tjson = {
        "compressionlevel": -1, "height": h, "width": w,
        "infinite": False, "orientation": "orthogonal",
        "renderorder": "right-down", "tiledversion": "1.10.2",
        "type": "map", "tileheight": TS, "tilewidth": TS,
        "nextlayerid": len(layers) + 1, "nextobjectid": 1,
        "layers": [
            {
                "data": [
                    l["gids"].get((x + minx, y + miny), 0)
                    for y in range(h) for x in range(w)
                ],
                "height": h, "width": w, "id": i + 1,
                "name": l["name"], "opacity": 1,
                "type": "tilelayer", "visible": True, "x": 0, "y": 0,
            }
            # Props layers stay in their original scene-tree order (they
            # must draw OVER walls/ground — demoting them made items
            # disappear under opaque floor tiles).
            for i, l in enumerate(layers)
        ],
    }
    # tilesets injected after finalize(); store for main()
    # Return the normalization shift too: solids share the RAW coord system
    # and must be shifted by the SAME (minx, miny) when written out.
    return fname, tjson, (minx, miny)


def main() -> int:
    maps: list[tuple[str, str]] = []
    args = sys.argv[1:]
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT,
                                                      "source/common/gameplay"
                                                      "/maps/maps")):
        for fn in files:
            if fn.endswith(".tscn"):
                rel = os.path.relpath(os.path.join(dirpath, fn),
                                      ROOT).replace("\\", "/")
                nm = fn[:-5]
                parent = os.path.basename(dirpath)
                key = nm if nm not in ("inside_map", "outside_building",
                                       "house", "house_1") \
                    else f"{parent}_{nm}"
                if not args or nm in args or key in args:
                    maps.append((rel, nm))
    print(f"converting {len(maps)} maps -> {OUT}")
    results = []
    for rel, nm in sorted(maps):
        r = convert_map(rel, nm)
        if r:
            results.append(r)
    tilesets = BAKER.finalize()
    print(f"baked tiles: {len(BAKER.piece_imgs)} "
          f"(solid: {len(BAKER.solid_gids)})")
    os.makedirs(OUT, exist_ok=True)
    for fname, tjson, (minx, miny) in results:
        tjson["tilesets"] = tilesets
        with open(os.path.join(OUT, f"{fname}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(tjson, f, separators=(",", ":"))
        solid_json = {
            "map": fname, "tilewidth": TS, "tileheight": TS,
            # Shift by the SAME (minx, miny) as the Tiled layers: _solid_cells
            # stores RAW absolute coords while the JSON grid is normalized to
            # (0,0). Unshifted solids landed (34,54) tiles off on forest —
            # blocking empty ground while trees/núi stayed walkable.
            "solid_cells": sorted(
                (x - minx, y - miny) for (x, y) in BAKER._solid_cells
            ),
        }
        with open(os.path.join(OUT, f"{fname}.solids.json"), "w") as f:
            json.dump(solid_json, f)
        print(f"  OK {fname}: {tjson['width']}x{tjson['height']}, "
              f"layers={[l['name'] for l in tjson['layers']]}")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
