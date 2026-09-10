"""Fetch missing block faces from the public Minecraft asset mirror and
convert them into 32x32 sprites for ``assets/blocks/<block_id>.png``.

Same pipeline as convert_block_textures.py (BOX down to the 16x16 authoring
grid, NEAREST back up to the 32px tile), but sources come straight from the
InventivetalentDev/minecraft-assets GitHub mirror of Mojang's vanilla
textures — the same origin as the wiki files already converted for
stone/wood/crafting_table/furnace.

Special cases:
- leaves textures ship GRAYSCALE in modern versions (biome-tinted in game);
  we bake the classic green tint at convert time so the renderer needs no
  tint support.
- torch.png has a transparent background — kept as-is (alpha preserved).

Usage:
    .venv/Scripts/python scripts/fetch_block_faces.py

Idempotent: re-running overwrites the three outputs with identical bytes.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DST_DIR = PROJECT_ROOT / "assets" / "blocks"

MC_BASE = (
    "https://raw.githubusercontent.com/InventivetalentDev/"
    "minecraft-assets/1.19.4/assets/minecraft/textures/block/"
)

# block id (game/blocks.py) -> (remote file, green tint factor or None)
FETCHES = {
    "leaves": ("oak_leaves.png", (0.45, 0.85, 0.32)),
    "torch": ("torch.png", None),
    "floor": ("dark_oak_planks.png", None),
}

PIXEL_GRID = 16   # vanilla block faces are authored at 16x16
TILE_SIZE = 32    # game renderer tile size


def fetch_and_convert(block_id: str, remote: str, tint) -> Path:
    url = MC_BASE + remote
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 fixed host
        data = resp.read()
    img = Image.open(__import__("io").BytesIO(data)).convert("RGBA")
    small = img.resize((PIXEL_GRID, PIXEL_GRID), Image.BOX)
    if tint is not None:
        # Multiply the grayscale leaf art by the classic foliage green.
        bands = small.split()
        luts = [
            [min(255, int(i * t)) for i in range(256)] for t in tint
        ]
        small = Image.merge("RGBA", (
            bands[0].point(luts[0]),
            bands[1].point(luts[1]),
            bands[2].point(luts[2]),
            bands[3],
        ))
    out = small.resize((TILE_SIZE, TILE_SIZE), Image.NEAREST)
    DST_DIR.mkdir(parents=True, exist_ok=True)
    dst = DST_DIR / f"{block_id}.png"
    out.save(dst, format="PNG")
    return dst


def main() -> int:
    ok = True
    for block_id, (remote, tint) in FETCHES.items():
        try:
            dst = fetch_and_convert(block_id, remote, tint)
            print(f"OK  {remote} -> {dst}")
        except Exception as e:  # noqa: BLE001 — report each failure, keep going
            print(f"FAIL {block_id} ({remote}): {e}", file=sys.stderr)
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
