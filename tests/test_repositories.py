import asyncio
import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.state import GameState, Player
from persistence.database import Database
from persistence.migrations import migrate
from persistence.repositories import (
    load_all_inventory,
    load_players,
    load_scenarios,
    save_inventory_item,
    save_player,
    save_scenario,
)


def _run():
    with tempfile.TemporaryDirectory() as d:
        db = Database(str(Path(d) / "game.db"))

        async def main():
            await db.connect()
            await migrate(db)

            # player with new stat columns
            s = GameState(1, "m")
            p = s.add_player(10, "A", 2, 3)
            p.hp, p.mana, p.level = 77, 22, 4
            await save_player(db, 1, p)

            rows = await load_players(db, 1)
            assert len(rows) == 1
            r = rows[0]
            assert r["hp"] == 77 and r["mana"] == 22 and r["level"] == 4
            assert r["class_id"] == "adventurer"

            # scenario with hub message id
            await save_scenario(db, 1, 555, "bigmap", 999)
            sc = await load_scenarios(db)
            assert sc[0]["hub_message_id"] == 999

            # inventory round-trip
            await save_inventory_item(db, 1, 10, "potion_hp", 3)
            inv = await load_all_inventory(db, 1)
            assert inv[10]["potion_hp"] == 3
            await save_inventory_item(db, 1, 10, "potion_hp", 0)  # delete
            inv = await load_all_inventory(db, 1)
            assert inv.get(10, {}).get("potion_hp", 0) == 0

            await db.close()

        asyncio.run(main())


def test_repositories_roundtrip():
    _run()
