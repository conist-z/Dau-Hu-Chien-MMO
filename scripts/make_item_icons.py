"""Generate the canonical item icon set from Kaetram item sprites.

Output (one 16x16 RGBA PNG per item id):
  - assets/gui/icons/<item_id>.png        (Discord hub renderer)
  - web_client/public/ui/icons/<item_id>.png  (web client)

Kaetram sprites are the PRIMARY source (pixel art matches the game world,
no emoji-font dependency). Items Kaetram doesn't have fall back to the
bundled Twemoji pack (assets/gui/items/<codepoint>.png). Emoji TEXT is
never used for icons — only the letter glyph remains as a last resort.

Run: .venv/Scripts/python scripts/make_item_icons.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
KAETRAM = ROOT / "kaetram_extract" / "04_items" / "sprites"
TWEMOJI = ROOT / "assets" / "gui" / "items"
BLOCKS = ROOT / "assets" / "blocks"  # placed-block face art (32x32 world PNG)
OUT_DIRS = [ROOT / "assets" / "gui" / "icons", ROOT / "web_client" / "public" / "ui" / "icons"]

# item_id -> Kaetram sprite stem (KAETRAM dir), or ("twemoji", codepoint)
# for items Kaetram doesn't ship. Optional tint multiplies the RGB channels
# (recolor while keeping shading) — used for HP/MP flasks + the steel sword.
# ("block", stem) sources the PLACED-BLOCK face PNG (assets/blocks/<stem>.png)
# so the inventory icon always matches exactly what the block looks like
# in the world — user rule: blocks use their own display asset as the icon.
SOURCES: dict[str, tuple[str, str] | tuple[str, str, tuple[float, float, float]]] = {
    # ---- Kaetram sprites (primary) ----
    "apple": ("kaetram", "apple"),
    "charcoal": ("kaetram", "coal"),
    "coal": ("kaetram", "coal"),
    "coin": ("kaetram", "gold"),
    "cooked_meat": ("kaetram", "cookedbeef"),
    "gold_axe": ("kaetram", "goldaxe"),
    "gold_pickaxe": ("kaetram", "goldpickaxe"),
    "gold_sword": ("kaetram", "goldsword"),
    "iron_axe": ("kaetram", "ironaxe"),
    "iron_ingot": ("kaetram", "ironbar"),
    "iron_ore": ("kaetram", "ironore"),
    "iron_pickaxe": ("kaetram", "ironpickaxe"),
    "iron_sword": ("kaetram", "ironsword"),
    "key_stone": ("kaetram", "candykey"),
    "raw_meat": ("kaetram", "rawbeef"),
    "steel_axe": ("kaetram", "cobaltaxe"),
    "steel_pickaxe": ("kaetram", "cobaltpickaxe"),
    "stick": ("kaetram", "stick"),
    "wood_pickaxe": ("kaetram", "bronzepickaxe"),
    "wood_shovel": ("kaetram", "ancientshovel"),
    "wood_sword": ("kaetram", "bronzesword"),
    "torch": ("block", "torch"),
    # ---- placed blocks: icon = the block's own world face asset ----
    "stone": ("block", "stone"),
    "wood": ("block", "wood"),
    "leaves": ("block", "leaves"),
    "floor": ("block", "floor"),
    "crafting_table": ("block", "crafting_table"),
    "furnace": ("block", "furnace"),
    # ---- tinted (same sprite, recolored) ----
    "potion_hp": ("kaetram", "flask", (1.0, 0.32, 0.32)),
    "potion_mp": ("kaetram", "flask", (0.42, 0.55, 1.15)),
    "steel_sword": ("kaetram", "goldsword", (0.55, 0.68, 0.92)),
    # ---- Twemoji fallbacks (Kaetram has no match) ----
    "dirt": ("twemoji", "1f7e4"),
    "plank": ("twemoji", "1f7eb"),
    "rotten_flesh": ("twemoji", "1f969"),
}


def load_kaetram(stem: str) -> Image.Image | None:
    p = KAETRAM / f"{stem}.png"
    if not p.is_file():
        return None
    return Image.open(p).convert("RGBA")


def load_block_face(stem: str) -> Image.Image | None:
    """A placed-block face PNG, downscaled to 16x16 icon (NEAREST — hard
    pixel edges kept, no blur: the icon must read as the same art)."""
    p = BLOCKS / f"{stem}.png"
    if not p.is_file():
        return None
    im = Image.open(p).convert("RGBA")
    if im.size != (16, 16):
        im = im.resize((16, 16), Image.NEAREST)
    return im


def load_twemoji(cp: str) -> Image.Image | None:
    p = TWEMOJI / f"{cp}.png"
    if not p.is_file():
        return None
    return Image.open(p).convert("RGBA")


def make() -> int:
    for d in OUT_DIRS:
        d.mkdir(parents=True, exist_ok=True)
    written, fallbacks = [], []
    for item_id, src in SOURCES.items():
        kind, name = src[0], src[1]
        tint = src[2] if len(src) > 2 else None
        if kind == "block":
            im = load_block_face(name)
        else:
            im = load_kaetram(name) if kind == "kaetram" else load_twemoji(name)
        if im is None and kind == "kaetram":
            # Kaetram file missing -> keep pipeline alive with twemoji fallback
            fallbacks.append(item_id)
            continue
        if im is None:
            fallbacks.append(item_id)
            continue
        if tint is not None:
            im = im.copy()
            px = im.load()
            w, h = im.size
            for y in range(h):
                for x in range(w):
                    pr, pg, pb, pa = px[x, y]
                    if pa:
                        px[x, y] = (
                            min(255, int(pr * tint[0])),
                            min(255, int(pg * tint[1])),
                            min(255, int(pb * tint[2])),
                            pa,
                        )
        for d in OUT_DIRS:
            im.save(d / f"{item_id}.png")
        written.append(item_id)
    if fallbacks:
        print("MISSING (no source found):", ", ".join(fallbacks))
    print(f"written {len(written)} icons")
    return 0


if __name__ == "__main__":
    sys.exit(make())
