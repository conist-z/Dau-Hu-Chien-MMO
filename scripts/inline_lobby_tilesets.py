"""Inline external .tsx tileset references into assets/maps/lobbytrade.json.

The Tiled export references tilesets via "source" (external .tsx files) and
carries NO image/columns/tilecount inline, so the loader's _resolve_tilesets
skipped every sheet and the renderer drew garbage. This script reads each
.tsx (searched next to the map, in the Pipoya SampleMap pack, and in
assets/tilesets) and writes image/columns/tilecount inline (one-time fix).
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "assets/maps/lobbytrade.json"
TSX_SEARCH_DIRS = [
    Path(r"D:/UserData/Downloads/New folder/Pipoya RPG Tileset 32x32"
         r"/Pipoya RPG Tileset 32x32/SampleMap"),
    ROOT / "assets/tilesets",
    ROOT / "assets/maps",
]

data = json.loads(MAP.read_text(encoding="utf-8"))
for ts in data.get("tilesets", []):
    src = ts.get("source")
    if ts.get("columns") and ts.get("image"):
        continue  # fully inline already
    # Tiled references the sheet either via external .tsx ("source") or an
    # inline "image" path — in both cases the tsx shares the PNG's stem.
    png_stem = Path(src or ts.get("image") or "").stem
    name = png_stem + ".tsx"
    tsx_path = next(
        (d / name for d in TSX_SEARCH_DIRS if name and (d / name).exists()), None
    )
    if tsx_path is None:
        print("!! tsx not found:", src)
        continue
    root = ET.parse(tsx_path).getroot()
    el = root if root.tag == "tileset" else root.find("tileset")
    img = el.find("image")
    image_src = img.get("source") if img is not None else None
    png_name = Path(image_src).name if image_src else None
    if image_src:
        ts["image"] = f"assets/tilesets/{png_name}"
    ts["columns"] = int(el.get("columns", 1))
    ts["tilecount"] = int(el.get("tilecount", 0))
    ts["tilewidth"] = int(el.get("tilewidth", data.get("tilewidth", 32)))
    ts["tileheight"] = int(el.get("tileheight", data.get("tileheight", 32)))
    if img is not None and img.get("width"):
        ts["imagewidth"] = int(img.get("width"))
        ts["imageheight"] = int(img.get("height"))
    print("inlined:", ts["firstgid"], png_name, "cols", ts["columns"])

MAP.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
print("written", MAP)
