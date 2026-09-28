"""Build the final hub world map asset:
1. Source = the user's APPROVED map (Lightshot 782, clean render).
2. Crop off the GPT golden frame; inpaint the 14 translucent red markers.
3. Scale the map to fit INSIDE the Kaetram frame's inner window (the frame
   ring occupies only the outer ~5px — the map must NOT bleed over it).
   Sharper look: LANCZOS downscale + unsharp mask (instead of BOX + flat).
4. Composite the hollow ORIGINAL Kaetram frame ring on top ->
   web_client/public/ui/kaetram/interface/mapframe_custom.png
5. Export a numbered check image (one bold number per warp icon) so the
   user can say "move icon N to X" easily.
"""
import json
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SRC = "_tmp_correct_map.png"
FRAME = "web_client/public/ui/kaetram/interface/mapframe.png"
HOLLOW = "web_client/public/ui/kaetram/interface/mapframe_hollow.png"
OUT = "web_client/public/ui/kaetram/interface/mapframe_custom.png"
MARKERS_JSON = "exported_map_art/warp_markers.json"
CHECK = "exported_map_art/mapframe_custom_markers_x3.png"

# --- inner window of the Kaetram frame (measured from mapframe.png) ------
# ring: rows 3-4 top/bottom, cols 2-3 left/right (alpha 0 at 0-1 / 0-1).
# Inner safe window with 1px breathing room:
INSET = 5
W, H = 270, 180
IX0, IY0, IX1, IY1 = INSET, INSET, W - INSET, H - INSET
IW, IH = IX1 - IX0, IY1 - IY0  # 260 x 170

src = Image.open(SRC).convert("RGB")
w, h = src.size
px = src.load()


def is_goldish(c):
    r, g, b = c
    return r > 170 and g > 110 and b < 110 and (r - b) > 90


# --- locate the golden GPT frame and crop INSIDE it ----------------------
def gold_band(axis, reverse):
    bands = []
    for i in range(120):
        if axis == "row":
            cnt = sum(1 for x in range(0, w, 13) if is_goldish(px[x, i if not reverse else h - 1 - i]))
            total = len(range(0, w, 13))
        else:
            cnt = sum(1 for y in range(0, h, 13) if is_goldish(px[i if not reverse else w - 1 - i, y]))
            total = len(range(0, h, 13))
        if cnt >= total * 0.6:
            bands.append(i)
    if not bands:
        return None
    return (bands[0], bands[-1])


top_band = gold_band("row", False)
bot_band = gold_band("row", True)
left_band = gold_band("col", False)
right_band = gold_band("col", True)
print("gold bands:", top_band, bot_band, left_band, right_band)

# --- inpaint the 14 translucent red marker circles -------------------------
mk = json.load(open(MARKERS_JSON))
msrc = src.load()
R = int(h * 0.032)
for m in mk["markers"]:
    cx = m["x"] / W * w
    cy = m["y"] / H * h
    ri = R + 2
    for dy in range(-ri, ri + 1):
        y = int(cy) + dy
        if not (0 <= y < h):
            continue
        span = int(math.sqrt(max(0.0, ri * ri - dy * dy)))
        x0, x1 = int(cx) - span, int(cx) + span
        lx = max(0, x0 - 6)
        rx = min(w - 1, x1 + 6)
        lc = msrc[lx, y]
        rc = msrc[rx, y]
        for x in range(max(0, x0), min(w, x1 + 1)):
            t = (x - lx) / max(1, (rx - lx))
            msrc[x, y] = (
                round(lc[0] + (rc[0] - lc[0]) * t),
                round(lc[1] + (rc[1] - lc[1]) * t),
                round(lc[2] + (rc[2] - lc[2]) * t),
            )
print("inpainted", len(mk["markers"]), "markers")

ft = (top_band[1] + 2) if top_band else 0
fb = (bot_band[1] + 2) if bot_band else 0
fl = (left_band[1] + 2) if left_band else 0
fr = (right_band[1] + 2) if right_band else 0
print(f"crop: L{fl} R{fr} T{ft} B{fb}")
inner = src.crop((fl, ft, w - fr, h - fb))
print("inner size:", inner.size)

# --- sharper downscale INTO the frame's inner window ----------------------
# Cover-fit: scale so the map fills the window, center-crop the overflow.
sw, sh = inner.size
scale = max(IW / sw, IH / sh)
nw, nh = round(sw * scale), round(sh * scale)
sharp = inner.resize((nw, nh), Image.LANCZOS)
# unsharp mask for crisp pixel-art edges after downscale
sharp = sharp.filter(__import__("PIL.ImageFilter", fromlist=["ImageFilter"]).UnsharpMask(radius=2, percent=110, threshold=2))
lx0, ly0 = (nw - IW) // 2, (nh - IH) // 2
map_img = sharp.crop((lx0, ly0, lx0 + IW, ly0 + IH))
q = map_img.quantize(colors=128, method=Image.MEDIANCUT, dither=Image.NONE).convert("RGB")
q.save("exported_map_art/worldmap_hub_inner.png")

# --- composite: transparent base + map inside window + hollow ring on top -
base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
base.paste(q, (IX0, IY0))
hollow = Image.open(HOLLOW).convert("RGBA")
base.alpha_composite(hollow)
base.save(OUT)
base.resize((W * 3, H * 3), Image.NEAREST).save("exported_map_art/mapframe_custom_x3.png")
print("saved ->", OUT)

# --- numbered check image (bold number on every warp icon) ---------------
S = 3
vis = base.convert("RGB").resize((W * S, H * S), Image.NEAREST)
d = ImageDraw.Draw(vis)
try:
    font = ImageFont.load_default(size=22)
except TypeError:
    font = ImageFont.load_default()
for m in mk["markers"]:
    x, y = m["x"] / W * W * S, m["y"] / H * H * S
    r_out = 13
    # white disc + dark outline + dark bold number = readable on any art
    d.ellipse([x - r_out, y - r_out, x + r_out, y + r_out], fill=(255, 255, 255), outline=(20, 20, 20), width=3)
    tb = d.textbbox((0, 0), str(m["id"]), font=font, stroke_width=1)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.text((x - tw / 2 - tb[0], y - th / 2 - tb[1]), str(m["id"]), font=font, fill=(10, 10, 10))
vis.save(CHECK)
print("numbered check ->", CHECK)
