"""Transient night zombies and visibility-gated world rules.

Background world ticks (game.manager._zombie_loop) call ``world_tick`` every
~1s: population upkeep runs off-screen while visible creatures take ONE turn
per tick themselves (a step toward the nearest player, or an attack when
adjacent — gated by an attack cooldown). The per-player coalescer + edit gate
in the Discord layer decide when a message actually gets re-rendered, so a
fast simulation never turns into fast message edits."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple


SECONDS_PER_DAY = 86400
NIGHT_START_SEC = 20 * 3600
NIGHT_END_SEC = 6 * 3600
ZOMBIE_MAX_COUNT = 3
ZOMBIE_SPAWN_CHANCE = 0.45
ZOMBIE_MIN_SPAWN_DISTANCE = 8
ZOMBIE_ATTACK_DAMAGE = 10
ZOMBIE_PLAYER_ATTACK_DAMAGE = 20  # with a weapon (axe/sword)
# Bare-handed punch: zombie HP 40 -> 7 hits to fell (5 with partial chip
# variance callers may add). Weapons are meant to feel meaningfully better.
BARE_HAND_ATTACK_DAMAGE = 6
ZOMBIE_HP = 40
# Legacy alias kept for callers/tests; the manager uses the configurable world
# tick so autonomous movement can feel responsive without rendering every tick.
ZOMBIE_TICK_SECONDS = 4.0
# Minimum seconds between two background bites from the same zombie. Movement
# is never gated — only the damage tick is, so chases stay lively without
# melting a player's HP bar between button presses.
ZOMBIE_ATTACK_COOLDOWN_SECONDS = 2.0
# ---- web realtime pack (20 Hz, float positions, no turns) -----------------
# Speeds are tiles/second in float space (mirrors WEB_WALK/RUN_SPEED: zombies
# shamble a touch slower than a walking player; hunters match a runner).
WEB_ZOMBIE_WALK_SPEED = 2.2
WEB_ZOMBIE_HUNTER_SPEED = 4.2
# Bite range in float tiles: touching distance + a small slack.
WEB_ZOMBIE_BITE_RANGE = 0.85
# Per-zombie bite cooldown (seconds): the "giật" fix — damage lands at most
# this often per zombie even though movement integrates every 50 ms.
WEB_ZOMBIE_BITE_COOLDOWN = 1.2
# Spawn/despawn distances in float tiles (mirror the int constants).
WEB_ZOMBIE_MIN_SPAWN_DIST = 8.0
WEB_ZOMBIE_DESPAWN_DIST = 90.0
WEB_ZOMBIE_VISION_RADIUS = 6.0
# Kaetram mob sheet rows for the zombie (5 cols x 9 rows of 32px, see
# util.ts getDefaultAnimations "mobs"): row 0 = atk (5f), row 1 = walk (4f),
# row 2 = idle (2f). Sent to the web client as anim state, NOT pixels.
WEB_Z_ANIM_ATK_ROW = 0
WEB_Z_ANIM_WALK_ROW = 1
WEB_Z_ANIM_IDLE_ROW = 2
WEB_Z_ATK_LEN = 5
WEB_Z_WALK_LEN = 4
WEB_Z_IDLE_LEN = 2

# ---- threat model (see README-ish notes in game.manager) -----------------
# A zombie "sees" a player within this Chebyshev radius; outside it the
# zombie wanders instead of chasing.
ZOMBIE_VISION_RADIUS = 6
# Population scales with the darkness around each player: within this many
# tiles of every player there may be at most ZOMBIE_AREA_MAX_COUNT zombies.
ZOMBIE_AREA_RADIUS = 75
ZOMBIE_AREA_MAX_COUNT = 30
# Chance a spawned zombie is a HUNTER: ignores vision (always knows where
# players are) and relentlessly chases the nearest one.
ZOMBIE_HUNTER_CHANCE = 0.005
# Hard ceiling on LIVE hunters in a scenario — even terrible luck can never
# field more than this many relentless chasers at once.
ZOMBIE_MAX_HUNTERS = 2
# Despawn when the nearest player is farther than this (they "wandered off").
ZOMBIE_DESPAWN_DISTANCE = 90
ZOMBIE_DROP_TABLE = (("rotten_flesh", 1.0, 1), ("coin", 0.35, 1))

_DIRECTIONS: Tuple[Tuple[int, int], ...] = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
)


@dataclass
class Zombie:
    zombie_id: str
    x: int
    y: int
    hp: int = ZOMBIE_HP
    max_hp: int = ZOMBIE_HP
    damage: int = ZOMBIE_ATTACK_DAMAGE
    kind: str = "walker"
    # time.monotonic() before which this zombie may not bite again (background
    # ticks). 0.0 = free to bite immediately (keeps player-triggered turns
    # exactly as responsive as before).
    attack_cooldown: float = 0.0
    # Hunters (25% of spawns) ignore vision and always chase the nearest
    # player; regular walkers only chase inside ZOMBIE_VISION_RADIUS.
    hunter: bool = False
    # Last chase step, kept so pursuit can keep momentum (less zig-zag).
    last_step: Optional[Tuple[int, int]] = None
    # ---- realtime web-pack fields (float space, ignored by the Discord pack)
    # Continuous position (float tile units, centre-based like players).
    # (x, y) ints stay synced (floor) so shared helpers keep working.
    x_f: float = 0.0
    y_f: float = 0.0
    # Facing for the sprite (one of N/S/E/W/NE/NW/SE/SW, Kaetram row pick).
    facing: str = "S"
    # Server-authoritative anim state for the web client: "walk" | "idle" |
    # "atk" — the client cuts the matching row/frame from its own sheet copy
    # (5 cols x 9 rows of 32px) instead of receiving the whole sheet as one
    # stretched texture. "atk" also drives the lunge + tint client-side.
    anim: str = "idle"
    anim_t: float = 0.0  # monotonic() when the current anim started
    # monotonic() of the last landed bite (web cooldown — the "giật" fix).
    last_bite: float = 0.0

    def sync_float_from_int(self) -> None:
        self.x_f = float(self.x) + 0.5
        self.y_f = float(self.y) + 0.5

    def sync_int_from_float(self) -> None:
        import math as _math

        self.x = _math.floor(self.x_f)
        self.y = _math.floor(self.y_f)

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class ZombieTurnResult:
    changed: bool = False
    # True when a spawn/despawn/step crossed or affected a player's viewport.
    visible_changed: bool = False
    spawned: List[Zombie] = field(default_factory=list)
    removed: List[Zombie] = field(default_factory=list)
    damaged_player_ids: Set[int] = field(default_factory=set)
    died_player_ids: Set[int] = field(default_factory=set)
    drops: List[Tuple[int, str, int]] = field(default_factory=list)


def is_night(second_of_day: int) -> bool:
    """Return whether the accelerated in-game clock is evening/night."""
    second = second_of_day % SECONDS_PER_DAY
    return second >= NIGHT_START_SEC or second < NIGHT_END_SEC


def _zombies(state) -> List[Zombie]:
    raw = getattr(state, "zombies", None)
    values = raw.values() if isinstance(raw, dict) else (raw or [])
    return [z for z in values if z.alive]


def _web_zombies(state) -> List[Zombie]:
    """Alive zombies of the SEPARATE realtime web pack."""
    raw = getattr(state, "web_zombies", None)
    values = raw.values() if isinstance(raw, dict) else (raw or [])
    return [z for z in values if z.alive]


def iter_zombies(state) -> List[Zombie]:
    """Return a snapshot of the currently alive zombies."""
    return list(_zombies(state))


def iter_web_zombies(state) -> List[Zombie]:
    """Return a snapshot of the currently alive WEB zombies."""
    return list(_web_zombies(state))


def _add_zombie(state, zombie: Zombie) -> None:
    raw = getattr(state, "zombies", None)
    if isinstance(raw, dict):
        raw[zombie.zombie_id] = zombie
        return
    if raw is None:
        state.zombies = {}
        state.zombies[zombie.zombie_id] = zombie
        return
    raw[zombie.zombie_id] = zombie


def _add_web_zombie(state, zombie: Zombie) -> None:
    raw = getattr(state, "web_zombies", None)
    if isinstance(raw, dict):
        raw[zombie.zombie_id] = zombie
        return
    if raw is None:
        state.web_zombies = {}
        state.web_zombies[zombie.zombie_id] = zombie
        return
    raw.append(zombie)


def remove_web_zombie(state, zombie_id: str) -> Optional[Zombie]:
    """Remove and return a WEB zombie by id, if still present."""
    raw = getattr(state, "web_zombies", None)
    if isinstance(raw, dict):
        return raw.pop(zombie_id, None)
    if raw is None:
        return None
    for zombie in list(raw):
        if zombie.zombie_id == zombie_id:
            raw.remove(zombie)
            return zombie
    return None


def _next_web_id(state) -> str:
    state.web_zombie_seq = getattr(state, "web_zombie_seq", 0) + 1
    return f"wzombie-{state.web_zombie_seq}"


def remove_zombie(state, zombie_id: str) -> Optional[Zombie]:
    """Remove and return a zombie by id, if it is still present."""
    raw = getattr(state, "zombies", None)
    if isinstance(raw, dict):
        return raw.pop(zombie_id, None)
    if raw is None:
        return None
    for zombie in list(raw):
        if zombie.zombie_id == zombie_id:
            raw.remove(zombie)
            return zombie
    return None


def _next_id(state) -> str:
    state.zombie_seq = getattr(state, "zombie_seq", 0) + 1
    return f"zombie-{state.zombie_seq}"


def _players(state) -> List[object]:
    # Discord turn-based pack: SEES ONLY Discord players. Web players run on
    # their own realtime pack (web_zombies) — never chased/bitten/counted
    # here, so chat turns stay instant no matter how wild the web gets.
    return [
        p for p in state.get_visible_players()
        if getattr(p, "alive", True) and not getattr(p, "is_web", False)
    ]


def _web_players(state) -> List[object]:
    """Visible + alive WEB players (targets of the realtime web pack)."""
    return [
        p for p in state.get_visible_players()
        if getattr(p, "alive", True) and getattr(p, "is_web", False)
    ]


def _inside_xy(x: int, y: int, rect: tuple) -> bool:
    x0, y0, x1, y1 = rect
    return x0 <= x < x1 and y0 <= y < y1


def _in_any_view_xy(x: int, y: int, view_rects: Dict[int, tuple]) -> bool:
    return any(_inside_xy(x, y, rect) for rect in (view_rects or {}).values())


def _in_any_view(zombie: Zombie, view_rects: Dict[int, tuple]) -> bool:
    return _in_any_view_xy(zombie.x, zombie.y, view_rects)


def _distance_xy(x: int, y: int, target) -> int:
    return max(abs(x - target.x), abs(y - target.y))


def _nearest_player(zombie: Zombie, players: Iterable[object]):
    choices = list(players)
    if not choices:
        return None
    return min(choices, key=lambda p: (_distance_xy(zombie.x, zombie.y, p), p.user_id))


def _occupied(state, ignore: Optional[Zombie] = None) -> set:
    occupied = {
        (p.x, p.y)
        for p in state.get_visible_players()
        if getattr(p, "alive", True)
    }
    occupied.update(
        (z.x, z.y) for z in _zombies(state) if z is not ignore
    )
    return occupied


def _move_options(zombie: Zombie, target, rng: random.Random) -> List[Tuple[int, int]]:
    """Chase options: the straight/diagonal steps that close the gap, ordered
    with a bias toward the PREVIOUS step so a pack spreads out instead of
    marching in lockstep down identical corridors."""
    if target is not None:
        dx = (target.x > zombie.x) - (target.x < zombie.x)
        dy = (target.y > zombie.y) - (target.y < zombie.y)
        preferred: List[Tuple[int, int]] = []
        if dx or dy:
            preferred.append((dx, dy))
            if dx:
                preferred.append((dx, 0))
            if dy:
                preferred.append((0, dy))
        # Momentum bias: keeping the last step first ~40% of the time breaks
        # the "three zombies always take the same tile" pattern.
        if zombie.last_step is not None and zombie.last_step in preferred:
            preferred.remove(zombie.last_step)
            preferred.insert(0, zombie.last_step)
        remaining = [d for d in _DIRECTIONS if d not in preferred]
        rng.shuffle(remaining)
        return preferred + remaining
    options = list(_DIRECTIONS)
    rng.shuffle(options)
    return options


def _move_one(state, collision, zombie: Zombie, target, rng: random.Random) -> bool:
    occupied = _occupied(state, ignore=zombie)
    for dx, dy in _move_options(zombie, target, rng):
        nx, ny = zombie.x + dx, zombie.y + dy
        if (nx, ny) in occupied or not collision.is_walkable(nx, ny):
            continue
        zombie.x, zombie.y = nx, ny
        zombie.last_step = (dx, dy)
        return True
    return False


def _sees_player(zombie: Zombie, players: Iterable[object]) -> bool:
    """Walkers chase only within ZOMBIE_VISION_RADIUS; hunters always do."""
    if getattr(zombie, "hunter", False):
        return True
    return any(
        _distance_xy(zombie.x, zombie.y, p) <= ZOMBIE_VISION_RADIUS for p in players
    )


def _count_near_players(state) -> int:
    """Zombies within ZOMBIE_AREA_RADIUS of ANY player (the crowd quota pool)."""
    players = _players(state)
    return sum(
        1
        for z in _zombies(state)
        if any(_distance_xy(z.x, z.y, p) <= ZOMBIE_AREA_RADIUS for p in players)
    )


def _spawn_position(state, collision, view_rects: Dict[int, tuple], rng: random.Random):
    """Random walkable tile near (but not on) a player: inside the crowd area
    (<= ZOMBIE_AREA_RADIUS of someone), outside every viewport, not too close
    to any player. Random pick = spawns scatter instead of stacking at one
    "preferred" ring position."""
    players = _players(state)
    if not players:
        return None

    occupied = _occupied(state)
    width = collision.map_data.width
    height = collision.map_data.height
    candidates: List[Tuple[int, int]] = []

    for y in range(height):
        for x in range(width):
            if (x, y) in occupied or not collision.is_walkable(x, y):
                continue
            if _in_any_view_xy(x, y, view_rects):
                continue
            dists = [max(abs(x - p.x), abs(y - p.y)) for p in players]
            if min(dists) < ZOMBIE_MIN_SPAWN_DISTANCE:
                continue
            if min(dists) > ZOMBIE_AREA_RADIUS:
                continue
            candidates.append((x, y))

    # Fall back to anywhere legal off-screen; tiny maps may not have the ideal
    # ring. Still random, still never inside a viewport.
    if not candidates:
        candidates = [
            (x, y) for y in range(height) for x in range(width)
            if (x, y) not in occupied and collision.is_walkable(x, y)
            and not _in_any_view_xy(x, y, view_rects)
        ]
    return rng.choice(candidates) if candidates else None


def _live_hunters(state) -> int:
    return sum(1 for z in _zombies(state) if getattr(z, "hunter", False))


def spawn_one(state, collision, view_rects: Dict[int, tuple], rng: random.Random) -> Optional[Zombie]:
    position = _spawn_position(state, collision, view_rects, rng)
    if position is None:
        return None
    zombie = Zombie(_next_id(state), position[0], position[1])
    # 5% hunter roll, but NEVER more than ZOMBIE_MAX_HUNTERS alive at once —
    # a pack of relentless chasers must not be possible, only a stray one.
    zombie.hunter = (
        _live_hunters(state) < ZOMBIE_MAX_HUNTERS
        and rng.random() < ZOMBIE_HUNTER_CHANCE
    )
    _add_zombie(state, zombie)
    return zombie


def _despawn_distant(state, view_rects: Dict[int, tuple], result: "ZombieTurnResult") -> None:
    """Remove zombies that wandered too far from every player — keeps the
    population near the action and lets the spawner refill the area."""
    players = _players(state)
    for zombie in list(_zombies(state)):
        if players and all(
            _distance_xy(zombie.x, zombie.y, p) > ZOMBIE_DESPAWN_DISTANCE
            for p in players
        ):
            removed = remove_zombie(state, zombie.zombie_id)
            if removed is not None:
                result.removed.append(removed)
                result.changed = True
                if _in_any_view(removed, view_rects):
                    result.visible_changed = True


def _pursuit_target(zombie: Zombie, player, state):
    """Aim slightly ahead of a travelling player to avoid a predictable tail."""
    if player is None:
        return None
    previous = getattr(state, "previous_positions", {}).get(player.user_id)
    if previous is None:
        return player
    dx = player.x - previous[0]
    dy = player.y - previous[1]
    if (dx or dy) and _distance_xy(zombie.x, zombie.y, player) > 2:
        return type("PursuitTarget", (), {
            "x": player.x + dx * 2, "y": player.y + dy * 2,
        })()
    return player


def tick_zombies(
    state,
    collision,
    view_rects: Dict[int, tuple],
    night: bool,
    rng: Optional[random.Random] = None,
    max_count: int = ZOMBIE_MAX_COUNT,
    spawn_chance: float = ZOMBIE_SPAWN_CHANCE,
) -> ZombieTurnResult:
    """Advance autonomous zombies and maintain the night population.

    Zombies outside every player viewport get one autonomous step per tick.
    A zombie entering a viewport stops moving HERE (``advance_visible_zombies``
    owns visible turns); this function only maintains the off-screen population.
    """
    rng = rng or random.Random()
    result = ZombieTurnResult()
    zombies = _zombies(state)

    if not night or not _players(state):
        for zombie in zombies:
            if _in_any_view(zombie, view_rects):
                result.visible_changed = True
            removed = remove_zombie(state, zombie.zombie_id)
            if removed is not None:
                result.removed.append(removed)
        result.changed = bool(result.removed)
        return result

    # Distant zombies leave; the area cap (darker -> denser via caller) then
    # lets the spawner refill the ring around the players.
    _despawn_distant(state, view_rects, result)

    spawned_ids = set()
    near = _count_near_players(state)
    if near < max_count and (
        not zombies or rng.random() < max(0.0, min(1.0, spawn_chance))
    ):
        zombie = spawn_one(state, collision, view_rects, rng)
        if zombie is not None:
            result.spawned.append(zombie)
            spawned_ids.add(zombie.zombie_id)
            result.changed = True
            if _in_any_view(zombie, view_rects):
                result.visible_changed = True

    players = _players(state)
    for zombie in list(_zombies(state)):
        if zombie.zombie_id in spawned_ids or _in_any_view(zombie, view_rects):
            continue
        # Off-screen wander: only toward players the zombie can actually see
        # (hunters always). Blind wandering toward unseen players is gone.
        if not _sees_player(zombie, players):
            continue
        before = (zombie.x, zombie.y)
        target = _pursuit_target(zombie, _nearest_player(zombie, players), state)
        if target is None or not _move_one(state, collision, zombie, target, rng):
            continue
        result.changed = True
        if _in_any_view(zombie, view_rects) and before != (zombie.x, zombie.y):
            result.visible_changed = True

    return result


def advance_visible_zombies(
    state,
    collision,
    view_rects: Dict[int, tuple],
    rng: Optional[random.Random] = None,
) -> ZombieTurnResult:
    """Take exactly one zombie turn for creatures currently in a viewport.

    Called after every successful player step (instant feedback) AND from each
    background ``world_tick`` (autonomous motion). Bites respect the zombie's
    attack cooldown so background ticks cannot chain damage between presses.
    """
    rng = rng or random.Random()
    now = time.monotonic()
    result = ZombieTurnResult()
    players = _players(state)

    for zombie in list(_zombies(state)):
        observers = [
            p for p in players
            if p.user_id in (view_rects or {})
            and _inside_xy(zombie.x, zombie.y, view_rects[p.user_id])
        ]
        if not observers:
            continue
        target = _nearest_player(zombie, observers)
        if target is None:
            continue
        # Lose interest: a walker whose target broke out of vision radius stops
        # chasing (it "lost sight" of the player). Hunters never let go.
        if not _sees_player(zombie, [target]):
            continue
        if _distance_xy(zombie.x, zombie.y, target) <= 1:
            if getattr(zombie, "attack_cooldown", 0.0) > now:
                continue  # still chewing on the last bite
            before = target.hp
            target.hp = max(0, target.hp - zombie.damage)
            if target.hp != before:
                result.changed = True
                result.visible_changed = True
                result.damaged_player_ids.add(target.user_id)
                zombie.attack_cooldown = now + ZOMBIE_ATTACK_COOLDOWN_SECONDS
                if target.hp <= 0:
                    target.visible = False
                    target.dead_until = time.time() + 5.0
                    target.death_reason = "bị zombie tấn công"
                    result.died_player_ids.add(target.user_id)
            continue
        if _move_one(state, collision, zombie, target, rng):
            result.changed = True
            result.visible_changed = True

    return result


def world_tick(
    state,
    collision,
    view_rects: Dict[int, tuple],
    night: bool,
    rng: Optional[random.Random] = None,
    max_count: int = ZOMBIE_MAX_COUNT,
    spawn_chance: float = ZOMBIE_SPAWN_CHANCE,
) -> ZombieTurnResult:
    """One background simulation beat: population upkeep + autonomous turns.

    Merges ``tick_zombies`` (off-screen wander/spawn/despawn) with
    ``advance_visible_zombies`` (on-screen chase/bite) into the single call the
    manager loop makes every WORLD_TICK_SECONDS. Damage only lands when a
    zombie's attack cooldown has expired; movement is never rate-limited."""
    rng = rng or random.Random()
    result = tick_zombies(
        state, collision, view_rects, night,
        rng=rng, max_count=max_count, spawn_chance=spawn_chance,
    )
    visible = advance_visible_zombies(state, collision, view_rects, rng=rng)
    result.changed = result.changed or visible.changed
    result.visible_changed = result.visible_changed or visible.visible_changed
    result.damaged_player_ids |= visible.damaged_player_ids
    result.died_player_ids |= visible.died_player_ids
    result.drops.extend(visible.drops)
    return result


def _web_dist(z: Zombie, p) -> float:
    import math as _math

    return _math.hypot(z.x_f - p.x_f, z.y_f - p.y_f)


def _web_nearest(z: Zombie, players: List[object]):
    choices = list(players)
    if not choices:
        return None
    return min(choices, key=lambda p: (_web_dist(z, p), p.user_id))


def _web_facing(dx: float, dy: float) -> str:
    if abs(dx) > abs(dy):
        return "E" if dx > 0 else "W"
    if abs(dy) > abs(dx):
        return "S" if dy > 0 else "N"
    if dx > 0:
        return "SE" if dy > 0 else "NE"
    return "SW" if dy > 0 else "NW"


def _web_set_anim(z: Zombie, anim: str, now: float) -> None:
    if z.anim != anim:
        z.anim = anim
        z.anim_t = now


def web_spawn_one(state, collision, players: List[object], rng: random.Random) -> Optional[Zombie]:
    """Spawn one web zombie in a ring around a random web player.

    Ring: >= WEB_ZOMBIE_MIN_SPAWN_DIST away (no pop-in on the player),
    walkable tile, centre-snapped float pos. None when no tile fits.
    """
    import math as _math

    alive = [p for p in players if getattr(p, "alive", True)]
    if not alive:
        return None
    anchor = rng.choice(alive)
    w = getattr(getattr(collision, "map_data", None), "width", 0) or 0
    h = getattr(getattr(collision, "map_data", None), "height", 0) or 0
    if not w or not h:
        return None
    hunters = sum(1 for z in _web_zombies(state) if getattr(z, "hunter", False))
    for _ in range(24):
        ang = rng.uniform(0, 2 * _math.pi)
        dist = rng.uniform(WEB_ZOMBIE_MIN_SPAWN_DIST, WEB_ZOMBIE_MIN_SPAWN_DIST + 10.0)
        tx = int(_math.floor(anchor.x_f + _math.cos(ang) * dist))
        ty = int(_math.floor(anchor.y_f + _math.sin(ang) * dist))
        if tx < 0 or ty < 0 or tx >= w or ty >= h:
            continue
        try:
            walkable = collision.is_walkable(tx, ty)
        except Exception:
            walkable = True
        if not walkable:
            continue
        z = Zombie(_next_web_id(state), tx, ty)
        z.x_f = float(tx) + 0.5
        z.y_f = float(ty) + 0.5
        z.hunter = hunters < ZOMBIE_MAX_HUNTERS and rng.random() < ZOMBIE_HUNTER_CHANCE
        z.facing = "S"
        z.anim = "walk"
        z.anim_t = time.monotonic()
        _add_web_zombie(state, z)
        return z
    return None


def web_tick(
    state,
    collision,
    night: bool,
    dt: float,
    rng: Optional[random.Random] = None,
    max_count: int = ZOMBIE_MAX_COUNT,
    spawn_chance: float = ZOMBIE_SPAWN_CHANCE,
) -> ZombieTurnResult:
    """One 20 Hz realtime beat for the WEB pack (inside the web tick).

    Day/night + spawn upkeep + float steering + cooldown bites. NEVER touches
    state.zombies — separate ids/store/math from the Discord turn pack.
    """
    import math as _math

    rng = rng or random.Random()
    now_mono = time.monotonic()
    now_wall = time.time()
    result = ZombieTurnResult()
    players = _web_players(state)

    if not night or not players:
        for z in list(_web_zombies(state)):
            removed = remove_web_zombie(state, z.zombie_id)
            if removed is not None:
                result.removed.append(removed)
                result.changed = True
        result.visible_changed = bool(result.removed)
        return result

    for z in list(_web_zombies(state)):
        nearest = _web_nearest(z, players)
        if nearest is not None and _web_dist(z, nearest) > WEB_ZOMBIE_DESPAWN_DIST:
            removed = remove_web_zombie(state, z.zombie_id)
            if removed is not None:
                result.removed.append(removed)
                result.changed = True
    zombies = _web_zombies(state)
    if len(zombies) < max_count and (
        not zombies or rng.random() < max(0.0, min(1.0, spawn_chance))
    ):
        spawned = web_spawn_one(state, collision, players, rng)
        if spawned is not None:
            result.spawned.append(spawned)
            result.changed = True
            result.visible_changed = True
            zombies = _web_zombies(state)

    step = max(0.0, min(0.25, dt))
    for z in list(zombies):
        target = _web_nearest(z, players)
        if target is None:
            _web_set_anim(z, "idle", now_mono)
            continue
        dist = _web_dist(z, target)
        dx = target.x_f - z.x_f
        dy = target.y_f - z.y_f
        length = _math.hypot(dx, dy)
        if dist <= WEB_ZOMBIE_BITE_RANGE:
            _web_set_anim(z, "atk", now_mono)
            z.facing = _web_facing(dx, dy)
            if now_mono - (z.last_bite or 0.0) >= WEB_ZOMBIE_BITE_COOLDOWN:
                before = target.hp
                target.hp = max(0, target.hp - z.damage)
                if target.hp != before:
                    result.changed = True
                    result.damaged_player_ids.add(target.user_id)
                    z.last_bite = now_mono
                    if target.hp <= 0:
                        target.visible = False
                        target.dead_until = now_wall + 5.0
                        target.death_reason = "bị zombie tấn công"
                        result.died_player_ids.add(target.user_id)
            continue
        sees = z.hunter or dist <= WEB_ZOMBIE_VISION_RADIUS
        if not sees or length <= 1e-6:
            _web_set_anim(z, "idle", now_mono)
            continue
        speed = WEB_ZOMBIE_HUNTER_SPEED if z.hunter else WEB_ZOMBIE_WALK_SPEED
        ux, uy = dx / length, dy / length
        can_float = getattr(collision, "can_move_float", None)
        if callable(can_float):
            try:
                nx_f, ny_f = can_float(z.x_f, z.y_f, ux * speed * step, uy * speed * step)
            except Exception:
                nx_f, ny_f = z.x_f + ux * speed * step, z.y_f + uy * speed * step
        else:
            nx_f, ny_f = z.x_f, z.y_f
            try:
                tx_a = int(_math.floor(z.x_f + ux * speed * step))
                ty_a = int(_math.floor(z.y_f))
                if collision.is_walkable(tx_a, ty_a):
                    nx_f = z.x_f + ux * speed * step
                if collision.is_walkable(int(_math.floor(nx_f)), int(_math.floor(z.y_f + uy * speed * step))):
                    ny_f = z.y_f + uy * speed * step
            except Exception:
                nx_f, ny_f = z.x_f + ux * speed * step, z.y_f + uy * speed * step
        if (nx_f, ny_f) != (z.x_f, z.y_f):
            z.x_f, z.y_f = nx_f, ny_f
            z.sync_int_from_float()
            z.facing = _web_facing(ux, uy)
            _web_set_anim(z, "walk", now_mono)
            result.changed = True
        else:
            _web_set_anim(z, "idle", now_mono)
    if result.changed:
        result.visible_changed = True
    return result
