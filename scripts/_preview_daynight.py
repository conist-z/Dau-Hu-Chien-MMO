"""Preview the day/night icons next to the hub digital clock.

Draws each phase (day / twilight / night) exactly like ``HubRenderer``
does -- same pixel-digit clock, same icon paste geometry -- then saves a
zoomed (crisp-pixel) strip and a true-size strip to %TEMP% so you can
eyeball how the HUD actually looks on Discord.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PIL import Image

from rendering.hub_renderer import (
    BG,
    CLOCK_COLOR,
    CLOCK_GAP,
    CLOCK_MARGIN,
    CLOCK_RAISE,
    CLOCK_SCALE,
    CLOCK_SHADOW,
    ICON_GAP,
    ICON_SIZE,
    HUB_DISPLAY_H,
    MAP_UPLOAD_SCALE,
    HubRenderer,
)

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets"
ICON_DIR = ASSETS / "gui" / "daynight"

W = 240
H = int(HUB_DISPLAY_H / MAP_UPLOAD_SCALE)  # internal hub height (132)

ROWS = [
    ("morning", "sun_morning.png", "08:00"),
    ("day", "sun_noon.png", "12:00"),
    ("evening", "sun_dusk.png", "18:00"),
    ("night", "moon_night.png", "23:45"),
]


def draw_clock(img, text, draw):
    dw = 3 * CLOCK_SCALE
    dh = 5 * CLOCK_SCALE
    total = len(text) * (dw + CLOCK_GAP) - CLOCK_GAP
    x = img.width - CLOCK_MARGIN - total
    base_y = img.height - CLOCK_MARGIN - dh  # icon anchor (unraised)
    y = base_y - CLOCK_RAISE  # raised digits
    start = x
    for ch in text:
        HubRenderer._draw_pixel_char(draw, x, y, ch, CLOCK_SCALE, CLOCK_COLOR, CLOCK_SHADOW)
        x += dw + CLOCK_GAP
    return start, base_y, y, dh


strip = Image.new("RGBA", (W, H * len(ROWS)))
from PIL import ImageDraw

for i, (phase, icon_name, clock) in enumerate(ROWS):
    f = Image.new("RGBA", (W, H), BG + (255,))
    d = ImageDraw.Draw(f)
    start, base_y, digit_y, dh = draw_clock(f, clock, d)
    icon = Image.open(ICON_DIR / icon_name).convert("RGBA")
    icon = icon.resize((ICON_SIZE, ICON_SIZE), Image.NEAREST)
    # Same geometry as HubRenderer._draw_clock: icon sits LEFT of the
    # whole clock text, anchored at the unraised bottom margin.
    icon_x = start - ICON_GAP - ICON_SIZE
    icon_y = base_y + dh - ICON_SIZE
    f.paste(icon, (icon_x, icon_y), icon)
    strip.paste(f, (0, i * H))

out_dir = pathlib.Path(os.environ.get("TEMP", "."))
zoom = strip.resize((W * 3, H * len(ROWS) * 3), Image.NEAREST)
zoom.save(out_dir / "daynight_preview_zoom.png")
true_size = strip.resize((int(W * MAP_UPLOAD_SCALE), int(H * len(ROWS) * MAP_UPLOAD_SCALE)), Image.NEAREST)
true_size.save(out_dir / "daynight_preview_true.png")
print(f"saved: {out_dir / 'daynight_preview_zoom.png'}")
print(f"saved: {out_dir / 'daynight_preview_true.png'}")