"""One-time asset slicer for the v5 kit Numbers + Inventory currency icons.

- Numbers/misc/grid16 row y=16 has 10 cells of 16x16: "10".."19" — each cell
  carries the digit `1` on the left half and digit N on the right half. We
  crop the RIGHT half (x 8..16, y 0..16) and slice the digit-1 core from
  cell "10"'s left half — giving the exact v5 digit sprites 0-9.
- Copies 009_coin_icon.png and 010_crystal_icon.png (already present as
  inv_coin/inv_crystal — verified identical by pixel-diff).

Run once:  .venv/Scripts/python scripts/slice_v5_numbers.py
"""
from __future__ import annotations

import os
import shutil

from PIL import Image

KIT = (r"C:\Users\phant\Downloads"
       r"\Free-Basic-Pixel-Art-UI-SLICED-v5-RECONSTRUCTION-KIT\assets")
GRID16 = os.path.join(KIT, "misc", "Numbers", "grid16")
INV = os.path.join(KIT, "psd_layers", "Inventory")
OUT = os.path.join("web_client", "public", "ui", "v5", "numbers")
OUT_LAYERS = os.path.join("web_client", "public", "ui", "v5", "layers")


def cell_file(x: int, y: int) -> str:
    for f in os.listdir(GRID16):
        stem = f.replace(".png", "").split("_")
        if int(stem[1]) == x and int(stem[2]) == y:
            return f
    raise FileNotFoundError(f"no grid16 cell at ({x}, {y})")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    # digits 0..9 live in cells (i*16, 16), right half
    for i in range(10):
        f = cell_file(i * 16, 16)
        im = Image.open(os.path.join(GRID16, f)).convert("RGBA")
        right = im.crop((8, 0, 16, 16))
        right.save(os.path.join(OUT, f"n{i}.png"))
        print("digit", i, "<-", f, right.size)
    # digit 1 core (left half of the "10" cell) for 2-digit rendering
    f10 = cell_file(0, 16)
    im10 = Image.open(os.path.join(GRID16, f10)).convert("RGBA")
    left = im10.crop((0, 0, 8, 16))
    left.save(os.path.join(OUT, "n1_left.png"))
    print("digit-1 left core <-", f10, left.size)
    # currency icons (idempotent copy)
    for src, dst in (("009_coin_icon.png", "inv_coin.png"),
                     ("010_crystal_icon.png", "inv_crystal.png")):
        shutil.copyfile(os.path.join(INV, src),
                        os.path.join(OUT_LAYERS, dst))
        print("icon", dst, "<-", src)
    print("DONE ->", OUT)


if __name__ == "__main__":
    main()
