"""Convert raw block textures (webp/png, any size) into per-block 32x32 sprites.

Pipeline per source file (deterministic, idempotent):
1. Open with PIL (webp/png/jpg all supported), convert to RGBA.
2. Downscale to a 16x16 pixel-art grid using BOX (area-average) — source
   files from the Minecraft wiki are integer multiples of 16, so BOX lands
   exactly on the original pixel grid with no color bleed.
3. Nearest-neighbor upscale 16 -> 32 so sprites match the renderer's 32px
   tile size pixel-perfectly (no blur, game maps use tile_size=32).

Usage:
    .venv/Scripts/python scripts/convert_block_textures.py \
        [--src "D:\\UserData\\Downloads\\New folder\\mine"] [--dst assets/blocks]

Mapping is explicit: source filename -> block id used by game/blocks.py.
Files without a mapping entry are reported and skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

# Source filename stem (from the wiki download) -> block id in BLOCK_REGISTRY.
# One entry per file the user dropped in; extend as more blocks are added.
SOURCE_TO_BLOCK = {
    # NOTE: filenames were URL-encoded on download: %28 = '(' and %29 = ')'.
    "Cobblestone_29_JE5_BE3": "stone",
    "Crafting_Table_%28top_texture%29_JE2_BE2": "crafting_table",
    "Oak_Planks_%28texture%29_JE6_BE3": "wood",
    "Off_Furnace_%28front_texture%29_JE2_BE2": "furnace",
}

PIXEL_GRID = 16   # art is authored at 16x16 (Minecraft block face resolution)
TILE_SIZE = 32    # game renderer tile size (rendering/renderer.py)


def convert_file(src: Path, block_id: str, dst_dir: Path) -> Path:
    img = Image.open(src).convert("RGBA")
    # BOX on an integer multiple lands back on the 16x16 authoring grid.
    small = img.resize((PIXEL_GRID, PIXEL_GRID), Image.BOX)
    out = small.resize((TILE_SIZE, TILE_SIZE), Image.NEAREST)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{block_id}.png"
    out.save(dst, format="PNG")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=r"D:\UserData\Downloads\New folder\mine")
    ap.add_argument("--dst", default="assets/blocks")
    args = ap.parse_args()

    src_dir = Path(args.src)
    if not src_dir.is_dir():
        print(f"ERROR: source folder not found: {src_dir}", file=sys.stderr)
        return 1

    dst_dir = Path(args.dst)
    converted, skipped = [], []
    for src in sorted(src_dir.iterdir()):
        if src.suffix.lower() not in {".webp", ".png", ".jpg", ".jpeg"}:
            continue
        block_id = SOURCE_TO_BLOCK.get(src.stem)
        if block_id is None:
            skipped.append(src.name)
            continue
        dst = convert_file(src, block_id, dst_dir)
        converted.append((src.name, dst))
        print(f"OK  {src.name} -> {dst}")

    for name in skipped:
        print(f"SKIP {name} (no mapping entry in SOURCE_TO_BLOCK)")
    if not converted:
        print("ERROR: nothing converted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
