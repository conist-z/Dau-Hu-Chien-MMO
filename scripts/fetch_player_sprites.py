"""Copy Kaetram player base + chosen weapon sheets into assets/players/
and build players_manifest.json (idempotent — rerunnable).

Source: kaetram_extract/08_player_sprites/ (shallow clone of Kaetram-Open).
License: assets CC-BY-SA 3.0 — see kaetram_extract/_license/ATTRIBUTION.md;
credits must stay anywhere these assets are shown.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "kaetram_extract" / "08_player_sprites"
OUT = ROOT / "assets" / "players"
WEAPON_SRC = SRC / "weapon"
WEAPON_OUT = OUT / "weapon"

# Same mapping as game/appearance.py WEAPON_SHEETS (kept in sync by test).
WEAPON_SHEETS = {
    "dirt_sword": "coppersword",
    "wood_sword": "ironsword",
    "stone_sword": "goldsword",
    "dirt_axe": "bronzeaxe",
    "wood_axe": "ironaxe",
    "stone_axe": "cobaltaxe",
    "dirt_pickaxe": "bronzepickaxe",
    "wood_pickaxe": "ironpickaxe",
    "stone_pickaxe": "cobaltpickaxe",
    "dirt_shovel": "spoon",
    "wood_shovel": "smithshammer",
    "stone_shovel": "ancientshovel",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    WEAPON_OUT.mkdir(parents=True, exist_ok=True)

    # Base body sheet (32x32 frames, 4x12 grid).
    shutil.copy2(SRC / "base.png", OUT / "base.png")

    # Weapon sheets (48x48 frames, 4x9 grid).
    stems = sorted(set(WEAPON_SHEETS.values()))
    for stem in stems:
        shutil.copy2(WEAPON_SRC / f"{stem}.png", WEAPON_OUT / f"{stem}.png")

    manifest = {
        "base": {
            "file": "base.png",
            "frame_w": 32, "frame_h": 32,
            "cols": 4, "rows": 12,
            # Draw origin: sprite drawn so the frame bottom sits on the tile
            # bottom (Kaetram offsetX=-8/-32; expressed here as centre-x).
            "offset_x": -8, "offset_y": -32,
        },
        "weapons": {
            stem: {
                "file": f"weapon/{stem}.png",
                "frame_w": 48, "frame_h": 48,
                "cols": 4, "rows": 9,
                # Kaetram sprites.json: weapons draw 24px above the entity
                # origin so the 48px frame centres over the 32px body.
                "offset_x": -8, "offset_y": -24,
            }
            for stem in stems
        },
        "rows": {
            "idle_down": 0, "idle_right": 1, "idle_up": 2,
            "walk_down": 3, "walk_right": 4, "walk_up": 5,
            "atk_down": 6, "atk_right": 7, "atk_up": 8,
        },
        "frames_per_row": 4,
        "speeds": {"idle": 250, "walk": 120, "atk": 50},
        "attribution": "Kaetram-Open assets, CC-BY-SA 3.0 — see kaetram_extract/_license/ATTRIBUTION.md",
    }
    with open(OUT / "players_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"[OK] base + {len(stems)} weapon sheets -> {OUT}")


if __name__ == "__main__":
    main()
