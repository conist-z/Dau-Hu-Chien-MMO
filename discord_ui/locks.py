"""Per-player asyncio locks shared across UI modules.

Hub creation can be triggered from several paths at once (hub flush, pair
self-heal, screen recreate, button press). Without one shared lock per
(channel, user), two recovery lanes can both pass the "already has a hub"
guard and each send a hub message — the duplicate-hub bug. Keyed locks are
created lazily and evicted when no waiter remains so the map cannot grow.

The lock is REENTRANT per asyncio task. Recovery flows legitimately nest:
``repair_session`` holds the lock and calls ``create_hub_message`` (which
acquires it again), and ``flush_hub_batch`` re-creates a dead hub from inside
its own locked section. A plain asyncio.Lock deadlocked those callers forever
— the exact reason a player's hub silently never appeared. Reentrancy keeps
mutual exclusion between DIFFERENT tasks while letting one task re-acquire.
"""
from __future__ import annotations

import asyncio
from typing import Dict, Optional, Tuple


_locks: Dict[Tuple[int, int], "ReentrantAsyncLock"] = {}


class ReentrantAsyncLock:
    """asyncio.Lock that the owning task can re-acquire without deadlocking."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: Optional[asyncio.Task] = None
        self._depth = 0

    async def acquire(self) -> None:
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._depth = 1

    def release(self) -> None:
        if self._owner is not asyncio.current_task():
            raise RuntimeError("player_lock released by a non-owning task")
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()

    def locked(self) -> bool:
        # "Locked" must mean held by SOMEONE — but for the caller in
        # session_recovery.schedule_repair the meaningful question is
        # "held by another task". Same-task callers are inside the flow
        # already and never ask. Keep the plain asyncio semantics.
        return self._lock.locked()

    async def __aenter__(self) -> "ReentrantAsyncLock":
        await self.acquire()
        return self

    async def __aexit__(self, *_exc) -> None:
        self.release()


def player_lock(channel_id: int, user_id: int) -> ReentrantAsyncLock:
    key = (int(channel_id), int(user_id))
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = ReentrantAsyncLock()
    return lock


def release_player_lock(channel_id: int, user_id: int) -> None:
    """Drop the lock entry when free and idle (keeps the registry bounded)."""
    key = (int(channel_id), int(user_id))
    lock = _locks.get(key)
    if lock is not None and not lock.locked():
        _locks.pop(key, None)
