import asyncio
import pathlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from discord_ui.map_view import MapView
from game.inventory import Inventory
from game.manager import GameManager
from persistence.database import Database
from persistence.migrations import migrate
from persistence.repositories import load_all_inventory, load_hotbars
from rendering.hub_renderer import HubRenderer

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _run_db():
    return tempfile.TemporaryDirectory()


def test_hotbar_persistence_roundtrip():
    with _run_db() as d:
        db = Database(str(Path(d) / "game.db"))

        async def main():
            await db.connect()
            try:
                await migrate(db)
                # Bag ORDER persists: the hotbar projection survives a restart.
                from persistence.repositories import save_inventory_item, save_inventory_order

                await save_inventory_item(db, 1, 10, "potion_hp", 2)
                await save_inventory_item(db, 1, 10, "potion_mp", 1)
                await save_inventory_order(db, 1, 10, ["potion_mp", "potion_hp"])
                invs = await load_all_inventory(db, 1)
                assert list(invs[10]) == ["potion_mp", "potion_hp"]
            finally:
                await db.close()

        asyncio.run(main())


def test_legacy_hotbar_rows_migrate_into_bag_order():
    """Old bindings (slot -> item_id) are re-applied as bag positions on
    load, then the legacy table is emptied."""
    from game.items import get_item

    assert get_item("potion_hp") is not None  # items used below must exist
    with _run_db() as d:
        db = Database(str(Path(d) / "game.db"))

        async def main():
            await db.connect()
            try:
                await migrate(db)
                mgr = GameManager(ASSETS)
                mgr.db = db
                from persistence.repositories import save_inventory_item, save_hotbar_slot

                await save_inventory_item(db, 1, 10, "potion_hp", 1)
                await save_inventory_item(db, 1, 10, "potion_mp", 1)
                await save_inventory_item(db, 1, 10, "key_stone", 1)
                # Legacy: potion_mp was bound to slot 2 (out of the first six).
                await save_hotbar_slot(db, 1, 10, 2, "potion_mp")
                rt = mgr.create_runtime(1, "test-map")
                await mgr.load_inventories(rt)
                await mgr.load_hotbars(rt)
                inv = rt.inventories[10]
                assert list(inv.items)[2] == "potion_mp"
                assert mgr.get_hotbar(1, 10)[2] == "potion_mp"
                # The legacy table is drained so the migration runs only once.
                assert await load_hotbars(db, 1) == {}
            finally:
                await db.close()

        asyncio.run(main())


def test_manager_hotbar_runtime_and_db():
    with tempfile.TemporaryDirectory() as d:
        mgr = GameManager(ASSETS)
        db = Database(str(Path(d) / "game.db"))

        async def main():
            await db.connect()
            try:
                await migrate(db)
                mgr.db = db
                rt = mgr.create_runtime(1, "test-map")
                # Default: 6 empty slots (empty bag).
                hb = mgr.get_hotbar(1, 10)
                assert len(hb) == 6 and all(v is None for v in hb.values())
                # Bag slot order projects onto the hotbar POSITIONALLY.
                await mgr.add_item(1, 10, "potion_hp")
                await mgr.add_item(1, 10, "potion_mp")
                hb = mgr.get_hotbar(1, 10)
                assert hb[0] == "potion_hp" and hb[1] == "potion_mp" and hb[2] is None
                # Assigning an item to a slot SWAPS it into that bag position.
                await mgr.set_hotbar_slot(1, 10, 0, "potion_mp")
                inv = mgr.get_inventory(1, 10)
                assert list(inv.items) == ["potion_mp", "potion_hp"]
                assert mgr.get_hotbar(1, 10)[0] == "potion_mp"
                # Bag order persists (restart recovery).
                rt3 = mgr.create_runtime(1, "test-map")
                await mgr.load_inventories(rt3)
                assert list(rt3.inventories[10].items) == ["potion_mp", "potion_hp"]
                # Moving a stack OUT of the hotbar window clears its cell —
                # the positional mapping shows the EMPTY bag slot as-is.
                rt3.inventories[10].remove("potion_mp", 1)
                assert mgr.get_hotbar(1, 10)[0] is None
                assert mgr.get_hotbar(1, 10)[1] == "potion_hp"
            finally:
                await db.close()

        asyncio.run(main())


def test_map_view_hotbar_buttons_mirror_bindings():
    inv = Inventory()
    inv.add("potion_hp", 3)  # bag order: potion_hp is stack 0 = hotbar slot 1
    class RT:
        hotbars = {}
        inventories = {42: inv}
        screens = {}
    class M:
        def get_runtime(self, cid):
            return RT()

        def get_runtime_for(self, cid, user_id=None):
            return self.get_runtime(cid)

    view = MapView(100, M(), 42)
    # Exactly 6 hotbar buttons on the D-pad.
    assert len(view.hotbar_btns) == 6
    # First bag stack shows the item emoji + count and is pressable.
    btn0 = view.hotbar_btns[0]
    assert btn0.emoji is not None and btn0.emoji.name == "🧪"
    assert btn0.label == "x3" and btn0.disabled is False
    # Empty slots (bag shorter than 6) are disabled with their slot number.
    btn5 = view.hotbar_btns[5]
    assert btn5.disabled is True and btn5.label == "6" and btn5.emoji is None
    # Per-player persistent custom_ids.
    assert view.hotbar_btns[5].custom_id == "mg:100:42:h5"
    # No runtime (legacy shared view): no crash, plain numbers.
    view2 = MapView(101, type("M2", (), {"get_runtime": lambda s, c: None, "get_runtime_for": lambda s, c, u=None: None})(), 42)
    assert view2.hotbar_btns[0].label == "1"


def test_hub_hotbar_draws_bound_items():
    from game.state import GameState

    renderer = HubRenderer(ASSETS)
    rt = GameManager(ASSETS).create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 5, 5)
    plain = renderer.render(rt, rt.map_data, rt.npc_map, 10)
    filled = renderer.render(rt, rt.map_data, rt.npc_map, 10,
                             hotbar={0: ("potion_hp", 3)})
    # Slot 0 (leftmost, vertically centred) now carries the item glyph.
    # Internal layout: 6x84px slots, 12px gaps, centred +60 shift in 840x132.
    total = 6 * 84 + 5 * 12
    x0 = (840 - total) // 2 + 60
    y0 = (132 - 84) // 2
    probe = (x0 + 42, y0 + 42)  # centre of slot 0
    assert plain.image.getpixel(probe) != filled.image.getpixel(probe)
    # A different slot (far right) stays empty in both.
    probe_empty = (x0 + 5 * (84 + 12) + 42, y0 + 42)
    assert (plain.image.getpixel(probe_empty) == filled.image.getpixel(probe_empty))


def test_hub_hotbar_only_six_slots_render():
    """The hub bar shows hotbar slots 0-5; bindings in slots 6-8 (D-pad only)
    must NOT be drawn on the hub."""
    from game.state import GameState

    renderer = HubRenderer(ASSETS)
    rt = GameManager(ASSETS).create_runtime(1, "test-map")
    rt.state.add_player(10, "A", 5, 5)
    plain = renderer.render(rt, rt.map_data, rt.npc_map, 10)
    filled = renderer.render(rt, rt.map_data, rt.npc_map, 10,
                             hotbar={7: ("potion_hp", 3)})
    total = 6 * 84 + 5 * 12
    x0 = (840 - total) // 2 + 60
    y0 = (132 - 84) // 2
    # Slot 6 and beyond do not exist on the hub bar — nothing changes.
    assert plain.image.tobytes() == filled.image.tobytes()
    assert x0 > 0 and y0 >= 0  # sanity for the layout math above


def test_block_items_are_not_question_marks():
    """Regression: stone/wood in the bag used to render as ❓ because they
    only existed in the BLOCK registry."""
    from game.items import get_item

    stone = get_item("stone")
    assert stone is not None and stone.emoji == "🪨" and stone.name == "Đá"
    wood = get_item("wood")
    assert wood is not None and wood.emoji == "🪵"
    assert get_item("potion_hp") is not None  # registry items unchanged
    assert get_item("nope") is None


def test_hotbar_strip_format():
    from discord_ui.inventory_view import _format_hotbar_strip

    strip = _format_hotbar_strip({0: ("potion_hp", 2), 3: ("stone", 5)})
    assert "🧪x2" in strip and "🪨x5" in strip
    assert strip.count("⬛") == 4  # the 4 empty slots (6 total)
    assert "🔥" in strip


def test_build_pickup_frames_drop_and_vanish():
    from PIL import Image

    from rendering.renderer import build_pickup_frames

    base = Image.new("RGB", (96, 64), (10, 20, 30))
    item = Image.new("RGBA", (32, 32), (200, 100, 50, 255))
    frames = build_pickup_frames(base, (16, 32), (80, 32), item, n_frames=12)
    assert len(frames) == 12
    # First frame: item at the broken block's tile (centre 16,32).
    assert frames[0].getpixel((16, 32)) != (10, 20, 30)
    # Mid-flight: somewhere between start and player, on an arc.
    assert frames[5].tobytes() != base.tobytes()
    # Final frame: picked up — identical to the bare post-break ground.
    assert frames[-1].tobytes() == base.tobytes()



def test_close_side_panels_clears_and_deletes():
    """A movement press must close every side panel: references cleared and
    panel messages deleted (only screen+hub remain)."""
    import asyncio
    import tempfile
    from pathlib import Path

    from discord_ui.map_view import MapView  # noqa: F401 (import sanity)
    from game.manager import GameManager, PlayerScreen

    class FakeMsg:
        def __init__(self, mid):
            self.id = mid

        async def delete(self):
            self.deleted = True

    class FakeChannel:
        def __init__(self):
            self.partials = {}

        def get_partial_message(self, mid):
            self.partials.setdefault(mid, FakeMsg(mid))
            return self.partials[mid]

    class FakeBot:
        def __init__(self, ch):
            self._ch = ch

        def get_channel(self, cid):
            return self._ch

    with tempfile.TemporaryDirectory() as d:
        mgr = GameManager(ASSETS)
        rt = mgr.create_runtime(1, "test-map")
        screen = PlayerScreen(
            user_id=10,
            inventory_message_id=111,
            craft_message_id=222,
            inventory_view=object(),
            craft_view=object(),
        )
        rt.screens[10] = screen
        ch = FakeChannel()
        mgr.bot_ref = FakeBot(ch)

        asyncio.run(mgr.close_side_panels(1, 10))

        # References dropped + both panel messages deleted.
        assert screen.inventory_message_id is None and screen.craft_message_id is None
        assert screen.inventory_view is None and screen.craft_view is None
        assert ch.partials[111].deleted and ch.partials[222].deleted


def test_step_size_persists_on_player():
    """The 1/3/5 preference lives on the persisted Player row."""
    import asyncio
    import tempfile
    from pathlib import Path

    from persistence.database import Database
    from persistence.migrations import migrate
    from persistence.repositories import load_players, save_player

    with tempfile.TemporaryDirectory() as d:
        db = Database(str(Path(d) / "game.db"))

        async def main():
            await db.connect()
            try:
                await migrate(db)
                rt = GameManager(ASSETS).create_runtime(1, "test-map")
                p = rt.state.add_player(10, "A", 5, 5)
                p.step_size = 5
                await save_player(db, 1, p)
                rows = await load_players(db, 1)
                assert rows[0]["step_size"] == 5
            finally:
                await db.close()

        asyncio.run(main())
