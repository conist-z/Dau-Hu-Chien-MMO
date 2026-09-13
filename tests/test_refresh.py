import asyncio
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

path = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(path))

from discord_ui.refresh import (  # noqa: E402
    RefreshScheduler,
    clear_pair,
    hub_signature,
    reattach_hub_under_screen,
    schedule_screen_refresh,
    verify_pair,
)
from game.manager import PlayerScreen  # noqa: E402
from game.state import GameState  # noqa: E402


class _Resp:
    def __init__(self, status: int):
        self.status = status
        self.reason = "x"


class FakeChannel:
    """Channel whose fetch_message 404s for any id not in `live_ids`."""

    def __init__(self, live_ids=()):
        self.live_ids = set(live_ids)
        self.deleted = []

    async def fetch_message(self, message_id):
        if message_id in self.live_ids:
            return SimpleNamespace(id=message_id)
        raise discord.NotFound(_Resp(404), "Unknown Message")

    def get_partial_message(self, message_id):
        return SimpleNamespace(delete=AsyncMock(side_effect=self._delete(message_id)))

    def _delete(self, message_id):
        async def _cb():
            self.deleted.append(message_id)
        return _cb


def make_rt(screen_id=1000, hub_id=2000):
    rt = SimpleNamespace(
        channel_id=100,
        message_id=screen_id,
        hub_message_id=hub_id,
        state=GameState(100, "t"),
        screens={},
        map_data=SimpleNamespace(map_id="t", display_name="T", width=8, height=8),
        npc_map=SimpleNamespace(npcs=[]),
    )
    screen = PlayerScreen(user_id=42, message_id=screen_id, hub_message_id=hub_id)
    rt.screens[42] = screen
    rt.state.add_player(42, "P", 2, 2)
    return rt


def make_manager(rt, channel):
    return SimpleNamespace(
        runtimes={rt.channel_id: rt},
        bot_ref=None,
        db=None,
        renderer=None,
        hub_renderer=None,
        hub_coalescer=SimpleNamespace(schedule=MagicMock()),
        coalescer=SimpleNamespace(schedule=MagicMock(), pending=MagicMock(return_value=0)),
    )


# --- hub signature: HUD-visible changes --------------------------------------

def test_hub_signature_ignores_accelerated_clock_minute():
    """The in-game clock minute flips every ~1.25 real seconds; the signature
    must NOT follow it or the hub would be re-uploaded about once per second
    (429s -> zero-byte attachments -> 'image failed to load')."""
    rt = make_rt()
    s1 = hub_signature(rt, 42)
    import rendering.daynight as daynight
    old = daynight.ingame_seconds
    daynight.ingame_seconds = lambda: (old() // 60 + 1) * 60
    try:
        s2 = hub_signature(rt, 42)
    finally:
        daynight.ingame_seconds = old
    assert s1 == s2


def test_hub_signature_changes_on_weather_and_stats():
    rt = make_rt()
    s1 = hub_signature(rt, 42)
    rt.weather_key = "storm"
    s2 = hub_signature(rt, 42)
    assert s1 != s2
    p = rt.state.get_player(42)
    p.hp -= 10
    s3 = hub_signature(rt, 42)
    assert s2 != s3


def test_hub_signature_changes_on_inventory():
    from game.inventory import Inventory

    rt = make_rt()
    rt.inventories = {42: Inventory()}
    s1 = hub_signature(rt, 42)
    rt.inventories[42].add("wood", 3)
    assert hub_signature(rt, 42) != s1


# --- pair coupling: screen dies => hub dies ----------------------------------

def test_screen_gone_drops_hub_and_clears_ids():
    rt = make_rt()
    ch = FakeChannel(live_ids=[2000])  # hub alive, screen deleted
    mgr = make_manager(rt, ch)
    outcome = asyncio.run(verify_pair(mgr, rt, 42, ch))
    assert outcome == "screen_missing"
    # Hub message deleted, pair ids cleared, flush scheduled (no lone hub).
    assert ch.deleted == [2000]
    screen = rt.screens[42]
    assert screen.message_id is None and screen.hub_message_id is None
    player = rt.state.get_player(42)
    assert player.screen_message_id is None and player.hub_message_id is None
    mgr.hub_coalescer.schedule.assert_called()


def test_screen_gone_keeps_nothing_even_if_transient_hub_fetch():
    rt = make_rt()
    # Screen deleted AND hub deleted: clear_pair must tolerate both gone.
    ch = FakeChannel(live_ids=[])
    mgr = make_manager(rt, ch)
    assert asyncio.run(verify_pair(mgr, rt, 42, ch)) == "screen_missing"
    assert rt.screens[42].hub_message_id is None


def test_transient_screen_fetch_verifies_nothing():
    class Flaky:
        async def fetch_message(self, message_id):
            raise discord.HTTPException(_Resp(500), "server error")

    rt = make_rt()
    assert asyncio.run(verify_pair(make_manager(rt, Flaky()), rt, 42, Flaky())) == "unknown"
    # Nothing touched on a transient signal.
    assert rt.screens[42].message_id == 1000
    assert rt.screens[42].hub_message_id == 2000


def test_hub_gone_recreated_under_live_screen():
    rt = make_rt()
    ch = FakeChannel(live_ids=[1000])  # screen alive, hub deleted
    mgr = make_manager(rt, ch)
    outcome = asyncio.run(verify_pair(mgr, rt, 42, ch))
    assert outcome == "hub_missing"
    assert ch.deleted == []  # nothing to delete — the hub is already gone
    assert rt.screens[42].hub_message_id is None
    mgr.hub_coalescer.schedule.assert_called()


def test_healthy_pair_is_left_alone():
    rt = make_rt()
    ch = FakeChannel(live_ids=[1000, 2000])
    mgr = make_manager(rt, ch)
    assert asyncio.run(verify_pair(mgr, rt, 42, ch)) == "ok"
    assert ch.deleted == []
    mgr.hub_coalescer.schedule.assert_not_called()


def test_inverted_pair_re_pairs():
    # Screen re-created later than the hub -> newer snowflake id ABOVE the hub.
    rt = make_rt(screen_id=5000, hub_id=2000)
    ch = FakeChannel(live_ids=[5000, 2000])
    mgr = make_manager(rt, ch)
    assert asyncio.run(verify_pair(mgr, rt, 42, ch)) == "inverted"
    assert ch.deleted == [2000]  # stale hub dropped...
    assert rt.screens[42].hub_message_id is None
    mgr.hub_coalescer.schedule.assert_called()  # ...fresh one scheduled under the screen


def test_clear_pair_schedules_flush_only():
    rt = make_rt()
    ch = FakeChannel(live_ids=[2000])
    mgr = make_manager(rt, ch)
    asyncio.run(clear_pair(mgr, rt, 42, ch))
    assert rt.screens[42].message_id is None
    mgr.hub_coalescer.schedule.assert_called_once()


def test_reattach_hub_under_screen_drops_old_hub():
    rt = make_rt()
    ch = FakeChannel(live_ids=[1000, 2000])
    mgr = make_manager(rt, ch)
    rt.screens[42].message_id = 3000  # new screen minted below old hub
    asyncio.run(reattach_hub_under_screen(mgr, rt, 42, ch))
    assert ch.deleted == [2000]
    assert rt.screens[42].hub_message_id is None
    mgr.hub_coalescer.schedule.assert_called_once()


# --- screen auto-refresh skips busy screens -----------------------------------

def test_schedule_screen_refresh_skips_busy_or_missing():
    rt = make_rt()
    rt.screens[42].rendering = True
    mgr = make_manager(rt, FakeChannel())
    assert schedule_screen_refresh(mgr, rt, 42) is False
    rt.screens[42].rendering = False
    rt.screens[42].message_id = None
    assert schedule_screen_refresh(mgr, rt, 42) is False
    rt.screens[42].message_id = 1000
    assert schedule_screen_refresh(mgr, rt, 42) is True
    mgr.coalescer.schedule.assert_called_once_with((100, 42), {"user_id": 42})


# --- the pacer ----------------------------------------------------------------

def test_scheduler_ticks_hub_and_pair_cadence():
    rt = make_rt()
    ch = FakeChannel(live_ids=[1000, 2000])
    mgr = make_manager(rt, ch)
    sched = RefreshScheduler(mgr)
    pf = SimpleNamespace(hub_sig=(), next_hub=0.0, next_screen=0.0, next_pair=0.0)
    import discord_ui.refresh as refresh_mod

    old_cadence = (refresh_mod.HUB_REFRESH_SECONDS, refresh_mod.PAIR_CHECK_SECONDS)
    refresh_mod.HUB_REFRESH_SECONDS = 5.0
    refresh_mod.PAIR_CHECK_SECONDS = 60.0
    try:
        asyncio.run(sched._tick_player(rt, 42, pf, now=100.0))
        assert pf.next_hub == 105.0  # hub beat armed 5s out
        assert pf.next_pair == 160.0  # pair check armed 60s out
        assert pf.hub_sig == hub_signature(rt, 42)
        assert mgr.hub_coalescer.schedule.call_count == 1
        assert mgr.coalescer.schedule.call_count == 1  # screen beat (8s)
        # Same beat window again: no duplicate hub/screen flush.
        mgr.hub_coalescer.schedule.reset_mock()
        mgr.coalescer.schedule.reset_mock()
        asyncio.run(sched._tick_player(rt, 42, pf, now=101.0))
        mgr.hub_coalescer.schedule.assert_not_called()
        mgr.coalescer.schedule.assert_not_called()
    finally:
        refresh_mod.HUB_REFRESH_SECONDS, refresh_mod.PAIR_CHECK_SECONDS = old_cadence


def test_scheduler_flushes_hub_on_signature_change():
    rt = make_rt()
    ch = FakeChannel(live_ids=[1000, 2000])
    mgr = make_manager(rt, ch)
    sched = RefreshScheduler(mgr)
    pf = SimpleNamespace(hub_sig=hub_signature(rt, 42), next_hub=1e9, next_screen=1e9, next_pair=1e9)
    p = rt.state.get_player(42)
    p.coins += 5  # HUD-visible change between ticks
    asyncio.run(sched._tick_player(rt, 42, pf, now=0.0))
    mgr.hub_coalescer.schedule.assert_called()  # change pulled the flush forward
    assert pf.hub_sig == hub_signature(rt, 42)


# --- hub recreate path must not depend on another function's local import -----

def test_create_hub_message_saves_player_without_stale_import_dependency():
    """Regression: the hub recreate path (flush_hub_batch NotFound ->
    create_hub_message) crashed with NameError 'save_player' because
    _create_hub_message_locked relied on create_hub_message's function-level
    import. Call the locked function DIRECTLY (fresh process semantics: no
    save_player in the module namespace) — it must still save the player."""
    import discord_ui.hub_view as hv

    rt = make_rt()
    # Simulate a fresh interpreter where create_hub_message has never run:
    # ensure save_player is NOT resolvable as a module global.
    import persistence.repositories as repos
    import discord_ui.hub_view as hv
    assert not hasattr(hv, "save_player")

    saved = []

    class SendChannel:
        async def fetch_message(self, message_id):
            raise discord.NotFound(_Resp(404), "Unknown Message")

        async def send(self, **kwargs):
            return SimpleNamespace(id=4242)

    async def fake_adjacency(channel, sid, hid, preserve_ids=None, suppress_ids=None):
        return []

    import discord_ui.adjacency as adjacency

    from unittest.mock import patch

    with patch.object(adjacency, "enforce_adjacency", fake_adjacency), \
         patch.object(repos, "save_player", _spy_save(saved)):
        mgr = make_manager(rt, SendChannel())
        mgr.db = object()  # truthy so the save_player branch runs
        ok = asyncio.run(
            hv._create_hub_message_locked(
                mgr, rt, 100, SendChannel(), 42, 42
            )
        )
    assert ok is True
    assert saved == [42]


def _spy_save(saved):
    async def _save(db, channel_id, player):
        saved.append(player.user_id)
    return _save
