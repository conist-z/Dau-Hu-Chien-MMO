import asyncio
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

path = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(path))

from discord_ui.commands import MapCog  # noqa: E402
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
        self.fetch_calls = []

    async def fetch_message(self, message_id):
        self.fetch_calls.append(message_id)
        if message_id in self.live_ids:
            return SimpleNamespace(id=message_id)
        raise discord.NotFound(_Resp(404), "Unknown Message")


class _Transient(Exception):
    pass


class FakeChannelTransient:
    def __init__(self):
        self.fetch_calls = []

    async def fetch_message(self, message_id):
        self.fetch_calls.append(message_id)
        raise discord.HTTPException(_Resp(500), "server error")


def _cog():
    return MapCog(MagicMock(), MagicMock(), MagicMock(), db=None)


def _rt(screen_msg_id, hub_msg_id=None):
    rt = SimpleNamespace(
        channel_id=100,
        message_id=None,
        hub_message_id=None,
        state=GameState(100, "t"),
        screens={},
    )
    rt.screens[42] = PlayerScreen(user_id=42)
    rt.screens[42].message_id = screen_msg_id
    rt.screens[42].hub_message_id = hub_msg_id
    rt.state.add_player(42, "P", 2, 2)
    return rt


def _interaction(channel, user_id=42):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name="P"),
        channel=channel,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
    )


def test_screen_alive_returns_true_when_live():
    cog = _cog()
    ch = FakeChannel(live_ids=[555])
    assert asyncio.run(cog._screen_alive(ch, 555)) is True


def test_screen_alive_returns_false_when_deleted():
    cog = _cog()
    ch = FakeChannel(live_ids=())
    assert asyncio.run(cog._screen_alive(ch, 555)) is False


def test_screen_alive_returns_none_on_transient_error():
    cog = _cog()
    assert asyncio.run(cog._screen_alive(FakeChannelTransient(), 555)) is None


def test_screen_alive_returns_none_when_channel_none():
    cog = _cog()
    assert asyncio.run(cog._screen_alive(None, 555)) is None


def test_allow_spawn_when_no_screen():
    cog = _cog()
    rt = SimpleNamespace(channel_id=100, state=GameState(100, "t"), screens={})
    inter = _interaction(FakeChannel(live_ids=()))
    rt.state.add_player(42, "P", 2, 2)
    assert asyncio.run(cog._allow_spawn_screen(inter, rt, 42)) is True
    inter.response.send_message.assert_not_called()


def test_allow_spawn_blocks_when_screen_alive():
    cog = _cog()
    rt = _rt(screen_msg_id=777, hub_msg_id=778)
    inter = _interaction(FakeChannel(live_ids=[777, 778]))
    assert asyncio.run(cog._allow_spawn_screen(inter, rt, 42)) is False
    inter.response.send_message.assert_called_once()
    assert rt.screens[42].message_id == 777  # untouched


def test_allow_spawn_recreates_when_screen_is_dead():
    cog = _cog()
    rt = _rt(screen_msg_id=777, hub_msg_id=778)
    inter = _interaction(FakeChannel(live_ids=()))  # both deleted
    assert asyncio.run(cog._allow_spawn_screen(inter, rt, 42)) is True
    inter.response.send_message.assert_not_called()
    assert rt.screens[42].message_id is None
    assert rt.screens[42].hub_message_id is None
    p = rt.state.get_player(42)
    assert p.screen_message_id is None
    assert p.hub_message_id is None


def test_allow_spawn_keeps_hub_when_hub_is_alive():
    cog = _cog()
    rt = _rt(screen_msg_id=777, hub_msg_id=778)  # screen dead, hub alive
    inter = _interaction(FakeChannel(live_ids=[778]))
    assert asyncio.run(cog._allow_spawn_screen(inter, rt, 42)) is True
    assert rt.screens[42].message_id is None      # screen cleared...
    assert rt.screens[42].hub_message_id == 778   # ...hub kept


def test_allow_spawn_blocks_on_transient_error():
    cog = _cog()
    rt = _rt(screen_msg_id=777)
    inter = _interaction(FakeChannelTransient())
    assert asyncio.run(cog._allow_spawn_screen(inter, rt, 42)) is False
    inter.response.send_message.assert_called_once()
    assert rt.screens[42].message_id == 777  # not nuked (avoid duplicate)
