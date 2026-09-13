"""Per-player Settings panel (persistent view, ephemeral message).

Structured as a feature HUB + per-feature tabs:

- Page "overview": one visual index of every feature. Each entry shows its
  current state and a one-line summary so a player can understand what the
  feature does WITHOUT opening it. Selecting an entry opens that feature's
  own tab.
- Tab "move": movement behaviour (step size cycle 1→3→5, auto-move toggle).
- Tab "avatar": avatar picker (bundled Twemoji by category + server emojis),
  with ✅ confirm — the same flow as before, now inside its own tab.
- Tab "weather": WEATHER STATUS for everyone (current key, real snapshot,
  buff modifiers, and whether the animated screen FX is running). Regular
  players are read-only; server admins (Administrator / Manage Server) get
  the animated FX toggle + a refresh button. The FX gate lives on the
  scenario runtime (``weather_fx_enabled``, default OFF) so turning it off
  globally keeps every screen render on the fast static-PNG path.

Tab is encoded into the nav custom_ids (``ov:``/``feat:<key>:``) so a press
always resolves against the currently-bound view instance. Persistent views
use timeout=None and per-player custom_ids (rule #13).
"""
import logging
import random

import discord
from discord.ui import Button, Select, View

from discord_ui.ephemeral import EPHEMERAL_WARN

log = logging.getLogger("GAME")

ACCENT = 0x5865F2          # blurple — settings hub + behaviour tabs
ACCENT_AVATAR = 0xFEE75C   # gold — avatar tab
ACCENT_WEATHER = 0x3498DB  # sky — weather tab

STEP_CYCLE = {1: 3, 3: 5, 5: 1}

FALLBACK_CATEGORIES = [
    ("animals", "Động vật"),
    ("faces", "Khuôn mặt"),
    ("food", "Đồ ăn"),
    ("fantasy", "Fantasy"),
]

# Feature registry for the overview hub: key -> (emoji, name, summary).
FEATURES = [
    (
        "move", "🏃 Di chuyển",
        "Bước/click & auto-move — cách nhân vật di chuyển khi bấm D-pad.",
    ),
    (
        "avatar", "🖼️ Avatar",
        "Đổi hình đại diện trên map & huy hiệu hub (Twemoji / emoji server).",
    ),
    (
        "weather", "🌦️ Thời tiết",
        "Xem thời tiết hiện tại & buff. Admin bật/tắt hiệu ứng động trên map.",
    ),
]


def _cid(channel_id: int, user_id: int, key: str) -> str:
    return f"st:{channel_id}:{user_id}:{key}"


def _unicode_char(codepoint: str) -> str:
    """'1f436' -> '🐶' (also handles ZWJ sequences like '1f9d1-200d-1f9d2')."""
    try:
        return "".join(chr(int(h, 16)) for h in (codepoint or "").split("-"))
    except (ValueError, TypeError):
        return ""


class SettingsView(View):
    """Persistent per-player settings view (timeout=None + per-player ids)."""

    def _cid(self, key: str) -> str:
        # Delegates to the module-level _cid (same-name global).
        return _cid(self.channel_id, self.user_id, key)

    def __init__(self, channel_id: int, user_id: int, manager, tab: str = "overview",
                 guild=None):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.user_id = user_id
        self.manager = manager
        self.tab = tab if tab in {"overview", "move", "avatar", "weather"} else "overview"
        self.guild_id = guild.id if guild is not None else None
        # Admin flag: resolved once at open/rebuild from the guild member.
        self.is_admin = self._resolve_admin(guild)
        # Pending avatar choice: ("twe", codepoint) | ("emoji", emoji_id)
        # | ("default", None). Only applied on ✅ Xác nhận.
        self.pending = None
        self.selected_category = None
        self.status = None
        self._build()

    # ----- context helpers (None-safe for tests / legacy runtimes) -----

    def _resolve_admin(self, guild) -> bool:
        perms = getattr(guild, "me", None)  # placeholder, replaced below
        member = self._guild_member()
        if member is not None:
            perms = getattr(member, "guild_permissions", None)
            if perms is not None:
                try:
                    return bool(perms.administrator or perms.manage_guild)
                except Exception:
                    return False
        # Interaction.guild is a Guild whose .me is the bot; the opener's own
        # permissions are threaded through open_with_interaction instead.
        return bool(getattr(self, "is_admin", False))

    def _guild_member(self):
        inter = getattr(self, "interaction", None)
        return getattr(inter, "user", None) if inter is not None else None

    @classmethod
    def open_with_interaction(cls, manager, interaction: discord.Interaction):
        """Build the panel from a command/button interaction.

        Copies the opener's permissions up front: the persistent view's later
        callbacks may run in DM-ish contexts where guild_permissions is gone.
        """
        guild = interaction.guild
        perms = getattr(interaction.user, "guild_permissions", None)
        is_admin = False
        if perms is not None:
            try:
                is_admin = bool(perms.administrator or perms.manage_guild)
            except Exception:
                is_admin = False
        view = cls(
            interaction.channel_id, interaction.user.id, manager,
            tab="overview", guild=guild,
        )
        view.is_admin = is_admin
        view.interaction = interaction
        return view

    def _rt(self):
        try:
            return self.manager.get_runtime_for(self.channel_id, getattr(self, "user_id", None))
        except Exception:
            return None

    def _screen(self, rt):
        try:
            return rt.screens.get(self.user_id) if rt is not None else None
        except Exception:
            return None

    def _step_size(self, rt) -> int:
        screen = self._screen(rt)
        if screen is not None:
            return getattr(screen, "step_size", 1)
        return getattr(rt, "step_size", 1) if rt is not None else 1

    def _auto_armed(self, rt) -> bool:
        screen = self._screen(rt)
        if screen is not None:
            return bool(getattr(screen, "auto_armed", False))
        return bool(getattr(rt, "auto_armed", False)) if rt is not None else False

    def _manifest_meta(self) -> dict:
        try:
            return self.manager.renderer.avatar_cache.manifest_meta()
        except Exception:
            return {}

    def _avatars(self) -> list:
        try:
            return self.manager.renderer.avatar_cache.default_avatars()
        except Exception:
            return []

    def _categories(self):
        meta = self._manifest_meta()
        cats = meta.get("categories") or [c for c, _ in FALLBACK_CATEGORIES]
        labels = meta.get("category_labels") or dict(FALLBACK_CATEGORIES)
        return [(c, labels.get(c, c)) for c in cats]

    def _guild(self):
        bot = getattr(self.manager, "bot_ref", None)
        if bot is None or not self.guild_id:
            return None
        try:
            return bot.get_guild(self.guild_id)
        except Exception:
            return None

    def _server_emoji_options(self):
        """Up to 24 server emojis as SelectOptions ([] when unavailable)."""
        guild = self._guild()
        if guild is None:
            return []
        try:
            emojis = [e for e in guild.emojis if getattr(e, "available", True)][:24]
        except Exception:
            return []
        return [
            discord.SelectOption(
                label=(getattr(e, "name", "") or f"emoji-{e.id}")[:80],
                value=f"emoji:{e.id}",
                emoji=e,
            )
            for e in emojis
        ]

    def _avatar_entries_for(self, category):
        entries = [
            a for a in self._avatars()
            if not category or a.get("category") == category
        ]
        return entries[:16] if entries else self._avatars()[:16]

    # ----- pending / current avatar descriptions -----

    def _pending_desc(self):
        if self.pending is None:
            return None, None
        kind, val = self.pending
        if kind == "twe":
            entry = next(
                (a for a in self._avatars() if a.get("id") == f"twe:{val}"), None
            )
            label = entry["label"] if entry else val
            ch = _unicode_char(entry.get("unicode", "")) if entry else ""
            return f"twe:{val}", f"{ch} {label} (bộ mặc định)".strip()
        if kind == "emoji":
            guild = self._guild()
            name = None
            if guild is not None:
                try:
                    e = discord.utils.get(guild.emojis, id=int(val))
                    name = getattr(e, "name", None)
                except (ValueError, TypeError):
                    name = None
            return f"emoji:{val}", (
                f"Emoji server :{name}:" if name else f"Emoji server (id {val})"
            )
        return "", "Mặc định (mặt cười)"

    def _current_desc(self) -> str:
        rt = self._rt()
        p = rt.state.get_player(self.user_id) if rt is not None else None
        sid = (getattr(p, "sprite_id", "") or "") if p is not None else ""
        if sid.startswith("twe:"):
            entry = next((a for a in self._avatars() if a.get("id") == sid), None)
            if entry:
                return (
                    f"{_unicode_char(entry.get('unicode', ''))} "
                    f"{entry.get('label', sid)}"
                ).strip()
            return sid
        if sid.startswith("emoji:"):
            return "Emoji server"
        if sid:
            return sid
        return "Mặc định (mặt cười)"

    # ----- component assembly -----

    def _build(self) -> None:
        self.clear_items()
        builder = {
            "overview": self._build_overview,
            "move": self._build_move,
            "avatar": self._build_avatar,
            "weather": self._build_weather,
        }[self.tab]
        builder()
        self._build_nav()

    def _build_nav(self) -> None:
        row = 4
        home = Button(
            emoji="🏠", label="Tổng quan", style=discord.ButtonStyle.secondary,
            row=row, custom_id=self._cid(f"t{self.tab}:home"),
            disabled=self.tab == "overview",
        )
        home.callback = self._make_tab_cb("overview")
        self.add_item(home)

        pos = Button(
            label=self._tab_title(), style=discord.ButtonStyle.secondary,
            row=row, custom_id=self._cid(f"t{self.tab}:pos"), disabled=True,
        )
        self.add_item(pos)

        close = Button(
            emoji="❌", label="Đóng", style=discord.ButtonStyle.danger, row=row,
            custom_id=self._cid(f"t{self.tab}:close"),
        )
        close.callback = self._on_close
        self.add_item(close)

    def _tab_title(self) -> str:
        if self.tab == "overview":
            return "⚙️ Cài đặt"
        name = next((n for k, _, n in FEATURES if k == self.tab), self.tab)
        return name

    # ----- overview (feature hub) -----

    def _build_overview(self) -> None:
        rt = self._rt()
        options = []
        for key, name, summary in FEATURES:
            state = self._feature_state(rt, key)
            options.append(discord.SelectOption(
                label=f"{name} — {state}",
                value=key,
                description=summary[:100],
                emoji=name.split()[0],
            ))
        sel = Select(
            placeholder="📂 Chọn một chức năng để mở bảng điều khiển…",
            options=options,
            custom_id=self._cid("ov:sel"),
            row=0,
        )
        sel.callback = self._on_feature_select
        self.add_item(sel)

        # Quick toggles for the two real settings stay one press away.
        step = Button(
            label=f"👣 Bước: {self._step_size(rt)}",
            style=discord.ButtonStyle.primary, row=2,
            custom_id=self._cid("ov:step"),
        )
        step.callback = self._on_step_btn
        self.add_item(step)

        auto = Button(
            label="🎬 Auto: " + ("Chờ hướng" if self._auto_armed(rt) else "Tắt"),
            style=discord.ButtonStyle.secondary, row=2,
            custom_id=self._cid("ov:auto"),
        )
        auto.callback = self._on_auto_btn
        self.add_item(auto)

    def _feature_state(self, rt, key: str) -> str:
        """Short current-state label shown on the overview entries."""
        if key == "move":
            if self._auto_armed(rt):
                return f"bước {self._step_size(rt)} · auto đang chờ hướng"
            return f"bước {self._step_size(rt)} · auto tắt"
        if key == "avatar":
            return self._current_desc()
        if key == "weather":
            fx = self._fx_enabled(rt)
            return "FX động BẬT" if fx else "FX động TẮT"
        return "—"

    def _fx_enabled(self, rt) -> bool:
        return bool(getattr(rt, "weather_fx_enabled", False)) if rt is not None else False

    # ----- feature tab: move -----

    def _build_move(self) -> None:
        rt = self._rt()
        step = Button(
            label=f"👣 Bước/click: {self._step_size(rt)}",
            style=discord.ButtonStyle.primary, row=1,
            custom_id=self._cid("mv:step"),
        )
        step.callback = self._on_step_btn
        self.add_item(step)

        auto = Button(
            label="🎬 Auto-move: " + ("Chờ hướng" if self._auto_armed(rt) else "Tắt"),
            style=discord.ButtonStyle.secondary, row=1,
            custom_id=self._cid("mv:auto"),
        )
        auto.callback = self._on_auto_btn
        self.add_item(auto)

    # ----- feature tab: avatar -----

    def _build_avatar(self) -> None:
        cat_sel = Select(
            placeholder="📂 Chọn thể loại avatar…",
            options=[
                discord.SelectOption(
                    label=name, value=key,
                    default=(self.selected_category == key),
                )
                for key, name in self._categories()
            ],
            custom_id=self._cid("av:cat"),
            row=0,
        )
        cat_sel.callback = self._on_category
        self.add_item(cat_sel)

        av_options = []
        for a in self._avatar_entries_for(self.selected_category):
            ch = _unicode_char(a.get("unicode", ""))
            av_options.append(discord.SelectOption(
                label=(a.get("label") or a.get("id", "?"))[:80],
                value=a.get("id", "?"),
                emoji=ch or "🖼️",
                description="Bộ mặc định",
            ))
        if not av_options:
            av_options = [
                discord.SelectOption(label="(không có avatar mặc định)", value="__none__")
            ]
        av_sel = Select(
            placeholder="🎨 Avatar mặc định (Twemoji)…",
            options=av_options,
            custom_id=self._cid("av:def"),
            row=1,
        )
        av_sel.callback = self._on_default_avatar
        self.add_item(av_sel)

        srv = self._server_emoji_options()
        if srv:
            srv_sel = Select(
                placeholder="😀 Emoji của server…",
                options=srv,
                custom_id=self._cid("av:emoji"),
                row=2,
            )
            srv_sel.callback = self._on_server_emoji
            self.add_item(srv_sel)

        rnd = Button(
            label="Ngẫu nhiên", emoji="🎲", style=discord.ButtonStyle.secondary,
            row=3, custom_id=self._cid("av:rand"),
        )
        rnd.callback = self._on_random
        self.add_item(rnd)

        reset = Button(
            label="Về mặc định", emoji="♻️", style=discord.ButtonStyle.secondary,
            row=3, custom_id=self._cid("av:reset"),
        )
        reset.callback = self._on_reset_default
        self.add_item(reset)

        ok = Button(
            label="Xác nhận", emoji="✅", style=discord.ButtonStyle.success,
            row=3, custom_id=self._cid("av:ok"),
        )
        ok.callback = self._on_confirm
        self.add_item(ok)

    # ----- feature tab: weather -----

    def _build_weather(self) -> None:
        rt = self._rt()
        row = 3
        if self.is_admin:
            fx_on = self._fx_enabled(rt)
            toggle = Button(
                label="⚡ Hiệu ứng động trên map: " + ("BẬT" if fx_on else "TẮT"),
                emoji="⚡" if fx_on else "🚫",
                style=discord.ButtonStyle.success if fx_on else discord.ButtonStyle.secondary,
                row=row, custom_id=self._cid("wx:fx"),
            )
            toggle.callback = self._on_fx_toggle
            self.add_item(toggle)

            refresh = Button(
                label="Làm mới trạng thái", emoji="🔄",
                style=discord.ButtonStyle.secondary, row=row,
                custom_id=self._cid("wx:refresh"),
            )
            refresh.callback = self._on_weather_refresh
            self.add_item(refresh)
        else:
            note = Button(
                label="Chỉ admin được đổi hiệu ứng", emoji="🔒",
                style=discord.ButtonStyle.secondary, row=row,
                custom_id=self._cid("wx:locked"), disabled=True,
            )
            self.add_item(note)

    def _weather_status_lines(self) -> list:
        """Weather status text shared by the embed and (indirectly) tests."""
        rt = self._rt()
        try:
            from rendering.hub_renderer import WEATHER
        except Exception:
            WEATHER = {}
        try:
            from game.weather import compute_modifiers
        except Exception:
            compute_modifiers = None

        key = getattr(rt, "weather_key", "sun_clouds") if rt is not None else "sun_clouds"
        lines = [f"**Thời tiết:** {WEATHER.get(key, key)} (`{key}`)"]

        fx = self._fx_enabled(rt)
        animated = key in {"rain", "heavy_rain", "snow", "cold", "wind", "storm"}
        if animated:
            lines.append(
                "**Hiệu ứng trên map:** " + ("⚡ đang chạy (GIF động)" if fx else "tắt — map render PNG nhanh")
            )
        else:
            lines.append("**Hiệu ứng trên map:** không cần cho thời tiết này")

        ws = getattr(rt, "weather_state", None) if rt is not None else None
        if ws is not None and compute_modifiers is not None:
            import time

            m = compute_modifiers(ws)
            r = ws.ratios()
            fetched = time.strftime("%H:%M", time.localtime(ws.fetched_at)) if ws.fetched_at else "?"
            lines.append(
                f"**Thời tiết thật (VN):** {len(ws.stations)} trạm, {fetched}\n"
                f"- mưa {r['rain_ratio']:.0%} · bão {r['storm_ratio']:.0%} · "
                f"mây {r['cloud_ratio']:.0%} · nắng nóng {r['heat_index']:.0%}\n"
                f"**Buff:** coin x{m.coin_mult} · mana x{m.mana_regen_mult} · "
                f"hp x{m.hp_regen_mult} · xp x{m.xp_mult}"
                + (" — 🌩 STORM EVENT!" if getattr(m, "storm_event", False) else "")
            )
        else:
            lines.append("_Chưa có snapshot thời tiết thật (chờ lần fetch đầu)._")
        return lines

    # ----- embed -----

    def build_embed(self) -> discord.Embed:
        rt = self._rt()
        if self.tab == "overview":
            players = len(rt.state.get_visible_players()) if rt is not None else 0
            e = discord.Embed(
                title="⚙️ Cài đặt",
                description=(
                    "Tất cả chức năng của bạn nằm ở đây. Chọn một chức năng "
                    "bên dưới để mở bảng điều khiển riêng của nó."
                ),
                color=ACCENT,
            )
            for key, name, summary in FEATURES:
                e.add_field(
                    name=name,
                    value=f"{summary}\n→ _{self._feature_state(rt, key)}_",
                    inline=False,
                )
            e.add_field(name="👥 Trên map", value=str(players), inline=True)
            e.set_footer(text="Chọn chức năng ở menu phía trên · ❌ để đóng")
            return e

        if self.tab == "move":
            auto = self._auto_armed(rt)
            e = discord.Embed(
                title="🏃 Di chuyển",
                description=(
                    "Tuỳ chỉnh cách nhân vật phản ứng với D-pad.\n\n"
                    "**Bước/click** — số block mỗi lần bấm: chu kỳ 1 → 3 → 5.\n"
                    "**Auto-move** — khi bật, nhân vật chạy liên tục theo hướng "
                    "bạn bấm lần cuối (bấm lại để dừng)."
                ),
                color=ACCENT,
            )
            e.add_field(name="👣 Bước/click", value=str(self._step_size(rt)), inline=True)
            e.add_field(
                name="🎬 Auto-move",
                value="Đang chờ hướng" if auto else "Tắt",
                inline=True,
            )
            e.set_footer(text="Bấm nút bên dưới để thay đổi")
            return e

        if self.tab == "avatar":
            e = discord.Embed(title="🖼️ Avatar", color=ACCENT_AVATAR)
            lines = [f"**Avatar hiện tại:** {self._current_desc()}"]
            _sid, label = self._pending_desc()
            if label is not None:
                lines.append(f"**Đang chọn:** {label}")
            else:
                lines.append(
                    "_Chọn avatar ở danh sách bên dưới (thể loại → avatar mặc định "
                    "hoặc emoji server), rồi bấm ✅ Xác nhận._"
                )
            if self.status:
                lines.append(self.status)
            e.description = "\n".join(lines)
            e.set_footer(
                text=f"{len(self._avatars())} avatar mặc định · Twemoji (CC-BY 4.0)"
            )
            return e

        # weather
        e = discord.Embed(
            title="🌦️ Thời tiết",
            description="\n".join(self._weather_status_lines()),
            color=ACCENT_WEATHER,
        )
        if self.is_admin:
            e.add_field(
                name="Quản trị",
                value="Dùng nút ⚡ bên dưới để bật/tắt hiệu ứng động (GIF) trên map.",
                inline=False,
            )
        else:
            e.set_footer(text="Hiệu ứng động do admin quản lý")
        return e

    # ----- callbacks -----

    def _make_tab_cb(self, target: str):
        async def cb(interaction: discord.Interaction) -> None:
            self.tab = target
            self.status = None
            self._build()
            await interaction.response.edit_message(
                embed=self.build_embed(), view=self
            )

        return cb

    async def _on_feature_select(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        target = values[0] if values else "overview"
        self.tab = target if target in {"move", "avatar", "weather"} else "overview"
        self.status = None
        self._build()
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self
        )

    async def _cycle_step(self, rt) -> str:
        screen = self._screen(rt)
        if screen is not None:
            screen.step_size = STEP_CYCLE.get(screen.step_size, 1)
            # Persist the preference on the Player row (survives restarts).
            player = rt.state.get_player(self.user_id) if rt is not None else None
            if player is not None and player.step_size != screen.step_size:
                player.step_size = screen.step_size
                if getattr(self.manager, "db", None) is not None:
                    self.manager._schedule_save(rt, player)
            return f"👣 Bước/click bây giờ: {screen.step_size}"
        if rt is not None:
            rt.step_size = STEP_CYCLE.get(rt.step_size, 1)
            return f"👣 Bước/click bây giờ: {rt.step_size}"
        return "Chưa có map."

    async def _toggle_auto(self, rt) -> str:
        target = self._screen(rt) if rt is not None else None
        if target is None and rt is not None:
            target = rt
        if target is None:
            return "Chưa có map."
        target.auto_armed = not target.auto_armed
        target.auto_running = False
        state = "chờ hướng (bấm mũi tên để chạy)" if target.auto_armed else "tắt"
        return f"🎬 Auto-move: {state}"

    async def _apply_fx(self, rt, enabled: bool) -> str:
        if rt is None:
            return "Chưa có map."
        rt.weather_fx_enabled = bool(enabled)
        # Fan out: re-render every screen (FX gate changes the render mode)
        # and refresh hubs (the weather tab shows the new state).
        try:
            co = getattr(self.manager, "coalescer", None)
            if co is not None:
                for uid, screen in rt.screens.items():
                    if getattr(screen, "message_id", None):
                        co.schedule((self.channel_id, uid), {"user_id": uid})
        except Exception as e:  # noqa: BLE001 — fan-out must not break the toggle
            log.warning("[SETTINGS] fx fan-out screens failed: %s", e)
        try:
            hub = getattr(self.manager, "hub_coalescer", None)
            if hub is not None:
                hub.schedule(rt.channel_id, {"type": "weather"})
        except Exception as e:  # noqa: BLE001
            log.warning("[SETTINGS] fx fan-out hub failed: %s", e)
        return (
            "⚡ **Hiệu ứng thời tiết động: BẬT** — map sẽ render GIF động "
            "(nặng hơn, có thể chậm khi nhiều người)." if enabled else
            "🚫 **Hiệu ứng thời tiết động: TẮT** — map render PNG nhanh."
        )

    async def _on_step_btn(self, interaction: discord.Interaction) -> None:
        msg = await self._cycle_step(self._rt())
        self._build()
        await interaction.response.edit_message(
            content=msg, embed=self.build_embed(), view=self
        )

    async def _on_auto_btn(self, interaction: discord.Interaction) -> None:
        msg = await self._toggle_auto(self._rt())
        self._build()
        await interaction.response.edit_message(
            content=msg, embed=self.build_embed(), view=self
        )

    async def _on_fx_toggle(self, interaction: discord.Interaction) -> None:
        if not self.is_admin:
            await interaction.response.send_message(
                "❌ Chỉ admin mới được đổi hiệu ứng thời tiết.",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return
        rt = self._rt()
        msg = await self._apply_fx(rt, not self._fx_enabled(rt))
        self.status = msg
        self._build()
        await interaction.response.edit_message(
            content=msg, embed=self.build_embed(), view=self
        )

    async def _on_weather_refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self
        )

    async def _on_category(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        if values:
            self.selected_category = values[0]
        self._build()
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self
        )

    async def _on_default_avatar(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        v = values[0] if values else None
        if not v or v == "__none__":
            await interaction.response.defer()
            return
        if v.startswith("twe:"):
            self.pending = ("twe", v[len("twe:"):])
        self.status = None
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self
        )

    async def _on_server_emoji(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        v = values[0] if values else None
        if not v or not v.startswith("emoji:"):
            await interaction.response.defer()
            return
        self.pending = ("emoji", v[len("emoji:"):])
        self.status = None
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self
        )

    async def _on_random(self, interaction: discord.Interaction) -> None:
        entries = self._avatars()
        if entries:
            a = random.choice(entries)
            self.pending = ("twe", a["id"][len("twe:"):])
        self.status = None
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self
        )

    async def _on_reset_default(self, interaction: discord.Interaction) -> None:
        self.pending = ("default", None)
        self.status = None
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self
        )

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if self.pending is None:
            await interaction.response.send_message(
                "Hãy chọn một avatar trước đã (hoặc bấm ♻️ Về mặc định).",
                ephemeral=True, delete_after=EPHEMERAL_WARN,
            )
            return
        kind, val = self.pending
        sprite_id = {"twe": f"twe:{val}", "emoji": f"emoji:{val}", "default": ""}.get(kind, "")
        rt = self._rt()
        p = rt.state.get_player(self.user_id) if rt is not None else None
        if p is None:
            await interaction.response.send_message(
                "Bạn chưa tham gia map.", ephemeral=True,
                delete_after=EPHEMERAL_WARN,
            )
            return

        p.sprite_id = sprite_id
        db = getattr(self.manager, "db", None)
        if db is not None:
            from persistence.repositories import save_player

            await save_player(db, self.channel_id, p)

        # Drop the stale cached token so the very next render uses the new avatar.
        try:
            self.manager.renderer.avatar_cache.invalidate(self.user_id)
        except Exception as e:  # noqa: BLE001 — decoration must never fail the confirm
            log.warning("[SETTINGS] avatar cache invalidate failed: %s", e)

        # Fan out: every player's screen shows the new token, every hub shows
        # the emblem badge (all players see each other's avatars).
        co = getattr(self.manager, "coalescer", None)
        if co is not None:
            for uid, screen in rt.screens.items():
                if getattr(screen, "message_id", None):
                    co.schedule((self.channel_id, uid), {"user_id": uid})
        hub = getattr(self.manager, "hub_coalescer", None)
        if hub is not None:
            for uid in list(rt.state.players):
                hub.schedule((self.channel_id, uid), {"focused_user_id": uid})

        self.tab = "avatar"
        self.pending = None
        self.status = "✅ **Đã đổi avatar!** Mọi người trên map sẽ thấy avatar mới."
        self._build()
        await interaction.response.edit_message(
            content=f"✅ Đã đổi avatar → {sprite_id or 'mặc định (mặt cười)'}",
            embed=self.build_embed(), view=self,
        )

    async def _on_close(self, interaction: discord.Interaction) -> None:
        self.stop()
        try:
            await interaction.response.edit_message(
                content="🔒 Đã đóng cài đặt.", embed=None, view=None
            )
        except (discord.NotFound, discord.HTTPException) as e:
            log.warning("[SETTINGS] close failed: %s", e)
