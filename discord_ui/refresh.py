"""Screen + hub pair coupling and periodic auto-refresh.

Invariants (AGENTS.md rule 7, tightened):
- The hub ALWAYS belongs to a live screen. When the screen message dies
  (deleted / purged / 404) the hub is deleted with it and re-created under the
  player's next screen — a lone hub is never allowed to linger.
- The screen ALWAYS has its hub directly underneath. When the hub dies, a
  fresh one is created below the same screen; if the pair is ever inverted
  (the screen was re-created BELOW the old hub by a self-heal path), the old
  hub is dropped and re-sent under the new screen.

Three mechanisms keep that true without any user input:

1. Event pushes (already wired everywhere): stat/inventory/weather changes
   schedule a coalesced hub flush.
2. Signature polling + periodic beats: :func:`hub_signature` snapshots
   everything the HUD displays (clock minute, weather, storm seed, players,
   HP/mana/coins, bag, hotbar...). A changed signature schedules a hub refresh
   even when no push path knew about it; the hub also re-renders every
   HUB_REFRESH_SECONDS and the screen every SCREEN_REFRESH_SECONDS so clocks,
   GIF weather and day/night lighting stay honest with zero interaction.
3. Pair liveness: every PAIR_CHECK_SECONDS the player's screen+hub messages
   are verified against Discord and self-healed (delete the orphaned side,
   re-create beneath the surviving screen).

All refreshes ride the existing per-player coalescer lanes (hub lane and
screen lane), so extra load stays bounded and deduplicated no matter how many
players are online. One shared pacer task serves every channel.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import discord

from config import (
    HUB_REFRESH_SECONDS,
    PAIR_CHECK_SECONDS,
    SCREEN_REFRESH_SECONDS,
)

log = logging.getLogger("GAME")

# One scheduler pass per second: signature polling is pure in-memory reads.
TICK_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Change detection (pure reads of live game state — no Discord I/O)
# ---------------------------------------------------------------------------

def hub_signature(rt, user_id: Optional[int]) -> tuple:
    """Cheap snapshot of everything this player's hub HUD displays.

    Pure and read-only. When any input changes (clock minute, weather key or
    snapshot, storm lightning seed, player list, the focused player's
    position/HP/mana/coins, their bag contents or hotbar bindings), the
    signature changes and the UI layer should re-render the hub.
    """
    sig = []
    state = getattr(rt, "state", None)
    if state is not None:
        players = state.get_visible_players()
        sig.append(len(players))
        sig.append(tuple(sorted(p.user_id for p in players)))
        p = state.get_player(user_id) if user_id is not None else None
        if p is None and players:
            p = players[0]
        if p is not None:
            sig.extend((p.x, p.y, p.hp, p.max_hp, p.mana, p.max_mana, p.coins))
    inventories = getattr(rt, "inventories", None) or {}
    inv = inventories.get(user_id) if user_id is not None else None
    sig.append(tuple(sorted(inv.items.items())) if inv is not None else ())
    hotbars = getattr(rt, "hotbars", None) or {}
    sig.append(
        tuple(sorted((hotbars.get(user_id) or {}).items())) if user_id is not None else ()
    )
    sig.append(getattr(rt, "weather_key", None))
    sig.append(getattr(rt, "weather_state", None))
    sig.append(getattr(rt, "lightning_seed", 0) or 0)
    # NOTE: the in-game clock is deliberately NOT in the signature. The clock
    # runs accelerated (a full in-game day defaults to 30 real minutes), so its
    # displayed minute flips every ~1.25s — a signature on it would flush the
    # hub about once per second, storming the per-message edit bucket (429s and
    # failed uploads -> "image failed to load"). The periodic 5s beat below
    # keeps the clock honest without any burst.
    return tuple(sig)


# ---------------------------------------------------------------------------
# Pair liveness + repair (screen dies => hub dies; hub dies => recreated)
# ---------------------------------------------------------------------------

async def _fetch_state(channel, message_id: Optional[int]) -> Optional[bool]:
    """True/False when Discord answered definitively; None on transient errors.

    A transient failure must never be mistaken for a deleted message (that
    would orphan a live screen/hub pair)."""
    if channel is None or message_id is None:
        return None
    try:
        await channel.fetch_message(message_id)
        return True
    except discord.NotFound:
        return False
    except discord.HTTPException as e:
        log.warning("[PAIR] fetch %s failed: %s", message_id, e)
        return None


async def drop_hub_message(rt, user_id: int, channel, suppress_ids=None) -> None:
    """Delete the player's hub message (best effort) and forget its id.

    Screen record and Player keep their screen id — only the hub side goes.
    ``suppress_ids`` (optional) receives the deleted id so bot.py's
    deleted-message hook never mistakes the bot's OWN cleanup for a
    destroyed session (false "ended" notice + lone hub). Callers holding a
    manager pass ``manager.session_adapter.suppress_ids``."""
    screen = rt.screens.get(user_id)
    hid = screen.hub_message_id if screen is not None else None
    if hid is not None and channel is not None:
        if suppress_ids is not None:
            suppress_ids.add(hid)
        try:
            await channel.get_partial_message(hid).delete()
        except (discord.NotFound, discord.HTTPException):
            pass  # already gone — that is the goal
    if screen is not None:
        screen.hub_message_id = None
    state = getattr(rt, "state", None)
    player = state.get_player(user_id) if state is not None else None
    if player is not None:
        player.hub_message_id = None


async def clear_pair(manager, rt, user_id: int, channel) -> None:
    """The screen message is GONE on Discord: drop the hub with it.

    Deletes the hub message, clears both persisted ids (so /joinmap or a
    button press mints a FRESH pair) and schedules a hub flush — which becomes
    a no-op until the next screen exists, guaranteeing no lone hub remains."""
    adapter = getattr(manager, "session_adapter", None)
    suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None
    await drop_hub_message(rt, user_id, channel, suppress_ids=suppress)
    screen = rt.screens.get(user_id)
    if screen is not None:
        screen.message_id = None
    state = getattr(rt, "state", None)
    player = state.get_player(user_id) if state is not None else None
    if player is not None:
        player.screen_message_id = None
        db = getattr(manager, "db", None)
        if db is not None:
            from persistence.repositories import save_player

            try:
                await save_player(db, rt.channel_id, player)
            except Exception as e:  # noqa: BLE001 — persistence is best-effort here
                log.warning("[PAIR] save_player after clear failed: %s", e)
    schedule_hub_refresh(manager, rt.channel_id, user_id)


async def verify_pair(manager, rt, user_id: int, channel) -> str:
    """Verify one player's screen+hub pair on Discord and self-heal it.

    Returns one of ``"ok"``, ``"screen_missing"``, ``"hub_missing"``,
    ``"inverted"`` (screen re-created below the hub) or ``"unknown"``
    (nothing verified — transient error / no channel / no screen record).

    The screen is fetched FIRST: a dead screen takes the hub with it before
    anything else happens, so the pair can never decay into a lone hub."""
    if channel is None:
        return "unknown"
    screen = rt.screens.get(user_id)
    if screen is None or screen.message_id is None:
        return "unknown"  # no screen minted yet — nothing to couple
    sid = screen.message_id
    screen_state = await _fetch_state(channel, sid)
    if screen_state is None:
        return "unknown"
    if screen_state is False:
        log.info(
            "[PAIR] screen %s gone -> dropping hub %s for user %s in %s",
            sid, screen.hub_message_id, user_id, rt.channel_id,
        )
        await clear_pair(manager, rt, user_id, channel)
        return "screen_missing"
    hid = screen.hub_message_id
    if hid is None:
        # Screen alive but hubless: the next flush re-creates one beneath it.
        schedule_hub_refresh(manager, rt.channel_id, user_id)
        return "hub_missing"
    hub_state = await _fetch_state(channel, hid)
    if hub_state is None:
        return "unknown"
    if hub_state is False:
        log.info(
            "[PAIR] hub %s gone -> re-creating under screen %s for user %s in %s",
            hid, sid, user_id, rt.channel_id,
        )
        screen.hub_message_id = None
        state = getattr(rt, "state", None)
        player = state.get_player(user_id) if state is not None else None
        if player is not None:
            player.hub_message_id = None
        schedule_hub_refresh(manager, rt.channel_id, user_id)
        return "hub_missing"
    # HARD ORDER RULE (split layout): screen (top) -> controls (middle) ->
    # hub (bottom). Snowflake ids grow with time, so id order == position
    # order within a channel. Anything out of order is deleted and re-sent.
    cid = getattr(screen, "controls_message_id", None)
    if cid is not None:
        c_state = await _fetch_state(channel, cid)
        if c_state is False:
            screen.controls_message_id = None
            player = rt.state.get_player(user_id) if hasattr(rt, "state") else None
            if player is not None:
                player.controls_message_id = None
            from discord_ui.map_view import create_controls_message

            await create_controls_message(manager, rt, rt.channel_id, channel, user_id)
            return "controls_recreated"
        if c_state is not None and not (sid < cid < hid):
            log.info(
                "[PAIR] controls %s out of order (screen=%s hub=%s) for user %s; re-seating",
                cid, sid, hid, user_id,
            )
            from discord_ui.map_view import create_controls_message

            await create_controls_message(
                manager, rt, rt.channel_id, channel, user_id,
                view=getattr(screen, "map_view", None),
            )
            return "controls_reordered"
    if sid > hid:
        # Snowflake ids are time-ordered: a screen id NEWER than its hub means
        # the screen was re-created (self-heal sends append at the channel
        # bottom), leaving the old hub floating ABOVE its screen. Drop the hub
        # so the next flush re-sends it directly underneath.
        log.info(
            "[PAIR] inverted pair (screen %s > hub %s) for user %s in %s; re-pairing",
            sid, hid, user_id, rt.channel_id,
        )
        await drop_hub_message(rt, user_id, channel)
        schedule_hub_refresh(manager, rt.channel_id, user_id)
        return "inverted"
    return "ok"


async def reattach_hub_under_screen(manager, rt, user_id: int, channel) -> None:
    """Force the hub back under its (re-created) screen message.

    Called by the screen self-heal paths right after a NEW screen message was
    minted: the old hub — still alive but now floating above the new screen —
    is deleted, and the next hub flush sends a fresh one directly below."""
    adapter = getattr(manager, "session_adapter", None)
    suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None
    await drop_hub_message(rt, user_id, channel, suppress_ids=suppress)
    schedule_hub_refresh(manager, rt.channel_id, user_id)


# ---------------------------------------------------------------------------
# Coalesced refresh scheduling (non-blocking; rides the per-player lanes)
# ---------------------------------------------------------------------------

def schedule_hub_refresh(manager, channel_id: int, user_id: int) -> None:
    """Queue one coalesced hub re-render for a player (same lane as pushes)."""
    hub = getattr(manager, "hub_coalescer", None)
    if hub is not None:
        hub.schedule((channel_id, user_id), {"focused_user_id": user_id})


def schedule_screen_refresh(manager, rt, user_id: int) -> bool:
    """Queue one background screen re-render; False when skipped.

    Skipped while a press-driven frame is in flight (``screen.rendering``) or
    a screen flush is already pending — their result is strictly fresher than
    a periodic one, so a duplicate render would only waste CPU."""
    screen = rt.screens.get(user_id)
    if screen is None or screen.rendering or screen.message_id is None:
        return False
    coalescer = getattr(manager, "coalescer", None)
    if coalescer is None or coalescer.pending((rt.channel_id, user_id)) > 0:
        return False
    coalescer.schedule((rt.channel_id, user_id), {"user_id": user_id})
    return True


# ---------------------------------------------------------------------------
# The pacer: one shared task pacing every player's refresh cadence
# ---------------------------------------------------------------------------

@dataclass
class PlayerRefresh:
    """Per-player refresh bookkeeping (runtime-only, never persisted)."""

    hub_sig: tuple = ()
    next_hub: float = 0.0
    next_screen: float = 0.0
    next_pair: float = 0.0


class RefreshScheduler:
    """One shared loop pacing hub/screen auto-refresh + pair checks.

    Cadence per player (configurable, see config.py):
    - hub: re-render every HUB_REFRESH_SECONDS, sooner when
      :func:`hub_signature` detects a HUD-visible change;
    - screen: re-render every SCREEN_REFRESH_SECONDS (slower on purpose — the
      map frame is the expensive upload);
    - pair: verify screen+hub existence/order every PAIR_CHECK_SECONDS.

    Every flush rides the coalescers, so this task only ever SCHEDULES work —
    it never renders or edits on its own (no event-loop blocking, no edit
    bucket pressure beyond what the lanes already pace).
    """

    def __init__(self, manager):
        self.manager = manager
        self._task: Optional[asyncio.Task] = None
        self._players: Dict[Tuple[int, int], PlayerRefresh] = {}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — the pacer must never die
                log.warning("[REFRESH] tick failed: %s", e)
            await asyncio.sleep(TICK_SECONDS)

    async def _tick(self) -> None:
        now = time.monotonic()
        all_rts = list(self.manager.runtimes.values()) + list(
            self.manager.side_runtimes.values()
        )
        for rt in all_rts:
            for uid in list(rt.screens.keys()):
                pf = self._players.setdefault((rt.channel_id, uid), PlayerRefresh())
                try:
                    await self._tick_player(rt, uid, pf, now)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — one player must not stall the rest
                    log.warning("[REFRESH] tick for user %s failed: %s", uid, e)

    async def _tick_player(self, rt, uid: int, pf: PlayerRefresh, now: float) -> None:
        screen = rt.screens.get(uid)
        if screen is None:
            self._players.pop((rt.channel_id, uid), None)
            return

        # "Web wins": while the web client controls this body the Discord
        # screen/hub sit out completely — no beat re-renders, no pair checks
        # spinning on an intentionally stale stack. The moment the web
        # session detaches (player.mode -> "chat") the next tick revives
        # everything through the normal cadence, no extra machinery.
        checker = getattr(self.manager, "web_controlled", None)
        if checker is not None and checker(rt, uid):
            pf.next_hub = now + HUB_REFRESH_SECONDS
            pf.next_screen = now + SCREEN_REFRESH_SECONDS
            pf.next_pair = now + PAIR_CHECK_SECONDS
            return

        # --- hub: periodic beat, or sooner on any HUD-visible change ---------
        if pf.next_hub <= now:
            pf.next_hub = now + HUB_REFRESH_SECONDS
            pf.hub_sig = hub_signature(rt, uid)
            schedule_hub_refresh(self.manager, rt.channel_id, uid)
        else:
            sig = hub_signature(rt, uid)
            if sig != pf.hub_sig:
                pf.hub_sig = sig
                # Event-style change: push the next periodic beat a full window
                # out so a burst of changes can never crowd the edit bucket.
                pf.next_hub = now + HUB_REFRESH_SECONDS
                schedule_hub_refresh(self.manager, rt.channel_id, uid)

        # --- screen: slower periodic beat (busy screens are skipped) --------
        if pf.next_screen <= now:
            pf.next_screen = now + SCREEN_REFRESH_SECONDS
            schedule_screen_refresh(self.manager, rt, uid)

        # --- pair liveness: existence + order, self-healing -----------------
        if pf.next_pair <= now:
            pf.next_pair = now + PAIR_CHECK_SECONDS
            await self._check_pair(rt, uid)
            # Silent repair backstop: verify_pair handles hub/controls side
            # effects, but if the stack is STILL incomplete (e.g. an early
            # return skipped a piece), schedule the repair loop once.
            from discord_ui.session_recovery import (
                _attempt_missing,
                schedule_repair,
            )

            if (
                _attempt_missing(rt, uid) is not None
                and not getattr(self, "_repair_scheduled", set()) & {(rt.channel_id, uid)}
            ):
                self._repair_scheduled = getattr(self, "_repair_scheduled", set())
                self._repair_scheduled.add((rt.channel_id, uid))
                schedule_repair(self.manager, rt, uid, why="pacer backstop")

    async def _check_pair(self, rt, uid: int) -> None:
        bot = getattr(self.manager, "bot_ref", None)
        channel = bot.get_channel(rt.channel_id) if bot is not None else None
        outcome = await verify_pair(self.manager, rt, uid, channel)
        if outcome == "screen_missing":
            # The screen vanished (glitch, purge race...). Default policy:
            # silent repair — the loop re-mints the full stack within its
            # window instead of leaving the player stranded on /joinmap.
            # (A moderator-initiated delete is handled authoritatively by
            # on_raw_message_delete, which ends the session and pops the
            # screen record — schedule_repair then refuses to run.)
            from discord_ui.session_recovery import schedule_repair

            schedule_repair(self.manager, rt, uid, why="pair check: screen gone")
