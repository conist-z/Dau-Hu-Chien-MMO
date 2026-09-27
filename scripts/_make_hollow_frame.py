"""Create mapframe_hollow.png: the ORIGINAL Kaetram frame ring with the
center punched transparent, so the custom world map shows through while the
decorative border (and its working close button area) stays pixel-identical."""
import numpy as np
from PIL import Image

ref = np.array(Image.open("web_client/public/ui/kaetram/interface/mapframe.png").convert("RGBA"))
h, w = ref.shape[:2]
r = ref[:, :, 0].astype(int)
g = ref[:, :, 1].astype(int)
b = ref[:, :, 2].astype(int)
a = ref[:, :, 3]

# border ring = red-brown edge + gold accents + the sand rim just inside
is_border_red = (a > 100) & (r > 40) & (r < 180) & (g < 80) & (b < 90)
is_gold = (a > 100) & (r > 200) & (g > 120) & (b < 150)

# NO DILATION: growing the ring inward baked the OLD map's ocean pixels
# (lighter blue) into the composite edge — the user's map showed a band of
# the old Kaetram map around its border ("layer map cũ đè lên"). The ring
# is exactly the red-brown/gold border pixels of the original frame; the
# custom map shows right up to them.
ring = is_border_red | is_gold

out = ref.copy()
out[:, :, 3] = np.where(ring, a, 0)
Image.fromarray(out, "RGBA").save("web_client/public/ui/kaetram/interface/mapframe_hollow.png")
print("ring px:", ring.sum(), "-> web_client/public/ui/kaetram/interface/mapframe_hollow.png")

# quick check composite over the user's world map for visual verification
world = Image.open(r"C:\Users\phant\Downloads\ChatGPT Image 01_39_44 28 thg 9, 2026.png").convert("RGBA")
world = world.resize((270, 180), Image.BOX)
hollow = Image.open("web_client/public/ui/kaetram/interface/mapframe_hollow.png")
comp = world.copy()
comp.alpha_composite(hollow)
comp.resize((810, 540), Image.NEAREST).save("exported_map_art/frame_hollow_check.png")
print("check image -> exported_map_art/frame_hollow_check.png")
