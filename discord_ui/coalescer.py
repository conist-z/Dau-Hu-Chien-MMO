from typing import Awaitable, Callable, Dict, List, Optional

import asyncio


class RenderCoalescer:
    """Coalesce rapid render requests into a single frame upload.

    Discord button presses ("emoji") arrive faster than an image can be
    generated and uploaded. Instead of rendering once per press, each press
    enqueues a payload and (re)starts a short debounce window. When the window
    lapses with no further presses, ``on_flush`` is invoked exactly once with
    the whole batch of pending payloads -> one render, one upload, many button
    acknowledgements. This is the "catch the emoji first, then load once"
    behaviour: input is coalesced into a single frame.

    Pure: it does not touch discord.py. The flush callback (provided by the
    Discord layer) performs the actual render + edit and must accept
    ``(key, items)`` and be awaitable.
    """

    def __init__(
        self,
        delay: float = 0.12,
        on_flush: Optional[Callable[[object, List[dict]], Awaitable[None]]] = None,
    ):
        self.delay = delay
        self.on_flush = on_flush
        self._tasks: Dict[object, asyncio.Task] = {}
        self._deadlines: Dict[object, float] = {}
        self._batches: Dict[object, List[dict]] = {}

    def schedule(self, key: object, item: dict) -> None:
        """Enqueue one render payload (e.g. a button press) for ``key``.

        Extends the debounce window so a burst of presses collapses into a
        single flush. Safe to call from the event loop only.
        """
        loop = asyncio.get_event_loop()
        self._batches.setdefault(key, []).append(item)
        self._deadlines[key] = loop.time() + self.delay
        if key not in self._tasks or self._tasks[key].done():
            self._tasks[key] = loop.create_task(self._drain(key))

    async def _drain(self, key: object) -> None:
        loop = asyncio.get_event_loop()
        while True:
            remaining = self._deadlines[key] - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)
        batch = self._batches.pop(key, [])
        self._deadlines.pop(key, None)
        self._tasks.pop(key, None)
        if batch and self.on_flush is not None:
            await self.on_flush(key, batch)

    def pending(self, key: object) -> int:
        return len(self._batches.get(key, []))
