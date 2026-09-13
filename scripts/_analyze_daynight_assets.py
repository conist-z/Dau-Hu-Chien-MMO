"""Analyse the cropped game assets: sizes, dominant colours, pixel grid."""
import pathlib
from collections import Counter

from PIL import Image

SRC = pathlib.Path(__file__).resolve().parent.parent / "assets" / "gui" / "daynight" / "source"

KEY = (38, 42, 70)


def classify(rgb):
    r, g, b = rgb
    if abs(r - KEY[0]) <= 14 and abs(g - KEY[1]) <= 14 and abs(b - KEY[2]) <= 14:
        return "."  # navy backdrop
    if r > 200 and g > 150 and b < 160:
        return "G"  # gold-ish
    if abs(r - g) < 40 and b > g > r - 10 and r > 110:
        return "S"  # silver-ish
    return "?"


for name in ["sun_rise.png", "sun_full.png", "sun_horizon.png",
             "moon_crescent_spark.png", "moon_crescent_face.png", "moon_full.png"]:
    im = Image.open(SRC / name).convert("RGBA")
    w, h = im.size
    side = min(w, h) // 7 * 7
    art = im.crop((0, 0, side, side)).resize((17, 17), Image.NEAREST)
    px = art.load()
    print(f"--- {name} (17x17 art grid, . = navy backdrop) ---")
    for y in range(17):
        print("   " + "".join(classify(px[x, y][:3]) for x in range(17)))