import pathlib
import sys
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pathlib import Path

from game.manager import GameManager
from rendering.hub_renderer import HubRenderer
from rendering.hub_renderer import (
    CLOCK_MARGIN, CLOCK_SCALE, CLOCK_GAP,
    ICON_GAP, ICON_SIZE,
    SUN_COLOR, MOON_COLOR, BG,
)
from rendering.renderer import finalize_for_upload, screen_internal_size, MAP_UPLOAD_SCALE
import rendering.hub_renderer as hub_mod

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "maps"


def _display_size(rt, target_internal_w=None):
    renderer = HubRenderer(ASSETS)
    result = renderer.render(
        rt, rt.map_data, rt.npc_map, target_internal_w=target_internal_w
    )
    return finalize_for_upload(result.image).size


def test_hub_renderer_produces_image():
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    # Default width: displayed hub is 420x66 (vertical height reduced by 50%).
    assert _display_size(rt) == (420, 66)


def test_hub_width_matches_screen_display_width():
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    # Forced internal width is mirrored exactly after the 0.5 upload shrink,
    # so the hub's displayed width equals the screen's displayed width.
    assert _display_size(rt, target_internal_w=1344) == (672, 66)
    assert _display_size(rt, target_internal_w=600) == (300, 66)


def test_hub_exact_match_with_screen_internal_size():
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    sw, _ = screen_internal_size(rt, 32)
    hub_w, hub_h = _display_size(rt, target_internal_w=sw)
    screen_w = int(sw * MAP_UPLOAD_SCALE)
    assert hub_w == screen_w
    assert hub_h == 66


# --- Day/night icon tests ---

def _make_rt():
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    return rt


def _icon_pos(internal_w=840, internal_h=132, text_len=5):
    """Top-left pixel of the day/night icon for the default clock layout
    (mirrors HubRenderer._draw_clock: bottom-aligned with the digits)."""
    total = text_len * (3 * CLOCK_SCALE + CLOCK_GAP) - CLOCK_GAP
    x = internal_w - CLOCK_MARGIN - total
    y = internal_h - CLOCK_MARGIN - 5 * CLOCK_SCALE
    icon_x = x - ICON_GAP - ICON_SIZE
    icon_y = y + 5 * CLOCK_SCALE - ICON_SIZE
    return icon_x, icon_y


def _icon_pixels(result, frame, ix, iy):
    """Pixel list inside the icon rect of a rendered GIF frame."""
    icon = result.frames[frame].crop((ix, iy, ix + ICON_SIZE, iy + ICON_SIZE))
    return [p[:3] for p in icon.getdata()]


def _count_color(result, frame, ix, iy, color):
    """How many pixels of *color* the icon paints in a rendered frame."""
    return sum(1 for p in _icon_pixels(result, frame, ix, iy) if p == color)


def test_daynight_phase_morning():
    with patch.object(hub_mod, 'ingame_seconds', return_value=8 * 3600):
        renderer = HubRenderer(ASSETS)
        assert renderer._daynight_phase() == "morning"


def test_daynight_phase_day():
    with patch.object(hub_mod, 'ingame_seconds', return_value=12 * 3600):
        renderer = HubRenderer(ASSETS)
        assert renderer._daynight_phase() == "day"


def test_daynight_phase_evening():
    with patch.object(hub_mod, 'ingame_seconds', return_value=18 * 3600):
        renderer = HubRenderer(ASSETS)
        assert renderer._daynight_phase() == "evening"


def test_daynight_phase_night():
    with patch.object(hub_mod, 'ingame_seconds', return_value=3 * 3600):
        renderer = HubRenderer(ASSETS)
        assert renderer._daynight_phase() == "night"


def test_icon_sun_drawn_in_morning():
    with patch.object(hub_mod, 'ingame_seconds', return_value=8 * 3600):
        renderer = HubRenderer(ASSETS)
        rt = _make_rt()
        result = renderer.render(rt, rt.map_data, rt.npc_map)
        ix, iy = _icon_pos()
        # Morning: the rising-sun icon (gold) must be inside the icon rect.
        assert _count_color(result, 0, ix, iy, SUN_COLOR) > 0


def test_icon_sun_drawn_in_day():
    with patch.object(hub_mod, 'ingame_seconds', return_value=12 * 3600):
        renderer = HubRenderer(ASSETS)
        rt = _make_rt()
        result = renderer.render(rt, rt.map_data, rt.npc_map)
        ix, iy = _icon_pos()
        # Day: the noon-sun icon (gold) must be inside the icon rect.
        assert _count_color(result, 0, ix, iy, SUN_COLOR) > 0


def test_icon_sun_drawn_in_evening():
    with patch.object(hub_mod, 'ingame_seconds', return_value=18 * 3600):
        renderer = HubRenderer(ASSETS)
        rt = _make_rt()
        result = renderer.render(rt, rt.map_data, rt.npc_map)
        ix, iy = _icon_pos()
        # Evening: the sunset-on-horizon icon (gold) must be in the rect.
        assert _count_color(result, 0, ix, iy, SUN_COLOR) > 0


def test_icon_moon_drawn_in_night():
    with patch.object(hub_mod, 'ingame_seconds', return_value=3 * 3600):
        renderer = HubRenderer(ASSETS)
        rt = _make_rt()
        result = renderer.render(rt, rt.map_data, rt.npc_map)
        ix, iy = _icon_pos()
        # Night: the moon icon (silver) must be inside the icon rect.
        assert _count_color(result, 0, ix, iy, MOON_COLOR) > 0


def test_icon_switches_between_periods():
    renderer = HubRenderer(ASSETS)
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    ix, iy = _icon_pos()

    def _icon_at(hour):
        with patch.object(hub_mod, 'ingame_seconds', return_value=hour * 3600):
            return renderer.render(rt, rt.map_data, rt.npc_map)

    # One fixed icon per "buổi": noon (full sun) and night (moon) must
    # draw different pixels in the icon slot.
    noon = _icon_at(12).frames[0].crop((ix, iy, ix + ICON_SIZE, iy + ICON_SIZE))
    night = _icon_at(3).frames[0].crop((ix, iy, ix + ICON_SIZE, iy + ICON_SIZE))
    assert list(noon.getdata()) != list(night.getdata())


def test_icon_drawn_without_players():
    with patch.object(hub_mod, 'ingame_seconds', return_value=12 * 3600):
        renderer = HubRenderer(ASSETS)
        mgr = GameManager(ASSETS)
        rt = mgr.create_runtime(1, "test-map")
        # No players added — icon should still appear.
        result = renderer.render(rt, rt.map_data, rt.npc_map)
        ix, iy = _icon_pos()
        assert _count_color(result, 0, ix, iy, SUN_COLOR) > 0


# --- Emblem avatar badge (player's chosen avatar on the hub) ---

def _emblem_rect(internal_w=840, internal_h=132):
    """Pixel rect of the first player-widget's emblem slot (0,0 origin of the
    widget, which sits top-left of the hub's player row)."""
    k = max(1, min(internal_h // 34, (internal_w - 0) // 140))
    unit_h = 34 * k
    y = (internal_h - unit_h) // 2
    return (0, y, 32 * k, y + 34 * k)


def test_emblem_badge_drawn_when_avatar_given():
    from PIL import Image as PILImage

    renderer = HubRenderer(ASSETS)
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    avatar = PILImage.new("RGBA", (28, 28), (255, 10, 10, 255))
    with_av = renderer.render(rt, rt.map_data, rt.npc_map, avatars={10: avatar})
    without_av = renderer.render(rt, rt.map_data, rt.npc_map)
    ex0, ey0, ex1, ey1 = _emblem_rect()
    a = with_av.frames[0].crop((ex0, ey0, ex1, ey1))
    b = without_av.frames[0].crop((ex0, ey0, ex1, ey1))
    assert list(a.getdata()) != list(b.getdata())  # badge visibly drawn
    # Strong red from the test avatar made it into the emblem area.
    reds = [p for p in a.convert("RGB").getdata() if p[0] > 200 and p[1] < 90]
    assert reds, "avatar badge red pixels expected inside emblem rect"


def test_emblem_badge_gold_ring_present():
    from PIL import Image as PILImage

    renderer = HubRenderer(ASSETS)
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    avatar = PILImage.new("RGBA", (28, 28), (20, 20, 20, 255))
    res = renderer.render(rt, rt.map_data, rt.npc_map, avatars={10: avatar})
    ex0, ey0, ex1, ey1 = _emblem_rect()
    crop = res.frames[0].crop((ex0, ey0, ex1, ey1)).convert("RGB")
    golds = [p for p in crop.getdata() if p[0] > 200 and p[1] > 160 and p[2] < 140]
    assert golds, "gold ring pixels expected around the badge"


def test_emblem_badge_skipped_without_avatar():
    renderer = HubRenderer(ASSETS)
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 3, 3)
    res_plain = renderer.render(rt, rt.map_data, rt.npc_map)
    res_empty = renderer.render(rt, rt.map_data, rt.npc_map, avatars={})
    assert list(res_plain.frames[0].getdata()) == list(res_empty.frames[0].getdata())
