"""PREVIEW HARNESS for the local game stack (extends scripts/_local_game_stack.py).

A remote-controllable test bench so a reviewer can exercise REAL server
mechanics from the Freebuff Preview tab without touching the Discord bot.
MULTI-SESSION (user 28/09): every browser tab mints a unique user_id +
channel in the base stack, so 2-4 preview tabs play in SEPARATE worlds at
the same time (each on its own port instance, see below).

  preview_cmd frames (browser -> stack):
    {"type":"preview_cmd","cmd":"clock","value":21|12|"normal"|"day"|...}
        Pin/reset the accelerated in-game clock (set_ingame_time) — flips the
        night gate for zombies/meteors and the map tint in one move. Also
        accepts "HH:MM" for any arbitrary hour.
    {"type":"preview_cmd","cmd":"meteor","value":"here"|"rand"|"auto"|"off"}
        Summon a meteor at the player tile / 6-12 tiles away / toggle the
        20%-halving night scheduler on or off (auto = on).
    {"type":"preview_cmd","cmd":"weather","value":"rain|snow|wind|storm|...|normal"}
        Force rt.weather_key ("" = auto/normal).
    {"type":"preview_cmd","cmd":"zombies","value":"pack"|"none"}
        Spawn the full web pack around the player instantly, or despawn all.
    {"type":"preview_cmd","cmd":"animals","value":"rand"|<kind>|"none"}
        Daytime wildlife bench (spawn one / despawn every ambient animal).
    {"type":"preview_cmd","cmd":"bite","value":<dmg>|undefined}
        One hostile bite on THIS tab's player (default 10 dmg, never kills).
    {"type":"preview_cmd","cmd":"heal"}
        Full HP/mana/stamina + clear every status effect on THIS tab's player.
    {"type":"preview_cmd","cmd":"kill"}
        Drop THIS tab's player to 0 HP (death overlay + 5s respawn countdown).
    {"type":"preview_cmd","cmd":"respawn"}
        Instant revive on a random walkable tile (skip the 5s wait).
    {"type":"preview_cmd","cmd":"status","value":"infection"|"poison"|"off"}
        Apply a REAL debuff (game.status_effects) on THIS tab's player —
        the 20 Hz tick + splats run for real, unlike the panel's local demo.
    {"type":"preview_cmd","cmd":"tp","value":"x,y"|"rand"|"spawn"}
        Teleport THIS tab's player: to a tile, a random walkable tile, or
        the map spawn area. Walkable-checked; clamps into the map.
    {"type":"preview_cmd","cmd":"map","value":"ekonia/overworld|forest|cave_area1"}
        Destroy + re-create the runtime on another map (fresh solo preview).
    {"type":"preview_cmd","cmd":"state"}
        Ask for a state dump -> answers a "preview_state" push (mobs, clock,
        weather, self hp/pos) — the panel's status line.

Server -> browser pushes:
    {"type":"push","message":...}        reused as the preview log feed
    {"type":"preview_state",...}         the status dump above

Run:  .venv/Scripts/python scripts/_preview_stack.py   (port 8898 by default;
set PREVIEW_PORT=0 to auto-pick a free port — the chosen URL is printed).
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
)
from web_api.snapshots import build_welcome  # noqa: E402

DEFAULT_MAP = "ekonia/overworld"
PREVIEW_PORT = int(os.environ.get("PREVIEW_PORT", "8898"))


def _walkable_spots(rt, exclude_players=True) -> list[tuple[int, int]]:
    """Every walkable tile on the runtime's map (occupied tiles excluded)."""
    occupied: set[tuple[int, int]] = (
        {(p.x, p.y) for p in rt.state.get_visible_players()}
        if exclude_players else set()
    )
    return [
        (x, y) for y in range(rt.map_data.height)
        for x in range(rt.map_data.width)
        if rt.collision.is_walkable(x, y) and (x, y) not in occupied
    ]


def _resolve_tp_tile(rt, player, value) -> tuple[int, int] | None:
    """"x,y" | "rand" | "spawn" -> a walkable tile, or None when impossible."""
    if value == "rand" or value is None:
        spots = _walkable_spots(rt)
        return random.choice(spots) if spots else None
    if value == "spawn":
        # The map's top-left walkable region — mirrors the original join spawn
        # scan in manager.create_runtime (walkable, not on another player).
        for y in range(min(12, rt.map_data.height)):
            for x in range(min(12, rt.map_data.width)):
                if rt.collision.is_walkable(x, y):
                    return (x, y)
        spots = _walkable_spots(rt)
        return random.choice(spots) if spots else None
    # "x,y"
    try:
        xs, ys = str(value).split(",")
        tx, ty = int(xs), int(ys)
    except (ValueError, AttributeError):
        return None
    w, h = rt.map_data.width, rt.map_data.height
    tx = max(0, min(w - 1, tx))
    ty = max(0, min(h - 1, ty))
    if not rt.collision.is_walkable(tx, ty):
        # Spiral out to the nearest walkable tile instead of refusing.
        for r in range(1, 6):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = tx + dx, ty + dy
                    if 0 <= nx < w and 0 <= ny < h and rt.collision.is_walkable(nx, ny):
                        return (nx, ny)
        return None
    return (tx, ty)


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
            "bite": self._cmd_bite,
            "animals": self._cmd_animals,
            "map": self._cmd_map,
            "state": self._cmd_state,
            # player admin (user 28/09 overhaul)
            "heal": self._cmd_heal,
            "hurt": self._cmd_hurt,
            "kill": self._cmd_kill,
            "respawn": self._cmd_respawn,
            "status": self._cmd_status,
            "tp": self._cmd_tp,
            "give": self._cmd_give,
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

    # ---- clock / world ----

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
        elif isinstance(value, str) and ":" in value:
            # Arbitrary "HH:MM" — the panel's time input sends this.
            try:
                hh, mm = value.split(":", 1)
                sec_of_day = (int(hh) % 24) * 3600 + (int(mm) % 60) * 60
            except ValueError:
                await self._send(cid, {"type": "push", "message": f"[preview] Giờ lạ: {value}"})
                return
            sec = set_ingame_time(sec_of_day)
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
            tx = int(px + random.uniform(-1, 1) + __import__("math").cos(ang) * dist)
            ty = int(py + __import__("math").sin(ang) * dist)
        w = rt.map_data.width
        h = rt.map_data.height
        m = meteor_summon(rt.meteors, now, tx, ty, rng=getattr(self.gm, "zombie_rng", random.Random()))
        m.tx = max(0, min(w - 1, m.tx))
        m.ty = max(0, min(h - 1, m.ty))
        where = "tại chỗ bạn đứng" if value != "rand" else f"({m.tx},{m.ty}) gần bạn"
        await self._send(cid, {
            "type": "push", "kind": "danger",
            "message": f"[preview] ☄️ CẢNH BÁO: Thiên thạch rơi {where} — 8s nữa!",
        })

    async def _cmd_weather(self, cid: int, uid: int, rt, value) -> None:
        if value in (None, "", "normal", "auto"):
            rt.weather_key = ""
            await self._send(cid, {"type": "push", "message": "[preview] Thời tiết về tự động."})
            return
        rt.weather_key = str(value)
        await self._send(cid, {"type": "push", "message": f"[preview] Thời tiết = {value}"})

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

    # ---- mobs / wildlife ----

    async def _cmd_zombies(self, cid: int, uid: int, rt, value) -> None:
        from game.zombies import iter_web_zombies, remove_web_zombie, web_spawn_one

        if value == "none":
            for z in iter_web_zombies(rt.state):
                remove_web_zombie(rt.state, z.zombie_id)
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

    async def _cmd_animals(self, cid: int, uid: int, rt, value) -> None:
        from game.zombies import iter_web_zombies, remove_web_zombie, spawn_animal_one

        if value == "none":
            n = 0
            for z in iter_web_zombies(rt.state):
                if getattr(z, "ambient", False):
                    remove_web_zombie(rt.state, z.zombie_id)
                    n += 1
            await self._send(cid, {"type": "push", "message": f"[preview] Đã dọn {n} con vật."})
            return
        player = rt.state.get_player(uid)
        if player is None:
            await self._send(cid, {"type": "push", "message": "[preview] Chưa có player trên map."})
            return
        rng = getattr(self.gm, "zombie_rng", random.Random())
        kind = None if value in (None, "", "rand") else str(value)
        z = spawn_animal_one(rt.state, rt.collision, [player], rng, kind=kind)
        if z is None:
            await self._send(cid, {"type": "push", "message": "[preview] Spawn động vật THẤT BẠI (hết ô trống?)."})
        else:
            await self._send(cid, {"type": "push", "message": f"[preview] Spawn 1 {z.kind}."})

    async def _cmd_bite(self, cid: int, uid: int, rt, value) -> None:
        """Simulate ONE hostile bite on the preview player (default -10 HP,
        never kills) — lets a reviewer exercise damage/death UI instantly."""
        player = rt.state.get_player(uid)
        if player is None:
            await self._send(cid, {"type": "push", "message": "[preview] Chưa có player trên map."})
            return
        dmg = 10
        if isinstance(value, (int, float)) and value > 0:
            dmg = int(value)
        player.hp = max(1, player.hp - dmg)
        # Feed the hitsplat stream so the number floats over the victim too.
        feed = getattr(rt.state, "recent_damage", None)
        if feed is not None:
            feed.append((time.time(), uid, dmg, "zombie"))
            del feed[:-40]
        await self._send(cid, {"type": "push", "message": f"[preview] Mô phỏng 1 cắn (-{dmg} HP)."})

    # ---- player admin (user 28/09) ----

    def _player_or_msg(self, cid: int, uid: int, rt):
        player = rt.state.get_player(uid)
        if player is None:
            asyncio.ensure_future(self._send(cid, {"type": "push", "message": "[preview] Chưa có player trên map."}))
            return None
        return player

    async def _cmd_heal(self, cid: int, uid: int, rt, value) -> None:
        player = self._player_or_msg(cid, uid, rt)
        if player is None:
            return
        player.hp = player.max_hp
        player.mana = getattr(player, "max_mana", player.mana)
        player.stamina = getattr(player, "max_stamina", player.stamina)
        player.status_effects = []
        player.last_damaged_at = None
        player.regen_bank = 0.0
        await self._send(cid, {"type": "push", "message": "[preview] ❤️ Full HP/mana/stamina + xoá sạch debuff."})

    async def _cmd_hurt(self, cid: int, uid: int, rt, value) -> None:
        """Set HP = ratio * max (default 0.15) — the low-HP bloody screen
        gate lives CLIENT-side (hp/max_hp <= 0.15 in the web client), so
        this only sets the truth and lets the client react."""
        player = self._player_or_msg(cid, uid, rt)
        if player is None:
            return
        try:
            ratio = float(value) if value not in (None, "") else 0.15
        except (TypeError, ValueError):
            ratio = 0.15
        ratio = max(0.0, min(1.0, ratio))
        player.hp = max(1, round(player.max_hp * ratio))  # floor 1: stay alive
        player.last_damaged_at = time.time()  # hold off regen so it sticks
        await self._send(cid, {"type": "push", "message": f"[preview] 🩸 HP = {player.hp}/{player.max_hp} ({ratio:.0%})."})

    async def _cmd_kill(self, cid: int, uid: int, rt, value) -> None:
        player = self._player_or_msg(cid, uid, rt)
        if player is None:
            return
        if not player.alive:
            await self._send(cid, {"type": "push", "message": "[preview] Đang chết rồi."})
            return
        player.hp = 0
        player.visible = False
        player.dead_until = time.time() + 5.0
        player.death_reason = "[preview] tự kết liễu"
        self.gm._schedule_respawn(rt, uid)
        await self._send(cid, {"type": "push", "message": "[preview] ☠️ Chết (5s hồi sinh) — /respawn để bỏ chờ."})

    async def _cmd_respawn(self, cid: int, uid: int, rt, value) -> None:
        player = self._player_or_msg(cid, uid, rt)
        if player is None:
            return
        spots = _walkable_spots(rt)
        player.hp = player.max_hp
        player.visible = True
        player.dead_until = None
        player.death_reason = None
        player.status_effects = []
        if spots:
            player.revive_teleport(*random.choice(spots))
        self.gm._schedule_save(rt, player)
        at = f"({player.x},{player.y})" if spots else "(giữ nguyên chỗ)"
        await self._send(cid, {"type": "push", "message": f"[preview] ✨ Hồi sinh full máu {at}."})

    async def _cmd_status(self, cid: int, uid: int, rt, value) -> None:
        """REAL debuffs (game.status_effects) — the 20 Hz tick, DOT splats and
        rail countdown all run for real, unlike the panel's local demo."""
        from game import status_effects as se

        player = self._player_or_msg(cid, uid, rt)
        if player is None:
            return
        if value in (None, "", "off", "clear"):
            player.status_effects = []
            await self._send(cid, {"type": "push", "message": "[preview] Đã xoá sạch status effects."})
            return
        effect_id = str(value)
        if effect_id not in se.EFFECTS:
            await self._send(cid, {"type": "push", "message": f"[preview] Không biết effect: {effect_id} (có: {', '.join(se.EFFECTS)})"})
            return
        existing = se.get_effect(player, effect_id)
        if existing is not None:
            eff = se.refresh_effect(player, effect_id, None)
        else:
            eff = se._apply_fresh(player, effect_id, 1)
        dmg = eff.get("dmg", 5)
        interval = eff.get("interval", 2.0)
        # First DOT tick lands right away so the green splat shows instantly.
        player.hp = max(1, player.hp - dmg)
        feed = getattr(rt.state, "recent_damage", None)
        if feed is not None:
            feed.append((time.time(), uid, dmg, "status"))
            del feed[:-40]
        await self._send(cid, {"type": "push", "message": f"[preview] ☣️ Áp {se.EFFECTS[effect_id]['name']} — {dmg} dmg mỗi {interval:.0f}s."})

    async def _cmd_give(self, cid: int, uid: int, rt, value) -> None:
        """[preview] give <item_id> [qty] — REAL inventory add (Inventory.add
        bumps the bag version, so the next snapshot pushes the bag to the
        client). Unknown ids fall back to a handy demo set."""
        player = self._player_or_msg(cid, uid, rt)
        if player is None:
            return
        inv = rt.inventories.get(uid)
        if inv is None:
            # Preview sessions don't run load_inventories — create on demand.
            from game.inventory import Inventory
            inv = Inventory()
            rt.inventories[uid] = inv
        parts = str(value or "").split()
        demo = ["potion_hp", "potion_mp", "iron_sword", "iron_pickaxe",
                "wood_axe", "stone", "torch", "leatherhelmet"]
        item_id = parts[0] if parts else "demo"
        try:
            qty = int(parts[1]) if len(parts) > 1 else 1
        except ValueError:
            qty = 1
        if item_id == "demo":
            for iid in demo:
                inv.add(iid, 3 if iid in ("potion_hp", "stone") else 1)
            await self._send(cid, {"type": "push", "message": "[preview] 🎁 Đã thêm bộ demo (potion, sword, block, giáp)."})
            return
        from game.items import ITEM_REGISTRY
        from game.blocks import BLOCK_REGISTRY
        if item_id not in ITEM_REGISTRY and item_id not in BLOCK_REGISTRY:
            await self._send(cid, {"type": "push", "message": f"[preview] Không biết item: {item_id}"})
            return
        inv.add(item_id, max(1, qty))
        await self._send(cid, {"type": "push", "message": f"[preview] 🎁 +{qty} {item_id}."})

    async def _cmd_tp(self, cid: int, uid: int, rt, value) -> None:
        player = self._player_or_msg(cid, uid, rt)
        if player is None:
            return
        tile = _resolve_tp_tile(rt, player, value)
        if tile is None:
            await self._send(cid, {"type": "push", "message": f"[preview] Không tìm được ô đi được cho '{value}'."})
            return
        # Despawn every hostile first: waking up inside the pack = instant
        # re-death (the "treo màn hồi sinh" loop).
        from game.zombies import iter_web_zombies, remove_web_zombie

        cleared = 0
        for z in list(iter_web_zombies(rt.state)):
            if not getattr(z, "ambient", False):
                remove_web_zombie(rt.state, z.zombie_id)
                cleared += 1
        player.revive_teleport(*tile)
        self.gm._schedule_save(rt, player)
        note = f", dọn {cleared} quái quanh chỗ cũ" if cleared else ""
        await self._send(cid, {"type": "push", "message": f"[preview] 🌀 Teleport tới ({tile[0]},{tile[1]}){note}."})

    # ---- state dump ----

    async def _cmd_state(self, cid: int, uid: int, rt, value) -> None:
        from game import status_effects as se
        from game.zombies import iter_web_zombies
        from rendering.daynight import ingame_seconds

        s = ingame_seconds()
        player = rt.state.get_player(uid)
        self.last_state = {
            "map": rt.map_data.map_id,
            "clock": f"{s // 3600:02d}:{s % 3600 // 60:02d}",
            "night": s >= 20 * 3600 or s < 6 * 3600,
            "weather": getattr(rt, "weather_key", "") or "auto",
            "zombies": len(iter_web_zombies(rt.state)),
            "meteors": len(rt.meteors.active),
            "felled_tonight": rt.meteors.felled_tonight,
            "auto_meteor": self.auto_meteor,
            "self": {
                "name": self.sessions[cid]["name"],
                "hp": player.hp if player else 0,
                "max_hp": player.max_hp if player else 0,
                "x": round(player.x_f, 1) if player else 0,
                "y": round(player.y_f, 1) if player else 0,
                "dead": bool(player is not None and not player.alive),
                "effects": se.payload(player) if player else [],
            },
        }
        await self._send(cid, {"type": "preview_state", **self.last_state})

    async def serve_index(self, request: web.Request) -> web.Response:
        """index.html MUST revalidate every load: it references a content-hashed
        bundle. A cached index kept the browser booting a STALE bundle (old
        MOB_SHEETS frame counts) long after `npm run build` — mobs then drew
        frames from the wrong rows (\"nhieu layer de len nhau\")."""
        body = (DIST_DIR / "index.html").read_bytes()
        return web.Response(
            body=body,
            content_type="text/html",
            charset="utf-8",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # ---------------- overrides ----------------

    async def _frame(self, cid: int, frame: dict) -> None:
        if frame.get("type") == "preview_cmd":
            if cid not in self.sessions:
                # ISOLATED: every connection is its own user (multi-tab safe).
                from scripts._local_game_stack import _fresh_preview_user

                uid0 = _fresh_preview_user()
                self.sessions[cid] = {"user_id": uid0, "name": f"Khach-{uid0 % 10000}"}
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
    # serve_index first: add_static("/") would shadow it (static mount wins
    # over later-registered routes for "/").
    app.router.add_get("/", stack.serve_index)
    app.router.add_get("/index.html", stack.serve_index)
    app.router.add_static("/", str(DIST_DIR))
    stack.snap_task = asyncio.get_event_loop().create_task(stack._snapshot_loop())

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PREVIEW_PORT)
    await site.start()
    url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}" if PREVIEW_PORT == 0 else f"http://127.0.0.1:{PREVIEW_PORT}"
    print(f"[preview-stack] ready on {url}/?preview=1  (dist={DIST_DIR})".encode("ascii", "replace").decode("ascii"), flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
