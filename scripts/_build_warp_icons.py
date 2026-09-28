"""Build the 14 region warp icons (Kaetram-style discs).

PER-ICON TUNING: every region gets its own motif size + offset in
ICON_TUNING below — the user wants each icon individually balanced in
the disc, not one bulk setting. Edit the dict, re-run, re-check the
contact sheet (exported_map_art/warpicons_sheet.html).

Pixelation: cumulative grid factor 0.8 x 0.85 x 0.9 = 0.612 of the
motif size (user-tuned by eye: 20% + 15% + 10% steps).
"""
from PIL import Image, ImageFilter
import numpy as np

SRC = "C:/Users/phant/Downloads/status_effect_assets_pixel_32x32_v3/mmo_world_icons_14_png"
DST = "web_client/public/ui/kaetram/interface/warpicons"
BIG = 64

# Per-icon motif size (px inside the 64px disc) + (dx, dy) fine offset.
# dx > 0 = shift right, dy > 0 = shift down. Tune individually here.
# Default size raised from 38 -> 42 ("to lên tý").
ICON_TUNING = {
    0:  {"size": 42, "dx": 0, "dy": 0},
    1:  {"size": 42, "dx": 0, "dy": 0},
    2:  {"size": 42, "dx": 0, "dy": 0},
    3:  {"size": 42, "dx": 0, "dy": 0},
    4:  {"size": 42, "dx": 0, "dy": 0},
    5:  {"size": 42, "dx": 0, "dy": 0},
    6:  {"size": 42, "dx": 0, "dy": 0},
    7:  {"size": 42, "dx": 0, "dy": 0},
    8:  {"size": 42, "dx": 0, "dy": 0},
    9:  {"size": 42, "dx": 0, "dy": 0},
    10: {"size": 42, "dx": 0, "dy": 0},
    11: {"size": 42, "dx": 0, "dy": 0},
    12: {"size": 42, "dx": 0, "dy": 0},
    13: {"size": 42, "dx": 0, "dy": 0},
}

GRID_FACTOR = 0.49  # cumulative 20% + 15% + 10% + 20% pixelation

# EXTRA per-icon pixelation (user pick list): these regions get another
# 15% off the grid (0.49 * 0.85 = 0.4165).
EXTRA_PIXEL = {0, 1, 2, 6, 7, 9, 10, 11, 12}

# --- Kaetram disc base -----------------------------------------------------
# Background = CLEAN vertical gold->orange gradient sampled from the disc's
# EDGE pixels (255,230,155 top-ish / 251,181,89 mid). (Per-row medians of the
# whole cell leaked the OLD motif's colors — the green horizontal band bug.)
cell = np.array(Image.open("web_client/public/ui/kaetram/interface/mapicons.png").convert("RGBA").crop((0, 0, 16, 16)))
mask16 = cell[:, :, 3] > 128
# CENTER + SHAPE FIX: upscaling the 15px-wide authentic mask NEAREST kept its
# skew (motifs read "pushed right", diamond-ish edges). Use an ANALYTIC circle
# centered on the canvas: radius 29 disc, 4px black outline (same 1px-at-16px
# proportion as the Kaetram original).
yy0, xx0 = np.mgrid[0:16, 0:16]
dist16 = np.sqrt((yy0 - 7.5) ** 2 + (xx0 - 7.5) ** 2)
mask16 = dist16 <= 7.5
top_c = np.array([255, 230, 155], dtype=float)
bot_c = np.array([244, 160, 70], dtype=float)
base = np.zeros((BIG, BIG, 4), dtype=np.uint8)
ys_, xs_ = np.where(mask16)
y0, y1 = ys_.min(), ys_.max()
for y in range(BIG):
    # map canvas row back into the 16px cell space for gradient position
    t = min(1.0, max(0.0, (y / BIG * 16 - y0) / max(1, y1 - y0)))
    c = top_c + (bot_c - top_c) * t
    base[y, :, 0] = int(c[0]); base[y, :, 1] = int(c[1]); base[y, :, 2] = int(c[2])
    base[y, :, 3] = 255
mask_big = np.array(Image.fromarray((mask16 * 255).astype(np.uint8)).resize((BIG, BIG), Image.NEAREST)) > 128
er = np.array(Image.fromarray((mask16 * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3)).resize((BIG, BIG), Image.NEAREST)) > 128
base[mask_big & ~er] = (0, 0, 0, 255)
# crop everything outside the disc mask (corners transparent)
base[~mask_big] = (0, 0, 0, 0)
base_img = Image.fromarray(base, "RGBA")

yy, xx = np.mgrid[0:BIG, 0:BIG]
inner = ((yy - BIG / 2) ** 2 + (xx - BIG / 2) ** 2) <= 28 ** 2
clip = Image.fromarray((inner * 255).astype(np.uint8), "L")

for i in range(14):
    t = ICON_TUNING[i]
    size = t["size"]
    factor = GRID_FACTOR * (0.85 if i in EXTRA_PIXEL else 1.0)
    grid = max(4, round(size * factor))
    im = Image.open(f"{SRC}/{i}.png").convert("RGBA")
    bbox = im.getbbox()
    if bbox: im = im.crop(bbox)
    w, h = im.size
    side = max(w, h)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.alpha_composite(im, ((side - w) // 2, (side - h) // 2))
    m = sq.resize((size, size), Image.LANCZOS)
    m = m.resize((grid, grid), Image.BOX).resize((size, size), Image.NEAREST)
    out = base_img.copy()
    masked = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    ox = (BIG - size) // 2 + t["dx"]
    oy = (BIG - size) // 2 + t["dy"]
    masked.paste(m, (ox, oy))
    masked.putalpha(Image.composite(masked.getchannel("A"), Image.new("L", (BIG, BIG), 0), clip))
    out.alpha_composite(masked)
    out.save(f"{DST}/{i}.png")
print("rebuilt 14 icons (disc centered, base factor", GRID_FACTOR, ", extra 15% on", sorted(EXTRA_PIXEL), ")")
