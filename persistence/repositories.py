from datetime import datetime, timezone

from game.state import Player


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def save_scenario(db, channel_id: int, message_id: int, map_id: str) -> None:
    now = _now()
    await db.execute(
        """
        INSERT INTO scenarios (channel_id, message_id, map_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            message_id=excluded.message_id,
            map_id=excluded.map_id,
            updated_at=excluded.updated_at
        """,
        (channel_id, message_id, map_id, now, now),
    )


async def load_scenarios(db) -> list:
    rows = await db.fetchall(
        "SELECT channel_id, message_id, map_id FROM scenarios"
    )
    return [{"channel_id": r[0], "message_id": r[1], "map_id": r[2]} for r in rows]


async def save_player(db, channel_id: int, player: Player) -> None:
    await db.execute(
        """
        INSERT INTO players (channel_id, user_id, x, y, direction, sprite_id, visible)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, user_id) DO UPDATE SET
            x=excluded.x, y=excluded.y, direction=excluded.direction,
            sprite_id=excluded.sprite_id, visible=excluded.visible
        """,
        (
            channel_id,
            player.user_id,
            player.x,
            player.y,
            player.direction,
            player.sprite_id,
            1 if player.visible else 0,
        ),
    )


async def load_players(db, channel_id: int) -> list:
    rows = await db.fetchall(
        "SELECT user_id, x, y, direction, sprite_id, visible FROM players WHERE channel_id=?",
        (channel_id,),
    )
    return [
        {
            "user_id": r[0],
            "x": r[1],
            "y": r[2],
            "direction": r[3],
            "sprite_id": r[4],
            "visible": bool(r[5]),
        }
        for r in rows
    ]


async def delete_player(db, channel_id: int, user_id: int) -> None:
    await db.execute(
        "DELETE FROM players WHERE channel_id=? AND user_id=?",
        (channel_id, user_id),
    )
