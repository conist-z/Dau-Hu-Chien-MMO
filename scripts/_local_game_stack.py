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
        self.selected_slot: dict[int, int] = {}  # cid -> held hotbar slot
        # PARITY with web_api/core.WebHub: the manager must push travel_begin
        # frames so the web client's iris veil closes BEFORE the teleport —
        # without this the preview tests a different flow than production.
        self.gm.web_travel_begin_hook = self._send_travel_begin

    async def _send_travel_begin(self, map_name: str, user_id: int) -> None:
        for cid, sess in self.sessions.items():
            if sess.get("user_id") == user_id and self.joined.get(cid):
                await self._send(cid, {
                    "type": "travel_begin", "map_name": map_name,
                })

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
        if t == "select_slot":
            slot = frame.get("slot")
            if isinstance(slot, int) and 0 <= slot < 8:
                self.selected_slot[cid] = slot
            return
        if t == "action":
            # Preview parity with web_api._handle_action: build the SAME
            # action objects and route through manager.dispatch like prod.
            print(f"[stack] action {frame.get('name')} tx={frame.get('tx')} ty={frame.get('ty')} block={frame.get('block_id')}", flush=True)
            uid = self.sessions[cid]["user_id"]
            ch = self.joined.get(cid)
            if not ch:
                return
            from game.actions import (
                AttackAction, ChopAction, BreakBlockAction,
                PlaceBlockAction, ShovelAction,
            )
            name = str(frame.get("name", ""))
            rt_a = self.gm.get_runtime(ch)
            player_a = rt_a.state.get_player(uid) if rt_a else None
            abs_dx = abs_dy = None
            raw_tx, raw_ty = frame.get("tx"), frame.get("ty")
            if (player_a is not None and isinstance(raw_tx, (int, float))
                    and isinstance(raw_ty, (int, float))):
                abs_dx = int(raw_tx) - player_a.x
                abs_dy = int(raw_ty) - player_a.y
                lim = 3 + 2
                if max(abs(abs_dx), abs(abs_dy)) > lim:
                    abs_dx = max(-3, min(3, abs_dx)) if abs(abs_dx) > 3 else abs_dx
                    abs_dy = max(-3, min(3, abs_dy)) if abs(abs_dy) > 3 else abs_dy
            action = None
            if name == "attack":
                action = AttackAction(user_id=uid)
            elif name == "chop":
                action = ChopAction(user_id=uid, dx=abs_dx, dy=abs_dy)
            elif name == "break":
                action = BreakBlockAction(user_id=uid, dx=abs_dx, dy=abs_dy)
            elif name == "shovel":
                action = ShovelAction(user_id=uid)
            elif name == "place":
                dx, dy = (abs_dx, abs_dy) if abs_dx is not None else (
                    frame.get("dx"), frame.get("dy"))
                block_id = str(frame.get("block_id", ""))
                if not block_id and player_a is not None:
                    inv = self.gm.get_inventory(ch, uid)
                    from game.blocks import get_block
                    held = inv.hotbar().get(self.selected_slot.get(cid, 0) or 0)
                    if held and get_block(held) is not None:
                        block_id = held
                action = PlaceBlockAction(
                    user_id=uid, block_id=block_id,
                    dx=int(dx) if dx is not None else None,
                    dy=int(dy) if dy is not None else None,
                )
            if action is None:
                await self._send(cid, {"type": "error", "code": "bad_action"})
                return
            try:
                await self.gm.dispatch(ch, action)
            except Exception as exc:
                await self._send(cid, {"type": "push", "message": f"[preview] action error: {exc!r}"})
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
        if t == "chat_cmd":
            # Preview parity with web_api/core._handle_chat_cmd: route slash
            # commands through the SAME manager calls production uses, so
            # /cuahang, /give, ... behave identically in the preview tab.
            text = (frame.get("text") or "").strip()
            uid = self.sessions[cid]["user_id"]
            ch = self.joined.get(cid)
            if not ch:
                return
            if not text.startswith("/"):
                await self._send(cid, {"type": "chat", "uid": uid,
                                       "name": self.sessions[cid]["name"],
                                       "color": "", "text": text[:200]})
                return
            parts = text[1:].split()
            cmd, args = parts[0].lower(), parts[1:]
            if cmd == "cuahang":
                dest = " ".join(args) if args else "hang"
                rt, message = await self.gm.web_travel_portal(ch, uid, dest)
                await self._send(cid, {"type": "push", "message": message})
                if rt is not None:
                    await self._send(cid, build_welcome(rt, uid))
                return
            if cmd == "khutraodoi":
                action = (args[0].lower() if args else "in")
                rt, message = await self.gm.web_travel_trade(ch, uid, action)
                await self._send(cid, {"type": "push", "message": message})
                if rt is not None:
                    await self._send(cid, build_welcome(rt, uid))
                return
            if cmd == "give":
                from game.items import ITEM_REGISTRY
                from game.blocks import BLOCK_REGISTRY
                if not args:
                    ids = " ".join(sorted(ITEM_REGISTRY.keys()))
                    await self._send(cid, {"type": "push", "message": f"Dùng: /give <item> [số lượng]. Có: {ids}"})
                    return
                item_id = args[0].lower()
                try:
                    qty = max(1, min(999, int(args[1]))) if len(args) > 1 else 1
                except ValueError:
                    qty = 1
                rt = self.gm.get_runtime(ch)
                if rt is not None and (item_id in ITEM_REGISTRY or item_id in BLOCK_REGISTRY):
                    await self.gm.add_item(ch, uid, item_id, qty)
                    await self._send(cid, {"type": "push", "message": f"Đã nhận {qty} {item_id}."})
                else:
                    await self._send(cid, {"type": "push", "message": f"Không biết vật phẩm: {item_id}"})
                return
            await self._send(cid, {"type": "push", "message": f"[preview] Lệnh không hỗ trợ local: {cmd}"})
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
        elif name.startswith("node/"):
            # Bundled node sprites (meteor-ore crater rock).
            base_dir = ASSETS_DIR.parent / "node"
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
                # PARITY with web_api/core: pump the runtime the player is
                # ACTUALLY on (side runtime after /cuahang or a portal step),
                # not the channel's main world — otherwise snapshots keep
                # carrying the OLD map_id and the client's map-change gate
                # (travel veil readiness) never fires in preview.
                rt = self.gm.runtime_of(ch, uid) or self.gm.get_runtime(ch)
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
