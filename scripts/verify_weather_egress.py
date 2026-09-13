"""Verify Open-Meteo network egress works from the host (rules #1/#21).

Run locally: .venv\\Scripts\\python scripts/verify_weather_egress.py
Prints the parsed WeatherState ratios + dominant key and asserts non-empty.
"""
from __future__ import annotations

import asyncio
import sys
import time

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from game.weather_service import WeatherService  # noqa: E402

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "maps"


async def main() -> int:
    svc = WeatherService(ASSETS, enabled=True, refresh_interval=900.0)
    print(f"[egress] {svc.url()}")
    ws = await svc.fetch(time.time())
    await svc.close()
    print(f"[egress] ok={ws.ok} stations={len(ws.stations)} dominant={ws.dominant_key()}")
    print(f"[egress] ratios={ws.ratios()}")
    if not ws.stations:
        print("[egress] FAIL: no stations parsed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
