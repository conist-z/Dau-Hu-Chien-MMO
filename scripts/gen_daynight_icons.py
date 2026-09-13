"""Build the day/night clock icons from the pixel-art asset pack.

Sources live in ``assets/gui/daynight/source/`` — verbatim copies of the
large (119x119) tiles from the "game_assets_cropped" pack. The tiles are
NOT animation frames: each one represents a period of the in-game day
("buổi") and is shown statically while that period lasts:

  sun_morning.png  rising sun        -> 05:00-10:59  (sáng)
  sun_noon.png     full sun          -> 11:00-16:59  (ban ngày)
  sun_dusk.png     sun on the horizon-> 17:00-18:59  (hoàng hôn)
  moon_night.png   moon              -> 19:00-04:59  (ban đêm)

The pack's third row (plain colour swatches) and its thin-crescent moon
tile (too intricate to read at small size) are deliberately skipped.

Each tile sits on an exact 7px pixel-art grid (119 = 7 x 17): it is
cropped to 119x119, downscaled NEAREST onto the true 17x17 art grid — no
blur, no jagged half-pixels — and the navy backdrop is keyed out so the
sun/moon floats directly on the hub background. The renderer scales the
17x17 art 2x with NEAREST, so every art pixel stays a perfect square.

Output: assets/gui/daynight/{sun_morning,sun_noon,sun_dusk,moon_night}.png
"""
import pathlib

from PIL import Image

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
ICON_DIR = PROJECT_ROOT / "assets" / "gui" / "daynight"
SOURCE_DIR = ICON_DIR / "source"

ART_SIZE = 17    # true pixel-art grid of the source tiles (119 / 7)

# The tiles sit on an opaque navy backdrop (38, 42, 70). The hub background
# is a very close dark navy, so keying the backdrop out lets the sun/moon
# art float directly on the HUD instead of showing as a square block.
BACKDROP_KEY = (38, 42, 70)
KEY_TOLERANCE = 14

# (output name, source tile)
MAPPING = [
    ("sun_morning.png", "sun_rise.png"),            # sáng: sun rises
    ("sun_noon.png", "sun_full.png"),               # ban ngày: full sun
    ("sun_dusk.png", "sun_horizon.png"),            # hoàng hôn: sun on horizon
    ("moon_night.png", "moon_crescent_face.png"),   # đêm: moon
]

# Legacy generated icons from previous revisions (frame-style naming).
STALE_OUTPUTS = [
    "sun_00.png", "sun_01.png",
    "star_00.png", "star_01.png",
    "moon_00.png", "moon_01.png", "moon_02.png",
]


def convert(src: Image.Image) -> Image.Image:
    """Crop to a square 119x119 tile, snap to the 17x17 art grid and key
    out the navy backdrop. Output stays at the native 17x17 art resolution
    — the renderer upscales 2x with NEAREST, keeping pixels square."""
    w, h = src.size
    side = min(w, h) // 7 * 7  # largest multiple of the 7px grid that fits
    img = src.crop((0, 0, side, side))
    art = img.resize((ART_SIZE, ART_SIZE), Image.NEAREST)  # exact 7px cells
    px = art.load()
    kr, kg, kb = BACKDROP_KEY
    for y in range(ART_SIZE):
        for x in range(ART_SIZE):
            r, g, b, a = px[x, y]
            if a and abs(r - kr) <= KEY_TOLERANCE and abs(g - kg) <= KEY_TOLERANCE and abs(b - kb) <= KEY_TOLERANCE:
                px[x, y] = (0, 0, 0, 0)
    return art


def main():
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    for stale in STALE_OUTPUTS:
        p = ICON_DIR / stale
        if p.exists():
            p.unlink()
            print(f"  removed stale {stale}")
    for out_name, src_name in MAPPING:
        src = Image.open(SOURCE_DIR / src_name).convert("RGBA")
        out = convert(src)
        out.save(ICON_DIR / out_name, "PNG")
        print(f"  {out_name} <- {src_name}")
    print("Done")


if __name__ == "__main__":
    main()
