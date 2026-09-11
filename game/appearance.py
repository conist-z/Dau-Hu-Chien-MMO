"""Character appearance resolution (paperdoll) — pure, no discord/IO.

Maps (held_item, action, facing) -> the sprite layers a client must draw:
the character base sheet plus an optional weapon sheet, with the exact
animation row/frame to play. All numbers come from ``assets/players/
players_manifest.json`` (data-driven, rule 10) — this module only adds the
held-item -> weapon-sheet mapping and the animation state machine.

Sheet layout (Kaetram paperdoll, confirmed from its renderer):
- Base: 32x32 frames, 4 columns (frames) x 12 rows.
- Weapon sheets: 48x48 frames, 4 columns x 9 rows (no bow rows), drawn with
  offsetY so it overlays the body.
- Rows (both sheets): idle_down 0, idle_right 1, idle_up 2, walk_down 3,
  walk_right 4, walk_up 5, atk_down 6, atk_right 7, atk_up 8. Facing LEFT
  reuses the *_right row with flipX.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "assets" / "players" / "players_manifest.json"

# Animation row table (shared by base + weapon sheets).
ANIM_ROWS: Dict[str, int] = {
    "idle_down": 0, "idle_right": 1, "idle_up": 2,
    "walk_down": 3, "walk_right": 4, "walk_up": 5,
    "atk_down": 6, "atk_right": 7, "atk_up": 8,
}
# Frames per animation row (Kaetram: 4 columns on every row).
FRAMES_PER_ROW = 4

# Client-facing dirs -> row suffix. LEFT reuses the right-facing row + flipX.
_DIR_SUFFIX = {"down": "down", "up": "up", "left": "right", "right": "right"}
_DIRS_WITH_LEFT = {"down", "up", "left", "right"}

# Kaetram's animation speeds (ms/frame), from character.ts.
SPEEDS = {"idle": 250, "walk": 120, "atk": 50}

# held item id -> weapon sheet stem under assets/players/weapon/.
WEAPON_SHEETS: Dict[str, str] = {
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


@dataclass(frozen=True)
class AnimState:
    """One animation tick: which sheet rows/frames to play."""

    name: str       # idle | walk | atk (base, no dir suffix)
    row: int        # resolved row index in the sheet
    frames: int     # frames in the row
    speed_ms: int   # ms per frame
    flip_x: bool    # True when facing left (right row mirrored)


def load_manifest() -> dict:
    """Load players_manifest.json (cached by the caller if hot-path)."""
    with open(_MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def anim_state(action: str, facing: str) -> AnimState:
    """Resolve (action, facing) -> AnimState.

    ``action``: idle | walk | atk. ``facing``: down | up | left | right.
    Unknown inputs fall back to idle_down — rendering never crashes.
    """
    act = action if action in SPEEDS else "idle"
    face = facing if facing in _DIRS_WITH_LEFT else "down"
    suffix = _DIR_SUFFIX[face]
    return AnimState(
        name=act,
        row=ANIM_ROWS[f"{act}_{suffix}"],
        frames=FRAMES_PER_ROW,
        speed_ms=SPEEDS[act],
        flip_x=(face == "left"),
    )


def weapon_sheet_for(held_item: Optional[str]) -> Optional[str]:
    """The weapon sheet stem for a held item id (None = bare hand)."""
    if not held_item:
        return None
    return WEAPON_SHEETS.get(held_item)


def layer_manifest(manifest: dict, held_item: Optional[str]) -> Optional[dict]:
    """The weapon layer entry from the manifest for a held item."""
    stem = weapon_sheet_for(held_item)
    if stem is None:
        return None
    return manifest.get("weapons", {}).get(stem)
