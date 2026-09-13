"""Silent session repair: the DEFAULT error policy for a player's stack.

Policy (user spec):
- Everything EXCEPT the three terminal cases is repaired SILENTLY: no channel
  log, no session end. The repair loop retries until a COMPLETE session stack
  exists — screen (map image) -> controls (D-pad) -> hub (HUD), in that exact
  top-to-bottom order — for up to SESSION_REPAIR_TIMEOUT_SEC seconds.
- On success the stack is left alone; nothing is posted.
- On timeout: every half-sent message created during repair is deleted, the
  stack ids are cleared, and ONE consolidated error notice is posted (the
  player keeps their row; /joinmap or a button press mints a fresh stack).
- Terminal cases that skip repair entirely and log immediately:
  1. a privileged user destroys a player's screen (destruction, not a glitch),
  2. inactivity timeout (watchdog ends the session),
  3. repair genuinely failing for the whole window (consolidated log).

The loop is serialized per player via the shared player_lock, deduplicates
concurrent schedules (one loop per stack at a time), and every Discord write
rides the manager's edit gate / creation locks so repair never storms a bucket.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import discord

from config import SESSION_REPAIR_RETRY_DELAY_SEC, SESSION_REPAIR_TIMEOUT_SEC
from game.terrain import render_kwargs as terrain_render_kwargs


def _wx_key_of(rt):
    """Effective screen weather key (respects the scenario FX gate)."""
    try:
        from rendering.renderer import effective_weather_key

        return effective_weather_key(rt)
    except Exception:
        return getattr(rt, "weather_key", None)

log = logging.getLogger("GAME")


class RepairFailed(Exception):
    """Raised internally when the repair window expires."""


def _attempt_missing(rt, user_id: int) -> Optional[str]:
    """First missing/broken piece of the stack, or None when complete.

    Pure check (no Discord I/O): completeness requires screen, controls and
    hub ids present on the screen record in a sane order. Message liveness is
    verified by the repair steps themselves (a 404 mid-repair just schedules
    another attempt inside the same window).
    """
    screen = rt.screens.get(user_id)
    if screen is None:
        return "screen"
    if screen.message_id is None:
        return "screen"
    if getattr(screen, "controls_message_id", None) is None:
        return "controls"
    if screen.hub_message_id is None:
        return "hub"
    if screen.hub_message_id <= screen.message_id:
        # Snowflakes are time-ordered: hub must be newer than the screen
        # (stack grows downward). Anything else is a broken order.
        return "order"
    return None


async def _mint_screen(manager, rt, channel, user_id: int,
                       attempts: list) -> bool:
    """Create a fresh screen message for the player (no interaction needed)."""
    from discord_ui.map_view import _screen_file_from_bytes, _screen_bytes
    from discord_ui.map_view import MapView, _focus_screen_camera
    from rendering.renderer import run_image_task
    from persistence.repositories import save_player

    screen = rt.screens.get(user_id)
    if screen is None or manager.renderer is None:
        return False
    _focus_screen_camera(rt, screen, user_id)
    try:
        result = await manager.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=screen.camera,
            full=True, focus_user_id=user_id,
            weather_key=_wx_key_of(rt),
            fx_seed=getattr(rt, "lightning_seed", 0),
            **terrain_render_kwargs(rt),
        )
        screen.composite = result.composite
        data = await run_image_task(_screen_bytes, result)
        view = getattr(screen, "map_view", None) or MapView(rt.channel_id, manager, user_id)
        screen.map_view = view
        # Split layout: the screen message is IMAGE-ONLY. The D-pad is posted
        # as its own message underneath by _ensure_controls — attaching the
        # view here merged screen+D-pad into ONE dead message (reported bug:
        # the merged message floated above and a working stack reloaded under
        # it after the next press).
        msg = await channel.send(file=_screen_file_from_bytes(data, result.filename))
    except Exception as e:  # noqa: BLE001 — one failed attempt is retried
        attempts.append(f"screen send: {e!r}")
        return False
    screen.message_id = msg.id
    player = rt.state.get_player(user_id)
    if player is not None:
        player.screen_message_id = msg.id
        if manager.db is not None:
            try:
                await save_player(manager.db, rt.channel_id, player)
            except Exception as e:  # noqa: BLE001 — best-effort persist
                attempts.append(f"screen persist: {e!r}")
    if getattr(manager, "bot_ref", None) is not None:
        manager.bot_ref.add_view(view)
    return True


async def _ensure_controls(manager, rt, channel, user_id: int,
                           attempts: list) -> bool:
    """Create the D-pad message under the screen (create_controls_message)."""
    from discord_ui.map_view import create_controls_message

    ok = await create_controls_message(
        manager, rt, rt.channel_id, channel, user_id
    )
    if not ok:
        attempts.append("controls create failed")
    return ok


async def _ensure_hub(manager, rt, channel, user_id: int,
                      attempts: list) -> bool:
    """Create/refresh the hub under the controls (per-player, locked)."""
    from discord_ui.hub_view import create_hub_message

    ok = await create_hub_message(
        manager, rt, rt.channel_id, channel, user_id, user_id
    )
    if not ok:
        attempts.append("hub create failed")
    return ok


async def repair_session(manager, rt, user_id: int, channel=None,
                         reason: str = "glitch",
                         timeout: float = SESSION_REPAIR_TIMEOUT_SEC,
                         post_notice=None) -> bool:
    """Silently repair one player's stack to a COMPLETE session.

    Returns True when a full screen -> controls -> hub stack exists (or was
    already complete — a no-op). Returns False only after the whole timeout
    window is spent, in which case half-sent messages are cleaned up and
    ``post_notice`` (when given) is awaited once with a consolidated summary.

    Concurrent schedules for the same player coalesce: if a repair loop is
    already running, the caller just waits for it to finish and re-checks.
    """
    from discord_ui.locks import player_lock

    if channel is None:
        bot = getattr(manager, "bot_ref", None)
        channel = bot.get_channel(rt.channel_id) if bot is not None else None
    if channel is None:
        return False

    lock = player_lock(rt.channel_id, user_id)
    if lock.locked():
        # Another repair/creation cycle is already running for this player:
        # let it finish, then report whether IT achieved a complete stack.
        async with lock:
            return _attempt_missing(rt, user_id) is None

    async with lock:
        deadline = time.monotonic() + timeout
        attempts: list = []
        round_no = 0
        while True:
            round_no += 1
            missing = _attempt_missing(rt, user_id)
            if missing is None:
                if round_no > 1:
                    log.info(
                        "[REPAIR] complete stack for user %s in %s after %d round(s): %s",
                        user_id, rt.channel_id, round_no, "; ".join(attempts[-4:]) or "-",
                    )
                return True
            attempts.append(f"r{round_no} missing={missing}")
            try:
                if missing == "screen":
                    await _mint_screen(manager, rt, channel, user_id, attempts)
                elif missing == "controls":
                    await _ensure_controls(manager, rt, channel, user_id, attempts)
                elif missing == "hub":
                    await _ensure_hub(manager, rt, channel, user_id, attempts)
                elif missing == "order":
                    # Hub above (or equal to) the screen: drop the hub; the
                    # next round re-sends it underneath.
                    from discord_ui.refresh import drop_hub_message

                    adapter = getattr(manager, "session_adapter", None)
                    suppress = (
                        getattr(adapter, "suppress_ids", None)
                        if adapter is not None else None
                    )
                    await drop_hub_message(rt, user_id, channel, suppress_ids=suppress)
            except Exception as e:  # noqa: BLE001 — keep the window open
                attempts.append(f"r{round_no} step error: {e!r}")

            if _attempt_missing(rt, user_id) is None:
                log.info(
                    "[REPAIR] complete stack for user %s in %s after %d round(s): %s",
                    user_id, rt.channel_id, round_no, "; ".join(attempts[-4:]) or "-",
                )
                return True
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(SESSION_REPAIR_RETRY_DELAY_SEC)

        # ---- window expired: one consolidated cleanup + notice ----
        detail = "; ".join(attempts[-8:])
        log.warning(
            "[REPAIR] FAILED for user %s in %s after %.0fs: %s",
            user_id, rt.channel_id, timeout, detail,
        )
        cleaned = await cleanup_half_stack(manager, rt, user_id, channel)
        if post_notice is not None:
            await post_notice(detail, cleaned)
        return False


async def cleanup_half_stack(manager, rt, user_id: int, channel) -> int:
    """Delete every half-sent message of a failed repair and clear the ids.

    Screen, controls and hub are deleted (suppressed so the deleted-message
    hook does not re-trigger repair), ids cleared on the screen record, the
    Player row and the DB. The player object stays — /joinmap or a button
    press mints a fresh stack instantly. Returns the number of deletes sent.
    """
    from persistence.repositories import save_player

    adapter = getattr(manager, "session_adapter", None)
    suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None
    screen = rt.screens.get(user_id)
    deleted = 0
    ids = []
    if screen is not None:
        ids = [
            mid for mid in (
                screen.hub_message_id,
                getattr(screen, "controls_message_id", None),
                screen.message_id,
            ) if mid
        ]
        if suppress is not None:
            suppress.update(ids)
        for mid in ids:
            try:
                await channel.get_partial_message(mid).delete()
                deleted += 1
            except (discord.NotFound, discord.HTTPException):
                pass
        screen.hub_message_id = None
        screen.controls_message_id = None
        screen.message_id = None
    player = rt.state.get_player(user_id)
    if player is not None:
        player.hub_message_id = None
        player.controls_message_id = None
        player.screen_message_id = None
        if manager.db is not None:
            try:
                await save_player(manager.db, rt.channel_id, player)
            except Exception as e:  # noqa: BLE001 — best-effort persist
                log.warning("[REPAIR] persist cleared ids failed: %s", e)
    return deleted


async def _post_consolidated_notice(manager, rt, user_id: int,
                                    detail: str, cleaned: int) -> None:
    """ONE consolidated error embed after a failed repair window.

    Says exactly what was attempted, how many half-sent messages were
    cleaned up, and how to get back in (position/stats survive)."""
    import discord

    bot = getattr(manager, "bot_ref", None)
    channel = bot.get_channel(rt.channel_id) if bot is not None else None
    if channel is None:
        return
    player = rt.state.get_player(user_id)
    name = player.display_name if player is not None else f"người chơi {user_id}"
    embed = discord.Embed(
        title="Không phục hồi được phiên",
        description=(
            f"**{name}** <@{user_id}>\n"
            f"Bot đã thử tự sửa trong 15s nhưng chưa tạo được phiên hoàn chỉnh "
            f"(màn hình → D-pad → hub).\n"
            f"- Đã dọn {cleaned} tin nhắn dở dang.\n"
            f"- Chi tiết: {detail}\n"
            f"Vị trí và chỉ số của bạn vẫn còn — bấm bất kỳ nút game còn hoạt "
            f"động hoặc /joinmap để vào lại ngay."
        ),
        color=discord.Color.red(),
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as e:
        log.warning("[REPAIR] consolidated notice send failed: %s", e)


def schedule_repair(manager, rt, user_id: int, why: str = "glitch") -> None:
    """Fire-and-forget silent repair (safe to call from any error path).

    On window expiry the half-sent messages are cleaned and ONE consolidated
    notice is posted (spec case 1)."""
    from discord_ui.locks import player_lock

    if rt.screens.get(user_id) is None:
        # No screen record at all: nothing to repair silently — this is a
        # /joinmap situation, not a glitch. Log-only, no channel notice.
        log.info("[REPAIR] no screen record for user %s in %s (%s); skip",
                 user_id, rt.channel_id, why)
        return
    if _attempt_missing(rt, user_id) is None:
        return  # already complete
    if player_lock(rt.channel_id, user_id).locked():
        return  # a repair loop is already working on this stack

    async def _run():
        await repair_session(
            manager, rt, user_id, reason=why,
            post_notice=lambda detail, cleaned: _post_consolidated_notice(
                manager, rt, user_id, detail, cleaned
            ),
        )

    asyncio.create_task(_run())
