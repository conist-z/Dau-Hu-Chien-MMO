import discord
from discord.ui import Button, Select, View, button, select

from game.items import get_item
from game.npc import get_node

MAX_ROWS = 5  # Discord allows component rows 0-4 per message.


class InventoryPanel(View):
    def __init__(self, channel_id: int, user_id: int, manager):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id
        self.manager = manager
        self._build()

    def _items(self):
        return self.manager.get_inventory(self.channel_id, self.user_id).items

    def _build(self) -> None:
        self.clear_items()
        options = []
        for iid, qty in self._items().items():
            item = get_item(iid)
            label = (item.emoji + " " if item else "") + (item.name if item else iid)
            options.append(discord.SelectOption(label=label[:100], value=iid, description=f"x{qty}"))
        if not options:
            options.append(discord.SelectOption(label="(trống)", value="__none__", description="Không có đồ"))
        sel = Select(
            placeholder="Chọn đồ để dùng",
            options=options,
            custom_id=f"invsel:{self.channel_id}:{self.user_id}",
            row=0,
        )
        sel.callback = self._on_select
        self.add_item(sel)
        close = Button(
            label="Đóng",
            custom_id=f"invclose:{self.channel_id}:{self.user_id}",
            row=1,
        )
        close.callback = self._on_close
        self.add_item(close)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        iid = interaction.data.get("values", [None])[0]
        if iid in (None, "__none__"):
            await interaction.response.edit_message(content="Không có đồ để dùng.", view=self)
            return
        ok, reason = await self.manager.use_item(self.channel_id, self.user_id, iid)
        self._build()
        msg = "Đã dùng." if ok else f"Không thể dùng: {reason}"
        await interaction.response.edit_message(content=msg, view=self)

    async def _on_close(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content="Đã đóng.", view=None)


class DialoguePanel(View):
    def __init__(self, channel_id: int, user_id: int, manager, npc, node):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id
        self.manager = manager
        self.npc = npc
        self.node = node
        self.node_key = getattr(npc, "dialogue", None)
        self._build()

    def _build(self) -> None:
        self.clear_items()
        if self.node is None:
            return
        for idx, opt in enumerate(self.node.options[: MAX_ROWS * MAX_ROWS]):
            b = Button(
                label=opt.label[:80],
                custom_id=f"dlg:{self.channel_id}:{self.user_id}:{self.node_key}:{idx}",
                row=min(idx, MAX_ROWS - 1),
            )
            b.callback = self._make_opt(idx)
            self.add_item(b)

    def _make_opt(self, idx: int):
        async def cb(interaction: discord.Interaction) -> None:
            opt = self.node.options[idx]
            if "give_item" in opt.effect:
                await self.manager.add_item(self.channel_id, self.user_id, opt.effect["give_item"], 1)
            nxt = get_node(self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None)).npc_map, opt.next)
            if nxt is None:
                self.stop()
                await interaction.response.edit_message(content="(kết thúc)", view=None)
                return
            self.node = nxt
            self.node_key = opt.next
            self._build()
            await interaction.response.edit_message(content=nxt.text, view=self)

        return cb
