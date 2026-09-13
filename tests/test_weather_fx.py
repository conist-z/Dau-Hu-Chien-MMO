"""Tests for the weather FX overlay (CraftPix pack adapted to top-down)."""
import asyncio
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from config import ASSETS_DIR
from game.manager import GameManager
from rendering.avatar import AvatarCache
from rendering.renderer import Renderer, encode_upload_gif
from rendering.weather_fx import ANIMATED_KEYS, WeatherFx, _STYLES

PROJECT_ROOT = ASSETS_DIR.parent


def _new_renderer():
    return Renderer(ASSETS_DIR, AvatarCache(use_network=False))


def _tiny_png(path: Path, size, color):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def _fake_fx_root(tmp_path: Path) -> Path:
    """An assets dir whose sibling fx/ carries tiny synthetic frames."""
    root = tmp_path / "assets" / "maps"
    root.mkdir(parents=True)
    fx = tmp_path / "assets" / "fx"
    for i in range(10):
        _tiny_png(fx / "rain" / f"rain_{i:02d}.png", (8, 4), (80, 120, 255, 140))
    _tiny_png(fx / "snow" / "snow_tile.png", (8, 4), (240, 240, 255, 200))
    for i in range(4):
        _tiny_png(fx / "wind" / f"wind_{i:02d}.png", (16, 4), (255, 255, 255, 120))
    _tiny_png(fx / "thunder" / "bolt_00.png", (6, 12), (255, 255, 255, 255))
    return root


# ---------------------------------------------------------------- pure logic


def test_is_animated_keys():
    for k in ("rain", "heavy_rain", "snow", "cold", "wind", "storm"):
        assert WeatherFx.is_animated(k)
    for k in ("sunny", "sun_clouds", "cloudy", "heavy_clouds", None):
        assert not WeatherFx.is_animated(k)
    assert ANIMATED_KEYS  # sanity


def test_missing_assets_still_draw_overlays(tmp_path):
    """Particles are drawn (seeded), so overlays survive even when the pack
    sheets are absent; ``available()`` still reports the missing assets."""
    root = tmp_path / "assets" / "maps"
    root.mkdir(parents=True)
    fx = WeatherFx(root)
    assert not fx.available()
    frames = fx.build_overlays("rain", (64, 48), n=6)
    assert len(frames) == 6
    assert all(ov.size == (64, 48) for ov in frames)


def test_rain_overlays_deterministic(tmp_path):
    fx = WeatherFx(_fake_fx_root(tmp_path))
    a = fx.build_overlays("rain", (64, 48), n=6, seed=3)
    b = fx.build_overlays("rain", (64, 48), n=6, seed=3)
    assert len(a) == 6
    assert all(ov.size == (64, 48) for ov in a)
    assert [ov.tobytes() for ov in a] == [ov.tobytes() for ov in b]
    # Frames actually carry particles (some non-transparent pixel).
    assert any(ov.getextrema()[3][1] > 0 for ov in a)


def test_weather_layers_are_independent_and_have_distinct_density(tmp_path):
    fx = WeatherFx(_fake_fx_root(tmp_path))
    near, far = fx._get_masters("rain", _STYLES["rain"])
    assert near.tobytes() != far.tobytes()
    assert near.getchannel("A").getbbox() is not None
    assert far.getchannel("A").getbbox() is not None


def test_entity_region_is_protected_from_weather_overlay():
    overlay = Image.new("RGBA", (32, 32), (220, 240, 255, 180))
    protected = _new_renderer()._weather_overlay_behind_entities(
        overlay, [(8, 8, 16, 16)]
    )
    assert protected.getpixel((0, 0))[3] == 180
    assert protected.getpixel((16, 16))[3] == 0


def test_overlay_cache_reuses_same_viewport(tmp_path):
    fx = WeatherFx(_fake_fx_root(tmp_path))
    with patch.object(fx, "_scroll_overlays", wraps=fx._scroll_overlays) as build:
        first = fx.build_overlays("rain", (64, 48), n=6)
        second = fx.build_overlays("rain", (64, 48), n=6)

    assert build.call_count == 1
    assert [frame.tobytes() for frame in first] == [
        frame.tobytes() for frame in second
    ]


def test_storm_bolt_requires_seed(tmp_path):
    """Storm without a lightning seed = pure rain loop (no flashbang).
    A seed spawns exactly one strike; the strike frame glows brighter."""
    fx = WeatherFx(_fake_fx_root(tmp_path))
    calm = fx.build_overlays("storm", (64, 48), n=6, seed=0)
    again = fx.build_overlays("storm", (64, 48), n=6, seed=0)
    assert len(calm) == 4  # storm caps to 4 frames (smaller GIF)
    assert [f.tobytes() for f in calm] == [f.tobytes() for f in again]

    strike = fx.build_overlays("storm", (64, 48), n=6, seed=12345)
    assert len(strike) == 4

    def _lum(img):
        return max(img.convert("RGB").getdata(), key=lambda p: sum(p))

    peak_calm = max(sum(_lum(f)) for f in calm)
    peak_strike = max(sum(_lum(f)) for f in strike)
    assert peak_strike > peak_calm
    # A different seed strikes differently (not the same static bolt).
    other = fx.build_overlays("storm", (64, 48), n=6, seed=999)
    assert [f.tobytes() for f in other] != [f.tobytes() for f in strike]


# ------------------------------------------------------------ real assets


def test_real_fx_assets_present():
    fx_root = PROJECT_ROOT / "fx"
    assert len(list((fx_root / "rain").glob("rain_*.png"))) == 10
    assert (fx_root / "snow" / "snow_tile.png").is_file()
    assert len(list((fx_root / "wind").glob("wind_*.png"))) == 4
    bolts = list((fx_root / "thunder").glob("bolt_*.png"))
    assert bolts, "thunder columns were all dropped by the converter"


def test_real_overlays_all_keys():
    fx = WeatherFx(ASSETS_DIR)
    assert fx.available()
    for key in ("rain", "heavy_rain", "snow", "cold", "wind", "storm"):
        frames = fx.build_overlays(key, (128, 96), n=6, seed=1)
        expected = 4 if key in ("heavy_rain", "storm") else 12 if key in ("snow", "cold") else 6
        assert len(frames) == expected, key
        assert all(fr.size == (128, 96) for fr in frames), key
        assert all(fr.mode == "RGBA" for fr in frames), key


# ------------------------------------------------------- renderer pipeline


def _runtime(channel=777001):
    mgr = GameManager(ASSETS_DIR)
    return mgr.create_runtime(channel, "test-map")


def test_renderer_animated_weather_returns_gif_result():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    result = asyncio.run(
        _new_renderer().render(rt.state, rt.map_data, weather_key="rain")
    )
    assert result.frames is not None and len(result.frames) == 6
    assert result.filename == "map.gif"
    assert result.content_type == "image/gif"
    # Composite stays overlay-free so incremental caching keeps working.
    assert result.composite is not None


def test_renderer_static_weather_stays_png():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    result = asyncio.run(
        _new_renderer().render(rt.state, rt.map_data, weather_key="sunny")
    )
    assert result.frames is None
    assert result.filename == "map.png"
    assert result.content_type == "image/png"


def test_rain_moves_downward(tmp_path):
    """Regression guard: frame i+1 must be frame i scrolled DOWN — particles
    fall toward the ground, never upward."""
    from rendering.weather_fx import _STYLES

    fx = WeatherFx(_fake_fx_root(tmp_path))
    frames = fx.build_overlays("rain", (64, 64), n=6)
    dy = _STYLES["rain"].speed
    a0 = frames[0].getchannel("A")
    a1 = frames[1].getchannel("A")
    top = a0.crop((0, 0, 64, 64 - dy))     # upper part of frame 0
    bot = a1.crop((0, dy, 64, 64))         # same content, shifted down in frame 1
    assert top.tobytes() == bot.tobytes()


def test_encode_upload_gif_produces_gif():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    result = asyncio.run(
        _new_renderer().render(rt.state, rt.map_data, weather_key="snow")
    )
    data = encode_upload_gif(result.frames, result.duration_ms)
    assert data[:3] == b"GIF"


def test_renderer_fx_seed_changes_storm_strike():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    renderer = _new_renderer()
    calm = asyncio.run(
        renderer.render(rt.state, rt.map_data, weather_key="storm")
    )
    strike = asyncio.run(
        renderer.render(rt.state, rt.map_data, weather_key="storm", fx_seed=4242)
    )
    assert calm.frames and strike.frames
    assert [f.tobytes() for f in strike.frames] != [f.tobytes() for f in calm.frames]
