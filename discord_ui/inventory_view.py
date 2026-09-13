import logging
import math

import discord
from discord.ui import Button, Select, View

from config import HOTBAR_SLOTS
from discord_ui.ephemeral import EPHEMERAL_WARN
from game.items import get_item

log = logging.getLogger("GAME")

# Visual language: the grid frame is drawn with 🔳 (light) so the bag reads
# as a bright-framed panel — and every glyph is an emoji, so the border ALWAYS
# lines up (proportional-font box-drawing chars never did).
#
# Cell design: ONE item stack = ONE text cell =
#   [item emoji][superscript qty]  e.g. 🍎⁰⁵, 🪵⁹⁹
# The quantity is PLAIN TEXT (superscript digits), zero-padded to two digits
# — NOT an emoji (keycap-emoji counts read as number balloons; the user
# explicitly wants text). The emoji frame (🔳 + ⬛) is KEPT.
#
# Alignment trick: in Discord's emoji+text model a line's rendered width is
#     2 * (#emoji glyphs) + (#text chars)
# (one emoji ≈ 2 text columns). The grid is a FULL fixed box: exactly 12
# item slots per row, 4 rows, ALWAYS — no dynamic wrap. Every row is padded
# to identical structure: 14 emoji (2 frame + 12 slots, empties filled with
# ⬛) and exactly 24 text characters (the 2-digit badges, with one space
# padded per missing superscript digit). Identical emoji count + identical
# text count on EVERY row -> identical rendered width -> the frame can
# never drift, while counts stay plain readable text.
# Stacks cap at MAX_STACK (99, Minecraft-style): 250 apples = 3 cells
# (🍎⁹⁹ 🍎⁹⁹ 🍎⁵²).
BORDER = "🔳"     # frame emoji (kept)
ITEM_SLOT = "⬛"  # empty-cell / line filler (kept)
QTY_SLOT = "⬛"   # legacy alias
GRID_W = 12   # max item cells per line
GRID_H = 4    # max lines in the panel

# Max quantity per stack; overflow splits into another cell (Minecraft-style).
MAX_STACK = 99

# Measured on Discord: one superscript digit renders ~1.3 space-widths (a
# GLYPH, not whitespace) — 10 digits push the closing 🔳 3 spaces right.
# The renderer trims round(0.3 * digits) spaces from the row's trailing
# empty cells to land the frame back on column.
DIGIT_EXTRA = 0.3

# Superscript digits: the qty badge as PLAIN TEXT. Zero-padded to two digits
# so every badge is exactly two glyphs wide.
SUP = {c: s for c, s in zip(
    "0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"
)}


def _qty_text(qty: int) -> str:
    """Zero-padded two-digit superscript badge for a stack count (e.g. 5 -> ⁰⁵)."""
    return "".join(SUP[c] for c in f"{max(0, min(qty, MAX_STACK)):02d}")


def _stacks(items: dict) -> list:
    """Split the bag into Minecraft-style stacks of at most MAX_STACK.

    ``items`` maps item_id -> total quantity. Each item becomes
    ``ceil(qty / MAX_STACK)`` stacks: full stacks of 99 first, then one
    remainder stack. The dict's insertion order IS the bag order (what the
    hotbar mirrors), so stacks are NOT sorted — they follow the bag's order.
    Returns a flat list of (item_id, stack_qty) pairs, packed left-to-right,
    top-to-bottom.
    """
    out = []
    for iid, qty in items.items():
        qty = max(0, int(qty))
        while qty > 0:
            take = min(qty, MAX_STACK)
            out.append((iid, take))
            qty -= take
    return out


RARITY_COLORS = {
    "common": 0x95A5A6,
    "uncommon": 0x3498DB,
    "rare": 0x9B59B6,
    "epic": 0xFF9900,
    "legendary": 0xF1C40F,
}
ITEM_RARITY = {
    "potion_hp": "common",
    "potion_mp": "uncommon",
    "key_stone": "rare",
}


def _cid(channel_id: int, user_id: int, key: str) -> str:
    return f"inv:{channel_id}:{user_id}:{key}"


def _grid_cells(items: dict) -> list:
    """Map the bag (``{item_id: qty}``) onto one cell per stack.

    Each stack (see ``_stacks``) fills ONE cell: item emoji + superscript
    qty badge, following the bag's insertion order. Returns a fixed-size
    list of length ``GRID_W * GRID_H`` whose entries are
    ``(glyph, qty_text)`` tuples or ``None`` for unused cells.
    """
    cells = [None] * (GRID_W * GRID_H)
    for idx, (iid, stack_qty) in enumerate(_stacks(items)):
        if idx >= len(cells):
            break
        item = get_item(iid)
        glyph = item.emoji if item else "❓"
        cells[idx] = (glyph, _qty_text(stack_qty))
    return cells


def _format_grid(items: dict) -> str:
    """Render the bag as a FULL 12x4 emoji-framed box with TEXT badges.

    Exactly 12 item slots per row, 4 rows, always — never a dynamic wrap.
    Each stack renders as ``emoji + _qty_text`` (e.g. 🍎⁰⁵).

    THE ALIGNMENT ALGORITHM ("the logic that knows where things go"):
      1. Build every row with per-cell gaps (empty/frame cells get 2 spaces).
      2. MEASURE each row under the glyph model — emoji = 2 columns, one
         superscript digit = 1 + DIGIT_EXTRA columns (user-measured: 10
         digits push the closing 🔳 3 spaces right).
      3. Trim round(0.3 * digits) spaces from a row's trailing empty cells
         (full rows cannot trim — they have no spaces to give).
      4. Target = the WIDEST row; distribute the leftover spaces of every
         narrower row (frame rows included) across its gaps, round-robin.

    Result: every row lands within ~1 space of the target — the frame
    cannot visibly drift on any platform, with any bag contents.
    """
    stack_cells = [
        f"{glyph}{qty}" for entry in _grid_cells(items)
        if entry is not None for glyph, qty in (entry,)
    ]
    frame_segs = [(BORDER, 2)] * GRID_W
    rows = [(frame_segs, 0)]
    for r in range(GRID_H):
        chunk = stack_cells[r * GRID_W:(r + 1) * GRID_W]
        filled = len(chunk)
        empties = GRID_W - filled
        digits = 2 * filled
        # Step 3: trim round(0.3 * digits) spaces from the trailing empties
        # (rightmost first) so the closing 🔳 lands back on the frame column.
        comp = min(round(digits * DIGIT_EXTRA), 2 * empties)
        gaps = [2] * empties
        i = empties - 1
        while comp > 0 and i >= 0:
            if gaps[i] > 0:
                gaps[i] -= 1
                comp -= 1
            i -= 1
        segs = [(cell, 0) for cell in chunk] + list(
            zip([ITEM_SLOT] * empties, gaps)
        )
        rows.append((segs, digits))
    rows.append((frame_segs, 0))

    # Steps 2 + 4: measure under the glyph model, then equalize every row
    # to the widest one by spreading its leftover spaces across its gaps.
    def _width(segs, digits):
        spaces = sum(g for _, g in segs)
        return 2 * (len(segs) + 2) + spaces + digits * (1 + DIGIT_EXTRA)

    widths = [_width(s, d) for s, d in rows]
    # Integer target: ceil of the widest row — every row then lands within
    # round-off (<= half a space) of the same width.
    target = math.ceil(max(widths))
    lines = []
    for (segs, _digits), w in zip(rows, widths):
        gaps = [g for _, g in segs] + [0]  # final gap sits before the close
        widen = [i for i, g in enumerate(gaps) if g > 0] or [len(gaps) - 1]
        # Floor: never overshoot the target (round could add a full space to
        # a row that was only 0.x under it).
        extra = int(target - w)
        i = 0
        while extra > 0:
            gaps[widen[i % len(widen)]] += 1
            extra -= 1
            i += 1
        body = "".join(
            f"{emoji}{' ' * g}" for (emoji, _), g in zip(segs, gaps[:-1])
        )
        lines.append(f"{BORDER}{body}{' ' * gaps[-1]}{BORDER}")
    return "\n".join(lines)


def _format_detail(item_id: str, qty: int) -> str:
    item = get_item(item_id)
    if item is None:
        return "❓ **Unknown item**"
    rarity = ITEM_RARITY.get(item_id, "common")
    return "\n".join(
        [
            f"**{item.emoji} {item.name}**",
            f"Rarity: {rarity}",
            f"Qty: {qty}",
            f"Effect: {item.description}",
        ]
    )


def _format_hotbar_strip(hotbar: dict) -> str:
    """One-line preview of the hotbar slots (what the D-pad rail shows):
    `1️⃣🧪x2 2️⃣⬛ …` — filled slots show their item emoji + qty, empty ones ⬛.

    Accepts both binding shapes: ``{slot: item_id}`` (manager.get_hotbar)
    and ``{slot: (item_id, qty)}`` (hub_view._hotbar_content)."""
    cells = []
    for i in range(HOTBAR_SLOTS):
        content = hotbar.get(i)
        if content:
            if isinstance(content, str):
                iid, qty = content, None
            else:
                iid, qty = content
            if qty is not None and qty <= 0:
                content = None
            if content is None:
                cells.append(f"{i + 1}⬛")
                continue
            item = get_item(iid)
            suffix = f"x{qty}" if qty is not None else ""
            cells.append(f"**{i + 1}**{item.emoji if item else '❓'}{suffix}")
        else:
            cells.append(f"{i + 1}⬛")
    return "🔥 **Hotbar** (D-pad): " + " · ".join(cells)


def _empty_detail() -> str:
    return "_Chọn một item ở dropdown để xem chi tiết._"


class InventoryView(View):
    """Emoji-grid inventory panel in its OWN message below the hub.

    Opening never edits the hub message — the hub HUD stays on top while the
    bag is open. Closing (🔙 Đóng) deletes the panel message entirely.
    Persistent per-player view: timeout=None + custom_id includes user_id
    (AGENTS.md principles 13/24/25).
    """

    def __init__(self, channel_id: int, user_id: int, manager):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id
        self.manager = manager
        self._selected_item = None
        self._build_controls()

    def _get_items(self) -> dict:
        inv = self.manager.get_inventory(self.channel_id, self.user_id)
        return dict(inv.items)

    def _item_list(self) -> list:
        # The bag dict is ORDERED (insertion order = hotbar projection order);
        # the grid and dropdown must follow it, not alphabetical order.
        items = self._get_items()
        return [(k, v) for k, v in items.items()]

    def _build_controls(self) -> None:
        self.clear_items()

        item_list = self._item_list()
        if item_list:
            options = []
            for iid, qty in item_list:
                item = get_item(iid)
                label = item.name if item else iid
                slot = self._hotbar_slot_of(iid)
                desc = f"x{qty}" + (f" · Hotbar ô {slot + 1}" if slot >= 0 else "")
                options.append(
                    discord.SelectOption(label=label[:100], value=iid, description=desc)
                )
        else:
            options = [discord.SelectOption(label="(trống)", value="__none__")]
        sel = Select(
            placeholder="Chọn item để xem / dùng" if item_list else "(túi trống)",
            options=options,
            custom_id=_cid(self.channel_id, self.user_id, "sel"),
            row=0,
        )
        sel.callback = self._on_select
        self.add_item(sel)

        back = Button(
            label="🔙 Đóng",
            style=discord.ButtonStyle.secondary,
            custom_id=_cid(self.channel_id, self.user_id, "back"),
            row=1,
        )
        back.callback = self._back_to_hub
        self.add_item(back)

        use = Button(
            label="🔧 Dùng",
            style=discord.ButtonStyle.success,
            custom_id=_cid(self.channel_id, self.user_id, "use"),
            row=1,
        )
        use.callback = self._use_item
        self.add_item(use)

        # Crafting: swap this panel message in place into a CraftPanel —
        # the button stands next to Đóng/Dùng as requested, and the hub HUD
        # above is untouched.
        craft = Button(
            label="🛠️ Chế tạo",
            style=discord.ButtonStyle.primary,
            custom_id=_cid(self.channel_id, self.user_id, "craft"),
            row=1,
        )
        craft.callback = self._open_craft
        self.add_item(craft)

        # Hotbar: the hotbar is a projection of the first HOTBAR_SLOTS bag
        # stacks, so assigning the selected item to a slot MOVES it to that
        # slot's position in the bag (swapping order) — no manual add/remove.
        assign = Select(
            placeholder="🎯 Gắn item đang chọn vào ô hotbar…",
            options=[discord.SelectOption(label=f"Ô {i + 1}", value=f"slot:{i}")
                     for i in range(HOTBAR_SLOTS)]
                    + [discord.SelectOption(label="Đưa xuống cuối túi", value="unbind",
                                            description="Rời khỏi hotbar (vị trí cuối)"),],
            custom_id=_cid(self.channel_id, self.user_id, "hbslot"),
            row=2,
        )
        assign.callback = self._on_assign_slot
        self.add_item(assign)

    def _hotbar_slot_of(self, item_id: str) -> int:
        """Which hotbar slot (0..HOTBAR_SLOTS-1) currently holds ``item_id``,
        or -1."""
        hb = self.manager.get_hotbar(self.channel_id, self.user_id)
        for slot, iid in hb.items():
            if iid == item_id:
                return slot
        return -1

    async def _refresh_screen_hotbar(self, interaction) -> None:
        """Push fresh hotbar labels onto the player's D-pad (screen message).

        The screen is a different message from this panel, so the labels are
        refreshed through the spaced edit gate and the persistent view is
        re-registered so future presses resolve to the fresh instance."""
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            return
        screen = rt.screens.get(self.user_id)
        if screen is None or screen.message_id is None:
            return
        from discord_ui.map_view import MapView

        view = getattr(screen, "map_view", None) or MapView(
            self.channel_id, self.manager, self.user_id
        )
        screen.map_view = view
        view._apply_hotbar_labels()
        if getattr(self.manager, "bot_ref", None) is not None:
            self.manager.bot_ref.add_view(view)
        try:
            await self.manager.edit_gate.edit_message(
                interaction.channel, screen.message_id, view=view
            )
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[INV] hotbar screen refresh failed: %s", e)

    async def _on_assign_slot(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values", [])
        value = values[0] if values else None
        if not value:
            await interaction.response.defer()
            return
        if self._selected_item is None:
            await interaction.response.send_message(
                "Chọn item ở dropdown phía trên trước đã.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        item = get_item(self._selected_item)
        name = item.name if item else self._selected_item
        if value == "unbind":
            slot = self._hotbar_slot_of(self._selected_item)
            if slot < 0:
                await interaction.response.send_message(
                    f"{name} đã ở ngoài hotbar rồi.", ephemeral=True,
                    delete_after=EPHEMERAL_WARN,
                )
                return
            # "Unbind" = move the stack past the hotbar window (to the end
            # of the bag): the next 6 stacks slide up into the hotbar.
            inv = self.manager.get_inventory(self.channel_id, self.user_id)
            inv.move_to(self._selected_item, max(0, len(inv.items) - 1))
            from persistence.repositories import save_inventory_order

            if self.manager.db is not None:
                await save_inventory_order(
                    self.db, self.channel_id, self.user_id, list(inv.items)
                )
            msg = f"Đã đưa {name} ra khỏi hotbar (cuối túi)."
        else:
            slot = int(value.split(":", 1)[1])
            await self.manager.set_hotbar_slot(
                self.channel_id, self.user_id, slot, self._selected_item
            )
            msg = f"Đã chuyển {name} vào ô {slot + 1} (tự đổi vị trí trong túi)."
        content, embed = self._render_content()
        await interaction.response.edit_message(
            content=f"{content}\n\n_🎯 {msg}_", embed=embed, view=self
        )
        await self._refresh_screen_hotbar(interaction)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values", [])
        iid = values[0] if values else None
        if iid in (None, "__none__"):
            await interaction.response.defer()
            return
        self._selected_item = iid
        content, embed = self._render_content()
        await interaction.response.edit_message(content=content, embed=embed, view=self)

    def _render_content(self) -> tuple[str, discord.Embed]:
        items = self._get_items()
        grid = _format_grid(items)
        strip = _format_hotbar_strip(self.manager.get_hotbar(
            self.channel_id, self.user_id
        ))

        if self._selected_item is not None:
            qty = items.get(self._selected_item)
            if qty:
                detail = _format_detail(self._selected_item, qty)
                slot = self._hotbar_slot_of(self._selected_item)
                detail += (
                    f"\nHotbar: ô {slot + 1} trên D-pad"
                    if slot >= 0
                    else "\nHotbar: _chưa gắn_ (chọn ô phía dưới để gắn)"
                )
            else:
                detail = _empty_detail()
        else:
            detail = _empty_detail()

        content = f"🎒 **Túi đồ của bạn**\n\n{grid}\n\n{strip}\n\n{detail}"
        rarity = ITEM_RARITY.get(self._selected_item, "common") if self._selected_item else "common"
        embed = discord.Embed(color=RARITY_COLORS.get(rarity, 0x95A5A6))
        item = get_item(self._selected_item) if self._selected_item else None
        if item is not None:
            embed.title = f"{item.emoji} {item.name}"
            embed.description = item.description
            embed.add_field(name="Rarity", value=rarity)
            embed.add_field(
                name="Qty", value=f"x{items.get(self._selected_item, 0)}"
            )
            embed.set_footer(text="Bấm 🔧 Dùng để sử dụng item đang chọn.")
        else:
            embed.title = "🎒 Túi đồ"
            embed.description = (
                "Chọn một item ở dropdown phía trên để xem chi tiết."
                if items
                else "Túi đồ trống — kiếm đồ bằng cách đập block hoặc nói chuyện với NPC."
            )
        return content, embed

    async def _back_to_hub(self, interaction: discord.Interaction) -> None:
        """Close the inventory: delete this panel message entirely.

        The panel lives in its OWN message below the hub (the hub is never
        edited), so closing = making the panel vanish; the hub HUD above is
        untouched and needs no re-render."""
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is not None:
            screen = rt.screens.get(self.user_id)
            if screen is not None:
                screen.inventory_message_id = None
                screen.inventory_view = None
        self.stop()
        # Acknowledge first (the component's message is the one being deleted,
        # otherwise Discord would show "interaction failed").
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[INV] close: cannot delete panel message: %s", e)

    async def _open_craft(self, interaction: discord.Interaction) -> None:
        """Swap this panel message in place into the crafting panel."""
        from discord_ui.craft_view import CraftPanel

        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        screen = rt.screens.get(self.user_id) if rt is not None else None
        view = CraftPanel(self.channel_id, self.user_id, self.manager)
        self.manager.bot_ref.add_view(view)
        self.stop()
        if screen is not None:
            screen.inventory_view = None
            screen.inventory_message_id = None
            screen.craft_view = view
            screen.craft_message_id = (
                interaction.message.id if interaction.message else None
            )
        content, embed = view._render_content()
        # The craft panel supplies its own recipe card. The inventory detail
        # card must NOT linger: passing the craft embed replaces the message's
        # existing embeds wholesale on edit_message.
        await interaction.response.edit_message(
            content=content, embed=embed, view=view
        )

    async def _use_item(self, interaction: discord.Interaction) -> None:
        if self._selected_item is None:
            await interaction.response.send_message(
                "Chọn item trước.", ephemeral=True, delete_after=EPHEMERAL_WARN
            )
            return

        item = get_item(self._selected_item)
        ok, reason = await self.manager.use_item(
            self.channel_id, self.user_id, self._selected_item
        )
        if ok:
            name = item.name if item else self._selected_item
            msg = f"✅ Đã dùng {name}."
            if self._selected_item not in self._get_items():
                self._selected_item = None  # consumed the last one
        else:
            msg = f"❌ Không thể dùng: {reason}"

        self._build_controls()
        content, embed = self._render_content()
        await interaction.response.edit_message(
            content=f"{content}\n\n_{msg}_", embed=embed, view=self
        )
        if ok:
            # HP/MP and bindings may both have changed. Refresh hub, map D-pad,
            # and this panel immediately from the same runtime snapshot.
            coalescer = getattr(self.manager, "hub_coalescer", None)
            if coalescer is not None:
                coalescer.schedule(
                    (self.channel_id, self.user_id),
                    {"focused_user_id": self.user_id},
                )
            await self._refresh_screen_hotbar(interaction)


async def refresh_if_open(manager, channel, user_id: int) -> None:
    """Refresh an OPEN inventory panel after the bag changed elsewhere
    (block break/place, hotbar use). No-op when the panel is closed.

    The panel keeps its own persistent view (``screen.inventory_view``) so
    the edit reuses the live instance instead of minting a new one."""
    rt = manager.get_runtime(channel.id)
    if rt is None:
        return
    screen = rt.screens.get(user_id)
    view = getattr(screen, "inventory_view", None) if screen is not None else None
    if view is None or screen.inventory_message_id is None:
        return
    view._build_controls()
    content, embed = view._render_content()
    try:
        await manager.edit_gate.edit_message(
            channel, screen.inventory_message_id,
            content=content, embed=embed, view=view,
        )
    except (discord.NotFound, discord.HTTPException) as e:
        log.warning("[INV] background refresh failed: %s", e)
