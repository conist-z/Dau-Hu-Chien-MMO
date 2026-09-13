import pathlib
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from discord_ui.adjacency import enforce_adjacency


def test_deletes_interlopers_between():
    fake_msg = AsyncMock()
    fake_msg.delete = AsyncMock()

    class _Hist:
        def __init__(self, msgs):
            self._msgs = msgs

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._msgs:
                return self._msgs.pop(0)
            raise StopAsyncIteration

    class _Channel:
        def history(self, *a, **k):
            return _Hist([fake_msg])

    import asyncio

    removed = asyncio.run(enforce_adjacency(_Channel(), 100, 200))
    assert removed == 1
    fake_msg.delete.assert_awaited_once()


def test_no_interlopers():
    class _Hist:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _Channel:
        def history(self, *a, **k):
            return _Hist()

    import asyncio

    removed = asyncio.run(enforce_adjacency(_Channel(), 100, 200))
    assert removed == 0


def test_preserved_ids_are_never_deleted():
    """The controls (D-pad) message sits between screen and hub by design; a
    sweep that preserves it must not delete it (the duplicate-hub churn bug)."""
    fake_msg = AsyncMock()
    fake_msg.id = 150  # between screen 100 and hub 200
    fake_msg.delete = AsyncMock()

    class _Hist:
        def __init__(self, msgs):
            self._msgs = msgs

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._msgs:
                return self._msgs.pop(0)
            raise StopAsyncIteration

    class _Channel:
        def history(self, *a, **k):
            return _Hist([fake_msg])

    import asyncio

    removed = asyncio.run(enforce_adjacency(_Channel(), 100, 200, preserve_ids=[150]))
    assert removed == 0
    fake_msg.delete.assert_not_awaited()
