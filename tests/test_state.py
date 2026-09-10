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


def test_player_default_stats():
    p = Player(10, "A")
    assert p.hp == 100 and p.max_hp == 100
    assert p.mana == 50 and p.max_mana == 50
    assert p.level == 1 and p.xp == 0
    assert p.class_id == "adventurer"
    assert p.learned_skills == ["slash"]


def test_revive_if_expired_skips_alive_player():
    p = Player(1, "A")
    assert p.alive is True
    assert p.revive_if_expired(1_000.0) is False
    assert p.hp == 100


def test_revive_if_expired_keeps_active_death_window():
    p = Player(1, "A")
    p.hp = 0
    p.visible = False
    p.dead_until = 1_003.0
    assert p.alive is False
    assert p.revive_if_expired(1_000.0) is False
    assert p.alive is False and p.hp == 0


def test_revive_if_expired_heals_expired_death_window():
    p = Player(1, "A")
    p.hp = 0
    p.visible = False
    p.dead_until = 1_000.0
    p.death_reason = "bị zombie tấn công"
    assert p.revive_if_expired(1_000.0) is True
    assert p.alive is True
    assert p.hp == p.max_hp and p.visible is True
    assert p.dead_until is None and p.death_reason is None


def test_revive_if_expired_heals_lost_respawn_task():
    # ``dead_until`` is ephemeral (never persisted): after a restart the row
    # loads with hp 0 and no deadline and no in-memory respawn task. Without a
    # lazy revive that player is locked out forever (frozen, all actions
    # "dead") — the reported "bị nhốt ở vòng tròn vô hình".
    p = Player(1, "A")
    p.hp = 0
    p.visible = False
    p.dead_until = None
    assert p.alive is False
    assert p.revive_if_expired(10.0) is True
    assert p.alive is True and p.visible is True

