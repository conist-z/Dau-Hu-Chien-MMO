"""Meteor-ore crater nodes (user 25/09/2026): every landed meteor mints a
2x2 meteor_ore resource node at the impact tile — interaction identical to
the rocks (pickaxe family, stone drops) but BIGGER (tree-like 2x2)."""

from game.meteors import spawn_crater_ore
from game.resources import METEOR_ORE_GID, ResourceGrid


def test_spawn_creates_2x2_node():
    g = ResourceGrid()
    assert spawn_crater_ore(g, 10, 10) is True
    node = g.node_at(10, 10)
    assert node is not None and node.kind == "meteor_ore"
    assert node.tiles == [(10, 10), (11, 10), (10, 11), (11, 11)]
    # Every tile carries the negative pseudo-gid (renderer key).
    assert all(g._tile_gids[t] == METEOR_ORE_GID for t in node.tiles)


def test_spawn_rejects_overlap():
    g = ResourceGrid()
    assert spawn_crater_ore(g, 10, 10) is True
    assert spawn_crater_ore(g, 10, 10) is False  # same anchor
    assert spawn_crater_ore(g, 9, 9) is False    # 2x2 would overlap
    assert spawn_crater_ore(g, 11, 10) is False  # covers an existing tile


def test_spawn_rejects_none_grid():
    assert spawn_crater_ore(None, 5, 5) is False


def test_visible_tiles_use_meteor_gid():
    g = ResourceGrid()
    spawn_crater_ore(g, 3, 3)
    tiles = g.visible_tiles()
    assert sorted(tiles) == [(3, 3, METEOR_ORE_GID), (3, 4, METEOR_ORE_GID),
                             (4, 3, METEOR_ORE_GID), (4, 4, METEOR_ORE_GID)]


def test_mining_matches_rock_rules():
    """Interaction parity with rock_big: pickaxe family + stone drops."""
    from game.resources import NODE_DEFS, is_ore_kind

    d = NODE_DEFS["meteor_ore"]
    assert is_ore_kind("meteor_ore")  # mined with the pickaxe
    assert all(item == "stone" for item, _c, _q in d.drops)  # đá rớt ra
    assert d.hits == NODE_DEFS["rock_big"].hits
    # BIGGER than a rock: 2x2 tiles vs 1.
    assert len(d.drops) > len(NODE_DEFS["rock_small"].drops)


def test_chop_fells_meteor_ore():
    from game.actions import ChopAction
    from game.resources import apply_chop
    from game.state import GameState

    g = ResourceGrid()
    spawn_crater_ore(g, 5, 5)
    state = GameState(1, "ekonia/overworld")
    state.add_player(1, "u1", x=6, y=6)
    p = state.get_player(1)
    p.direction = "n"  # facing tile (6,5) is part of the node

    result = None
    # Bare hands: 65*1.4 = 91 swings — drive progress straight to the fell.
    for _ in range(200):
        result = apply_chop(state, ChopAction(user_id=1, dx=0, dy=-1),
                            g, inventory=None)
        if result.success and result.drops:
            break
    assert result is not None
    assert result.drops and all(i == "stone" for i, _q in result.drops)
    assert g.is_chopped((5, 5))
