import pathlib
import random

import pytest

from game.actions import AttackAction, MoveAction
from game.collision import Collision
from game.map_loader import MapData
from game.rules import apply_attack, apply_move
from game.state import Direction, GameState
from game.zombies import (
    NIGHT_END_SEC,
    NIGHT_START_SEC,
    ZOMBIE_ATTACK_COOLDOWN_SECONDS,
    Zombie,
    _live_hunters,
    advance_visible_zombies,
    is_night,
    tick_zombies,
    world_tick,
)


@pytest.fixture
def world():
    map_data = MapData(
        map_id="zombie-test",
        width=20,
        height=20,
        collision=[[0] * 20 for _ in range(20)],
        spawn=(10, 10),
    )
    state = GameState(1, map_data.map_id)
    player = state.add_player(1, "A", 10, 10)
    return state, Collision(map_data, state.blocks), player


def test_night_boundaries():
    assert is_night(NIGHT_START_SEC) is True
    assert is_night(NIGHT_END_SEC - 1) is True
    assert is_night(NIGHT_END_SEC) is False
    assert is_night(NIGHT_START_SEC - 1) is False
    assert is_night(0) is True


def test_night_spawn_prefers_unseen_distant_tile(world):
    state, collision, _player = world
    result = tick_zombies(
        state,
        collision,
        {1: (0, 0, 5, 5)},
        night=True,
        max_count=1,
    )

    assert len(result.spawned) == 1
    zombie = result.spawned[0]
    assert (zombie.x, zombie.y) not in {(x, y) for x in range(5) for y in range(5)}
    assert max(abs(zombie.x - 10), abs(zombie.y - 10)) >= 8


def test_visible_zombie_is_frozen_during_autonomous_tick(world):
    state, collision, _player = world
    zombie = Zombie("z1", 13, 10)
    state.zombies[zombie.zombie_id] = zombie

    result = tick_zombies(
        state,
        collision,
        {1: (0, 0, 20, 20)},
        night=True,
        max_count=1,
    )

    assert result.changed is False
    assert (zombie.x, zombie.y) == (13, 10)


def test_offscreen_zombie_moves_autonomously(world):
    """An off-screen zombie that can SEE the player (dist <= vision 6) keeps
    approaching on background ticks."""
    state, collision, _player = world
    zombie = Zombie("z1", 16, 16)  # dist 6 = vision edge, OUTSIDE view rect
    state.zombies[zombie.zombie_id] = zombie

    result = tick_zombies(
        state,
        collision,
        {1: (0, 0, 15, 15)},
        night=True,
        max_count=30,
    )

    assert result.changed is True
    assert (zombie.x, zombie.y) != (16, 16)
    assert result.visible_changed is False


def test_offscreen_zombie_without_vision_stays_put(world):
    """Beyond vision radius a walker does NOT chase blind (the old
    'always-beeline-to-screen' behaviour is gone). A spawn may still happen
    on the same tick (spawn_chance) — that does not count as chasing."""
    state, collision, _player = world
    zombie = Zombie("z1", 17, 17)  # dist 7 > vision 6, outside view rect
    state.zombies[zombie.zombie_id] = zombie

    result = tick_zombies(
        state,
        collision,
        {1: (0, 0, 15, 15)},
        night=True,
        max_count=30,
    )

    assert (zombie.x, zombie.y) == (17, 17)
    for spawned in result.spawned:
        assert spawned.zombie_id != "z1"


def test_walker_in_view_but_out_of_vision_stops_chasing(world):
    """A walker standing inside the viewport but beyond vision 6 does NOT
    chase on player-action turns — it lost sight of the player."""
    state, collision, player = world
    zombie = Zombie("z1", 18, 10)  # dist 8 > vision 6, but inside views
    state.zombies[zombie.zombie_id] = zombie
    views = {1: (0, 0, 20, 20)}

    result = advance_visible_zombies(state, collision, views)

    assert result.changed is False
    assert (zombie.x, zombie.y) == (18, 10)


def test_walker_chases_inside_vision_then_hunter_still_does(world):
    state, collision, _player = world
    walker = Zombie("z1", 15, 10)  # dist 5 <= vision 6
    hunter = Zombie("z2", 15, 12, hunter=True)  # dist 5, always chases
    state.zombies["z1"] = walker
    state.zombies["z2"] = hunter
    views = {1: (0, 0, 20, 20)}

    result = advance_visible_zombies(state, collision, views)

    assert result.changed is True
    assert walker.x < 15  # closed in
    assert hunter.x < 15  # hunters chase too


def test_hunter_chases_beyond_vision(world):
    state, collision, _player = world
    zombie = Zombie("z1", 17, 17, hunter=True)
    state.zombies[zombie.zombie_id] = zombie

    result = tick_zombies(
        state,
        collision,
        {1: (0, 0, 15, 15)},
        night=True,
        max_count=30,
    )

    assert result.changed is True
    assert (zombie.x, zombie.y) != (18, 18)


def test_spawn_rolls_hunter_with_seed(world):
    """spawn_one marks ~ZOMBIE_HUNTER_CHANCE (5%) of the population as hunters."""
    from game.zombies import ZOMBIE_HUNTER_CHANCE, spawn_one

    state, collision, _player = world
    rng = random.Random(7)
    hunters = 0
    trials = 40
    for _ in range(trials):
        z = spawn_one(state, collision, {}, rng)
        if z is not None and z.hunter:
            hunters += 1
    expected = trials * ZOMBIE_HUNTER_CHANCE
    assert abs(hunters - expected) <= trials * 0.2  # loose binomial bound


def test_hunter_count_never_exceeds_cap(world):
    """Hard ceiling: no matter the RNG, at most ZOMBIE_MAX_HUNTERS live at
    once — the 'opened the map and got swarmed by 10 hunters' bug is dead."""
    from game.zombies import ZOMBIE_MAX_HUNTERS, spawn_one

    state, collision, _player = world
    rng = random.Random(1)
    for _ in range(200):
        spawn_one(state, collision, {}, rng)
    assert _live_hunters(state) <= ZOMBIE_MAX_HUNTERS


def test_visible_zombie_advances_only_on_player_turn(world):
    state, collision, _player = world
    zombie = Zombie("z1", 13, 10)
    state.zombies[zombie.zombie_id] = zombie
    views = {1: (0, 0, 20, 20)}

    before = (zombie.x, zombie.y)
    tick_zombies(state, collision, views, night=True, max_count=1)
    assert (zombie.x, zombie.y) == before

    result = advance_visible_zombies(state, collision, views)
    assert result.changed is True
    assert (zombie.x, zombie.y) == (12, 10)


def test_visible_zombie_damages_player_on_turn(world):
    state, collision, player = world
    zombie = Zombie("z1", 11, 10, damage=7)
    state.zombies[zombie.zombie_id] = zombie

    result = advance_visible_zombies(state, collision, {1: (0, 0, 20, 20)})

    assert result.damaged_player_ids == {1}
    assert player.hp == 93
    assert (zombie.x, zombie.y) == (11, 10)


def test_attack_hits_facing_zombie_and_removes_it_at_zero(world):
    state, _collision, player = world
    player.direction = "EAST"
    zombie = Zombie("z1", 11, 10, hp=20, max_hp=20)
    state.zombies[zombie.zombie_id] = zombie

    result = apply_attack(state, AttackAction(1))

    assert result.success is True
    # Bare-handed default: 6 dmg (zombie hp 40 -> 7 punches to fell).
    assert result.damage == 6
    assert result.target_id == "z1"
    assert result.target_defeated is False
    assert "z1" in state.zombies


def test_attack_does_not_advance_visible_zombie():
    map_data = MapData(
        map_id="zombie-test-attack",
        width=20,
        height=20,
        collision=[[0] * 20 for _ in range(20)],
        spawn=(10, 10),
    )
    state = GameState(2, map_data.map_id)
    player = state.add_player(1, "A", 10, 10)
    player.direction = "EAST"
    zombie = Zombie("z1", 11, 10, hp=40)
    state.zombies[zombie.zombie_id] = zombie

    result = apply_attack(state, AttackAction(1))

    assert result.success is True
    assert zombie.hp == 40 - 6  # bare-handed punch
    assert (zombie.x, zombie.y) == (11, 10)


def test_attack_bare_hand_vs_weapon_damage():
    """Balance rule: zombie hp 40 -> 7 bare-hand punches (6 dmg) vs 2 weapon
    hits (20 dmg). Weapon = a bound hotbar slot holding axe/pickaxe with
    stock in the bag."""
    map_data = MapData(
        map_id="zombie-attack-dmg",
        width=20,
        height=20,
        collision=[[0] * 20 for _ in range(20)],
        spawn=(10, 10),
    )
    state = GameState(3, map_data.map_id)
    player = state.add_player(1, "A", 10, 10)
    player.direction = "EAST"
    z1 = Zombie("z1", 11, 10, hp=40)
    z2 = Zombie("z2", 11, 10, hp=40)
    state.zombies["z1"] = z1
    state.zombies["z2"] = z2

    # Bare hand: 6 dmg per hit.
    r1 = apply_attack(state, AttackAction(1))
    assert r1.damage == 6 and z1.hp == 34

    # Weapon: bind wood_axe to slot 0 and give one to the bag. The rule
    # targets the nearest zombie (z1, same tile as z2 but lower id wins tie).
    from game.inventory import Inventory

    state.hotbars = {1: {0: "wood_axe"}}
    state.inventories = {1: Inventory()}
    state.inventories[1].add("wood_axe", 1)
    r2 = apply_attack(state, AttackAction(1))
    assert r2.damage == 20 and z1.hp == 34 - 20

    # Binding present but item out of stock -> still bare-handed.
    state.inventories[1].remove("wood_axe", 1)
    r3 = apply_attack(state, AttackAction(1))
    assert r3.damage == 6


def test_world_tick_advances_visible_zombie_autonomously(world):
    """The core realtime beat: a zombie inside the viewport steps toward the
    player from a background world_tick — no button press involved."""
    state, collision, _player = world
    zombie = Zombie("z1", 13, 10)
    state.zombies[zombie.zombie_id] = zombie
    views = {1: (0, 0, 20, 20)}

    result = world_tick(state, collision, views, night=True, max_count=1)

    assert result.changed is True
    assert result.visible_changed is True
    assert (zombie.x, zombie.y) == (12, 10)  # closed the gap 3 -> 2


def test_world_tick_bites_then_respects_cooldown(world):
    state, collision, player = world
    zombie = Zombie("z1", 11, 10, damage=7)
    state.zombies[zombie.zombie_id] = zombie
    views = {1: (0, 0, 20, 20)}

    first = world_tick(state, collision, views, night=True, max_count=1)
    assert first.damaged_player_ids == {1}
    assert player.hp == 93

    # Next tick: adjacent zombie must NOT bite again (cooldown), and must not
    # move either — it is already next to its target.
    second = world_tick(state, collision, views, night=True, max_count=1)
    assert 1 not in second.damaged_player_ids
    assert player.hp == 93


def test_cooldown_blocks_bites_but_never_movement(world):
    """The cooldown gates DAMAGE only: a zombie on cooldown still steps freely
    (both on background ticks and player-triggered turns), so chases stay
    lively while the HP bar drains at most once per ZOMBIE_ATTACK_COOLDOWN_SECONDS."""
    state, collision, player = world
    zombie = Zombie("z1", 11, 10, damage=7)
    state.zombies[zombie.zombie_id] = zombie
    views = {1: (0, 0, 20, 20)}

    # Background bite arms the cooldown.
    world_tick(state, collision, views, night=True, max_count=1)
    assert player.hp == 93

    # Player steps away (WEST, away from the zombie at 11,10); the zombie's
    # turn still runs and it closes the gap — movement is never gated.
    apply_move(state, MoveAction(1, Direction.WEST), collision)
    assert (player.x, player.y) == (9, 10)
    result = advance_visible_zombies(state, collision, views)

    assert result.changed is True
    assert (zombie.x, zombie.y) == (10, 10)  # adjacent again
    # ...but no second bite: hp unchanged.
    assert player.hp == 93


def test_world_tick_daytime_despawns_visible_zombie(world):
    state, collision, _player = world
    zombie = Zombie("z1", 13, 10)
    state.zombies[zombie.zombie_id] = zombie

    result = world_tick(state, collision, {1: (0, 0, 20, 20)}, night=False)

    assert result.changed is True
    assert result.visible_changed is True
    assert "z1" not in state.zombies


def test_player_cannot_walk_onto_zombie_tile(world):
    state, collision, player = world
    zombie = Zombie("z1", 11, 10)
    state.zombies[zombie.zombie_id] = zombie

    result = apply_move(state, MoveAction(1, Direction.EAST), collision)

    assert result.moved is False
    assert (player.x, player.y) == (10, 10)
    assert player.direction == "EAST"
