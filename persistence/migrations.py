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

CREATE TABLE IF NOT EXISTS inventory (
    channel_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    item_id     TEXT NOT NULL,
    qty         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (channel_id, user_id, item_id)
);

CREATE TABLE IF NOT EXISTS blocks (
    channel_id  INTEGER NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    block_id    TEXT NOT NULL,
    PRIMARY KEY (channel_id, x, y)
);

CREATE TABLE IF NOT EXISTS hotbar (
    channel_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    slot        INTEGER NOT NULL,
    item_id     TEXT NOT NULL,
    PRIMARY KEY (channel_id, user_id, slot)
);

-- Harvestable nodes currently felled (game/resources.py). A missing row
-- means the node is standing; chopped_at drives the regrow deadline so a
-- restart never forgets a chopped tree nor makes it regrow too early.
CREATE TABLE IF NOT EXISTS resources (
    channel_id  INTEGER NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    chopped_at  REAL NOT NULL,
    PRIMARY KEY (channel_id, x, y)
);

-- Scooped grass tufts (game/terrain.py): a row means the tile's grass
-- overlay was shovelled off and the bare-dirt base shows through.
CREATE TABLE IF NOT EXISTS terrain (
    channel_id  INTEGER NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    PRIMARY KEY (channel_id, x, y)
);

-- Where a player was standing on the MAIN map before /khutraodoi teleported
-- them into the trade lobby. One row per (channel, user): /khutraodoi out
-- restores it (survives restarts — rule 14). A missing row = not travelling.
CREATE TABLE IF NOT EXISTS travel_return (
    channel_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    map_id      TEXT NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    direction   TEXT NOT NULL,
    PRIMARY KEY (channel_id, user_id)
);

-- Persistent web login tokens (Discord OAuth sessions): survive bot
-- restarts so a browser refresh NEVER needs the OAuth dance again —
-- the client replays its token and the server rebuilds the session.
-- One live token per user (new login replaces the old row).
CREATE TABLE IF NOT EXISTS web_tokens (
    token        TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    avatar_hash  TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);

-- Per-tile furnace state (game/smelting.py): input/fuel/output slots + the
-- smelt deadline (unix seconds) so a restart never loses smelting progress
-- nor gives free fuel. One row per furnace block tile.
CREATE TABLE IF NOT EXISTS furnaces (
    channel_id     INTEGER NOT NULL,
    x              INTEGER NOT NULL,
    y              INTEGER NOT NULL,
    input_item     TEXT,
    input_qty      INTEGER NOT NULL DEFAULT 0,
    fuel_item      TEXT,
    fuel_seconds   REAL NOT NULL DEFAULT 0,
    output_item    TEXT,
    output_qty     INTEGER NOT NULL DEFAULT 0,
    smelt_deadline REAL,
    PRIMARY KEY (channel_id, x, y)
);
"""


PLAYER_COLUMNS = [
    # Permanent role color ("1 a maus su dung mai moi" — user rule 15/09):
    # random on first join, then kept forever until manually changed.
    ("name_color", "TEXT"),
    ("hp", "INTEGER NOT NULL DEFAULT 100"),
    ("max_hp", "INTEGER NOT NULL DEFAULT 100"),
    ("mana", "INTEGER NOT NULL DEFAULT 50"),
    ("max_mana", "INTEGER NOT NULL DEFAULT 50"),
    ("level", "INTEGER NOT NULL DEFAULT 1"),
    ("xp", "INTEGER NOT NULL DEFAULT 0"),
    ("coins", "INTEGER NOT NULL DEFAULT 0"),
    ("class_id", "TEXT NOT NULL DEFAULT 'adventurer'"),
    ("screen_message_id", "INTEGER"),
    ("controls_message_id", "INTEGER"),
    ("hub_message_id", "INTEGER"),
    ("step_size", "INTEGER NOT NULL DEFAULT 1"),
    # Continuous (web-client) position: float tile units. NULL = never moved
    # continuously (Discord-only player) — load keeps int coords then.
    ("x_f", "REAL"),
    ("y_f", "REAL"),
]

# Bag ORDER persistence: the hotbar is a projection of the first HOTBAR_SLOTS
# stacks of the ordered bag, so the insertion order must survive a restart.
# ``pos`` is a dense 0..n-1 index, rewritten wholesale on every reorder.
INVENTORY_COLUMNS = [
    ("pos", "INTEGER"),
]

SCENARIO_COLUMNS = [
    ("hub_message_id", "INTEGER"),
    # Server-authoritative weather (survives restarts + rejoins): the pinned
    # key + whether it was manually set. NULL manual = follow live weather.
    ("weather_key", "TEXT"),
    ("weather_manual", "INTEGER"),
]


async def _add_columns(db, table: str, columns: list) -> None:
    cur = await db.conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in await cur.fetchall()}
    for name, definition in columns:
        if name in existing:
            continue
        try:
            await db.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        except Exception:
            # Column may have appeared from a concurrent migrate; ignore.
            pass
    # isolation_level=None (autocommit) — DDL commits implicitly, no
    # conn.commit() needed (it would raise "no transaction is active").


async def migrate(db) -> None:
    await db.conn.executescript(SCHEMA)
    await _add_columns(db, "players", PLAYER_COLUMNS)
    await _add_columns(db, "inventory", INVENTORY_COLUMNS)
    await _add_columns(db, "scenarios", SCENARIO_COLUMNS)
