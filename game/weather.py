"""Pure weather logic: classification, national ratios, and weather state.

No discord.py, no network I/O. Safe to unit-test without egress.

Mapping priority (Vietnamese national weather, avoids wasting icon types):
  storm codes (95,96,99)      -> storm        (highest drama, wins ties)
  precip codes (51-67,80-82)  -> rain/heavy_rain
  snow codes (71-77,85,86)    -> snow
  temp <= 5C                  -> snow         (too cold == snow, not "cold")
  5C < temp <= 13C            -> cold         (mild chill, still a real type)
  fog (45,48)                 -> heavy_clouds
  cloud bucket (0,1,2,3)      -> sunny/sun_clouds/cloudy/heavy_clouds
  wind >= 25 km/h             -> wind         (overrides calm sky)
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# WMO weather_code buckets.
_STORM_CODES = {95, 96, 99}
_RAIN_CODES = set(range(51, 68)) | {80, 81, 82}
_SNOW_CODES = set(range(71, 78)) | {85, 86}
_FOG_CODES = {45, 48}

# Tie-break priority for dominant_key (higher == more "interesting").
_KEY_PRIORITY = {
    "storm": 9,
    "snow": 8,
    "heavy_rain": 7,
    "rain": 6,
    "wind": 5,
    "heavy_clouds": 4,
    "cold": 3,
    "cloudy": 2,
    "sun_clouds": 1,
    "sunny": 0,
}

COLD_SNOW_C = 5.0       # at/below this -> snow (not "cold")
COLD_MILD_C = 13.0      # at/below this (and > snow) -> cold
WINDY_KMH = 25.0        # at/above this -> wind
HEAVY_RAIN_PRECIP = 1.0  # mm; above this -> heavy_rain


@dataclass
class StationReading:
    station_id: str
    code: int
    temp: float
    precip: float
    cloud: float
    wind: float
    is_day: bool


@dataclass
class WeatherModifiers:
    coin_mult: float = 1.0
    mana_regen_mult: float = 1.0
    hp_regen_mult: float = 1.0
    xp_mult: float = 1.0
    storm_event: bool = False


@dataclass
class WeatherState:
    """Latest national weather snapshot for one scenario."""

    stations: Dict[str, StationReading] = field(default_factory=dict)
    weather_key: str = "sun_clouds"
    fetched_at: float = 0.0
    ok: bool = True

    @classmethod
    def fallback_clear(cls, fetched_at: float = 0.0) -> "WeatherState":
        return cls(weather_key="sun_clouds", fetched_at=fetched_at, ok=False)

    def dominant_key(self) -> str:
        if not self.stations:
            return self.weather_key
        counts: Dict[str, int] = {}
        for r in self.stations.values():
            k = classify(r)
            counts[k] = counts.get(k, 0) + 1
        # Most frequent; tie -> most dramatic weather type.
        best = max(
            counts.items(),
            key=lambda kv: (kv[1], _KEY_PRIORITY.get(kv[0], 0)),
        )[0]
        return best

    def ratios(self) -> Dict[str, float]:
        n = len(self.stations)
        if n == 0:
            return {"rain_ratio": 0.0, "storm_ratio": 0.0, "cloud_ratio": 0.0, "heat_index": 0.0}
        rain_n = sum(
            1 for r in self.stations.values()
            if r.precip > 0.1 or r.code in _RAIN_CODES
        )
        storm_n = sum(1 for r in self.stations.values() if r.code in _STORM_CODES)
        cloud_mean = sum(r.cloud for r in self.stations.values()) / n
        temp_mean = sum(r.temp for r in self.stations.values()) / n
        heat = (temp_mean - 15.0) / 25.0
        heat = max(0.0, min(1.0, heat))
        return {
            "rain_ratio": rain_n / n,
            "storm_ratio": storm_n / n,
            "cloud_ratio": max(0.0, min(1.0, cloud_mean / 100.0)),
            "heat_index": heat,
        }

    def is_day_ratio(self) -> float:
        """Fraction of national stations currently in daylight (0..1)."""
        if not self.stations:
            return 0.0
        return sum(1 for r in self.stations.values() if r.is_day) / len(self.stations)

    def _day_only_keys(self) -> set:
        return {"sunny", "sun_clouds"}

    def sample_key(self, rng: Optional[random.Random] = None) -> str:
        """Weighted-random weather key for the HUD.

        Keeps the display varied instead of freezing on the national dominant
        type. Weights are shaped by the real ratios, so conditions still bias
        the result (a cloudy nation still leans cloudy/sunny-stormy), but every
        type remains possible. Deterministic per fetch via a seeded RNG when
        provided (no mutable global state — rule #15).

        Night rule: when the nation is predominantly night (is_day_ratio < 0.5)
        the day-only keys (sunny/sun_clouds) are suppressed so an evening HUD
        never shows sunshine (no star asset exists, so night clear falls back to
        cloudy/heavy_clouds instead).
        """
        if not self.ok or not self.stations:
            return "sun_clouds"
        r = self.ratios()
        storm = r["storm_ratio"]
        weights = {
            "storm": storm,
            "rain": r["rain_ratio"],
            "heavy_rain": r["rain_ratio"] * 0.5,
            "heavy_clouds": r["cloud_ratio"],
            "cloudy": r["cloud_ratio"] * 0.6 + 0.1,
            "sun_clouds": (1.0 - r["cloud_ratio"]) * 0.6 + 0.2,
            "sunny": (1.0 - r["cloud_ratio"]) * 0.4,
            "snow": 0.05,
            "cold": max(0.0, 0.3 - r["heat_index"]),
            "wind": 0.15,
        }
        if self.is_day_ratio() < 0.5:
            for day_key in self._day_only_keys():
                weights[day_key] = 0.0
        rng = rng or random.Random(self.fetched_at)
        keys, vals = zip(*weights.items())
        chosen = rng.choices(keys, weights=vals, k=1)[0]
        # Guarantee we never surface a day-only key after dark (no star asset).
        if chosen in self._day_only_keys() and self.is_day_ratio() < 0.5:
            chosen = "cloudy"
        return chosen


def classify(r: StationReading) -> str:
    if r.code in _STORM_CODES:
        return "storm"
    if r.code in _SNOW_CODES:
        return "snow"
    if r.code in _RAIN_CODES:
        return "heavy_rain" if r.precip > HEAVY_RAIN_PRECIP else "rain"
    if r.temp <= COLD_SNOW_C:
        # Too cold -> snow (Vietnam only really sees this in the mountains).
        return "snow"
    if r.code in _FOG_CODES:
        return "heavy_clouds"
    if r.wind >= WINDY_KMH:
        return "wind"
    if r.temp <= COLD_MILD_C:
        return "cold"
    if r.code == 0:
        return "sunny"
    if r.code == 1:
        return "sun_clouds"
    if r.code == 2:
        return "cloudy"
    if r.code == 3:
        return "heavy_clouds"
    # Unknown code -> fall back to cloud cover heuristic.
    if r.cloud >= 80:
        return "heavy_clouds"
    if r.cloud >= 40:
        return "cloudy"
    return "sun_clouds"


def compute_modifiers(ws: WeatherState) -> WeatherModifiers:
    """Light gameplay modifiers from the national snapshot (clamped ±20-50%)."""
    ratios = ws.ratios()
    key = ws.dominant_key()
    m = WeatherModifiers()

    if key in ("sunny", "sun_clouds"):
        m.coin_mult = 1.1                       # clear -> small coin bonus
    if key in ("rain", "heavy_rain"):
        m.mana_regen_mult = 1.2                 # rain -> mana regen up
    if key == "storm" and ratios["storm_ratio"] > 0.4:
        m.xp_mult = 1.4                         # server-wide storm event
        m.storm_event = True
    if ratios["heat_index"] > 0.6:
        m.hp_regen_mult = 1.2                   # heat -> hp regen up

    # Safety clamps: nothing swings beyond +50% / below +0%.
    m.coin_mult = max(1.0, min(1.5, m.coin_mult))
    m.mana_regen_mult = max(1.0, min(1.5, m.mana_regen_mult))
    m.hp_regen_mult = max(1.0, min(1.5, m.hp_regen_mult))
    m.xp_mult = max(1.0, min(1.5, m.xp_mult))
    return m
