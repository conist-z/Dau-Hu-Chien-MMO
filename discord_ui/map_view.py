import asyncio
import io
import logging
from typing import Optional

import discord
from discord.ui import Button, View

log = logging.getLogger("GAME")

from config import HOTBAR_SLOTS
from discord_ui.ephemeral import (
    EPHEMERAL_ACTION,
    EPHEMERAL_OK,
    EPHEMERAL_WARN,
    send_ephemeral_followup,
)
from discord_ui.coalescer import resolve_channel
from discord_ui.refresh import reattach_hub_under_screen as _reattach_hub_under_screen
from game.actions import (
    AimAction,
    AimResetAction,
    AttackAction,
    BreakBlockAction,
    ChopAction,
    MoveAction,
    PlaceBlockAction,
    TurnAction,
)
from game.blocks import get_block, next_placeable_block
from game.items import get_item
from game.resources import (
    NODE_DEFS,
    is_ore_kind,
    render_kwargs as resource_render_kwargs,
)
from game.terrain import render_kwargs as terrain_render_kwargs
from game.state import Direction, next_clockwise
from rendering.renderer import (
    build_chop_frames,
    encode_upload_gif,
    finalize_for_upload,
    run_image_task,
)

# Friendly ephemeral messages for blocked block actions.
BLOCK_REASON_TEXT = {
    "no_player": "Bạn chưa tham gia map.",
    "invisible": "Bạn đang ẩn mình.",
    "not_placeable": "Khối này không thể đặt.",
    "blocked_tile": "Không thể đặt khối ở đó (đất không bằng / ngoài map / có khối rồi).",
    "tile_occupied": "Có người chơi đang đứng ở ô đó.",
    "no_material": "Bạn không có nguyên liệu của khối này trong túi.",
    "already_block": "Ô đó đã có khối.",
    "no_block": "Không có khối nào để phá ở ô trước mặt.",
    "out_of_range": "Ô mục tiêu quá xa (tối đa 3 ô quanh bạn).",
    "own_tile": "Không thể đặt khối ngay chỗ bạn đang đứng.",
}

CHOP_REASON_TEXT = {
    "no_player": "Bạn chưa tham gia map.",
    "invisible": "Bạn đang ẩn mình.",
    "no_node": "Không có cây / bụi cây nào để chặt ở ô trước mặt.",
    "regrowing": "Cây đang mọc lại — quay lại sau một lát nhé!",
    "too_hard": "Quá cứng để đập bằng tay.",
}

SHOVEL_REASON_TEXT = {
    "no_player": "Bạn chưa tham gia map.",
    "invisible": "Bạn đang ẩn mình.",
    "no_grass": "Không có cỏ để xúc ở ô đó (dùng được trên ô cỏ trống).",
    "already_scooped": "Ô này đã được xúc rồi.",
    "blocked_tile": "Có khối chắn ở ô đó.",
    "tile_occupied": "Có người chơi đang đứng ở ô đó.",
}

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

# Auto-move tick (seconds between continuous steps). The actual frame rate is
# floored by the edit gate (MIN_EDIT_SPACING) — this tick only keeps the loop
# from spinning when the gate allows faster frames.
AUTO_INTERVAL = 0.4


def _focus_screen_camera(rt, screen, user_id: Optional[int] = None) -> None:
    """Centre the screen's OWN camera on its owner (or the first player)."""
    cam = screen.camera
    if cam is None or not cam.follow:
        return
    p = rt.state.get_player(user_id) if user_id is not None else None
    if p is None:
        players = rt.state.get_visible_players()
        if players:
            p = players[0]
    if p is not None:
        cam.center_on(p.x, p.y, rt.map_data.width, rt.map_data.height)
    else:
        cam.center_on(
            rt.map_data.spawn[0], rt.map_data.spawn[1], rt.map_data.width, rt.map_data.height
        )


def _screen_bytes(result) -> bytes:
    """Encode a RenderResult into upload bytes (pure, sync — run off-loop).

    Weather-FX renders carry ``frames`` (animated overlay loop) and encode as
    a looping GIF; plain renders stay a single PNG."""
    frames = getattr(result, "frames", None)
    if frames and len(frames) > 1:
        return encode_upload_gif(
            frames, getattr(result, "duration_ms", 160), optimize=False
        )
    upload = finalize_for_upload(result.image)
    buf = io.BytesIO()
    # optimize=False: the quantized map PNG is tiny (~5-10 KB); the optimize
    # delta pass buys <2 KB for ~5ms of CPU on every movement frame.
    upload.save(buf, "PNG", optimize=False)
    return buf.getvalue()


async def create_controls_message(
    manager, rt, channel_id: int, channel, user_id: int, view=None
) -> bool:
    """Create or repair the image-free controls message below the map.

    The map message intentionally carries no View in the split layout. This
    keeps every D-pad interaction on a tiny components-only message, so the
    interaction ACK never waits for a map attachment upload. Existing legacy
    map messages are migrated in place once; movement never pays that cost.
    """
    screen = rt.screens.get(user_id)
    if screen is None or screen.message_id is None or channel is None:
        return False

    controls_view = view or getattr(screen, "map_view", None) or MapView(
        channel_id, manager, user_id
    )
    existing_id = getattr(screen, "controls_message_id", None)
    if existing_id is not None:
        try:
            await channel.fetch_message(existing_id)
        except discord.NotFound:
            screen.controls_message_id = None
            player = rt.state.get_player(user_id)
            if player is not None:
                player.controls_message_id = None
        except discord.HTTPException as e:
            # A transient fetch failure must not create a duplicate controls
            # message. Keep the persisted id and let the next repair retry.
            log.warning("[CONTROLS] could not verify %s: %s", existing_id, e)
            return True
        else:
            # HARD ORDER RULE: screen (top) -> controls (middle) -> hub
            # (bottom). Snowflake ids grow with time, so in one channel the
            # id order IS the position order. A controls message older than
            # the screen (above it) or newer than the hub (below it) is in
            # the wrong slot; Discord cannot move messages, so delete it and
            # fall through to a fresh send directly under the screen.
            sid = screen.message_id
            hub_id = getattr(screen, "hub_message_id", None)
            wrong_slot = existing_id <= sid or (
                hub_id is not None and existing_id >= hub_id
            )
            if not wrong_slot:
                screen.split_layout = True
                screen.map_view = controls_view
                if getattr(manager, "bot_ref", None) is not None:
                    manager.bot_ref.add_view(controls_view)
                return True
            adapter = getattr(manager, "session_adapter", None)
            suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None
            if suppress is not None:
                suppress.add(existing_id)
            try:
                await channel.get_partial_message(existing_id).delete()
            except (discord.NotFound, discord.HTTPException):
                pass
            screen.controls_message_id = None
            player = rt.state.get_player(user_id)
            if player is not None:
                player.controls_message_id = None

    # Migrate an old combined message in place: remove its obsolete buttons
    # before appending the new controls message. The edit is paid once during
    # migration, never on the movement path.
    map_message = channel.get_partial_message(screen.message_id)
    edit = getattr(map_message, "edit", None)
    if callable(edit):
        try:
            await edit(view=None)
        except discord.NotFound:
            return False
        except discord.HTTPException as e:
            log.warning("[SCREEN] could not remove legacy map view: %s", e)

    # If a hub already exists during migration/recovery, it is now above the
    # controls message. Drop it before posting controls; create_hub_message()
    # will append a fresh hub underneath afterwards.
    old_hub = getattr(screen, "hub_message_id", None)
    if old_hub is not None:
        from discord_ui.refresh import drop_hub_message

        adapter = getattr(manager, "session_adapter", None)
        suppress = getattr(adapter, "suppress_ids", None) if adapter is not None else None
        await drop_hub_message(rt, user_id, channel, suppress_ids=suppress)

    try:
        controls = await channel.send(content="\u200b", view=controls_view)
    except discord.HTTPException as e:
        log.warning("[CONTROLS] create failed for user %s in %s: %s", user_id, channel_id, e)
        return False
    screen.controls_message_id = controls.id
    screen.split_layout = True
    screen.map_view = controls_view
    player = rt.state.get_player(user_id)
    if player is not None:
        player.controls_message_id = controls.id
        if getattr(manager, "db", None) is not None:
            from persistence.repositories import save_player

            await save_player(manager.db, channel_id, player)
    if getattr(manager, "bot_ref", None) is not None:
        manager.bot_ref.add_view(controls_view)

    if old_hub is not None:
        from discord_ui.hub_view import create_hub_message

        await create_hub_message(
            manager, rt, channel_id, channel, user_id, user_id
        )
    return True


def _screen_file_from_bytes(data: bytes, filename: str) -> discord.File:
    """Wrap upload bytes in a FRESH BytesIO-backed discord.File.

    Every HTTP send needs its own File: discord.py's File.reset(seek=tries)
    skips the seek on a request's FIRST attempt, so reusing one File across
    two requests (e.g. interaction-token edit, then gate fallback) uploads a
    consumed stream at EOF -> Discord stores a 0-byte map.png."""
    buf = io.BytesIO(data)
    buf.seek(0)
    return discord.File(buf, filename=filename)


async def _screen_file(result) -> discord.File:
    """Shrink + encode a RenderResult into a tiny upload-ready discord.File.

    The PIL finalize + encode runs in the bounded render worker pool so a
    burst of frames never blocks interaction handling."""
    data = await run_image_task(_screen_bytes, result)
    return _screen_file_from_bytes(data, result.filename)


def _wx_key_of(rt) -> Optional[str]:
    """Effective weather key for screen renders (None-safe for plain test
    runtimes). Delegates to the scenario FX gate: when it is OFF (default)
    this returns None so every screen renders a fast static PNG instead of
    the 6-frame weather GIF."""
    try:
        from rendering.renderer import effective_weather_key

        return effective_weather_key(rt)
    except Exception:
        return getattr(rt, "weather_key", None)


def _fx_seed_of(rt) -> int:
    """Storm lightning seed of a scenario (0 = calm sky, no strike)."""
    return getattr(rt, "lightning_seed", 0)


def _is_split_screen(screen) -> bool:
    """Whether this player uses the image-only map + separate controls layout.

    A screen whose ``message_id`` was JUST re-minted by the silent repair loop
    (fresh snowflake) with its old controls id still set is ALSO split: the
    controls id is verified/re-created by create_controls_message on the next
    repair round or press. Treating it as legacy here would push the map frame
    through the D-pad's interaction token and stamp a full D-pad onto the map
    message (the "screen+D-pad merged into one message" bug)."""
    return bool(
        getattr(screen, "split_layout", False)
        or getattr(screen, "controls_message_id", None) is not None
    )


class MapView(View):
    """Persistent per-player screen view.

    Each player owns a screen message (map image + their own D-pad) and its
    buttons carry per-player custom_ids (``mg:{channel}:{user}:{key}``) so they
    survive a restart and re-bind via bot.add_view() in setup_hook.

    The press path edits the screen THROUGH THE INTERACTION TOKEN
    (``interaction.response.edit_message``) — a per-interaction endpoint that
    never touches the channel message-edit bucket. That is what makes presses
    near-instant and immune to the PATCH 429 no matter how many players press
    at once. Only background updates (auto-move, hub) go through the spaced
    channel-edit gate.

    ``user_id=None`` is the LEGACY shared screen: pressing any of its buttons
    mints the presser their own personal screen instead of moving.
    """

    def __init__(self, channel_id: int, manager, user_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.manager = manager
        self.user_id = user_id
        self._build()

    def _cid(self, key: str) -> str:
        if self.user_id is None:
            return f"mg:{self.channel_id}:{key}"
        return f"mg:{self.channel_id}:{self.user_id}:{key}"

    def _build(self) -> None:
        # The old reload control keeps its persistent custom_id for already
        # posted views, but now visibly and functionally becomes the attack
        # button. The tool rail remains on the RIGHT of the movement grid.
        self.tool_refresh = Button(emoji="⚔️", row=0, custom_id=self._cid("t_refresh"))
        self.tool_step = Button(emoji="⏏️", row=1, custom_id=self._cid("t_step"))
        self.tool_auto = Button(emoji="🎦", row=2, custom_id=self._cid("t_auto"))
        for key, btn in (
            ("t_refresh", self.tool_refresh),
            ("t_step", self.tool_step),
            ("t_auto", self.tool_auto),
        ):
            btn.callback = self._make_tool_callback(key)

        # Sandbox: 🔨 break (the only fixed block button — placing happens
        # through the hotbar: bind a block to a slot and press it).
        self.b_break = Button(emoji="🔨", row=0, custom_id=self._cid("b_break"))
        self.b_break.callback = self._make_block_callback("b_break")

        # Build tools: 🔁 turn in place (row 1) and 🧱 Build Mode toggle
        # (row 2 — took over the old follow-build slot). The centre d-pad cell
        # doubles as ✅ place-at-cursor while Build Mode is ON.
        self.b_turn = Button(emoji="🔁", row=1, custom_id=self._cid("t_turn"))
        self.b_turn.callback = self._make_tool_callback("t_turn")
        self.b_build = Button(emoji="🧱", row=2, custom_id=self._cid("t_build"))
        self.b_build.callback = self._make_tool_callback("t_build")

        # (row_idx, keys) placed left-to-right; "blank" = the empty centre cell.
        grid = [
            (0, ["nw", "n", "ne"]),
            (1, ["w", "blank", "e"]),
            (2, ["sw", "s", "se"]),
        ]
        for row_idx, keys in grid:
            for key in keys:
                if key == "blank":
                    # Centre cell: inert spacer while moving; becomes the ✅
                    # place-at-cursor button while Build Mode is ON (the label
                    # flip happens in _apply_tool_labels).
                    self.b_place = Button(
                        label="\u200b", disabled=True, row=row_idx,
                        custom_id=self._cid("c_place"),
                    )
                    self.b_place.callback = self._make_block_callback("c_place")
                    self.add_item(self.b_place)
                    continue
                btn = Button(emoji=EMOJI[key], row=row_idx, custom_id=self._cid(key))
                btn.callback = self._make_callback(key)
                self.add_item(btn)
            tool_key = {0: "t_refresh", 1: "t_step", 2: "t_auto"}[row_idx]
            self.add_item({"t_refresh": self.tool_refresh, "t_step": self.tool_step, "t_auto": self.tool_auto}[tool_key])
            if row_idx == 0:
                self.add_item(self.b_break)
            elif row_idx == 1:
                self.add_item(self.b_turn)
            elif row_idx == 2:
                self.add_item(self.b_build)

        self._apply_tool_labels()

        # Hotbar rail (rows 3-4): the 6 hotbar slots — an automatic PROJECTION
        # of the first HOTBAR_SLOTS stacks of the ordered bag (slot N = bag
        # stack N). A bound BLOCK is placed on the facing tile; a bound
        # consumable is used directly — this rail REPLACED the old 🧱/📦
        # block buttons. Order is changed from the inventory panel.
        # Discord rows hold 5 buttons max: 5 slots on row 3, the 6th on row 4.
        self.hotbar_btns = []
        for slot in range(HOTBAR_SLOTS):
            btn = Button(
                label=str(slot + 1),
                row=3 + slot // 5,
                custom_id=self._cid(f"h{slot}"),
            )
            btn.callback = self._make_hotbar_callback(slot)
            self.hotbar_btns.append(btn)
            self.add_item(btn)
        self._apply_hotbar_labels()

    def _make_callback(self, key: str):
        async def cb(interaction: discord.Interaction):
            await self._handle_move(interaction, key)

        return cb

    def _make_tool_callback(self, kind: str):
        async def cb(interaction: discord.Interaction):
            await self._handle_tool(interaction, kind)

        return cb

    def _make_block_callback(self, kind: str):
        async def cb(interaction: discord.Interaction):
            await self._handle_block(interaction, kind)

        return cb

    def _make_hotbar_callback(self, slot: int):
        async def cb(interaction: discord.Interaction):
            await self._handle_hotbar(interaction, slot)

        return cb

    def _apply_hotbar_labels(self) -> None:
        """Mirror the player's hotbar (a projection of the first HOTBAR_SLOTS
        bag stacks) onto the D-pad buttons: item emoji + remaining count;
        empty slots stay disabled with their slot number."""
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None or not hasattr(self, "hotbar_btns"):
            return
        inv = rt.inventories.get(self.user_id) if self.user_id is not None else None
        hotbar = inv.hotbar() if inv is not None else {s: None for s in range(HOTBAR_SLOTS)}
        for slot, btn in enumerate(self.hotbar_btns):
            iid = hotbar.get(slot)
            item = get_item(iid) if iid else None
            if item is None or (inv is not None and inv.count(iid) <= 0):
                btn.emoji = None
                btn.label = str(slot + 1)
                btn.disabled = True
                continue
            btn.emoji = item.emoji
            btn.label = f"x{inv.count(iid) if inv is not None else 0}"
            btn.disabled = False

    def _target_tile_of(self, rt, uid: int):
        """The square the facing/aim highlight marks: the Build-Mode aim
        cursor tile while one is active, otherwise the facing tile."""
        p = getattr(rt, "state", None)
        p = rt.state.get_player(uid) if rt is not None and hasattr(rt, "state") else None
        if p is None:
            return None
        if getattr(p, "aim_active", False):
            return (p.x + p.aim_dx, p.y + p.aim_dy)
        try:
            dx, dy = Direction[p.direction].vector
        except (KeyError, TypeError):
            return None
        return (p.x + dx, p.y + dy)

    def _apply_axe_labels(self) -> None:
        """The 🔨 break button morphs by what the TARGET SQUARE holds:

        - a tree/bush -> 🪓 + progress %
        - an ore/rock node -> ⛏️ + progress %
        - a scoopable grass tuft -> 🥄 (shovel) + progress %
        - anything else -> plain 🔨 (break placed blocks)

        The target square is exactly the one the renderer highlights, so the
        button never promises a different tile than the one marked on screen.
        This label update runs on EVERY button build AND after any state
        refresh (the fix for "đứng gần cây nhưng không thấy hiện rìu": the
        morph no longer waits for an interaction — movement/tool/aim refresh
        paths all call _apply_tool_labels, which calls this)."""
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None or not hasattr(self, "b_break"):
            return
        btn = self.b_break
        uid = self.user_id
        state = getattr(rt, "state", None)
        resources = getattr(rt, "resources", None)
        if state is None or resources is None or uid is None:
            btn.emoji = "🔨"
            btn.label = ""
            btn.disabled = False
            return
        tile = self._target_tile_of(rt, uid)
        node = resources.node_at(*tile) if tile is not None else None
        if node is not None and not resources.is_chopped(node.anchor):
            from game.tools import axe_hits, pickaxe_hits

            ore = is_ore_kind(node.kind)
            hits = NODE_DEFS[node.kind].hits
            if ore:
                from game.tools import best_tool_of_family, parse_tool_id

                tool = parse_tool_id(best_tool_of_family(rt.inventories.get(uid), "pickaxe"))
                effective = pickaxe_hits(tool.material) if tool else hits
            else:
                from game.tools import best_tool_of_family as _b, parse_tool_id as _p

                tool = _p(_b(rt.inventories.get(uid), "axe"))
                effective = axe_hits(tool.material) if tool else hits
            pct = int(round(resources.progress_at(node.anchor) / max(1, effective) * 100))
            btn.emoji = "⛏️" if ore else "🪓"
            btn.label = f"{pct}%"
            btn.disabled = False
            return
        # Scoopable grass tuft on the target square: shovel morph.
        terrain = getattr(rt, "terrain", None)
        if terrain is not None and tile is not None and terrain.scoopable(*tile):
            from game.tools import best_tool_of_family as _b, parse_tool_id as _p, shovel_hits

            tool = _p(_b(rt.inventories.get(uid), "shovel"))
            hits = shovel_hits(tool.material if tool else "dirt")
            from game.terrain_rules import scoop_progress_at

            pct = int(round(scoop_progress_at(state, *tile) / max(1, hits) * 100))
            btn.emoji = "🥄"
            btn.label = f"{pct}%" if pct else ""
            btn.disabled = False
            return
        btn.emoji = "🔨"
        btn.label = ""
        btn.disabled = False

    def _attack_emoji(self, rt) -> str:
        """⚔️ when the player effectively holds a weapon, 👊 when bare-handed.

        Mirrors the rule layer's check (first bound hotbar slot carrying a
        weapon-class item with stock in the bag) so the button never promises
        more damage than the next ⚔️ press will deliver."""
        from game.items import WEAPON_ITEM_IDS

        if self.user_id is None:
            return "⚔️"
        inv = rt.inventories.get(self.user_id)
        for _slot, iid in (inv.hotbar() if inv is not None else {}).items():
            if iid in WEAPON_ITEM_IDS and (inv is None or inv.count(iid) > 0):
                return "⚔️"
        return "👊"

    # ----- small helpers -----

    def _stop_auto(self, screen) -> None:
        screen.auto_running = False
        screen.auto_armed = False
        if screen.auto_task is not None and not screen.auto_task.done():
            screen.auto_task.cancel()
        screen.auto_task = None

    async def _refresh_controls_message(self, interaction, screen) -> None:
        """Publish current button labels to the separate controls message.

        This is component-only and deliberately does not upload the map image.
        It is best-effort because movement must never wait for a cosmetic label
        refresh (hotbar/axe/build state).
        """
        if not _is_split_screen(screen) or screen.controls_message_id is None:
            return
        try:
            await interaction.channel.get_partial_message(
                screen.controls_message_id
            ).edit(view=self)
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[CONTROLS] refresh failed for user %s: %s", screen.user_id, e)

    def _apply_tool_labels(self) -> None:
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            return
        # ⚔️ while a weapon-class item is bound+in bag; 👊 bare hands.
        self.tool_refresh.emoji = self._attack_emoji(rt)
        self.tool_step.emoji = "⏏️"
        self.tool_auto.label = ""
        screen = rt.screens.get(self.user_id) if self.user_id is not None else None
        if screen is not None:
            # ⏏️ keeps its icon; the step count is a text label (e.g. "x3").
            self.tool_step.label = f"x{screen.step_size}"
            if screen.auto_running:
                self.tool_auto.emoji = "▶️"
            elif screen.auto_armed:
                self.tool_auto.emoji = "🎬"
            else:
                self.tool_auto.emoji = "🎦"
        else:
            # Legacy shared-screen fallback (fields may be absent on fakes).
            self.tool_step.label = f"x{getattr(rt, 'step_size', 1)}"
            if getattr(rt, "auto_running", False):
                self.tool_auto.emoji = "▶️"
            elif getattr(rt, "auto_armed", False):
                self.tool_auto.emoji = "🎬"
            else:
                self.tool_auto.emoji = "🎦"
        # Build tools (state-aware; safe when screen is None / legacy fakes).
        build_mode = bool(getattr(screen, "build_mode", False))
        self.b_build.emoji = "🧱"
        self.b_build.label = "ON" if build_mode else ""
        if hasattr(self, "b_place"):
            if build_mode:
                self.b_place.emoji = "✅"
                self.b_place.label = ""
            else:
                self.b_place.emoji = None
                self.b_place.label = "\u200b"
            self.b_place.disabled = not build_mode
        # Tree/bush facing reflects on the 🔨 -> 🪓 tool button.
        self._apply_axe_labels()

    def _schedule_hub(self, interaction: discord.Interaction, focused_user_id: int) -> None:
        coalescer = getattr(self.manager, "hub_coalescer", None)
        if coalescer is not None:
            coalescer.schedule(
                (self.channel_id, focused_user_id),
                {"interaction": interaction, "focused_user_id": focused_user_id},
            )

    async def _ensure_controls_message(self, rt, screen, channel, view=None) -> bool:
        """Ensure this screen's separate D-pad message exists."""
        if screen.controls_message_id is not None:
            screen.split_layout = True
            return True
        return await create_controls_message(
            self.manager, rt, self.channel_id, channel, screen.user_id, view=view
        )

    async def _ack_controls(self, interaction: discord.Interaction) -> None:
        """ACK the controls interaction without touching the map attachment."""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _create_screen_for(self, interaction: discord.Interaction, rt, user_id: int) -> None:
        """Mint a personal screen for ``user_id`` and reply with it.

        Used by /joinmap-style flows AND by presses on a legacy shared screen
        (pressing an old shared D-pad gives the presser their own screen)."""
        user = interaction.user
        rt.members[user_id] = user
        if user_id not in rt.state.players:
            rt.state.add_player(user_id, user.display_name, *rt.map_data.spawn)
            await self.manager.renderer.avatar_cache.get_avatar(user, label=user.display_name[:1])
        screen = self.manager.ensure_screen(rt, user_id)
        # Re-apply the remembered movement preference (1/3/5) on re-mint.
        player = rt.state.get_player(user_id)
        if player is not None:
            screen.step_size = player.step_size
        _focus_screen_camera(rt, screen, user_id)
        result = await self.manager.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=screen.camera,
            full=True, focus_user_id=user_id, weather_key=_wx_key_of(rt),
            fx_seed=_fx_seed_of(rt),
            **resource_render_kwargs(rt),
            **terrain_render_kwargs(rt),
        )
        screen.composite = result.composite
        # The map message is deliberately image-only. Controls are posted as a
        # second message immediately afterwards, then the hub below them.
        view = MapView(self.channel_id, self.manager, user_id)
        screen.map_view = view
        await interaction.response.send_message(file=await _screen_file(result))
        msg = await interaction.original_response()
        screen.message_id = msg.id
        await create_controls_message(
            self.manager, rt, self.channel_id, interaction.channel, user_id, view=view
        )
        player = rt.state.get_player(user_id)
        if player is not None:
            player.screen_message_id = msg.id
            from persistence.repositories import save_player

            if self.manager.db is not None:
                await save_player(self.manager.db, self.channel_id, player)
        if getattr(self.manager, "bot_ref", None) is not None:
            self.manager.bot_ref.add_view(view)
        # Create the player's personal hub under the new screen.
        from discord_ui.hub_view import create_hub_message

        await create_hub_message(self.manager, rt, self.channel_id, interaction.channel, user_id, user_id)
        self._schedule_hub(interaction, user_id)

    async def _instant_frame(
        self, interaction: discord.Interaction, rt, screen, user_id: int
    ) -> None:
        """Render one frame and publish it to the image-only map message.

        Split screens do not use the controls interaction token for this edit:
        that token belongs to the D-pad message. The split press path therefore
        queues this function through ``flush_render_batch`` and this direct
        method remains the legacy combined-message fallback.
        """
        self._apply_axe_labels()
        result = await self.manager.renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=screen.camera,
            full=True, focus_user_id=user_id, weather_key=_wx_key_of(rt),
            fx_seed=_fx_seed_of(rt),
            indicator_dim=screen.auto_running or screen.travel_steps >= 4,
            **resource_render_kwargs(rt),
            **terrain_render_kwargs(rt),
        )
        screen.composite = result.composite
        data = await run_image_task(_screen_bytes, result)
        try:
            # The map message stays image-only on a split screen: never
            # re-attach the D-pad view to the map image here (this includes
            # the legacy combined fallback below — on a split screen the
            # interaction token edits the CONTROLS message, so pushing the
            # map frame through it stamps a full D-pad onto the map GIF).
            if _is_split_screen(screen):
                # FIX "đứng gần cây không thấy hiện rìu": every frame edit
                # also re-publishes the (morphed) tool button labels to the
                # separate controls message — a component-only edit on the
                # gate, so the 🪓/⛏️/🥄 morph arrives WITH the frame.
                await self.manager.edit_gate.edit_message(
                    interaction.channel, screen.message_id,
                    attachments=[_screen_file_from_bytes(data, result.filename)],
                    view=self,
                )
                await self._push_labels_via_gate(interaction, screen)
                return
            if interaction.response.is_done():
                await interaction.edit_original_response(
                    attachments=[_screen_file_from_bytes(data, result.filename)],
                    view=self,
                )
            else:
                await interaction.response.edit_message(
                    attachments=[_screen_file_from_bytes(data, result.filename)],
                    view=self,
                )
        except discord.NotFound:
            # The map message was deleted: hand the whole stack to the silent
            # repair loop (screen -> controls -> hub, retried up to 15s).
            log.info("[SCREEN] instant-frame NotFound; scheduling silent repair")
            from discord_ui.session_recovery import schedule_repair

            schedule_repair(self.manager, rt, user_id, why="screen 404 (instant frame)")
        except discord.HTTPException as e:
            log.warning("[SCREEN] instant frame edit failed: %s", e)
            from discord_ui.session_recovery import schedule_repair

            schedule_repair(self.manager, rt, user_id, why=f"screen edit {e!r}")

    async def _push_labels_via_gate(self, interaction, screen) -> None:
        """Component-only label refresh on the CONTROLS message through the
        spaced gate (used by frame paths that must carry the tool-button
        morph without touching the map image)."""
        if screen.controls_message_id is None:
            return
        try:
            await self.manager.edit_gate.edit_message(
                interaction.channel, screen.controls_message_id, view=self,
            )
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SCREEN] controls label push failed: %s", e)

    async def _render_and_respond(
        self, interaction: discord.Interaction, rt, screen, user_id: int
    ) -> None:
        """Queue a latest-state frame without holding up a controls press."""
        self._schedule_hub(interaction, user_id)
        if not _is_split_screen(screen):
            # One-time migration: mint the separate D-pad message (+ re-seat
            # the hub under it) so presses stop waiting for image uploads.
            # Best-effort: on failure the legacy path below still runs.
            channel = getattr(interaction, "channel", None)
            if channel is not None:
                try:
                    await create_controls_message(
                        self.manager, rt, self.channel_id, channel, user_id,
                        view=self,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("[CONTROLS] migration on press failed: %s", e)
        if _is_split_screen(screen):
            # ACK instantly on the callback endpoint (no attachment, no edit
            # bucket). The frame itself is published to the MAP message by
            # flush_render_batch through the spaced gate — the controls
            # interaction token can never edit the separate map message.
            await self._ack_controls(interaction)
            coalescer = getattr(self.manager, "coalescer", None)
            if coalescer is not None:
                coalescer.schedule(
                    (self.channel_id, user_id),
                    {"interaction": interaction, "view": self, "user_id": user_id},
                )
            else:
                # Test/minimal runtimes may not install the app-wide coalescer;
                # keep the action usable rather than dropping its frame.
                asyncio.create_task(
                    flush_render_batch(
                        self.manager, (self.channel_id, user_id),
                        [{"interaction": interaction, "view": self, "user_id": user_id}],
                    )
                )
            return

        # Legacy combined message: retain the original interaction-token fast
        # path for old screens until they are migrated to split layout.
        if screen.rendering:
            try:
                await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException):
                pass
            coalescer = getattr(self.manager, "coalescer", None)
            if coalescer is not None:
                coalescer.schedule(
                    (self.channel_id, user_id),
                    {"interaction": interaction, "view": self, "user_id": user_id},
                )
            return
        screen.rendering = True
        try:
            await self._instant_frame(interaction, rt, screen, user_id)
        finally:
            screen.rendering = False

    # ----- tool bar -----

    async def on_error(self, interaction: discord.Interaction, error: Exception,
                       item: discord.ui.Item) -> None:
        """Safety net: a button callback crashed. NO channel notice (a live
        session must never look shut down) — log, then make sure the stack is
        still complete via the silent repair loop."""
        log.exception("[SCREEN] button handler crashed (user=%s)", self.user_id)
        try:
            rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
            if rt is not None and self.user_id is not None:
                from discord_ui.session_recovery import schedule_repair

                schedule_repair(self.manager, rt, self.user_id,
                                why=f"button error {error!r}")
        except Exception:  # noqa: BLE001 — the net must never raise
            pass
        if interaction is not None and not interaction.response.is_done():
            try:
                await interaction.response.send_message(
                    "Đã xảy ra lỗi khi xử lý nút bấm (đã báo về kênh).",
                    ephemeral=True, delete_after=EPHEMERAL_WARN,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

    async def _remember_step_size(self, screen, uid: int) -> None:
        """Mirror the chosen step size onto the persisted Player row.

        The preference must survive restarts (and screen re-mints), so it
        lives on the Player; the screen is the live copy. A debounced save
        keeps the change durable without blocking the press.
        """
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            return
        player = rt.state.get_player(uid)
        if player is None or player.step_size == screen.step_size:
            return
        player.step_size = screen.step_size
        if self.manager.db is not None:
            self.manager._schedule_save(rt, player)

    async def _handle_tool(self, interaction: discord.Interaction, kind: str) -> None:
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        if self.user_id is not None and interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Đây là màn hình riêng của người chơi khác. Dùng /joinmap để có màn hình của bạn.",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return
        if self.user_id is None:
            # Legacy shared screen: mint the presser a personal screen.
            await self._create_screen_for(interaction, rt, interaction.user.id)
            return
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is None:
            await self._create_screen_for(interaction, rt, uid)
            return
        self.manager.touch_session(self.channel_id, uid)

        if kind == "t_refresh":
            # Backwards-compatible custom_id, new meaning: attack the nearest
            # hostile. With NO hostile around, the rule layer falls back to
            # breaking the block on the target square (the highlighted tile).
            self._stop_auto(screen)
            screen.travel_steps = 0
            _, result = await self.manager.dispatch(
                self.channel_id, AttackAction(uid)
            )
            if not result.state_changed:
                text = {
                    "no_player": "Bạn chưa tham gia map.",
                    "invisible": "Bạn đang ẩn mình.",
                    "no_target": "Không có zombie hay khối nào trong tầm đánh.",
                }.get(result.reason, "Không thể tấn công.")
                try:
                    await interaction.response.send_message(
                        text, ephemeral=True, delete_after=EPHEMERAL_WARN
                    )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            _focus_screen_camera(rt, screen, uid)
            if result.damage == 0:
                # Block-break fallback: material flies back to the bag.
                await self._play_pickup(interaction, rt, screen, uid, result)
                return
            await self._render_and_respond(interaction, rt, screen, uid)
            defeated = " (đã hạ gục)" if result.target_defeated else ""
            loot = ""
            if result.drops:
                from game.items import get_item as _get_item
                loot = " Nhặt: " + ", ".join(
                    f"{_get_item(iid).emoji if _get_item(iid) else '❓'}x{qty}"
                    for iid, qty in result.drops
                )
            try:
                hit_emoji = self._attack_emoji(rt)
                # Kaetram parity: a crit hit shows 💥, a whiff shows MISS.
                if getattr(result, "missed", False):
                    hit_note = " (TRƯỢT — đòn đánh không trúng)"
                elif getattr(result, "critical", False):
                    hit_emoji = "💥"
                    hit_note = " (CHÍ MẠNG!)"
                else:
                    hit_note = ""
                await send_ephemeral_followup(
                    interaction,
                    f"{hit_emoji} Đánh zombie -{result.damage} HP{defeated}{hit_note}.{loot}",
                    EPHEMERAL_ACTION,
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        # t_step / t_auto: label/state-only component edit (no image upload) via
        # the interaction token — instant and free of the edit bucket.
        if kind == "t_step":
            self._stop_auto(screen)
            # Cycle step form: 1 -> 3 -> 5 -> 1 blocks per click.
            screen.step_size = {1: 3, 3: 5, 5: 1}.get(screen.step_size, 1)
            await self._remember_step_size(screen, uid)
        elif kind == "t_auto":
            if screen.auto_running:
                self._stop_auto(screen)
            elif screen.auto_armed:
                screen.auto_armed = False
            else:
                screen.auto_armed = True

        elif kind == "t_turn":
            # 🔁 rotate in place — cycles clockwise through the 8 directions
            # WITHOUT stepping (the only deliberate no-move rotation).
            self._stop_auto(screen)
            screen.travel_steps = 0
            player = rt.state.get_player(uid)
            if player is not None and player.direction in Direction.__members__:
                _, result = await self.manager.dispatch(
                    self.channel_id,
                    TurnAction(uid, next_clockwise(Direction[player.direction])),
                )
                if result.state_changed:
                    _focus_screen_camera(rt, screen, uid)
                    await self._render_and_respond(interaction, rt, screen, uid)
                    return
            # No player / already facing that way: cheap ACK, no re-upload.
            try:
                await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        elif kind == "t_build":
            # 🧱 Build Mode toggle: ON = D-pad drives the aim cursor and the
            # centre cell becomes ✅ place. Seeding the cursor on the facing
            # tile gives ✅ a valid target the moment the mode turns on.
            self._stop_auto(screen)
            screen.travel_steps = 0
            screen.build_mode = not screen.build_mode
            player = rt.state.get_player(uid)
            if screen.build_mode:
                if player is not None:
                    player.aim_block = screen.selected_block
                    fdx, fdy = Direction[player.direction].vector
                    await self.manager.dispatch(self.channel_id, AimAction(uid, fdx, fdy))
            elif player is not None:
                await self.manager.dispatch(self.channel_id, AimResetAction(uid))
            self._apply_tool_labels()
            # Split layout: the label lives on the separate D-pad message —
            # component-only edit so 🧱 ON flips instantly (the map render
            # below never carries the controls view).
            await self._refresh_controls_message(interaction, screen)
            _focus_screen_camera(rt, screen, uid)
            await self._render_and_respond(interaction, rt, screen, uid)
            return

        self._apply_tool_labels()
        try:
            await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SCREEN] tool edit failed: %s", e)

    # ----- sandbox blocks -----

    async def _handle_block(self, interaction: discord.Interaction, kind: str) -> None:
        """🔨 break / 🪓 / ⛏️ — acts on the TARGET SQUARE (the tile the facing/
        aim highlight marks); the drop flies to the nearest player as a
        one-shot pickup animation before entering the bag."""
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        if self.user_id is not None and interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Đây là màn hình riêng của người chơi khác. Dùng /joinmap để có màn hình của bạn.",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return
        if self.user_id is None:
            # Legacy shared screen: mint the presser a personal screen.
            await self._create_screen_for(interaction, rt, interaction.user.id)
            return
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is None:
            await self._create_screen_for(interaction, rt, uid)
            return
        self.manager.touch_session(self.channel_id, uid)
        rt.members[uid] = interaction.user
        if uid not in rt.state.players:
            rt.state.add_player(uid, interaction.user.display_name, *rt.map_data.spawn)
        screen.travel_steps = 0  # aiming/resting: indicators back to full

        self._stop_auto(screen)

        if kind == "b_break":
            # The tool button morphs by the TARGET SQUARE (the highlighted
            # tile): tree/bush -> 🪓 chop, ore/rock -> ⛏️ mine, grass tuft
            # -> 🥄 scoop (reveals bare dirt + grants dirt).
            tile = self._target_tile_of(rt, uid)
            node = rt.resources.node_at(*tile) if (tile is not None and rt.resources) else None
            if node is not None and not rt.resources.is_chopped(node.anchor):
                await self._handle_chop(interaction, rt, screen, uid)
                return
            terrain = getattr(rt, "terrain", None)
            if terrain is not None and tile is not None and terrain.scoopable(*tile):
                await self._handle_scoop(interaction, rt, screen, uid)
                return

        if kind == "c_place":
            # ✅ Build Mode: place the selected block on the aim-cursor tile.
            await self._handle_place(interaction, rt, screen, uid)
            return

        _, result = await self.manager.dispatch(self.channel_id, BreakBlockAction(uid))

        if not result.state_changed:
            text = BLOCK_REASON_TEXT.get(result.reason, "Không thực hiện được.")
            if result.reason == "too_hard":
                text = "Quá cứng để đập bằng tay."
            try:
                await interaction.response.send_message(
                    text, ephemeral=True, delete_after=EPHEMERAL_WARN
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        _focus_screen_camera(rt, screen, uid)
        await self._play_pickup(interaction, rt, screen, uid, result)

    async def _handle_chop(self, interaction: discord.Interaction,
                           rt, screen, uid: int) -> None:
        """One 🪓/⛏️ swing at the node on the target square.

        Progress stays visible as a % on the tool button (component edit, no
        upload). The felling swing fades the node out as a one-shot GIF, then
        the drops land in the bag and the screen reloads static.
        """
        import io as _io
        from PIL import Image as PILImage

        _, result = await self.manager.dispatch(self.channel_id, ChopAction(uid))
        if not result.state_changed:
            self._apply_axe_labels()
            text = CHOP_REASON_TEXT.get(result.reason, "Không chặt được.")
            if result.reason == "too_hard":
                text = "Quá cứng để đập bằng tay."
            try:
                await interaction.response.send_message(
                    text, ephemeral=True, delete_after=EPHEMERAL_WARN
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        await self._handle_chop_result(interaction, rt, screen, uid, result,
                                       _io=_io, PILImage=PILImage)

    async def _handle_chop_result(self, interaction, rt, screen, uid, result,
                                  _io=None, PILImage=None) -> None:
        """Shared post-dispatch path for chop swings (tool button AND hotbar
        tool presses): progress % update, felling GIF, drop announcement."""
        import io as _io_default
        from PIL import Image as PILImage_default

        _io = _io or _io_default
        PILImage = PILImage or PILImage_default

        if result.drops is None:
            # Progress swing: tree still standing, just update the axe %.
            self._apply_axe_labels()
            self._apply_hotbar_labels()
            try:
                await interaction.response.edit_message(view=self)
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[SCREEN] chop progress edit failed: %s", e)
            return

        # ---- felling swing ----
        self._apply_hotbar_labels()
        self._after_inventory_change(uid)
        _focus_screen_camera(rt, screen, uid)

        renderer = self.manager.renderer
        tile = renderer.tile_size
        cam = screen.camera
        follow = cam is not None and getattr(cam, "follow", False)
        zoom = float(getattr(cam, "zoom", 1.0) or 1.0) if follow else 1.0
        x0 = y0 = 0
        if follow:
            x0, y0 = cam.top_left(rt.map_data.width, rt.map_data.height)

        tx, ty = result.pos
        node = rt.resources.node_at(tx, ty) if rt.resources else None

        await self._finish_chop(
            interaction, rt, screen, uid, renderer, tile, zoom, x0, y0,
            tx, ty, node, follow, cam, _io, PILImage,
        )

        if result.drops:
            from game.items import get_item as _get_item

            lines = ", ".join(
                f"{_get_item(iid).emoji if _get_item(iid) else '❓'}x{q}"
                for iid, q in result.drops
            )
            try:
                await send_ephemeral_followup(
                    interaction, f"🪓 Chặt xong! Nhận: {lines}", EPHEMERAL_ACTION,
                )
            except (discord.NotFound, discord.HTTPException):
                pass

    async def _handle_scoop(self, interaction: discord.Interaction,
                            rt, screen, uid: int) -> None:
        """One 🥄 scoop swing at the grass tuft on the target square.

        Progress stays visible as a % on the tool button. The final press
        removes the tuft (the bare-dirt tile shows through) and grants 1
        dirt, announced as a transient ephemeral.
        """
        from game.actions import ShovelAction

        _, result = await self.manager.dispatch(self.channel_id, ShovelAction(uid))
        if not result.state_changed:
            self._apply_axe_labels()
            text = SHOVEL_REASON_TEXT.get(result.reason, "Không xúc được.")
            try:
                await interaction.response.send_message(
                    text, ephemeral=True, delete_after=EPHEMERAL_WARN
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        if result.drops is None:
            # Progress swing: tuft still standing, just update the %.
            self._apply_axe_labels()
            self._apply_hotbar_labels()
            try:
                await interaction.response.edit_message(view=self)
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[SCREEN] scoop progress edit failed: %s", e)
            return

        # ---- felling scoop: refresh screen, grant announcement ----
        self._apply_axe_labels()
        self._apply_hotbar_labels()
        self._after_inventory_change(uid)
        _focus_screen_camera(rt, screen, uid)
        await self._render_and_respond(interaction, rt, screen, uid)
        try:
            await send_ephemeral_followup(
                interaction, "🥄 Đã xúc cỏ! Nhận: 🟤x1 (Đất)", EPHEMERAL_ACTION,
            )
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _finish_chop(
        self, interaction, rt, screen, uid, renderer, tile, zoom,
        x0, y0, tx, ty, node, follow, cam, _io, PILImage,
    ) -> None:
        """Build + upload the fade-out GIF (or plain fallback frame)."""
        # The node's exact sprite tiles (grid -> final-frame pixels), resized
        # to follow-zoom so the fade matches the on-screen tree.
        parts = []
        if node is not None:
            for gx, gy, gid in rt.resources.node_tiles_gids(node):
                img = renderer._resource_tile_image(rt.map_data, gid)
                if img is None:
                    continue
                if zoom != 1.0:
                    sz = max(4, int(tile * zoom))
                    img = img.resize((sz, sz), PILImage.NEAREST)
                parts.append(
                    (int(((gx - x0) * tile) * zoom), int(((gy - y0) * tile) * zoom), img)
                )

        # Frame WITHOUT the node (it is now chopped in the grid).
        render = await renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=cam,
            full=True, focus_user_id=uid, weather_key=_wx_key_of(rt),
            fx_seed=_fx_seed_of(rt),
            indicator_dim=screen.auto_running or screen.travel_steps >= 4,
            **resource_render_kwargs(rt),
            **terrain_render_kwargs(rt),
        )

        in_view = False
        if follow:
            in_view = x0 <= tx < x0 + cam.view_w and y0 <= ty < y0 + cam.view_h
        else:
            in_view = 0 <= tx < rt.map_data.width and 0 <= ty < rt.map_data.height

        duration_ms = 120
        if parts and in_view:
            frames = await run_image_task(build_chop_frames, render.image, parts, 8)
            data = await run_image_task(encode_upload_gif, frames, duration_ms, 1)
            screen.composite = None
            await self._edit_map_frame(
                interaction, rt, screen,
                discord.File(_io.BytesIO(data), filename="chop.gif"),
            )
            anim_secs = len(frames) * duration_ms / 1000 + 0.25
        else:
            # Node off-screen / no fade parts: plain static swap.
            screen.composite = render.composite
            await self._edit_map_frame(
                interaction, rt, screen, await _screen_file(render)
            )
            anim_secs = 0.5

        asyncio.create_task(self._finish_pickup(rt, screen, uid, anim_secs))

    async def _handle_place(self, interaction, rt, screen, uid: int) -> None:
        """✅ Build Mode: place screen.selected_block on the aim-cursor tile."""
        player = rt.state.get_player(uid)
        block_id = screen.selected_block
        dx = dy = None
        if player is not None:
            player.aim_block = block_id  # ghost preview mirrors the placement
            dx, dy = player.aim_dx, player.aim_dy
        _, result = await self.manager.dispatch(
            self.channel_id, PlaceBlockAction(uid, block_id, dx=dx, dy=dy)
        )
        if not result.state_changed:
            text = BLOCK_REASON_TEXT.get(result.reason, "Không đặt được.")
            try:
                await interaction.response.send_message(
                    text, ephemeral=True, delete_after=EPHEMERAL_WARN
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return
        self._apply_hotbar_labels()
        self._after_inventory_change(uid)
        _focus_screen_camera(rt, screen, uid)
        await self._render_and_respond(interaction, rt, screen, uid)

    # ----- pickup animation -----
    async def _edit_map_frame(self, interaction, rt, screen, file: discord.File,
                              view=None) -> None:
        """Push an animation/static frame to the CORRECT message.

        On a split screen the interaction token belongs to the D-pad message,
        so editing through it would stamp the map GIF (and a full D-pad view)
        onto the controls message — that is exactly the "D-pad spawns its own
        screen" bug. Split screens edit the stored MAP message through the
        spaced gate instead; legacy combined screens keep the instant
        interaction-token edit."""
        if _is_split_screen(screen):
            channel = getattr(interaction, "channel", None)
            if channel is None:
                bot = getattr(self.manager, "bot_ref", None)
                channel = bot.get_channel(self.channel_id) if bot is not None else None
            if channel is None:
                return
            try:
                await self.manager.edit_gate.edit_message(
                    channel, screen.message_id, attachments=[file],
                )
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[SCREEN] map frame edit failed: %s", e)
            return
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(attachments=[file], view=view or self)
            else:
                await interaction.response.edit_message(attachments=[file], view=view or self)
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SCREEN] frame edit failed: %s", e)

    async def _play_pickup(self, interaction: discord.Interaction,
                           rt, screen, uid: int, result) -> None:
        """One-shot break→drop→pickup GIF; the item only "appears" in the
        hub HUD / open inventory panel after the animation ends."""
        import io
        from PIL import Image as PILImage

        from rendering.renderer import build_pickup_frames, encode_upload_gif

        renderer = self.manager.renderer
        tile = renderer.tile_size
        tx, ty = result.pos
        cam = screen.camera
        _focus_screen_camera(rt, screen, uid)
        follow = cam is not None and getattr(cam, "follow", False)
        zoom = float(getattr(cam, "zoom", 1.0) or 1.0) if follow else 1.0
        if follow:
            x0, y0 = cam.top_left(rt.map_data.width, rt.map_data.height)
        else:
            x0 = y0 = 0  # non-follow: the frame is the whole map at zoom 1

        def cell_px(gx: int, gy: int) -> tuple:
            """Tile-grid coords -> pixels on the FINAL frame (post-zoom)."""
            return (
                int(((gx - x0) * tile + tile // 2) * zoom),
                int(((gy - y0) * tile + tile // 2) * zoom),
            )

        if follow:
            in_view = (
                x0 <= tx < x0 + cam.view_w and y0 <= ty < y0 + cam.view_h
            )
        else:
            in_view = 0 <= tx < rt.map_data.width and 0 <= ty < rt.map_data.height

        # Nearest alive player to the broken tile (usually the breaker).
        best, best_d = None, None
        for p in rt.state.get_visible_players():
            d = (p.x - tx) ** 2 + (p.y - ty) ** 2
            if best_d is None or d < best_d:
                best, best_d = p, d

        # Post-break frame: ground revealed, item flying toward the player.
        render = await renderer.render(
            rt.state, rt.map_data, members=rt.members, camera=cam,
            full=True, focus_user_id=uid, weather_key=_wx_key_of(rt),
            indicator_dim=screen.auto_running or screen.travel_steps >= 4,
            **resource_render_kwargs(rt),
            **terrain_render_kwargs(rt),
        )

        duration_ms = 120
        if in_view and best is not None:
            item_img = renderer._block_tile(result.block_id)
            if item_img is not None:
                # The base frame is already zoomed in follow mode: scale the
                # sprite with the camera too, or it flies/shrinks wrongly.
                if zoom != 1.0:
                    sprite_px = max(4, int(tile * zoom))
                    item_img = item_img.resize((sprite_px, sprite_px), PILImage.NEAREST)
                from_px = cell_px(tx, ty)
                to_px = cell_px(best.x, best.y)
                frames = await run_image_task(
                    build_pickup_frames, render.image, from_px, to_px,
                    item_img, 12, max(4, int(tile * zoom)),
                )
                data = await run_image_task(encode_upload_gif, frames, duration_ms, 1)
                screen.composite = None
                self._apply_hotbar_labels()
                await self._edit_map_frame(
                    interaction, rt, screen,
                    discord.File(io.BytesIO(data), filename="pickup.gif"),
                )
                # After the animation: reload the screen with a static frame
                # (kills client-side looping), then the bag shows the item.
                anim_secs = len(frames) * duration_ms / 1000 + 0.25
                asyncio.create_task(self._finish_pickup(rt, screen, uid, anim_secs))
                return

        # Fallback (no camera / drop out of view): plain render.
        screen.composite = render.composite
        self._apply_hotbar_labels()
        await self._edit_map_frame(
            interaction, rt, screen, await _screen_file(render)
        )
        asyncio.create_task(self._finish_pickup(rt, screen, uid, 0.5))

    async def _finish_pickup(self, rt, screen, uid: int, delay: float) -> None:
        """After the pickup animation ends: reload the screen with a static
        frame (so the GIF cannot loop), then the hub HUD + open inventory
        panel show the newly collected item."""
        await asyncio.sleep(delay)
        try:
            _focus_screen_camera(rt, screen, uid)
            renderer = self.manager.renderer
            render = await renderer.render(
                rt.state, rt.map_data, members=rt.members,
                camera=screen.camera, full=True, focus_user_id=uid,
                weather_key=_wx_key_of(rt),
                indicator_dim=screen.auto_running or screen.travel_steps >= 4,
                **resource_render_kwargs(rt),
            **terrain_render_kwargs(rt),
            )
            screen.composite = render.composite
            # The map message stays image-only on a split screen: never
            # re-attach the D-pad view to the map image here.
            bot = getattr(self.manager, "bot_ref", None)
            channel = bot.get_channel(self.channel_id) if bot is not None else None
            view = None if _is_split_screen(screen) else (
                getattr(screen, "map_view", None) or self
            )
            await self.manager.edit_gate.edit_message(
                channel, screen.message_id,
                attachments=[await _screen_file(render)], view=view,
            )
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SCREEN] pickup reload failed: %s", e)
        coalescer = getattr(self.manager, "hub_coalescer", None)
        if coalescer is not None:
            coalescer.schedule((self.channel_id, uid), {"focused_user_id": uid})
        try:
            from discord_ui.inventory_view import refresh_if_open

            bot = getattr(self.manager, "bot_ref", None)
            channel = bot.get_channel(self.channel_id) if bot is not None else None
            if channel is not None:
                await refresh_if_open(self.manager, channel, uid)
        except Exception as e:  # noqa: BLE001 — background refresh must never raise
            log.warning("[SCREEN] delayed inventory refresh failed: %s", e)

    # ----- hotbar -----

    async def _handle_hotbar(self, interaction: discord.Interaction, slot: int) -> None:
        """Press one of the 9 hotbar buttons = use the bound inventory item."""
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        if self.user_id is not None and interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Đây là màn hình riêng của người chơi khác. Dùng /joinmap để có màn hình của bạn.",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return
        if self.user_id is None:
            await self._create_screen_for(interaction, rt, interaction.user.id)
            return
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is None:
            await self._create_screen_for(interaction, rt, uid)
            return
        self.manager.touch_session(self.channel_id, uid)
        rt.members[uid] = interaction.user
        if uid not in rt.state.players:
            rt.state.add_player(uid, interaction.user.display_name, *rt.map_data.spawn)
        screen.travel_steps = 0

        inv = rt.inventories.get(uid)
        bound = (inv.hotbar() if inv is not None else {}).get(slot)
        if not bound:
            await interaction.response.send_message(
                f"Ô {slot + 1} trống — mở 🎒 Inventory trên hub, chọn item rồi "
                f"gắn vào ô {slot + 1}.",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return
        item = get_item(bound)
        name = item.name if item else bound

        # TOOLS (game/tools.py): a bound tool is USED against the TARGET
        # square (the highlighted tile): the tool acts on exactly the tile
        # the cursor/facing marks. Near rock the press mines (pickaxe), near
        # a tree it chops (axe), on grass it scoops (shovel) — but a tool can
        # never act outside its family's target: e.g. facing away from the
        # rock with a pickaxe bound does nothing on the empty/grass tile.
        from game.tools import parse_tool_id

        tool = parse_tool_id(bound)
        if tool is not None and inv.count(bound) > 0:
            tile = self._target_tile_of(rt, uid)
            node = (
                rt.resources.node_at(*tile)
                if (tile is not None and rt.resources) else None
            )
            standing = node is not None and not rt.resources.is_chopped(node.anchor)
            terrain = getattr(rt, "terrain", None)
            grass = (
                terrain is not None and tile is not None
                and terrain.scoopable(*tile)
            )
            if tool.family in ("axe", "pickaxe") and standing:
                _, result = await self.manager.dispatch(
                    self.channel_id, ChopAction(uid, tool_id=bound)
                )
                if not result.state_changed:
                    text = CHOP_REASON_TEXT.get(result.reason, "Không dùng được.")
                    try:
                        await interaction.response.send_message(
                            text, ephemeral=True, delete_after=EPHEMERAL_WARN
                        )
                    except (discord.NotFound, discord.HTTPException):
                        pass
                    return
                await self._handle_chop_result(interaction, rt, screen, uid, result)
                return
            if tool.family == "shovel" and grass:
                _, result = await self.manager.dispatch(
                    self.channel_id, ShovelAction(uid, tool_id=bound)
                )
                if not result.state_changed:
                    text = SHOVEL_REASON_TEXT.get(result.reason, "Không xúc được.")
                    try:
                        await interaction.response.send_message(
                            text, ephemeral=True, delete_after=EPHEMERAL_WARN
                        )
                    except (discord.NotFound, discord.HTTPException):
                        pass
                    return
                if result.drops is None:
                    self._apply_axe_labels()
                    self._apply_hotbar_labels()
                    try:
                        await interaction.response.edit_message(view=self)
                    except (discord.NotFound, discord.HTTPException) as e:
                        log.warning("[SCREEN] scoop progress edit failed: %s", e)
                    return
                self._apply_axe_labels()
                self._apply_hotbar_labels()
                self._after_inventory_change(uid)
                _focus_screen_camera(rt, screen, uid)
                await self._render_and_respond(interaction, rt, screen, uid)
                try:
                    await send_ephemeral_followup(
                        interaction, "🥄 Đã xúc cỏ! Nhận: 🟤x1 (Đất)", EPHEMERAL_ACTION,
                    )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            # Tool with no matching target under the cursor: generic hint.
            hints = {
                "axe": "Không có cây nào ở ô đang nhắm — rìu chỉ chặt được cây.",
                "pickaxe": "Không có đá/quặng nào ở ô đang nhắm — cúp chỉ đập được đá.",
                "shovel": "Không có cỏ để xúc ở ô đang nhắm — xẻng chỉ xúc được ô cỏ trống.",
                "sword": "🗡️ Kiếm tự động dùng khi ⚔️ tấn công zombie.",
            }
            try:
                await interaction.response.send_message(
                    hints.get(tool.family, "Không dùng được công cụ này ở đây."),
                    ephemeral=True, delete_after=EPHEMERAL_WARN,
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        # Route by item kind: a placeable BLOCK is placed on the facing tile
        # (or on the aim-cursor tile while Build Mode is ON); anything else is
        # consumed via the normal item-use path.
        bdef = get_block(bound)
        if bdef is not None and bdef.placeable:
            # Remember the last bound block: ✅ place reuses it, and the
            # Build Mode ghost preview shows this block.
            screen.selected_block = bound
            player = rt.state.get_player(uid)
            if player is not None:
                player.aim_block = bound
            dx = dy = None
            if screen.build_mode and player is not None:
                dx, dy = player.aim_dx, player.aim_dy
            _, result = await self.manager.dispatch(
                self.channel_id, PlaceBlockAction(uid, bound, dx=dx, dy=dy)
            )
            if not result.state_changed:
                text = BLOCK_REASON_TEXT.get(result.reason, "Không đặt được.")
                try:
                    await interaction.response.send_message(
                        text, ephemeral=True, delete_after=EPHEMERAL_WARN
                    )
                except (discord.NotFound, discord.HTTPException):
                    pass
                return
            self._apply_hotbar_labels()
            self._after_inventory_change(uid)
            _focus_screen_camera(rt, screen, uid)
            await self._render_and_respond(interaction, rt, screen, uid)
            return

        ok, reason = await self.manager.use_item(self.channel_id, uid, bound)
        if ok:
            msg = f"✅ Đã dùng {item.emoji if item else ''} {name}."
        elif reason == "empty":
            msg = f"❌ Hết {item.emoji if item else ''} {name} rồi."
        else:
            msg = f"❌ Không dùng được {name}: {reason}"

        self._apply_hotbar_labels()
        try:
            # Component-only edit (labels/counts) — no image upload, instant.
            await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SCREEN] hotbar edit failed: %s", e)
        try:
            await send_ephemeral_followup(
                interaction, msg, EPHEMERAL_OK if ok else EPHEMERAL_WARN,
            )
        except (discord.NotFound, discord.HTTPException):
            pass
        if ok:
            self._after_inventory_change(uid)

    def _after_inventory_change(self, uid: int) -> None:
        """Bag contents changed (place/use/break): refresh the hub HUD and any
        open inventory panel so both always mirror the bag."""
        coalescer = getattr(self.manager, "hub_coalescer", None)
        if coalescer is not None:
            coalescer.schedule((self.channel_id, uid), {"focused_user_id": uid})
        try:
            from discord_ui.inventory_view import refresh_if_open

            bot = getattr(self.manager, "bot_ref", None)
            channel = bot.get_channel(self.channel_id) if bot is not None else None
            if channel is not None:
                asyncio.create_task(refresh_if_open(self.manager, channel, uid))
        except Exception as e:  # noqa: BLE001 — background refresh must never raise
            log.warning("[SCREEN] inventory refresh failed: %s", e)

    # ----- movement -----

    async def _handle_move(self, interaction: discord.Interaction, key: str) -> None:
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            await interaction.response.send_message(
                "Chưa có map.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return
        if self.user_id is not None and interaction.user.id != self.user_id:
            owner = rt.state.get_player(self.user_id)
            name = owner.display_name if owner is not None else "người chơi khác"
            await interaction.response.send_message(
                f"Đây là màn hình riêng của {name}. Dùng /joinmap để có màn hình của bạn.",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return
        if self.user_id is None:
            # Legacy shared screen: pressing mints the presser a personal screen.
            await self._create_screen_for(interaction, rt, interaction.user.id)
            return
        uid = interaction.user.id
        screen = rt.screens.get(uid)
        if screen is None:
            await self._create_screen_for(interaction, rt, uid)
            return
        self.manager.touch_session(self.channel_id, uid)

        rt.members[uid] = interaction.user
        if uid not in rt.state.players:
            rt.state.add_player(uid, interaction.user.display_name, *rt.map_data.spawn)
            await self.manager.renderer.avatar_cache.get_avatar(
                interaction.user, label=interaction.user.display_name[:1]
            )

        direction = DIR_BY_KEY[key]

        # Moving on the screen closes every side panel (inventory / craft):
        # only the screen+hub pair stays (requested UX). Build Mode aim moves
        # and tool presses do NOT trigger this — only real movement does.
        if not screen.build_mode:
            await self.manager.close_side_panels(self.channel_id, uid)

        # Build Mode: the D-pad drives the aim cursor, NOT the player.
        if screen.build_mode:
            if screen.auto_running:
                self._stop_auto(screen)
            screen.travel_steps = 0
            _, result = await self.manager.dispatch(
                self.channel_id, AimAction(uid, *direction.vector)
            )
            if result.state_changed:
                _focus_screen_camera(rt, screen, uid)
                await self._render_and_respond(interaction, rt, screen, uid)
            else:
                # Cursor pinned at the range edge: cheap ACK, no re-upload.
                try:
                    await interaction.response.defer()
                except (discord.NotFound, discord.HTTPException):
                    pass
            return

        # Any button cancels a running auto-move. A direction press also still
        # moves once (feels natural); the auto loop is stopped first.
        if screen.auto_running:
            self._stop_auto(screen)
        elif screen.auto_armed:
            # 🎦 was armed: this direction starts continuous auto-move.
            screen.auto_armed = False
            await self._start_auto(interaction, screen, direction)
            return

        step = screen.step_size
        changed = False
        t_state = asyncio.get_running_loop().time()
        for i in range(step):
            p = rt.state.get_player(uid)
            px, py = (p.x, p.y) if p is not None else (None, None)
            # All segments but the last are 'intermediate': zombies get their
            # single turn only on the final segment, never per-tile.
            _, result = await self.manager.dispatch(
                self.channel_id,
                MoveAction(uid, direction, intermediate=(i < step - 1)),
            )
            if result.state_changed:
                changed = True
            else:
                break
        _lag_ms = (asyncio.get_running_loop().time() - t_state) * 1000
        if _lag_ms > 50:
            # The press reached the bot but the state took unusually long to
            # apply — almost always a busy event loop (should not happen now
            # that rendering runs in worker threads). Watch [LOOP] logs too.
            log.warning("[INPUT] press state-apply lag %.0fms (event loop busy?)", _lag_ms)

        if not changed:
            screen.travel_steps = 0  # stopped: bring the indicators back
            _focus_screen_camera(rt, screen, uid)
            # No movement: just ACK the press cheaply. Defer hits the interaction
            # callback endpoint (per-interaction token) — NOT the edit bucket.
            try:
                await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException):
                pass
            return

        screen.travel_steps += 1
        _focus_screen_camera(rt, screen, uid)
        await self._render_and_respond(interaction, rt, screen, uid)

    # ----- auto-move -----

    async def _start_auto(self, interaction: discord.Interaction, screen, direction: Direction) -> None:
        screen.auto_direction = direction
        screen.auto_running = True
        screen.auto_task = asyncio.ensure_future(self._auto_loop(interaction.channel, screen))
        self._apply_tool_labels()
        # ACK on the callback endpoint, then update the tool labels via the
        # spaced gate (background lane — auto frames ride the edit bucket).
        try:
            await interaction.response.defer()
        except (discord.NotFound, discord.HTTPException):
            pass
        try:
            await interaction.channel.get_partial_message(
                screen.controls_message_id if _is_split_screen(screen)
                else screen.message_id
            ).edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _auto_loop(self, channel, screen) -> None:
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            return
        uid = screen.user_id
        try:
            while screen.auto_running:
                step = screen.step_size
                moved = False
                for i in range(step):
                    p = rt.state.get_player(uid)
                    px, py = (p.x, p.y) if p is not None else (0, 0)
                    _, result = await self.manager.dispatch(
                        self.channel_id,
                        MoveAction(
                            uid, screen.auto_direction,
                            intermediate=(i < step - 1),
                        ),
                    )
                    if result.state_changed:
                        moved = True
                    else:
                        break
                if not moved:
                    self._stop_auto(screen)
                    break
                _focus_screen_camera(rt, screen, uid)
                render = await self.manager.renderer.render(
                    rt.state, rt.map_data, members=rt.members, camera=screen.camera,
                    full=True, focus_user_id=uid, weather_key=_wx_key_of(rt),
                    fx_seed=_fx_seed_of(rt),
                    indicator_dim=True,
                    **resource_render_kwargs(rt),
            **terrain_render_kwargs(rt),
                )
                screen.composite = render.composite
                try:
                    await self.manager.edit_gate.edit_message(
                        channel, screen.message_id,
                        attachments=[await _screen_file(render)],
                    )
                except discord.NotFound:
                    self._stop_auto(screen)
                    break
                except discord.HTTPException as e:
                    log.warning("[SCREEN] auto-move edit failed: %s", e)
                    self._stop_auto(screen)
                    break
                await asyncio.sleep(AUTO_INTERVAL)
        except asyncio.CancelledError:
            pass
        except Exception:
            self._stop_auto(screen)
        finally:
            self._apply_tool_labels()


async def flush_render_batch(manager, key, batch: list) -> None:
    """Coalesced frame flush for a per-player screen.

    ``key`` is ``(channel_id, user_id)``. Renders ONCE with the player's own
    camera and pushes the FINAL state, then acknowledges every press in the
    batch. Preferred lane: the LAST interaction's webhook endpoint
    (``edit_original_response`` — per-interaction bucket, never the channel
    PATCH bucket). Fallback: the spaced gate, only when no live interaction
    exists in the batch.
    """
    if isinstance(key, tuple):
        channel_id, user_id = key
    else:  # legacy single-channel key
        channel_id, user_id = key, None
    rt = manager.get_runtime_for(channel_id, user_id)
    if rt is None or manager.renderer is None:
        return
    screen = rt.screens.get(user_id) if user_id is not None else None
    if screen is None:
        return
    _focus_screen_camera(rt, screen, user_id)
    result = await manager.renderer.render(
        rt.state, rt.map_data, members=rt.members, camera=screen.camera,
        full=True, focus_user_id=user_id, weather_key=_wx_key_of(rt),
        fx_seed=_fx_seed_of(rt),
        indicator_dim=screen.auto_running or screen.travel_steps >= 4,
        **resource_render_kwargs(rt),
            **terrain_render_kwargs(rt),
    )
    screen.composite = result.composite
    # Encode ONCE (off-loop); every send attempt below wraps the SAME bytes in
    # a FRESH discord.File — a File must never be reused across two HTTP
    # requests, or the second one uploads an EOF-consumed 0-byte attachment.
    data = await run_image_task(_screen_bytes, result)
    view = batch[0].get("view") or MapView(channel_id, manager, user_id)
    screen.map_view = view

    # Preferred: per-interaction webhook endpoint — BUT only for legacy
    # combined screens. On a split screen the interaction token edits the
    # CONTROLS message the button lives on, not the map image; pushing the
    # map frame there overwrites the D-pad and kicks off the recovery churn
    # (ghost screens, duplicate hubs). Split screens always edit the stored
    # map message through the spaced gate instead.
    last = batch[-1].get("interaction")
    if (
        last is not None
        and last.response.is_done()
        and not _is_split_screen(screen)
    ):
        try:
            await last.edit_original_response(
                attachments=[_screen_file_from_bytes(data, result.filename)], view=view
            )
            return
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SCREEN] coalesced original-response edit failed: %s", e)

    channel = resolve_channel(manager, channel_id, batch)
    try:
        await manager.edit_gate.edit_message(
            channel, screen.message_id,
            attachments=[_screen_file_from_bytes(data, result.filename)],
        )
        # Keep the D-pad honest on EVERY frame flush: the hotbar mirrors the
        # bag (projection of the first HOTBAR_SLOTS stacks), so a pickup from
        # chopping/mining must relabel the rail immediately — not wait for
        # the next direct press. Component-only edit on the controls message
        # (its own bucket; never blocks the map upload).
        controls_id = getattr(screen, "controls_message_id", None)
        if controls_id is not None:
            view._apply_hotbar_labels()
            view._apply_tool_labels()
            try:
                await manager.edit_gate.edit_message(
                    channel, controls_id, view=view,
                )
            except (discord.NotFound, discord.HTTPException) as e:
                log.warning("[CONTROLS] coalesced label refresh failed: %s", e)

    except discord.NotFound:
        # Screen message was deleted: hand the whole stack to the silent
        # repair loop instead of a one-shot recreate (which raced the hub
        # lane and produced duplicate/orphan hubs).
        log.info("[SCREEN] coalesced flush NotFound; scheduling silent repair")
        from discord_ui.session_recovery import schedule_repair

        schedule_repair(manager, rt, user_id, why="screen 404 (coalesced flush)")
    except discord.HTTPException as e:
        log.warning("[SCREEN] coalesced gate edit failed: %s", e)
        from discord_ui.session_recovery import schedule_repair

        schedule_repair(manager, rt, user_id, why=f"screen edit {e!r}")
