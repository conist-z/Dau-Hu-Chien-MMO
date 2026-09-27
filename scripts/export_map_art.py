"""Export the web client's map-bake art (the minimap source image) as PNG(s).

The web hub's map page ("map hiển thị" in the right-side hub bar) draws its
minimap from the runtime-baked canvas "map-bake" (game.ts bakeMapIfReady) —
there is no static PNG file for it. This script replays the SAME bake rules
offline (per-layer draw, largest-firstgid-below GID -> tileset resolution,
resource-layer exclusion only where a live server node exists, above-layer
separation) and writes:

    exported_map_art/<map_id>.png        — base bake (what the minimap shows)
    exported_map_art/<map_id>_above.png  — above-player canvas (canopy/roof),
                                           only when the map has such layers

Usage:
    .venv/Scripts/python scripts/export_map_art.py            # all maps
    .venv/Scripts/python scripts/export_map_art.py bigmap     # one map
"""
import sys
import json
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from PIL import Image

from game.map_loader import load_map

ASSETS = Path("assets/maps")
OUT = Path("exported_map_art")

# Mirror of RESOURCE_LAYERS in game.ts bakeMapIfReady (folded names).
RESOURCE_LAYERS = {
    "cay", "tree", "trees", "resources",
    "vat pham ko lien quan", "ore", "ores", "mine",
    "tang da nho", "tang da lon",
    "nam nau", "nam tim", "co", "hoa trang", "hoa xanh", "hoa tim", "hoa vang",
}


def fold_name(s: str) -> str:
    return (s.normalize if False else s)  # placeholder, replaced below


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("đ", "d").replace("Đ", "D").lower()


def tileset_for_gid(md, gid: int):
    """game.ts tilesetForGid: prefer the range hit (last one wins), else the
    largest-firstgid-below fallback."""
    best = None
    range_hit = None
    for t in md.tilesets:
        if gid < t["firstgid"]:
            continue
        count = t.get("tilecount")
        if count is None:
            # mirror welcome payload: columns*512 fallback guess
            count = t.get("columns", 1) * 512
        if gid < t["firstgid"] + count:
            range_hit = t
        if best is None or t["firstgid"] > best["firstgid"]:
            best = t
    return range_hit or best


def bake_map_art(map_id: str) -> None:
    md = load_map(map_id, ASSETS)
    if not md.tile_layers:
        print(f"[{map_id}] no tile layers (simple map) — image_path={md.image_path}")
        if md.image_path and md.image_path.exists():
            img = Image.open(md.image_path)
            out = OUT / f"{map_id}.png"
            img.save(out)
            print(f"  -> copied {out} ({img.width}x{img.height})")
        return

    tw, th = md.tile_width, md.tile_height
    W, H = md.width * tw, md.height * th
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    above = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if md.ysort_cells else None

    # Server-side ysort_cells exist -> some cells belong to the above canvas.
    ysort = set(md.ysort_cells)
    sheets = {}  # image_path -> PIL image

    def sheet_for(ts):
        p = ts.get("image_path")
        if p is None:
            return None
        if p not in sheets:
            try:
                sheets[p] = Image.open(p).convert("RGBA")
            except Exception as e:  # noqa: BLE001
                print(f"  ! cannot open sheet {p}: {e}")
                sheets[p] = None
        return sheets[p]

    # No live-node info offline: bake every resource-layer tile (same as the
    # client's rule for node-less tiles). The minimap only ever shows the
    # FULL art in the welcome moment anyway.
    for name, grid in md.tile_layers:
        folded = _fold(name or "")
        is_above_layer = False  # above_layers only arrive via welcome payload;
        # ysort cells still land on the above canvas below.
        for y, row in enumerate(grid):
            for x, gid in enumerate(row):
                if not gid:
                    continue
                ts = tileset_for_gid(md, gid)
                if ts is None or ts.get("image_path") is None:
                    continue
                img = sheet_for(ts)
                if img is None:
                    continue
                local = gid - ts["firstgid"]
                cols = ts.get("columns", 1) or 1
                tilew = ts.get("tilewidth", 32) or 32
                col = local % cols
                rowi = local // cols
                if col * tilew >= img.width:
                    continue
                tile = img.crop((col * tilew, rowi * th, col * tilew + tilew, rowi * th + th))
                dest = above if (ysort and (x, y) in ysort) else base
                dest.alpha_composite(tile, (x * tw, y * th))
        del is_above_layer

    OUT.mkdir(exist_ok=True)
    base_out = OUT / f"{map_id}.png"
    base.save(base_out)
    print(f"[{map_id}] {md.width}x{md.height} tiles ({W}x{H} px), "
          f"{len(sheets)} sheets used -> {base_out}")
    if above is not None and above.getbbox():
        above_out = OUT / f"{map_id}_above.png"
        above.save(above_out)
        print(f"  -> above/canopy layer -> {above_out}")


def main() -> None:
    map_ids = sys.argv[1:]
    if not map_ids:
        map_ids = sorted(
            p.stem for p in ASSETS.glob("*.json")
            if not p.name.endswith((".solids.json", ".npcs.json"))
        )
        # also ekonia subdir maps
        map_ids += sorted(f"ekonia/{p.stem}" for p in (ASSETS / "ekonia").glob("*.json")
                          if not p.name.endswith((".solids.json", ".npcs.json")))
    for mid in map_ids:
        try:
            bake_map_art(mid)
        except FileNotFoundError as e:
            print(f"[{mid}] skipped: {e}")
        except Exception as e:  # noqa: BLE001 — report and continue
            print(f"[{mid}] FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
