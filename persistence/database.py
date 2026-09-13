import asyncio

import aiosqlite
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        # Serializes multi-statement transactions: execute_many spans awaits,
        # and an interleaved BEGIN from another task would crash with
        # "cannot start a transaction within a transaction". One lock per
        # Database instance (never a global — rule 15).
        self._txn_lock = asyncio.Lock()

    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # WAL journal: writers append to a log instead of fsyncing the main
        # db file per commit — inventory/craft spam goes from ~10-30 fsyncs
        # per op to near-zero disk stalls (WAL checkpoints are periodic).
        self.conn = await aiosqlite.connect(
            self.path, isolation_level=None  # autocommit; commits explicit
        )
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        return self.conn

    async def execute(self, sql: str, params=()):
        """One statement, one transaction (compatible legacy helper)."""
        assert self.conn is not None
        async with self._txn_lock:
            await self.conn.execute("BEGIN")
            try:
                await self.conn.execute(sql, params)
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            await self.conn.execute("COMMIT")

    async def execute_many(self, statements) -> None:
        """Batch N statements into ONE commit — the fix for per-row commits
        serializing a drag/craft burst (save_inventory_order used to commit
        after EVERY row, one fsync each, ~N fsyncs per inventory op)."""
        assert self.conn is not None
        async with self._txn_lock:
            await self.conn.execute("BEGIN")
            try:
                for sql, params in statements:
                    await self.conn.execute(sql, params)
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            await self.conn.execute("COMMIT")

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def fetchall(self, sql: str, params=()):
        assert self.conn is not None
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def fetchone(self, sql: str, params=()):
        assert self.conn is not None
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()
