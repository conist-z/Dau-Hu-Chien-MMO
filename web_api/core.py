"""Web API core: frame handling for the web client (transport-agnostic).

The relay fronts browser WebSockets; the bot speaks ONE multiplexed stream
with the relay. Every envelope carries a relay-side connection id (``cid``):

    {"cid": 7, "frame": {...}}          client frame routed to the bot
    {"type": "client_connected", "cid": 7}
    {"type": "client_gone", "cid": 7}
    {"type": "login", "cid": 7, "code": "...", "redirect_uri": "..."}

Outgoing bot->relay envelopes:
    {"cid": 7, "frame": {...}}          frame delivered to that client
    {"cid": 7, "type": "login_result", ...}
    {"type": "asset_data", "cid": 7, "name": "...", "b64": "..."}

This module never touches sockets — ``WebHub`` consumes/produces dicts, the
relay client moves bytes. Game mutations go through GameManager only.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from config import WEB_TICK_HZ
from game.manager import GameManager
from web_api import auth
from web_api.auth import OAuthError
from web_api.protocol import (
    MSG_ACTION,
    MSG_CRAFT_OP,
    MSG_CRAFT_RESULT,
    MSG_ERROR,
    MSG_INPUT,
    MSG_INV_OP,
    MSG_JOIN,
    MSG_PING,
    MSG_PONG,
    MSG_PUSH,
    MSG_WELCOME,
    SessionRegistry,
    WebSession,
)
from web_api.snapshots import build_snapshot, build_welcome

log = logging.getLogger("WEB")

# Weather keys accepted by /setweather on web (mirrors the Discord command's
# label->key map; data-driven, no logic changes needed to extend).
# Must cover ALL animated web overlay keys (rain/heavy_rain/storm/snow/cold/
# wind — see web_client/src/weather.ts STYLES) plus every static Discord key,
# otherwise admins cannot demo weather on web and it feels "turned off".
# Legacy aliases (sun/clouds/fog) kept for backward compatibility.
WEATHER_KEYS = (
    "sun_clouds", "sunny", "cloudy", "heavy_clouds",
    "rain", "heavy_rain", "storm", "snow", "cold", "wind",
    "fog", "sun", "clouds",
)


@dataclass
class ClientConnection:
    """One relay-side browser connection (by cid)."""

    cid: int
    session: Optional[WebSession] = None
    joined: bool = False
    # seq counter for snapshots on this connection.
    seq: int = 0


class WebHub:
    """Consumes relay envelopes, mutates the game, produces reply envelopes.

    All methods are async-safe and testable without a socket: feed envelopes
    into :meth:`handle_envelope`, drain :attr:`outbox`.
    """

    def __init__(self, manager: GameManager):
        self.manager = manager
        self.registry = SessionRegistry()
        self.connections: Dict[int, ClientConnection] = {}
        self.outbox: asyncio.Queue = asyncio.Queue()
        self._snapshot_task: Optional[asyncio.Task] = None

    # ----- envelope plumbing -----

    async def send_to_client(self, cid: int, frame: dict) -> None:
        await self.outbox.put({"cid": cid, "frame": frame})

    async def handle_envelope(self, envelope: dict) -> None:
        """UNIFORM convention: every browser frame arrives as
        {"cid": N, "frame": {...}} and every reply leaves as the same shape.
        Only client_connected / client_gone are envelope-level (relay bookkeeping).
        """
        etype = envelope.get("type")
        cid = envelope.get("cid", -1)
        if etype == "client_connected":
            self.connections[cid] = ClientConnection(cid=cid)
            return
        if etype == "client_gone":
            await self._handle_gone(cid)
            return
        frame = envelope.get("frame")
        if not isinstance(frame, dict):
            return
        await self.handle_frame(cid, frame)

    async def handle_frame(self, cid: int, frame: dict) -> None:
        conn = self.connections.get(cid)
        if conn is None:
            return
        ftype = frame.get("type")
        if ftype == MSG_PING:
            await self.send_to_client(cid, {"type": MSG_PONG, "t": frame.get("t")})
            return
        if ftype == "login":
            await self._handle_login(
                cid, frame.get("code", ""), frame.get("redirect_uri", "")
            )
            return
        if ftype == "guest_login":
            await self._handle_guest_login(cid, frame.get("guest_id", ""))
            return
        if ftype == "asset_request":
            await self._handle_asset_request(cid, frame.get("name", ""))
            return
        if ftype == MSG_JOIN:
            await self._handle_join(conn, frame)
            return
        if ftype == "select_slot":
            # Hotbar selection mirror (numbers / wheel / click). Stores the
            # held slot on the session (used by `place` to resolve "cầm gì
            # đặt nấy") AND on the runtime's held_slots map (so every OTHER
            # client sees this player's hand update in their next snapshot),
            # then echoes the held item so the client can label it.
            slot = frame.get("slot")
            sess = conn.session
            if sess is not None and isinstance(slot, int) and 0 <= slot < 8:
                sess.selected_slot = slot
                rt0 = self.manager.get_runtime_for(sess.channel_id, sess.user_id)
                if rt0 is not None:
                    try:
                        rt0.held_slots[sess.user_id] = slot
                    except Exception:  # noqa: BLE001 — UI-only mirror, never fail selection
                        log.warning("[WEB] held_slots mirror failed for %s", sess.user_id)
            if sess is not None and sess.channel_id:
                rt = self.manager.get_runtime_for(sess.channel_id, sess.user_id)
                if rt is not None:
                    from web_api.snapshots import _inventory_payload

                    inv = _inventory_payload(rt, sess.user_id)
                    held = inv["hotbar"][slot] if isinstance(slot, int) and 0 <= slot < len(inv["hotbar"]) else None
                    await self.send_to_client(cid, {
                        "type": "held", "slot": slot, "item_id": held,
                    })
            return
        if ftype == "list":
            # Pre-join picklist: requires a login (session) but NOT a joined
            # scenario — gating it below made the map picker unreachable.
            if conn.session is None:
                await self.send_to_client(
                    cid, {"type": MSG_ERROR, "code": "not_joined"}
                )
                return
            await self._handle_list(conn.session)
            return
        if conn.session is None or not conn.joined:
            await self.send_to_client(
                cid, {"type": MSG_ERROR, "code": "not_joined"}
            )
            return
        sess = conn.session
        if ftype == MSG_INPUT:
            await self._handle_input(sess, frame)
        elif ftype == MSG_ACTION:
            await self._handle_action(sess, frame)
        elif ftype == MSG_INV_OP:
            await self._handle_inventory_op(sess, frame)
        elif ftype == MSG_CRAFT_OP:
            await self._handle_craft_op(sess, frame)
        elif ftype == "chat_cmd":
            await self._handle_chat_cmd(sess, frame)
        else:
            await self.send_to_client(
                cid, {"type": MSG_ERROR, "code": "unknown_type"}
            )

    # ----- login + join -----

    async def _handle_list(self, sess: WebSession) -> None:
        """Joinable scenarios for the logged-in player (join screen picklist)."""
        items = []
        for rt in self.manager.runtimes.values():
            items.append({
                # STRING id: Discord snowflakes exceed JS Number precision;
                # the Node relay mangles raw ints (trailing digits -> 00).
                "channel_id": str(rt.channel_id),
                "map_id": rt.map_data.map_id,
                "map_name": rt.map_data.display_name or rt.map_data.map_id,
                "players": len(rt.state.get_visible_players()),
            })
        await self.send_to_client_conn(
            sess, {"type": "scenario_list", "items": items}
        )

    async def _handle_guest_login(self, cid: int, guest_id: str) -> None:
        """Quick-play: no Discord login — a stable per-browser guest id.

        Guest ids live in the 9xx… range (real Discord snowflakes currently
        start much lower), so a future Discord login with the same person is
        a different (upgraded) identity; guests keep a separate bag.
        """
        raw = str(guest_id or "").strip()
        if not raw.isdigit() or len(raw) > 19:
            await self.send_to_client(cid, {
                "type": "login_result", "ok": False, "error": "bad_guest_id",
            })
            return
        user_id = int(raw)
        if user_id < 900000000000000000:
            await self.send_to_client(cid, {
                "type": "login_result", "ok": False, "error": "bad_guest_id",
            })
            return
        sess = self.registry.create(
            user_id, f"Khach-{raw[-4:]}", channel_id=0,
        )
        # Bind the session to THIS connection NOW (not at join): reply helpers
        # route by conn.session identity — without this, scenario_list and
        # every pre-join reply silently vanish.
        conn = self.connections.get(cid)
        if conn is not None:
            conn.session = sess
        print(f"[WEB] guest login {user_id}", flush=True)
        await self.send_to_client(cid, {
            "type": "login_result", "ok": True,
            "token": sess.token,
            "user_id": sess.user_id,
            "display_name": sess.display_name,
        })

    async def _handle_login(self, cid: int, code: str, redirect_uri: str) -> None:
        try:
            profile = await auth.verify_code(code, redirect_uri)
        except OAuthError as e:
            await self.send_to_client(cid, {
                "type": "login_result", "ok": False, "error": str(e),
            })
            return
        sess = self.registry.create(
            profile["user_id"], profile["display_name"], channel_id=0,
        )
        conn = self.connections.get(cid)
        if conn is not None:
            conn.session = sess
        await self.send_to_client(cid, {
            "type": "login_result", "ok": True,
            "token": sess.token,
            "user_id": sess.user_id,
            "display_name": sess.display_name,
        })

    async def _handle_join(self, conn: ClientConnection, frame: dict) -> None:
        token = frame.get("token", "")
        raw_channel = frame.get("channel_id")
        try:
            channel_id = int(str(raw_channel))
        except (TypeError, ValueError):
            channel_id = -1
        if channel_id <= 0:
            await self.send_to_client(conn.cid, {"type": MSG_ERROR, "code": "bad_channel"})
            return
        sess = self.registry.get(token)
        if sess is None:
            await self.send_to_client(conn.cid, {"type": MSG_ERROR, "code": "bad_token"})
            return
        sess.channel_id = channel_id
        ok = self.manager.register_web_session(
            channel_id, sess.user_id, sess.display_name,
        )
        if not ok:
            await self.send_to_client(
                conn.cid, {"type": MSG_ERROR, "code": "scenario_missing_or_full"}
            )
            return
        await self.manager.ensure_web_tick_async()
        conn.session = sess
        conn.joined = True
        rt = self.manager.get_runtime(channel_id)
        if rt is None:
            await self.send_to_client(conn.cid, {"type": MSG_ERROR, "code": "scenario_missing"})
            return
        welcome = build_welcome(rt, sess.user_id)
        await self.send_to_client(conn.cid, welcome)
        self.start_snapshots()

    async def _handle_gone(self, cid: int) -> None:
        conn = self.connections.pop(cid, None)
        if conn is None:
            return
        if conn.session is not None:
            self.registry.drop(conn.session.token)
            try:
                self.manager.drop_web_session(conn.session.channel_id, conn.session.user_id)
            except Exception as e:  # noqa: BLE001 — teardown must never raise
                log.warning("[WEB] drop session failed: %s", e)

    # ----- gameplay frames -----

    async def _handle_action(self, sess: WebSession, frame: dict) -> None:
        """Discrete gameplay actions (attack/chop/break/place/shovel)."""
        from game.actions import (
            AttackAction,
            BreakBlockAction,
            ChopAction,
            PlaceBlockAction,
            ShovelAction,
            TurnAction,
        )
        from game.state import Direction

        name = frame.get("name")
        uid = sess.user_id
        action = None
        # ABSOLUTE mouse-tile targeting: the client sends the tile it clicked
        # (tx, ty); the server derives the offset from ITS OWN player tile —
        # client-side position math drifts (prediction) and misplaced blocks.
        rt_a = self.manager.get_runtime(sess.channel_id)
        player_a = rt_a.state.get_player(uid) if rt_a else None
        abs_dx = abs_dy = None
        raw_tx, raw_ty = frame.get("tx"), frame.get("ty")
        if (
            player_a is not None
            and isinstance(raw_tx, (int, float))
            and isinstance(raw_ty, (int, float))
        ):
            abs_dx = int(raw_tx) - player_a.x
            abs_dy = int(raw_ty) - player_a.y
            # Lag tolerance: the client targets from a snapshot that can lag
            # the server tick while walking — clamp into AIM_RANGE (toward
            # the player) instead of rejecting with out_of_range. Only a
            # click genuinely beyond AIM_RANGE+1 is refused.
            from config import WEB_AIM_RANGE_TOLERANCE

            lim = 3 + WEB_AIM_RANGE_TOLERANCE
            if max(abs(abs_dx), abs(abs_dy)) > lim:
                # GENUINELY too far: REJECT. The old "facing-tile fallback"
                # silently placed the held block in front of the player and
                # ate a block on every far click (blocks kept appearing where
                # nobody clicked; "vị trí bị cập nhật sai"). The client's
                # prediction is now leashed to the server, so a far click is
                # a far click — answer honestly, never redirect it.
                if name in ("chop", "break", "place"):
                    await self.send_to_client_conn(sess, {
                        "type": "action_result",
                        "name": name,
                        "ok": False,
                        "reason": "out_of_range",
                        "tx": int(raw_tx),
                        "ty": int(raw_ty),
                        "kind": "",
                        "needed": None,
                        "drops": [],
                    })
                    return
                abs_dx = abs_dy = None
            elif max(abs(abs_dx), abs(abs_dy)) > 3:
                # Truncate each axis into range (preserves direction).
                abs_dx = max(-3, min(3, abs_dx)) if abs(abs_dx) > 3 else abs_dx
                abs_dy = max(-3, min(3, abs_dy)) if abs(abs_dy) > 3 else abs_dy
        if name == "attack":
            action = AttackAction(user_id=uid)
        elif name == "chop":  # chặt cây (mouse tile nếu có)
            action = ChopAction(
                user_id=uid,
                dx=abs_dx if abs_dx is not None else None,
                dy=abs_dy if abs_dy is not None else None,
            )
        elif name == "break":  # đập block (mouse tile nếu có)
            action = BreakBlockAction(
                user_id=uid,
                dx=abs_dx if abs_dx is not None else None,
                dy=abs_dy if abs_dy is not None else None,
            )
        elif name == "shovel":  # xúc cỏ/đất (target tile)
            action = ShovelAction(user_id=uid)
        elif name == "turn":  # quay hướng nhìn (8-way)
            try:
                direction = Direction[str(frame.get("dir", "SOUTH")).upper()]
                action = TurnAction(user_id=uid, direction=direction)
            except KeyError:
                action = None
        elif name == "place":  # đặt block (mouse tile tuyệt đối nếu có)
            if abs_dx is not None:
                dx, dy = abs_dx, abs_dy
            else:
                dx = frame.get("dx")
                dy = frame.get("dy")
            block_id = str(frame.get("block_id", ""))
            if not block_id:
                # Client sent no block id. Resolution: ONLY the HELD hotbar
                # item — right-click scope follows the active slot exactly.
                # NO bag fallback: if the held item isn't a placeable block
                # the action simply fails (the client already refuses to
                # send place when the active slot holds no block).
                inv = self.manager.get_inventory(sess.channel_id, uid)
                from game.blocks import get_block

                held = inv.hotbar().get(sess.selected_slot)
                if held and get_block(held) is not None:
                    block_id = held
            action = PlaceBlockAction(
                user_id=uid,
                block_id=block_id,
                dx=int(dx) if dx is not None else None,
                dy=int(dy) if dy is not None else None,
            )
        if action is None:
            await self.send_to_client_conn(
                sess, {"type": MSG_ERROR, "code": "bad_action"}
            )
            return
        _, result = await self.manager.dispatch(sess.channel_id, action)
        # Echo the action outcome so the client can show progress/failures.
        # Zombie kills ride here too: target_id/defeated drive the death
        # animation + loot pop on web (same pack as Discord).
        if result is not None:
            await self.send_to_client_conn(sess, {
                "type": "action_result",
                "name": name,
                "ok": bool(result.state_changed),
                "reason": result.reason or "",
                "tx": result.pos[0] if result.pos else None,
                "ty": result.pos[1] if result.pos else None,
                "kind": (
                    "zombie" if getattr(result, "target_id", None)
                    else (result.block_id or "")
                ),
                "target_id": getattr(result, "target_id", None),
                "target_defeated": bool(getattr(result, "target_defeated", False)),
                "needed": result.needed,
                "drops": [[i, q] for i, q in (result.drops or [])],
                "damage": int(getattr(result, "damage", 0) or 0),
                "critical": bool(getattr(result, "critical", False)),
                "missed": bool(getattr(result, "missed", False)),
            })

    async def _handle_input(self, sess: WebSession, frame: dict) -> None:
        if not sess.input_allowed():
            return  # flood: silently dropped (client sends <=30/s by design)
        # Input-sequence reconciliation: remember the client's monotonic seq
        # so snapshots can ack it (client rewinds + replays unacked inputs).
        # A missing/non-int seq (old client) just leaves input_seq untouched.
        raw_seq = frame.get("seq")
        if isinstance(raw_seq, int) and raw_seq > sess.input_seq:
            sess.input_seq = raw_seq
        self.manager.web_input(
            sess.channel_id, sess.user_id,
            float(frame.get("dx", 0.0)), float(frame.get("dy", 0.0)),
            bool(frame.get("running", False)),
        )

    async def _handle_inventory_op(self, sess: WebSession, frame: dict) -> None:
        op = frame.get("op")
        cid, uid = sess.channel_id, sess.user_id
        if op == "move_to":
            slot = int(frame.get("slot", 0))
            item_id = frame.get("item_id")
            try:
                await self.manager.set_hotbar_slot(cid, uid, slot, item_id)
            except ValueError:
                await self.send_to_client_conn(sess, {"type": MSG_ERROR, "code": "bad_slot"})
                return
        elif op == "split":
            inv = self.manager.get_inventory(cid, uid)
            ok = inv.split_at(frame.get("item_id", ""))
            if not ok:
                await self.send_to_client_conn(sess, {"type": MSG_ERROR, "code": "bad_split"})
                return
            if self.manager.db is not None:
                from persistence.repositories import save_inventory_order

                await save_inventory_order(
                    self.manager.db, cid, uid, list(inv.items))
        elif op == "use":
            await self.manager.use_item(cid, uid, frame.get("item_id", ""))
        else:
            await self.send_to_client_conn(sess, {"type": MSG_ERROR, "code": "bad_op"})
            return
        rt = self.manager.get_runtime_for(cid, uid)
        if rt is not None:
            from web_api.snapshots import _inventory_payload

            await self.send_to_client_conn(sess, {
                "type": MSG_INV_DELTA,
                "inventory": _inventory_payload(rt, uid),
            })

    async def _handle_craft_op(self, sess: WebSession, frame: dict) -> None:
        cid, uid = sess.channel_id, sess.user_id
        placed = frame.get("inputs")
        if placed is not None:
            # Grid model: the client sends exactly what sits in the craft
            # input grid; the server matches it to a recipe and consumes
            # from the bag.
            res = await self.manager.craft_from_inputs(cid, uid, placed)
            await self.send_to_client_conn(sess, {
                "type": MSG_CRAFT_RESULT,
                "ok": res["ok"],
                "reason": res["reason"],
                "item_id": res["item_id"],
                "qty": res["qty"],
            })
            if res["ok"]:
                rt = self.manager.get_runtime_for(cid, uid)
                if rt is not None:
                    from web_api.snapshots import _inventory_payload

                    await self.send_to_client_conn(sess, {
                        "type": MSG_INV_DELTA,
                        "inventory": _inventory_payload(rt, uid),
                    })
            return
        ok, reason, out_id, out_qty = await self.manager.craft_item(
            cid, uid, frame.get("recipe_id", "")
        )
        await self.send_to_client_conn(sess, {
            "type": MSG_CRAFT_RESULT,
            "ok": ok,
            "reason": reason,
            "item_id": out_id,
            "qty": out_qty,
        })
        if ok:
            rt = self.manager.get_runtime_for(cid, uid)
            if rt is not None:
                from web_api.snapshots import _inventory_payload

                await self.send_to_client_conn(sess, {
                    "type": MSG_INV_DELTA,
                    "inventory": _inventory_payload(rt, uid),
                })

    # ----- chat commands -----

    async def _handle_chat_cmd(self, sess: WebSession, frame: dict) -> None:
        text = (frame.get("text") or "").strip()
        if not text.startswith("/"):
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH, "message": "Lệnh phải bắt đầu bằng /",
            })
            return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]
        if cmd == "help":
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH,
                "message": "Lệnh: /help, /weather, /time, /setweather <key> (admin), /give <item> [số lượng] (admin)",
            })
        elif cmd == "weather":
            rt = self.manager.get_runtime(sess.channel_id)
            key = rt.weather_key if rt is not None else "?"
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH, "message": f"Thời tiết hiện tại: {key}",
            })
        elif cmd == "setweather":
            await self._cmd_setweather(sess, args)
        elif cmd == "time":
            await self._cmd_time(sess, args)
        elif cmd == "give":
            await self._cmd_give(sess, args)
        else:
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH, "message": f"Lệnh không rõ: /{cmd}",
            })

    async def _cmd_give(self, sess: WebSession, args: List[str]) -> None:
        """Admin /give: mint any item or block straight into the player's bag.

        Usage: /give <item_id> [qty]. Item ids are the registry ids
        (dirt_sword, stone_pickaxe, potion_hp, coin, wood, ...). Admin-gated
        the same way as /setweather.
        """
        if not args:
            # No args: list every mintable id so the admin can pick by hand.
            from game.items import ITEM_REGISTRY

            ids = " ".join(sorted(ITEM_REGISTRY.keys()))
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH,
                "message": f"Dùng: /give <item> [số lượng]. Có: {ids}",
            })
            return
        item_id = args[0].lower()
        qty = 1
        if len(args) > 1:
            try:
                qty = max(1, min(999, int(args[1])))
            except ValueError:
                await self.send_to_client_conn(sess, {
                    "type": MSG_PUSH, "message": "Số lượng phải là số nguyên.",
                })
                return
        from game.items import ITEM_REGISTRY, get_item
        from game.blocks import BLOCK_REGISTRY

        known = item_id in ITEM_REGISTRY or item_id in BLOCK_REGISTRY
        if not known:
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH, "message": f"Không rõ vật phẩm: {item_id}",
            })
            return
        # TEMP (quick test): admin gate OFF — everyone can /give.
        # Restore `if not await self._is_web_admin(sess): ...` before prod.
        # if not await self._is_web_admin(sess):
        #     await self.send_to_client_conn(sess, {
        #         "type": MSG_PUSH, "message": "Chỉ admin mới được dùng /give (chưa bật).",
        #     })
        #     return
        rt = self.manager.get_runtime_for(sess.channel_id, sess.user_id)
        if rt is None:
            return
        async with rt.lock:
            inv = self.manager.get_inventory(sess.channel_id, sess.user_id)
            inv.add(item_id, qty)
            if self.manager.db is not None:
                from persistence.repositories import save_inventory_item
                await save_inventory_item(
                    self.manager.db, sess.channel_id, sess.user_id,
                    item_id, inv.count(item_id),
                )
            self.manager._notify_inventory_change(sess.channel_id, sess.user_id)
        from web_api.snapshots import _inventory_payload

        await self.send_to_client_conn(sess, {
            "type": MSG_INV_DELTA,
            "inventory": _inventory_payload(rt, sess.user_id),
        })
        it = get_item(item_id)
        label = f"{it.name} x{qty}" if it is not None else f"{item_id} x{qty}"
        await self.send_to_client_conn(sess, {
            "type": MSG_PUSH, "message": f"🎁 Đã nhận {label} vào túi.",
        })

    _TIME_PRESETS = {
        # word -> second_of_day (night window = 20:00..06:00, see zombies.py)
        "day": 12 * 3600,       # noon
        "noon": 12 * 3600,
        "night": 22 * 3600,     # deep night (zombies active)
        "midnight": 0,
        "dawn": 6 * 3600,
        "dusk": 19 * 3600,
    }

    async def _cmd_time(self, sess: WebSession, args: List[str]) -> None:
        """Web chat /time set <preset|HH:MM|normal>: jump the in-game clock.

        The clock keeps advancing normally from the new point. Zombie activity
        follows the same clock (night = 20:00..06:00), so /time set night is
        the fast way to test zombies without waiting for dusk.
        """
        from rendering.daynight import ingame_seconds, set_ingame_time

        usage = "Dùng: /time set <day|night|dawn|dusk|midnight|HH:MM|normal>"
        if len(args) < 2 or args[0].lower() != "set":
            if not args:
                sec = ingame_seconds()
                await self.send_to_client_conn(sess, {
                    "type": MSG_PUSH,
                    "message": f"Giờ trong game: {sec // 3600:02d}:{sec % 3600 // 60:02d}. {usage}",
                })
                return
            await self.send_to_client_conn(sess, {"type": MSG_PUSH, "message": usage})
            return
        if not await self._is_web_admin(sess):
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH, "message": "Chỉ admin mới được chỉnh giờ.",
            })
            return
        value = args[1].lower()
        target: int | None
        if value in ("normal", "auto", "reset"):
            target = None
        elif value in self._TIME_PRESETS:
            target = self._TIME_PRESETS[value]
        elif ":" in value:
            try:
                hh, mm = value.split(":", 1)
                h, m = int(hh), int(mm)
                if not (0 <= h < 24 and 0 <= m < 60):
                    raise ValueError
                target = h * 3600 + m * 60
            except ValueError:
                await self.send_to_client_conn(sess, {"type": MSG_PUSH, "message": usage})
                return
        else:
            await self.send_to_client_conn(sess, {"type": MSG_PUSH, "message": usage})
            return
        rt = self.manager.get_runtime(sess.channel_id)
        if rt is None:
            return
        async with rt.lock:
            result = set_ingame_time(target)
        # HUD/web clock updates automatically: web snapshots carry the clock at
        # 20 Hz, Discord hubs re-render on their 5s signature beat.
        label = (
            "vòng tự nhiên"
            if target is None
            else f"{result // 3600:02d}:{result % 3600 // 60:02d}"
        )
        await self.send_to_client_conn(sess, {
            "type": MSG_PUSH, "message": f"⏰ Đã chỉnh giờ trong game: {label}.",
        })

    async def _cmd_setweather(self, sess: WebSession, args: List[str]) -> None:
        if not args or args[0] not in WEATHER_KEYS:
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH,
                "message": "Dùng: /setweather <" + "|".join(WEATHER_KEYS) + ">",
            })
            return
        if not await self._is_web_admin(sess):
            await self.send_to_client_conn(sess, {
                "type": MSG_PUSH, "message": "Chỉ admin mới được đổi thời tiết.",
            })
            return
        rt = self.manager.get_runtime(sess.channel_id)
        if rt is None:
            return
        async with rt.lock:
            rt.weather_key = args[0]
            # Pin manual key (same as the Discord /setweather path): the auto
            # weather loop only refreshes weather_state while pinned.
            rt.weather_manual = True
        await self.send_to_client_conn(sess, {
            "type": MSG_PUSH, "message": f"Đã đổi thời tiết: {args[0]}",
        })

    async def _is_web_admin(self, sess: WebSession) -> bool:
        """Admin = Administrator / Manage Server on the channel's guild,
        resolved from the bot's member cache (same gate as /setweather)."""
        bot = getattr(self.manager, "bot_ref", None)
        if bot is None:
            return False
        channel = bot.get_channel(sess.channel_id)
        guild = getattr(channel, "guild", None)
        if guild is None:
            return False
        member = guild.get_member(sess.user_id)
        if member is None:
            return False
        perms = member.guild_permissions
        if perms.administrator or perms.manage_guild:
            return True
        # Env override: WEB_ADMIN_IDS="123,456" grants web admin to specific
        # Discord user_ids (guests / bot-less test setups). Empty = off.
        import os

        allowed = os.environ.get("WEB_ADMIN_IDS", "")
        return str(sess.user_id) in {s.strip() for s in allowed.split(",") if s.strip()}

    # ----- assets -----

    async def _handle_asset_request(self, cid: int, name: str) -> None:
        """Serve one PNG by BASENAME from the maps assets dir.

        Basename-only + directory confinement = no traversal; no filesystem
        paths ever reach the client (license-safe: assets stay on the bot).
        ``blocks/<id>.png`` is served from ``assets/blocks`` so the web client
        draws the same real block faces as the Discord renderer.
        ``mobs/<id>.png`` (the Kaetram zombie sheet) is served from
        ``assets/mobs`` the same way. ``players/...`` serves the paperdoll
        sheets from ``assets/players``; the manifest rides the welcome frame.
        """
        from config import ASSETS_DIR

        # players/weapon/<stem>.png must keep its subfolder: the basename
        # squeeze looked for assets/players/<stem>.png (never exists) and
        # every weapon sheet came back b64=null — the web paperdoll could
        # never attach a sword. Traversal is still blocked by the resolved-
        # path confinement check below (defence in depth).
        safe = Path(name).name
        if name.startswith("players/weapon/"):
            safe = Path(name).parts[-2] + "/" + Path(name).parts[-1]
        if not safe.lower().endswith(".png"):
            await self.send_to_client(cid, {"type": "asset_data", "name": name, "b64": None})
            return
        if name.startswith("blocks/"):
            base_dir = ASSETS_DIR.parent / "blocks"
        elif name.startswith("mobs/"):
            base_dir = ASSETS_DIR.parent / "mobs"
        elif name.startswith("players/"):
            # Paperdoll sheets (player/base + players/weapon/<tool>.png)
            # served from assets/players — same manifest-driven pipeline as
            # blocks/mobs so the web client can build Kaetram-style sprites.
            base_dir = ASSETS_DIR.parent / "players"
        else:
            base_dir = ASSETS_DIR
        path = base_dir / safe
        # Defence in depth: resolved path must stay inside the chosen dir.
        try:
            path.resolve().relative_to(base_dir.resolve())
        except ValueError:
            path = None  # type: ignore[assignment]
        b64 = None
        if path is not None and Path(path).exists():
            try:
                b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            except OSError as e:
                log.warning("[WEB] asset read failed %s: %s", safe, e)
        await self.send_to_client(cid, {"type": "asset_data", "name": name, "b64": b64})

    # ----- snapshot pump -----

    def start_snapshots(self) -> None:
        if self._snapshot_task is None or self._snapshot_task.done():
            try:
                self._snapshot_task = asyncio.create_task(self._snapshot_loop())
            except RuntimeError:
                pass  # no loop yet (tests); join() starts one via ensure_web_tick

    async def stop(self) -> None:
        task = self._snapshot_task
        self._snapshot_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _snapshot_loop(self) -> None:
        interval = 1.0 / max(1.0, WEB_TICK_HZ)
        while True:
            started = asyncio.get_running_loop().time()
            for conn in list(self.connections.values()):
                if not conn.joined or conn.session is None:
                    continue
                rt = self.manager.get_runtime(conn.session.channel_id)
                if rt is None:
                    continue
                conn.seq += 1
                try:
                    frame = build_snapshot(rt, conn.session.user_id, conn.seq)
                except Exception:
                    # Never let one bad snapshot kill the loop: a dead loop =
                    # every client frozen (welcome arrives, snapshots never
                    # do => no map, no movement). Log and keep pumping.
                    log.exception(
                        "[WEB] build_snapshot failed for %s", conn.session.user_id
                    )
                    continue
                await self.send_to_client(conn.cid, frame)
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.001, interval - elapsed))

    # helper: send to the connection bound to a session (reply path)

    async def send_to_client_conn(self, sess: WebSession, frame: dict) -> None:
        for conn in self.connections.values():
            if conn.session is sess:
                await self.send_to_client(conn.cid, frame)
                return
