import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.state import ActionResult, Direction, GameState, Player


def test_add_player_idempotent():
    s = GameState(1, "m")
    p = s.add_player(10, "A")
    assert p.x == 0 and p.y == 0
    p2 = s.add_player(10, "A")
    assert p2 is p
    assert len(s.players) == 1


def test_8_directions():
    s = GameState(1, "m")
    for d in Direction:
        dx, dy = d.vector
        s.players[10] = Player(10, "A", 5, 5)
        p = s.players[10]
        p.x += dx
        p.y += dy
        assert (p.x, p.y) == (5 + dx, 5 + dy)


def test_invalid_player():
    s = GameState(1, "m")
    assert s.get_player(999) is None
    assert s.remove_player(999) is None


def test_visible_players():
    s = GameState(1, "m")
    s.add_player(1, "A")
    s.add_player(2, "B")
    s.players[2].visible = False
    assert [p.user_id for p in s.get_visible_players()] == [1]
