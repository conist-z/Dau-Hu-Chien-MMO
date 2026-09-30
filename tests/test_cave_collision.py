"""Cave collision regression tests ("box chặn không khớp vật thể").

Root cause of the recurring mismatch: poly-derived sub-tile masks were
built BEFORE the invisible-blocker carve / portal arrival carve, so tiles
that ended up WALKABLE kept a mask — the sweep lets the box in (mask
passable) but masks.correct() still pushed it out of the leftover opaque
sub-cells: an invisible wall on open floor.
"""
from pathlib import Path

import pytest

from config import ASSETS_DIR

from game.map_loader import load_map, _cell_composite_alpha


@pytest.fixture(scope="module")
def cave_map():
    return load_map("ekonia/cave_area1", ASSETS_DIR)


def test_no_mask_on_walkable_tiles(cave_map):
    """Every mask must sit on a statically-blocked tile — a mask on a
    walkable tile acts as an invisible wall (the box is pushed out of the
    leftover opaque sub-cells even though nothing blocks there)."""
    masks = cave_map.tile_masks
    assert masks is not None
    stale = [
        (x, y)
        for y in range(cave_map.height)
        for x in range(cave_map.width)
        if masks.grid[y][x] is not None and not cave_map.collision[y][x]
    ]
    assert stale == [], f"stale masks on walkable tiles: {stale[:20]}"


def test_no_solid_cell_without_art_interior(cave_map):
    """After the invisible-blocker carve, no interior solid cell may lack
    non-ground art (the player stops where nothing is drawn).

    Exception: the gate rows — portal trigger tiles are force-solid by the
    loader (walking THROUGH the door art must be impossible) even where the
    door art reads as open. Row 5 cols 16-20 is the forest gate (the art
    there is the exit corridor mouth, drawn on row 6+)."""
    comp = _cell_composite_alpha(cave_map.tile_layers, cave_map.width, cave_map.height)
    if comp is None:  # baked sheet unavailable in this environment
        pytest.skip("ekonia_baked.png not available")
    from game.portals import Portals

    cfg = Portals.load(ASSETS_DIR / "portals.json")
    gates = set()
    mp = cfg.for_map("ekonia/cave_area1")
    if mp is not None:
        gates = set(mp.trigger_tiles)
    edge = 2
    bad = [
        (x, y)
        for y in range(edge, cave_map.height - edge)
        for x in range(edge, cave_map.width - edge)
        if cave_map.collision[y][x] and comp[y][x] == 0 and (x, y) not in gates
    ]
    assert bad == [], f"solid cells without art: {bad[:20]}"


def test_cave_masks_payload_matches_grid(cave_map):
    """The wire payload must agree with the pruned grid (client parity)."""
    masks = cave_map.tile_masks
    assert masks is not None
    payload = masks.to_payload()
    for y_str, row in payload["tiles"].items():
        y = int(y_str)
        for x_str in row:
            x = int(x_str)
            assert cave_map.collision[y][x] == 1, (
                f"payload masks walkable tile {(x, y)}"
            )
