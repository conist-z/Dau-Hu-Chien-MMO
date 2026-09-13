"""Async weather fetch service (rule #21: never block the event loop).

Open-Meteo primary source: one call carries all 14 VN stations
(multi-coordinate), no API key, CC BY 4.0 attribution required.

On ANY failure we return WeatherState.fallback_clear() so the game keeps
running with clear weather instead of crashing (graceful degradation).
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import List, Optional

import aiohttp

from game.weather import StationReading, WeatherState

log = logging.getLogger("GAME")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_CURRENT_FIELDS = "weather_code,temperature_2m,precipitation,cloud_cover,wind_speed_10m,is_day"


class WeatherService:
    def __init__(
        self,
        assets_dir: Path,
        enabled: bool = True,
        refresh_interval: float = 900.0,
    ):
        self.enabled = enabled
        self.refresh_interval = refresh_interval
        self._stations = self._load_stations(assets_dir)
        self._session: Optional[aiohttp.ClientSession] = None

    @staticmethod
    def _load_stations(assets_dir: Path) -> List[dict]:
        path = assets_dir.parent / "weather" / "vn_stations.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning("[WEATHER] could not load vn_stations.json: %s", e)
            return []

    def url(self) -> str:
        lats = ",".join(str(s["lat"]) for s in self._stations)
        lons = ",".join(str(s["lon"]) for s in self._stations)
        return (
            f"{OPEN_METEO_URL}?latitude={lats}&longitude={lons}"
            f"&current={_CURRENT_FIELDS}&timezone=Asia%2FHo_Chi_Minh"
        )

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def fetch(self, now: float = 0.0) -> WeatherState:
        if not self.enabled or not self._stations:
            return WeatherState.fallback_clear(now)
        try:
            session = await self._ensure_session()
            async with session.get(self.url()) as resp:
                resp.raise_for_status()
                data = await resp.json()
            return self._parse(data, now)
        except Exception as e:
            log.warning("[WEATHER] fetch failed, falling back to clear: %s", e)
            return WeatherState.fallback_clear(now)

    def _parse(self, data: dict, now: float) -> WeatherState:
        # Open-Meteo returns a JSON LIST (one object per requested coordinate)
        # when multiple lat/lon are supplied; a single dict only for one coord.
        locs = data if isinstance(data, list) else [data]
        stations: dict = {}
        for i, st in enumerate(self._stations):
            cur = {}
            if i < len(locs):
                cur = locs[i].get("current", {}) or {}
            stations[st["id"]] = StationReading(
                station_id=st["id"],
                code=_as_int(cur.get("weather_code", []), 0) if isinstance(cur.get("weather_code"), list) else int(cur.get("weather_code", 0) or 0),
                temp=_as_float(cur.get("temperature_2m", []), 0) if isinstance(cur.get("temperature_2m"), list) else float(cur.get("temperature_2m", 0) or 0),
                precip=_as_float(cur.get("precipitation", []), 0) if isinstance(cur.get("precipitation"), list) else float(cur.get("precipitation", 0) or 0),
                cloud=_as_float(cur.get("cloud_cover", []), 0) if isinstance(cur.get("cloud_cover"), list) else float(cur.get("cloud_cover", 0) or 0),
                wind=_as_float(cur.get("wind_speed_10m", []), 0) if isinstance(cur.get("wind_speed_10m"), list) else float(cur.get("wind_speed_10m", 0) or 0),
                is_day=bool(_as_int(cur.get("is_day", []), 0) if isinstance(cur.get("is_day"), list) else int(cur.get("is_day", 0) or 0)),
            )
        ws = WeatherState(stations=stations, fetched_at=now, ok=True)
        # Sample a varied display key (deterministic per fetch via seed); real
        # gameplay modifiers still follow the true national ratios dominant.
        ws.weather_key = ws.sample_key(random.Random(now))
        return ws


def _as_int(arr, i) -> int:
    try:
        return int(arr[i])
    except (TypeError, IndexError, ValueError):
        return 0


def _as_float(arr, i) -> float:
    try:
        return float(arr[i])
    except (TypeError, IndexError, ValueError):
        return 0.0
