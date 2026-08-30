import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.collision import Collision
from game.map_loader import load_map
from game.state import Direction

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"


def _map():
    return load_map("test-map", ASSETS)


def test_load_map_meta():
    md = _map()
    assert md.width == 10 and md.height == 10
    assert md.spawn == (5, 5)
    assert md.display_name == "Đảo Cá Voi"


def test_walkable():
    md = _map()
    assert md.is_walkable(0, 0) is False
    assert md.is_walkable(5, 5) is True


def test_can_move():
    md = _map()
    c = Collision(md)
    assert c.can_move(5, 5, Direction.EAST) is True
    assert c.can_move(5, 5, Direction.WEST) is True
    assert c.can_move(1, 1, Direction.WEST) is False
    assert c.can_move(8, 8, Direction.SOUTH) is False


def test_out_of_bounds():
    md = _map()
    c = Collision(md)
    assert c.is_walkable(-1, 0) is False
    assert c.is_walkable(10, 0) is False
