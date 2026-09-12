"""Pure appearance mapping tests (game/appearance.py)."""

import json
from pathlib import Path

from game.appearance import (
    ANIM_ROWS,
    SPEEDS,
    WEAPON_SHEETS,
    anim_state,
    layer_manifest,
    load_manifest,
    weapon_sheet_for,
)

_ROOT = Path(__file__).resolve().parent.parent

# Generated held-item sheets (scripts/make_held_sheet.py) — keep this list in
# sync with WEAPON_SHEETS and web_client/src/appearance_client.ts.
GENERATED_HELD = {
    "apple": "apple",
    "stone": "stone",
    "wood": "wood",
    "leaves": "leaves",
    "torch": "torch",
    "stick": "stick",
    "plank": "plank",
    "coal": "coal",
    "iron_ore": "ironore",
    "iron_ingot": "ironbar",
    "coin": "coin",
    "cooked_meat": "cookedmeat",
    "rotten_flesh": "rottenflesh",
    "potion_hp": "potionhp",
}


def test_anim_rows_layout_matches_kaetram():
    assert ANIM_ROWS["idle_down"] == 0
    assert ANIM_ROWS["walk_right"] == 4
    assert ANIM_ROWS["atk_up"] == 8


def test_anim_state_basic():
    st = anim_state("walk", "down")
    assert st.row == ANIM_ROWS["walk_down"]
    assert st.speed_ms == SPEEDS["walk"]
    assert st.flip_x is False


def test_left_reuses_right_row_flipped():
    st = anim_state("atk", "left")
    assert st.row == ANIM_ROWS["atk_right"]
    assert st.flip_x is True


def test_unknown_inputs_fall_back_idle_down():
    st = anim_state("dance", "sideways")
    assert st.row == 0
    assert st.speed_ms == SPEEDS["idle"]
    assert st.flip_x is False


def test_weapon_sheet_mapping_complete():
    m = load_manifest()
    for item_id, stem in [
        ("dirt_sword", "coppersword"),
        ("wood_sword", "ironsword"),
        ("stone_sword", "goldsword"),
        ("wood_pickaxe", "ironpickaxe"),
        ("stone_shovel", "ancientshovel"),
    ]:
        assert weapon_sheet_for(item_id) == stem
        entry = layer_manifest(m, item_id)
        assert entry is not None
        assert entry["frame_w"] == 48 and entry["frame_h"] == 48


def test_bare_hand_has_no_weapon():
    assert weapon_sheet_for(None) is None
    assert weapon_sheet_for("") is None
    assert weapon_sheet_for("nonexistent_item") is None


def test_generated_held_sheets_mapped_and_present():
    """Every generated held item maps to a real manifest entry + PNG on disk."""
    m = load_manifest()
    for item_id, stem in GENERATED_HELD.items():
        assert weapon_sheet_for(item_id) == stem, item_id
        entry = layer_manifest(m, item_id)
        assert entry is not None, item_id
        assert entry["frame_w"] == 48 and entry["frame_h"] == 48
        assert entry["cols"] == 4 and entry["rows"] == 9
        png = _ROOT / "assets" / "players" / entry["file"]
        assert png.exists(), f"missing sheet {png}"
        from PIL import Image

        im = Image.open(png)
        assert im.size == (48 * 4, 48 * 9), (item_id, im.size)


def test_python_ts_mapping_in_sync():
    """The TS mirror in appearance_client.ts must list the same item ids."""
    ts = (_ROOT / "web_client" / "src" / "appearance_client.ts").read_text(
        encoding="utf-8"
    )
    for item_id in GENERATED_HELD:
        assert f'{item_id}: "' in ts, f"{item_id} missing from TS mirror"


def test_manifest_files_exist():
    m = load_manifest()
    assert m["base"]["frame_w"] == 32
    for stem, entry in m["weapons"].items():
        assert entry["file"].startswith("weapon/")
