from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "maps"
W = H = 10
T = 32

img = Image.new("RGBA", (W * T, H * T), (34, 45, 70, 255))
d = ImageDraw.Draw(img)
for y in range(H):
    for x in range(W):
        base = (46, 120, 70, 255) if (x + y) % 2 == 0 else (40, 105, 62, 255)
        d.rectangle([x * T, y * T, (x + 1) * T, (y + 1) * T], fill=base)
# rock border (matches collision border in test-map.json)
d.rectangle([0, 0, W * T - 1, H * T - 1], outline=(90, 90, 90, 255), width=T)

out = ASSETS / "test-map.png"
img.save(out)
print("wrote", out)
