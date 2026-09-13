import logging

import discord
from discord.ui import Button, Select, View

from game.crafting import RecipeDef, STATION_RANGE  # noqa: F401
from game import crafting
from discord_ui.ephemeral import EPHEMERAL_WARN
from game.items import get_item

log = logging.getLogger("GAME")


def _cid(channel_id: int, user_id: int, key: str) -> str:
    return f"craft:{channel_id}:{user_id}:{key}"


def _iname(item_id: str) -> str:
    item = get_item(item_id)
    return f"{item.emoji} {item.name}" if item else item_id


def _can_craft_now(recipe: RecipeDef, inv, near_table: bool) -> bool:
    """Generic availability check — no per-item detail is ever exposed."""
    ok, _ = crafting.can_craft(recipe, inv, near_table)
    return ok


def _chain(recipe: RecipeDef) -> str:
    """Pure ingredient chain for the dropdown description: 🥢x2 + 🟫x3 → 🪓x1."""
    left = " + ".join(f"{_emoji(i)}x{q}" for i, q in recipe.inputs)
    out_id, out_qty = recipe.output
    return f"{left} → {_emoji(out_id)}x{out_qty}"


def _emoji(item_id: str) -> str:
    item = get_item(item_id)
    return item.emoji if item else "❓"


class CraftPanel(View):
    """Crafting panel in its OWN message below the hub (same pattern as the
    inventory panel). Persistent per-player view: timeout=None + custom_id
    includes user_id (AGENTS.md principle 13).

    Visual language (mirrors the inventory grid style — emoji only, so rows
    always line up):
      • Dropdown rows: a single status dot (🟢/🔴) + recipe name; the
        description is a pure ingredient chain.
      • The selected recipe renders as an embed card with separated
        Nguyên liệu / Sản phẩm fields and a one-line generic status footer.
      • No per-item missing detail anywhere — availability is generic.
    """

    def __init__(self, channel_id: int, user_id: int, manager):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id
        self.manager = manager
        self._selected_recipe = None
        self._build_controls()

    # ----- data -----

    def _inventory(self):
        return self.manager.get_inventory(self.channel_id, self.user_id)

    def _near_table(self) -> bool:
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is None:
            return False
        player = rt.state.get_player(self.user_id)
        if player is None:
            return False
        return crafting.nearest_station(rt.state.blocks, player)

    def _recipes(self) -> list:
        return sorted(crafting.RECIPE_REGISTRY.values(), key=lambda r: r.name)

    # ----- rendering -----

    def _build_controls(self) -> None:
        self.clear_items()

        near_table = self._near_table()
        inv = self._inventory()

        options = []
        for recipe in self._recipes():
            dot = "🟢" if _can_craft_now(recipe, inv, near_table) else "🔴"
            options.append(
                discord.SelectOption(
                    label=f"{dot} {recipe.name}"[:100],
                    value=recipe.id,
                    description=_chain(recipe)[:100],
                )
            )
        sel = Select(
            placeholder="Chọn công thức",
            options=options,
            custom_id=_cid(self.channel_id, self.user_id, "sel"),
            row=0,
        )
        sel.callback = self._on_select
        self.add_item(sel)

        craft_btn = Button(
            label="🛠️ Chế tạo",
            style=discord.ButtonStyle.success,
            custom_id=_cid(self.channel_id, self.user_id, "craft"),
            row=1,
        )
        craft_btn.callback = self._on_craft
        self.add_item(craft_btn)

        close = Button(
            label="🔙 Đóng",
            style=discord.ButtonStyle.secondary,
            custom_id=_cid(self.channel_id, self.user_id, "back"),
            row=1,
        )
        close.callback = self._on_close
        self.add_item(close)

        # Back to the inventory panel (swap in place, same message).
        inv_btn = Button(
            label="🎒 Về túi",
            style=discord.ButtonStyle.secondary,
            custom_id=_cid(self.channel_id, self.user_id, "inv"),
            row=1,
        )
        inv_btn.callback = self._on_back_to_inventory
        self.add_item(inv_btn)

    def _render_content(self) -> tuple:
        """(content, embed) for the panel. The embed is the recipe card."""
        recipe = (
            crafting.get_recipe(self._selected_recipe)
            if self._selected_recipe
            else None
        )
        content = "🛠️ **Chế tạo**"
        if recipe is None:
            embed = discord.Embed(
                title="🛠️ Chế tạo",
                description="Chọn một công thức ở menu phía trên.",
                color=0x95A5A6,
            )
            return content, embed

        inv = self._inventory()
        near_table = self._near_table()

        # Embed card: clear separation of name / description / materials /
        # product / status. Quantities use "×" and a separate "có N" clause
        # so emoji, name, need and have never blur together.
        embed = discord.Embed(
            title=f"{recipe.emoji} {recipe.name}",
            description=recipe.description or None,
            color=0x3498DB if _can_craft_now(recipe, inv, near_table) else 0x95A5A6,
        )
        mats = "\n".join(
            f"{_emoji(i)} **{_plain(i)}** ×{q} — có {inv.count(i)}"
            for i, q in recipe.inputs
        )
        out_id, out_qty = recipe.output
        embed.add_field(name="Nguyên liệu", value=mats, inline=False)
        embed.add_field(
            name="Sản phẩm",
            value=f"➜ {_emoji(out_id)} **{_plain(out_id)}** ×{out_qty}",
            inline=False,
        )
        embed.set_footer(text="Bấm 🛠️ Chế tạo để làm.")
        return content, embed

    # ----- handlers -----

    async def _on_select(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values", [])
        rid = values[0] if values else None
        if not rid:
            await interaction.response.defer()
            return
        self._selected_recipe = rid
        content, embed = self._render_content()
        await interaction.response.edit_message(content=content, embed=embed, view=self)
    async def _on_craft(self, interaction: discord.Interaction) -> None:
        if self._selected_recipe is None:
            await interaction.response.send_message(
                "Chọn công thức trước.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return
        ok, reason, out_id, out_qty = await self.manager.craft_item(
            self.channel_id, self.user_id, self._selected_recipe
        )
        # Generic feedback only — never point at a specific missing item.
        if ok:
            msg = f"✅ Đã chế tạo {_iname(out_id)} ×{out_qty}."
        elif reason == "no_station":
            msg = "❌ Cần Bàn chế tạo."
        else:
            msg = "❌ Thiếu vật phẩm."
        self._build_controls()
        content, embed = self._render_content()
        await interaction.response.edit_message(
            content=f"{content}\n\n{msg}", embed=embed, view=self
        )
        if ok:
            # The bag changed -> refresh the open inventory panel (if any).
            from discord_ui.inventory_view import refresh_if_open

            rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
            if rt is not None:
                await refresh_if_open(
                    self.manager, interaction.channel, self.user_id
                )

    async def _on_back_to_inventory(self, interaction: discord.Interaction) -> None:
        """Swap this panel message in place back into the inventory panel."""
        from discord_ui.inventory_view import InventoryView

        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        screen = rt.screens.get(self.user_id) if rt is not None else None
        view = InventoryView(self.channel_id, self.user_id, self.manager)
        self.manager.bot_ref.add_view(view)
        self.stop()
        if screen is not None:
            screen.craft_view = None
            screen.craft_message_id = None
            screen.inventory_view = view
            screen.inventory_message_id = (
                interaction.message.id if interaction.message else None
            )
        content, embed = view._render_content()
        await interaction.response.edit_message(
            content=content, embed=embed, view=view
        )
        # The panel swapped OUT of craft mode: clear any craft embed so the
        # inventory view fully owns this message's embeds again.

    async def _on_close(self, interaction: discord.Interaction) -> None:
        rt = self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        if rt is not None:
            screen = rt.screens.get(self.user_id)
            if screen is not None:
                screen.craft_message_id = None
                screen.craft_view = None
        self.stop()
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            pass
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[CRAFT] close: cannot delete panel message: %s", e)


def _plain(item_id: str) -> str:
    """Item name without the emoji (emoji is printed separately in rows)."""
    item = get_item(item_id)
    return item.name if item else item_id
