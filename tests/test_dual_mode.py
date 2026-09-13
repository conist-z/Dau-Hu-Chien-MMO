"""Dual-client "2 mode" agreement (1 account, web + Discord clients).

Covers: web attach takes control (player.mode -> "web"), web detach returns
control to Discord ("chat"), the web_controlled helper, the snapshot payload
carrying per-player mode, and the refresh pacer sitting out while the web
client holds the body.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.manager import GameManager
from web_api.snapshots import build_snapshot

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _gm():
    gm = GameManager(ASSETS)
    gm.create_runtime(1, "test-map")
    return gm


# ----- control agreement (web wins) -----

def test_attach_sets_web_mode():
    gm = _gm()
    rt = gm.runtimes[1]
    assert gm.register_web_session(1, 42, "tester")
    p = rt.state.get_player(42)
    assert p.is_web is True and p.mode == "web"
    assert gm.web_controlled(rt, 42) is True


def test_detach_returns_control_to_discord():
    gm = _gm()
    rt = gm.runtimes[1]
    gm.db = None
    gm._schedule_save = lambda rt, player: None
    gm.register_web_session(1, 42, "tester")
    gm.drop_web_session(1, 42)
    p = rt.state.get_player(42)
    assert p.is_web is False and p.mode == "chat"
    assert gm.web_controlled(rt, 42) is False


def test_discord_only_player_is_chat_mode():
    gm = _gm()
    rt = gm.runtimes[1]
    rt.state.add_player(7, "chat-only", 2, 2)
    assert gm.web_controlled(rt, 7) is False


def test_web_controlled_tolerates_none_runtime():
    gm = _gm()
    assert gm.web_controlled(None, 42) is False


# ----- snapshot payload -----

def test_snapshot_carries_mode_for_remote_players():
    gm = _gm()
    rt = gm.runtimes[1]
    rt.state.add_player(7, "chatter", 2, 2)  # Discord body (mode chat)
    gm.register_web_session(1, 42, "webby")  # web-controlled body
    snap = build_snapshot(rt, 42, seq=1)
    modes = {pl["id"]: pl.get("mode") for pl in snap["players"]}
    assert modes[7] == "chat"  # remote chat body (self 42 is excluded)
    # Self echo reports the controlling mode too.
    assert snap["self"]["mode"] == "web"


# ----- Discord refresh pacer sits out while web controls -----

def test_refresh_pacer_skips_web_controlled_player():
    """While web controls the body, _tick_player must not schedule any
    screen/hub work (the Discord screen never chases the web player)."""
    from discord_ui.refresh import RefreshScheduler, PlayerRefresh
    from types import SimpleNamespace

    gm = _gm()
    rt = gm.runtimes[1]
    gm.register_web_session(1, 42, "webby")

    scheduled = []

    class FakeHub:
        def schedule(self, key, payload):
            scheduled.append(("hub", key))

    class FakeCoalescer:
        def schedule(self, key, payload):
            scheduled.append(("screen", key))

        def pending(self, key):
            return 0

    gm.hub_coalescer = FakeHub()
    gm.coalescer = FakeCoalescer()

    rt.screens[42] = SimpleNamespace(
        user_id=42, rendering=False, message_id=123,
    )
    sched = RefreshScheduler(gm)
    pf = sched._players.setdefault((1, 42), PlayerRefresh())
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(sched._tick_player(rt, 42, pf, 10_000.0))
    finally:
        loop.close()
    assert scheduled == []  # sat out completely — no CPU spent on renders


def test_refresh_pacer_resumes_after_detach():
    from discord_ui.refresh import RefreshScheduler, PlayerRefresh
    from types import SimpleNamespace

    gm = _gm()
    rt = gm.runtimes[1]
    gm.db = None
    gm._schedule_save = lambda rt, player: None
    gm.register_web_session(1, 42, "webby")
    gm.drop_web_session(1, 42)

    scheduled = []

    class FakeHub:
        def schedule(self, key, payload):
            scheduled.append(("hub", key))

    class FakeCoalescer:
        def schedule(self, key, payload):
            scheduled.append(("screen", key))

        def pending(self, key):
            return 0

    gm.hub_coalescer = FakeHub()
    gm.coalescer = FakeCoalescer()
    rt.screens[42] = SimpleNamespace(
        user_id=42, rendering=False, message_id=123,
    )
    sched = RefreshScheduler(gm)
    pf = sched._players.setdefault((1, 42), PlayerRefresh())
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(sched._tick_player(rt, 42, pf, 10_000.0))
    finally:
        loop.close()
    # Control returned to Discord: the pacer schedules the hub beat again.
    assert ("hub", (1, 42)) in scheduled
