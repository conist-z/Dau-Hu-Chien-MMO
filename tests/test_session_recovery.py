"""Regression tests for the silent session-repair policy (user spec):

- Default: ANY glitch is repaired SILENTLY until a complete
  screen -> controls -> hub stack exists (within a bounded window).
- Window expiry: half-sent messages cleaned + ONE consolidated notice.
- Terminal cases (destruction, inactivity) skip repair and log immediately.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

import discord_ui.session_recovery as sr
from discord_ui.session_recovery import (
    _attempt_missing,
    cleanup_half_stack,
    repair_session,
    schedule_repair,
)
from game.manager import PlayerScreen


class _Resp:
    def __init__(self, status: int):
        self.status = status
        self.reason = "x"


class FakeChannel:
    """Channel whose fetch_message 404s for ids not in live_ids; send works."""

    def __init__(self, live_ids=()):
        self.live_ids = set(live_ids)
        self.sent = []
        self.deleted = []
        self._next_id = 5000

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

    async def send(self, *args, **kwargs):
        self._next_id += 1
        self.sent.append(self._next_id)
        return SimpleNamespace(id=self._next_id)


def make_rt():
    rt = SimpleNamespace(
        channel_id=100,
        message_id=None,
        hub_message_id=None,
        state=SimpleNamespace(),
        screens={},
        map_data=SimpleNamespace(width=32, height=32, spawn=(5, 5)),
        weather_key=None,
        lightning_seed=0,
        members={},
        inventories={},
    )
    rt.state.get_player = lambda uid: SimpleNamespace(
        user_id=uid, display_name="P", screen_message_id=None,
        controls_message_id=None, hub_message_id=None,
        x=5, y=5,
    )
    return rt


class FakeManager:
    def __init__(self, rt, channel):
        self.rt = rt
        self.channel = channel
        self.db = None
        self.renderer = None
        self.bot_ref = SimpleNamespace(
            get_channel=MagicMock(return_value=channel),
            add_view=MagicMock(),
        )
        self.session_adapter = SimpleNamespace(suppress_ids=set())

    def get_runtime(self, cid):
        return self.rt

    def get_runtime_for(self, cid, user_id=None):
        return self.rt


# --- completeness check --------------------------------------------------------

def test_attempt_missing_reports_pieces_in_order():
    rt = make_rt()
    assert _attempt_missing(rt, 42) == "screen"  # no record at all
    rt.screens[42] = PlayerScreen(user_id=42)
    assert _attempt_missing(rt, 42) == "screen"
    rt.screens[42].message_id = 1000
    assert _attempt_missing(rt, 42) == "controls"
    rt.screens[42].controls_message_id = 1500
    assert _attempt_missing(rt, 42) == "hub"
    rt.screens[42].hub_message_id = 900  # older than the screen: broken order
    assert _attempt_missing(rt, 42) == "order"
    rt.screens[42].hub_message_id = 2000
    assert _attempt_missing(rt, 42) is None


# --- repair loop ---------------------------------------------------------------

def test_repair_noop_when_stack_already_complete():
    rt = make_rt()
    rt.screens[42] = PlayerScreen(
        user_id=42, message_id=1000, controls_message_id=1500, hub_message_id=2000
    )
    ch = FakeChannel(live_ids=[1000, 1500, 2000])
    mgr = FakeManager(rt, ch)
    assert asyncio.run(repair_session(mgr, rt, 42, ch, timeout=0.2)) is True
    assert ch.sent == [] and ch.deleted == []


def test_repair_builds_missing_hub_then_succeeds(monkeypatch):
    monkeypatch.setattr(sr, "SESSION_REPAIR_RETRY_DELAY_SEC", 0.01)
    rt = make_rt()
    rt.screens[42] = PlayerScreen(user_id=42, message_id=1000, controls_message_id=1500)
    ch = FakeChannel(live_ids=[1000, 1500])
    mgr = FakeManager(rt, ch)

    async def fake_ensure_hub(manager, rt, channel, user_id, attempts):
        rt.screens[42].hub_message_id = 2000
        return True

    monkeypatch.setattr(sr, "_ensure_hub", fake_ensure_hub)
    assert asyncio.run(repair_session(mgr, rt, 42, ch, timeout=1.0)) is True
    assert rt.screens[42].hub_message_id == 2000
    assert ch.deleted == []


def test_repair_timeout_cleans_and_posts_one_notice(monkeypatch):
    monkeypatch.setattr(sr, "SESSION_REPAIR_RETRY_DELAY_SEC", 0.01)
    rt = make_rt()
    # The SCREEN piece is missing forever (mint always fails) while stale
    # hub/controls ids linger — repair can never complete inside the window.
    rt.screens[42] = PlayerScreen(
        user_id=42, message_id=None, controls_message_id=222, hub_message_id=333
    )
    ch = FakeChannel(live_ids=[])
    mgr = FakeManager(rt, ch)

    async def fail_mint(*a, **k):
        return False

    monkeypatch.setattr(sr, "_mint_screen", fail_mint)
    notices = []

    async def post_notice(detail, cleaned):
        notices.append((detail, cleaned))

    ok = asyncio.run(
        repair_session(mgr, rt, 42, ch, timeout=0.05, post_notice=post_notice)
    )
    assert ok is False
    # The half-sent messages (stale controls + hub) were deleted once each.
    assert sorted(ch.deleted) == [222, 333]
    assert rt.screens[42].message_id is None
    assert rt.screens[42].controls_message_id is None
    assert rt.screens[42].hub_message_id is None
    # Exactly ONE consolidated notice.
    assert len(notices) == 1
    player = rt.state.get_player(42)
    assert player.screen_message_id is None


def test_mint_screen_sends_image_only_message():
    """Regression: the repair-loop screen send must NOT attach the D-pad view.

    Attaching it merged screen + D-pad into ONE message (the reported bug);
    the D-pad is posted as its own message underneath by _ensure_controls."""
    rt = make_rt()
    rt.screens[42] = PlayerScreen(user_id=42)
    ch = FakeChannel(live_ids=[])
    mgr = FakeManager(rt, ch)

    sent_kwargs = []

    async def send(*args, **kwargs):
        sent_kwargs.append(kwargs)
        return SimpleNamespace(id=7000)

    ch.send = send

    class FakeRenderer:
        async def render(self, *a, **k):
            from PIL import Image as PILImage

            return SimpleNamespace(
                composite=object(),
                image=PILImage.new("RGBA", (8, 8)),
                filename="map.png",
                frames=[],
            )

    mgr.renderer = FakeRenderer()

    async def run():
        assert await sr._mint_screen(mgr, rt, ch, 42, []) is True

    asyncio.run(run())
    # The screen message went out WITHOUT a view (image-only).
    assert "view" not in sent_kwargs[0]


def test_mint_screen_sets_camera():
    """The re-minted screen recentres its camera on the player before render."""
    rt = make_rt()
    rt.screens[42] = PlayerScreen(user_id=42)
    ch = FakeChannel(live_ids=[])
    mgr = FakeManager(rt, ch)


def test_cleanup_suppresses_its_own_deletes():
    rt = make_rt()
    rt.screens[42] = PlayerScreen(user_id=42, message_id=111, hub_message_id=333)
    ch = FakeChannel(live_ids=[])
    mgr = FakeManager(rt, ch)
    deleted = asyncio.run(cleanup_half_stack(mgr, rt, 42, ch))
    assert deleted == 2
    assert {111, 333} <= mgr.session_adapter.suppress_ids


def test_schedule_repair_skips_when_complete_or_busy():
    rt = make_rt()
    rt.screens[42] = PlayerScreen(
        user_id=42, message_id=1000, controls_message_id=1500, hub_message_id=2000
    )
    ch = FakeChannel(live_ids=[1000, 1500, 2000])
    mgr = FakeManager(rt, ch)
    schedule_repair(mgr, rt, 42, why="test")  # complete: no task, no churn
    assert ch.sent == []

    # Busy lock: no second loop.
    rt.screens[42].hub_message_id = None
    from discord_ui.locks import player_lock

    lock = player_lock(100, 42)

    async def hold():
        async with lock:
            schedule_repair(mgr, rt, 42, why="test-busy")
            await asyncio.sleep(0)

    asyncio.run(hold())
    assert ch.sent == []


def test_schedule_repair_spawns_task_when_incomplete(monkeypatch):
    rt = make_rt()
    rt.screens[42] = PlayerScreen(user_id=42, message_id=1000)
    ch = FakeChannel(live_ids=[1000])
    mgr = FakeManager(rt, ch)

    ran = []

    async def fake_repair(*a, **k):
        ran.append(k.get("reason"))

    monkeypatch.setattr(sr, "repair_session", fake_repair)

    async def run():
        schedule_repair(mgr, rt, 42, why="glitch-test")
        await asyncio.sleep(0.01)  # let the spawned task run

    asyncio.run(run())
    assert ran == ["glitch-test"]
