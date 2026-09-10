import logging

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import asyncio
import random

import discord

log = logging.getLogger("GAME")

from game.actions import (
    AimAction,
    AimResetAction,
    AttackAction,
    BreakBlockAction,
    ChopAction,
    MoveAction,
    PlaceBlockAction,
    ShovelAction,
    TurnAction,
)

# Zombie chase cadence. Base speed is ALWAYS 1 tile per granted turn (never
# scales with the player's step size); what scales is how OFTEN the zombie is
# granted a turn: every ZOMBIE_ACTION_SPEED_DIVISOR-th non-intermediate player
# action (2 -> half player speed: player moves 2x, zombie 1x; player 6x,
# zombie 3x). On every other action the zombie still gets a turn with
# ZOMBIE_FAST_CHASE_CHANCE, so chases occasionally match player pace and stay
# threatening instead of being trivially outrun.
ZOMBIE_ACTION_SPEED_DIVISOR = 2
ZOMBIE_FAST_CHASE_CHANCE = 0.25

from game.collision import Collision
from game.inventory import Inventory
from game.map_loader import MapData, load_map
from game.npc import NpcMap, load_npcs
from game.rules import (
    apply_aim,
    apply_aim_reset,
    apply_attack,
    apply_break_block,
    apply_move,
    apply_place_block,
    apply_turn,
    apply_weather_regen,
)
from game.state import ActionResult, GameState, Player
from game.resources import NODE_DEFS, ResourceGrid, apply_chop
from game.terrain import TerrainGrid
from game.terrain_rules import apply_scoop
from game.weather import WeatherState
from game.zombies import (
    advance_visible_zombies,
    is_night,
    tick_zombies,
)
from rendering.daynight import ingame_seconds
from config import (
    WEB_RUN_SPEED,
    WEB_TICK_HZ,
    WEB_WALK_SPEED,
    WORLD_TICK_SECONDS,
    ZOMBIE_SPAWN_CHANCE,
)
from game.zombies import ZOMBIE_AREA_MAX_COUNT
from persistence.repositories import load_all_inventory, save_inventory_order
from rendering.camera import Camera, DEFAULT_VIEW_H, DEFAULT_VIEW_W
from discord_ui.editor import ChannelEditGate
from config import HOTBAR_SLOTS
from game.session import REASON_INACTIVITY, SessionTracker
from PIL import Image

# Fallback cooldown if the bot never sets one (see config.SESSION_NOTICE_COOLDOWN_SEC).
SESSION_NOTICE_COOLDOWN_SEC_DEFAULT = 300.0


def _loop_time() -> float:
    """Monotonic wall clock for the web sim loop (time.monotonic — one shared
    timeline with the session watchdog, like manager.touch_session)."""
    import time as _time

    return _time.monotonic()


@dataclass
class WebSession:
    """One connected web client's live input state (runtime-only).

    The client streams its held movement vector every tick (or on change);
    the server integrates it at WEB_WALK/WEB_RUN_SPEED — the client never
    sets its own speed (anti-speedhack, plan acceptance 2).
    """

    dx: float = 0.0
    dy: float = 0.0
    running: bool = False
    last_tick: float = 0.0


def _web_direction(dx: float, dy: float) -> str:
    """Dominant-direction name for a continuous input vector (8-way)."""
    if abs(dx) > abs(dy):
        return "EAST" if dx > 0 else "WEST"
    if abs(dy) > abs(dx):
        return "SOUTH" if dy > 0 else "NORTH"
    if dx > 0:
        return "SOUTH_EAST" if dy > 0 else "NORTH_EAST"
    return "SOUTH_WEST" if dy > 0 else "NORTH_WEST"


@dataclass
class PlayerScreen:
    """Per-player screen state: one Discord message (image + own D-pad) per
    player. Each screen owns its camera (viewport centred on its owner), its
    last rendered composite, and its own auto-move/tool state, so players never
    contend for the same message-edit bucket."""

    user_id: int
    # The image-only map message. The D-pad lives in the separate controls
    # message below it so input ACKs never wait for an attachment upload.
    message_id: Optional[int] = None
    controls_message_id: Optional[int] = None
    # True once this player has the split map → controls → hub layout. False
    # keeps the legacy combined-message fallback alive during migration/tests.
    split_layout: bool = False
    hub_message_id: Optional[int] = None
    # Message id of this player's open inventory panel (None = closed). The
    # panel is a SEPARATE message posted below the hub so the hub HUD stays
    # visible on top while the inventory is open.
    inventory_message_id: Optional[int] = None
    camera: Optional[Camera] = None
    composite: Optional[Image.Image] = None
    step_size: int = 1
    auto_running: bool = False
    auto_armed: bool = False
    auto_direction: Optional[object] = None
    auto_task: Optional[asyncio.Task] = None
    # Sandbox: block currently selected for the 🧱 place button.
    selected_block: str = "stone"
    # Build Mode: when ON the D-pad moves the aim cursor instead of stepping.
    build_mode: bool = False
    # Consecutive travelling steps (manual). >= 4 fades the facing indicators
    # so long treks stay visually quiet; any aim/tool action resets it.
    travel_steps: int = 0
    # The persistent MapView instance bound to this screen's message, so other
    # panels (inventory) can refresh its hotbar labels in place.
    map_view: Optional[object] = None
    # The open inventory panel (own message below the hub), if any — refreshed
    # in the background whenever the bag changes.
    inventory_view: Optional[object] = None
    # The open crafting panel (same message slot as the inventory panel: the
    # 🛠️ Chế tạo button swaps the panel in place, so only one is set).
    craft_message_id: Optional[int] = None
    craft_view: Optional[object] = None
    # Latest-wins flag: True while an interaction-response frame is in flight,
    # so extra presses during that window coalesce instead of racing renders.
    rendering: bool = False

    def clear_panels(self) -> None:
        """Drop every side-panel reference (inventory / craft).

        Called when the player MOVES on their screen: panels must close so
        only the screen+hub pair remains (their messages are deleted by the
        adapter helper)."""
        self.inventory_message_id = None
        self.inventory_view = None
        self.craft_message_id = None
        self.craft_view = None


@dataclass
class ScenarioRuntime:
    channel_id: int
    message_id: Optional[int]
    state: GameState
    map_data: MapData
    collision: Collision
    members: Dict[int, object] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dirty: bool = False
    composite: Optional[Image.Image] = None
    camera: Optional[Camera] = None
    # Per-player screens (the current model). ``camera``/``message_id`` above
    # remain as the legacy shared-screen view + hub sizing template.
    screens: Dict[int, "PlayerScreen"] = field(default_factory=dict)
    _save_task: Optional[asyncio.Task] = None
    pending_player: Optional[Player] = None
    # UI/form state (per channel, not persisted): step size + auto-move.
    # (Legacy shared-screen fields; per-player screens carry their own.)
    step_size: int = 1
    auto_running: bool = False
    auto_armed: bool = False
    auto_user_id: Optional[int] = None
    auto_direction: Optional[object] = None
    auto_task: Optional[asyncio.Task] = None
    # Hub message + panel state.
    hub_message_id: Optional[int] = None
    # One creation lock per player prevents concurrent refresh/recovery paths
    # from publishing duplicate hubs.
    hub_locks: Dict[int, asyncio.Lock] = field(default_factory=dict)
    hub_panel: str = "hud"  # hud | inventory | settings | dialogue
    hub_dialogue_npc: Optional[str] = None
    hub_dialogue_node: Optional[str] = None
    # Current weather shown on the hub HUD (key into the weather icon set).
    weather_key: str = "sun_clouds"
    # Latest national weather snapshot (drives HUD + gameplay modifiers).
    weather_state: Optional[WeatherState] = None
    # True when an admin pinned this scenario via /setweather (manual key).
    # While pinned, the auto _weather_loop only refreshes weather_state and
    # never clobbers weather_key; unpinned only by the "Tự Động" resume path.
    weather_manual: bool = False
    # Storm lightning event: seed for the current strike (0/None = calm sky).
    # Re-randomised by GameManager._lightning_loop every 5-13 s, so strikes
    # change position/size instead of strobing the same bolt.
    lightning_seed: int = 0
    # Animated weather FX gate (rain/snow/storm GIF on screens). OFF by
    # default: the 6-frame GIF composition was the dominant CPU cost of busy
    # scenarios. Toggled per scenario by admins (settings panel / /setweather
    # panel path); the hub HUD weather icon is unaffected.
    weather_fx_enabled: bool = False
    # Per-player item bags, persisted via repositories.
    inventories: Dict[int, Inventory] = field(default_factory=dict)
    # Per-player hotbar bindings (slot 0..8 -> item_id or absent), persisted.
    # LEGACY: kept only to migrate old saved bindings on load (see
    # manager.load_hotbars); the live hotbar is a PROJECTION of the first
    # HOTBAR_SLOTS stacks of the ordered inventory.
    hotbars: Dict[int, Dict[int, str]] = field(default_factory=dict)
    # Data-driven NPCs for this map.
    npc_map: NpcMap = field(default_factory=lambda: NpcMap([], {}))
    # Harvestable nodes (trees/bushes) indexed from the map's resource layer.
    # Chop progress + regrow deadlines; felled nodes persist via repositories.
    resources: Optional[ResourceGrid] = None
    # Scoopable grass tufts ("cỏ" layer): scooping reveals the bare-dirt base
    # and grants dirt. Scooped tiles persist via repositories (per scenario).
    terrain: Optional[TerrainGrid] = None
    # Connected web clients (user_id -> WebSession). Runtime-only; a session
    # exists only while the web client's socket is up.
    web_sessions: Dict[int, "WebSession"] = field(default_factory=dict)
    # Held hotbar slot per player (user_id -> slot 0..7). Runtime-only UI state
    # so every client can SEE what everyone else holds (the hand + tool icon).
    # Web sessions mirror here on select_slot; Discord players stay at slot 0
    # (their hotbar projection's first stack).
    held_slots: Dict[int, int] = field(default_factory=dict)


class GameManager:
    def __init__(self, assets_dir):
        self.assets_dir = assets_dir
        self.runtimes: Dict[int, ScenarioRuntime] = {}
        # Side-world runtimes (the /khutraodoi trade lobby + interiors), keyed
        # by (channel_id, map_id). The main world stays in ``runtimes``.
        self.side_runtimes: Dict[tuple, ScenarioRuntime] = {}
        # Data-driven portal config (teleport tiles between worlds).
        from game.travel import load_portals

        self.portals = load_portals(assets_dir)
        self.renderer = None
        self.db = None
        self.weather_service = None
        self.weather_task: Optional[asyncio.Task] = None
        self.lightning_task: Optional[asyncio.Task] = None
        self.zombie_task: Optional[asyncio.Task] = None
        self.zombie_rng = random.Random()
        # Continuous (web client) simulation loop. Started lazily by the first
        # web session join; self-stops when the last session drops so an idle
        # panel pays nothing (rule 15/16: per-runtime state, no global lock —
        # the loop takes each runtime's own lock per tick).
        self.web_task: Optional[asyncio.Task] = None
        self.respawn_tasks: Dict[tuple, asyncio.Task] = {}
        # Serializes real Discord message edits per (channel, message) and spaces
        # them so we never hit the edit rate-limit bucket (429). See editor.py.
        self.edit_gate = ChannelEditGate()
        # Per-player session watchdog (game/session.py): activity ledger + the
        # asyncio loop that ends idle sessions. ``session_adapter`` is injected by
        # bot.py (an object with async end(channel_id, user_id, reason, detail)) so
        # the game layer never imports discord.py.
        self.sessions = SessionTracker()
        self.session_adapter = None
        self.session_task: Optional[asyncio.Task] = None
        self.session_timeout_minutes: float = 30.0
        self.session_check_interval: float = 60.0
        self.session_notice_cooldown: float = SESSION_NOTICE_COOLDOWN_SEC_DEFAULT
        # (channel_id, user_id) -> monotonic time of the last session-end notice;
        # debounces repeated notices (error retries, watchdog vs manual paths).
        self._session_notice_at: Dict[tuple, float] = {}

    # ----- session watchdog (inactivity auto-end + reason notices) -----

    def configure_sessions(
        self, timeout_minutes: float, check_interval: float,
        cooldown: float = SESSION_NOTICE_COOLDOWN_SEC_DEFAULT,
    ) -> None:
        self.session_timeout_minutes = timeout_minutes
        self.session_check_interval = check_interval
        self.session_notice_cooldown = cooldown

    def start_sessions(self, enabled: bool = True) -> None:
        if not enabled or self.session_timeout_minutes <= 0:
            return
        if self.session_task is None or self.session_task.done():
            self.session_task = asyncio.create_task(self._session_loop())

    async def stop_sessions(self) -> None:
        task = self.session_task
        self.session_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def touch_session(self, channel_id: int, user_id: int) -> None:
        """Record user activity (any button press / screen spawn).

        Uses ``time.monotonic`` (NOT ``loop.time()``) so every session clock
        in the process shares one timeline regardless of which loop created
        it."""
        import time as _time

        self.sessions.touch(channel_id, user_id, _time.monotonic())

    async def notify_session_event(
        self, channel_id: int, user_id: int, reason: str,
        detail: str = "", *, ended: bool = True,
    ) -> None:
        """Post a notice only after an authoritative session teardown.

        Live-session failures are deliberately log-only. A transient callback,
        render, or repair error must never look like a session shutdown to the
        channel user."""
        import time as _time

        if not ended:
            log.warning(
                "[SESSION] live-session event (no channel notice) user=%s channel=%s "
                "reason=%s detail=%s",
                user_id, channel_id, reason, detail,
            )
            return

        key = (channel_id, user_id)
        now = _time.monotonic()
        last = self._session_notice_at.get(key)
        if last is not None and now - last < self.session_notice_cooldown:
            return
        self._session_notice_at[key] = now
        adapter = self.session_adapter
        if adapter is None:
            return
        try:
            await adapter.notify(
                channel_id, user_id, reason, detail, ended=ended,
                duration=self.sessions.duration(channel_id, user_id, now),
            )
        except Exception as e:  # noqa: BLE001 — notices must never break gameplay
            log.warning("[SESSION] notice adapter failed for %s: %s", user_id, e)

    def discard_session(self, channel_id: int, user_id: int) -> None:
        """Forget a session silently (no notice) — for voluntary paths."""
        self.sessions.discard(channel_id, user_id)
        self._session_notice_at.pop((channel_id, user_id), None)

    async def _session_loop(self) -> None:
        """End every session idle past the timeout, one adapter call at a time."""
        import time as _time

        while True:
            await asyncio.sleep(self.session_check_interval)
            now = _time.monotonic()
            timeout = self.session_timeout_minutes * 60.0
            for channel_id, user_id in self.sessions.due(now, timeout):
                rt = self.runtimes.get(channel_id)
                if rt is None:
                    self.discard_session(channel_id, user_id)
                    continue
                if user_id not in rt.screens:
                    # Activity without an open screen (e.g. legacy shared-screen
                    # presses): nothing to tear down — drop the ledger entry.
                    self.discard_session(channel_id, user_id)
                    continue
                # Inactivity end is authoritative: stop tracking FIRST so a
                # slow adapter call cannot re-queue the same session next tick.
                info = self.sessions.discard(channel_id, user_id)
                idle_for = (
                    now - info.last_activity if info is not None else timeout
                )
                try:
                    if self.session_adapter is not None:
                        await self.session_adapter.end(
                            channel_id, user_id, REASON_INACTIVITY,
                            detail=f"người chơi không thao tác đến {idle_for / 60:.0f} phút",
                        )
                except Exception as e:  # noqa: BLE001 — watchdog must survive
                    log.warning(
                        "[SESSION] inactivity end failed for %s in %s: %s",
                        user_id, channel_id, e,
                    )

    # ----- web client sessions (continuous movement) -----

    def _channel_web_count(self, channel_id: int) -> int:
        """Web players across the channel's main world + side worlds."""
        n = 0
        rt = self.runtimes.get(channel_id)
        if rt is not None:
            n += sum(1 for p in rt.state.players.values() if p.is_web)
        for side in self.side_runtimes.values():
            if side.channel_id == channel_id:
                n += sum(1 for p in side.state.players.values() if p.is_web)
        return n

    def register_web_session(self, channel_id: int, user_id: int,
                             display_name: str = "") -> bool:
        """Attach a web client to the scenario as its Discord-known user.

        Creates the shared Player on demand (same object every client sees).
        Returns False when the scenario does not exist or the web cap is hit.
        """
        from config import WEB_MAX_PLAYERS

        rt = self.runtimes.get(channel_id)
        if rt is None:
            return False
        player = rt.state.get_player(user_id)
        if player is None:
            if self._channel_web_count(channel_id) >= WEB_MAX_PLAYERS:
                return False
            player = rt.state.add_player(
                user_id, display_name or f"player-{user_id}",
                rt.map_data.spawn[0], rt.map_data.spawn[1],
            )
            player.x, player.y = rt.map_data.spawn
        player.is_web = True
        if player.float_moved:
            player.sync_int_from_float()
        else:
            player.sync_float_from_int()
        if not rt.map_data.is_walkable(player.x, player.y):
            player.x, player.y = rt.map_data.spawn
            player.sync_float_from_int()
        rt.web_sessions[user_id] = WebSession(last_tick=_loop_time())
        self.touch_session(channel_id, user_id)
        return True

    def drop_web_session(self, channel_id: int, user_id: int) -> None:
        """Detach one web client: freeze the player on their current tile and
        stop the sim loop when no web session remains anywhere."""
        rt = self.runtime_of(channel_id, user_id) or self.runtimes.get(channel_id)
        if rt is None:
            return
        rt.web_sessions.pop(user_id, None)
        player = rt.state.get_player(user_id)
        if player is not None and player.is_web:
            player.is_web = False
            player.sync_int_from_float()
            self._schedule_save(rt, player)
        remaining = any(
            getattr(r, "web_sessions", None)
            for r in list(self.runtimes.values()) + list(self.side_runtimes.values())
        )
        # The tick loop self-exits on its next pass when sessions are gone;
        # cancelling here would need a running loop (this method is also
        # called from sync/test contexts), so just let the loop wind down.
        _ = remaining

    def web_input(self, channel_id: int, user_id: int,
                  dx: float, dy: float, running: bool = False) -> bool:
        """Store one input vector (called from the WS handler)."""
        rt = self.runtime_of(channel_id, user_id) or self.runtimes.get(channel_id)
        if rt is None:
            return False
        sess = rt.web_sessions.get(user_id)
        if sess is None:
            return False
        sess.dx = max(-1.0, min(1.0, float(dx)))
        sess.dy = max(-1.0, min(1.0, float(dy)))
        sess.running = bool(running)
        self.touch_session(channel_id, user_id)
        return True

    def start_web_tick(self) -> None:
        if self.web_task is not None and not self.web_task.done():
            return
        try:
            self.web_task = asyncio.create_task(self._web_tick_loop())
        except RuntimeError:
            # No running loop (sync/test context): the loop starts on the
            # first async entry point (WS handler) via ensure_web_tick_async.
            pass

    async def ensure_web_tick_async(self) -> None:
        if self.web_task is None or self.web_task.done():
            self.web_task = asyncio.create_task(self._web_tick_loop())

    async def stop_web_tick(self) -> None:
        task = self.web_task
        self.web_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _web_tick_loop(self) -> None:
        """20 Hz continuous-movement integration for connected web clients.

        One shared task; each tick takes the touched runtime's OWN lock
        (rule 15/16). Discord clients see the resulting tiles on their normal
        coalesced refresh beats — no extra Discord renders from here.
        """
        interval = 1.0 / max(1.0, WEB_TICK_HZ)
        while True:
            started = asyncio.get_running_loop().time()
            now = _loop_time()
            # Main worlds + side worlds (trade lobby/interiors): a web player
            # can stand in any of them.
            for rt in list(self.runtimes.values()) + list(self.side_runtimes.values()):
                sessions = getattr(rt, "web_sessions", None)
                if sessions:
                    await self._web_tick_runtime(rt, sessions, now)
            if not any(
                getattr(r, "web_sessions", None)
                for r in list(self.runtimes.values()) + list(self.side_runtimes.values())
            ):
                return  # last web client left; stop paying for the loop
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.001, interval - elapsed))

    async def _web_tick_runtime(self, rt: ScenarioRuntime,
                                sessions: Dict[int, WebSession], now: float) -> None:
        moved_any = False
        async with rt.lock:
            for user_id, sess in list(sessions.items()):
                player = rt.state.get_player(user_id)
                if player is None or not player.alive:
                    continue
                if sess.dx == 0.0 and sess.dy == 0.0:
                    sess.last_tick = now
                    continue
                # Clamp dt so a stalled loop can never teleport the player
                # through the world (swept collision assumes <= 1 tile steps).
                dt = min(0.2, max(0.0, now - sess.last_tick))
                sess.last_tick = now
                speed = WEB_RUN_SPEED if sess.running else WEB_WALK_SPEED
                step_x = sess.dx * speed * dt
                step_y = sess.dy * speed * dt
                nx_f, ny_f = rt.collision.can_move_float(
                    player.x_f, player.y_f, step_x, step_y
                )
                if (nx_f, ny_f) != (player.x_f, player.y_f):
                    player.x_f, player.y_f = nx_f, ny_f
                    player.sync_int_from_float()
                    player.float_moved = True
                    player.direction = _web_direction(sess.dx, sess.dy)
                    moved_any = True
                    self._schedule_save(rt, player)
        if moved_any:
            self._touch_web_activity(rt)

    def _touch_web_activity(self, rt: ScenarioRuntime) -> None:
        """Hook for the web layer (set by bot.py): notify snapshot consumers
        that tiles changed this tick. Never touches Discord rendering."""
        hook = getattr(self, "on_web_movement", None)
        if hook is not None:
            try:
                hook(rt)
            except Exception as e:  # noqa: BLE001 — must never break the tick
                log.warning("[WEB] movement hook failed: %s", e)

    def create_runtime(self, channel_id: int, map_id: str, message_id: Optional[int] = None,
                        hub_message_id: Optional[int] = None) -> ScenarioRuntime:
        map_data = load_map(map_id, self.assets_dir)
        state = GameState(scenario_id=channel_id, map_id=map_id)
        resources = ResourceGrid.from_map(map_data)
        terrain = TerrainGrid.from_map(map_data)
        rt = ScenarioRuntime(
            channel_id=channel_id,
            message_id=message_id,
            hub_message_id=hub_message_id,
            state=state,
            map_data=map_data,
            collision=Collision(map_data, state.blocks, resources=resources),
            camera=Camera.auto(map_data),
            npc_map=load_npcs(map_id, self.assets_dir),
            resources=resources,
            terrain=terrain,
        )
        self.runtimes[channel_id] = rt
        # New scenario should open on LIVE weather, not the sun_clouds
        # placeholder: seed from the freshest known snapshot. Keeps the web
        # overlay + hub icon truthful from the first snapshot.
        latest = getattr(self, "_latest_weather", None)
        if latest is not None:
            rt.weather_state = latest
            rt.weather_key = latest.weather_key
        return rt

    async def load_blocks(self, rt: ScenarioRuntime) -> None:
        """Populate the placed-block overlay from the DB (async context only)."""
        if self.db is None:
            return
        from persistence.repositories import load_blocks

        for x, y, bid in await load_blocks(self.db, rt.channel_id):
            rt.state.blocks.place(x, y, bid)

    async def load_hotbars(self, rt: ScenarioRuntime) -> None:
        """MIGRATION ONLY: reposition old saved hotbar bindings in the bag.

        The hotbar used to be a separate binding table (slot -> item_id); it
        is now the first HOTBAR_SLOTS stacks of the ordered bag. For each
        player with legacy bindings, pull the bound items into bag positions
        0..N-1 (same semantics as a manual assign), then drop the rows.
        """
        if self.db is None:
            return
        from persistence.repositories import load_hotbars, save_inventory_order

        legacy = await load_hotbars(self.db, rt.channel_id)
        if not legacy:
            return
        for uid, bindings in legacy.items():
            inv = self.get_inventory(rt.channel_id, uid)
            for slot in sorted(bindings):
                inv.move_to(bindings[slot], slot)
            await save_inventory_order(
                self.db, rt.channel_id, uid, list(inv.items)
            )
        await self.db.execute(
            "DELETE FROM hotbar WHERE channel_id=?", (rt.channel_id,)
        )

    def get_hotbar(self, channel_id: int, user_id: int) -> Dict[int, Optional[str]]:
        """The player's hotbar: slots 0..HOTBAR_SLOTS-1 projected from the
        ordered bag — slot N holds the Nth stack (None when the bag is
        shorter). A projection: writing goes through set_hotbar_slot."""
        rt = self.get_runtime_for(channel_id, user_id)
        inv = rt.inventories.get(user_id)
        if inv is None:
            inv = self.get_inventory(channel_id, user_id)
        return inv.hotbar()

    async def _clear_depleted_hotbars(self, channel_id: int, user_id: int) -> None:
        # A depleted stack leaves the ordered bag (Inventory.remove), so the
        # projection closes the gap automatically — nothing to unbind.
        return

    def _notify_inventory_change(self, channel_id: int, user_id: int) -> None:
        """Refresh every projection of one player's inventory asynchronously."""
        rt = self.runtimes.get(channel_id)
        if rt is None:
            return
        coalescer = getattr(self, "coalescer", None)
        screen = rt.screens.get(user_id)
        if coalescer is not None and screen is not None and screen.message_id is not None:
            coalescer.schedule((channel_id, user_id), {"user_id": user_id})
        hub = getattr(self, "hub_coalescer", None)
        if hub is not None:
            hub.schedule((channel_id, user_id), {"focused_user_id": user_id})
        if screen is not None and screen.inventory_message_id is not None:
            bot = getattr(self, "bot_ref", None)
            channel = bot.get_channel(channel_id) if bot is not None else None
            if channel is not None:
                from discord_ui.inventory_view import refresh_if_open
                asyncio.create_task(refresh_if_open(self, channel, user_id))

    async def set_hotbar_slot(self, channel_id: int, user_id: int, slot: int,
                              item_id: Optional[str]) -> None:
        """Assign ``item_id`` to hotbar ``slot`` = move it to the slot's bag
        position (the hotbar mirrors the first HOTBAR_SLOTS bag stacks).

        ``item_id=None`` is a no-op (an empty slot is simply a short bag).
        """
        if not 0 <= slot < HOTBAR_SLOTS:
            raise ValueError(f"hotbar slot out of range: {slot}")
        if item_id is None:
            return
        rt = self.get_runtime_for(channel_id, user_id)
        inv = self.get_inventory(channel_id, user_id)
        inv.move_to(item_id, slot)
        if self.db is not None:
            await save_inventory_order(self.db, channel_id, user_id, list(inv.items))

    async def load_inventories(self, rt: ScenarioRuntime) -> None:
        """Populate rt.inventories from the DB (call from async context)."""
        if self.db is None:
            return
        inv = await load_all_inventory(self.db, rt.channel_id)
        for uid, items in inv.items():
            iv = Inventory()
            for iid, qty in items.items():
                iv.add(iid, qty)
            rt.inventories[uid] = iv

    def get_inventory(self, channel_id: int, user_id: int) -> Inventory:
        rt = self.get_runtime_for(channel_id, user_id)
        inv = rt.inventories.get(user_id)
        if inv is None:
            inv = Inventory()
            rt.inventories[user_id] = inv
        return inv

    async def add_item(self, channel_id: int, user_id: int, item_id: str, qty: int = 1) -> None:
        inv = self.get_inventory(channel_id, user_id)
        inv.add(item_id, qty)
        await self._persist_inventory(channel_id, user_id, item_id, inv.count(item_id))
        self._notify_inventory_change(channel_id, user_id)

    async def use_item(self, channel_id: int, user_id: int, item_id: str):
        rt = self.get_runtime_for(channel_id, user_id)
        p = rt.state.get_player(user_id)
        if p is None:
            return False, "no_player"
        inv = self.get_inventory(channel_id, user_id)
        ok, reason = inv.use(item_id, p)
        if ok:
            remaining = inv.count(item_id)
            await self._persist_inventory(channel_id, user_id, item_id, remaining)
            if remaining <= 0:
                # A depleted stack must disappear from every hotbar projection,
                # not merely become a disabled-looking button.
                await self._clear_depleted_hotbars(channel_id, user_id)
            if self.db is not None:
                from persistence.repositories import save_player

                await save_player(self.db, channel_id, p)
            self._notify_inventory_change(channel_id, user_id)
        return ok, reason

    async def craft_item(self, channel_id: int, user_id: int, recipe_id: str):
        """Attempt one craft (UI adapter -> pure game/crafting.py logic).

        Station proximity is re-checked HERE (server-side, rule 9): the panel
        may have been opened near the table, but the check runs at craft time
        on the live BlockGrid. Returns (ok, reason, out_item_id, out_qty).
        """
        from game import crafting

        rt = self.get_runtime_for(channel_id, user_id)
        player = rt.state.get_player(user_id)
        if player is None:
            return False, "no_player", None, 0
        recipe = crafting.get_recipe(recipe_id)
        if recipe is None:
            return False, "unknown_recipe", None, 0
        near_table = crafting.nearest_station(rt.state.blocks, player)
        inv = self.get_inventory(channel_id, user_id)
        ok, reason = crafting.can_craft(recipe, inv, near_table)
        if not ok:
            return False, reason, None, 0
        out_id, out_qty = crafting.do_craft(recipe, inv)
        # Persist every changed stack (inputs consumed + output added).
        for item_id, qty in recipe.inputs:
            await self._persist_inventory(channel_id, user_id, item_id, inv.count(item_id))
        await self._persist_inventory(channel_id, user_id, out_id, inv.count(out_id))
        await self._clear_depleted_hotbars(channel_id, user_id)
        self._notify_inventory_change(channel_id, user_id)
        return True, "ok", out_id, out_qty

    async def _persist_inventory(self, channel_id: int, user_id: int, item_id: str, qty: int) -> None:
        if self.db is None:
            return
        from persistence.repositories import save_inventory_item

        await save_inventory_item(self.db, channel_id, user_id, item_id, qty)

    def get_runtime(self, channel_id: int) -> Optional[ScenarioRuntime]:
        return self.runtimes.get(channel_id)

    def get_runtime_for(
        self, channel_id: int, user_id: Optional[int]
    ) -> Optional[ScenarioRuntime]:
        """Runtime of the world the USER currently stands in (multi-world
        aware). Falls back to the channel's main runtime when the user is not
        found anywhere (legacy call sites, hub-wide broadcasts)."""
        if user_id is not None:
            rt = self.runtime_of(channel_id, user_id)
            if rt is not None:
                return rt
        return self.get_runtime(channel_id)

    # ----- multi-world: the /khutraodoi trade lobby + interiors -----

    def get_or_create_side_runtime(
        self, main_rt: ScenarioRuntime, map_id: str
    ) -> ScenarioRuntime:
        """Lazy side runtime for ``map_id`` (lobby/interior) of one channel.

        Created on first entry; per-runtime lock (rule 16); the bag is shared
        with the main world so items carry over. Weather/zombies stay OFF on
        side worlds (no outdoor survival there)."""
        key = (main_rt.channel_id, map_id)
        rt = self.side_runtimes.get(key)
        if rt is not None:
            return rt
        rt = self.create_side_runtime(main_rt, map_id)
        self.side_runtimes[key] = rt
        return rt

    def create_side_runtime(
        self, main_rt: ScenarioRuntime, map_id: str
    ) -> ScenarioRuntime:
        map_data = load_map(map_id, self.assets_dir)
        resources = ResourceGrid.from_map(map_data)
        terrain = TerrainGrid.from_map(map_data)
        rt = ScenarioRuntime(
            channel_id=main_rt.channel_id,
            message_id=None,
            state=GameState(scenario_id=main_rt.channel_id, map_id=map_id),
            map_data=map_data,
            collision=Collision(map_data, GameState(
                scenario_id=main_rt.channel_id, map_id=map_id
            ).blocks, resources=resources),
            camera=Camera.auto(map_data),
            npc_map=load_npcs(map_id, self.assets_dir),
            resources=resources,
            terrain=terrain,
        )
        # Side worlds share the main world's placed-block grid reference? No:
        # each runtime needs its OWN empty overlay (blocks are per-world), but
        # the constructor wired a throwaway one above. Rebuild cleanly.
        blocks = type(rt.state.blocks)()
        rt.state.blocks = blocks
        rt.collision = Collision(map_data, blocks, resources=resources)
        # Mirror inventories with the main world (same bag everywhere).
        rt.inventories = main_rt.inventories
        return rt

    def runtime_of(self, channel_id: int, user_id: int) -> Optional[ScenarioRuntime]:
        """The runtime whose world the player currently stands in (the world
        holding their PLAYER object; falls back to the screen holder)."""
        main = self.runtimes.get(channel_id)
        side = getattr(self, "side_runtimes", None) or {}
        if main is not None and user_id in main.state.players:
            return main
        for rt in side.values():
            if rt.channel_id == channel_id and user_id in rt.state.players:
                return rt
        if main is not None and user_id in main.screens:
            return main
        for rt in side.values():
            if rt.channel_id == channel_id and user_id in rt.screens:
                return rt
        return None

    def ensure_screen(self, rt: ScenarioRuntime, user_id: int) -> PlayerScreen:
        """Get or create the player's personal screen (own camera included)."""
        self.touch_session(rt.channel_id, user_id)
        screen = rt.screens.get(user_id)
        if screen is None:
            screen = PlayerScreen(user_id=user_id, camera=Camera.auto(rt.map_data))
            rt.screens[user_id] = screen
        return screen

    async def close_side_panels(self, channel_id: int, user_id: int) -> None:
        """Close every side panel (inventory / craft) of one player.

        Triggered by a MOVEMENT press on their screen: the game must fall back
        to just the screen+hub pair. The panel messages are deleted; their
        persistent views are stopped so stale button presses 404 harmlessly.
        """
        rt = self.runtimes.get(channel_id)
        if rt is None:
            return
        screen = rt.screens.get(user_id)
        if screen is None:
            return
        panel_ids = [
            mid for mid in (screen.inventory_message_id, screen.craft_message_id)
            if mid is not None
        ]
        views = [
            v for v in (screen.inventory_view, screen.craft_view)
            if v is not None
        ]
        screen.clear_panels()
        for v in views:
            try:
                v.stop()
            except Exception as e:  # noqa: BLE001 — view stop is best-effort
                log.warning("[PANEL] stop view failed: %s", e)
        if not panel_ids:
            return
        bot = getattr(self, "bot_ref", None)
        channel = bot.get_channel(channel_id) if bot is not None else None
        if channel is None:
            return
        for mid in panel_ids:
            try:
                msg = channel.get_partial_message(mid)
                await msg.delete()
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[PANEL] delete panel %s failed: %s", mid, e)

    def zombie_view_rects(self, rt: ScenarioRuntime) -> Dict[int, tuple]:
        """Return each visible player's current camera rectangle in map tiles."""
        width, height = rt.map_data.width, rt.map_data.height
        rects: Dict[int, tuple] = {}
        for player in rt.state.get_visible_players():
            screen = rt.screens.get(player.user_id)
            camera = getattr(screen, "camera", None) if screen is not None else None
            if camera is not None and camera.follow:
                camera.center_on(player.x, player.y, width, height)
                x0, y0 = camera.top_left(width, height)
                rects[player.user_id] = (
                    x0, y0,
                    min(width, x0 + camera.view_w),
                    min(height, y0 + camera.view_h),
                )
                continue
            if camera is not None:
                rects[player.user_id] = (0, 0, width, height)
                continue

            # A player can briefly exist before their screen is created. Give
            # them the same virtual viewport as the normal follow camera so a
            # nearby zombie never gets a free real-time attack in that gap.
            view_w = min(DEFAULT_VIEW_W, width)
            view_h = min(DEFAULT_VIEW_H, height)
            x0 = max(0, min(width - view_w, player.x - view_w // 2))
            y0 = max(0, min(height - view_h, player.y - view_h // 2))
            rects[player.user_id] = (x0, y0, x0 + view_w, y0 + view_h)
        return rects

    def _schedule_zombie_updates(
        self, rt: ScenarioRuntime, exclude_user_id: Optional[int] = None,
        zombie_result=None,
    ) -> None:
        """Refresh screens/hubs affected by a background or visible zombie turn."""
        coalescer = getattr(self, "coalescer", None)
        if coalescer is not None:
            for uid, screen in rt.screens.items():
                if uid == exclude_user_id or screen.message_id is None:
                    continue
                coalescer.schedule((rt.channel_id, uid), {"user_id": uid})

        if zombie_result is not None:
            hub = getattr(self, "hub_coalescer", None)
            if hub is not None:
                for uid in zombie_result.damaged_player_ids:
                    hub.schedule(
                        (rt.channel_id, uid),
                        {"focused_user_id": uid, "type": "zombie"},
                    )

    def start_zombies(self, enabled: bool = True) -> None:
        """Start the shared night-zombie loop once."""
        if not enabled:
            return
        if self.zombie_task is None or self.zombie_task.done():
            self.zombie_task = asyncio.create_task(self._zombie_loop())

    async def stop_zombies(self) -> None:
        """Cancel the shared night-zombie loop cleanly during bot shutdown."""
        task = self.zombie_task
        self.zombie_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _zombie_loop(self) -> None:
        """Spawn/despawn at night and move only zombies outside every viewport."""
        while True:
            # Use a monotonic deadline instead of sleeping a fixed duration so
            # render/upload work cannot progressively slow the simulation down.
            tick_started = asyncio.get_running_loop().time()
            now_sec = ingame_seconds()
            night = is_night(now_sec)
            # "Càng về tối càng đông": scale the per-player-area cap from a
            # sparse day pool up to ZOMBIE_AREA_MAX_COUNT at deep night.
            sec = now_sec % 86400
            if 18 * 3600 <= sec or sec < 6 * 3600:
                darkness = 1.0                                   # 18:00-06:00
            elif 12 * 3600 <= sec < 18 * 3600:
                darkness = (sec - 12 * 3600) / (6 * 3600)        # noon->dusk ramp
            else:
                darkness = 0.15                                  # 06:00-12:00 day floor
            area_cap = max(1, round(ZOMBIE_AREA_MAX_COUNT * darkness))
            for rt in list(self.runtimes.values()):
                async with rt.lock:
                    # Population upkeep ONLY (spawn/despawn/off-screen wander).
                    # Visible zombies are FROZEN here — they take one turn per
                    # player action in dispatch(), never from this loop.
                    result = tick_zombies(
                        rt.state,
                        rt.collision,
                        self.zombie_view_rects(rt),
                        night,
                        rng=self.zombie_rng,
                        max_count=area_cap,
                        spawn_chance=ZOMBIE_SPAWN_CHANCE,
                    )
                # Off-screen wandering does not need an upload. Refresh only
                # when a creature becomes visible or is removed from a view.
                if result.visible_changed:
                    self._schedule_zombie_updates(rt, zombie_result=result)
            elapsed = asyncio.get_running_loop().time() - tick_started
            await asyncio.sleep(max(0.05, WORLD_TICK_SECONDS - elapsed))

    def remove_runtime(self, channel_id: int) -> None:
        rt = self.runtimes.pop(channel_id, None)
        if rt is not None:
            if rt._save_task is not None and not rt._save_task.done():
                rt._save_task.cancel()
            if rt.auto_running and rt.auto_task is not None and not rt.auto_task.done():
                rt.auto_task.cancel()
            rt.auto_running = False
            rt.auto_armed = False
            for key, task in list(self.respawn_tasks.items()):
                if key[0] == channel_id:
                    if not task.done():
                        task.cancel()
                    self.respawn_tasks.pop(key, None)
            for screen in rt.screens.values():
                if screen.auto_task is not None and not screen.auto_task.done():
                    screen.auto_task.cancel()
                screen.auto_running = False
                screen.auto_armed = False

    async def dispatch(self, channel_id: int, action, rt=None):
        """Apply one action in a world. ``rt=None`` = the channel's MAIN map;
        UI adapters for a side world (lobby/interior) pass that runtime so
        movement applies to the world the player currently stands in."""
        if rt is None:
            # Multi-world: apply the action in the world the player currently
            # stands in (bigmap OR a side world like the trade lobby).
            rt = self.get_runtime_for(channel_id, action.user_id)
            if rt is None:
                rt = self.runtimes.get(channel_id)
            if rt is None:
                return None, None
        # Any dispatched action counts as session activity (watchdog reset).
        self.touch_session(channel_id, action.user_id)
        zombie_result = None
        async with rt.lock:
            # Expose the runtime-scoped inventory map to the pure rule layer
            # (apply_attack reads the held item for bare-hand vs weapon dmg).
            # The hotbar is a projection of each ordered bag — no separate map.
            rt.state.inventories = rt.inventories
            if isinstance(action, PlaceBlockAction):
                inv = self.get_inventory(channel_id, action.user_id)
                result = apply_place_block(
                    rt.state, action, rt.collision, rt.state.blocks, inv
                )
                rt.dirty = rt.dirty or result.state_changed
                if result.state_changed:
                    await self._clear_depleted_hotbars(channel_id, action.user_id)
            elif isinstance(action, BreakBlockAction):
                inv = self.get_inventory(channel_id, action.user_id)
                result = apply_break_block(rt.state, action, rt.state.blocks, inv)
                rt.dirty = rt.dirty or result.state_changed
            elif isinstance(action, ChopAction):
                inv = self.get_inventory(channel_id, action.user_id)
                result = apply_chop(rt.state, action, rt.resources, inv)
                rt.dirty = rt.dirty or result.state_changed
            elif isinstance(action, ShovelAction):
                inv = self.get_inventory(channel_id, action.user_id)
                result = apply_scoop(
                    rt.state, action, rt.terrain, rt.state.blocks, inv
                )
                rt.dirty = rt.dirty or result.state_changed
            elif isinstance(action, TurnAction):
                result = apply_turn(rt.state, action)
                rt.dirty = rt.dirty or result.state_changed
            elif isinstance(action, AttackAction):
                # With no hostile nearby the attack rule falls back to
                # breaking the block on the target (highlighted) square.
                inv = self.get_inventory(channel_id, action.user_id)
                result = apply_attack(rt.state, action, rt.state.blocks, inv)
                rt.dirty = rt.dirty or result.state_changed
            elif isinstance(action, AimAction):
                # Aim cursor is view state: never marks the scenario dirty.
                result = apply_aim(rt.state, action)
            elif isinstance(action, AimResetAction):
                result = apply_aim_reset(rt.state, action)
            else:
                moving_player = rt.state.get_player(action.user_id)
                if moving_player is not None:
                    rt.state.previous_positions[action.user_id] = (
                        moving_player.x, moving_player.y
                    )
                result = apply_move(rt.state, action, rt.collision)
                rt.dirty = rt.dirty or result.state_changed
                if (result.state_changed and result.moved
                        and rt.weather_state is not None):
                    p = rt.state.get_player(action.user_id)
                    if p is not None:
                        apply_weather_regen(p, rt.weather_state)

            # Visible zombies take their turn on a CADENCE, not every press:
            # a counter per scenario increments on each zombie-eligible action
            # (moves count once even at step-size x3/x5 via intermediate=True;
            # aim-cursor actions never count). A turn is granted when the
            # counter reaches the divisor (2 -> zombie moves 1 tile per 2
            # player actions) OR on the fast-chase roll (25%), keeping them
            # threatening. Zombie movement itself stays 1 tile per turn.
            is_final_move = isinstance(action, MoveAction) and not action.intermediate
            zombie_eligible = (
                not isinstance(action, (AimAction, AimResetAction, MoveAction))
                or is_final_move
            )
            if zombie_eligible:
                rt.zombie_action_counter = getattr(rt, "zombie_action_counter", 0) + 1
                due = rt.zombie_action_counter >= ZOMBIE_ACTION_SPEED_DIVISOR
                fast = self.zombie_rng.random() < ZOMBIE_FAST_CHASE_CHANCE
                if due or fast:
                    if due:
                        rt.zombie_action_counter = 0
                    zombie_result = advance_visible_zombies(
                        rt.state, rt.collision, self.zombie_view_rects(rt),
                        rng=self.zombie_rng,
                    )
        if zombie_result is not None and zombie_result.changed:
            await self._finish_zombie_turn(
                rt, zombie_result,
                exclude_user_id=None if zombie_result.died_player_ids else action.user_id,
            )
        if result.state_changed and isinstance(action, PlaceBlockAction):
            self._notify_inventory_change(channel_id, action.user_id)
        if result.state_changed and self.db is not None:
            if isinstance(action, ChopAction):
                # Progress swings touch nothing durable; only the felling
                # swing persists the node + its drops.
                if result.drops is not None:
                    await self._persist_chop(channel_id, action, result)
            elif isinstance(action, (PlaceBlockAction, BreakBlockAction)):
                await self._persist_block(channel_id, action, result)
            elif isinstance(action, ShovelAction):
                # Only the FINAL scoop persists (the tile + its dirt drop);
                # progress swings touch nothing durable.
                if result.drops is not None:
                    await self._persist_scoop(channel_id, action, result)
            else:
                p = rt.state.get_player(action.user_id)
                if p is not None:
                    self._schedule_save(rt, p)
        if isinstance(action, AttackAction) and result.drops:
            await self._grant_zombie_drops(rt, action.user_id, result.drops)

        # Portal check (trade lobby/interior worlds only — the main world has
        # no portal config entry, so this is a dict-miss no-op there).
        if (
            isinstance(action, MoveAction)
            and result.state_changed
            and result.moved
            and self.side_runtimes.get((channel_id, rt.map_data.map_id)) is rt
        ):
            from game.travel import check_portal_after_move

            fired = check_portal_after_move(rt, self.portals, action.user_id)
            if fired is not None:
                link, player = fired
                await self._teleport_through_link(channel_id, rt, action.user_id, link)

        return rt, result

    async def _teleport_through_link(
        self, channel_id: int, src_rt: ScenarioRuntime, user_id: int, link
    ) -> None:
        """Move one player across worlds (portal step). Keeps their Discord
        screen/controls/hub messages; only the rendered map changes."""
        from game.travel import (
            free_arrival_tile,
            move_player_between_runtimes,
            resolve_link_target,
        )

        dst_rt = self.get_or_create_side_runtime(
            self.runtimes.get(channel_id, src_rt), link.map_id
        )
        # Choose the arrival tile now that the destination map is loaded.
        candidates = resolve_link_target(dst_rt, link, self.portals)
        occupied = {
            (p.x, p.y)
            for p in dst_rt.state.get_visible_players()
        }
        tile = free_arrival_tile(dst_rt, candidates, occupied)
        move_player_between_runtimes(src_rt, dst_rt, user_id, tile)
        # Both worlds changed: refresh the mover now and everyone else shortly.
        self._notify_travel_change(src_rt, user_id)
        self._notify_travel_change(dst_rt, user_id)

    def _notify_travel_change(self, rt: ScenarioRuntime, moved_uid: int) -> None:
        """Refresh the mover's screen+hub; list changes hit other hubs via the
        periodic signature beat."""
        coalescer = getattr(self, "coalescer", None)
        hub = getattr(self, "hub_coalescer", None)
        screen = rt.screens.get(moved_uid)
        if coalescer is not None and screen is not None and screen.message_id is not None:
            coalescer.schedule((rt.channel_id, moved_uid), {"user_id": moved_uid})
        if hub is not None:
            hub.schedule(
                (rt.channel_id, moved_uid), {"focused_user_id": moved_uid}
            )

    async def _finish_zombie_turn(
        self, rt: ScenarioRuntime, result, exclude_user_id: Optional[int] = None,
    ) -> None:
        """Shared post-turn work for player-triggered AND background turns:
        refresh affected screens/hubs, apply drops/deaths, persist damage."""
        self._schedule_zombie_updates(rt, exclude_user_id=exclude_user_id, zombie_result=result)
        await self._apply_zombie_effects(rt, result)
        if self.db is not None:
            for user_id in result.damaged_player_ids:
                damaged = rt.state.get_player(user_id)
                if damaged is not None:
                    self._schedule_save(rt, damaged)

    async def _grant_zombie_drops(self, rt, user_id: int, drops) -> None:
        inv = self.get_inventory(rt.channel_id, user_id)
        changed = False
        for item_id, qty in drops:
            inv.add(item_id, qty)
            changed = True
            if self.db is not None:
                from persistence.repositories import save_inventory_item
                await save_inventory_item(self.db, rt.channel_id, user_id, item_id, inv.count(item_id))
        if changed:
            # Loot landed in the bag: refresh the hub HUD + D-pad hotbar rail
            # immediately (same coalesced path as every other bag change).
            self._notify_inventory_change(rt.channel_id, user_id)

    async def _apply_zombie_effects(self, rt, result) -> None:
        """Apply loot and schedule one five-second respawn per dead player."""
        for user_id, item_id, qty in result.drops:
            await self._grant_zombie_drops(rt, user_id, [(item_id, qty)])
        for user_id in result.died_player_ids:
            self._schedule_respawn(rt, user_id)

    def _schedule_respawn(self, rt, user_id: int) -> None:
        key = (rt.channel_id, user_id)
        old = self.respawn_tasks.get(key)
        if old is not None and not old.done():
            return
        self.respawn_tasks[key] = asyncio.create_task(self._respawn_after(rt.channel_id, user_id))

    async def _respawn_after(self, channel_id: int, user_id: int) -> None:
        await asyncio.sleep(5.0)
        rt = self.runtimes.get(channel_id)
        if rt is None:
            return
        async with rt.lock:
            player = rt.state.get_player(user_id)
            if player is None or player.dead_until is None:
                return
            occupied = {(p.x, p.y) for p in rt.state.get_visible_players()}
            candidates = [
                (x, y) for y in range(rt.map_data.height)
                for x in range(rt.map_data.width)
                if rt.collision.is_walkable(x, y) and (x, y) not in occupied
            ]
            if not candidates:
                candidates = [rt.map_data.spawn]
            player.x, player.y = random.choice(candidates)
            player.hp = player.max_hp
            player.visible = True
            player.dead_until = None
            player.death_reason = None
            # Respawn is tile-based: re-centre the continuous position so a
            # connected web client sees the player at the new tile's centre.
            player.sync_float_from_int()
        if self.db is not None:
            self._schedule_save(rt, player)
        screen = rt.screens.get(user_id)
        if screen is not None:
            self._schedule_zombie_updates(rt)
            hub = getattr(self, "hub_coalescer", None)
            if hub is not None:
                hub.schedule((channel_id, user_id), {"focused_user_id": user_id})

    async def _persist_scoop(self, channel_id: int, action, result) -> None:
        """Persist one completed scoop: the tile's overlay removal + the dirt
        that entered the bag (mirrors _persist_chop)."""
        from persistence.repositories import (
            save_inventory_item,
            save_scooped_tile,
        )

        tx, ty = result.pos
        await save_scooped_tile(self.db, channel_id, tx, ty)
        inv = self.get_inventory(channel_id, action.user_id)
        for item_id, _qty in result.drops or []:
            await save_inventory_item(
                self.db, channel_id, action.user_id, item_id, inv.count(item_id)
            )

    async def load_terrain(self, rt: ScenarioRuntime) -> None:
        """Restore scooped tiles from the DB (call from async context)."""
        if self.db is None or rt.terrain is None:
            return
        from persistence.repositories import load_scooped_tiles

        for x, y in await load_scooped_tiles(self.db, rt.channel_id):
            rt.terrain.scoop(x, y)

    async def _persist_chop(self, channel_id: int, action, result) -> None:
        """Persist a felled node + every drop that entered the bag, then arm
        the regrow timer (in-memory only — the DB row carries the deadline)."""
        import time as _time

        from persistence.repositories import (
            save_chopped_resource,
            save_inventory_item,
        )

        rt = self.get_runtime_for(channel_id, action.user_id)
        node = rt.resources.node_at(*result.pos) if rt.resources else None
        if node is None:
            return
        ts = rt.resources.chopped_at.get(node.anchor)
        if ts is not None:
            await save_chopped_resource(self.db, channel_id, node.anchor[0], node.anchor[1], ts)
            self.schedule_resource_respawn(rt, node.anchor)
        inv = self.get_inventory(channel_id, action.user_id)
        for item_id, _qty in result.drops or []:
            await save_inventory_item(
                self.db, channel_id, action.user_id, item_id, inv.count(item_id)
            )

    # ----- resource regrow (trees/bushes) -----

    def schedule_resource_respawn(self, rt: ScenarioRuntime, anchor: tuple) -> None:
        """Arm one regrow for a felled node at its respawn deadline."""
        import time as _time

        node = rt.resources.node_at(*anchor) if rt.resources else None
        if node is None:
            return
        ts = rt.resources.chopped_at.get(node.anchor)
        if ts is None:
            return
        delay = max(0.0, ts + NODE_DEFS[node.kind].respawn_s - _time.time())
        asyncio.create_task(self._regrow_later(rt.channel_id, anchor, delay))

    async def _regrow_later(self, channel_id: int, anchor: tuple, delay: float) -> None:
        """Sleep to the deadline, then regrow the node + refresh its screens."""
        import time as _time

        await asyncio.sleep(delay)
        rt = self.runtimes.get(channel_id)
        if rt is None or rt.resources is None:
            return
        async with rt.lock:
            if not rt.resources.is_chopped(anchor):
                return  # already regrown (e.g. /mapreset) — nothing to do
            rt.resources.regrow(anchor)
            if self.db is not None:
                from persistence.repositories import delete_chopped_resource

                await delete_chopped_resource(self.db, channel_id, anchor[0], anchor[1])
        # The tree is visible again: refresh every personal screen + the hub.
        co = getattr(self, "coalescer", None)
        if co is not None:
            for uid, screen in rt.screens.items():
                if screen.message_id is not None:
                    co.schedule((channel_id, uid), {"user_id": uid})
        hub = getattr(self, "hub_coalescer", None)
        if hub is not None:
            hub.schedule(channel_id, {"type": "regrow"})

    async def load_resources(self, rt: ScenarioRuntime) -> None:
        """Restore felled nodes from the DB and re-arm their regrow timers.

        Rows whose deadline already elapsed while offline regrow immediately
        (their DB rows are dropped); unknown anchors (map changed) are purged.
        """
        import time as _time

        if self.db is None or rt.resources is None:
            return
        from persistence.repositories import (
            delete_chopped_resource,
            load_chopped_resources,
        )

        now = _time.time()
        for x, y, ts in await load_chopped_resources(self.db, rt.channel_id):
            node = rt.resources.node_at(x, y)
            if node is None or now >= ts + NODE_DEFS[node.kind].respawn_s:
                await delete_chopped_resource(self.db, rt.channel_id, x, y)
                continue
            rt.resources.mark_chopped((x, y), ts)
            self.schedule_resource_respawn(rt, (x, y))

    async def _persist_block(self, channel_id: int, action, result) -> None:
        """Persist one block change + the material that moved in/out of the bag."""
        from persistence.repositories import (
            delete_block,
            save_block,
            save_inventory_item,
        )

        tx, ty = result.pos
        if isinstance(action, PlaceBlockAction):
            await save_block(self.db, channel_id, tx, ty, action.block_id)
        else:
            await delete_block(self.db, channel_id, tx, ty)
        inv = self.get_inventory(channel_id, action.user_id)
        await save_inventory_item(
            self.db, channel_id, action.user_id, result.block_id,
            inv.count(result.block_id),
        )

    def start_weather(self, enabled: bool = True, refresh_interval: float = 900.0) -> None:
        """Spin up the shared national-weather fetch loop (one fetch / interval)."""
        from game.weather_service import WeatherService

        if not enabled:
            return
        if self.weather_task is not None and not self.weather_task.done():
            return
        self.weather_service = WeatherService(
            self.assets_dir, enabled=enabled, refresh_interval=refresh_interval
        )
        self.weather_task = asyncio.create_task(self._weather_loop())
        if self.lightning_task is None or self.lightning_task.done():
            self.lightning_task = asyncio.create_task(self._lightning_loop())

    async def stop_weather(self) -> None:
        if self.weather_task is not None and not self.weather_task.done():
            self.weather_task.cancel()
        self.weather_task = None
        if self.lightning_task is not None and not self.lightning_task.done():
            self.lightning_task.cancel()
        self.lightning_task = None
        if self.weather_service is not None:
            await self.weather_service.close()

    async def _weather_loop(self) -> None:
        """One fetch per interval; fan the snapshot out to every scenario.

        A single network call feeds all channels (rule #16: per-channel lock for
        the state write, but one shared fetch), so we never multiply quota use.

        Manual-override safety (web client "weather looks off" bug): an admin
        /setweather override pins rt.weather_key to the chosen key. A later
        auto-fetch must NOT clobber that pinned key — only refresh the
        underlying WeatherState snapshot (ratios/buffs). The pinned scenario
        resumes real weather only via /setweather "Tự Động" (WEATHER_AUTO on
        Discord) — see _force_weather_fetch. Same for side_runtimes.
        """
        import time

        while True:
            ws = await self.weather_service.fetch(time.time())
            # Cache the freshest snapshot: new scenarios seed from it so they
            # open on live weather instead of the sun_clouds placeholder.
            self._latest_weather = ws
            for rt in list(self.runtimes.values()) + list(self.side_runtimes.values()):
                async with rt.lock:
                    rt.weather_state = ws
                    # Only adopt the fresh auto key when the scenario is NOT
                    # manually pinned by an admin override.
                    if not getattr(rt, "weather_manual", False):
                        rt.weather_key = ws.weather_key
                hub = getattr(self, "hub_coalescer", None)
                if hub is not None:
                    hub.schedule(rt.channel_id, {"type": "weather"})
            await asyncio.sleep(self.weather_service.refresh_interval)

    async def _lightning_loop(self) -> None:
        """Storm lightning events: every 5-13 s, re-seed every storm runtime's
        strike (position / size / mirror / distant-flicker are all derived
        from the seed in rendering.weather_fx) and refresh its screens + hub.
        Between strikes the storm GIF loops as pure rain - no strobing."""
        import random

        while True:
            await asyncio.sleep(random.uniform(5.0, 13.0))
            # Skip entirely while the screen FX gate is OFF (default): the
            # bolt lives in the screen GIF, so there is nothing to re-render.
            storm_rts = [
                rt for rt in self.runtimes.values()
                if rt.weather_key == "storm" and getattr(rt, "weather_fx_enabled", False)
            ]
            if not storm_rts:
                continue
            hub = getattr(self, "hub_coalescer", None)
            co = getattr(self, "coalescer", None)
            for rt in storm_rts:
                async with rt.lock:
                    rt.lightning_seed = random.randrange(1, 10**6)
                if hub is not None:
                    hub.schedule(rt.channel_id, {"type": "weather"})
                if co is not None:
                    for uid, screen in rt.screens.items():
                        if screen.message_id is not None:
                            co.schedule((rt.channel_id, uid), {"user_id": uid})

    def _schedule_save(self, rt: ScenarioRuntime, player: Player) -> None:
        """Debounce DB writes: at most one save task per channel in flight.

        Keeps the per-move critical path free of DB I/O; the pending player is
        flushed on the next tick or explicitly via flush_runtime/flush_all.
        """
        rt.pending_player = player
        if rt._save_task is None or rt._save_task.done():
            rt._save_task = asyncio.create_task(self._flush_save(rt))

    async def _flush_save(self, rt: ScenarioRuntime) -> None:
        p = rt.pending_player
        rt.pending_player = None
        if p is not None and self.db is not None:
            from persistence.repositories import save_player

            await save_player(self.db, rt.channel_id, p)

    async def flush_runtime(self, rt: ScenarioRuntime) -> None:
        if rt._save_task is not None and not rt._save_task.done():
            await rt._save_task
        elif rt.pending_player is not None and self.db is not None:
            from persistence.repositories import save_player

            await save_player(self.db, rt.channel_id, rt.pending_player)
            rt.pending_player = None

    async def flush_all(self) -> None:
        for rt in self.runtimes.values():
            await self.flush_runtime(rt)
