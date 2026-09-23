"""PREVIEW HARNESS for the local game stack (extends scripts/_local_game_stack.py).

A remote-controllable test bench so a reviewer can exercise REAL server
mechanics from the Freebuff Preview tab without touching the Discord bot:

  preview_cmd frames (browser -> stack):
    {"type":"preview_cmd","cmd":"clock","value":21|12|"normal"}
        Pin/reset the accelerated in-game clock (set_ingame_time) — flips the
        night gate for zombies/meteors and the map tint in one move.
    {"type":"preview_cmd","cmd":"meteor","value":"here"|"rand"|"auto"|"off"}
        Summon a meteor at the player tile / 6-12 tiles away / toggle the
        20%-halving night scheduler on or off (auto = on).
    {"type":"preview_cmd","cmd":"weather","value":"rain|snow|wind|storm|...|normal"}
        Force rt.weather_key ("" = auto/normal).
    {"type":"preview_cmd","cmd":"zombies","value":"pack"|"none"}
        Spawn the full web pack around the player instantly, or despawn all.
    {"type":"preview_cmd","cmd":"map","value":"ekonia/overworld|forest|cave_area1"}
        Destroy + re-create the runtime on another map (fresh solo preview).
    {"type":"preview_cmd","cmd":"state"}
        Ask for a state dump -> answers a "preview_state" push (mobs, meteors,
        clock, weather) — the panel's status line.

Server -> browser pushes:
    {"type":"push","message":...}        reused as the preview log feed
    {"type":"preview_state",...}         the status dump above

Run:  .venv/Scripts/python scripts/_preview_stack.py   (port 8898, Ctrl+C to stop)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiohttp import web, WSMsgType  # noqa: E402

# Reuse the whole plumbing from the local stack (GameManager wiring, asset
# lane, welcome/snapshot pumps) — the harness only adds preview_cmd handling.
from scripts._local_game_stack import (  # noqa: E402
    DIST_DIR,
    LocalStack,
    PREVIEW_USER,
)
from web_api.snapshots import build_welcome  # noqa: E402

DEFAULT_MAP = "ekonia/overworld"
PREVIEW_PORT = int(os.environ.get("PREVIEW_PORT", "8898"))


class PreviewStack(LocalStack):
    def __init__(self) -> None:
        super().__init__()
        self.auto_meteor = True  # the scheduler rolls on by default (night)
        self.last_state: dict = {}

    # ---------------- preview commands ----------------

    async def _preview_cmd(self, cid: int, frame: dict) -> None:
        cmd = str(frame.get("cmd", ""))
        value = frame.get("value")
        uid = self.sessions[cid]["user_id"]
        ch = self.joined.get(cid)
        if cmd != "map" and ch is None:
            await self._send(cid, {"type": "push", "message": "[preview] Chưa vào map."})
            return
        rt = self.gm.get_runtime(ch) if ch is not None else None
        if cmd != "map" and rt is None:
            await self._send(cid, {"type": "push", "message": "[preview] Runtime mất."})
            return
        handler = {
            "clock": self._cmd_clock,
            "meteor": self._cmd_meteor,
            "weather": self._cmd_weather,
            "zombies": self._cmd_zombies,
            "map": self._cmd_map,
            "state": self._cmd_state,
        }.get(cmd)
        if handler is None:
            await self._send(cid, {"type": "push", "message": f"[preview] Lệnh lạ: {cmd}"})
            return
        try:
            await handler(cid, uid, rt, value)
        except Exception as exc:  # report, never swallow
            import traceback
            traceback.print_exc()
            await self._send(cid, {"type": "push", "message": f"[preview] Lỗi {cmd}: {exc!r}"})

    async def _cmd_clock(self, cid: int, uid: int, rt, value) -> None:
        from rendering.daynight import ingame_seconds, set_ingame_time

        presets = {
            "day": 12 * 3600, "noon": 12 * 3600,
            "dusk": 19 * 3600, "night": 21 * 3600,
            "midnight": 0, "dawn": 6 * 3600,
        }
        if value == "normal":
            set_ingame_time(None)
            await self._send(cid, {"type": "push", "message": "[preview] Giờ về chu kỳ tự nhiên."})
        elif isinstance(value, str) and value in presets:
            sec = set_ingame_time(presets[value])
            await self._send(cid, {"type": "push", "message": f"[preview] Giờ = {sec // 3600:02d}:{sec % 3600 // 60:02d}"})
        elif isinstance(value, (int, float)):
            sec = set_ingame_time(int(value) % 86400)
            await self._send(cid, {"type": "push", "message": f"[preview] Giờ = {sec // 3600:02d}:{sec % 3600 // 60:02d}"})
        else:
            s = ingame_seconds()
            await self._send(cid, {"type": "push", "message": f"[preview] Giờ hiện tại: {s // 3600:02d}:{s % 3600 // 60:02d}"})

    async def _cmd_meteor(self, cid: int, uid: int, rt, value) -> None:
        from game.meteors import summon as meteor_summon

        player = rt.state.get_player(uid)
        if player is None:
            await self._send(cid, {"type": "push", "message": "[preview] Chưa có player trên map."})
            return
        if value == "auto" or value == "off":
            self.auto_meteor = value == "auto"
            # Scheduler gate without touching game code: the per-night cap
            # blocks further spawns when reached; resetting the counter
            # re-arms the 20% roll.
            from game.meteors import MAX_METEORS_PER_NIGHT

            rt.meteors.felled_tonight = (
                0 if self.auto_meteor else MAX_METEORS_PER_NIGHT
            )
            await self._send(cid, {"type": "push", "message": f"[preview] Scheduler auto = {self.auto_meteor}"})
            return
        now = time.monotonic()
        px = getattr(player, "x_f", None)
        py = getattr(player, "y_f", None)
        if px is None or py is None:
            px, py = float(player.x) + 0.5, float(player.y) + 0.5
        tx, ty = int(px), int(py)
        if value == "rand":
            ang = random.uniform(0, 6.283185)
            dist = random.uniform(6.0, 12.0)
            tx = int(px + __import__("math").cos(ang) * dist)
            ty = int(py + __import__("math").sin(ang) * dist)
        w = rt.map_data.width
        h = rt.map_data.height
        m = meteor_summon(rt.meteors, now, tx, ty, rng=getattr(self.gm, "zombie_rng", random.Random()))
        m.tx = max(0, min(w - 1, m.tx))
        m.ty = max(0, min(h - 1, m.ty))
        where = "tại chỗ bạn đứng" if value != "rand" else f"({m.tx},{m.ty}) gần bạn"
        await self._send(cid, {"type": "push", "message": f"[preview] ☄️ Thiên thạch rơi {where} — 8s nữa!"})

    async def _cmd_weather(self, cid: int, uid: int, rt, value) -> None:
        if value in (None, "", "normal", "auto"):
            rt.weather_key = ""
            await self._send(cid, {"type": "push", "message": "[preview] Thời tiết về tự động."})
            return
        rt.weather_key = str(value)
        await self._send(cid, {"type": "push", "message": f"[preview] Thời tiết = {value}"})

    async def _cmd_zombies(self, cid: int, uid: int, rt, value) -> None:
        from game.zombies import iter_web_zombies, remove_web_zombie, web_spawn_one

        if value == "none":
            for z in iter_web_zombies(rt.state):
                remove_web_zombie(rt.state, z.id)
            await self._send(cid, {"type": "push", "message": "[preview] Đã dọn sạch quái."})
            return
        player = rt.state.get_player(uid)
        if player is None:
            await self._send(cid, {"type": "push", "message": "[preview] Chưa có player trên map."})
            return
        rng = getattr(self.gm, "zombie_rng", random.Random())
        made = 0
        for _ in range(10):
            z = web_spawn_one(rt.state, rt.collision, [player], rng)
            if z is not None:
                made += 1
        await self._send(cid, {"type": "push", "message": f"[preview] Spawn {made} quái xung quanh ({rt.map_data.map_id})."})

    async def _cmd_map(self, cid: int, uid: int, rt, value) -> None:
        map_id = str(value or DEFAULT_MAP)
        allowed = ("ekonia/overworld", "ekonia/forest", "ekonia/cave_area1")
        if map_id not in allowed:
            await self._send(cid, {"type": "push", "message": f"[preview] Map không hỗ trợ: {map_id}"})
            return
        ch = self.joined.get(cid)
        if ch is not None:
            self.gm.remove_runtime(ch)
        self.gm.create_runtime(ch, map_id)
        self.gm.register_web_session(ch, uid, self.sessions[cid]["name"])
        self.ensure_tick()
        rt2 = self.gm.get_runtime(ch)
        await self._send(cid, build_welcome(rt2, uid))
        await self._send(cid, {"type": "push", "message": f"[preview] Đã chuyển sang {map_id}."})

    async def _cmd_state(self, cid: int, uid: int, rt, value) -> None:
        from game.zombies import iter_web_zombies
        from rendering.daynight import ingame_seconds

        s = ingame_seconds()
        self.last_state = {
            "map": rt.map_data.map_id,
            "clock": f"{s // 3600:02d}:{s % 3600 // 60:02d}",
            "night": s >= 20 * 3600 or s < 6 * 3600,
            "weather": getattr(rt, "weather_key", "") or "auto",
            "zombies": len(iter_web_zombies(rt.state)),
            "meteors": len(rt.meteors.active),
            "felled_tonight": rt.meteors.felled_tonight,
            "auto_meteor": self.auto_meteor,
        }
        await self._send(cid, {"type": "preview_state", **self.last_state})

    # ---------------- overrides ----------------

    async def _frame(self, cid: int, frame: dict) -> None:
        if frame.get("type") == "preview_cmd":
            if cid not in self.sessions:
                self.sessions[cid] = {"user_id": PREVIEW_USER, "name": "Khach-preview"}
            await self._preview_cmd(cid, frame)
            return
        await super()._frame(cid, frame)


async def main() -> None:
    stack = PreviewStack()
    app = web.Application()
    # NOTE: routes MUST be registered BEFORE the catch-all static router —
    # add_static("/") swallows everything registered after it (403).
    app.router.add_get("/ws", stack.handle_browser)
    app.router.add_get("/config.json", stack.serve_config)
    app.router.add_get("/", stack.serve_index)
    app.router.add_get("/index.html", stack.serve_index)
    app.router.add_static("/", str(DIST_DIR))
    stack.snap_task = asyncio.get_event_loop().create_task(stack._snapshot_loop())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PREVIEW_PORT)
    await site.start()
    print(f"[preview-stack] ready on http://127.0.0.1:{PREVIEW_PORT}  (dist={DIST_DIR})".encode("ascii", "replace").decode("ascii"), flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
