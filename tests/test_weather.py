from game.weather import (
    StationReading,
    WeatherModifiers,
    WeatherState,
    classify,
    compute_modifiers,
)
from game.rules import apply_weather_regen
from game.state import Player


def _reading(code=0, temp=25.0, precip=0.0, cloud=10.0, wind=5.0, is_day=True):
    return StationReading("X", code, temp, precip, cloud, wind, is_day)


def test_classify_buckets():
    assert classify(_reading(code=0)) == "sunny"
    assert classify(_reading(code=1)) == "sun_clouds"
    assert classify(_reading(code=2)) == "cloudy"
    assert classify(_reading(code=3)) == "heavy_clouds"
    assert classify(_reading(code=45)) == "heavy_clouds"  # fog
    assert classify(_reading(code=61, precip=0.5)) == "rain"
    assert classify(_reading(code=65, precip=3.0)) == "heavy_rain"
    assert classify(_reading(code=95)) == "storm"
    assert classify(_reading(code=75)) == "snow"
    # User tweak: too cold -> snow (not "cold") to avoid wasting the snow type.
    assert classify(_reading(temp=2.0)) == "snow"
    # Mild chill still uses the cold type.
    assert classify(_reading(temp=10.0, code=1)) == "cold"
    # Windy -> wind overrides a calm sky.
    assert classify(_reading(code=1, wind=30.0)) == "wind"


def test_dominant_key_tiebreak():
    ws = WeatherState(
        stations={
            "a": _reading(code=0),
            "b": _reading(code=95),
        }
    )
    # Equal counts -> storm wins via priority (more dramatic weather shown).
    assert ws.dominant_key() == "storm"


def test_ratios_math():
    ws = WeatherState(
        stations={
            "a": _reading(code=65, precip=2.0, cloud=90.0, temp=30.0),  # rain+cloud+hot
            "b": _reading(code=95, cloud=100.0, temp=28.0),             # storm+cloud
            "c": _reading(code=0, cloud=0.0, temp=20.0),               # clear
        }
    )
    r = ws.ratios()
    assert abs(r["rain_ratio"] - 1 / 3) < 1e-9
    assert abs(r["storm_ratio"] - 1 / 3) < 1e-9
    assert abs(r["cloud_ratio"] - (190 / 3) / 100) < 1e-9
    # mean temp = (30+28+20)/3 = 26 -> heat (26-15)/25 = 0.44
    assert abs(r["heat_index"] - 0.44) < 1e-9


def test_fallback_clear():
    ws = WeatherState.fallback_clear()
    assert ws.weather_key == "sun_clouds"
    assert ws.ok is False
    assert ws.ratios()["rain_ratio"] == 0.0


def test_modifiers_clamped_and_typed():
    clear = WeatherState(stations={"a": _reading(code=0)})
    m: WeatherModifiers = compute_modifiers(clear)
    assert m.coin_mult == 1.1
    assert m.mana_regen_mult == 1.0

    rain = WeatherState(stations={"a": _reading(code=63, precip=1.0)})
    assert compute_modifiers(rain).mana_regen_mult == 1.2

    hot = WeatherState(
        stations={"a": _reading(code=0, temp=40.0), "b": _reading(code=0, temp=40.0)}
    )
    assert compute_modifiers(hot).hp_regen_mult == 1.2

    storm = WeatherState(
        stations={
            "a": _reading(code=95),
            "b": _reading(code=95),
            "c": _reading(code=0),
        }
    )
    sm = compute_modifiers(storm)
    assert sm.storm_event is True
    assert sm.xp_mult == 1.4

    # Nothing can exceed +50%.
    for mod in (m, compute_modifiers(rain), compute_modifiers(hot), sm):
        assert mod.coin_mult <= 1.5
        assert mod.mana_regen_mult <= 1.5
        assert mod.hp_regen_mult <= 1.5


def test_apply_weather_regen_mutates_player():
    p = Player(user_id=1, display_name="t", mana=0, max_mana=50)
    rain = WeatherState(stations={"a": _reading(code=63, precip=1.0)})
    apply_weather_regen(p, rain)
    assert p.mana > 0  # rain gave mana regen
    assert p.mana <= p.max_mana


def test_sample_key_is_valid_and_seeded():
    import random

    ws = WeatherState(
        stations={
            "a": _reading(code=0),
            "b": _reading(code=1),
            "c": _reading(code=95),
        },
        fetched_at=1788123147.0,
    )
    valid = {
        "sunny", "sun_clouds", "cloudy", "heavy_clouds",
        "rain", "heavy_rain", "snow", "storm", "cold", "wind",
    }
    k = ws.sample_key(random.Random(1788123147.0))
    assert k in valid
    # Same seed -> same key (deterministic per fetch).
    assert ws.sample_key(random.Random(1788123147.0)) == k


def test_sample_key_fallback_when_broken():
    ws = WeatherState.fallback_clear()
    assert ws.sample_key() == "sun_clouds"


def test_night_suppresses_sunny_keys():
    import random

    # All stations night (is_day=False) -> never sunny/sun_clouds.
    ws = WeatherState(
        stations={
            "a": _reading(code=0, is_day=False),
            "b": _reading(code=1, is_day=False),
            "c": _reading(code=2, is_day=False),
            "d": _reading(code=0, is_day=False),
        },
        fetched_at=100.0,
        ok=True,
    )
    assert ws.is_day_ratio() == 0.0
    for _ in range(200):
        k = ws.sample_key(random.Random())
        assert k not in ("sunny", "sun_clouds")
