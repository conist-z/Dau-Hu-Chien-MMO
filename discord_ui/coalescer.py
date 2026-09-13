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
        # Do not keep extending an already-open debounce window. A sustained
        # stream of input must produce regular latest-state frames instead of
        # starving the renderer until the user finally stops pressing.
        deadline = loop.time() + self.delay
        old_deadline = self._deadlines.get(key)
        self._deadlines[key] = min(old_deadline, deadline) if old_deadline is not None else deadline
        if key not in self._tasks or self._tasks[key].done():
            self._tasks[key] = loop.create_task(self._drain(key))

    async def _drain(self, key: object) -> None:
        """Flush repeatedly until no presses arrived during the callback.

        A new press can arrive while ``on_flush`` is rendering/uploading. Keep
        the task alive for that next batch; the old implementation left that
        batch orphaned because it only created a task before the first flush.
        """
        loop = asyncio.get_event_loop()
        try:
            while True:
                deadline = self._deadlines.get(key)
                if deadline is None:
                    return
                remaining = deadline - loop.time()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                batch = self._batches.pop(key, [])
                self._deadlines.pop(key, None)
                if batch and self.on_flush is not None:
                    await self.on_flush(key, batch)
                # Only this key controls this drain task. Batches for other
                # channels/players must not keep this task alive, and a batch
                # scheduled during ``on_flush`` must never be orphaned.
                if key not in self._batches:
                    return
        finally:
            self._tasks.pop(key, None)
            self._deadlines.pop(key, None)

    def pending(self, key: object) -> int:
        return len(self._batches.get(key, []))


def resolve_channel(manager, channel_id: object, batch: list):
    """Best-effort channel for a flushed batch.

    Usually the channel comes from the triggering ``interaction``. But batches
    can be scheduled without an interaction (e.g. a hub re-render triggered at
    boot/restore), so fall back to the channel cached on the manager's client.
    """
    for item in batch:
        inter = item.get("interaction")
        if inter is not None:
            return inter.channel
    bot = getattr(manager, "bot_ref", None)
    if bot is not None:
        return bot.get_channel(channel_id)
    return None
