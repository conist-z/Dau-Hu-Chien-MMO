"""Convert assets/maps/lobbytrade.tmx (Tiled XML) into Tiled JSON.

The bot's map loader (game/map_loader.py) only understands Tiled JSON (and the
legacy Godot-wrapped .js). The lobby trade map was authored in Tiled and
exported as TMX, so this script re-exports it as standard Tiled JSON with the
tileset image paths rewritten to point into assets/tilesets/.

Run once after editing the TMX in Tiled:
    .venv\\Scripts\\python -X utf8 scripts/convert_lobbytrade_tmx.py
"""
import json
import pathlib
import xml.etree.ElementTree as ET

MAPS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def main() -> None:
    src = MAPS / "lobbytrade.tmx"
    root = ET.parse(src).getroot()
    w = int(root.get("width"))
    h = int(root.get("height"))

    # Tilesets: inline every referenced .tsx so firstgid mapping survives.
    tilesets = []
    for ts_el in root.iter("tileset"):
        firstgid = int(ts_el.get("firstgid"))
        source = ts_el.get("source")
        if source:
            # External .tsx lives next to the ORIGINAL authored path; resolve
            # by name from the sample dir used by the authoring machine.
            tsx_root = ET.parse(src.parent / source).getroot()
            name = tsx_root.get("name")
            img = tsx_root.find("image").get("source")
        else:
            name = ts_el.get("name")
            img = ts_el.find("image").get("source")
        tilesets.append(
            {
                "firstgid": firstgid,
                "name": name,
                "image": "assets/tilesets/" + pathlib.Path(img).name,
                "tilewidth": 32,
                "tileheight": 32,
            }
        )

    layers = []
    for layer in root.iter("layer"):
        name = layer.get("name")
        data = layer.find("data")
        if data.get("encoding") != "csv":
            raise SystemExit(f"unexpected encoding for layer {name!r}")
        flat = [int(x) for x in data.text.split(",")]
        layers.append(
            {
                "type": "tilelayer",
                "name": name,
                "width": w,
                "height": h,
                "data": flat,
            }
        )

    out = {
        "type": "map",
        "width": w,
        "height": h,
        "tilewidth": 32,
        "tileheight": 32,
        "orientation": "orthogonal",
        "renderorder": "right-down",
        "layers": layers,
        "tilesets": tilesets,
    }
    dst = MAPS / "lobbytrade.json"
    dst.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {dst} ({w}x{h}, {len(layers)} layers, {len(tilesets)} tilesets)")


if __name__ == "__main__":
    main()
