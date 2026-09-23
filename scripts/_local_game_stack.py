"""Local repro stack for the web-client movement stutter (no Discord, no cloud).

Starts:
  1. an in-process game loop (GameManager + 20 Hz web tick + snapshot pump),
     speaking the EXACT WebHub frame protocol (net.ts speaks the same),
  2. an in-process relay: serves web_client/relay/dist statically AND accepts
     the browser WebSocket at /ws, faking the bot side in-process,

so the REAL web client can be loaded in a browser (Freebuff preview) and the
movement pipeline can be measured end-to-end on THIS machine.

Run:  .venv/Scripts/python scripts/_local_game_stack.py   (Ctrl+C to stop)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiohttp import web, WSMsgType  # noqa: E402

from game.manager import GameManager, _loop_time  # noqa: E402
from web_api.snapshots import build_welcome, build_snapshot  # noqa: E402

RELAY_PORT = int(os.environ.get("RELAY_PORT", "8899"))
# Simulated one-way latency (ms) for browser<->game frames — set
# LATENCY_MS=150 to reproduce prod relay conditions locally.
LATENCY_MS = float(os.environ.get("LATENCY_MS", "0"))
DIST_DIR = ROOT / "web_client" / "relay" / "dist"

PREVIEW_USER = 910000000000000042
PREVIEW_CHANNEL = PREVIEW_USER ^ 0x5EED000000000000


def now_ms() -> float:
    return time.time() * 1000


class LocalStack:
    def __init__(self) -> None:
        self.gm = GameManager(ROOT / "assets" / "maps")
        self.browsers: dict[int, web.WebSocketResponse] = {}
        self.next_cid = 1
        self.sessions: dict[int, dict] = {}
        self.joined: dict[int, int] = {}  # cid -> channel_id
        self.seq = 0
        self.tick_task: asyncio.Task | None = None
        self.snap_task: asyncio.Task | None = None

    # ---------------- game plumbing ----------------

    def ensure_tick(self) -> None:
        if self.tick_task is None or self.tick_task.done():
            self.tick_task = asyncio.get_event_loop().create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        interval = 1 / 20
        next_t = time.perf_counter()
        while True:
            next_t += interval
            await asyncio.sleep(max(0.0, next_t - time.perf_counter()))
            now = _loop_time()
            for rt in list(self.gm.runtimes.values()) + list(self.gm.side_runtimes.values()):
                sessions = getattr(rt, "web_sessions", None)
                if not sessions:
                    continue
                try:
                    await self.gm._web_tick_runtime(rt, sessions, now)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    import traceback
                    traceback.print_exc()

    def _map_payload(self, rt) -> dict:
        from web_api import snapshots as S

        fn = getattr(S, "_web_map_payload", None)
        if fn is None:
            fn = getattr(S, "build_welcome", None)
        return fn(rt) if fn else {}

    async def handle_browser(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)
        cid = self.next_cid
        self.next_cid += 1
        self.browsers[cid] = ws
        print(f"[stack] browser connected cid={cid}", flush=True)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                await self._frame(cid, json.loads(msg.data))
        finally:
            self.browsers.pop(cid, None)
            ch = self.joined.pop(cid, None)
            sess = self.sessions.pop(cid, None)
            if ch and sess:
                self.gm.drop_web_session(ch, sess["user_id"])
            print(f"[stack] browser disconnected cid={cid}", flush=True)
        return ws

    async def _frame(self, cid: int, frame: dict) -> None:
        t = frame.get("type")
        if t == "ping":
            await self._send(cid, {"type": "pong"})
            return
        if cid not in self.sessions and t not in ("guest_login", "resume_login"):
            # Frame before login (stale reconnect): auto-guest so old clients
            # never KeyError the handler.
            self.sessions[cid] = {"user_id": PREVIEW_USER, "name": f"Khach-{PREVIEW_USER % 10000}"}
        if t == "guest_login" or t == "resume_login":
            try:
                user_id = int(frame.get("guest_id") or PREVIEW_USER)
            except (TypeError, ValueError):
                user_id = PREVIEW_USER
            self.sessions[cid] = {"user_id": user_id, "name": f"Khach-{user_id % 10000}"}
            await self._send(cid, {
                "type": "login_result", "ok": True,
                "token": "local", "user_id": user_id,
                "display_name": self.sessions[cid]["name"],
            })
            return
        if t == "map_preview" or t == "join":
            uid = self.sessions[cid]["user_id"]
            map_id = str(frame.get("map_id") or "ekonia/forest")
            if map_id not in ("ekonia/forest", "ekonia/cave_area1", "ekonia/overworld", "bigmap"):
                map_id = "ekonia/forest"
            ch = uid ^ 0x5EED000000000000
            old = self.gm.get_runtime(ch)
            if old is not None:
                self.gm.remove_runtime(ch)
            self.gm.create_runtime(ch, map_id)
            self.gm.register_web_session(ch, uid, self.sessions[cid]["name"])
            self.joined[cid] = ch
            self.ensure_tick()
            rt = self.gm.get_runtime(ch)
            await self._send(cid, build_welcome(rt, uid))
            return
        if t == "input":
            uid = self.sessions[cid]["user_id"]
            ch = self.joined.get(cid)
            if ch is None:
                return
            self.gm.web_input(
                ch, uid,
                float(frame.get("dx", 0.0)),
                float(frame.get("dy", 0.0)),
                running=bool(frame.get("running", frame.get("sprint", False))),
                input_seq=frame.get("seq"),
                report_x=frame.get("x", frame.get("report_x")),
                report_y=frame.get("y", frame.get("report_y")),
            )
            return
        if t == "asset_request":
            await self._serve_asset(cid, str(frame.get("name", "")))
            return
        if t == "list":
            items = [
                {"channel_id": str(rt.channel_id), "map_id": rt.map_data.map_id,
                 "map_name": rt.map_data.display_name or rt.map_data.map_id,
                 "players": len(rt.state.get_visible_players())}
                for rt in self.gm.runtimes.values()
            ]
            await self._send(cid, {"type": "scenario_list", "items": items})
            return
        if t == "command":
            await self._send(cid, {"type": "command_result", "ok": False, "code": "local_stack"})
            return
        await self._send(cid, {"type": "error", "code": "unknown_type", "got": t})

    async def _serve_asset(self, cid: int, name: str) -> None:
        """Mirror WebHub._handle_asset_request (basename-confined PNG serve)."""
        import base64
        from config import ASSETS_DIR

        safe = Path(name).name
        if name.startswith("players/weapon/"):
            safe = Path(name).parts[-2] + "/" + Path(name).parts[-1]
        if not safe.lower().endswith(".png"):
            await self._send(cid, {"type": "asset_data", "name": name, "b64": None})
            return
        if name.startswith("blocks/"):
            base_dir = ASSETS_DIR.parent / "blocks"
        elif name.startswith("tilesets/"):
            base_dir = ASSETS_DIR.parent / "tilesets"
        elif name.startswith("icons/"):
            base_dir = ASSETS_DIR.parent / "gui" / "icons"
        elif name.startswith("mobs/"):
            base_dir = ASSETS_DIR.parent / "mobs"
        elif name.startswith("players/"):
            base_dir = ASSETS_DIR.parent / "players"
        else:
            base_dir = ASSETS_DIR
        path = base_dir / safe
        try:
            path.resolve().relative_to(base_dir.resolve())
        except ValueError:
            path = None
        if path is not None and not Path(path).exists() and name.startswith("tilesets/"):
            for cand in ASSETS_DIR.rglob(safe):
                path = cand
                break
        b64 = None
        if path is not None and Path(path).exists():
            try:
                b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            except OSError:
                b64 = None
        await self._send(cid, {"type": "asset_data", "name": name, "b64": b64})

    async def _send(self, cid: int, frame: dict) -> None:
        ws = self.browsers.get(cid)
        if ws is None or ws.closed:
            return
        try:
            if LATENCY_MS > 0:
                await asyncio.sleep(LATENCY_MS / 1000)
            await ws.send_str(json.dumps(frame))
        except ConnectionResetError:
            pass

    async def _snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            for cid, ch in list(self.joined.items()):
                uid = self.sessions[cid]["user_id"]
                rt = self.gm.get_runtime(ch)
                if rt is None:
                    continue
                self.seq += 1
                snap = build_snapshot(rt, uid, self.seq)
                if snap is not None:
                    await self._send(cid, snap)

    # ---------------- static relay ----------------

    async def serve_index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(DIST_DIR / "index.html")

    async def serve_config(self, request: web.Request) -> web.Response:
        return web.json_response({"client_id": "0", "redirect_uri": None})


async def main() -> None:
    stack = LocalStack()
    app = web.Application()
    app.router.add_get("/ws", stack.handle_browser)
    app.router.add_static("/", str(DIST_DIR))
    app.router.add_get("/config.json", stack.serve_config)
    app.router.add_get("/", stack.serve_index)
    app.router.add_get("/index.html", stack.serve_index)
    stack.snap_task = asyncio.get_event_loop().create_task(stack._snapshot_loop())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", RELAY_PORT)
    await site.start()
    print(f"[stack] ready on http://127.0.0.1:{RELAY_PORT}  (dist={DIST_DIR})", flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
