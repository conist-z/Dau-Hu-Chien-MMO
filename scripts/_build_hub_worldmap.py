"""Build the final hub world map asset:
1. Source = the user's GPT render (retro 96-color look they chose).
2. CROP off the GPT-generated golden frame (keep only the inner map/ocean).
3. True-downscale to 270x180 + flat 96-color quantize (retro pixel look).
4. Composite the hollow ORIGINAL Kaetram frame ring on top -> this becomes
   web_client/public/ui/kaetram/interface/mapframe_custom.png
(The CSS keeps the Kaetram frame visuals: we bake map under the ring into
one PNG so no CSS layering is needed, and the close button area is intact.)
"""
import numpy as np
from PIL import Image

src = Image.open(r"C:\Users\phant\Downloads\ChatGPT Image 01_39_44 28 thg 9, 2026.png").convert("RGB")
w, h = src.size

# --- locate the golden GPT frame and crop INSIDE it -------------------
# gold: high r, mid g, low b
px = src.load()

def is_goldish(c):
    r, g, b = c
    return r > 170 and g > 110 and b < 110 and (r - b) > 90

# The GPT frame does NOT start at the image edge: outside it there is a
# blue ocean margin, THEN the gold ring, THEN the map. So detect the gold
# BAND range per edge (first/last mostly-gold row/col) and crop just inside
# its inner boundary. Measured on the actual render: gold band at y≈8-16.
def gold_band(axis, reverse):
    """(first, last) index of the dominant-gold band along an edge."""
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
ft = (top_band[1] + 2) if top_band else 0
fb = (bot_band[1] + 2) if bot_band else 0
fl = (left_band[1] + 2) if left_band else 0
fr = (right_band[1] + 2) if right_band else 0
print(f"crop: L{fl} R{fr} T{ft} B{fb}")
inner = src.crop((fl, ft, w - fr, h - fb))
print("inner size:", inner.size)

# --- true downscale + retro flat quantize -----------------------------
small = inner.resize((270, 180), Image.BOX)
q = small.quantize(colors=96, method=Image.MEDIANCUT, dither=Image.NONE).convert("RGB")
q.save("exported_map_art/worldmap_hub_inner.png")

# --- composite the hollow Kaetram ring on top -------------------------
hollow = Image.open("web_client/public/ui/kaetram/interface/mapframe_hollow.png").convert("RGBA")
base = q.convert("RGBA")
base.alpha_composite(hollow)
base.save("web_client/public/ui/kaetram/interface/mapframe_custom.png")
base.resize((810, 540), Image.NEAREST).save("exported_map_art/mapframe_custom_x3.png")
print("saved -> web_client/public/ui/kaetram/interface/mapframe_custom.png")

# --- markers check on the final asset ---------------------------------
import json
from PIL import ImageDraw
mk = json.load(open("exported_map_art/warp_markers.json"))
vis = base.convert("RGB").resize((810, 540), Image.NEAREST)
d = ImageDraw.Draw(vis)
for m in mk["markers"]:
    x, y = m["x"] / 270 * 810, m["y"] / 180 * 540
    d.ellipse([x - 8, y - 8, x + 8, y + 8], outline=(0, 255, 60), width=2)
    d.text((x + 9, y - 6), str(m["id"]), fill=(0, 255, 60))
vis.save("exported_map_art/mapframe_custom_markers_x3.png")
print("marker check -> exported_map_art/mapframe_custom_markers_x3.png")
