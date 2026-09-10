import io
import logging
from typing import Optional

import discord

from discord_ui.ephemeral import EPHEMERAL_OK, EPHEMERAL_WARN, send_ephemeral_followup
from discord import app_commands
from discord.ext import commands

from discord_ui.map_view import MapView, create_controls_message
from discord_ui.hub_view import create_hub_message
from game.terrain import render_kwargs as terrain_render_kwargs
from game.state import Direction
from rendering.hub_renderer import WEATHER
from rendering.renderer import run_image_task

log = logging.getLogger("GAME")


def _wx_key_of(rt):
    """Effective screen weather key (respects the scenario FX gate)."""
    try:
        from rendering.renderer import effective_weather_key

        return effective_weather_key(rt)
    except Exception:
        return getattr(rt, "weather_key", None)


async def _file(result) -> discord.File:
    """Encode a rendered screen off the event loop."""
    from rendering.renderer import encode_upload_gif, finalize_for_upload

    def _encode() -> bytes:
        frames = getattr(result, "frames", None)
        if frames and len(frames) > 1:
            return encode_upload_gif(
                frames, getattr(result, "duration_ms", 160), optimize=False
            )
        upload = finalize_for_upload(result.image)
        buf = io.BytesIO()
        # optimize=False: tiny palette PNG, optimize pass not worth the CPU.
        upload.save(buf, "PNG", optimize=False)
        return buf.getvalue()

    data = await run_image_task(_encode)
    buf = io.BytesIO(data)
    buf.seek(0)
    return discord.File(buf, filename=result.filename)


# Value signalling "resume real national weather" in /setweather.
WEATHER_AUTO = "__auto__"

_WX_CHOICES = [app_commands.Choice(name=n, value=n) for n in WEATHER.values()] + [
    app_commands.Choice(name="Tự Động (thời tiết thật)", value=WEATHER_AUTO),
]


def _is_admin(interaction: discord.Interaction) -> bool:
    """Admin test gate: server admins (or Manage Server) only."""
    perms = getattr(interaction.user, "guild_permissions", None)
    if perms is None:
        return False
    return bool(perms.administrator or perms.manage_guild)


class MapCog(commands.Cog):
    def __init__(self, bot: commands.Bot, manager, renderer, db):
        self.bot = bot
        self.manager = manager
        self.renderer = renderer
        self.db = db

    async def _create_player_hub(self, interaction: discord.Interaction, rt, uid: int) -> bool:
        """Create the player's personal hub message under their screen."""
        return await create_hub_message(
            self.manager, rt, interaction.channel.id, interaction.channel, uid
        )

    def _focus_screen_camera(self, rt, screen, user_id: int) -> None:
        """Centre the screen's own camera on its owner."""
        cam = screen.camera
        if cam is None or not cam.follow:
            return
        p = rt.state.get_player(user_id)
        if p is not None:
            cam.center_on(p.x, p.y, rt.map_data.width, rt.map_data.height)
        else:
            cam.center_on(
                rt.map_data.spawn[0], rt.map_data.spawn[1], rt.map_data.width, rt.map_data.height
            )

    async def _spawn_screen(self, interaction: discord.Interaction, rt) -> discord.Message:
        """Create the caller's personal screen and reply with it.

        The command response IS the screen message (image + per-player D-pad),
        so no extra round trip is needed. Persists the screen id on the player.
        """
        uid = interaction.user.id
        self.manager.touch_session(interaction.channel.id, uid)
        # ACK FIRST: the render below (PIL compose + upload) regularly exceeds
        # the 3s interaction-response deadline -> 10062 Unknown interaction.
        # defer(thinking=True) buys a 15-minute window; the first followup
        # send REPLACES the thinking message, so the screen message id is
        # still interaction.original_response().
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
        rt.members[uid] = interaction.user
        if uid not in rt.state.players:
            rt.state.add_player(uid, interaction.user.display_name, *rt.map_data.spawn)
            await self.renderer.avatar_cache.get_avatar(
                interaction.user, label=interaction.user.display_name[:1]
            )
        screen = self.manager.ensure_screen(rt, uid)
        self._focus_screen_camera(rt, screen, uid)
        result = await self.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=screen.camera,
            full=True, focus_user_id=uid,
            weather_key=_wx_key_of(rt),
            fx_seed=getattr(rt, "lightning_seed", 0),
            **terrain_render_kwargs(rt),
        )
        screen.composite = result.composite
        view = MapView(rt.channel_id, self.manager, uid)
        screen.map_view = view
        # Split layout: the command response is the IMAGE-ONLY map message.
        # D-pad + hub are posted underneath by the helpers below.
        await interaction.followup.send(file=await _file(result))
        msg = await interaction.original_response()
        screen.message_id = msg.id
        player = rt.state.get_player(uid)
        if player is not None:
            player.screen_message_id = msg.id
            from persistence.repositories import save_player

            await save_player(self.db, rt.channel_id, player)
        await create_controls_message(
            self.manager, rt, rt.channel_id, interaction.channel, uid, view=view
        )
        self.bot.add_view(view)
        await self._create_player_hub(interaction, rt, uid)
        return msg

    async def _delete_screen(self, interaction: discord.Interaction, rt, screen) -> None:
        """Delete a player's screen + controls + hub messages and detach them."""
        uid = screen.user_id
        # Delete hub, controls, then screen. Register the ids with the session
        # adapter's suppress set so bot.py's deleted-message hook does not
        # mistake OUR teardown for "someone deleted the screen".
        adapter = getattr(self.manager, "session_adapter", None)
        suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None
        if suppress is not None:
            if screen.hub_message_id is not None:
                suppress.add(screen.hub_message_id)
            if screen.controls_message_id is not None:
                suppress.add(screen.controls_message_id)
            if screen.message_id is not None:
                suppress.add(screen.message_id)
        # Delete the personal hub + controls first, then the screen.
        hid = screen.hub_message_id
        if hid is not None:
            try:
                await interaction.channel.get_partial_message(hid).delete()
            except (discord.NotFound, discord.HTTPException):
                pass
        cid_msg = screen.controls_message_id
        if cid_msg is not None:
            try:
                await interaction.channel.get_partial_message(cid_msg).delete()
            except (discord.NotFound, discord.HTTPException):
                pass
            screen.controls_message_id = None
            player = rt.state.get_player(uid) if uid is not None else None
            if player is not None:
                player.controls_message_id = None
        if screen.message_id is not None:
            try:
                await interaction.channel.get_partial_message(screen.message_id).delete()
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[SCREEN] delete failed: %s", e)
        player = rt.state.get_player(uid) if uid is not None else None
        if player is not None:
            player.hub_message_id = None
            player.screen_message_id = None
        rt.screens.pop(uid, None)
        self.manager.discard_session(rt.channel_id, uid)
        if rt.hub_message_id is None and player is not None and self.db is not None:
            from persistence.repositories import save_player

            await save_player(self.db, rt.channel_id, player)

    def _schedule_hub(self, interaction: discord.Interaction, uid: int) -> None:
        coalescer = getattr(self.manager, "hub_coalescer", None)
        if coalescer is not None:
            coalescer.schedule(
                (interaction.channel.id, uid),
                {"interaction": interaction, "focused_user_id": uid},
            )

    def _schedule_screen(self, channel_id: int, uid: int) -> None:
        """Queue a background screen re-render (no interaction token lane).

        Used when weather changes: every online player's map gets the new FX
        without anyone pressing a button. ``flush_render_batch`` falls back to
        the spaced edit gate when the batch carries no interaction."""
        coalescer = getattr(self.manager, "coalescer", None)
        if coalescer is not None:
            coalescer.schedule((channel_id, uid), {"user_id": uid})

    async def _screen_alive(self, channel, message_id) -> Optional[bool]:
        """True if the message still exists; False if deleted (404); None on a
        transient (non-429) fetch error.

        Used by /joinmap and /startmap so a screen auto-deleted by Discord
        doesn't block re-creation while a stale non-None id is still held.
        """
        if channel is None or message_id is None:
            return None
        try:
            await channel.fetch_message(message_id)
            return True
        except discord.NotFound:
            return False
        except discord.HTTPException as e:
            log.warning("[SCREEN] could not verify message %s: %s", message_id, e)
            return None

    async def _allow_spawn_screen(self, interaction: discord.Interaction, rt, uid: int) -> bool:
        """Return True if a (fresh) screen may be spawned now.

        - No screen / stale id already cleared -> True (proceed to spawn).
        - Live screen exists -> reply "already have", return False.
        - Screen message is gone (auto-delete/404) -> clear the stale screen
          id (and the hub id if that hub is also gone) so /joinmap re-creates a
          fresh pair instead of leaving the player stuck. Return True.
        - Transient check error -> reply with guidance, return False (never
          risk a duplicate screen on an uncertain signal).
        """
        screen = rt.screens.get(uid)
        if screen is None or screen.message_id is None:
            return True
        alive = await self._screen_alive(interaction.channel, screen.message_id)
        if alive is None:
            await interaction.response.send_message(
                "Không kiểm tra được màn hình của bạn (lỗi mạng). Dùng /map để làm mới.",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return False
        if alive:
            await interaction.response.send_message(
                "Bạn đã có màn hình riêng ở kênh này (bấm ⚔️ trên đó để tấn công).",
                ephemeral=True,
            )
            return False
        # Screen gone: clear stale ids so a fresh pair is minted below.
        log.info("[SCREEN] stale screen %s for user %s; re-creating on demand", screen.message_id, uid)
        screen.message_id = None
        player = rt.state.get_player(uid)
        if player is not None:
            player.screen_message_id = None
        if (
            screen.hub_message_id is not None
            and await self._screen_alive(interaction.channel, screen.hub_message_id) is False
        ):
            screen.hub_message_id = None
            if player is not None:
                player.hub_message_id = None
        if player is not None and self.db is not None:
            from persistence.repositories import save_player

            await save_player(self.db, rt.channel_id, player)
        return True

    @app_commands.command(name="startmap", description="Tạo map game mới cho kênh này")
    @app_commands.describe(map_id="ID map (tên file trong assets/maps, mặc định bigmap)")
    async def startmap(self, interaction: discord.Interaction, map_id: str = "bigmap"):
        cid = interaction.channel.id
        uid = interaction.user.id
        rt = self.manager.get_runtime(cid)
        if rt is not None and rt.map_data.map_id != map_id:
            # Different map: wipe the whole scenario (all personal screens too).
            for screen in list(rt.screens.values()):
                await self._delete_screen(interaction, rt, screen)
                await self.manager.notify_session_event(
                    rt.channel_id, screen.user_id, "reset",
                    detail=f"kênh đổi sang map {map_id} — phiên map {rt.map_data.map_id} bị kết thúc",
                )
            self.manager.remove_runtime(cid)
            if self.db is not None:
                await self.db.execute("DELETE FROM players WHERE channel_id=?", (cid,))
                await self.db.execute("DELETE FROM scenarios WHERE channel_id=?", (cid,))
                await self.db.execute("DELETE FROM blocks WHERE channel_id=?", (cid,))
            rt = None
        if rt is None:
            rt = self.manager.create_runtime(cid, map_id)
            await self.manager.load_inventories(rt)
            await self.manager.load_blocks(rt)
            await self.manager.load_terrain(rt)
            await self.manager.load_hotbars(rt)
        from discord_ui.locks import player_lock

        async with player_lock(cid, uid):
            if not await self._allow_spawn_screen(interaction, rt, uid):
                return
            msg = await self._spawn_screen(interaction, rt)
        if rt.message_id is None:
            rt.message_id = msg.id  # primary screen (adjacency ref)
        from persistence.repositories import save_scenario

        await save_scenario(self.db, cid, rt.message_id, rt.map_data.map_id, rt.hub_message_id)

    @app_commands.command(name="bigmap", description="Tạo map bigmap (Tiled) cho kênh này")
    async def bigmap(self, interaction: discord.Interaction):
        cid = interaction.channel.id
        rt = self.manager.get_runtime(cid)
        if rt is not None:
            self.manager.remove_runtime(cid)
            if self.db is not None:
                await self.db.execute("DELETE FROM players WHERE channel_id=?", (cid,))
                await self.db.execute("DELETE FROM scenarios WHERE channel_id=?", (cid,))
                await self.db.execute("DELETE FROM blocks WHERE channel_id=?", (cid,))
        await self.startmap(interaction, map_id="bigmap")

    @app_commands.command(name="joinmap", description="Tạo màn hình chơi riêng của bạn trong kênh này")
    async def joinmap(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map. Dùng /startmap trước.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        uid = interaction.user.id
        # Serialize check+spawn per player: a concurrent silent-repair loop
        # (or a double /joinmap) would otherwise BOTH mint a screen — the
        # reported "dead screen reply + duplicate live screen" bug. The lock
        # is reentrant per task, so the nested hub/controls creation inside
        # _spawn_screen cannot deadlock.
        from discord_ui.locks import player_lock

        async with player_lock(interaction.channel.id, uid):
            if not await self._allow_spawn_screen(interaction, rt, uid):
                return
            msg = await self._spawn_screen(interaction, rt)
        if rt.message_id is None:
            rt.message_id = msg.id  # primary screen (adjacency ref)
        from persistence.repositories import save_scenario

        await save_scenario(
            self.db, interaction.channel.id, rt.message_id, rt.map_data.map_id, rt.hub_message_id
        )

    @app_commands.command(name="leave-map", description="Rời khỏi map (xoá màn hình của bạn)")
    async def leave_map(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is not None:
            await self._delete_screen(interaction, rt, screen)
        rt.state.remove_player(uid)
        rt.members.pop(uid, None)
        from persistence.repositories import delete_player

        await delete_player(self.db, interaction.channel.id, uid)
        self.manager.discard_session(interaction.channel.id, uid)
        await self.manager.notify_session_event(
            interaction.channel.id, uid, "leave",
            detail="bạn đã rời map bằng lệnh /leave-map",
        )
        # If the primary screen left, re-point the scenario at another screen.
        if rt.message_id is not None and (screen is None or rt.message_id == screen.message_id):
            rt.message_id = next((s.message_id for s in rt.screens.values() if s.message_id), None)
            if rt.message_id is not None:
                from persistence.repositories import save_scenario

                await save_scenario(self.db, rt.channel_id, rt.message_id, rt.map_data.map_id, rt.hub_message_id)
        await interaction.response.send_message(
            "Đã rời map.", ephemeral=True, delete_after=EPHEMERAL_OK
        )
        for remaining_uid in list(rt.screens.keys()):
            self._schedule_hub(interaction, remaining_uid)

    @app_commands.command(name="map", description="Làm mới ảnh màn hình riêng của bạn")
    async def map_refresh(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is None or screen.message_id is None:
            await interaction.response.send_message(
                "Bạn chưa có màn hình riêng. Dùng /joinmap.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        self._focus_screen_camera(rt, screen, uid)
        result = await self.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=screen.camera,
            full=True, focus_user_id=uid,
            weather_key=_wx_key_of(rt),
            fx_seed=getattr(rt, "lightning_seed", 0),
            **terrain_render_kwargs(rt),
        )
        screen.composite = result.composite
        await interaction.response.defer()
        try:
            await self.manager.edit_gate.edit_message(
                interaction.channel, screen.message_id,
                attachments=[await _file(result)], view=MapView(rt.channel_id, self.manager, uid),
            )
            await send_ephemeral_followup(
                interaction, "Đã làm mới màn hình của bạn.", EPHEMERAL_OK
            )
        except discord.NotFound:
            # Screen message was deleted mid-session (or gone since last boot):
            # recreate it via the interaction followup so /map is self-healing,
            # mirroring the button press / auto-move paths.
            log.info("[SCREEN] /map refresh hit a deleted screen (%s); recreating", screen.message_id)
            try:
                # Split layout: the screen message is IMAGE-ONLY. Attaching
                # the D-pad view here merged screen + D-pad into ONE dead
                # message (the reported merged-message bug); the D-pad stays
                # on its own controls message (re-created below).
                msg = await interaction.followup.send(file=await _file(result))
                screen.message_id = msg.id
                player = rt.state.get_player(uid)
                if player is not None:
                    player.screen_message_id = msg.id
                    from persistence.repositories import save_player

                    await save_player(self.db, rt.channel_id, player)
                await create_controls_message(
                    self.manager, rt, rt.channel_id, interaction.channel, uid,
                    view=getattr(screen, "map_view", None),
                )
                await send_ephemeral_followup(
                    interaction, "Đã tạo lại màn hình của bạn.", EPHEMERAL_OK
                )
            except (discord.NotFound, discord.HTTPException) as e2:
                log.warning("[SCREEN] /map recreate failed: %s", e2)
                await send_ephemeral_followup(
                    interaction, "Không thể tạo lại màn hình (dùng /joinmap).",
                    EPHEMERAL_WARN,
                )
        except discord.HTTPException as e:
            log.warning("[SCREEN] /map refresh edit failed: %s", e)
            await send_ephemeral_followup(
                interaction,
                "Không làm mới được màn hình (có thể đã bị xoá — dùng /joinmap).",
                EPHEMERAL_WARN,
            )
        self._schedule_hub(interaction, uid)

    # ----- /khutraodoi: trade lobby travel -----

    async def _travel_render(
        self, interaction: discord.Interaction, rt, screen, uid: int
    ) -> None:
        """Re-render the player's existing screen message for their NEW world
        (keep the same Discord messages — rule 24/25) and refresh the hub."""
        from discord_ui.map_view import _focus_screen_camera

        _focus_screen_camera(rt, screen, uid)
        result = await self.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=screen.camera,
            full=True, focus_user_id=uid,
            weather_key=_wx_key_of(rt),
            fx_seed=getattr(rt, "lightning_seed", 0),
            **terrain_render_kwargs(rt),
        )
        screen.composite = result.composite
        await interaction.response.defer()
        try:
            await self.manager.edit_gate.edit_message(
                interaction.channel, screen.message_id,
                attachments=[await _file(result)],
                view=MapView(rt.channel_id, self.manager, uid),
            )
        except discord.NotFound:
            # Screen died mid-travel: mint a replacement via the followup.
            try:
                msg = await interaction.followup.send(file=await _file(result))
                screen.message_id = msg.id
                player = rt.state.get_player(uid)
                if player is not None:
                    player.screen_message_id = msg.id
                    from persistence.repositories import save_player

                    await save_player(self.db, rt.channel_id, player)
                await create_controls_message(
                    self.manager, rt, rt.channel_id, interaction.channel, uid,
                    view=getattr(screen, "map_view", None),
                )
            except (discord.NotFound, discord.HTTPException) as e2:
                log.warning("[TRAVEL] screen re-mint failed: %s", e2)
        hub = getattr(self.manager, "hub_coalescer", None)
        if hub is not None:
            hub.schedule((rt.channel_id, uid), {"focused_user_id": uid})

    @app_commands.command(
        name="khutraodoi", description="Vào khu chợ trade (lobby ngoài big map)"
    )
    @app_commands.describe(action="in = vào chợ, out = quay về chỗ cũ")
    @app_commands.choices(action=[
        app_commands.Choice(name="in — vào chợ trade", value="in"),
        app_commands.Choice(name="out — quay về chỗ cũ", value="out"),
    ])
    async def khutraodoi(self, interaction: discord.Interaction, action: str = "in"):
        cid = interaction.channel.id
        uid = interaction.user.id
        main_rt = self.manager.get_runtime(cid)
        if main_rt is None:
            await interaction.response.send_message(
                "Chưa có map. Dùng /startmap trước.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        current = self.manager.runtime_of(cid, uid)
        screen = current.screens.get(uid) if current is not None else None
        if screen is None or screen.message_id is None:
            await interaction.response.send_message(
                "Bạn chưa có màn hình. Dùng /joinmap.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        from discord_ui.locks import player_lock

        async with player_lock(cid, uid):
            if action == "in":
                if current.map_data.map_id != main_rt.map_data.map_id:
                    await interaction.response.send_message(
                        "Bạn đang ở trong chợ rồi (dùng `/khutraodoi out` để ra).",
                        ephemeral=True, delete_after=EPHEMERAL_WARN,
                    )
                    return
                await self._travel_in(interaction, main_rt, current, screen, uid)
            else:
                await self._travel_out(interaction, main_rt, current, screen, uid)

    async def _travel_in(
        self, interaction, main_rt, cur_rt, screen, uid: int
    ) -> None:
        """/khutraodoi in: bigmap -> lobbytrade, spawn on the noted tile."""
        from game.travel import (
            free_arrival_tile,
            move_player_between_runtimes,
            resolve_spawn_tiles,
        )
        from persistence.repositories import save_travel_return

        player = cur_rt.state.get_player(uid)
        if player is None:
            await interaction.response.send_message(
                "Bạn chưa tham gia map.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        # Remember the bigmap spot (survives restarts — rule 14).
        if self.db is not None:
            await save_travel_return(
                self.db, cur_rt.channel_id, uid,
                cur_rt.map_data.map_id, player.x, player.y, player.direction,
            )
        lobby_rt = self.manager.get_or_create_side_runtime(main_rt, "lobbytrade")
        occupied = {(p.x, p.y) for p in lobby_rt.state.get_visible_players()}
        tile = free_arrival_tile(
            lobby_rt, resolve_spawn_tiles(lobby_rt, self.manager.portals, "lobbytrade"),
            occupied,
        )
        move_player_between_runtimes(cur_rt, lobby_rt, uid, tile)
        self.manager.touch_session(cur_rt.channel_id, uid)
        await self._travel_render(interaction, lobby_rt, screen, uid)

    async def _travel_out(
        self, interaction, main_rt, cur_rt, screen, uid: int
    ) -> None:
        """/khutraodoi out: back to the saved bigmap spot."""
        from game.travel import move_player_between_runtimes
        from persistence.repositories import delete_travel_return, load_travel_return

        saved = None
        if self.db is not None:
            saved = await load_travel_return(self.db, cur_rt.channel_id, uid)
        if cur_rt.map_data.map_id == main_rt.map_data.map_id and saved is None:
            await interaction.response.send_message(
                "Bạn không ở trong chợ.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        target_rt = main_rt
        tile = (main_rt.map_data.spawn[0], main_rt.map_data.spawn[1])
        direction = "SOUTH"
        if saved is not None:
            if saved[0] != main_rt.map_data.map_id:
                # Saved spot belongs to another world (map switched while
                # travelling) — fall back to the main map's spawn.
                pass
            else:
                tile = (saved[1], saved[2])
                direction = saved[3]
        if not main_rt.collision.is_walkable(*tile):
            tile = main_rt.map_data.spawn
        move_player_between_runtimes(cur_rt, target_rt, uid, tile)
        player = target_rt.state.get_player(uid)
        if player is not None:
            player.direction = direction
        if self.db is not None:
            await delete_travel_return(self.db, cur_rt.channel_id, uid)
        self.manager.touch_session(cur_rt.channel_id, uid)
        await self._travel_render(interaction, target_rt, screen, uid)

    @app_commands.command(name="mapreset", description="Reset toàn bộ map (admin)")
    async def mapreset(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        for screen in list(rt.screens.values()):
            await self._delete_screen(interaction, rt, screen)
            await self.manager.notify_session_event(
                rt.channel_id, screen.user_id, "reset",
                detail=f"map {rt.map_data.map_id} đã được reset bởi admin",
            )
        rt.state.players.clear()
        rt.state.zombies.clear()
        rt.members.clear()
        rt.state.blocks.clear()  # ground shows again everywhere
        if rt.resources is not None:
            rt.resources.reset()  # every felled tree/bush regrows immediately
        if rt.terrain is not None:
            rt.terrain.reset()  # every scooped tile grows its tuft back
        await self.db.execute("DELETE FROM players WHERE channel_id=?", (interaction.channel.id,))
        await self.db.execute("DELETE FROM inventory WHERE channel_id=?", (interaction.channel.id,))
        await self.db.execute("DELETE FROM blocks WHERE channel_id=?", (interaction.channel.id,))
        await self.db.execute("DELETE FROM resources WHERE channel_id=?", (interaction.channel.id,))
        await self.db.execute("DELETE FROM terrain WHERE channel_id=?", (interaction.channel.id,))
        await interaction.response.send_message(
            "Đã reset map (đã xoá mọi màn hình riêng).", ephemeral=True,
            delete_after=EPHEMERAL_OK,
        )

    @app_commands.command(name="mapinfo", description="Xem thông tin map")
    async def mapinfo(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        cam_mode = rt.camera.mode if rt.camera else "full"
        lines = [
            f"**Map:** {rt.map_data.display_name} (`{rt.map_data.map_id}`)",
            f"**Kích thước:** {rt.map_data.width}x{rt.map_data.height} tile",
            f"**Camera:** {cam_mode}" + (f" ({rt.camera.view_w}x{rt.camera.view_h})" if rt.camera and rt.camera.follow else ""),
            f"Players: {len(rt.state.get_visible_players())} · Screens: {len(rt.screens)}",
            f"Legacy hub: {'có' if rt.hub_message_id else 'không'}",
        ]
        for uid, screen in rt.screens.items():
            p = rt.state.get_player(uid)
            name = p.display_name if p is not None else str(uid)
            if screen.auto_running:
                auto_state = f"chạy ({screen.auto_direction.name})"
            elif screen.auto_armed:
                auto_state = "chờ hướng"
            else:
                auto_state = "tắt"
            lines.append(
                f"  - {name}: step x{screen.step_size} · auto {auto_state} · screen {'có' if screen.message_id else 'không'} · hub {'có' if screen.hub_message_id else 'không'}"
            )
        for p in rt.state.get_visible_players():
            lines.append(f"  - {p.display_name}: HP {p.hp}/{p.max_hp} MP {p.mana}/{p.max_mana} ({p.x}, {p.y}) {p.direction}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="setweather", description="[Admin] Đổi thời tiết (hub + hiệu ứng trên map)")
    @app_commands.choices(weather=_WX_CHOICES)
    @app_commands.check(_is_admin)
    async def setweather(self, interaction: discord.Interaction, weather: str):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map. Dùng /startmap trước.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        if weather == WEATHER_AUTO:
            # Resume the real national snapshot (undo any manual override).
            await interaction.response.defer(ephemeral=True)
            msg = await self._force_weather_fetch(rt.channel_id)
            await send_ephemeral_followup(interaction, msg, EPHEMERAL_OK)
            return
        key = next((k for k, v in WEATHER.items() if v == weather), None)
        if key is None:
            await interaction.response.send_message(
                "Thời tiết không hợp lệ.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        await interaction.response.defer(ephemeral=True)
        async with rt.lock:
            rt.weather_key = key
            # Pin: the auto weather loop must not clobber this manual key on
            # the next 15-min fetch (web client "weather looks off" bug).
            rt.weather_manual = True
        self._refresh_weather_ui(rt)
        fx = self.renderer.weather_fx
        mode = "GIF động" if fx.is_animated(key) and fx.available() else "PNG tĩnh (không particle)"
        gate = bool(getattr(rt, "weather_fx_enabled", False))
        gate_note = (
            "(FX gate đang BẬT)" if gate
            else "(FX gate đang TẮT — chỉ hiển thị icon hub; bật trong Cài đặt → Thời tiết)"
        )
        await send_ephemeral_followup(
            interaction,
            f"✅ Đã đổi thời tiết: **{weather}** (`{key}`) — map sẽ render {mode} {gate_note}.",
            EPHEMERAL_OK,
        )

    def _refresh_weather_ui(self, rt) -> None:
        """Push a weather change to every hub + screen in the scenario."""
        hub = getattr(self.manager, "hub_coalescer", None)
        if hub is not None:
            hub.schedule(rt.channel_id, {"type": "weather"})
        for uid, screen in rt.screens.items():
            if screen.message_id is not None:
                self._schedule_screen(rt.channel_id, uid)

    @app_commands.command(name="weatherinfo", description="Xem trạng thái thời tiết + hiệu ứng hiện tại")
    async def weatherinfo(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map. Dùng /startmap trước.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        from game.weather import compute_modifiers

        ws = getattr(rt, "weather_state", None)
        key = getattr(rt, "weather_key", "sun_clouds")
        fx = self.renderer.weather_fx
        lines = [f"**Key hiển thị:** `{key}` — {WEATHER.get(key, '?')}"]
        gate = bool(getattr(rt, "weather_fx_enabled", False))
        lines.append(
            "**FX map (GIF động):** "
            + ("BẬT" if gate else "TẮT — player bật qua admin (Cài đặt → Thời tiết)")
        )
        if fx.is_animated(key):
            state = (
                "đang chạy (GIF động)" if gate and fx.available()
                else "BỊ CHẶN bởi gate" if not gate
                else "TẮT (thiếu assets/fx!)"
            )
            lines.append(f"**FX cho key này:** {state}")
        else:
            lines.append("**FX cho key này:** không có particle (key tĩnh)")
        missing = fx.missing_sets()
        if missing:
            lines.append(f"**FX thiếu:** {', '.join(missing)} — chạy scripts/convert_weather_fx.py")
        if ws is None:
            lines.append("_Chưa có snapshot thời tiết thật (chờ lần fetch đầu)._")
        else:
            import time

            r = ws.ratios()
            m = compute_modifiers(ws)
            fetched = time.strftime("%H:%M:%S", time.localtime(ws.fetched_at)) if ws.fetched_at else "?"
            lines.append(
                f"**Snapshot thật:** {len(ws.stations)} trạm, fetch {fetched}, ok={ws.ok}\n"
                f"- rain {r['rain_ratio']:.0%} / storm {r['storm_ratio']:.0%} / "
                f"cloud {r['cloud_ratio']:.0%} / heat {r['heat_index']:.0%}\n"
                f"- Dominant: `{ws.dominant_key()}`\n"
                f"- Buffs: coin x{m.coin_mult}, mana x{m.mana_regen_mult}, "
                f"hp x{m.hp_regen_mult}, xp x{m.xp_mult}"
                + (" — 🌩 STORM EVENT đang chạy!" if m.storm_event else "")
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="weatherfetch", description="[Admin] Fetch ngay thời tiết thật (Open-Meteo)")
    @app_commands.check(_is_admin)
    async def weatherfetch(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        msg = await self._force_weather_fetch(interaction.channel.id)
        await send_ephemeral_followup(interaction, msg, EPHEMERAL_OK)

    async def _force_weather_fetch(self, channel_id: int) -> str:
        """Fetch the real national snapshot now and fan it out (admin path)."""
        import time

        svc = getattr(self.manager, "weather_service", None)
        if svc is None or not svc.enabled:
            return "⚠️ Weather service đang TẮT (WEATHER_ENABLED)."
        ws = await svc.fetch(time.time())
        rt = self.manager.runtimes.get(channel_id)
        if rt is None:
            return "⚠️ Chưa có map trong channel này (/startmap trước)."
        async with rt.lock:
            rt.weather_state = ws
            rt.weather_key = ws.weather_key
            # Resume-auto path (WEATHER_AUTO): unpin so future auto fetches
            # adopt the fresh key again.
            rt.weather_manual = False
        self._refresh_weather_ui(rt)
        fx = self.renderer.weather_fx
        missing = fx.missing_sets()
        fx_note = f"FX thiếu: {', '.join(missing)}" if missing else "FX assets đầy đủ"
        if not ws.ok or not ws.stations:
            return "⚠️ Fetch thất bại — fallback clear. Kiểm tra log [WEATHER]."
        return (
            f"✅ Fetch xong ({len(ws.stations)} trạm) → key `{ws.weather_key}` "
            f"({WEATHER.get(ws.weather_key, '?')}) | {fx_note}."
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        from discord.app_commands import CheckFailure

        if isinstance(error, CheckFailure):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Lệnh này chỉ dành cho admin (Administrator/Manage Server).",
                    ephemeral=True, delete_after=EPHEMERAL_WARN,
                )
            return
        raise error

    @app_commands.command(name="hub", description="Tạo/khôi phụng tin nhắn hub (HUD) của riêng bạn")
    async def hub(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map. Dùng /startmap trước.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is None or screen.message_id is None:
            await interaction.response.send_message(
                "Bạn chưa có màn hình. Dùng /joinmap.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        if screen.hub_message_id is not None:
            await interaction.response.send_message(
                "Hub của bạn đã tồn tại.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        created = await self._create_player_hub(interaction, rt, uid)
        await interaction.response.send_message(
            "Đã tạo hub." if created else "Không thể tạo hub.",
            ephemeral=True, delete_after=EPHEMERAL_OK if created else EPHEMERAL_WARN,
        )

    @app_commands.command(name="inventory", description="Mở túi đồ của bạn")
    async def inventory(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        from discord_ui.panels import InventoryPanel

        panel = InventoryPanel(interaction.channel.id, interaction.user.id, self.manager)
        await interaction.response.send_message("🎒 Inventory", view=panel, ephemeral=True)
        self.bot.add_view(panel)

    @app_commands.command(name="settings", description="Mở cài đặt")
    async def settings(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        from discord_ui.settings_view import SettingsView

        panel = SettingsView.open_with_interaction(self.manager, interaction)
        await interaction.response.send_message(
            embed=panel.build_embed(), view=panel, ephemeral=True
        )
        self.bot.add_view(panel)
