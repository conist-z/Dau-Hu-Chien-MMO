import asyncio
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import discord
from discord.ui import Select

from discord_ui.settings_view import (
    FEATURES,
    STEP_CYCLE,
    SettingsView,
    _unicode_char,
)
from game.state import Player

CHANNEL = 1
UID = 10

META = {
    "categories": ["animals", "faces", "food", "fantasy"],
    "category_labels": {"animals": "Động vật", "faces": "Khuôn mặt"},
    "attribution": "Twemoji CC-BY 4.0",
}


def run(coro):
    return asyncio.run(coro)


class FakeAvatarCache:
    def default_avatars(self):
        return [
            {"id": "twe:aaa", "unicode": "1f436", "label": "Chó", "category": "animals"},
            {"id": "twe:bbb", "unicode": "1f431", "label": "Mèo", "category": "animals"},
            {"id": "twe:ccc", "unicode": "1f600", "label": "Cười", "category": "faces"},
        ]

    def manifest_meta(self):
        return dict(META)

    def invalidate(self, user_id):
        self.invalidated = getattr(self, "invalidated", 0) + 1


class FakeScreen:
    def __init__(self):
        self.step_size = 1
        self.auto_armed = False
        self.auto_running = False
        self.message_id = 111


class FakeState:
    def __init__(self, player):
        self.players = {UID: player}
        self._player = player

    def get_player(self, uid):
        return self._player if uid == UID else None

    def get_visible_players(self):
        return [self._player]


class FakeRT:
    def __init__(self):
        self.player = Player(user_id=UID, display_name="Tester")
        self.state = FakeState(self.player)
        self.screens = {UID: FakeScreen()}
        self.members = {}
        self.weather_key = "sun_clouds"
        self.weather_fx_enabled = False
        self.weather_state = None
        self.channel_id = CHANNEL


class FakeCoalescer:
    def __init__(self):
        self.scheduled = []

    def schedule(self, key, payload):
        self.scheduled.append((key, payload))


class FakeManager:
    def __init__(self):
        self.rt = FakeRT()
        self.runtimes = {CHANNEL: self.rt}
        self.renderer = SimpleNamespace(avatar_cache=FakeAvatarCache())
        self.coalescer = FakeCoalescer()
        self.hub_coalescer = FakeCoalescer()
        self.db = None
        self.bot_ref = None

    def get_runtime(self, channel_id):
        return self.runtimes.get(channel_id)

    def get_runtime_for(self, channel_id, user_id=None):
        return self.get_runtime(channel_id)


class FakeResponse:
    def __init__(self):
        self.calls = []

    async def edit_message(self, *a, **kw):
        if a:
            kw.setdefault("content", a[0])
        self.calls.append(("edit", kw))

    async def send_message(self, *a, **kw):
        if a:
            kw.setdefault("content", a[0])
        self.calls.append(("send", kw))

    async def defer(self):
        self.calls.append(("defer", {}))


class FakeInteraction:
    def __init__(self, values=None):
        self.data = {"values": values} if values is not None else {}
        self.response = FakeResponse()
        self.user = SimpleNamespace(id=UID)


def make_view(manager=None, tab="overview", admin=False):
    manager = manager or FakeManager()
    view = SettingsView(CHANNEL, UID, manager, tab=tab)
    view.is_admin = admin
    if tab == "weather":
        view._build()  # rebuild so admin-only widgets exist
    return view, manager


def _find(view, suffix):
    return next(
        c for c in view.children
        if getattr(c, "custom_id", "").endswith(f":{suffix}")
    )


def _has(view, suffix):
    return any(
        getattr(c, "custom_id", "").endswith(f":{suffix}")
        for c in view.children
    )


# --- helpers ---

def test_unicode_char_roundtrip():
    assert _unicode_char("1f436") == "\U0001F436"
    assert _unicode_char("1f9d1-200d-1f9d2") == "\U0001F9D1\u200D\U0001F9D2"
    assert _unicode_char("zzz") == ""
    assert _unicode_char(None) == ""


def test_step_cycle_table():
    assert STEP_CYCLE == {1: 3, 3: 5, 5: 1}


def test_features_registry_covers_move_avatar_weather():
    assert [k for k, _, _ in FEATURES] == ["move", "avatar", "weather"]


# --- overview hub ---

def test_overview_lists_every_feature_in_select():
    view, _ = make_view()
    sel = next(c for c in view.children if isinstance(c, Select))
    values = [o.value for o in sel.options]
    assert values == ["move", "avatar", "weather"]
    # Each entry carries a state summary on its label.
    assert all(o.label and "—" in o.label for o in sel.options)


def test_overview_embed_shows_feature_summaries():
    view, _ = make_view()
    e = view.build_embed()
    for key, name, summary in FEATURES:
        field = next(f for f in e.fields if f.name == name)
        assert summary in field.value


def test_feature_select_opens_matching_tab():
    view, _ = make_view()
    sel = next(c for c in view.children if isinstance(c, Select))
    inter = FakeInteraction(values=["weather"])
    run(sel.callback(inter))
    assert view.tab == "weather"
    embed = inter.response.calls[0][1]["embed"]
    assert "Thời tiết" in embed.title


def test_overview_quick_toggles_still_work():
    view, manager = make_view()
    step = _find(view, "step")
    inter = FakeInteraction()
    run(step.callback(inter))
    assert manager.rt.screens[UID].step_size == 3
    auto = _find(view, "auto")
    run(auto.callback(FakeInteraction()))
    assert manager.rt.screens[UID].auto_armed is True


# --- move tab ---

def test_move_tab_steps_and_toggles():
    view, manager = make_view(tab="move")
    step = _find(view, "step")
    inter = FakeInteraction()
    run(step.callback(inter))
    assert manager.rt.screens[UID].step_size == 3
    auto = _find(view, "auto")
    run(auto.callback(FakeInteraction()))
    assert manager.rt.screens[UID].auto_armed is True
    embed = inter.response.calls[0][1]["embed"]
    assert "Di chuyển" in embed.title


# --- avatar tab ---

def test_avatar_tab_builds_pickers_and_action_row():
    view, _ = make_view(tab="avatar")
    assert any(isinstance(c, Select) for c in view.children), "category select missing"
    for key in ("cat", "def", "rand", "reset", "ok"):
        assert _has(view, key), f"missing {key}"


def test_avatar_category_and_default_select_flow():
    view, _ = make_view(tab="avatar")
    cat = _find(view, "cat")
    assert {o.value for o in cat.options} == {"animals", "faces", "food", "fantasy"}
    sel = _find(view, "def")
    assert sel.options, "avatar select must list bundled avatars"
    inter = FakeInteraction(values=["twe:aaa"])
    run(sel.callback(inter))
    assert view.pending == ("twe", "aaa")
    embed = inter.response.calls[0][1]["embed"]
    assert "Chó" in embed.description


def test_avatar_confirm_persists_and_fans_out():
    view, manager = make_view(tab="avatar")
    sel = _find(view, "def")
    run(sel.callback(FakeInteraction(values=["twe:bbb"])))
    ok = _find(view, "ok")
    inter = FakeInteraction()
    run(ok.callback(inter))
    rt = manager.rt
    assert rt.player.sprite_id == "twe:bbb"
    assert manager.renderer.avatar_cache.invalidated == 1
    screen_keys = [k for k, _ in manager.coalescer.scheduled]
    assert (CHANNEL, UID) in screen_keys
    hub_keys = [k for k, _ in manager.hub_coalescer.scheduled]
    assert (CHANNEL, UID) in hub_keys
    assert view.tab == "avatar"
    assert "Đã đổi avatar" in view.status


def test_avatar_confirm_reset_sets_empty_sprite():
    view, manager = make_view(tab="avatar")
    view.pending = ("default", None)
    ok = _find(view, "ok")
    run(ok.callback(FakeInteraction()))
    assert manager.rt.player.sprite_id == ""


# --- weather tab ---

def test_weather_tab_readonly_for_players():
    view, manager = make_view(tab="weather", admin=False)
    manager.rt.weather_fx_enabled = False
    view._build()
    # No toggle for regular players; a locked note instead.
    assert not _has(view, "fx")
    assert _has(view, "locked")
    e = view.build_embed()
    assert "Thời tiết" in e.title
    assert "sun_clouds" in e.description


def test_weather_fx_default_off_and_toggle_fans_out():
    view, manager = make_view(tab="weather", admin=True)
    rt = manager.rt
    assert rt.weather_fx_enabled is False, "FX must default to OFF (perf)"
    toggle = _find(view, "fx")
    inter = FakeInteraction()
    run(toggle.callback(inter))
    assert rt.weather_fx_enabled is True
    # Every screen re-renders + hub refreshes.
    screen_keys = [k for k, _ in manager.coalescer.scheduled]
    assert (CHANNEL, UID) in screen_keys
    assert manager.hub_coalescer.scheduled, "hub must refresh on FX change"
    # Toggle back off.
    toggle = _find(view, "fx")
    run(toggle.callback(FakeInteraction()))
    assert rt.weather_fx_enabled is False


def test_weather_fx_toggle_denied_for_non_admin():
    view, manager = make_view(tab="weather", admin=False)
    # Force-inject the toggle callback path (as if a stale admin view were
    # pressed by a non-admin): the guard must answer with the admin-only toast.
    view.is_admin = False
    inter = FakeInteraction()
    run(view._on_fx_toggle(inter))
    kind, kw = inter.response.calls[0]
    assert kind == "send" and kw.get("ephemeral") is True
    assert manager.rt.weather_fx_enabled is False


def test_weather_status_shows_snapshot_when_present():
    view, manager = make_view(tab="weather")
    manager.rt.weather_key = "rain"
    e = view.build_embed()
    assert "rain" in e.description
    assert "tắt" in e.description  # FX gate off


# --- navigation & closing ---

def test_nav_home_returns_to_overview():
    view, _ = make_view(tab="avatar")
    home = _find(view, "home")
    inter = FakeInteraction()
    run(home.callback(inter))
    assert view.tab == "overview"
    embed = inter.response.calls[0][1]["embed"]
    assert "Cài đặt" in embed.title


def test_open_with_interaction_resolves_admin():
    manager = FakeManager()

    class FakePerms:
        administrator = True
        manage_guild = False

    inter = FakeInteraction()
    inter.user = SimpleNamespace(
        id=UID, guild_permissions=FakePerms(), display_name="Boss"
    )
    inter.channel_id = CHANNEL
    inter.guild = None
    panel = SettingsView.open_with_interaction(manager, inter)
    assert panel.is_admin is True
    assert panel.tab == "overview"


def test_close_button_stops_view():
    view, _ = make_view(tab="overview")
    close = _find(view, "close")
    inter = FakeInteraction()
    run(close.callback(inter))
    kind, kw = inter.response.calls[0]
    assert kind == "edit"
    assert kw.get("view") is None
