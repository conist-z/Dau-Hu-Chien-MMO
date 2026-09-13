"""Session-end adapter: turns a game-layer session event into Discord actions.

This is the ONLY place that knows both sides (game/session.py reasons and
Discord messages). GameManager stays discord.py-free; bot.py injects one
instance as ``manager.session_adapter``.

``end()``    — tear a session down (delete screen/hub/panel messages, stop
               auto-move, remove the player) and post the reason notice.
``notify()`` — post the reason notice only (session stays open, e.g. a
               transient error that self-heals, or a deleted-message report
               while a replacement screen is being minted).
"""
from __future__ import annotations

import logging
from typing import Optional

import discord

log = logging.getLogger("GAME")

# reason key -> (emoji, Vietnamese human text). Keep keys in sync with
# game/session.py — they also appear in logs.
REASON_TEXT = {
    "inactivity": (
        "⏰",
        "bạn không thao tác trong **{timeout} phút** nên phiên đã tự tắt để tiết kiệm tài nguyên",
    ),
    "leave": ("🚪", "bạn đã chủ động rời map"),
    "reset": ("♻️", "map trong kênh đã được **reset**"),
    "message_deleted": ("🗑️", "tin nhắn màn hình/hub của phiên đã **bị xoá**"),
    "error": ("⚠️", "phiên gặp **lỗi không xác định**"),
}


def _fmt_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds <= 0:
        return ""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f" (đã chơi {h}g{m:02d}p)"
    if m:
        return f" (đã chơi {m}p{s:02d}s)"
    return f" (đã chơi {s}s)"


class SessionEndAdapter:
    """Implements the adapter contract expected by GameManager."""

    def __init__(self, bot: discord.Client, manager):
        self.bot = bot
        self.manager = manager
        # Message ids the bot itself is deleting right now. bot.py's
        # on_raw_message_delete consumes (and clears) them so a teardown's
        # own deletes are never misread as "someone deleted the screen".
        self.suppress_ids: set = set()

    # ----- helpers -----

    def _resolve_names(self, rt, user_id: int) -> tuple:
        """(display_name, mention) for a session owner."""
        player = rt.state.get_player(user_id) if rt is not None else None
        if player is not None and player.display_name:
            return player.display_name, f"<@{user_id}>"
        member = rt.members.get(user_id) if rt is not None else None
        if member is not None and getattr(member, "display_name", ""):
            return member.display_name, f"<@{user_id}>"
        return f"người chơi {user_id}", f"<@{user_id}>"

    async def _delete_message(self, channel, message_id: Optional[int]) -> None:
        if channel is None or message_id is None:
            return
        # Any delete we perform ourselves must not trip the deleted-message
        # session hook in bot.py (it would loop: notice -> delete -> notice).
        self.suppress_ids.add(message_id)
        if len(self.suppress_ids) > 256:
            # Bound the set; stale ids only cost a harmless extra lookup.
            self.suppress_ids.clear()
            self.suppress_ids.add(message_id)
        try:
            await channel.get_partial_message(message_id).delete()
        except (discord.NotFound, discord.Forbidden):
            pass
        except discord.HTTPException as e:
            log.warning("[SESSION] delete message %s failed: %s", message_id, e)

    def _stop_auto(self, screen) -> None:
        task = screen.auto_task
        screen.auto_running = False
        screen.auto_armed = False
        screen.auto_task = None
        if task is not None and not task.done():
            task.cancel()

    # ----- notices -----

    def _notice_text(self, reason: str, detail: str, ended: bool,
                     timeout_minutes: float) -> str:
        emoji, base = REASON_TEXT.get(reason, ("⚠️", reason))
        if reason == "inactivity":
            base = base.format(timeout=max(1, round(timeout_minutes)))
        if detail:
            base = f"{base} — {detail}"
        action = "đã DỪNG hoạt động" if ended else "VẪN ĐANG hoạt động (tự phục hồi)"
        return f"{emoji} {action}: {base}."

    async def _post_notice(self, channel_id: int, user_id: int, reason: str,
                           detail: str, ended: bool,
                           duration: Optional[float]) -> None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            log.warning(
                "[SESSION] no channel %s for notice (user %s, reason %s)",
                channel_id, user_id, reason,
            )
            return
        rt = self.manager.get_runtime(channel_id)
        name, mention = self._resolve_names(rt, user_id)
        text = self._notice_text(
            reason, detail, ended,
            getattr(self.manager, "session_timeout_minutes", 30.0),
        )
        dur = _fmt_duration(duration)
        embed = discord.Embed(
            title="Kết thúc phiên chơi",
            description=f"**{name}** {mention}\n{text}{dur}",
            color=discord.Color.red() if ended else discord.Color.orange(),
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            log.warning("[SESSION] notice send failed in %s: %s", channel_id, e)

    # ----- adapter contract -----

    async def end(self, channel_id: int, user_id: int, reason: str,
                  detail: str = "", duration: Optional[float] = None) -> None:
        """Tear one player's session down and post the reason notice.

        ONLY a voluntary /leave-map (``leave``) and an admin /mapreset
        (``reset``) delete the players row. Every other reason — including
        the inactivity watchdog — keeps it (position/stats/sprite survive) so
        the player rejoins with /joinmap or any button press EXACTLY where
        they left off (reported bug: returning hours later restored the
        default spawn + face token instead of the saved position/avatar)."""
        remove_row = reason in ("leave", "reset")
        rt = self.manager.get_runtime_for(channel_id, user_id)
        screen = rt.screens.get(user_id) if rt is not None else None
        # Capture the ids BEFORE mutation so the notice can cite them.
        sid = screen.message_id if screen else None
        hid = screen.hub_message_id if screen else None
        iid = screen.inventory_message_id if screen else None
        try:
            await self._teardown(rt, screen, user_id, channel_id, remove_row)
        except Exception as e:  # noqa: BLE001 — notice must still go out
            log.warning(
                "[SESSION] teardown failed for %s in %s (reason=%s): %s",
                user_id, channel_id, reason, e,
            )
        await self._post_notice(channel_id, user_id, reason, detail, True, duration)
        log.info(
            "[SESSION] ended user %s in channel %s reason=%s detail=%s "
            "(screen=%s hub=%s inv=%s)",
            user_id, channel_id, reason, detail, sid, hid, iid,
        )

    async def _teardown(self, rt, screen, user_id: int, channel_id: int,
                        remove_row: bool = True) -> None:
        """Best-effort removal of one session's messages + state + DB row."""
        channel = self.bot.get_channel(channel_id)
        if rt is None:
            return
        if screen is not None:
            self._stop_auto(screen)
            await self._delete_message(channel, screen.inventory_message_id)
            await self._delete_message(channel, screen.hub_message_id)
            # The controls (D-pad) message belongs to the same session stack:
            # leave none of it behind when the session ends.
            await self._delete_message(channel, screen.controls_message_id)
            await self._delete_message(channel, screen.message_id)
            rt.screens.pop(user_id, None)
        if remove_row:
            rt.members.pop(user_id, None)
            rt.state.remove_player(user_id)
        # For message_deleted the player STAYS on the map (position/stats
        # intact) so a quick /joinmap or button press restores the screen.
        if rt.message_id is not None and (
            screen is None or rt.message_id == screen.message_id
        ):
            rt.message_id = next(
                (s.message_id for s in rt.screens.values() if s.message_id), None
            )
        if self.manager.db is not None:
            if remove_row:
                from persistence.repositories import delete_player

                await delete_player(self.manager.db, channel_id, user_id)
            else:
                # Keep the row (position/stats) but forget the dead message
                # ids so /joinmap mints a fresh screen+hub cleanly.
                # NOTE: import BEFORE branching — an import inside one branch
                # makes the name function-local for the WHOLE function, so the
                # other branch hits UnboundLocalError
                # ("cannot access local variable 'save_player'") and the whole
                # teardown crashed on every message_deleted with a live player.
                from persistence.repositories import load_players, save_player

                player = rt.state.get_player(user_id)
                if player is None:
                    # Not in memory (e.g. cleared earlier): rebuild the saved
                    # position from the DB row, then clear the message ids.
                    rows = await load_players(self.manager.db, channel_id)
                    row = next(
                        (r for r in rows if r["user_id"] == user_id), None
                    )
                    if row is not None:
                        from game.state import Player as _P

                        player = _P(
                            user_id=user_id, display_name=row.get("display_name", "")
                        )
                        player.x, player.y = row["x"], row["y"]
                if player is not None:
                    player.screen_message_id = None
                    player.hub_message_id = None
                    if getattr(player, "controls_message_id", "keep") != "keep":
                        player.controls_message_id = None
                    await save_player(self.manager.db, channel_id, player)
        self.manager.discard_session(channel_id, user_id)

    async def notify(self, channel_id: int, user_id: int, reason: str,
                     detail: str = "", *, ended: bool = True,
                     duration: Optional[float] = None) -> None:
        """Post the reason notice WITHOUT touching the session."""
        await self._post_notice(channel_id, user_id, reason, detail, ended, duration)
