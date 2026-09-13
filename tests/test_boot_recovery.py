import asyncio
import pathlib
import sys
from types import SimpleNamespace

import discord

path = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(path))

from bot import _message_exists  # noqa: E402


class _Resp:
    def __init__(self, status: int):
        self.status = status
        self.reason = "Test"


class FakeChannel:
    def __init__(self, exc=None):
        self.exc = exc
        self.calls = 0

    async def fetch_message(self, message_id):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(id=message_id)


def test_message_exists_true_when_found():
    ch = FakeChannel()
    assert asyncio.run(_message_exists(ch, 123)) is True
    assert ch.calls == 1


def test_message_exists_false_on_not_found():
    ch = FakeChannel(exc=discord.NotFound(_Resp(404), "Unknown Message"))
    assert asyncio.run(_message_exists(ch, 123)) is False
    assert ch.calls == 1


def test_message_exists_none_on_transient_error():
    ch = FakeChannel(exc=discord.HTTPException(_Resp(500), "server error"))
    assert asyncio.run(_message_exists(ch, 123)) is None


def test_message_exists_none_when_channel_none():
    assert asyncio.run(_message_exists(None, 123)) is None


def test_message_exists_none_when_message_id_none():
    ch = FakeChannel()
    assert asyncio.run(_message_exists(ch, None)) is None
