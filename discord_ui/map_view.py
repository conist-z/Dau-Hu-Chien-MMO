import asyncio
import io

import discord
from discord.ui import Button, View

from game.actions import MoveAction
from game.state import Direction
from rendering.camera import Camera
from rendering.renderer import RenderResult, finalize_for_upload

DIR_BY_KEY = {
    "nw": Direction.NORTH_WEST, "n": Direction.NORTH, "ne": Direction.NORTH_EAST,
    "w": Direction.WEST, "s": Direction.SOUTH, "e": Direction.EAST,
    "sw": Direction.SOUTH_WEST, "se": Direction.SOUTH_EAST,
}

# Direction d-pad glyphs.
EMOJI = {
    "nw": "↖️", "n": "⬆️", "ne": "↗️",
    "w": "⬅️", "s": "⬇️", "e": "➡️",
    "sw": "↙️", "se": "↘️",
}

# Auto-move tick (seconds between continuous steps).
AUTO_INTERVAL = 0.4


def _focus_camera(rt, user_id: int = None) -> None:
    """Centre the (follow) camera on a player so the world scrolls with them."""
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


class MapView(View):
    """Persistent movement view.

    Layout (Discord lays buttons out in horizontal rows, so a true vertical
    sidebar is not possible — the tool bar is its own dedicated top row,
    left-aligned, and is never mixed into the movement grid):

        Row 0 (tool bar):  🔄  ⏏️  🎦
        Row 1 (d-pad):     ↖️  ⬆️  ↗️
        Row 2 (d-pad):     ⬅️  ⬇️  ➡️
        Row 3 (d-pad):     ↙️  ↘️  (spacer)

    - 🔄 refreshes the map image.
    - ⏏️ toggles step form: 1 block <-> 3 blocks per press (changes every
      movement emoji into multi-step).
    - 🎦 toggles auto-move: arm -> the next direction becomes the auto direction
      and the player keeps walking that way until any button is pressed.

    Uses timeout=None + explicit custom_id (mg:{channel_id}:{key}) so buttons
    survive a bot restart and are re-bound via bot.add_view() in setup_hook.
    """

    def __init__(self, channel_id: int, manager):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.manager = manager
        self._build()

    def _cid(self, key: str) -> str:
        return f"mg:{self.channel_id}:{key}"

    def _build(self) -> None:
        # Tool rail on the RIGHT column (🔄 / ⏏️ / 🎦), kept fully separate from
        # the movement grid. The d-pad centre cell is intentionally left EMPTY.
        self.tool_refresh = Button(emoji="🔄", row=0, custom_id=self._cid("t_refresh"))
        self.tool_step = Button(emoji="⏏️", row=1, custom_id=self._cid("t_step"))
        self.tool_auto = Button(emoji="🎦", row=2, custom_id=self._cid("t_auto"))
        for key, btn in (
            ("t_refresh", self.tool_refresh),
            ("t_step", self.tool_step),
            ("t_auto", self.tool_auto),
        ):
            btn.callback = self._make_tool_callback(key)

        # (row_idx, keys) placed left-to-right; "blank" = the empty centre cell.
        grid = [
            (0, ["nw", "n", "ne"]),
            (1, ["w", "blank", "e"]),
            (2, ["sw", "s", "se"]),
        ]
        for row_idx, keys in grid:
            for key in keys:
                if key == "blank":
                    spacer = Button(label="\u200b", disabled=True, row=row_idx, custom_id=self._cid("blank"))
                    self.add_item(spacer)
                    continue
                btn = Button(emoji=EMOJI[key], row=row_idx, custom_id=self._cid(key))
                btn.callback = self._make_callback(key)
                self.add_item(btn)
            tool_key = {0: "t_refresh", 1: "t_step", 2: "t_auto"}[row_idx]
            self.add_item({"t_refresh": self.tool_refresh, "t_step": self.tool_step, "t_auto": self.tool_auto}[tool_key])

        self._apply_tool_labels()

    def _make_callback(self, key: str):
        async def cb(interaction: discord.Interaction):
            await self._handle_move(interaction, key)

        return cb

    def _make_tool_callback(self, kind: str):
        async def cb(interaction: discord.Interaction):
            await self._handle_tool(interaction, kind)

        return cb

    def _apply_tool_labels(self) -> None:
        rt = self.manager.get_runtime(self.channel_id)
        if rt is None:
            return
        # ⏏️ keeps its icon; the step count is shown as a text label (e.g. "x3")
        # so the button reads "⏏️ x3" without stacking two emojis.
        self.tool_step.emoji = "⏏️"
        self.tool_step.label = f"x{rt.step_size}"
        if rt.auto_running:
            self.tool_auto.emoji = "▶️"
        elif rt.auto_armed:
            self.tool_auto.emoji = "🎬"
        else:
            self.tool_auto.emoji = "🎦"
        self.tool_auto.label = ""

    # ----- tool bar -----

    async def _handle_tool(self, interaction: discord.Interaction, kind: str) -> None:
        rt = self.manager.get_runtime(self.channel_id)
        if rt is None:
            await interaction.response.send_message("Chưa có map.", ephemeral=True)
            return

        if kind == "t_refresh":
            self._stop_auto(rt)
            _focus_camera(rt, interaction.user.id)
            await self._render_and_send(
                interaction,
                await self.manager.renderer.render(
                    rt.state, rt.map_data, members=rt.members, camera=rt.camera, full=True
                ),
                edit=True,
            )
            return

        # t_step / t_auto: lightweight component edit (no image re-upload, no
        # defer) so the label/state updates instantly without a 2nd round trip.
        if kind == "t_step":
            self._stop_auto(rt)
            # Cycle step form: 1 -> 3 -> 5 -> 1 blocks per click.
            rt.step_size = {1: 3, 3: 5, 5: 1}.get(rt.step_size, 1)
        elif kind == "t_auto":
            if rt.auto_running:
                self._stop_auto(rt)
            elif rt.auto_armed:
                rt.auto_armed = False
            else:
                rt.auto_armed = True

        self._apply_tool_labels()
        try:
            await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    # ----- movement -----

    async def _handle_move(self, interaction: discord.Interaction, key: str) -> None:
        rt = self.manager.get_runtime(self.channel_id)
        if rt is None:
            await interaction.response.send_message("Chưa có map.", ephemeral=True)
            return

        rt.members[interaction.user.id] = interaction.user
        is_new = interaction.user.id not in rt.state.players
        if is_new:
            rt.state.add_player(interaction.user.id, interaction.user.display_name, *rt.map_data.spawn)
            await self.manager.renderer.avatar_cache.get_avatar(
                interaction.user, label=interaction.user.display_name[:1]
            )

        direction = DIR_BY_KEY[key]

        # Any button cancels a running auto-move. A direction press also still
        # moves once (feels natural); the auto loop is stopped first.
        if rt.auto_running:
            self._stop_auto(rt)
        elif rt.auto_armed:
            # 🎦 was armed: this direction starts continuous auto-move.
            rt.auto_armed = False
            await self._start_auto(interaction, direction)
            return

        prev = rt.state.get_player(interaction.user.id)
        prev_pos = (prev.x, prev.y) if prev is not None else None
        step = rt.step_size
        changed = False
        for _ in range(step):
            _, result = await self.manager.dispatch(self.channel_id, MoveAction(interaction.user.id, direction))
            if result.state_changed:
                changed = True
            else:
                break

        if not changed:
            _focus_camera(rt, interaction.user.id)
            try:
                await interaction.response.edit_message(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        # Acknowledge instantly with a lightweight component edit (no image
        # re-upload, no defer), then let the coalescer produce ONE rendered frame
        # for the whole burst. This is what stops rapid presses from each
        # triggering a slow full render+upload and making the frame load feel laggy.
        try:
            await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

        coalescer = getattr(self.manager, "coalescer", None)
        if coalescer is not None:
            coalescer.schedule(
                self.channel_id,
                {"interaction": interaction, "view": self, "focus_user_id": interaction.user.id},
            )
            return

        _focus_camera(rt, interaction.user.id)
        result = await self.manager.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=rt.camera, full=True
        )
        rt.composite = result.composite
        await self._render_and_send(interaction, result, edit=True)

    # ----- auto-move -----

    async def _start_auto(self, interaction: discord.Interaction, direction: Direction) -> None:
        rt = self.manager.get_runtime(self.channel_id)
        if rt is None or rt.message_id is None:
            return
        rt.auto_user_id = interaction.user.id
        rt.auto_direction = direction
        rt.auto_running = True
        rt.auto_task = asyncio.ensure_future(self._auto_loop(interaction.channel, interaction.user.id))
        self._apply_tool_labels()
        try:
            await interaction.edit_original_response(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    def _stop_auto(self, rt) -> None:
        rt.auto_running = False
        rt.auto_armed = False
        if rt.auto_task is not None and not rt.auto_task.done():
            rt.auto_task.cancel()
        rt.auto_task = None

    async def _auto_loop(self, channel, user_id: int) -> None:
        rt = self.manager.get_runtime(self.channel_id)
        if rt is None:
            return
        try:
            while rt.auto_running:
                step = rt.step_size
                moved = False
                for _ in range(step):
                    _, result = await self.manager.dispatch(
                        self.channel_id, MoveAction(user_id, rt.auto_direction)
                    )
                    if result.state_changed:
                        moved = True
                    else:
                        break
                if not moved:
                    self._stop_auto(rt)
                    break
                _focus_camera(rt, user_id)
                render = await self.manager.renderer.render(
                    rt.state, rt.map_data, members=rt.members, camera=rt.camera, full=True
                )
                rt.composite = render.composite
                upload = finalize_for_upload(render.image)
                buf = io.BytesIO()
                upload.save(buf, "PNG", optimize=True)
                buf.seek(0)
                try:
                    msg = await channel.fetch_message(rt.message_id)
                    await msg.edit(attachments=[discord.File(buf, filename=render.filename)], view=self)
                except (discord.NotFound, discord.HTTPException):
                    self._stop_auto(rt)
                    break
                await asyncio.sleep(AUTO_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception:
            self._stop_auto(rt)
        finally:
            self._apply_tool_labels()

    # ----- rendering helpers -----

    async def _render_and_send(self, interaction: discord.Interaction, result: RenderResult, edit: bool) -> None:
        rt = self.manager.get_runtime(self.channel_id)
        if rt is None or self.manager.renderer is None:
            return
        rt.composite = result.composite
        upload = finalize_for_upload(result.image)
        buf = io.BytesIO()
        upload.save(buf, "PNG", optimize=True)
        buf.seek(0)
        attachment = discord.File(buf, filename=result.filename)
        try:
            if edit:
                if interaction.response.is_done():
                    await interaction.edit_original_response(attachments=[attachment], view=self)
                else:
                    await interaction.response.edit_message(attachments=[attachment], view=self)
            else:
                await interaction.response.send_message(file=attachment, view=self)
        except (discord.NotFound, discord.HTTPException):
            if not interaction.response.is_done():
                try:
                    msg = await interaction.channel.send(file=attachment, view=self)
                    rt.message_id = msg.id
                    if self.manager.db is not None:
                        from persistence.repositories import save_scenario

                        await save_scenario(self.manager.db, self.channel_id, msg.id, rt.map_data.map_id)
                except Exception:
                    pass


async def flush_render_batch(manager, channel_id: int, batch: list) -> None:
    """Render ONCE for a coalesced batch, then acknowledge every press.

    Called by RenderCoalescer.on_flush. ``batch`` holds the payloads enqueued by
    each button press (interaction + persistent view). The heavy work (image
    generation + upload) happens a single time; each interaction's original
    response is then edited with that one image so the buttons stop "loading".
    """
    rt = manager.get_runtime(channel_id)
    if rt is None or manager.renderer is None:
        return
    focus_user_id = batch[-1].get("focus_user_id")
    _focus_camera(rt, focus_user_id)
    result = await manager.renderer.render(
        rt.state,
        rt.map_data,
        members=rt.members,
        camera=rt.camera,
        full=True,
    )
    rt.composite = result.composite
    # ONE upload for the whole burst: edit the map message directly via the
    # channel (bot token), not per-interaction edit_original_response. This avoids
    # the extra round trip per press and is what makes the coalesced frame fast.
    upload = finalize_for_upload(result.image)
    buf = io.BytesIO()
    upload.save(buf, "PNG", optimize=True)
    buf.seek(0)
    attachment = discord.File(buf, filename=result.filename)
    view = batch[0].get("view")
    channel = batch[0]["interaction"].channel
    try:
        msg = await channel.fetch_message(rt.message_id)
        await msg.edit(attachments=[attachment], view=view)
    except (discord.NotFound, discord.HTTPException):
        try:
            msg = await channel.send(file=attachment, view=view)
            rt.message_id = msg.id
            if manager.db is not None:
                from persistence.repositories import save_scenario

                await save_scenario(manager.db, channel_id, msg.id, rt.map_data.map_id)
        except Exception:
            pass
