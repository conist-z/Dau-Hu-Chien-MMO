import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import (
    ASSETS_DIR,
    DATA_DIR,
    DISCORD_TOKEN,
    SESSION_CHECK_INTERVAL_SEC,
    SESSION_NOTICE_COOLDOWN_SEC,
    SESSION_TIMEOUT_MINUTES,
    USE_DISCORD_AVATARS,
    WEATHER_ENABLED,
    WEATHER_REFRESH_SEC,
    ZOMBIES_ENABLED,
)
from discord_ui.commands import MapCog
from discord_ui.session_end import SessionEndAdapter
from discord_ui.coalescer import RenderCoalescer
from discord_ui.hub_view import HubView, flush_hub_batch, render_hub_to_file
from discord_ui.map_view import MapView, flush_render_batch
from discord_ui.refresh import RefreshScheduler
from game.manager import GameManager
from persistence.database import Database
from persistence.migrations import migrate
from persistence.repositories import load_players, load_scenarios, save_player, save_scenario
from rendering.avatar import AvatarCache
from rendering.hub_renderer import HubRenderer
from rendering.renderer import Renderer
from config import WEB_API_ENABLED

load_dotenv()

log = logging.getLogger("GAME")

intents = discord.Intents.default()

_monitor_task: asyncio.Task | None = None


async def _loop_lag_monitor() -> None:
    """Detect event-loop stalls: every second, measure how much a 1s sleep
    overshot. CPU work running ON the loop (should now be rare — rendering runs
    in worker threads) shows up as lag and would delay interaction handling."""
    loop = asyncio.get_running_loop()
    while True:
        t0 = loop.time()
        await asyncio.sleep(1.0)
        lag = (loop.time() - t0) - 1.0
        if lag > 0.1:
            log.warning("[LOOP] event-loop lag %.0fms — input handling was queued", lag * 1000)


async def _message_exists(channel, message_id: Optional[int]) -> Optional[bool]:
    """Return True if ``message_id`` still exists in ``channel``.

    False (definitive 404 / Unknown Message) lets the caller drop a stale
    persisted id so the player can re-create their screen via /joinmap.
    None (channel cache miss or a transient non-429 HTTP error) tells the caller
    to keep the id and rely on the runtime self-heal paths (button-press
    NotFound -> recreate, edit-gate NotFound -> recreate). A transient failure
    must NOT be mistaken for a deleted message.
    """
    if channel is None or message_id is None:
        return None
    try:
        await channel.fetch_message(message_id)
        return True
    except discord.NotFound:
        return False
    except discord.HTTPException as e:
        log.warning("[BOOT] could not verify message %s: %s", message_id, e)
        return None


class GameBot(commands.Bot):
    async def close(self):
        # Stop world loops and flush any debounced player saves first.
        if relay_client is not None:
            await relay_client.hub.stop()
            await relay_client.stop()
        await refresh_scheduler.stop()
        await manager.stop_sessions()
        if manager.web_task is not None and not manager.web_task.done():
            await manager.stop_web_tick()
        await manager.stop_zombies()
        await manager.stop_weather()
        await manager.flush_all()
        await super().close()


bot = GameBot(command_prefix="!", intents=intents)

manager = GameManager(ASSETS_DIR)

# Paces the hub (~5s) and screen (~8s) auto-refresh beats + the screen/hub pair
# liveness checks. One shared task; per-player cadence state is runtime-only.
refresh_scheduler = RefreshScheduler(manager)
session_adapter = SessionEndAdapter(bot, manager)
manager.session_adapter = session_adapter

# Default avatar pack (bundled Twemoji) + server-emoji disk cache. The pack
# ships with the bot so avatar choice works with zero runtime network access;
# server emoji PNGs are cached on disk, never in SQLite (rule 19).
AVATARS_DIR = ASSETS_DIR.parent / "avatars"
EMOJI_CACHE_DIR = DATA_DIR / "avatar_cache"


def _make_emoji_fetcher(b):
    """Injected coroutine: emoji id -> PNG bytes (any guild the bot can see).

    Kept here so game/rendering layers never import discord.py (rule 2)."""

    async def _fetch(emoji_id: int):
        emoji = discord.utils.get(b.emojis, id=int(emoji_id))
        if emoji is None:
            for g in b.guilds:
                emoji = g.get_emoji(int(emoji_id))
                if emoji is not None:
                    break
        if emoji is None:
            return None
        return await emoji.read()

    return _fetch


avatar_cache = AvatarCache(
    use_network=USE_DISCORD_AVATARS,
    avatars_dir=AVATARS_DIR,
    emoji_cache_dir=EMOJI_CACHE_DIR,
    emoji_fetcher=_make_emoji_fetcher(bot),
)
renderer = Renderer(ASSETS_DIR, avatar_cache)
db = Database(str(DATA_DIR / "game.db"))

# Web client gateway (dial-out relay client + WebHub frame processor). Started
# in setup_hook after the manager is wired; stopped in close().
relay_client = None
if WEB_API_ENABLED:
    from web_api.relay_client import build_relay_client

    relay_client = build_relay_client(manager)


@bot.event
async def setup_hook():
    await db.connect()
    await migrate(db)
    manager.renderer = renderer
    manager.db = db
    manager.hub_renderer = HubRenderer(ASSETS_DIR)
    manager.bot_ref = bot
    # Coalesce rapid emoji presses into a single render+upload per channel.
    manager.coalescer = RenderCoalescer(
        delay=0.08,
        on_flush=lambda cid, batch: flush_render_batch(manager, cid, batch),
    )
    manager.hub_coalescer = RenderCoalescer(
        delay=0.12,
        on_flush=lambda cid, batch: flush_hub_batch(manager, cid, batch),
    )

    for sc in await load_scenarios(db):
        rt = manager.create_runtime(sc["channel_id"], sc["map_id"], sc["message_id"], sc["hub_message_id"])
        await manager.load_inventories(rt)
        await manager.load_blocks(rt)
        await manager.load_resources(rt)
        await manager.load_terrain(rt)
        await manager.load_furnaces(rt)
        await manager.load_hotbars(rt)
        channel = bot.get_channel(sc["channel_id"])
        has_screens = False
        for p in await load_players(db, sc["channel_id"]):
            player = rt.state.add_player(p["user_id"], p.get("display_name", ""), p["x"], p["y"])
            player.direction = p["direction"]
            player.sprite_id = p["sprite_id"]
            player.visible = p["visible"]
            player.hp = p["hp"]
            player.max_hp = p["max_hp"]
            # Death state is EPHEMERAL: ``dead_until`` is deliberately never
            # persisted, so a player saved at hp 0 (killed while the bot was
            # up) reloaded as "dead with no respawn task" — and ``alive`` is
            # hp > 0 AND no deadline, so they stayed locked out FOREVER:
            # frozen in place, every action refused, the web overlay stuck at
            # "Đang hồi sinh…". A restart means the 5 s window is long over.
            if player.hp <= 0:
                player.hp = player.max_hp
                player.visible = True
                if manager.db is not None:
                    await save_player(manager.db, sc["channel_id"], player)
            player.mana = p["mana"]
            player.max_mana = p["max_mana"]
            player.level = p["level"]
            player.xp = p["xp"]
            player.coins = p["coins"]
            player.class_id = p["class_id"]
            # Remembered movement preference (1/3/5 steps per press).
            player.step_size = int(p.get("step_size") or 1)
            # Per-player screen: restore its message id and re-bind the view so
            # the buttons keep working after a restart.
            screen = rt.screens.get(player.user_id)
            if screen is not None:
                screen.step_size = player.step_size
            if p.get("screen_message_id"):
                screen = manager.ensure_screen(rt, player.user_id)
                screen.message_id = p["screen_message_id"]
                if p.get("hub_message_id"):
                    screen.hub_message_id = p["hub_message_id"]
                # Split layout: restore the persisted D-pad (controls) id. It
                # is verified in create_controls_message lazily, so a stale
                # id self-heals on the next press/refresh without blocking.
                if p.get("controls_message_id"):
                    screen.controls_message_id = p["controls_message_id"]
                    screen.split_layout = True
                # The screen message may have been deleted while the bot was
                # down (Discord purge / manual delete). A stale persisted id
                # makes every edit 404 until the message is recreated — the
                # reported "màn hình bị mất" case. Verify it still exists; if
                # not, drop the stale id (and the hub id, but only if that hub
                # message is gone too, to avoid orphaning a still-live hub) so
                # /joinmap and the button self-heal can mint a fresh screen
                # instead of leaving the player stuck on a dead message.
                stale = False
                if await _message_exists(channel, screen.message_id) is False:
                    log.info(
                        "[BOOT] stale screen id %s for user %s in %s; clearing",
                        screen.message_id, player.user_id, sc["channel_id"],
                    )
                    screen.message_id = None
                    player.screen_message_id = None
                    if (
                        screen.hub_message_id is not None
                        and await _message_exists(channel, screen.hub_message_id) is False
                    ):
                        screen.hub_message_id = None
                        player.hub_message_id = None
                    if manager.db is not None:
                        await save_player(manager.db, rt.channel_id, player)
                    stale = True
                if not stale:
                    mv = MapView(sc["channel_id"], manager, player.user_id)
                    screen.map_view = mv
                    bot.add_view(mv)
                    bot.add_view(HubView(sc["channel_id"], manager, player.user_id))
                    has_screens = True
            # Restored coords may be out of bounds / inside a wall after a map
            # edit (the map is remapped to its asset bounding box on load).
            if not rt.map_data.is_walkable(player.x, player.y):
                player.x, player.y = rt.map_data.spawn
        # Legacy shared-screen message (pre per-player screens): keep its
        # buttons alive — pressing one now mints the presser a personal screen.
        if rt.message_id is not None and not has_screens:
            bot.add_view(MapView(sc["channel_id"], manager, None))
        bot.add_view(HubView(sc["channel_id"], manager))
        # Recreate the hub message if it was deleted while the bot was down.
        if rt.hub_message_id is not None:
            try:
                await channel.fetch_message(rt.hub_message_id)
            except Exception:
                try:
                    rt.hub_message_id = None
                    await channel.fetch_message(rt.message_id)
                    file, embed = await render_hub_to_file(manager, rt)
                    hub = await channel.send(file=file, embed=embed, view=HubView(sc["channel_id"], manager))
                    rt.hub_message_id = hub.id
                    await save_scenario(db, sc["channel_id"], rt.message_id, rt.map_data.map_id, rt.hub_message_id)
                except Exception:
                    pass

    await bot.add_cog(MapCog(bot, manager, renderer, db))

    # Web gateway: dial out to the relay so browser clients can join the same
    # scenarios (no inbound panel port needed). After the manager is fully
    # wired (db/renderer/coalescers above).
    if relay_client is not None:
        relay_client.start()

    # Start the shared national-weather, night-zombie and furnace-smelting
    # loops (non-blocking).
    manager.start_weather(enabled=WEATHER_ENABLED, refresh_interval=WEATHER_REFRESH_SEC)
    manager.start_zombies(enabled=ZOMBIES_ENABLED)
    manager.start_smelting()

    # Hub/screen auto-refresh + pair self-heal (the hub always lives directly
    # under a live screen; neither message is ever allowed to linger alone).
    refresh_scheduler.start()
    # Session watchdog: auto-end idle sessions with a channel notice.
    manager.configure_sessions(
        SESSION_TIMEOUT_MINUTES, SESSION_CHECK_INTERVAL_SEC,
        cooldown=SESSION_NOTICE_COOLDOWN_SEC,
    )
    manager.start_sessions(enabled=SESSION_TIMEOUT_MINUTES > 0)

    # Event-loop stall monitor (keeps a module-level reference so it is not GC'd).
    global _monitor_task
    _monitor_task = asyncio.create_task(_loop_lag_monitor())

    # Discord apps may carry an "Entry Point" command (raw API type 4, used by
    # Activities/App-launch) that a bulk global sync is forbidden from removing.
    # Delete it separately first, otherwise tree.sync() raises HTTP 50240.
    try:
        existing = await bot.http.get_global_commands(bot.application_id)
        for cmd in existing:
            if cmd.get("type") == 4:
                await bot.http.delete_global_command(bot.application_id, cmd["id"])
                print(f"[BOOT] removed entry-point command {cmd.get('name')!r}")
    except Exception as e:
        print(f"[WARN] could not prune entry-point command: {e}")

    await bot.tree.sync()
    print(f"[BOOT] restored {len(manager.runtimes)} scenario(s)")


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    """Catch ANY deletion of a session's screen/hub/inventory message.

    Deletes the bot performs itself are suppressed (session_end adapter and
    the UI teardowns register their ids first). Everything else — a moderator
    purge, a manual right-click delete, Discord auto-cleanup — is reported
    with the specific player, message and channel, and the stale ids are
    cleared so /joinmap (or a button press) mints a fresh screen.
    """
    suppress = getattr(session_adapter, "suppress_ids", None)
    if suppress is not None and payload.message_id in suppress:
        suppress.discard(payload.message_id)
        return
    cid = payload.channel_id
    rt = manager.get_runtime(cid)
    if rt is None:
        return
    for uid, screen in list(rt.screens.items()):
        role = None
        if payload.message_id == screen.message_id:
            role = "screen (màn hình chơi)"
        elif payload.message_id == screen.hub_message_id:
            role = "hub (HUD + nút chức năng)"
        elif payload.message_id == screen.inventory_message_id:
            role = "inventory (túi đồ)"
        elif payload.message_id == screen.controls_message_id:
            role = "controls (nút di chuyển)"
        if role is None:
            continue
        # A deleted SCREEN is case 2 (deliberate destruction by a privileged
        # user): NO silent re-mint (that would resurrect a screen someone
        # intentionally destroyed) — tear the whole session down, keep the
        # player row, and post the authoritative "ended" notice. /joinmap
        # (or a legacy button press) mints a fresh pair on demand.
        if role.startswith("screen"):
            await session_adapter.end(
                cid, uid, "message_deleted",
                detail=f"tin nhắn {role} của bạn (id {payload.message_id}) vừa bị xoá "
                       f"trong kênh — dùng /joinmap để chơi lại",
            )
            break
        # Controls deletion: session stays open; the next press/refresh
        # re-creates the D-pad message lazily (create_controls_message).
        if role.startswith("controls"):
            screen.controls_message_id = None
            player = rt.state.get_player(uid)
            if player is not None and player.controls_message_id == payload.message_id:
                player.controls_message_id = None
                if manager.db is not None:
                    from persistence.repositories import save_player

                    try:
                        await save_player(manager.db, cid, player)
                    except Exception as e:  # noqa: BLE001
                        log.warning("[SESSION] persist cleared controls id failed: %s", e)
            log.info("[SESSION] controls message %s deleted for user %s in %s; "
                     "will re-create on next press", payload.message_id, uid, cid)
            break
        # Hub/inventory deletion: the session STAYS open and the default
        # silent-repair policy applies — re-create the missing piece quietly
        # (no channel notice; a missing hub/panel is a glitch, not a shutdown).
        if role.startswith("hub"):
            screen.hub_message_id = None
            player = rt.state.get_player(uid)
            if player is not None and player.hub_message_id == payload.message_id:
                player.hub_message_id = None
                if manager.db is not None:
                    from persistence.repositories import save_player

                    try:
                        await save_player(manager.db, cid, player)
                    except Exception as e:  # noqa: BLE001
                        log.warning("[SESSION] persist cleared hub id failed: %s", e)
            from discord_ui.session_recovery import schedule_repair

            schedule_repair(manager, rt, uid, why="hub message deleted")
        elif role.startswith("controls"):
            pass  # id already cleared above; next press re-creates it lazily
        else:
            screen.inventory_message_id = None
        break


@bot.event
async def on_ready():
    # OAuth visibility check: makes "oauth_not_configured" instantly debuggable
    # from the panel log (vars are read from env at import time).
    from config import DISCORD_OAUTH_CLIENT_ID, DISCORD_OAUTH_CLIENT_SECRET
    print(
        f"[OAUTH] client_id={'set' if DISCORD_OAUTH_CLIENT_ID else 'MISSING'} "
        f"secret={'set' if DISCORD_OAUTH_CLIENT_SECRET else 'MISSING'}"
    )
    print(f"[READY] {bot.user} ({bot.user.id})")
    # Sync per-guild so commands appear instantly (global commands can take
    # minutes to propagate). This also replaces any stale guild commands.
    for g in bot.guilds:
        try:
            await bot.tree.sync(guild=discord.Object(id=g.id))
        except Exception as e:
            print(f"[WARN] guild sync failed for {g.id}: {e}")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN missing in .env")
    bot.run(DISCORD_TOKEN)
