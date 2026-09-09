import asyncio
import pathlib
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.actions import ChopAction
from game.inventory import Inventory
from game.manager import GameManager
from game.map_loader import load_map
from game.resources import NODE_DEFS, ResourceGrid, apply_chop, render_kwargs
from game.state import GameState

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _bigmap_grid() -> ResourceGrid:
    md = load_map("bigmap", ASSETS)
    return ResourceGrid.from_map(md)


# Anchor coords observed on the real bigmap (see scripts/_inspect_bigmap.py);
# updated for the rebuilt 180x120 Tiled export.
TREE_ANCHOR = (3, 63)  # tree top-left; tiles (3,63)(4,63)(3,64)(4,64)
BUSH_ANCHOR = (103, 44)  # single bush tile
ORE_ANCHOR = (98, 41)  # single ore tile (gid 46, misc-item layer)


def _make_state(grid, px, py, facing="NORTH") -> GameState:
    s = GameState(1, "bigmap")
    p = s.add_player(10, "A", px, py)
    p.direction = facing
    return s


def test_grid_indexes_bigmap_trees_and_bushes():
    g = _bigmap_grid()
    assert len(g.nodes) == 82
    kinds = {}
    for n in g.nodes.values():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    assert kinds == {"tree": 76, "ore": 1, "bush": 5}
    assert g.layer_names == {"cây", "vật phẩm ko liên quan"}
    assert len(g.visible_tiles()) == 310


def test_ore_nodes_are_indexed_and_mined_with_pickaxe():
    """Ore veins from the misc-item layer become mineable ore nodes."""
    from game.resources import is_ore_kind

    g = _bigmap_grid()
    ores = [n for n in g.nodes.values() if n.kind == "ore"]
    assert len(ores) == 1
    assert all(is_ore_kind(n.kind) for n in ores)
    # An ore node is minable: full-hits swinging yields stone drops.
    inv = Inventory()
    inv.add("stone_pickaxe", 1)  # stone blocks need at least a dirt-tier pickaxe
    node = ores[0]
    tx, ty = node.tiles[0]
    s = _make_state(g, tx, ty + 1, "NORTH")
    for _ in range(16):
        r = apply_chop(s, ChopAction(10), g, inv, rng=random.Random(1), now=100.0)
        if g.is_chopped(node.anchor):
            break
        assert r.success
    assert g.is_chopped(node.anchor)
    assert inv.count("stone") >= 1


def test_tree_is_2x2_node_and_anchor_mapping():
    g = _bigmap_grid()
    node = g.node_at(*TREE_ANCHOR)
    assert node is not None and node.kind == "tree"
    assert set(node.tiles) == {
        (3, 63), (4, 63), (3, 64), (4, 64),
    }
    # Any of the 4 sprite tiles resolves back to the SAME node.
    for tile in node.tiles:
        assert g.node_at(*tile).anchor == TREE_ANCHOR
    assert len(g.node_tiles_gids(node)) == 4


def test_apply_chop_progress_then_felling():
    g = _bigmap_grid()
    inv = Inventory()
    s = _make_state(g, TREE_ANCHOR[0], TREE_ANCHOR[1] + 1, "NORTH")  # face the tree
    rng = random.Random(1)

    # hits-1 swings: progress only, no drops, tree still visible.
    for _ in range(NODE_DEFS["tree"].hits - 1):
        r = apply_chop(s, ChopAction(10), g, inv, rng=rng, now=100.0)
        assert r.success and r.state_changed and r.drops is None
    assert g.is_chopped(TREE_ANCHOR) is False

    # Final swing: felled + at least the guaranteed 1 wood in the bag.
    r = apply_chop(s, ChopAction(10), g, inv, rng=rng, now=100.0)
    assert r.success and r.drops is not None
    assert g.is_chopped(TREE_ANCHOR)
    assert inv.count("wood") >= 1
    assert inv.count("wood") == sum(q for iid, q in r.drops if iid == "wood")


def test_chopped_node_disappears_and_regrows():
    g = _bigmap_grid()
    inv = Inventory()
    s = _make_state(g, TREE_ANCHOR[0], TREE_ANCHOR[1] + 1, "NORTH")
    for _ in range(NODE_DEFS["tree"].hits):
        apply_chop(s, ChopAction(10), g, inv, rng=random.Random(2), now=100.0)
    assert g.is_chopped(TREE_ANCHOR)

    # All 4 tree tiles vanish from the visible set (other trees remain).
    visible = set((x, y) for x, y, _g in g.visible_tiles())
    assert not (set(g.nodes[TREE_ANCHOR].tiles) & visible)
    assert len(g.visible_tiles()) == 306  # 310 - 4

    # Chopping again while regrowing is rejected.
    r = apply_chop(s, ChopAction(10), g, inv, rng=random.Random(3), now=101.0)
    assert not r.success and r.reason == "regrowing"

    # After the respawn deadline the node is regrow-ready.
    respawn = NODE_DEFS["tree"].respawn_s
    ready = g.regrow_ready(100.0 + respawn + 1)
    assert TREE_ANCHOR in ready
    g.regrow(TREE_ANCHOR)
    assert not g.is_chopped(TREE_ANCHOR)
    assert len(g.visible_tiles()) == 310


def test_bush_requires_two_hits_and_less_drops():
    g = _bigmap_grid()
    assert NODE_DEFS["bush"].hits == 2
    inv = Inventory()
    s = _make_state(g, BUSH_ANCHOR[0], BUSH_ANCHOR[1] + 1, "NORTH")
    r = apply_chop(s, ChopAction(10), g, inv, rng=random.Random(4), now=200.0)
    assert r.drops is None
    r = apply_chop(s, ChopAction(10), g, inv, rng=random.Random(4), now=200.0)
    assert r.drops is not None and g.is_chopped(BUSH_ANCHOR)


def test_apply_chop_no_node():
    g = _bigmap_grid()
    inv = Inventory()
    s = _make_state(g, 5, 5, "EAST")  # (6,5) has no resource tile
    r = apply_chop(s, ChopAction(10), g, inv)
    assert not r.success and r.reason == "no_node"


def test_render_kwargs_hides_chopped_trees():
    g = _bigmap_grid()
    rt = type("RT", (), {})()
    rt.resources = g
    kw = render_kwargs(rt)
    assert kw["resource_layer_names"] == {"cây", "vật phẩm ko liên quan"}
    assert len(kw["resource_tiles"]) == 310

    inv = Inventory()
    s = _make_state(g, TREE_ANCHOR[0], TREE_ANCHOR[1] + 1, "NORTH")
    for _ in range(NODE_DEFS["tree"].hits):
        apply_chop(s, ChopAction(10), g, inv, rng=random.Random(5), now=100.0)
    assert len(render_kwargs(rt)["resource_tiles"]) == 306


def test_persistence_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        from persistence.database import Database
        from persistence.migrations import migrate
        from persistence.repositories import (
            delete_chopped_resource,
            load_chopped_resources,
            save_chopped_resource,
        )

        db = Database(str(Path(d) / "g.db"))

        async def main():
            await db.connect()
            await migrate(db)
            await save_chopped_resource(db, 1, 20, 0, 100.0)
            await save_chopped_resource(db, 1, 38, 6, 150.0)
            rows = await load_chopped_resources(db, 1)
            assert (20, 0, 100.0) in rows
            assert (38, 6, 150.0) in rows
            assert len(rows) == 2
            await delete_chopped_resource(db, 1, 20, 0)
            assert len(await load_chopped_resources(db, 1)) == 1
            await db.close()

        asyncio.run(main())


def test_manager_dispatch_chop_persists_inventory():
    mgr = GameManager(ASSETS)
    rt = mgr.create_runtime(1, "bigmap")
    p = rt.state.add_player(10, "A", TREE_ANCHOR[0], TREE_ANCHOR[1] + 1)
    p.direction = "NORTH"

    async def main():
        last = None
        for _ in range(NODE_DEFS["tree"].hits):
            _rt, last = await mgr.dispatch(1, ChopAction(10))
        assert last.drops is not None
        inv = mgr.get_inventory(1, 10)
        assert inv.count("wood") >= 1
        assert rt.resources.is_chopped(TREE_ANCHOR)
        # Re-swing while regrowing is blocked.
        _rt, again = await mgr.dispatch(1, ChopAction(10))
        assert not again.success and again.reason == "regrowing"

    asyncio.run(main())


def test_no_collision_changes_from_resources():
    """Resource collision is driven ONLY by the node's alive/felled state.

    A standing tree is solid (user rule: no walking through trees). A FELLED
    tree frees its tiles completely — the player walks over the grass where
    the tree stood, even though the static "cây" layer still lists the tile
    as blocked. No other tile ever changes walkability because of the grid.
    """
    from game.collision import Collision

    md = load_map("bigmap", ASSETS)
    g = _bigmap_grid()
    node = g.node_at(*TREE_ANCHOR)
    assert node is not None
    col = Collision(md, None, resources=g)
    # Standing tree: solid (both the static layer and the grid agree).
    assert not col.is_walkable(*TREE_ANCHOR)
    # Felled: the tiles free up (the static blocker is overridden by design).
    g.chop(TREE_ANCHOR, 0.0)
    assert col.is_walkable(*TREE_ANCHOR)
    # A tile without a node keeps the static map's word exactly.
    open_tile = md.spawn
    col_plain = Collision(md, None)
    assert Collision(md, None, resources=g).is_walkable(*open_tile) == (
        col_plain.is_walkable(*open_tile)
    )