"""Cave portal-net + harvestable-props regression tests (user 30/09).

Three bugs, one module:

1. Cave rocks/mushrooms were pure decor — harvestable like the overworld
   requires them in TILE_NODE_PARTS via the cave "Props" layer.
2. portals.json lost the bigmap<->cave<->forest links (only the stash copy
   had them): arrivals were never carved walkable, so walking into the cave
   sometimes landed the player on a SOLID tile ("dịch chuyển sai vị trí").
3. The bigmap cave mouth spans 3 art tiles (80..82) but only (81,2),(81,3)
   triggered — the side columns looked open yet never teleported ("bỏ 2 ô
   trái phải").
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.map_loader import load_map
from game.portals import Portals
from game.resources import ResourceGrid
from game.collision import Collision

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def test_portals_json_has_the_cave_net():
    """The config must wire bigmap <-> cave <-> forest (restored from the
    stash copy — the working tree had regressed to the trade-lobby-only
    version, which silently disabled the arrival carve on both maps)."""
    cfg = Portals.load(ASSETS / "portals.json")
    big = cfg.for_map("bigmap")
    cave = cfg.for_map("ekonia/cave_area1")
    forest = cfg.for_map("ekonia/forest")
    assert big is not None and "ekonia/cave_area1" in big.destinations
    assert cave is not None and "bigmap" in cave.destinations
    assert cave is not None and "ekonia/forest" in cave.destinations
    assert forest is not None and "ekonia/cave_area1" in forest.destinations


def test_cave_arrival_tiles_are_walkable():
    """multi-tile targets ([[x,y],...]) parse AND the loader's arrival carve
    frees them — entering the cave must never land on a solid tile."""
    md = load_map("ekonia/cave_area1", ASSETS)
    for x, y in ((80, 54), (81, 54)):
        assert md.is_walkable(x, y), f"cave arrival ({x},{y}) solid"
    big = load_map("bigmap", ASSETS)
    for x, y in ((80, 4), (81, 4)):
        assert big.is_walkable(x, y), f"bigmap arrival ({x},{y}) solid"


def test_bigmap_gate_spans_the_whole_mouth():
    """The mouth art spans cols 80..82 on rows 2-3; every column must be a
    trigger tile (force-solid + teleports on touch)."""
    cfg = Portals.load(ASSETS / "portals.json")
    link = cfg.for_map("bigmap").destinations["ekonia/cave_area1"]
    tiles = set(link.portal_tiles)
    for x in (80, 81, 82):
        for y in (2, 3):
            assert (x, y) in tiles, f"gate tile ({x},{y}) missing"
    big = load_map("bigmap", ASSETS)
    for x, y in ((80, 2), (82, 3)):
        assert not big.is_walkable(x, y), f"gate ({x},{y}) walkable"


def test_gate_tiles_stay_solid_walk_through_impossible():
    """The cave's own exit row (55) and the forest gate rows must be solid:
    the teleport fires on box-touch, never by walking through."""
    md = load_map("ekonia/cave_area1", ASSETS)
    assert not md.is_walkable(81, 55)
    assert not md.is_walkable(17, 2)


def test_cave_props_are_harvestable_nodes():
    """Cave mushrooms = forage (walk-through, 1 hit), boulders = rocks
    (solid, pickaxe) — mirroring the overworld behaviour."""
    md = load_map("ekonia/cave_area1", ASSETS)
    res = ResourceGrid.from_map(md)
    kinds = {}
    for node in res.nodes.values():
        kinds[node.kind] = kinds.get(node.kind, 0) + 1
    assert kinds.get("mushroom_brown", 0) >= 20
    assert kinds.get("rock_big", 0) >= 5
    assert kinds.get("rock_small", 0) >= 5
    col = Collision(md, resources=res)
    mush = next(n for n in res.nodes.values() if n.kind == "mushroom_brown")
    rock = next(n for n in res.nodes.values() if n.kind.startswith("rock"))
    # forage: walk-through; rock: blocks until felled
    assert col.is_walkable(*mush.tiles[0])
    assert not col.is_walkable(*rock.tiles[0])


def test_overworld_forest_registry_unchanged():
    """Adding the cave gids must NOT resurrect nodes on other maps (the
    dead-tree/minable bug class): forest has no Props-layer nodes, bigmap
    keeps its own kinds only."""
    from game.resources import ResourceGrid as RG

    forest = RG.from_map(load_map("ekonia/forest", ASSETS))
    assert len(forest.nodes) == 0
    big = RG.from_map(load_map("bigmap", ASSETS))
    kinds = {n.kind for n in big.nodes.values()}
    assert kinds <= {"grass", "mushroom_brown", "mushroom_purple", "ore",
                     "tree", "bush", "flower", "rock_big", "rock_small"}
