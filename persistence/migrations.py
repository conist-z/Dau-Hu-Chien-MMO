SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    channel_id  INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL,
    map_id      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    channel_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    direction   TEXT NOT NULL,
    sprite_id   TEXT NOT NULL,
    visible     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (channel_id, user_id)
);
"""


async def migrate(db) -> None:
    await db.conn.executescript(SCHEMA)
