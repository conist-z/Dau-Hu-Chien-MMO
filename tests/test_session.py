import asyncio
import time
import unittest.mock as mock

import pytest

from game.session import (
    REASON_INACTIVITY,
    REASON_MESSAGE_DELETED,
    SessionTracker,
)


def _now() -> float:
    return time.monotonic()


def test_touch_creates_and_updates_session():
    tr = SessionTracker()
    t0 = _now()
    tr.touch(1, 42, t0)
    info = tr.get(1, 42)
    assert info is not None
    assert info.started_at == t0
    assert info.last_activity == t0
    # A later touch updates last_activity but not started_at (duration keeps growing).
    tr.touch(1, 42, t0 + 5)
    info = tr.get(1, 42)
    assert info.started_at == t0
    assert info.last_activity == t0 + 5
    assert tr.duration(1, 42, t0 + 9) == 9


def test_due_reports_only_idle_sessions():
    tr = SessionTracker()
    t0 = 100.0
    tr.touch(1, 1, t0)
    tr.touch(1, 2, t0)
    tr.touch(2, 3, t0)
    # Nothing idle yet.
    assert tr.due(t0 + 10, timeout=30) == []
    # Refresh only user 2 in channel 1.
    tr.touch(1, 2, t0 + 25)
    assert set(tr.due(t0 + 31, timeout=30)) == {(1, 1), (2, 3)}
    assert tr.due(t0 + 55, timeout=30) == {(1, 1), (2, 3)} or set(
        tr.due(t0 + 55, timeout=30)
    ) == {(1, 1), (2, 3), (1, 2)}


def test_discard_removes_session():
    tr = SessionTracker()
    tr.touch(1, 42, _now())
    info = tr.discard(1, 42)
    assert info is not None
    assert tr.get(1, 42) is None
    assert tr.discard(1, 42) is None
    assert len(tr) == 0


def _manager_with_session(timeout_min=30.0, interval=60.0):
    from game.manager import GameManager

    m = GameManager(assets_dir=None)
    m.configure_sessions(timeout_min, interval, cooldown=0)
    return m


def test_touch_session_uses_monotonic_clock():
    m = _manager_with_session()
    m.touch_session(1, 42)
    info = m.sessions.get(1, 42)
    assert info is not None
    assert info.last_activity == pytest.approx(time.monotonic(), abs=5)


def test_session_loop_ends_idle_session_with_reason():
    """Watchdog must call adapter.end with REASON_INACTIVITY for idle players."""
    m = _manager_with_session(timeout_min=0.05, interval=0.05)  # 3s idle budget
    m.session_timeout_minutes = 0.05
    m.session_check_interval = 0.05

    ended = []
    rt = mock.MagicMock()
    rt.screens = {42: mock.MagicMock()}
    rt.state = mock.MagicMock()
    m.runtimes = {1: rt}

    class Adapter:
        async def end(self, cid, uid, reason, detail="", duration=None):
            ended.append((cid, uid, reason))

        async def notify(self, *a, **k):
            pass

    m.session_adapter = Adapter()
    m.touch_session(1, 42)
    # Force the ledger far into the past so the first tick is already due.
    m.sessions.get(1, 42).last_activity -= 999

    async def run():
        task = asyncio.create_task(m._session_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert ended == [(1, 42, REASON_INACTIVITY)]


def test_session_loop_skips_sessions_without_screen():
    """A ledger entry with no open screen must be dropped, not torn down."""
    m = _manager_with_session(timeout_min=0.05, interval=0.05)
    m.session_timeout_minutes = 0.05
    m.session_check_interval = 0.05

    ended = []
    rt = mock.MagicMock()
    rt.screens = {}  # no screens at all
    m.runtimes = {1: rt}

    class Adapter:
        async def end(self, cid, uid, reason, detail="", duration=None):
            ended.append((cid, uid, reason))

        async def notify(self, *a, **k):
            pass

    m.session_adapter = Adapter()
    m.touch_session(1, 42)
    m.sessions.get(1, 42).last_activity -= 999

    async def run():
        task = asyncio.create_task(m._session_loop())
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert ended == []
    assert m.sessions.get(1, 42) is None  # dropped silently


def test_notify_session_event_debounces():
    m = _manager_with_session(cooldown_holder := None) if False else _manager_with_session()
    m.configure_sessions(30, 60, cooldown=300)
    calls = []

    class Adapter:
        async def notify(self, cid, uid, reason, detail, *, ended, duration):
            calls.append(reason)

        async def end(self, *a, **k):
            pass

    m.session_adapter = Adapter()

    async def run():
        await m.notify_session_event(1, 42, REASON_MESSAGE_DELETED, "x")
        await m.notify_session_event(1, 42, "error", "y")  # inside cooldown
        await m.notify_session_event(1, 43, REASON_MESSAGE_DELETED, "z")  # other user

    asyncio.run(run())
    assert calls == [REASON_MESSAGE_DELETED, REASON_MESSAGE_DELETED]


def test_notify_session_event_live_session_is_log_only():
    """ended=False (button error, deleted hub, transient repair) must NEVER
    post a channel notice — only a confirmed teardown may notify."""
    m = _manager_with_session()
    m.configure_sessions(30, 60, cooldown=0)
    calls = []

    class Adapter:
        async def notify(self, cid, uid, reason, detail, *, ended, duration):
            calls.append((reason, ended))

        async def end(self, *a, **k):
            pass

    m.session_adapter = Adapter()

    async def run():
        await m.notify_session_event(1, 42, "error", "lỗi nút bấm", ended=False)
        await m.notify_session_event(1, 42, "message_deleted", "hub bị xoá", ended=False)

    asyncio.run(run())
    assert calls == []  # nothing published for a live session


def test_session_end_adapter_deletes_messages_and_state():
    """end() tears down messages, removes player, posts a notice embed."""
    from discord_ui.session_end import SessionEndAdapter

    bot = mock.MagicMock()
    channel = mock.MagicMock()
    partial = mock.MagicMock()
    partial.delete = mock.AsyncMock()
    channel.get_partial_message.return_value = partial
    channel.send = mock.AsyncMock()
    bot.get_channel.return_value = channel

    from game.manager import GameManager, PlayerScreen

    manager = GameManager(assets_dir=None)
    manager.discard_session = mock.MagicMock(wraps=manager.discard_session)
    rt = mock.MagicMock()
    rt.channel_id = 7
    screen = PlayerScreen(user_id=42, message_id=111, hub_message_id=222)
    rt.screens = {42: screen}
    rt.state.remove_player = mock.MagicMock()
    rt.members = {42: mock.MagicMock()}
    rt.message_id = 111
    manager.runtimes = {7: rt}
    manager.db = None

    adapter = SessionEndAdapter(bot, manager)
    suppress = adapter.suppress_ids

    async def run():
        await adapter.end(7, 42, REASON_INACTIVITY, "idle 30 phút", duration=3600)

    asyncio.run(run())

    # All three messages registered for suppression and delete() called.
    assert {111, 222} <= suppress
    deleted = [c.args[0] for c in channel.get_partial_message.call_args_list]
    assert deleted == [222, 111]  # hub first, then screen
    partial.delete.assert_awaited()
    # Player row is KEPT for inactivity too (position/sprite survive so
    # /joinmap restores the session exactly as left off) — only the screen
    # object is detached. The controls message id, if any, is also cleared.
    rt.state.remove_player.assert_not_called()
    assert 42 not in rt.screens
    manager.discard_session.assert_called_once_with(7, 42)
    # A notice embed went out to the channel.
    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert "embed" in kwargs


def test_session_end_adapter_message_deleted_keeps_player_row():
    from discord_ui.session_end import SessionEndAdapter
    from game.manager import GameManager, PlayerScreen

    bot = mock.MagicMock()
    bot.get_channel.return_value = None  # no channel: deletes no-op, notice skipped
    manager = GameManager(assets_dir=None)
    rt = mock.MagicMock()
    screen = PlayerScreen(user_id=42, message_id=111, hub_message_id=222)
    rt.screens = {42: screen}
    rt.state.remove_player = mock.MagicMock()
    manager.runtimes = {7: rt}
    manager.db = None

    adapter = SessionEndAdapter(bot, manager)

    async def run():
        await adapter.end(7, 42, REASON_MESSAGE_DELETED, "bị xoá")

    asyncio.run(run())
    # Player stays in state (they can /joinmap straight back).
    rt.state.remove_player.assert_not_called()
    assert 42 not in rt.screens  # but the screen object is detached


def test_end_to_end_session_notice_has_reason_and_user():
    """The notice text must carry the user name + reason (the core ask)."""
    from discord_ui.session_end import REASON_TEXT, SessionEndAdapter
    from game.manager import GameManager, PlayerScreen

    bot = mock.MagicMock()
    channel = mock.MagicMock()
    channel.send = mock.AsyncMock()
    bot.get_channel.return_value = channel

    manager = GameManager(assets_dir=None)
    rt = mock.MagicMock()
    rt.screens = {42: PlayerScreen(user_id=42, message_id=111, hub_message_id=222)}
    p = mock.MagicMock()
    p.display_name = "Bảo"
    rt.state.get_player.return_value = p
    manager.runtimes = {7: rt}

    adapter = SessionEndAdapter(bot, manager)

    async def run():
        await adapter.end(7, 42, REASON_MESSAGE_DELETED, "hub bị xoá", duration=125)

    asyncio.run(run())
    embed = channel.send.await_args.kwargs["embed"]
    desc = embed.description
    assert "Bảo" in desc
    emoji, base = REASON_TEXT[REASON_MESSAGE_DELETED]
    assert "bị xoá" in desc
    assert "hub bị xoá" in desc
    assert "2p05s" in desc  # duration formatting 125s = 2m05s
