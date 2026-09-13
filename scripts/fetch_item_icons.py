"""One-time fetch of item icon PNGs for the hub hotbar (dev machine only).

Downloads a curated Twemoji PNG per game item / block (repo: jdecked/twemoji,
CC-BY 4.0) into ``assets/gui/items/`` so ``rendering/hub_renderer.py`` can draw
the REAL item icon inside each hotbar slot — no runtime network needed (the
hosting container has no trusted egress, see docs/deployment.md §4).

Mapping lives in ``ITEM_ICON_CODEPOINTS`` (scripts-local): item_id -> unicode
codepoint. Usage:

    .venv/Scripts/python scripts/fetch_item_icons.py
"""
from __future__ import annotations

import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "gui" / "items"

BASE = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72"

# item_id / block_id -> Twemoji codepoint. Mirrors the emoji shown on the
# D-pad hotbar buttons and the inventory grid, so the hub slot icon matches.
ITEM_ICON_CODEPOINTS = {
    # --- items (game/items.py) ---
    "potion_hp": "1f48a",       # 🧪 pill? no: pill 1f48a; potion uses test tube
    "potion_mp": "1f7e6",       # 🔵 blue circle
    "key_stone": "1f511",       # 🔑
    "apple": "1f34e",           # 🍎
    "plank": "1fab5",           # 🪵? no: plank -> brown square 1f7eb fallback below
    "stick": "1f962",           # 🥢 chopsticks
    "wood_axe": "1fa93",        # 🪓 axe
    "wood_pickaxe": "26cf",     # ⛏️ pick
    "rotten_flesh": "1f969",    # 🥩 cut of meat
    "coin": "1fa99",            # 🪙 coin
    # --- smelting chain (game/smelting.py) ---
    "iron_ore": "1f348",        # 🍈 chestnut-like brown blob (fallback ore)
    "coal": "26ab",             # ⚫ black circle
    "iron_ingot": "1f948",      # 🥈 silver medal (ingot-ish)
    "charcoal": "1f311",        # 🌑 new moon
    "raw_meat": "1f356",        # 🍖 meat on bone
    "cooked_meat": "1f357",     # 🍗 poultry leg
    # --- blocks (game/blocks.py) ---
    "stone": "1faa8",           # 🪨 rock
    "wood": "1fab5",            # 🪵 wood
    "leaves": "1f33f",          # 🌿 herb
    "torch": "1f56f",           # 🕯️ candle
    "floor": "1f7e4",           # 🟫 brown square
    "crafting_table": "1f6e0",  # 🛠️ hammer & wrench
    "furnace": "1f525",         # 🔥 fire
}

# Post-fetch sanity mapping fixes (kept explicit instead of magic above):
ITEM_ICON_CODEPOINTS["potion_hp"] = "1f48a"  # 💊 pill (test tube renders odd at 84px)
ITEM_ICON_CODEPOINTS["plank"] = "1f7eb"      # 🟫 brown square (wood log = wood block)

# Tool families missing from the base map: every tier (dirt/wood/stone/iron)
# of the sword family uses the dagger, the shovel family the spoon (the
# pack has no per-tier tool art; tiers are distinguished by name/tooltip).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import game.tools as _tools  # noqa: E402  (script run from project root)

for _mat in ("dirt", "wood", "stone", "iron"):
    ITEM_ICON_CODEPOINTS[_tools.tool_item_id("sword", _mat)] = "1f5e1"   # 🗡️ dagger
    ITEM_ICON_CODEPOINTS[_tools.tool_item_id("shovel", _mat)] = "1f944"  # 🥄 spoon
    ITEM_ICON_CODEPOINTS[_tools.tool_item_id("axe", _mat)] = "1fa93"     # 🪓 axe
    ITEM_ICON_CODEPOINTS[_tools.tool_item_id("pickaxe", _mat)] = "26cf"  # ⛏️ pick


def codepoint_to_file(codepoint: str) -> str:
    # Twemoji file naming strips VS16 ('fe0f') when the base codepoint exists.
    parts = codepoint.split("-")
    if len(parts) > 1 and parts[-1] == "fe0f":
        stripped = "-".join(parts[:-1])
        return f"{stripped}.png"
    return f"{codepoint}.png"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    for item_id in sorted(ITEM_ICON_CODEPOINTS):
        codepoint = ITEM_ICON_CODEPOINTS[item_id]
        fname = codepoint_to_file(codepoint)
        path = OUT_DIR / fname
        if path.exists():
            ok += 1
            print("skip (cached)", item_id, fname)
            continue
        url = f"{BASE}/{codepoint}.png"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = resp.read()
            path.write_bytes(data)
            print("fetched", item_id, fname, len(data), "bytes")
            ok += 1
            time.sleep(0.05)
        except Exception as e:  # noqa: BLE001 — one bad emoji must not abort the pack
            print("MISS", item_id, codepoint, e)
            failed += 1
    print(f"done: {ok} ok, {failed} failed")


if __name__ == "__main__":
    main()
