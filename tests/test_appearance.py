"""Pure appearance mapping tests (game/appearance.py)."""

from game.appearance import (
    ANIM_ROWS,
    SPEEDS,
    anim_state,
    layer_manifest,
    load_manifest,
    weapon_sheet_for,
)


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
    assert weapon_sheet_for("apple") is None


def test_manifest_files_exist():
    m = load_manifest()
    assert m["base"]["frame_w"] == 32
    for stem, entry in m["weapons"].items():
        assert entry["file"].startswith("weapon/")
