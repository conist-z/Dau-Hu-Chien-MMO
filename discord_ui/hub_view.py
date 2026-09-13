import io
import logging

import discord
from discord.ui import Button, View

from discord_ui.ephemeral import EPHEMERAL_OK, EPHEMERAL_WARN
from discord_ui.coalescer import resolve_channel
from discord_ui.locks import player_lock
from game.terrain import render_kwargs as terrain_render_kwargs
from game.npc import npc_adjacent
from rendering.hub_renderer import HubRenderResult, encode_hub
from rendering.renderer import (
    run_image_task,
    screen_internal_size,
    screen_internal_size_for_camera,
)


def _wx_key_of(rt):
    """Effective screen weather key (respects the scenario FX gate)."""
    try:
        from rendering.renderer import effective_weather_key

        return effective_weather_key(rt)
    except Exception:
        return getattr(rt, "weather_key", None)

log = logging.getLogger("GAME")

# Hub keeps exactly three emoji buttons: Map, Inventory, Settings.
HUB_BUTTONS = {
    "map": "🗺️",
    "inv": "🎒",
    "set": "⚙️",
}


def _cid(channel_id: int, user_id, key: str) -> str:
    if user_id is None:
        return f"hb:{channel_id}:{key}"
    return f"hb:{channel_id}:{user_id}:{key}"


def build_hub_embed(rt, focused_user_id: int = None) -> discord.Embed:
    players = rt.state.get_visible_players()
    lines = []
    for p in players:
        lines.append(
            f"{p.display_name}: HP {p.hp}/{p.max_hp} · MP {p.mana}/{p.max_mana} · Lv{p.level}"
        )
    focused = rt.state.get_player(focused_user_id) if focused_user_id else None
    npc = None
    if focused is not None:
        npc = npc_adjacent(rt.npc_map.npcs, focused.x, focused.y)
    text = "\n".join(lines) if lines else "_Chưa có ai trên map._"
    embed = discord.Embed(title=rt.map_data.display_name, description=text)
    if npc is not None:
        embed.set_footer(text=f"{npc.name} đang ở gần")
    return embed


def _hotbar_content(manager, rt, focused_user_id):
    """{slot: (item_id, qty)} for the focused player's hotbar, or None."""
    uid = focused_user_id
    if uid is None:
        players = rt.state.get_visible_players()
        uid = players[0].user_id if players else None
    if uid is None:
        return None
    try:
        bindings = manager.get_hotbar(rt.channel_id, uid)
        inv = manager.get_inventory(rt.channel_id, uid)
    except Exception:
        return None
    out = {
        s: (iid, inv.count(iid))
        for s, iid in bindings.items()
        if iid and inv.count(iid) > 0
    }
    return out or None


async def _resolve_hub_avatars(manager, rt) -> dict:
    """Per-player avatar tokens for the hub HUD emblem badges.

    Honors each player's chosen sprite (bundled pack / server emoji); falls
    back to the face token. Never raises — a broken avatar must not kill the
    whole hub render."""
    cache = getattr(manager.renderer, "avatar_cache", None) if manager.renderer else None
    if cache is None:
        return {}
    out = {}
    for p in rt.state.get_visible_players():
        try:
            member = rt.members.get(p.user_id)
            if member is not None:
                out[p.user_id] = await cache.get_avatar(
                    member, label=p.display_name[:1],
                    sprite_id=getattr(p, "sprite_id", "") or "",
                )
            else:
                out[p.user_id] = cache.fallback(p.display_name[:1])
        except Exception as e:  # noqa: BLE001 — avatar is decoration, never fatal
            log.warning("[HUB] avatar resolve failed for %s: %s", p.user_id, e)
    return out


async def render_hub_to_file(manager, rt, focused_user_id: int = None, screen=None):
    """Render this player's hub image + embed.

    The hub image width is forced to match the player's OWN screen width so the
    bar visually lines up with their personal map frame. Falls back to the shared
    ``rt.camera`` when no per-player screen is available (legacy / restore).

    NEVER raises: if the HUD image render blows up for any reason the caller
    still gets ``(None, embed)`` and posts a text-only hub — the HUD bar must
    never be the reason a player's hub message is missing entirely."""
    tile = getattr(manager.renderer, "tile_size", 32) if manager.renderer else 32
    try:
        if screen is not None and screen.camera is not None:
            screen_w, _ = screen_internal_size_for_camera(screen.camera, rt, tile)
        else:
            screen_w, _ = screen_internal_size(rt, tile)
        # Read the live game state on the event loop; only the pure image encoder
        # runs in a worker so a concurrent state update cannot race a PIL render.
        avatars = await _resolve_hub_avatars(manager, rt)
        result: HubRenderResult = manager.hub_renderer.render(
            rt, rt.map_data, rt.npc_map, focused_user_id,
            target_internal_w=screen_w, hotbar=_hotbar_content(manager, rt, focused_user_id),
            avatars=avatars,
        )
        data = await run_image_task(encode_hub, result)
    except Exception as e:  # noqa: BLE001 — image is optional, the hub is not
        log.warning("[HUB] HUD render failed (%r) — posting text-only hub", e)
        return None, build_hub_embed(rt, focused_user_id)
    buf = io.BytesIO(data)
    buf.seek(0)
    return discord.File(buf, filename=result.filename), build_hub_embed(rt, focused_user_id)


async def flush_hub_batch(manager, key, batch: list) -> None:
    """Coalesced per-player hub HUD update: render ONCE, edit the player's own hub.

    ``key`` is ``(channel_id, user_id)`` (or a bare channel_id for legacy/hub-wide
    updates). Each player owns their hub message, so updates to different players
    never contend for the same edit bucket."""
    if isinstance(key, tuple):
        channel_id, user_id = key
    else:  # legacy hub-wide key (e.g. weather change broadcast to all)
        channel_id, user_id = key, None
    rt = manager.get_runtime_for(channel_id, user_id)
    if rt is None or manager.hub_renderer is None:
        return
    from discord_ui.adjacency import enforce_adjacency
    from discord_ui.locks import player_lock
    from persistence.repositories import save_player, save_scenario

    adapter = getattr(manager, "session_adapter", None)
    suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None

    channel = resolve_channel(manager, channel_id, batch)
    focused_user_id = batch[-1].get("focused_user_id")

    if user_id is not None:
        # Serialize the whole edit/recover/create cycle for this player: two
        # lanes racing here is exactly what produced duplicate hubs, each one
        # deleting the other's message until a single orphan remained.
        async with player_lock(channel_id, user_id):
            screen = rt.screens.get(user_id)
            sid = screen.message_id if screen is not None else rt.message_id
            hid = screen.hub_message_id if screen is not None else rt.hub_message_id

            file, embed = await render_hub_to_file(manager, rt, focused_user_id, screen)

            if hid is not None and channel is not None:
                try:
                    await manager.edit_gate.edit_message(
                        channel, hid,
                        attachments=[file], embed=embed,
                        view=HubView(channel_id, manager, user_id),
                    )
                    if sid is not None:
                        # Preserve the player's controls message: the valid
                        # split stack is screen -> controls -> hub, and the
                        # controls message is NOT an interloper to delete.
                        controls_id = (
                            screen.controls_message_id if screen is not None else None
                        )
                        preserve = [mid for mid in (controls_id,) if mid]
                        removed = await enforce_adjacency(
                            channel, sid, hid, preserve_ids=preserve,
                            suppress_ids=suppress,
                        )
                        if removed:
                            log.info(
                                "[ADJ] removed %s interloper(s) for player %s in %s",
                                removed, user_id, channel_id,
                            )
                    if screen is not None:
                        player = rt.state.get_player(user_id)
                        if player is not None:
                            player.hub_message_id = hid
                            if manager.db is not None:
                                await save_player(manager.db, channel_id, player)
                except discord.NotFound:
                    # The hub message was deleted. Forget the dead id and run
                    # one in-lock re-create; if the stack is still broken after
                    # that, the silent repair loop takes over.
                    from discord_ui.refresh import drop_hub_message

                    await drop_hub_message(rt, user_id, channel, suppress_ids=suppress)
                    await create_hub_message(
                        manager, rt, channel_id, channel, user_id, focused_user_id
                    )
                except discord.HTTPException as e:
                    log.warning("[EDIT] flush_hub_batch edit failed: %s", e)
            else:
                await create_hub_message(
                    manager, rt, channel_id, channel, user_id, focused_user_id
                )
        # Post-flush completeness check: anything still missing (hub create
        # failed, screen 404 discovered late...) goes to the silent repair
        # loop instead of leaving the player with a partial stack forever.
        from discord_ui.session_recovery import _attempt_missing, schedule_repair

        if _attempt_missing(rt, user_id) is not None:
            schedule_repair(manager, rt, user_id, why="incomplete stack after hub flush")
        return

    # Legacy path: channel-wide hub (no specific user).
    file, embed = await render_hub_to_file(manager, rt, focused_user_id)
    if rt.hub_message_id is None or channel is None:
        await create_hub_message(manager, rt, channel_id, channel, None, focused_user_id)
        return
    try:
        await manager.edit_gate.edit_message(
            channel, rt.hub_message_id,
            attachments=[file], embed=embed, view=HubView(channel_id, manager, None),
        )
        if rt.message_id is not None:
            removed = await enforce_adjacency(
                channel, rt.message_id, rt.hub_message_id, suppress_ids=suppress
            )
            if removed:
                log.info("[ADJ] removed %s interloper(s) in %s", removed, channel_id)
    except discord.NotFound:
        rt.hub_message_id = None
        await create_hub_message(manager, rt, channel_id, channel, None, focused_user_id)
    except discord.HTTPException as e:
        log.warning("[EDIT] flush_hub_batch (legacy) edit failed: %s", e)


class HubView(View):
    """Persistent per-player hub view (timeout=None, explicit custom_id).

    ``user_id`` routes this hub to a specific player's screen. When
    ``user_id is None`` it is the legacy channel-wide hub."""

    def __init__(self, channel_id: int, manager, user_id=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.manager = manager
        self.user_id = user_id
        for key, label in HUB_BUTTONS.items():
            btn = Button(label=label, row=0, custom_id=_cid(channel_id, self.user_id, key))
            btn.callback = self._make_cb(key)
            self.add_item(btn)

    def _make_cb(self, key: str):
        async def cb(interaction: discord.Interaction):
            await self._handle(interaction, key)

        return cb

    async def on_error(self, interaction: discord.Interaction, error: Exception,
                       item: discord.ui.Item) -> None:
        """Safety net: a hub button crashed. NO channel notice (a live session
        must never look shut down) — log, then verify stack completeness via
        the silent repair loop."""
        log.exception("[HUB] button handler crashed (user=%s)", self.user_id)
        try:
            rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
            uid = interaction.user.id if interaction is not None else None
            if rt is not None and uid is not None:
                from discord_ui.session_recovery import schedule_repair

                schedule_repair(self.manager, rt, uid,
                                why=f"hub button error {error!r}")
        except Exception:  # noqa: BLE001 — the net must never raise
            pass
        if interaction is not None and not interaction.response.is_done():
            try:
                await interaction.response.send_message(
                    "Đã xảy ra lỗi khi xử lý nút hub (đã báo về kênh).",
                    ephemeral=True, delete_after=EPHEMERAL_WARN,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

    async def _handle(self, interaction: discord.Interaction, key: str) -> None:
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        self.manager.touch_session(self.channel_id, interaction.user.id)
        await self._ensure_hub(interaction, rt)

        if key == "map":
            uid = interaction.user.id
            screen = rt.screens.get(uid)
            if screen is None:
                await self._create_screen_from_hub(interaction, rt, uid)
                return
            # Refresh the presser's own screen via the gate (background lane),
            # since the interaction token only edits the message the button is on.
            from discord_ui.map_view import _screen_file, _focus_screen_camera

            _focus_screen_camera(rt, screen, uid)
            result = await self.manager.renderer.render(
                rt.state, rt.map_data, members=rt.members, camera=screen.camera,
                full=True, focus_user_id=uid,
                weather_key=_wx_key_of(rt),
                fx_seed=getattr(rt, "lightning_seed", 0),
                **terrain_render_kwargs(rt),
            )
            screen.composite = result.composite
            try:
                await self.manager.edit_gate.edit_message(
                    interaction.channel, screen.message_id,
                    attachments=[await _screen_file(result)], view=self._build_screen_view(uid),
                )
            except discord.NotFound:
                await self.manager.notify_session_event(
                    self.channel_id, uid, "message_deleted",
                    detail=f"tin nhắn màn hình {screen.message_id} đã biến mất — đang tạo lại",
                    ended=False,
                )
                await self._create_screen_from_hub(interaction, rt, uid)
            except discord.HTTPException as e:
                log.warning("[SCREEN] hub-map refresh edit failed: %s", e)
            try:
                await interaction.response.send_message(
                    "Đã làm mới màn hình của bạn.", ephemeral=True,
                    delete_after=EPHEMERAL_OK,
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            self._schedule_hub(interaction, uid)
            return

        if key == "inv":
            from discord_ui.inventory_view import InventoryView

            uid = interaction.user.id
            screen = rt.screens.get(uid)
            view = InventoryView(self.channel_id, uid, self.manager)
            content, embed = view._render_content()
            self.manager.bot_ref.add_view(view)
            if screen is not None:
                screen.inventory_view = view

            # Reuse the already-open panel if there is one (edit in place);
            # otherwise post a NEW message below the hub — the hub HUD on top
            # is never overwritten while the bag is open.
            old_id = screen.inventory_message_id if screen is not None else None
            if old_id is not None:
                try:
                    await interaction.channel.get_partial_message(old_id).edit(
                        content=content, embed=embed, view=view,
                    )
                    await interaction.response.defer()
                    return
                except discord.NotFound:
                    pass  # panel was deleted manually -> post a fresh one
            await interaction.response.send_message(content=content, embed=embed, view=view)
            msg = await interaction.original_response()
            if screen is not None:
                screen.inventory_message_id = msg.id
            return

        if key == "set":
            from discord_ui.settings_view import SettingsView

            panel = SettingsView.open_with_interaction(self.manager, interaction)
            await interaction.response.send_message(
                embed=panel.build_embed(), view=panel, ephemeral=True
            )
            self.manager.bot_ref.add_view(panel)
            return

    def _build_screen_view(self, uid: int):
        from discord_ui.map_view import MapView

        return MapView(self.channel_id, self.manager, uid)

    def _schedule_hub(self, interaction: discord.Interaction, user_id: int) -> None:
        coalescer = getattr(self.manager, "hub_coalescer", None)
        if coalescer is not None:
            coalescer.schedule(
                (self.channel_id, user_id),
                {"interaction": interaction, "focused_user_id": user_id},
            )

    async def _ensure_hub(self, interaction: discord.Interaction, rt) -> None:
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is None or screen.hub_message_id is None:
            await create_hub_message(self.manager, rt, self.channel_id, interaction.channel, uid)

    async def _create_screen_from_hub(
        self, interaction: discord.Interaction, rt, uid: int
    ) -> None:
        """Mint a personal screen for this user from a hub button press."""
        from discord_ui.map_view import MapView, _screen_file, _focus_screen_camera

        rt.members[uid] = interaction.user
        if uid not in rt.state.players:
            rt.state.add_player(uid, interaction.user.display_name, *rt.map_data.spawn)
            await self.manager.renderer.avatar_cache.get_avatar(
                interaction.user, label=interaction.user.display_name[:1]
            )
        screen = self.manager.ensure_screen(rt, uid)
        # Re-apply the remembered movement preference (1/3/5) on re-mint.
        player = rt.state.get_player(uid)
        if player is not None:
            screen.step_size = player.step_size
        _focus_screen_camera(rt, screen, uid)
        # ACK FIRST (defer) — same 10062 fix as MapCog._spawn_screen: the PIL
        # render + upload routinely overruns the 3s interaction deadline.
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
        result = await self.manager.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=screen.camera,
            full=True, focus_user_id=uid,
            weather_key=_wx_key_of(rt),
            fx_seed=getattr(rt, "lightning_seed", 0),
            **terrain_render_kwargs(rt),
        )
        screen.composite = result.composite
        view = MapView(self.channel_id, self.manager, uid)
        screen.map_view = view
        try:
            await interaction.followup.send(file=await _screen_file(result))
            msg = await interaction.original_response()
            screen.message_id = msg.id
            player = rt.state.get_player(uid)
            if player is not None:
                player.screen_message_id = msg.id
                from persistence.repositories import save_player

                if self.manager.db is not None:
                    await save_player(self.manager.db, self.channel_id, player)
            # Split layout: post the D-pad below the map, then the hub below it.
            from discord_ui.map_view import create_controls_message

            await create_controls_message(
                self.manager, rt, self.channel_id, interaction.channel, uid,
                view=view,
            )
            if getattr(self.manager, "bot_ref", None) is not None:
                self.manager.bot_ref.add_view(view)
                self.manager.bot_ref.add_view(HubView(self.channel_id, self.manager, uid))
            self._schedule_hub(interaction, uid)
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SCREEN] hub-map create screen failed: %s", e)


async def create_hub_message(manager, rt, channel_id: int, channel, user_id=None,
                             focused_user_id: int = None) -> bool:
    """Create the hub message directly under a player's screen message (adjacent).

    Per-player when ``user_id`` is given (hub sits under that player's screen and
    its id is stored on the ``PlayerScreen`` / ``Player``). Falls back to the
    legacy channel-wide hub when ``user_id is None``. Returns True on success.

    Per-player creation is serialized under the shared per-player lock so two
    concurrent recovery paths can never both pass the existence check and send
    two hub messages (the duplicate-hub bug)."""
    from discord_ui.adjacency import enforce_adjacency
    from discord_ui.locks import player_lock
    from persistence.repositories import save_player, save_scenario

    if user_id is not None:
        async with player_lock(channel_id, user_id):
            return await _create_hub_message_locked(
                manager, rt, channel_id, channel, user_id, focused_user_id
            )
    return await _create_hub_message_locked(
        manager, rt, channel_id, channel, user_id, focused_user_id
    )


async def _create_hub_message_locked(manager, rt, channel_id: int, channel,
                                     user_id, focused_user_id: int = None) -> bool:
    from discord_ui.adjacency import enforce_adjacency
    # Import save helpers HERE, not relying on create_hub_message's import:
    # recovery paths call this locked function DIRECTLY (bypassing the wrapper),
    # and function-level imports only bind names in the module namespace when
    # THAT function runs — relying on another function's import is a latent
    # NameError (seen live: "name 'save_player' is not defined" in the hub
    # recreate path after a hub message was deleted on Discord).
    from persistence.repositories import save_player, save_scenario

    # Every delete this function performs is the bot's OWN cleanup: register
    # the ids so bot.py's deleted-message hook does not misread them as
    # "someone deleted a player's screen" (false session-end + lone hub).
    adapter = getattr(manager, "session_adapter", None)
    suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None

    file, embed = await render_hub_to_file(manager, rt, focused_user_id)
    view = HubView(channel_id, manager, user_id)

    if user_id is not None:
        screen = rt.screens.get(user_id)
        if screen is not None and screen.hub_message_id is not None:
            # Belt-and-braces for the "already has a hub" guard: only trust the
            # id when the message still exists on Discord. A stale id (deleted
            # hub) must fall through and re-create the pair's lower half.
            if channel is not None:
                try:
                    await channel.fetch_message(screen.hub_message_id)
                except discord.NotFound:
                    screen.hub_message_id = None
                    state = getattr(rt, "state", None)
                    player = state.get_player(user_id) if state is not None else None
                    if player is not None:
                        player.hub_message_id = None
                except discord.HTTPException:
                    return True  # transient: assume the hub is fine
            else:
                return True  # nothing to verify against
            if screen.hub_message_id is not None:
                return True  # verified live
        sid = screen.message_id if screen is not None else rt.message_id
        if sid is None or channel is None:
            return False
        try:
            kwargs = {"embed": embed, "view": view}
            if file is not None:
                kwargs["file"] = file
            hub = await channel.send(**kwargs)
        except Exception as e:
            log.warning("[HUB] create per-player hub failed: %s", e)
            from discord_ui.session_recovery import schedule_repair

            schedule_repair(manager, rt, user_id, why=f"hub create {e!r}")
            return False
        if screen is not None:
            screen.hub_message_id = hub.id
        player = rt.state.get_player(user_id)
        if player is not None:
            player.hub_message_id = hub.id
            if manager.db is not None:
                await save_player(manager.db, channel_id, player)
        if getattr(manager, "bot_ref", None) is not None:
            manager.bot_ref.add_view(HubView(channel_id, manager, user_id))
        # Preserve the controls message: valid split stack is
        # screen -> controls -> hub (the controls message is not junk).
        controls_id = screen.controls_message_id if screen is not None else None
        await enforce_adjacency(
            channel, sid, hub.id, preserve_ids=[mid for mid in (controls_id,) if mid],
            suppress_ids=suppress,
        )
        return True
    else:
        # Legacy channel-wide hub under the primary screen message.
        if rt.message_id is None or channel is None:
            return False
        try:
            kwargs = {"embed": embed, "view": view}
            if file is not None:
                kwargs["file"] = file
            hub = await channel.send(**kwargs)
        except Exception as e:
            log.warning("[HUB] create legacy hub failed: %s", e)
            return False
        rt.hub_message_id = hub.id
        if manager.db is not None:
            await save_scenario(
                manager.db, channel_id, rt.message_id, rt.map_data.map_id, rt.hub_message_id
            )
        if getattr(manager, "bot_ref", None) is not None:
            manager.bot_ref.add_view(HubView(channel_id, manager, None))
        await enforce_adjacency(channel, rt.message_id, hub.id, suppress_ids=suppress)
        return True
