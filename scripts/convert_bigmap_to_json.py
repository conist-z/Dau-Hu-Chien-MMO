"""Unwrap the Godot-wrapped bigmap.js into a plain Tiled JSON file.

The uploaded ``bigmap.js`` is a Tiled export wrapped in the Godot
``(::function(name,data){...}(...)`` form, which Tiled Desktop cannot open.
This script unwraps it into ``assets/maps/bigmap.json`` (standard Tiled JSON,
all tile layers + tileset intact) and fixes the tileset image path so the
editor/bot resolve the bundled PNG.

Usage:  .venv\\Scripts\\python scripts/convert_bigmap_to_json.py
"""

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
MAPS = ROOT / "assets" / "maps"


def unwrap(text: str) -> dict:
    m = re.search(r"\(\s*[^,()]+,\s*(\{.*\})\s*\)\s*;?\s*$", text, re.DOTALL)
    if not m:
        raise ValueError("Not a recognisable Godot/Tiled wrapper")
    return json.loads(m.group(1))


def main() -> None:
    src = MAPS / "bigmap.js"
    dst = MAPS / "bigmap.json"
    if not src.exists():
        raise FileNotFoundError(f"missing {src}")
    data = unwrap(src.read_text(encoding="utf-8"))
    # Tileset image path must resolve relative to the map file. The bundled
    # PNG lives flat in assets/maps, so drop the fake folder prefix.
    for ts in data.get("tilesets", []):
        img = ts.get("image", "")
        if img:
            ts["image"] = Path(img).name
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {dst}")


if __name__ == "__main__":
    sys.exit(main() or 0)