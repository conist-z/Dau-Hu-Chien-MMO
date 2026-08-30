import asyncio

from discord_ui.coalescer import RenderCoalescer


def test_coalescer_batches_single_key_into_one_flush():
    flushed = []

    async def on_flush(key, batch):
        flushed.append((key, list(batch)))

    async def run():
        c = RenderCoalescer(delay=0.05, on_flush=on_flush)
        c.schedule("ch", {"n": 1})
        c.schedule("ch", {"n": 2})
        c.schedule("ch", {"n": 3})
        for _ in range(100):
            if flushed:
                break
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert len(flushed) == 1
    assert flushed[0][0] == "ch"
    assert [b["n"] for b in flushed[0][1]] == [1, 2, 3]


def test_coalescer_separates_keys():
    flushed = []

    async def on_flush(key, batch):
        flushed.append(key)

    async def run():
        c = RenderCoalescer(delay=0.03, on_flush=on_flush)
        c.schedule("a", {"n": 1})
        c.schedule("b", {"n": 2})
        for _ in range(100):
            if len(flushed) >= 2:
                break
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert sorted(flushed) == ["a", "b"]


def test_coalescer_pending_count():
    async def run():
        c = RenderCoalescer(delay=0.05)
        c.schedule("x", {"n": 1})
        c.schedule("x", {"n": 2})
        assert c.pending("x") == 2
        assert c.pending("y") == 0

    asyncio.run(run())
