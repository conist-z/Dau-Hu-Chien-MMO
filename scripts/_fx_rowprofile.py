import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from PIL import Image

def row_profile(p):
    img = Image.open(p).convert("RGBA")
    a = img.getchannel("A")
    w, h = img.size
    px = a.load()
    counts = [sum(1 for x in range(w) if px[x, y] > 24) for y in range(h)]
    return w, h, counts

for name in ["rain/rain_00.png", "rain/rain_04.png", "snow/snow_tile.png", "wind/wind_00.png"]:
    p = Path("assets/fx") / name
    w, h, counts = row_profile(p)
    empty = [y for y, c in enumerate(counts) if c == 0]
    print(f"{name} {w}x{h} empty_rows={empty}")
    print("   counts:", counts)
