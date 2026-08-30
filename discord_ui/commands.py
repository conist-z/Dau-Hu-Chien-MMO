import io

import discord
from discord import app_commands
from discord.ext import commands

from discord_ui.map_view import MapView
from game.state import Direction
from rendering.camera import Camera
from rendering.renderer import RenderResult


def _focus(rt, user_id=None) -> None:
    cam = rt.camera
    if cam is None or not cam.follow:
        return
    if user_id is not None and user_id in rt.state.players:
        p = rt.state.get_player(user_id)
        cam.center_on(p.x, p.y, rt.map_data.width, rt.map_data.height)
    else:
        players = rt.state.get_visible_players()
        if players:
            cam.center_on(players[0].x, players[0].y, rt.map_data.width, rt.map_data.height)
        else:
            cam.center_on(rt.map_data.spawn[0], rt.map_data.spawn[1], rt.map_data.width, rt.map_data.height)


def _file(result: RenderResult) -> discord.File:
    from rendering.renderer import finalize_for_upload

    upload_img = finalize_for_upload(result.image)
    buf = io.BytesIO()
    upload_img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return discord.File(buf, filename=result.filename)


class MapCog(commands.Cog):
    def __init__(self, bot: commands.Bot, manager, renderer, db):
        self.bot = bot
        self.manager = manager
        self.renderer = renderer
        self.db = db

    async def _ensure_map_message(self, interaction: discord.Interaction, rt, result):
        """Edit the existing map message, or recreate it if it was deleted.

        Returns (message, created). Recreating updates rt.message_id + DB so the
        game stays playable even after the user deletes the bot's message.
        """
        if rt.message_id:
            try:
                msg = await interaction.channel.fetch_message(rt.message_id)
                await msg.edit(attachments=[_file(result)], view=MapView(interaction.channel.id, self.manager))
                return msg, False
            except (discord.NotFound, discord.HTTPException):
                rt.message_id = None
        view = MapView(interaction.channel.id, self.manager)
        msg = await interaction.channel.send(file=_file(result), view=view)
        rt.message_id = msg.id
        from persistence.repositories import save_scenario

        await save_scenario(self.db, interaction.channel.id, msg.id, rt.map_data.map_id)
        self.bot.add_view(view)
        return msg, True

    @app_commands.command(name="startmap", description="Tạo map game mới cho kênh này")
    @app_commands.describe(map_id="ID map (tên file trong assets/maps, mặc định bigmap)")
    async def startmap(self, interaction: discord.Interaction, map_id: str = "bigmap"):
        cid = interaction.channel.id
        rt = self.manager.get_runtime(cid)
        if rt is not None and rt.map_data.map_id != map_id:
            # A different map was requested: replace the running scenario so the
            # new map actually loads (otherwise it reports "already exists").
            self.manager.remove_runtime(cid)
            if self.db is not None:
                await self.db.execute("DELETE FROM players WHERE channel_id=?", (cid,))
                await self.db.execute("DELETE FROM scenarios WHERE channel_id=?", (cid,))
            rt = None
        if rt is not None:
            if rt.message_id:
                try:
                    await interaction.channel.fetch_message(rt.message_id)
                    await interaction.response.send_message("Map đã tồn tại ở kênh này.", ephemeral=True)
                    return
                except (discord.NotFound, discord.HTTPException):
                    pass
            # runtime exists but its message was deleted -> recreate it
            _focus(rt)
            result = await self.renderer.render(rt.state, rt.map_data, members=rt.members, camera=rt.camera)
            rt.composite = result.composite
            view = MapView(cid, self.manager)
            await interaction.response.send_message(file=_file(result), view=view)
            msg = await interaction.original_response()
            rt.message_id = msg.id
            from persistence.repositories import save_scenario

            await save_scenario(self.db, cid, msg.id, rt.map_data.map_id)
            self.bot.add_view(view)
            return
        rt = self.manager.create_runtime(cid, map_id)
        _focus(rt)
        result = await self.renderer.render(rt.state, rt.map_data, camera=rt.camera)
        rt.composite = result.composite
        view = MapView(cid, self.manager)
        await interaction.response.send_message(file=_file(result), view=view)
        msg = await interaction.original_response()
        rt.message_id = msg.id
        from persistence.repositories import save_scenario

        await save_scenario(self.db, cid, msg.id, map_id)
        self.bot.add_view(view)

    @app_commands.command(name="bigmap", description="Tạo map bigmap (Tiled) cho kênh này")
    async def bigmap(self, interaction: discord.Interaction):
        # Force-switch to the bigmap even if another map is already running in
        # this channel (so a stale demo/test-map scenario is replaced).
        cid = interaction.channel.id
        rt = self.manager.get_runtime(cid)
        if rt is not None:
            self.manager.remove_runtime(cid)
            if self.db is not None:
                await self.db.execute("DELETE FROM players WHERE channel_id=?", (cid,))
                await self.db.execute("DELETE FROM scenarios WHERE channel_id=?", (cid,))
        await self.startmap(interaction, map_id="bigmap")

    @app_commands.command(name="joinmap", description="Tham gia map game")
    async def joinmap(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message("Chưa có map. Dùng /startmap trước.", ephemeral=True)
            return
        rt.members[interaction.user.id] = interaction.user
        p = rt.state.add_player(interaction.user.id, interaction.user.display_name, *rt.map_data.spawn)
        from persistence.repositories import save_player

        await save_player(self.db, interaction.channel.id, p)
        _focus(rt, interaction.user.id)
        result = await self.renderer.render(rt.state, rt.map_data, members=rt.members, camera=rt.camera)
        rt.composite = result.composite
        await interaction.response.defer()
        if rt.message_id:
            msg = await interaction.channel.fetch_message(rt.message_id)
            await msg.edit(attachments=[_file(result)], view=MapView(interaction.channel.id, self.manager))
        await interaction.followup.send("Đã tham gia map.", ephemeral=True)

    @app_commands.command(name="leave-map", description="Rời khỏi map")
    async def leave_map(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message("Chưa có map.", ephemeral=True)
            return
        rt.state.remove_player(interaction.user.id)
        rt.members.pop(interaction.user.id, None)
        from persistence.repositories import delete_player

        await delete_player(self.db, interaction.channel.id, interaction.user.id)
        _focus(rt)
        result = await self.renderer.render(rt.state, rt.map_data, members=rt.members, camera=rt.camera)
        rt.composite = result.composite
        await interaction.response.defer()
        _, created = await self._ensure_map_message(interaction, rt, result)
        await interaction.followup.send("Đã rời map." + (" (tạo lại map)" if created else ""), ephemeral=True)

    @app_commands.command(name="map", description="Làm mới ảnh map")
    async def map_refresh(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message("Chưa có map.", ephemeral=True)
            return
        _focus(rt)
        result = await self.renderer.render(rt.state, rt.map_data, members=rt.members, camera=rt.camera)
        rt.composite = result.composite
        await interaction.response.defer()
        _, created = await self._ensure_map_message(interaction, rt, result)
        await interaction.followup.send("Đã tạo lại map." if created else "Đã làm mới.", ephemeral=True)

    @app_commands.command(name="mapreset", description="Reset toàn bộ map (admin)")
    async def mapreset(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message("Chưa có map.", ephemeral=True)
            return
        rt.state.players.clear()
        rt.members.clear()
        await self.db.execute("DELETE FROM players WHERE channel_id=?", (interaction.channel.id,))
        _focus(rt)
        result = await self.renderer.render(rt.state, rt.map_data, camera=rt.camera)
        rt.composite = result.composite
        await interaction.response.defer()
        _, created = await self._ensure_map_message(interaction, rt, result)
        await interaction.followup.send("Đã tạo lại map." if created else "Đã reset map.", ephemeral=True)

    @app_commands.command(name="mapinfo", description="Xem thông tin map")
    async def mapinfo(self, interaction: discord.Interaction):
        rt = self.manager.get_runtime(interaction.channel.id)
        if rt is None:
            await interaction.response.send_message("Chưa có map.", ephemeral=True)
            return
        cam_mode = rt.camera.mode if rt.camera else "full"
        auto_state = "tắt"
        if rt.auto_running:
            auto_state = f"chạy ({rt.auto_direction.name})"
        elif rt.auto_armed:
            auto_state = "chờ hướng"
        lines = [
            f"**Map:** {rt.map_data.display_name} (`{rt.map_data.map_id}`)",
            f"**Kích thước:** {rt.map_data.width}x{rt.map_data.height} tile",
            f"**Camera:** {cam_mode}" + (f" ({rt.camera.view_w}x{rt.camera.view_h})" if rt.camera and rt.camera.follow else ""),
            f"**Bước/click:** {rt.step_size} block",
            f"**Auto-move:** {auto_state}",
            f"**Players:** {len(rt.state.get_visible_players())}",
        ]
        for p in rt.state.get_visible_players():
            lines.append(f"  - {p.display_name}: ({p.x}, {p.y}) {p.direction}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
