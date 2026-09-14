from datetime import datetime, timezone

from game.state import Player


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def save_scenario(db, channel_id: int, message_id: int, map_id: str, hub_message_id: int = None) -> None:
    now = _now()
    await db.execute(
        """
        INSERT INTO scenarios (channel_id, message_id, map_id, hub_message_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            message_id=excluded.message_id,
            map_id=excluded.map_id,
            hub_message_id=excluded.hub_message_id,
            updated_at=excluded.updated_at
        """,
        (channel_id, message_id, map_id, hub_message_id, now, now),
    )


async def save_scenario_weather(db, channel_id: int, key: str, manual: bool) -> None:
    """Persist the scenario's weather pin (server-authoritative weather)."""
    await db.execute(
        "UPDATE scenarios SET weather_key = ?, weather_manual = ? WHERE channel_id = ?",
        (key, 1 if manual else 0, channel_id),
    )


async def load_scenarios(db) -> list:
    rows = await db.fetchall(
        "SELECT channel_id, message_id, map_id, hub_message_id, weather_key, weather_manual FROM scenarios"
    )
    return [
        {"channel_id": r[0], "message_id": r[1], "map_id": r[2], "hub_message_id": r[3],
         "weather_key": r[4], "weather_manual": r[5]}
        for r in rows
    ]


async def save_player(db, channel_id: int, player: Player) -> None:
    # Float position wins when the player has moved continuously (web); int
    # coords are its floor. Discord-only players keep their int coords and a
    # NULL float (never moved continuously) so nothing changes for them.
    x_f = player.x_f if player.float_moved else None
    y_f = player.y_f if player.float_moved else None
    await db.execute(
        """
        INSERT INTO players (channel_id, user_id, x, y, x_f, y_f, direction, sprite_id, visible,
                            hp, max_hp, mana, max_mana, level, xp, coins, class_id,
                            screen_message_id, controls_message_id, hub_message_id, step_size)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, user_id) DO UPDATE SET
            x=excluded.x, y=excluded.y, x_f=excluded.x_f, y_f=excluded.y_f,
            direction=excluded.direction,
            sprite_id=excluded.sprite_id, visible=excluded.visible,
            hp=excluded.hp, max_hp=excluded.max_hp, mana=excluded.mana,
            max_mana=excluded.max_mana, level=excluded.level, xp=excluded.xp,
            coins=excluded.coins, class_id=excluded.class_id,
            screen_message_id=excluded.screen_message_id,
            controls_message_id=excluded.controls_message_id,
            hub_message_id=excluded.hub_message_id,
            step_size=excluded.step_size
        """,
        (
            channel_id,
            player.user_id,
            player.x,
            player.y,
            x_f,
            y_f,
            player.direction,
            player.sprite_id,
            1 if player.visible else 0,
            player.hp,
            player.max_hp,
            player.mana,
            player.max_mana,
            player.level,
            player.xp,
            player.coins,
            player.class_id,
            player.screen_message_id,
            player.controls_message_id,
            player.hub_message_id,
            player.step_size,
        ),
    )


async def load_players(db, channel_id: int) -> list:
    rows = await db.fetchall(
        "SELECT user_id, x, y, x_f, y_f, direction, sprite_id, visible, "
        "hp, max_hp, mana, max_mana, level, xp, coins, class_id, "
        "screen_message_id, controls_message_id, hub_message_id, step_size "
        "FROM players WHERE channel_id=?",
        (channel_id,),
    )
    return [
        {
            "user_id": r[0],
            "x": r[1],
            "y": r[2],
            "x_f": r[3],
            "y_f": r[4],
            "direction": r[5],
            "sprite_id": r[6],
            "visible": bool(r[7]),
            "hp": r[8],
            "max_hp": r[9],
            "mana": r[10],
            "max_mana": r[11],
            "level": r[12],
            "xp": r[13],
            "coins": r[14],
            "class_id": r[15],
            "screen_message_id": r[16],
            "controls_message_id": r[17],
            "hub_message_id": r[18],
            "step_size": r[19] if len(r) > 19 else 1,
        }
        for r in rows
    ]


async def save_inventory_item(db, channel_id: int, user_id: int, item_id: str, qty: int) -> None:
    if qty <= 0:
        await db.execute(
            "DELETE FROM inventory WHERE channel_id=? AND user_id=? AND item_id=?",
            (channel_id, user_id, item_id),
        )
        return
    await db.execute(
        """
        INSERT INTO inventory (channel_id, user_id, item_id, qty)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel_id, user_id, item_id) DO UPDATE SET qty=excluded.qty
        """,
        (channel_id, user_id, item_id, qty),
    )


async def load_all_inventory(db, channel_id: int) -> dict:
    """{user_id: {item_id: qty}} with each bag dict in its persisted ORDER.

    Rows carry a ``pos`` index (written by save_inventory_order); rows without
    one (legacy data) keep their natural rowid order at the end. The ordered
    dict is what projects the first HOTBAR_SLOTS stacks onto the hotbar.
    """
    rows = await db.fetchall(
        "SELECT user_id, item_id, qty FROM inventory WHERE channel_id=? "
        "ORDER BY user_id, COALESCE(pos, 2147483647), rowid",
        (channel_id,),
    )
    out = {}
    for uid, iid, qty in rows:
        out.setdefault(uid, {})[iid] = qty
    return out


async def save_inventory_order(db, channel_id: int, user_id: int,
                               item_ids: list) -> None:
    """Persist the bag's ORDER for one player: ``item_ids`` is the dense
    position list (index in the list = hotbar/inventory grid position).
    Rewritten wholesale so order stays consistent after any reorder.

    PERF: this used to issue one UPDATE + COMMIT per row — a bag with 15
    stacks cost 16 sequential fsyncs on EVERY drag/craft, and spamming drags
    serialized the event loop behind disk I/O (the "ms tăng vọt" bug). All
    statements now ride ONE batched transaction (WAL → 1 cheap fsync).
    """
    stmts = [
        ("UPDATE inventory SET pos=NULL WHERE channel_id=? AND user_id=?",
         (channel_id, user_id)),
    ]
    for pos, iid in enumerate(item_ids):
        stmts.append((
            "UPDATE inventory SET pos=? WHERE channel_id=? AND user_id=? AND item_id=?",
            (pos, channel_id, user_id, iid),
        ))
    await db.execute_many(stmts)


async def delete_player(db, channel_id: int, user_id: int) -> None:
    await db.execute(
        "DELETE FROM players WHERE channel_id=? AND user_id=?",
        (channel_id, user_id),
    )


async def save_block(db, channel_id: int, x: int, y: int, block_id: str) -> None:
    """Persist one placed block (upsert). Ground tiles simply have no row."""
    await db.execute(
        """
        INSERT INTO blocks (channel_id, x, y, block_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel_id, x, y) DO UPDATE SET block_id=excluded.block_id
        """,
        (channel_id, x, y, block_id),
    )


async def delete_block(db, channel_id: int, x: int, y: int) -> None:
    await db.execute(
        "DELETE FROM blocks WHERE channel_id=? AND x=? AND y=?",
        (channel_id, x, y),
    )


async def load_blocks(db, channel_id: int) -> list:
    rows = await db.fetchall(
        "SELECT x, y, block_id FROM blocks WHERE channel_id=? ORDER BY rowid",
        (channel_id,),
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def save_chopped_resource(db, channel_id: int, x: int, y: int, chopped_at: float) -> None:
    """Persist one felled resource node (upsert by its anchor tile)."""
    await db.execute(
        """
        INSERT INTO resources (channel_id, x, y, chopped_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel_id, x, y) DO UPDATE SET chopped_at=excluded.chopped_at
        """,
        (channel_id, x, y, chopped_at),
    )


async def delete_chopped_resource(db, channel_id: int, x: int, y: int) -> None:
    """The node regrew (or the map changed): drop its row."""
    await db.execute(
        "DELETE FROM resources WHERE channel_id=? AND x=? AND y=?",
        (channel_id, x, y),
    )


async def load_chopped_resources(db, channel_id: int) -> list:
    rows = await db.fetchall(
        "SELECT x, y, chopped_at FROM resources WHERE channel_id=?",
        (channel_id,),
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def load_hotbars(db, channel_id: int) -> dict:
    """All players' hotbar bindings: {user_id: {slot: item_id}}."""
    rows = await db.fetchall(
        "SELECT user_id, slot, item_id FROM hotbar WHERE channel_id=?",
        (channel_id,),
    )
    out: dict = {}
    for uid, slot, iid in rows:
        out.setdefault(uid, {})[slot] = iid
    return out


async def save_hotbar_slot(db, channel_id: int, user_id: int, slot: int,
                           item_id: str) -> None:
    """Bind one hotbar slot; ``item_id=None`` unbinds it."""
    if item_id is None:
        await db.execute(
            "DELETE FROM hotbar WHERE channel_id=? AND user_id=? AND slot=?",
            (channel_id, user_id, slot),
        )
        return
    await db.execute(
        """
        INSERT INTO hotbar (channel_id, user_id, slot, item_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel_id, user_id, slot) DO UPDATE SET item_id=excluded.item_id
        """,
        (channel_id, user_id, slot, item_id),
    )


async def save_scooped_tile(db, channel_id: int, x: int, y: int) -> None:
    """Persist one scooped grass tile (upsert). A missing row means the tuft
    is still standing."""
    await db.execute(
        """
        INSERT OR IGNORE INTO terrain (channel_id, x, y)
        VALUES (?, ?, ?)
        """,
        (channel_id, x, y),
    )


async def delete_scooped_tile(db, channel_id: int, x: int, y: int) -> None:
    await db.execute(
        "DELETE FROM terrain WHERE channel_id=? AND x=? AND y=?",
        (channel_id, x, y),
    )


async def load_scooped_tiles(db, channel_id: int) -> list:
    rows = await db.fetchall(
        "SELECT x, y FROM terrain WHERE channel_id=?",
        (channel_id,),
    )
    return [(r[0], r[1]) for r in rows]


# ----- /khutraodoi travel return spot (main-map position before entering) -----


async def save_travel_return(
    db, channel_id: int, user_id: int, map_id: str, x: int, y: int, direction: str
) -> None:
    """Upsert the player's pre-lobby position (one row per player)."""
    await db.execute(
        """
        INSERT INTO travel_return (channel_id, user_id, map_id, x, y, direction)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, user_id) DO UPDATE SET
            map_id=excluded.map_id, x=excluded.x, y=excluded.y,
            direction=excluded.direction
        """,
        (channel_id, user_id, map_id, x, y, direction),
    )


async def load_travel_return(db, channel_id: int, user_id: int):
    """The saved (map_id, x, y, direction) tuple, or None when not travelling."""
    row = await db.fetchone(
        "SELECT map_id, x, y, direction FROM travel_return "
        "WHERE channel_id=? AND user_id=?",
        (channel_id, user_id),
    )
    return (row[0], row[1], row[2], row[3]) if row else None


async def delete_travel_return(db, channel_id: int, user_id: int) -> None:
    await db.execute(
        "DELETE FROM travel_return WHERE channel_id=? AND user_id=?",
        (channel_id, user_id),
    )


# ----- per-tile furnace state (game/smelting.py) -------------------------------


async def save_furnace(db, channel_id: int, furnace) -> None:
    """Upsert one furnace's slots + smelt deadline (deadline keeps ticking
    through a restart — never stored as remaining time)."""
    await db.execute(
        """
        INSERT INTO furnaces (channel_id, x, y, input_item, input_qty,
                              fuel_item, fuel_seconds, output_item, output_qty,
                              smelt_deadline)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id, x, y) DO UPDATE SET
            input_item=excluded.input_item,
            input_qty=excluded.input_qty,
            fuel_item=excluded.fuel_item,
            fuel_seconds=excluded.fuel_seconds,
            output_item=excluded.output_item,
            output_qty=excluded.output_qty,
            smelt_deadline=excluded.smelt_deadline
        """,
        (
            channel_id,
            furnace.x,
            furnace.y,
            furnace.input_item,
            furnace.input_qty,
            furnace.fuel_item,
            furnace.fuel_seconds,
            furnace.output_item,
            furnace.output_qty,
            furnace.smelt_deadline,
        ),
    )


async def delete_furnace(db, channel_id: int, x: int, y: int) -> None:
    await db.execute(
        "DELETE FROM furnaces WHERE channel_id=? AND x=? AND y=?",
        (channel_id, x, y),
    )


async def load_furnaces(db, channel_id: int) -> list:
    """Furnace rows as FurnaceState.from_dict-compatible dicts."""
    rows = await db.fetchall(
        "SELECT x, y, input_item, input_qty, fuel_item, fuel_seconds, "
        "output_item, output_qty, smelt_deadline FROM furnaces WHERE channel_id=?",
        (channel_id,),
    )
    return [
        {
            "x": r[0], "y": r[1],
            "input_item": r[2], "input_qty": r[3],
            "fuel_item": r[4], "fuel_seconds": r[5],
            "output_item": r[6], "output_qty": r[7],
            "smelt_deadline": r[8],
        }
        for r in rows
    ]


# ----- persistent web login tokens (OAuth session resume) -----

async def save_web_token(db, token: str, user_id: int, display_name: str,
                         avatar_hash: str = "") -> None:
    """Upsert the user's live login token (one per user — a new login
    replaces the previous row so old tokens stop working)."""
    import time as _time
    await db.execute(
        "INSERT INTO web_tokens (token, user_id, display_name, avatar_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, "
        "display_name=excluded.display_name, avatar_hash=excluded.avatar_hash, "
        "created_at=excluded.created_at",
        (token, user_id, display_name, avatar_hash, _time.time()),
    )


async def load_web_token(db, token: str):
    """Return (user_id, display_name, avatar_hash) for a live token, else None."""
    cur = await db.conn.execute(
        "SELECT user_id, display_name, avatar_hash FROM web_tokens WHERE token = ?",
        (token,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return int(row[0]), row[1], row[2]


async def delete_web_token(db, token: str) -> None:
    await db.execute("DELETE FROM web_tokens WHERE token = ?", (token,))


async def load_inventory_slots(db, channel_id: int) -> dict:
    """{user_id: [(item_id, qty) | None, ...]} — the raw pixel-grid slots
    WITH positions (pos index = slot). Preserves empty slots and drag
    layout across sessions, unlike load_all_inventory's dense dict."""
    rows = await db.fetchall(
        "SELECT user_id, item_id, qty, pos FROM inventory WHERE channel_id=? "
        "ORDER BY user_id, COALESCE(pos, 2147483647), rowid",
        (channel_id,),
    )
    out = {}
    for uid, iid, qty, pos in rows:
        slots = out.setdefault(uid, [])
        if pos is None or pos >= len(slots):
            slots.append((iid, qty))  # legacy row: dense-append
        else:
            while len(slots) <= pos:
                slots.append(None)
            # Two items sharing a pos (stale write): keep both, dense-append.
            if slots[pos] is not None:
                slots.append((iid, qty))
            else:
                slots[pos] = (iid, qty)
    return out
