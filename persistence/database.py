import aiosqlite
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        return self.conn

    async def close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def execute(self, sql: str, params=()):
        assert self.conn is not None
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def fetchall(self, sql: str, params=()):
        assert self.conn is not None
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def fetchone(self, sql: str, params=()):
        assert self.conn is not None
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()
