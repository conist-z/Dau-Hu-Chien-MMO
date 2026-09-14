import logging
import math

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
    REGEN_DELAY_S,
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
    EAT_COOLDOWN_S,
    EAT_DURATION_S,
    EAT_SPEED_MULT,
    STAMINA_CHOP_DRAIN,
    STAMINA_REGEN,
    STAMINA_REGEN_DELAY_S,
    STAMINA_RUN_DRAIN,
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
    # Debt of integration time owed to the client after a stall. When the
    # event loop hiccups (heavy Discord PNG renders at night, GC), raw dt
    # exceeds the 0.2s sweep-collision clamp and the excess used to be thrown
    # away — the server integrated less time than the client, the prediction
    # walked ahead, and the 3-tile glide correction then dragged the player
    # BACKWARDS against their movement: the "invisible block shoving me" feel
    # at night with many zombies. Debt is repaid gradually (0.4x rate) so the
    # client never outruns the server by more than ~1 tile, without ever
    # applying a counter-force.
    time_debt: float = 0.0  # legacy, unused since the catch-up integration (kept for pickled sessions)
    # Client-authoritative position (web client is the truth source for its
    # own body — user's design decision): last reported predicted position
    # + monotonic stamp. _web_tick_runtime converges the body toward the
    # report (speed-capped to run speed + swept-collision-checked) instead
    # of integrating time independently. Stale reports (>1s) are ignored.
    report_x: float = 0.0
    report_y: float = 0.0
    report_at: float = 0.0
    # Stamp of the last consumed convergence: a report is applied once.
    last_converge: float = 0.0
    # Highest input seq seen (mirrored from web_api's WebSession — snapshots
    # read THIS object for last_seq; the two classes were disconnected so the
    # ack was permanently -1 and the client's seq-replay never armed).
    input_seq: int = -1


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
    # Per-player craft material grid (server-authoritative "what the player
    # took out of the bag onto the craft table") + parked craft result slot.
    mat_grids: Dict[int, Dict[str, int]] = field(default_factory=dict)
    craft_results: Dict[int, Dict[str, object]] = field(default_factory=dict)
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
        # Shared furnace smelting loop (game/smelting.py): ticks every placed
        # furnace ~1s. Started in bot.py setup_hook next to the zombie loop.
        self.smelting_task: Optional[asyncio.Task] = None
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
            try:
                due = list(self.sessions.due(now, timeout))
            except Exception:  # noqa: BLE001 — the watchdog must survive
                log.exception("[SESSION] due() scan failed (loop survives)")
                continue
            for channel_id, user_id in due:
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
        player.display_name = display_name or player.display_name
        # Permanent role color: mint once on first appearance, keep forever
        # ("1 màu dùng mãi mãi" — colors chat name + avatar label).
        if not player.name_color:
            from game.state import random_name_color
            player.name_color = random_name_color()
        player.is_web = True
        # "Web wins": while a web session is attached it controls the shared
        # body; the Discord side reads this flag to pause its own refresh +
        # input (no continuous screen re-renders chasing the web player).
        player.mode = "web"
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
            # Control returns to the Discord client automatically — its
            # refresh pacer un-pauses on the next beat (web_controlled gate).
            player.mode = "chat"
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

    @staticmethod
    def web_controlled(rt, user_id: int) -> bool:
        """True when the web session currently controls this player's body.

        The Discord adapter checks this to pause its own refresh beat and
        gate its D-pad input — the screen NEVER re-renders to chase the web
        player (CPU), it simply sits out until the web session detaches,
        after which the normal cadence revives it.
        """
        player = rt.state.get_player(user_id) if rt is not None else None
        return player is not None and player.mode == "web"

    def _web_rebind_session(self, rt, user_id: int):
        """Self-heal a web session left behind in another runtime.

        Portal travel moves the player between runtimes; if any path ever
        forgets to migrate ``web_sessions`` (bug: frozen server-side position
        while the client predicted forward, every click "Quá xa"), re-bind
        the stale session to the runtime actually holding the player."""
        if user_id in rt.web_sessions:
            return rt.web_sessions[user_id]
        for other in list(self.runtimes.values()) + list(self.side_runtimes.values()):
            sess = other.web_sessions.pop(user_id, None)
            if sess is not None:
                sess.last_tick = 0.0  # fresh dt anchor in the new world
                rt.web_sessions[user_id] = sess
                return sess
        return None

    def web_input(self, channel_id: int, user_id: int,
                  dx: float, dy: float, running: bool = False,
                  report_x: object = None, report_y: object = None,
                  input_seq: int = None) -> bool:
        """Store one input vector (called from the WS handler).

        Client-authoritative position: when the (web) client reports its
        predicted position, remember it — _web_tick_runtime converges the
        real body toward the reported position instead of integrating time
        independently. This structurally removes server-integration desync
        ("player here, hitbox bitten over there").
        """
        rt = self.runtime_of(channel_id, user_id) or self.runtimes.get(channel_id)
        if rt is None:
            return False
        sess = rt.web_sessions.get(user_id) or self._web_rebind_session(rt, user_id)
        if sess is None:
            return False
        sess.dx = max(-1.0, min(1.0, float(dx)))
        sess.dy = max(-1.0, min(1.0, float(dy)))
        sess.running = bool(running)
        # SEQ MIRROR: snapshots read last_seq from THIS session object, but
        # the seq arrived on web_api's session — without this mirror the ack
        # was permanently -1, the client's seq-replay never armed, and every
        # position correction degraded to the slow drift-glide.
        if input_seq is not None and input_seq > getattr(sess, "input_seq", -1):
            sess.input_seq = int(input_seq)
        try:
            rx = float(report_x) if report_x is not None else None
            ry = float(report_y) if report_y is not None else None
        except (TypeError, ValueError):
            rx = ry = None
        if rx is not None and ry is not None:
            sess.report_x, sess.report_y = rx, ry
            sess.report_at = _loop_time()
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
                if not sessions:
                    continue
                try:
                    await self._web_tick_runtime(rt, sessions, now)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # CRASH-PROOF LOOP (the "đơ phải reload" root cause): any
                    # exception used to escape the while-body and KILL the
                    # shared tick task forever (the earlier
                    # "Task exception was never retrieved ... _web_tick_loop"
                    # death). Every web client then froze server-side — no
                    # integration, no bites — until a manual reload restarted
                    # the loop via rejoin. Isolate per-runtime: one bad world
                    # logs loudly and the loop lives on.
                    log.exception(
                        "[WEB] tick failed for channel %s (loop survives)",
                        getattr(rt, "channel_id", "?"),
                    )
            if not any(
                getattr(r, "web_sessions", None)
                for r in list(self.runtimes.values()) + list(self.side_runtimes.values())
            ):
                return  # last web client left; stop paying for the loop
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.001, interval - elapsed))

    def _converge_to_report(self, rt: ScenarioRuntime, player, sess, now: float) -> bool:
        """Pull the server body toward the client's reported predicted position
        (speed-capped + swept-collision-checked). Shared by the MOVING path
        and the IDLE path — both must converge, or a standing player's server
        body silently drifts from the on-screen avatar (the "hitbox bên kia"
        ghost). Returns True when the body moved.

        One report = one application: report_at is consumed here. The time
        budget is the gap since the report stamp (capped at 0.5 s, floored at
        one tick) so per-report movement can never exceed the legal rate.
        """
        step_budget = min(
            0.5, max(0.0, now - max(sess.report_at, sess.last_converge))
        )
        sess.last_converge = now
        sess.report_at = 0.0  # consume: a report is applied once
        tgt_x = max(0.0, float(sess.report_x))
        tgt_y = max(0.0, float(sess.report_y))
        step_total = math.hypot(tgt_x - player.x_f, tgt_y - player.y_f)
        # CONVERGE faster than run speed (1.6x, user 15/09 "siết chặt, update
        # nhanh hơn"): residual desync decays in ~1-2 ticks instead of
        # trailing behind at walk pace. A hacked client still can't teleport
        # — the cap is finite and swept collision holds.
        max_speed = WEB_RUN_SPEED * 1.6
        if player.eating_until > now:
            max_speed *= EAT_SPEED_MULT
        max_step = max_speed * max(step_budget, 2.0 / WEB_TICK_HZ)
        moved_any = False
        if step_total > 1e-6:
            moved = min(step_total, max_step)
            ux = (tgt_x - player.x_f) / step_total
            uy = (tgt_y - player.y_f) / step_total
            nx_f, ny_f = rt.collision.can_move_float(
                player.x_f, player.y_f, ux * moved, uy * moved,
            )
            if (nx_f, ny_f) != (player.x_f, player.y_f):
                player.x_f, player.y_f = nx_f, ny_f
                moved_any = True
        if (sess.dx or sess.dy):
            player.direction = _web_direction(sess.dx, sess.dy)
        return moved_any

    async def _web_tick_runtime(self, rt: ScenarioRuntime,
                                sessions: Dict[int, WebSession], now: float) -> None:
        moved_any = False
        zombie_touched = False
        import time as _time

        async with rt.lock:
            # --- block self-repair + node durability regen beats ----------
            # One call per tick drives BOTH grids' gradual healing:
            #   blocks: 3.5 s idle -> crack damage drains back to 0 at 1/s
            #   nodes : 5 s idle   -> chop progress rewinds 1 hit/s
            # Server-driven — the client only mirrors the numbers it receives.
            try:
                rt.state.blocks.decay_damage(_time.time())
                if rt.resources is not None:
                    rt.resources.decay_progress(_time.time())
            except Exception as e:  # noqa: BLE001 — must never kill the tick
                log.warning("[WEB] repair/regen beat failed: %s", e)
            for user_id, sess in list(sessions.items()):
                player = rt.state.get_player(user_id)
                if player is None:
                    # CRITICAL dt hygiene: stamp the clock even for a session
                    # whose player vanished. Leaving last_tick frozen meant
                    # the NEXT movement tick computed raw_dt over the whole
                    # vanished gap and teleported the server body along the
                    # stale input vector — the accumulated-desync bug that
                    # survived every previous fix (ghost hitbox tiles away).
                    sess.last_tick = now
                    continue
                if not player.alive:
                    # Same stamp here: dead time is NOT movement time. The
                    # old `continue` without a stamp let raw_dt swallow the
                    # entire death window (5s+ respawn, or minutes when the
                    # socket dropped silently) into one integration burst.
                    sess.last_tick = now
                    # Self-heal a LOST respawn (bot restart, side world, or a
                    # dropped task): ``dead_until`` is ephemeral, so without
                    # this the player stayed "dead" forever — frozen in place
                    # and every action refused (see Player.revive_if_expired).
                    # Revive teleports to a random walkable tile (same rule as
                    # the task path) so they never wake inside the zombie pack
                    # and die again on the next tick ("treo màn hồi sinh").
                    if player.revive_if_expired(_time.time()):
                        occupied = {(p.x, p.y) for p in rt.state.get_visible_players()}
                        spots = [
                            (x, y) for y in range(rt.map_data.height)
                            for x in range(rt.map_data.width)
                            if rt.collision.is_walkable(x, y) and (x, y) not in occupied
                        ]
                        if spots:
                            player.revive_teleport(*random.choice(spots))
                        moved_any = True
                        self._schedule_save(rt, player)
                    continue
                # CAP the integration window: real time never freezes, but a
                # session whose input socket silently died (tab closed without
                # onclose, OS network switch) kept the OLD held vector in
                # sess.dx/dy while `now` marched on. The old unbounded catch-up
                # then integrated EVERY second since the socket died in one
                # burst — the server body strolled up to speed*dt seconds away
                # from the (freshly rejoined) client. 0.5s = 10 ticks: generous
                # headroom for genuine event-loop hiccups, but a hard ceiling
                # on how far any single desync can run. Rejoin always stamps a
                # fresh last_tick (register_web_session), so a reconnect NEVER
                # inherits stale time.
                raw_dt = min(0.5, max(0.0, now - sess.last_tick))
                sess.last_tick = now
                remaining = raw_dt
                if sess.dx == 0.0 and sess.dy == 0.0:
                    sess.last_tick = now
                    self._regen_player_beat(rt, player, now)
                    self._stamina_regen_beat(rt, player, now)
                    # IDLE CONVERGE (the heartbeat's other half): the client's
                    # idle heartbeat still carries its predicted position —
                    # apply it HERE instead of `continue`, or the server body
                    # silently drifts while the player stands still (the
                    # "đứng yên mà hitbox ở chỗ khác" ghost, worse than ever
                    # once the heartbeat made report_at perpetually fresh).
                    if (sess.report_at > 0.0
                            and 0.0 < (now - sess.report_at) < 1.0):
                        if self._converge_to_report(rt, player, sess, now):
                            # The idle body moved: sync the int tile + save,
                            # exactly like the moving path does — actions
                            # (place/chop/attack offsets) derive from player.x
                            # and would act on a STALE tile otherwise (the
                            # "block nhảy vào trong" bug at range).
                            player.sync_int_from_float()
                            player.float_moved = True
                            self._schedule_save(rt, player)
                    continue
                self._regen_player_beat(rt, player, now)
                self._stamina_regen_beat(rt, player, now)
                self._eat_complete_beat(rt, player, now)
                # CLIENT-AUTHORITATIVE MOVEMENT (replaces time integration).
                # The web client reports its predicted position with every
                # input flush; the server pulls the real body toward that
                # position, speed-capped to the legal move rate and swept-
                # collision-checked. Whatever the client shows is what the
                # server converges to — server-side time integration can no
                # longer diverge ("player here, hitbox bitten over there").
                # Fairness: max convergence rate = run speed (2x walk), so a
                # hacked client teleports at most 2x too fast, never skips
                # walls (swept collision), and a silent socket's stale report
                # expires (see below) — no rubber-banding of honest players.
                # SPRINT stamina gate still applies in the legacy fallback
                # below (clients that never report a position).
                have_report = (
                    sess.report_at > 0.0
                    and 0.0 < (now - sess.report_at) < 1.0  # fresh report
                )
                if have_report:
                    if self._converge_to_report(rt, player, sess, now):
                        moved_any = True
                else:
                    # LEGACY fallback (old client / expired report): the
                    # original time integration, still dt-capped.
                    eff_running = sess.running
                    if eff_running and (sess.dx or sess.dy):
                        if player.stamina <= 0.0:
                            eff_running = False
                    remaining = raw_dt
                    while remaining > 1e-6:
                        dt = min(0.2, remaining)
                        remaining -= dt
                        if eff_running:
                            self._drain_stamina(player, STAMINA_RUN_DRAIN * dt)
                        speed = WEB_RUN_SPEED if eff_running else WEB_WALK_SPEED
                        # EATING: chew while walking = half speed (user feature).
                        if player.eating_until > now:
                            speed *= EAT_SPEED_MULT
                        step_x = sess.dx * speed * dt
                        step_y = sess.dy * speed * dt
                        nx_f, ny_f = rt.collision.can_move_float(
                            player.x_f, player.y_f, step_x, step_y
                        )
                        if (nx_f, ny_f) != (player.x_f, player.y_f):
                            player.x_f, player.y_f = nx_f, ny_f
                            moved_any = True
                    if moved_any:
                        player.direction = _web_direction(sess.dx, sess.dy)
                # One int sync + save per tick (not per chunk).
                if moved_any:
                    player.sync_int_from_float()
                    player.float_moved = True
                    self._schedule_save(rt, player)
                # PORTAL CHECK for WEB movement (user 15/09: web clients could
                # never teleport through the lobby doors — the old check ran
                # only in the Discord dispatch path). The web tick moves the
                # body via converge/integration, so this is the correct hook:
                # side worlds only. moved_off_portal lets a player STANDING on
                # the door tile get the teleport (they may have walked on
                # between two flushes); the latch still prevents bounce-back
                # within one continuous portal contact.
                if moved_any and self.side_runtimes.get(
                    (rt.channel_id, rt.map_data.map_id)
                ) is rt:
                    from game.travel import check_portal_after_move

                    prev_off = (
                        getattr(rt, "on_portal_tile", None) is None
                        or user_id not in rt.on_portal_tile
                    )
                    fired = check_portal_after_move(
                        rt, self.portals, user_id, moved_off_portal=prev_off
                    )
                    if fired is not None:
                        link, portal_player = fired
                        await self._teleport_through_link(
                            rt.channel_id, rt, user_id, link
                        )
                        # The player object moved runtime — skip further
                        # per-tick work against the old rt this iteration.
                        continue
            # SEPARATE realtime web pack (state.web_zombies, float positions):
            # driven by this same 20 Hz tick with the tick dt — movement
            # integrates smoothly every frame like a player, bites are gated
            # by a per-zombie cooldown so HP can never melt ("giật" fix).
            # NEVER touches the Discord turn pack (state.zombies).
            if sessions:
                from game.zombies import is_night as _is_night
                from game.zombies import web_tick as _z_web_tick

                from rendering.daynight import ingame_seconds as _ingame_s

                # TRADE ZONES ARE MOB-FREE (user 15/09: "tắt quái khi ở trong
                # chợ"): skip the web pack tick entirely there — web_tick
                # despawns the existing pack on its next pass when night goes
                # false, so force that state instead of running the spawn.
                from game.travel import is_trade_zone

                if is_trade_zone(rt):
                    zres = _z_web_tick(
                        rt.state, rt.collision, False, 0.0,
                        rng=self.zombie_rng,
                    )
                else:
                    tick_dt = 1.0 / max(1.0, WEB_TICK_HZ)
                    zres = _z_web_tick(
                        rt.state, rt.collision, _is_night(_ingame_s()),
                        tick_dt, rng=self.zombie_rng,
                    )
                if zres.changed:
                    zombie_touched = True
                # Bite damage persists like any other HP change.
                if zres.damaged_player_ids and self.db is not None:
                    for uid in zres.damaged_player_ids:
                        hurt = rt.state.get_player(uid)
                        if hurt is not None:
                            self._schedule_save(rt, hurt)

                # Drop entities ("linh khí"): one physics + vortex beat per
                # tick. Collect grants are persisted like any bag change.
                from game.drops import tick_drops as _tick_drops

                web_players = [
                    p for p in rt.state.get_visible_players()
                    if getattr(p, "is_web", False)
                ]
                collections, _pruned = _tick_drops(
                    rt.state, web_players, 1.0 / max(1.0, WEB_TICK_HZ),
                    collision=rt.collision, rng=self.zombie_rng,
                )
                if collections:
                    await self._grant_drop_collections(rt, collections)
        if moved_any:
            self._touch_web_activity(rt)
        if zombie_touched:
            # Web-only refresh: snapshots carry the new pack at 20 Hz, so no
            # Discord coalescer work is scheduled here (that was the "cắn là
            # giật" cause — every web bite re-rendered chat screens).
            pass

    # ---- stamina -------------------------------------------------------

    @staticmethod
    def _drain_stamina(player, amount: float) -> None:
        """Spend stamina (fractional, never below 0); stamps the exertion
        time so regen waits STAMINA_REGEN_DELAY_S after the last effort."""
        import time as _time
        if player is None:
            return
        player.stamina = max(0.0, player.stamina - amount)
        player.last_exert_at = _time.monotonic()

    def _drain_stamina_harvest(self, player) -> None:
        """One harvest swing costs a flat stamina chunk (per-swing rather
        than per-second — swings are discrete)."""
        if player is not None:
            self._drain_stamina(player, STAMINA_CHOP_DRAIN * 0.25)

    def _stamina_regen_beat(self, rt: ScenarioRuntime, player, now: float) -> None:
        """Refill stamina after a short grace period since the last exertion
        (sprint tick / harvest hit). Fast refill (~20 s to full)."""
        last = getattr(player, "last_exert_at", None)
        if last is not None and now - last < STAMINA_REGEN_DELAY_S:
            return
        if player.stamina >= player.max_stamina:
            return
        player.stamina = min(
            player.max_stamina, player.stamina + STAMINA_REGEN / max(1.0, WEB_TICK_HZ)
        )

    def _regen_player_beat(self, rt: ScenarioRuntime, player, now: float) -> None:
        """One 20 Hz out-of-combat HP-regen beat for one web player.

        Healing starts only after REGEN_DELAY_S of NO hp loss (user rule
        13/09) and runs at REGEN_HP_PER_SEC HP/s; the fractional bank keeps
        the pace smooth at 20 Hz. Any future damage source stamps
        ``last_damaged_at`` (zombie bites do it in game.zombies) and healing
        stops the same tick.
        """
        if player.hp <= 0 or player.hp >= player.max_hp:
            player.regen_bank = 0.0
            return
        if player.last_damaged_at is not None and (
            now - player.last_damaged_at < REGEN_DELAY_S
        ):
            return  # recently hurt: no healing yet
        from game.rules import apply_regen

        if apply_regen(player, 1.0 / max(1.0, WEB_TICK_HZ)):
            self._schedule_save(rt, player)

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
                inv.swap_to_slot(bindings[slot], slot)
            await save_inventory_order(
                self.db, rt.channel_id, uid, list(inv.items)
            )
        await self.db.execute(
            "DELETE FROM hotbar WHERE channel_id=?", (rt.channel_id,)
        )

    def get_hotbar(self, channel_id: int, user_id: int) -> Dict[int, Optional[str]]:
        """The player's hotbar: slot N mirrors BAG SLOT N (positional — see
        Inventory.hotbar). Empty bag slot = empty hotbar slot. Writing goes
        through set_hotbar_slot."""
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
        """Refresh every projection of one player's inventory asynchronously.

        PERF: a WEB-ONLY player (no Discord screen, mode == "web") has no
        Discord-side projection of their bag — scheduling the hub coalescer
        for them triggered a full HUD PIL render + image upload on EVERY
        drag/craft (the "spam di đồ → ms tăng vọt" bug). Their bag lives on
        the web client, which syncs via inv_delta; skip Discord entirely.
        """
        rt = self.runtimes.get(channel_id)
        if rt is None:
            return
        screen = rt.screens.get(user_id)
        player = rt.state.get_player(user_id)
        if screen is None and player is not None and player.mode == "web":
            return
        coalescer = getattr(self, "coalescer", None)
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
        """Assign ``item_id`` to hotbar ``slot`` = move its first stack into
        BAG SLOT ``slot`` (positional hotbar mapping), swapping with whatever
        sits there.

        ``item_id=None`` is a no-op (an empty slot is simply an empty bag
        slot).
        """
        if not 0 <= slot < HOTBAR_SLOTS:
            raise ValueError(f"hotbar slot out of range: {slot}")
        if item_id is None:
            return
        rt = self.get_runtime_for(channel_id, user_id)
        inv = self.get_inventory(channel_id, user_id)
        inv.swap_to_slot(item_id, slot)
        if self.db is not None:
            await save_inventory_order(self.db, channel_id, user_id, list(inv.items))

    async def reorder_bag(self, channel_id: int, user_id: int,
                          order: list) -> None:
        """Replace the bag GRID wholesale (web drag & drop): ``order`` is
        [(item_id, qty), ...] in slot order, one entry per pixel-grid slot
        (index = slot, ``qty <= 0`` / None entries are empty slots).

        Pure reorder: the multiset of (item_id, qty) is validated to equal
        the current bag's before applying, so a dropped/stale client frame
        can never create or destroy items. Empty slots are REAL — the client
        decides where everything sits, no auto-compaction.
        """
        rt = self.get_runtime_for(channel_id, user_id)
        inv = self.get_inventory(channel_id, user_id)
        # Normalise the client order: one entry per slot, None/empty = free.
        cells: List[Optional[Tuple[str, int]]] = []
        for entry in order:
            iid = qty = None
            if isinstance(entry, dict):
                iid = entry.get("id") or None
                qty = entry.get("qty", 0)
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                iid, qty = entry[0], entry[1]
            if iid and qty and int(qty) > 0:
                cells.append((str(iid), int(qty)))
            else:
                cells.append(None)
        if len(cells) > len(inv.slots):
            cells = cells[: len(inv.slots)]
        cells += [None] * (len(inv.slots) - len(cells))
        # TOLERANT reorder: the multiset sent by the client may be stale
        # (sent before a loot pickup / craft consumed something). NEVER raise
        # — that surfaced as "bad_order" toasts mid-drag. Instead: apply the
        # client's ORDER for the items it got right and keep the server's
        # authoritative quantities for everything else (resync-on-mismatch).
        # NOTE: currency in the grid is a FREE item — the reorder applies it
        # like anything else. Deposit into the purse is the explicit
        # purse_deposit op (drag onto a matching purse cell).
        want: Dict[str, int] = {}
        for c in cells:
            if c:
                want[c[0]] = want.get(c[0], 0) + c[1]
        current = inv.items
        if want == current:
            inv.slots = cells
            inv.version += 1
        else:
            # Mismatch: rebuild a grid with the SERVER's totals placed in the
            # client's slot positions (same item id -> client slot, extra
            # server-only stacks -> first free slots).
            rebuilt: List[Optional[Tuple[str, int]]] = [None] * len(inv.slots)
            remaining = dict(current)
            for c in cells:
                if c and remaining.get(c[0], 0) > 0:
                    take = min(remaining[c[0]], c[1])
                    if take > 0:
                        # First fit into free rebuilt slots.
                        for i in range(len(rebuilt)):
                            if rebuilt[i] is None:
                                rebuilt[i] = (c[0], take)
                                remaining[c[0]] -= take
                                break
            for iid, qty in list(remaining.items()):
                if qty <= 0:
                    continue
                for i in range(len(rebuilt)):
                    if rebuilt[i] is None:
                        rebuilt[i] = (iid, qty)
                        remaining[iid] = 0
                        break
            inv.slots = rebuilt
            inv.version += 1
        if self.db is not None:
            await save_inventory_order(self.db, channel_id, user_id, list(inv.items))

    async def load_inventories(self, rt: ScenarioRuntime) -> None:
        """Populate rt.inventories from the DB (call from async context).
        Uses the positional loader so the player's drag layout (including
        empty slots) survives a restart — the dense-dict path auto-compacted
        and reset every layout on re-login."""
        if self.db is None:
            return
        from persistence.repositories import load_inventory_slots
        slots_by_user = await load_inventory_slots(self.db, rt.channel_id)
        if not slots_by_user:
            return
        from game.purse import is_currency, purse_add
        for uid, slots in slots_by_user.items():
            inv = Inventory()
            # Overlay the persisted layout (slot indices preserved).
            for i, cell in enumerate(slots[: inv.BAG_SLOTS]):
                if cell:
                    inv.slots[i] = cell
            rt.inventories[uid] = inv
            # Currency in a saved bag is a FREE item now (deposit = explicit
            # drag onto the purse icons) — no auto-migration.

    def get_inventory(self, channel_id: int, user_id: int) -> Inventory:
        rt = self.get_runtime_for(channel_id, user_id)
        inv = rt.inventories.get(user_id)
        if inv is None:
            inv = Inventory()
            rt.inventories[user_id] = inv
        # NOTE: deliberately NO purse sweep here — purse_add internally calls
        # get_inventory, so a sweep in here recurses infinitely (seen live as
        # a RecursionError storm on every action). Conversion happens at the
        # mutation entry points (add_item, _grant_*, load_inventories).
        return inv

    # ----- craft material grid (server-side buffer, one per player) --------

    @staticmethod
    def _clean_grid(grid: list) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for iid, qty in grid:
            if iid and int(qty) > 0:
                out[str(iid)] = out.get(str(iid), 0) + int(qty)
        return out

    def get_mat_grid(self, channel_id: int, user_id: int) -> Dict[str, int]:
        rt = self.get_runtime_for(channel_id, user_id)
        return rt.mat_grids.setdefault(user_id, {})

    async def mat_sync(self, channel_id: int, user_id: int, grid: list) -> dict:
        """Set the craft material grid = server truth for what the player
        "took out of the bag".

        The DELTA between the old and new grid is removed from / returned to
        the bag here (server-authoritative — the client's local buffer is
        just a preview). Returns the resulting (grid, bag) snapshot dict."""
        rt = self.get_runtime_for(channel_id, user_id)
        want = self._clean_grid(grid)
        current = self.get_mat_grid(channel_id, user_id)
        inv = self.get_inventory(channel_id, user_id)
        # Items the player removed from the grid go back to the bag.
        for iid, had in list(current.items()):
            back = had - want.get(iid, 0)
            if back > 0:
                inv.add(iid, back)
        # Items newly placed on the grid come out of the bag.
        for iid, wanted in want.items():
            extra = wanted - current.get(iid, 0)
            if extra > 0:
                if inv.count(iid) < extra:
                    # Not enough in the bag: revert THIS item's placement.
                    want[iid] = current.get(iid, 0)
                    continue
                inv.remove(iid, extra)
        rt.mat_grids[user_id] = want
        await self._persist_full_inventory(channel_id, user_id, inv)
        self._notify_inventory_change(channel_id, user_id)
        return {"mat_grid": want, "inventory": inv.items}

    async def mat_move_slot(self, channel_id: int, user_id: int,
                            src: int, dst: int) -> dict:
        """Reorder WITHIN the material grid (client sends cell indexes)."""
        rt = self.get_runtime_for(channel_id, user_id)
        grid = self.get_mat_grid(channel_id, user_id)
        entries = [(iid, q) for iid, q in grid.items() if q > 0]
        i = src if 0 <= src < len(entries) else -1
        j = dst if 0 <= dst < len(entries) else -1
        if i >= 0 and j >= 0 and i != j:
            entries[i], entries[j] = entries[j], entries[i]
            rt.mat_grids[user_id] = dict(entries)
        return {"mat_grid": self.get_mat_grid(channel_id, user_id)}

    async def craft_from_grid(self, channel_id: int, user_id: int) -> dict:
        """Craft from the SERVER-side material grid (the real recipe table).

        Consumes exactly what sits in the grid, matches the multiset to a
        recipe, and parks the output in ``rt.craft_results[user_id]`` — the
        player clicks the result slot to collect it into the bag. On failure
        nothing is consumed."""
        from game import crafting

        rt = self.get_runtime_for(channel_id, user_id)
        player = rt.state.get_player(user_id)
        if player is None:
            return {"ok": False, "reason": "no_player"}
        grid = self.get_mat_grid(channel_id, user_id)
        if not grid:
            return {"ok": False, "reason": "empty_grid"}
        recipe = crafting.find_recipe_by_inputs(list(grid.items()))
        if recipe is None:
            return {"ok": False, "reason": "no_matching_recipe"}
        near_table = crafting.nearest_station(rt.state.blocks, player)
        inv = self.get_inventory(channel_id, user_id)
        ok, reason = crafting.can_craft_table_free(recipe, grid, near_table)
        if not ok:
            return {"ok": False, "reason": reason}
        out_id, out_qty = recipe.output
        for iid, qty in recipe.inputs:
            grid[iid] = grid.get(iid, 0) - qty
            if grid[iid] <= 0:
                grid.pop(iid)
        rt.craft_results[user_id] = {"id": out_id, "qty": out_qty}
        self._notify_inventory_change(channel_id, user_id)
        return {"ok": True, "reason": "ok", "item_id": out_id, "qty": out_qty}

    async def craft_collect(self, channel_id: int, user_id: int,
                            slot: Optional[int] = None) -> dict:
        """Take the crafted output out of the result slot.

        ``slot`` (web drag-to-slot) places the stack into that exact bag
        cell — merging onto the same item kind, ``bad_slot`` otherwise.
        ``slot=None`` (plain click) keeps the classic first-free-slot path.
        """
        rt = self.get_runtime_for(channel_id, user_id)
        res = rt.craft_results.get(user_id)
        if not res:
            return {"ok": False, "reason": "empty_result"}
        inv = self.get_inventory(channel_id, user_id)
        if slot is not None:
            if not 0 <= slot < len(inv.slots):
                return {"ok": False, "reason": "bad_slot"}
            target = inv.slots[slot]
            if target and target[0] == res["id"]:
                # Same kind: merge onto the stack.
                inv.set_slot(slot, res["id"], target[1] + res["qty"])
            elif target:
                # DIFFERENT kind: plain SWAP (same as a bag-to-bag drag) —
                # the result lands on the chosen cell and the displaced
                # stack parks back on the result slot. The item is "free":
                # the player decides where it goes, nothing is rejected.
                inv.set_slot(slot, res["id"], res["qty"])
                rt.craft_results[user_id] = {"id": target[0], "qty": target[1]}
            else:
                inv.set_slot(slot, res["id"], res["qty"])
                rt.craft_results.pop(user_id, None)
        else:
            inv.add(res["id"], res["qty"])
            rt.craft_results.pop(user_id, None)
        await self._persist_full_inventory(channel_id, user_id, inv)
        self._notify_inventory_change(channel_id, user_id)
        return {"ok": True, "reason": "ok", "item_id": res["id"], "qty": res["qty"]}

    async def add_item(self, channel_id: int, user_id: int, item_id: str, qty: int = 1) -> None:
        # NOTE: currency is a FREE item in the bag (the player drags it
        # anywhere; depositing is an explicit drag onto the purse/icons).
        # No auto-convert here.
        inv = self.get_inventory(channel_id, user_id)
        inv.add(item_id, qty)
        # FULL persist (not single-item): the new stack's slot position must
        # survive a restart or the positional load re-compacts the layout.
        await self._persist_full_inventory(channel_id, user_id, inv)
        self._notify_inventory_change(channel_id, user_id)

    async def use_item(self, channel_id: int, user_id: int, item_id: str):
        rt = self.get_runtime_for(channel_id, user_id)
        p = rt.state.get_player(user_id)
        if p is None:
            return False, "no_player"
        # ---- EATING flow (user feature) ----
        # A consumable starts an EAT: the player moves at half speed for
        # EAT_DURATION_S (chewing), then the heal lands. Anti-spam cooldown
        # 1.5s (Kaetram EDIBLE_COOLDOWN). Full HP/mana rejects like Kaetram.
        import time as _time
        from game.items import ITEM_REGISTRY

        item = ITEM_REGISTRY.get(item_id)
        if item is not None and item.type == "consumable":
            now = _time.monotonic()
            if now - p.last_eat_at < EAT_COOLDOWN_S:
                return False, "eat_cooldown"
            heals_hp = "heal_hp" in item.effect
            heals_mp = "heal_mp" in item.effect
            # NOTE: NO "already_full" gate — full HP/mana may still eat (the
            # user wants Minecraft-style free eating); the heal just caps.
            if self.get_inventory(channel_id, user_id).count(item_id) <= 0:
                return False, "empty"
            # Start the eat: slow movement + client particles begin NOW; the
            # heal + stack decrement land when the chew completes.
            p.last_eat_at = now
            p.eating_item = item_id
            p.eating_until = now + EAT_DURATION_S
            return True, "eating"

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

    def _eat_complete_beat(self, rt: ScenarioRuntime, player, now: float) -> None:
        """20 Hz beat: when an eat's chew window elapses, land the heal and
        consume the item (the stack drains through the normal remove path)."""
        if player.eating_until <= 0.0 or now < player.eating_until:
            return
        item_id = player.eating_item
        player.eating_until = 0.0
        player.eating_item = None
        if not item_id:
            return
        from game.items import ITEM_REGISTRY

        item = ITEM_REGISTRY.get(item_id)
        inv = rt.inventories.get(player.user_id)
        if item is None or inv is None or inv.count(item_id) <= 0:
            return
        # Apply the heal (same rules as apply_effect but through inv.use so
        # the stack decrements atomically).
        ok, _reason = inv.use(item_id, player)
        if not ok:
            return
        if self.db is not None:
            from persistence.repositories import save_player, save_inventory_order

            self._schedule_save(rt, player)
            self._schedule_inv_save(rt, player.user_id, inv)
        self._notify_inventory_change(rt.channel_id, player.user_id)
        # Remember the finished eat for the client's heal burst (snapshot
        # payload reads this; short-lived, runtime-only).
        import time as _t
        player.last_heal_eat = (item_id, _t.monotonic())

    def _schedule_inv_save(self, rt: ScenarioRuntime, user_id: int, inv) -> None:
        """Persist one player's bag order (debounced through the same flush
        task as the player save — reuse _schedule_save's task slot)."""
        if self.db is None:
            return
        from persistence.repositories import save_inventory_order

        async def _run() -> None:
            await save_inventory_order(self.db, rt.channel_id, user_id, list(inv.items))

        self._pending_inv = getattr(self, "_pending_inv", {})
        self._pending_inv[(rt.channel_id, user_id)] = _run
        if getattr(rt, "_inv_save_task", None) is None or rt._inv_save_task.done():
            rt._inv_save_task = asyncio.create_task(self._flush_inv_saves(rt))

    async def _flush_inv_saves(self, rt: ScenarioRuntime) -> None:
        pending = getattr(self, "_pending_inv", {})
        mine = [
            coro for (cid, _uid), coro in pending.items() if cid == rt.channel_id
        ]
        for key in [k for k in pending if k[0] == rt.channel_id]:
            pending.pop(key)
        for coro in mine:
            try:
                await coro()
            except Exception as e:  # noqa: BLE001 — save failure must not break the tick
                log.warning("[SAVE] inventory order flush failed: %s", e)

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

    async def craft_from_inputs(self, channel_id: int, user_id: int,
                                inputs: list,
                                layout: list | None = None) -> dict:
        """Web craft-grid craft: consume EXACTLY the materials the client's
        material grid held ([(item_id, qty), ...]) from the REAL bag and
        PARK the output in the result slot (never straight into the bag —
        the player clicks the result slot to collect it). ``layout`` is the
        optional exact 3x3 placement [(item_id, col, row)] — when present
        the recipe must match the arrangement (mirror allowed).

        Result-slot rule: same item stacks on top of what's parked; a
        different item is rejected with ``result_slot_occupied`` until the
        player collects.

        Table-gated recipes additionally require standing near a crafting
        table (re-checked here at craft time, rule 9).

        Returns {ok, reason, item_id, qty}: on success the output was parked
        in the result slot; on failure nothing was consumed.
        """
        from game import crafting

        rt = self.get_runtime_for(channel_id, user_id)
        player = rt.state.get_player(user_id)
        if player is None:
            return {"ok": False, "reason": "no_player", "item_id": None, "qty": 0}
        # Client shapes vary: web sends [{"id","qty"}, ...] dicts while the
        # legacy path sends [(id, qty), ...] pairs. Normalise BOTH — iterating
        # a dict entry as (iid, qty) unpacked its KEYS ("id","qty") and
        # int("qty") raised ValueError, which used to KILL the whole relay
        # socket (every web client disconnected + backoff-reconnected).
        cleaned = []
        for entry in inputs or []:
            if isinstance(entry, dict):
                iid, qty = entry.get("id"), entry.get("qty", 0)
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                iid, qty = entry[0], entry[1]
            else:
                continue
            try:
                q = int(qty)
            except (TypeError, ValueError):
                continue
            if iid and q > 0:
                cleaned.append((str(iid), q))
        if not cleaned:
            return {"ok": False, "reason": "empty_grid", "item_id": None, "qty": 0}
        # Layout-aware match: the client may send the exact 3x3 placement
        # [(item_id, col, row), ...]. With a layout, the recipe must match
        # the ARRANGEMENT (mirror allowed) — not just the multiset.
        layout_norm = None
        if isinstance(layout, list):
            layout_norm = []
            for entry in layout:
                if isinstance(entry, dict):
                    iid = entry.get("id")
                    col, row = entry.get("col"), entry.get("row")
                elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
                    iid, col, row = entry[0], entry[1], entry[2]
                else:
                    continue
                try:
                    layout_norm.append((str(iid), int(col), int(row)))
                except (TypeError, ValueError):
                    continue
        recipe = crafting.find_recipe_by_inputs(cleaned, pattern=layout_norm)
        if recipe is None:
            return {"ok": False, "reason": "no_matching_recipe", "item_id": None, "qty": 0}
        near_table = crafting.nearest_station(rt.state.blocks, player)
        inv = self.get_inventory(channel_id, user_id)
        ok, reason = crafting.can_craft(recipe, inv, near_table)
        if not ok:
            return {"ok": False, "reason": reason, "item_id": None, "qty": 0}
        out_id, out_qty = recipe.output
        # Result-slot stacking rule (user spec): same item stacks, a
        # different item must be collected first.
        parked = rt.craft_results.get(user_id)
        if parked and parked["id"] != out_id:
            return {"ok": False, "reason": "result_slot_occupied",
                    "item_id": None, "qty": 0}
        # Consume the inputs from the REAL bag (remove() is total-aware).
        # AGGREGATE the client's cells first: the same item may sit on
        # several grid cells (stone x3 + stone x5), while the recipe needs
        # the total in one entry — a per-cell check would reject the craft.
        totals: Dict[str, int] = {}
        for iid, qty in cleaned:
            totals[iid] = totals.get(iid, 0) + qty
        for iid, qty in totals.items():
            inv.remove(iid, qty)
        if parked:
            parked["qty"] += out_qty
        else:
            rt.craft_results[user_id] = {"id": out_id, "qty": out_qty}
        await self._persist_full_inventory(channel_id, user_id, inv)
        await self._clear_depleted_hotbars(channel_id, user_id)
        self._notify_inventory_change(channel_id, user_id)
        return {"ok": True, "reason": "ok", "item_id": out_id, "qty": out_qty}

    async def _persist_inventory(self, channel_id: int, user_id: int, item_id: str, qty: int) -> None:
        if self.db is None:
            return
        from persistence.repositories import save_inventory_item

        await save_inventory_item(self.db, channel_id, user_id, item_id, qty)

    async def _persist_full_inventory(self, channel_id: int, user_id: int,
                                      inv: Inventory) -> None:
        """Persist every stack position of a slot-grid bag (order included).

        PERF: batched into ONE transaction — used to commit per-stack plus
        per-row order updates (a 15-stack bag ≈ 30 fsyncs per craft/move)."""
        if self.db is None:
            return
        from persistence.repositories import save_inventory_item, save_inventory_order

        stmts = []
        for iid, qty in inv.items.items():
            if qty <= 0:
                stmts.append((
                    "DELETE FROM inventory WHERE channel_id=? AND user_id=? AND item_id=?",
                    (channel_id, user_id, iid),
                ))
            else:
                stmts.append((
                    """INSERT INTO inventory (channel_id, user_id, item_id, qty)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(channel_id, user_id, item_id) DO UPDATE SET qty=excluded.qty""",
                    (channel_id, user_id, iid, qty),
                ))
        await self.db.execute_many(stmts)
        await save_inventory_order(self.db, channel_id, user_id, list(inv.items))

    # ----- furnace (smelting) adapters: thin, all logic in game/smelting.py --

    def _furnace_at(self, rt: ScenarioRuntime, x: int, y: int):
        """The FurnaceState for the furnace block at (x, y), creating it on
        first use. Returns None when no furnace block sits on that tile."""
        from game.smelting import FurnaceState

        if rt.state.blocks.get(x, y) != "furnace":
            return None
        f = rt.state.furnaces.get((x, y))
        if f is None:
            f = FurnaceState(x=x, y=y)
            rt.state.furnaces[(x, y)] = f
        return f

    async def furnace_put_input(self, channel_id: int, user_id: int,
                                x: int, y: int, item_id: str, qty: int = 1):
        """Bag -> furnace input slot. Station gate re-checked server-side.
        Returns (ok, reason)."""
        from game import smelting

        rt = self.get_runtime_for(channel_id, user_id)
        player = rt.state.get_player(user_id)
        if player is None:
            return False, "no_player"
        f = self._furnace_at(rt, x, y)
        if f is None:
            return False, "no_furnace"
        if smelting.nearest_furnace(rt.state.blocks, player) != (x, y):
            return False, "too_far"
        inv = self.get_inventory(channel_id, user_id)
        ok, reason = smelting.put_input(f, inv, item_id, qty)
        if ok:
            await self._persist_inventory(channel_id, user_id, item_id, inv.count(item_id))
            await self._persist_furnace(channel_id, f)
        return ok, reason

    async def furnace_add_fuel(self, channel_id: int, user_id: int,
                               x: int, y: int, item_id: str, qty: int = 1):
        """Bag -> furnace burn time. Returns (ok, reason)."""
        from game import smelting

        rt = self.get_runtime_for(channel_id, user_id)
        player = rt.state.get_player(user_id)
        if player is None:
            return False, "no_player"
        f = self._furnace_at(rt, x, y)
        if f is None:
            return False, "no_furnace"
        if smelting.nearest_furnace(rt.state.blocks, player) != (x, y):
            return False, "too_far"
        inv = self.get_inventory(channel_id, user_id)
        ok, reason = smelting.add_fuel(f, inv, item_id, qty)
        if ok:
            await self._persist_inventory(channel_id, user_id, item_id, inv.count(item_id))
            await self._persist_furnace(channel_id, f)
        return ok, reason

    async def furnace_take_output(self, channel_id: int, user_id: int, x: int, y: int):
        """Furnace output slot -> bag. Returns (ok, reason, item_id, qty)."""
        from game import smelting

        rt = self.get_runtime_for(channel_id, user_id)
        player = rt.state.get_player(user_id)
        if player is None:
            return False, "no_player", None, 0
        f = self._furnace_at(rt, x, y)
        if f is None:
            return False, "no_furnace", None, 0
        if smelting.nearest_furnace(rt.state.blocks, player) != (x, y):
            return False, "too_far", None, 0
        inv = self.get_inventory(channel_id, user_id)
        item_id, qty = f.output_item, f.output_qty
        ok, reason = smelting.take_output(f, inv)
        if ok:
            await self._persist_inventory(channel_id, user_id, item_id, inv.count(item_id))
            await self._persist_furnace(channel_id, f)
            self._notify_inventory_change(channel_id, user_id)
        return ok, reason, item_id, qty

    async def _persist_furnace(self, channel_id: int, furnace) -> None:
        if self.db is None:
            return
        from persistence.repositories import save_furnace

        await save_furnace(self.db, channel_id, furnace)

    async def load_furnaces(self, rt: ScenarioRuntime) -> None:
        """Populate per-tile furnace states from the DB (boot restore, rule 14).
        Furnace blocks themselves come back via load_blocks."""
        if self.db is None:
            return
        from game.smelting import FurnaceState
        from persistence.repositories import load_furnaces

        for row in await load_furnaces(self.db, rt.channel_id):
            f = FurnaceState.from_dict(row)
            rt.state.furnaces[(f.x, f.y)] = f

    def start_smelting(self, enabled: bool = True) -> None:
        """Start the shared furnace-tick loop once."""
        if not enabled:
            return
        if self.smelting_task is None or self.smelting_task.done():
            self.smelting_task = asyncio.create_task(self._smelting_loop())

    async def stop_smelting(self) -> None:
        task = self.smelting_task
        self.smelting_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _smelting_loop(self) -> None:
        """Tick every placed furnace ~1s: land finished outputs, re-arm the
        next smelt, persist on completion. Deadline model means a restart
        loses nothing (the persisted deadline keeps ticking through downtime)."""
        import time as _time

        from game import smelting

        while True:
            started = asyncio.get_running_loop().time()
            now = _time.time()
            for rt in list(self.runtimes.values()) + list(self.side_runtimes.values()):
                # CRASH-PROOF: one bad runtime must not kill the smelting
                # task (same freeze-at-restart death as _web_tick_loop).
                try:
                    if not rt.state.furnaces:
                        continue
                    changed_any = False
                    async with rt.lock:
                        for f in list(rt.state.furnaces.values()):
                            # A broken furnace block kills its state (persisted
                            # row deleted below, outside the lock).
                            if rt.state.blocks.get(f.x, f.y) != "furnace":
                                continue
                            if smelting.tick(f, now):
                                changed_any = True
                    # Drop states whose block was removed; persist completions.
                    for key, f in list(rt.state.furnaces.items()):
                        if rt.state.blocks.get(f.x, f.y) != "furnace":
                            rt.state.furnaces.pop(key, None)
                            if self.db is not None:
                                from persistence.repositories import delete_furnace

                                await delete_furnace(self.db, rt.channel_id, f.x, f.y)
                    if changed_any and self.db is not None:
                        for f in list(rt.state.furnaces.values()):
                            await self._persist_furnace(rt.channel_id, f)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — the loop must survive
                    log.exception(
                        "[SMELT] tick failed for channel %s (loop survives)",
                        getattr(rt, "channel_id", "?"),
                    )
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.05, 1.0 - elapsed))

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
        # A side world created BEFORE this session starts with a stale mob
        # pack (spawned when it was briefly eligible): clear it — trade zones
        # are mob-free by rule.
        from game.travel import is_trade_zone

        if is_trade_zone(rt) and rt.state.web_zombies:
            rt.state.web_zombies.clear()
        return rt

    async def web_travel_trade(self, channel_id: int, user_id: int,
                               action: str) -> tuple:
        """Web mirror of the Discord /khutraodoi command (game-layer only —
        no Discord I/O): teleport the player between the main world and the
        trade lobby.

        action "in": bigmap -> lobbytrade, spawn on the noted tile and save
        the return spot. action "out": back to the saved bigmap spot.
        Returns ``(rt, message)`` — the runtime the player now stands in and
        a Vietnamese status string for the chat (or a refusal reason).
        """
        from game.travel import (
            TRADE_LOBBY_MAP,
            free_arrival_tile,
            move_player_between_runtimes,
            resolve_spawn_tiles,
        )
        main_rt = self.runtimes.get(channel_id)
        if main_rt is None:
            return None, "Chưa có map trong kênh này."
        cur_rt = self.runtime_of(channel_id, user_id) or main_rt
        player = cur_rt.state.get_player(user_id)
        if player is None:
            return None, "Bạn chưa tham gia map."

        if action == "in":
            if cur_rt.map_data.map_id == TRADE_LOBBY_MAP:
                return cur_rt, "Bạn đang ở trong chợ rồi."
            if self.db is not None:
                from persistence.repositories import save_travel_return
                async with cur_rt.lock:
                    await save_travel_return(
                        self.db, channel_id, user_id,
                        cur_rt.map_data.map_id, player.x, player.y,
                        player.direction,
                    )
            lobby_rt = self.get_or_create_side_runtime(main_rt, TRADE_LOBBY_MAP)
            occupied = {(p.x, p.y) for p in lobby_rt.state.get_visible_players()}
            tile = free_arrival_tile(
                lobby_rt,
                resolve_spawn_tiles(lobby_rt, self.portals, TRADE_LOBBY_MAP),
                occupied,
            )
            async with cur_rt.lock:
                move_player_between_runtimes(cur_rt, lobby_rt, user_id, tile)
            self.touch_session(channel_id, user_id)
            self._notify_travel_change(cur_rt, user_id)
            self._notify_travel_change(lobby_rt, user_id)
            return lobby_rt, "Đã vào khu trao đổi. (dùng /khutraodoi out để ra)"

        # action == "out"
        saved = None
        if self.db is not None:
            from persistence.repositories import load_travel_return
            saved = await load_travel_return(self.db, channel_id, user_id)
        if cur_rt.map_data.map_id == main_rt.map_data.map_id and saved is None:
            return cur_rt, "Bạn không ở trong chợ."
        tile = (main_rt.map_data.spawn[0], main_rt.map_data.spawn[1])
        direction = "SOUTH"
        if saved is not None and saved[0] == main_rt.map_data.map_id:
            tile, direction = (saved[1], saved[2]), saved[3]
        if not main_rt.collision.is_walkable(*tile):
            tile = tuple(main_rt.map_data.spawn)
        async with cur_rt.lock:
            move_player_between_runtimes(cur_rt, main_rt, user_id, tile)
        player = main_rt.state.get_player(user_id)
        if player is not None:
            player.direction = direction
        if self.db is not None:
            from persistence.repositories import delete_travel_return
            await delete_travel_return(self.db, channel_id, user_id)
        self.touch_session(channel_id, user_id)
        self._notify_travel_change(cur_rt, user_id)
        self._notify_travel_change(main_rt, user_id)
        return main_rt, "Đã quay về chỗ cũ."

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
                # TRADE ZONES ARE MOB-FREE (user 15/09): the lobby/interior
                # worlds are skipped entirely — no spawn upkeep, no wandering,
                # no bites from the Discord turn pack either.
                from game.travel import is_trade_zone

                if is_trade_zone(rt):
                    continue
                # CRASH-PROOF: a raised exception here used to escape the
                # while-body and kill the shared zombie task forever — night
                # mobs then never spawned/moved/bited again until restart.
                # Isolate per-runtime like _web_tick_loop.
                try:
                    async with rt.lock:
                        # Population upkeep ONLY (spawn/despawn/off-screen
                        # wander). Visible zombies are FROZEN here — they take
                        # one turn per player action in dispatch(), never from
                        # this loop.
                        result = tick_zombies(
                            rt.state,
                            rt.collision,
                            self.zombie_view_rects(rt),
                            night,
                            rng=self.zombie_rng,
                            max_count=area_cap,
                            spawn_chance=ZOMBIE_SPAWN_CHANCE,
                        )
                    # Off-screen wandering does not need an upload. Refresh
                    # only when a creature becomes visible or is removed.
                    if result.visible_changed:
                        self._schedule_zombie_updates(rt, zombie_result=result)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — the loop must survive
                    log.exception(
                        "[ZOMBIE] tick failed for channel %s (loop survives)",
                        getattr(rt, "channel_id", "?"),
                    )
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
        import time as _time

        async with rt.lock:
            # A respawn whose in-memory task was lost (restart / side world)
            # must not keep the player locked out: revive lazily so the very
            # next action works instead of answering "dead" forever.
            _actor = rt.state.get_player(action.user_id)
            if _actor is not None and _actor.revive_if_expired(_time.time()):
                self._schedule_save(rt, _actor)
            # Expose the runtime-scoped inventory map to the pure rule layer
            # (apply_attack reads the held item for bare-hand vs weapon dmg).
            # The hotbar is a projection of each ordered bag — no separate map.
            rt.state.inventories = rt.inventories
            # TRADE ZONE GATE (user rule 15/09): in the trade lobby/interior
            # building is forbidden — no placing AND no breaking blocks.
            # One check here covers the Discord D-pad path and the web mouse
            # path alike (both funnel through dispatch).
            from game.travel import is_trade_zone

            if is_trade_zone(rt) and isinstance(
                action, (PlaceBlockAction, BreakBlockAction)
            ):
                _actor_g = rt.state.get_player(action.user_id)
                result = ActionResult(
                    False, "trade_zone_protected",
                    pos=(_actor_g.x, _actor_g.y) if _actor_g is not None else None,
                )
            elif isinstance(action, PlaceBlockAction):
                inv = self.get_inventory(channel_id, action.user_id)
                result = apply_place_block(
                    rt.state, action, rt.collision, rt.state.blocks, inv
                )
                rt.dirty = rt.dirty or result.state_changed
                if result.state_changed:
                    await self._clear_depleted_hotbars(channel_id, action.user_id)
            elif isinstance(action, BreakBlockAction):
                inv = self.get_inventory(channel_id, action.user_id)
                actor0 = rt.state.get_player(action.user_id)
                tired0 = actor0 is not None and actor0.stamina <= 0.0
                if tired0:
                    # Out of stamina: THIS swing deals half damage (min 1) —
                    # the block still breaks eventually, just twice as slow.
                    self._tired_break_tile = True
                self._drain_stamina_harvest(actor0)
                result = apply_break_block(rt.state, action, rt.state.blocks, inv, tired=tired0)
                self._tired_break_tile = False
                rt.dirty = rt.dirty or result.state_changed
            elif isinstance(action, ChopAction):
                inv = self.get_inventory(channel_id, action.user_id)
                actor1 = rt.state.get_player(action.user_id)
                tired1 = actor1 is not None and actor1.stamina <= 0.0
                self._drain_stamina_harvest(actor1)
                result = apply_chop(rt.state, action, rt.resources, inv, tired=tired1)
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

    async def _grant_drop_collections(self, rt, collections) -> None:
        """Persist bag changes from collected drop entities + notify hubs."""
        from game.purse import is_currency
        by_user: dict = {}
        for user_id, item_id, qty in collections:
            # Currency picked up from a drop AUTO-BANKS into the purse —
            # the user's rule: nhặt xu từ map thì vào ví luôn, không nằm
            # trong túi. Direct purse credit (the coin never touches the
            # bag, so purse_add's bag-drain is not the right primitive).
            if is_currency(item_id):
                p = rt.state.get_player(user_id)
                if p is not None:
                    field = "coins" if item_id == "coin" else "crystals"
                    setattr(p, field, getattr(p, field, 0) + qty)
                    self._schedule_save(rt, p)
                    by_user.setdefault(user_id, True)
                continue
            inv = self.get_inventory(rt.channel_id, user_id)
            # Non-currency lands as a FREE bag stack (deposit = drag it onto
            # the purse icons); the next dispatch sweep does NOT touch it
            # (the sweep only fires for the acting user, on their actions).
            inv.add(item_id, qty)
            by_user.setdefault(user_id, True)
            if self.db is not None:
                from persistence.repositories import save_inventory_item

                await save_inventory_item(
                    self.db, rt.channel_id, user_id,
                    item_id, inv.count(item_id),
                )
        for user_id in by_user:
            self._notify_inventory_change(rt.channel_id, user_id)

    async def _grant_zombie_drops(self, rt, user_id: int, drops) -> None:
        inv = self.get_inventory(rt.channel_id, user_id)
        changed = False
        for item_id, qty in drops:
            inv.add(item_id, qty)  # currency = free bag stack (see add_item)
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
        # Multi-world: the player may have died in a SIDE runtime (lobby /
        # interior). Looking only at self.runtimes made ``player`` come back
        # None there, so the task returned without ever respawning them —
        # dead forever. runtime_of resolves the world that holds them.
        rt = self.runtime_of(channel_id, user_id)
        if rt is None:
            return
        async with rt.lock:
            player = rt.state.get_player(user_id)
            if player is None or player.dead_until is None:
                return
            # Respawn to a RANDOM walkable tile — never in place. Waking up on
            # the death tile (inside the zombie pack) got the player bitten
            # back to 0 HP on the next tick, forever ("treo màn hồi sinh").
            # Clear the zombie aggro too so nothing is mid-bite on arrival.
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
            # Web pack grace: reset every web zombie's bite cooldown so the
            # arrival tile is never bitten on the very next 20 Hz tick
            # ("treo màn hồi sinh" — died, revived, instantly re-bitten).
            import time as _respawn_time

            _web_pack = getattr(rt.state, "web_zombies", None)
            if isinstance(_web_pack, dict):
                for z in _web_pack.values():
                    try:
                        z.last_bite = _respawn_time.monotonic()
                    except Exception:
                        pass
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
            try:
                ws = await self.weather_service.fetch(time.time())
                # Cache the freshest snapshot: new scenarios seed from it so
                # they open on live weather instead of the sun_clouds
                # placeholder.
                self._latest_weather = ws
                hub = getattr(self, "hub_coalescer", None)
                for rt in list(self.runtimes.values()) + list(self.side_runtimes.values()):
                    async with rt.lock:
                        rt.weather_state = ws
                        # Only adopt the fresh auto key when the scenario is
                        # NOT manually pinned by an admin override.
                        if not getattr(rt, "weather_manual", False):
                            rt.weather_key = ws.weather_key
                            # Persist the adopted key so a rejoin sees the
                            # same sky the rest of the server is under.
                            if self.db is not None:
                                from persistence.repositories import save_scenario_weather
                                await save_scenario_weather(
                                    self.db, rt.channel_id, ws.weather_key, False)
                    if hub is not None:
                        hub.schedule(rt.channel_id, {"type": "weather"})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad fetch must not kill
                # the weather task (all scenarios would freeze on the last
                # snapshot forever after).
                log.exception("[WEATHER] fetch/fanout failed (loop survives)")
            await asyncio.sleep(self.weather_service.refresh_interval)

    async def _lightning_loop(self) -> None:
        """Storm lightning events: every 5-13 s, re-seed every storm runtime's
        strike (position / size / mirror / distant-flicker are all derived
        from the seed in rendering.weather_fx) and refresh its screens + hub.
        Between strikes the storm GIF loops as pure rain - no strobing."""
        import random

        while True:
            await asyncio.sleep(random.uniform(5.0, 13.0))
            try:
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
                    try:
                        async with rt.lock:
                            rt.lightning_seed = random.randrange(1, 10**6)
                        if hub is not None:
                            hub.schedule(rt.channel_id, {"type": "weather"})
                        if co is not None:
                            for uid, screen in rt.screens.items():
                                if screen.message_id is not None:
                                    co.schedule((rt.channel_id, uid), {"user_id": uid})
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 — per-runtime isolation
                        log.exception(
                            "[WEATHER] lightning refresh failed for channel %s",
                            getattr(rt, "channel_id", "?"),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — the loop must survive
                log.exception("[WEATHER] lightning pass failed (loop survives)")

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
