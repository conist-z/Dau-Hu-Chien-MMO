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
# Night mob population: +15% over the original 3 (user 2026-09-14), the
# extra spawns distributed across MOB_KINDS by spawn weight.
NIGHT_MOB_COUNT_SCALE = 1.15
# Precomputed default caps (+15%, ceil so 3.45 -> 4 and 30 -> 35).
import math as _math

NIGHT_MOB_MAX_COUNT = _math.ceil(ZOMBIE_MAX_COUNT * NIGHT_MOB_COUNT_SCALE)

# ---- night mob kinds (Kaetram 02_mobs_stats + sprites.json) ---------------
# Stats derived from the Kaetram mob stats relative to the zombie baseline
# (zombie HP 40 / dmg 10 in our scale). Spawn weights DESCEND by rarity:
# zombie (most common) -> rat (rarest), per the user's order.
# speed = web tiles/s; cooldown = seconds between bites.
MOB_KINDS: Dict[str, dict] = {
    #        hp_mult dmg_mult speed  cd    weight (zombie highest, rat lowest)
    "zombie":   dict(hp=40, dmg=10, speed=2.2, cooldown=1.8, weight=38),
    "skeleton": dict(hp=56, dmg=11, speed=2.0, cooldown=2.2, weight=22),   # tanky, slow (lvl14/HP140)
    "spider":   dict(hp=44, dmg=10, speed=2.2, cooldown=1.8, weight=16),   # lvl47/HP650 ~ zombie-ish
    "slime":    dict(hp=50, dmg=9,  speed=2.2, cooldown=1.8, weight=12),   # lvl48/HP694 tanky
    "bat":      dict(hp=20, dmg=6,  speed=2.8, cooldown=1.6, weight=8),    # lvl4/HP65 fast swarm
    "rat":      dict(hp=12, dmg=4,  speed=2.6, cooldown=2.0, weight=4),    # lvl1/HP20 pest
    # ---- cave/forest packs (game/mob_profiles.py routes them per map) ----
    # Stats scaled from Kaetram _all_mobs.json relative to the zombie row.
    "skeleton2": dict(hp=56, dmg=14, speed=2.0, cooldown=2.6, weight=0),  # lvl30/HP375 tanky bruiser
    "spectre":   dict(hp=27, dmg=9,  speed=1.8, cooldown=2.4, weight=0),  # lvl32/HP270 ghost, RANGED
    "goblin":    dict(hp=9,  dmg=4,  speed=2.4, cooldown=2.0, weight=0),  # lvl7/HP90 weak nuisance
    "hobgoblin": dict(hp=26, dmg=13, speed=2.2, cooldown=1.8, weight=0),  # lvl42/HP260 aggressive bruiser
}
MOB_SPAWN_WEIGHTS: List[Tuple[str, float]] = [
    (kind, float(cfg["weight"])) for kind, cfg in MOB_KINDS.items()
]


def roll_mob_kind(rng: random.Random) -> str:
    """Weighted kind roll — zombie most common, rat rarest."""
    total = sum(w for _k, w in MOB_SPAWN_WEIGHTS)
    r = rng.uniform(0.0, total)
    acc = 0.0
    for kind, w in MOB_SPAWN_WEIGHTS:
        acc += w
        if r <= acc:
            return kind
    return MOB_SPAWN_WEIGHTS[0][0]


def mob_stats(kind: str) -> dict:
    """Stat dict for a kind (falls back to the zombie row)."""
    return MOB_KINDS.get(kind) or MOB_KINDS["zombie"]
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
# Per-zombie bite cooldown (seconds): damage lands at most this often per
# zombie even though movement integrates every 50 ms.
WEB_ZOMBIE_BITE_COOLDOWN = 1.8
# Length of ONE bite swing on the web client (4 atk frames x 90 ms). Kept a
# touch SHORTER than the bite cooldown so the pose un-freezes before the
# next bite re-arms it.
WEB_ZOMBIE_ATK_MS = 4 * 90
# Recovery window AFTER a bite (seconds): the zombie stops lunging and takes
# 1-2 shuffling steps away/sideways (anim=walk) before closing in again.
# Total rhythm = atk swing (~0.36s) + recovery (~0.7s) between bites.
WEB_ZOMBIE_RECOVER_S = 0.7
# How far the recovery shuffle drifts (tiles/s, fractional so it looks like
# hesitant small steps rather than a determined retreat).
WEB_ZOMBIE_RECOVER_SPEED = 1.4
# Spawn/despawn distances in float tiles (mirror the int constants).
WEB_ZOMBIE_MIN_SPAWN_DIST = 8.0
WEB_ZOMBIE_DESPAWN_DIST = 90.0
WEB_ZOMBIE_VISION_RADIUS = 6.0
# Hard chase leash (tiles, centre distance): a zombie NEVER chases or bites
# beyond this even if its steering got confused by a lagging player ghost.
# Without a leash a bad server tick made zombies pursue (and damage) a
# player-position from tiles away ("đánh mình khi đứng xa 7-8 ô").
WEB_ZOMBIE_LEASH = 3.5
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
ZOMBIE_AREA_MAX_COUNT = 35  # +15% over 30 (NIGHT_MOB_COUNT_SCALE)
# Chance a spawned zombie is a HUNTER: ignores vision (always knows where
# players are) and relentlessly chases the nearest one.
ZOMBIE_HUNTER_CHANCE = 0.005
# Hard ceiling on LIVE hunters in a scenario — even terrible luck can never
# field more than this many relentless chasers at once.
ZOMBIE_MAX_HUNTERS = 2
# Despawn when the nearest player is farther than this (they "wandered off").
ZOMBIE_DESPAWN_DISTANCE = 90
ZOMBIE_DROP_TABLE = (
    ("rotten_flesh", 1.0, 1),
    ("coin", 0.35, 1),
    ("raw_meat", 0.5, 1),  # cook it in the furnace (game/smelting.py)
)

# Per-kind night-mob drop tables: (item_id, chance 0..1, qty).
# Themed per Kaetram flavor; everything is cookable/spendable in existing
# systems (smelting, purse) — no new item mechanics.
MOB_DROP_TABLES: Dict[str, tuple] = {
    "zombie": ZOMBIE_DROP_TABLE,
    "skeleton": (
        ("coin", 0.6, 1),          # adventurers' remains
        ("coal", 0.45, 1),         # smelt fuel
        ("stick", 0.35, 1),
    ),
    "spider": (
        ("stick", 0.4, 1),         # stand-in until a "string" item exists
        ("coin", 0.45, 1),
        ("raw_meat", 0.35, 1),
    ),
    "slime": (
        ("apple", 0.45, 1),        # jelly-ish treat
        ("coin", 0.4, 1),
        ("leaves", 0.3, 1),
    ),
    "bat": (
        ("coin", 0.3, 1),
        ("coal", 0.25, 1),         # cave dweller
    ),
    "rat": (
        ("rotten_flesh", 0.4, 1),
        ("coin", 0.2, 1),
    ),
    "skeleton2": (
        ("coin", 0.7, 2),          # richer grave-robber remains
        ("coal", 0.5, 1),
        ("stick", 0.3, 1),
    ),
    "spectre": (
        ("coin", 0.55, 1),
        ("coal", 0.3, 1),          # cave wisp residue
        ("leaves", 0.2, 1),
    ),
    "goblin": (
        ("stick", 0.45, 1),
        ("coin", 0.35, 1),
        ("apple", 0.25, 1),        # stolen snacks
    ),
    "hobgoblin": (
        ("coin", 0.6, 1),
        ("raw_meat", 0.4, 1),
        ("coal", 0.2, 1),
    ),
}


def mob_drop_table(kind: str) -> tuple:
    """Drop table for a mob kind (falls back to the zombie one)."""
    return MOB_DROP_TABLES.get(kind) or ZOMBIE_DROP_TABLE

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
    # Mob kind: "zombie" | "skeleton" | "spider" | "slime" | "bat" | "rat".
    # Drives stats (MOB_KINDS), drops, and the client's sprite sheet choice.
    kind: str = "zombie"
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
    # Web pack attack rhythm: after ONE bite the zombie plays a short
    # "recovery" window (shuffle back/strafe, anim=walk) before lunging again
    # — no more vibrating in place on the lunge pose.
    attack_recover_until: float = 0.0
    # Per-kind web stats (filled by web_spawn_one from MOB_KINDS): chase
    # speed (tiles/s) and bite cooldown (seconds).
    web_speed: float = WEB_ZOMBIE_WALK_SPEED
    web_cooldown: float = WEB_ZOMBIE_BITE_COOLDOWN
    # Unit-vector of the recovery drift, picked once per bite.
    recover_dx: float = 0.0
    recover_dy: float = 0.0
    # Per-kind combat style extras (game/mob_profiles.MOB_BEHAVIORS):
    # ambush = spider camouflage state, recover_until = skittish hit-and-run
    # retreat window (monotonic).
    ambush_armed: bool = False
    ambush_until: float = 0.0
    recover_until: float = 0.0

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


def _in_bounds_float(x: float, y: float, w: int, h: int) -> bool:
    """Strict in-map test for float positions (VOID GUARD): a mob centre may
    never sit on/behind the outer wall band, so nothing can drift into the
    black void around converted maps."""
    return 0.5 <= x <= w - 0.5 and 0.5 <= y <= h - 0.5


def _live_hunters(state) -> int:
    return sum(1 for z in _zombies(state) if getattr(z, "hunter", False))


def spawn_one(state, collision, view_rects: Dict[int, tuple], rng: random.Random) -> Optional[Zombie]:
    position = _spawn_position(state, collision, view_rects, rng)
    if position is None:
        return None
    zombie = Zombie(_next_id(state), position[0], position[1])
    # Kind roll is PER-MAP (game/mob_profiles.py): cave fields bats, forest
    # fields goblins, bigmap keeps the mixed night roster.
    from game.mob_profiles import roll_kind_for

    zombie.kind = roll_kind_for(
        getattr(getattr(collision, "map_data", None), "map_id", "bigmap"), rng
    )
    stats = mob_stats(zombie.kind)
    zombie.hp = zombie.max_hp = stats["hp"]
    zombie.damage = stats["dmg"]
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
    max_count: int = NIGHT_MOB_MAX_COUNT,
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
                # Out-of-combat regen clock: any HP loss re-arms the 5 s wait.
                target.last_damaged_at = time.monotonic()
                target.regen_bank = 0.0
                result.changed = True
                result.visible_changed = True
                result.damaged_player_ids.add(target.user_id)
                # Hitsplat feed (chat pack bites appear on web too).
                feed = getattr(state, "recent_damage", None)
                if feed is not None:
                    import time as _t
                    feed.append((_t.time(), target.user_id, zombie.damage, "zombie"))
                    del feed[:-40]
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
    max_count: int = NIGHT_MOB_MAX_COUNT,
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
        # VOID GUARD: keep the spawn centre at least half a tile inside the
        # map so nothing pops in the black band around converted maps.
        if not _in_bounds_float(tx + 0.5, ty + 0.5, w, h):
            continue
        if not walkable:
            continue
        z = Zombie(_next_web_id(state), tx, ty)
        # Kind roll FIRST (per-map profile), then per-kind stats.
        from game.mob_profiles import roll_kind_for

        z.kind = roll_kind_for(
            getattr(getattr(collision, "map_data", None), "map_id", "bigmap"), rng
        )
        stats = mob_stats(z.kind)
        z.hp = z.max_hp = stats["hp"]
        z.damage = stats["dmg"]
        z.web_speed = stats["speed"]
        z.web_cooldown = stats["cooldown"]
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
    max_count: int = NIGHT_MOB_MAX_COUNT,
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
        # Per-kind combat style (game/mob_profiles.MOB_BEHAVIORS).
        from game.mob_profiles import behavior_of

        beh = behavior_of(z.kind)
        style = beh.get("style", "melee")
        dx = target.x_f - z.x_f
        dy = target.y_f - z.y_f
        length = _math.hypot(dx, dy)

        # ---- RANGED (spectre): fires from a distance, never closes in. ----
        if style == "ranged":
            rng_range = float(beh.get("ranged_range", 4.0))
            since = now_mono - (z.last_bite or 0.0)
            if dist <= rng_range:
                z.facing = _web_facing(dx, dy)
                if since >= z.web_cooldown:
                    _web_set_anim(z, "atk", now_mono)
                    z.last_bite = now_mono
                    before = target.hp
                    target.hp = max(0, target.hp - z.damage)
                    if target.hp != before:
                        target.last_damaged_at = now_mono
                        target.regen_bank = 0.0
                        result.changed = True
                        result.damaged_player_ids.add(target.user_id)
                        feed = getattr(state, "recent_damage", None)
                        if feed is not None:
                            feed.append((now_wall, target.user_id, z.damage, "zombie"))
                            del feed[:-40]
                        if target.hp <= 0:
                            target.visible = False
                            target.dead_until = now_wall + 5.0
                            target.death_reason = "bị spectre bắn trúng"
                            result.died_player_ids.add(target.user_id)
                else:
                    _web_set_anim(z, "idle", now_mono)
                continue  # hold position — a ghost never trades punches
            # Out of range: drift slowly closer (casters keep their distance
            # once in range, so the chase speed stays the low MOB_KINDS one).
            # _web_chase_step takes a UNIT vector (dx/length, dy/length) —
            # the old call passed the raw vector + length as extra positionals,
            # shifting every arg one slot and double-feeding `speed`
            # (TypeError: got multiple values for 'speed' — the ranged
            # caster's whole web tick crashed every 50 ms).
            if length > 1e-6:
                _web_chase_step(
                    z, dx / length, dy / length, collision, step, now_mono,
                    result, speed=z.web_speed,
                )
            continue

        # ---- AMBUSH (spider): camouflaged until the prey is close, then a
        # short fast pounce burst before settling into normal chase speed.
        if style == "ambush":
            trigger = float(beh.get("ambush_bonus_vision", 6.0)) * 0.5
            if not getattr(z, "ambush_armed", False):
                if dist <= trigger:
                    z.ambush_armed = True
                    z.ambush_until = now_mono + 2.0  # pounce window
                    _web_set_anim(z, "atk", now_mono)
                else:
                    _web_set_anim(z, "idle", now_mono)  # waiting in its web
                    continue
        else:
            z.ambush_armed = False

        # LEASH: beyond WEB_ZOMBIE_LEASH a zombie simply loses interest (no
        # steering, no bite) — every attack the player sees is within reach.
        if dist > WEB_ZOMBIE_LEASH:
            _web_set_anim(z, "idle", now_mono)
            continue
        if dist <= WEB_ZOMBIE_BITE_RANGE:
            # Attack rhythm: lunge (~0.36 s, pose plays out) -> short recovery
            # shuffle (small steps away/sideways) -> creep back toward the
            # player until the bite cooldown expires -> bite again. Without
            # this the zombie re-fired the lunge every tick and looked like it
            # was vibrating in place.
            since_bite = now_mono - (z.last_bite or 0.0)
            if since_bite < WEB_ZOMBIE_ATK_MS / 1000.0:
                continue  # let the lunge pose finish before anything moves
            # SKITTISH gets a longer retreat window (hit-and-run rhythm).
            recover_s = max(WEB_ZOMBIE_RECOVER_S, getattr(z, "recover_until", 0.0) - now_mono) if style == "skittish" else WEB_ZOMBIE_RECOVER_S
            if since_bite < recover_s:
                _web_recovery_drift(z, collision, step, now_mono, result)
                continue
            if since_bite < z.web_cooldown:
                _web_creep(z, dx, dy, length, collision, step, now_mono, result)
                continue
            z.facing = _web_facing(dx, dy)
            _web_set_anim(z, "atk", now_mono)
            z.last_bite = now_mono  # rhythm clock ticks even if damage is blocked
            before = target.hp
            # SWARM (bat): dive-bomb burst — each bite flings the bat a step
            # PAST the target so it circles around for the next pass.
            dealt = z.damage
            if style == "swarm":
                dealt = max(1, round(z.damage * 0.8))  # light pecks, fast
            # MELEE damage_mult (skeleton2 / hobgoblin: heavy slow hitters).
            dealt = max(1, round(dealt * float(beh.get("damage_mult", 1.0))))
            target.hp = max(0, target.hp - dealt)
            if target.hp != before:
                # Out-of-combat regen clock: any HP loss re-arms the 5 s
                # wait (web pack — same rule as the Discord pack).
                target.last_damaged_at = now_mono
                target.regen_bank = 0.0
                result.changed = True
                result.damaged_player_ids.add(target.user_id)
                # Hitsplat feed: floating damage number on the victim.
                feed = getattr(state, "recent_damage", None)
                if feed is not None:
                    feed.append((now_wall, target.user_id, dealt, "zombie"))
                    del feed[:-40]
                # Pick the recovery drift: mostly AWAY from the target with a
                # random sideways component, so packs break apart instead of
                # shuffling in lockstep.
                rec_len = max(1e-6, length)
                # SKITTISH (rat/goblin): hit-and-run — a LONG retreat drift
                # straight away from the player after every successful bite.
                if style == "skittish":
                    z.recover_dx = -dx / rec_len
                    z.recover_dy = -dy / rec_len
                    z.recover_until = now_mono + 1.2  # longer retreat window
                else:
                    z.recover_until = 0.0
                if style == "swarm":
                    # Dive past the target: keep the velocity, flip the
                    # component along the approach to overshoot.
                    z.recover_dx = (dx / rec_len) * 0.4 + random.uniform(-0.8, 0.8)
                    z.recover_dy = (dy / rec_len) * 0.4 + random.uniform(-0.8, 0.8)
                    z.recover_until = 0.0
                elif style != "skittish":
                    z.recover_dx = (-dx / rec_len) * 0.7 + random.uniform(-0.6, 0.6)
                    z.recover_dy = (-dy / rec_len) * 0.7 + random.uniform(-0.6, 0.6)
                rl = _math.hypot(z.recover_dx, z.recover_dy)
                if rl > 1e-6:
                    z.recover_dx /= rl
                    z.recover_dy /= rl
                else:
                    z.recover_dx, z.recover_dy = 0.0, 0.0
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
        # Hunters are always fast; ambush pounce bursts faster; regular kinds
        # use their MOB_KINDS speed.
        speed = WEB_ZOMBIE_HUNTER_SPEED if z.hunter else z.web_speed
        if style == "ambush" and getattr(z, "ambush_armed", False) and now_mono < getattr(z, "ambush_until", 0.0):
            speed = max(speed, z.web_speed * 1.8)  # pounce!
        # SWARM (bat): erratic weaving — sinusoidal sideways offset while
        # closing in so it flies in loops instead of a straight beeline.
        ux, uy = dx / length, dy / length
        if style == "swarm":
            import math as _m2
            wob = _m2.sin(now_mono * 6.0 + hash(z.zombie_id) % 7)
            ux, uy = ux - uy * wob * 0.6, uy + ux * wob * 0.6
            wl = _m2.hypot(ux, uy) or 1.0
            ux, uy = ux / wl, uy / wl
        _web_chase_step(z, ux, uy, collision, step, now_mono, result, speed=speed)
    if result.changed:
        result.visible_changed = True
    return result


def _web_chase_step(
    z: Zombie, ux: float, uy: float, collision,
    step: float, now_mono: float, result: "ZombieTurnResult",
    speed: float,
) -> None:
    """One float chase step along a UNIT vector with collision + the void
    guard (shared by the melee chase and the ranged caster drift)."""
    import math as _math

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
    # VOID GUARD: reject any step that leaves the map rect — collision
    # alone let mobs hug the outer wall band and drift into the void.
    mw = getattr(getattr(collision, "map_data", None), "width", 0) or 0
    mh = getattr(getattr(collision, "map_data", None), "height", 0) or 0
    if mw and mh and not _in_bounds_float(nx_f, ny_f, mw, mh):
        _web_set_anim(z, "idle", now_mono)
        return
    if (nx_f, ny_f) != (z.x_f, z.y_f):
        z.x_f, z.y_f = nx_f, ny_f
        z.sync_int_from_float()
        z.facing = _web_facing(ux, uy)
        _web_set_anim(z, "walk", now_mono)
        result.changed = True
    else:
        _web_set_anim(z, "idle", now_mono)


def _web_recovery_drift(
    z: Zombie, collision, step: float, now_mono: float,
    result: "ZombieTurnResult",
) -> None:
    """Post-bite recovery shuffle: 1-2 hesitant steps away/sideways."""
    dx = z.recover_dx * WEB_ZOMBIE_RECOVER_SPEED * step
    dy = z.recover_dy * WEB_ZOMBIE_RECOVER_SPEED * step
    _web_slide(z, dx, dy, collision, now_mono, result)


def _web_creep(
    z: Zombie, dx: float, dy: float, length: float, collision,
    step: float, now_mono: float, result: "ZombieTurnResult",
) -> None:
    """Cooldown remainder: creep slowly back toward the target (pacing) —
    the zombie looks alive between bites instead of frozen at range."""
    if length <= 1e-6:
        _web_set_anim(z, "idle", now_mono)
        return
    speed = WEB_ZOMBIE_WALK_SPEED * 0.45
    ux, uy = dx / length, dy / length
    _web_slide(z, ux * speed * step, uy * speed * step, collision, now_mono, result)


def _web_slide(
    z: Zombie, dx: float, dy: float, collision,
    now_mono: float, result: "ZombieTurnResult",
) -> None:
    """Move by (dx, dy) float offset with collision; walk anim on success,
    idle breathe when blocked."""
    can_float = getattr(collision, "can_move_float", None)
    if callable(can_float):
        try:
            nx_f, ny_f = can_float(z.x_f, z.y_f, dx, dy)
        except Exception:
            nx_f, ny_f = z.x_f + dx, z.y_f + dy
    else:
        nx_f, ny_f = z.x_f + dx, z.y_f + dy
    # VOID GUARD: the recovery/creep drift respects the map rect too.
    mw = getattr(getattr(collision, "map_data", None), "width", 0) or 0
    mh = getattr(getattr(collision, "map_data", None), "height", 0) or 0
    if mw and mh and not _in_bounds_float(nx_f, ny_f, mw, mh):
        _web_set_anim(z, "idle", now_mono)
        return
    if (nx_f, ny_f) != (z.x_f, z.y_f):
        z.x_f, z.y_f = nx_f, ny_f
        z.sync_int_from_float()
        z.facing = _web_facing(dx, dy)
        _web_set_anim(z, "walk", now_mono)
        result.changed = True
    else:
        # Blocked: just breathe on the idle frame instead of pressing into
        # the wall/player.
        _web_set_anim(z, "idle", now_mono)
