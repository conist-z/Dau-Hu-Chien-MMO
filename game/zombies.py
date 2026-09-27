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
    # HP x5 (user 25/09: "nâng máu tổng hiện tại của tụi quái lên gấp 5 lần")
    # applied to the HOSTILE roster; wildlife keeps its tuned numbers.
    "zombie":   dict(hp=200, dmg=10, speed=2.2, cooldown=1.8, weight=38),
    "skeleton": dict(hp=280, dmg=11, speed=2.0, cooldown=2.2, weight=22),   # tanky, slow (lvl14/HP140)
    "spider":   dict(hp=220, dmg=10, speed=2.2, cooldown=1.8, weight=16),   # lvl47/HP650 ~ zombie-ish
    "slime":    dict(hp=250, dmg=9,  speed=2.2, cooldown=1.8, weight=12),   # lvl48/HP694 tanky
    "bat":      dict(hp=100, dmg=6,  speed=2.8, cooldown=1.6, weight=8),    # lvl4/HP65 fast swarm
    "rat":      dict(hp=60,  dmg=4,  speed=2.6, cooldown=2.0, weight=4),    # lvl1/HP20 pest
    # (user 28/09: rare bruisers + the ranged caster join the shared night
    # pool — the weight-0 rows only spawned through map profiles before.)
    # ---- cave/forest packs (game/mob_profiles.py routes them per map) ----
    # Stats scaled from Kaetram _all_mobs.json relative to the zombie row.
    "skeleton2": dict(hp=280, dmg=14, speed=2.0, cooldown=2.6, weight=4),   # lvl30/HP375 tanky bruiser
    "spectre":   dict(hp=135, dmg=9,  speed=1.8, cooldown=2.4, weight=3),   # lvl32/HP270 ghost, RANGED
    "goblin":    dict(hp=45,  dmg=4,  speed=2.4, cooldown=2.0, weight=3),   # lvl7/HP90 weak nuisance
    "hobgoblin": dict(hp=130, dmg=13, speed=2.2, cooldown=1.8, weight=4),   # lvl42/HP260 aggressive bruiser
    # ---- daytime wildlife (Minifolks Forest Animals; ambient pool, no
    # hostile-weight 0 means they only spawn via the ambient roster) -------
    # Prey flee before fighting, so their dmg only matters when cornered.
    "bunny":  dict(hp=8,  dmg=2,  speed=2.4, cooldown=2.0, weight=0),  # flee mult 1.35 -> ~3.2 real
    "deer":   dict(hp=22, dmg=4,  speed=2.3, cooldown=2.2, weight=0),  # big meat sack
    "deer2":  dict(hp=30, dmg=6,  speed=2.3, cooldown=2.0, weight=0),  # rare buck
    "bird":   dict(hp=5,  dmg=1,  speed=2.6, cooldown=2.0, weight=0),  # flies away (unfightable)
    "boar":   dict(hp=26, dmg=9,  speed=2.0, cooldown=1.8, weight=0),  # neutral, gored back
    "bear":   dict(hp=70, dmg=16, speed=2.0, cooldown=2.4, weight=0),  # elite — hits HARD
    "fox":    dict(hp=14, dmg=5,  speed=2.5, cooldown=1.8, weight=0),  # skittish scavenger
    "wolf":   dict(hp=24, dmg=10, speed=2.4, cooldown=1.6, weight=0),  # hostile pack hunter
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
WEB_ZOMBIE_VISION_RADIUS = 15.0  # 6.0 x2.5 (user 28/09): mob phát hiện xa hơn
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
ZOMBIE_VISION_RADIUS = 15  # 6 x2.5 (user 28/09): mob phát hiện xa hơn
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
# A fleeing bird that has kept panicking this long despawns ("bay mất") —
# it flew out of the area instead of staying punchable forever.
_PREY_BIRD_FLEE_DESPAWN_S = 4.0

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
    # ---- wildlife: the hunting economy (meat + hide + rare coin) ----------
    "bunny": (
        ("raw_meat", 0.6, 1),
        ("hide", 0.25, 1),
    ),
    "deer": (
        ("raw_meat", 1.0, 2),     # the reliable hunter's prize
        ("hide", 0.6, 1),
    ),
    "deer2": (
        ("raw_meat", 1.0, 2),
        ("hide", 0.8, 1),
        ("coin", 0.4, 2),         # trophy buck
    ),
    "bird": (
        ("feather", 0.7, 1),      # barely worth the arrow
    ),
    "boar": (
        ("raw_meat", 1.0, 2),
        ("hide", 0.7, 1),         # thick skin
    ),
    "bear": (
        ("raw_meat", 1.0, 3),     # most meat in the game
        ("hide", 1.0, 2),
        ("coin", 0.6, 3),
    ),
    "fox": (
        ("raw_meat", 0.5, 1),
        ("hide", 0.6, 1),         # prized pelt
        ("coin", 0.3, 1),
    ),
    "wolf": (
        ("raw_meat", 0.8, 1),
        ("hide", 0.5, 1),
        ("coin", 0.3, 1),
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
    attack_recover_until: float = 0.0# Per-kind web stats (filled by web_spawn_one from MOB_KINDS): chase
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
    # ---- ambient wildlife flag (game/mob_profiles ambient pool) ------------
    # True = daytime animal: separate pool cap, prey/neutral behaviors, and
    # it never counts toward the night-mob cap.
    ambient: bool = False
    # NEUTRAL animals: monotonic() until which the animal fights back after
    # being hit (0.0 = calm, never attacks unprovoked).
    aggro_until: float = 0.0
    # AMBIENT wander heading persistence: the animal KEEPS one direction for
    # 1.5-4 s (wander_until) instead of re-rolling a move/stand coin every
    # tick — a per-tick coin flip made the anim flap walk<->idle ~10x/s and
    # the client reset to frame 0 each change (sprites looked frozen/idle
    # while actually moving).
    wander_until: float = 0.0
    wander_walking: bool = False
    # ---- ANIMAL AI v2 (user 28/09: vision x2.5 + per-species behavior) -----
    # Panic clock: monotonic() until which a fleeing animal KEEPS running
    # even if the player already left flee_vision (the calm-overrun fix —
    # animals used to stop dead the instant they crossed the vision rim).
    # Panic decays past panic_slow_at into a walk, then stops.
    panic_until: float = 0.0
    # Alert phase: monotonic() until which a startled animal STANDS facing
    # the threat (theHunter parity: the player must SEE the tell before the
    # flight). Atk anim row doubles as the bark/ alarm pose.
    alert_until: float = 0.0
    # Bunny zigzag / bird scatter / boar charge clocks + heading memory.
    zigzag_until: float = 0.0
    zigzag_dx: float = 0.0
    zigzag_dy: float = 0.0
    # Boar/bear charge: monotonic deadline + locked unit vector.
    charge_until: float = 0.0
    charge_dx: float = 0.0
    charge_dy: float = 0.0
    # Boar warning stage: True after the first hit until aggro lapses — the
    # SECOND hit inside the warning window escalates to a real charge.
    boar_warned: bool = False
    # Bear endurance: position (x, y) where aggro began + hit memory for the
    # two-stage rage. Back-turn speed boost deadline.
    aggro_origin: Optional[tuple] = None
    bear_hit_at: float = 0.0
    backturn_until: float = 0.0
    # Wolf pack: bite queue relay (monotonic slot) + howl broadcast.
    bite_slot_at: float = 0.0
    howled_at: float = 0.0
    # Fox: drops it is currently scavenging toward ("x,y" key).
    scavenge_target: Optional[str] = None
    # Side-view flip accumulator (see _side_animal_facing).
    _side_flip_acc: float = 0.0

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


def next_animal_id(state) -> str:
    """Ambient wildlife share the web store under their own id prefix."""
    state.web_animal_seq = getattr(state, "web_animal_seq", 0) + 1
    return f"animal-{state.web_animal_seq}"


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


# ---- STEALTH APPROACH (user 28/09: "player tiếp cận mà nó không bị giật") --
# SPRINTING = loud (full flee_vision); WALKING or STANDING = quiet (the
# animal only notices you at sneak_vision_frac of it). Mirrors real flight-
# initiation behavior and theHunter's movement-speed detection rules.
WEB_SNEAK_VISION_FRAC = 0.30


def _player_loudness(state, player) -> str:
    """"loud" when this web player is SPRINTING right now, else "quiet"."""
    rt = getattr(state, "_rt_ref", None)
    sess = getattr(rt, "web_sessions", {}).get(getattr(player, "user_id", 0)) if rt else None
    running = getattr(sess, "running", False)
    moving = bool(getattr(sess, "dx", 0.0) or getattr(sess, "dy", 0.0))
    return "loud" if (running and moving) else "quiet"


def _effective_flee_vision(state, player, base_vision: float) -> float:
    """Vision radius for ONE player: sprinters are seen from full range,
    quiet movers only from the sneak fraction."""
    if _player_loudness(state, player) == "loud":
        return base_vision
    return base_vision * WEB_SNEAK_VISION_FRAC


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


def _build_art_ok_set(map_data) -> Optional[set]:
    """Set of (x, y) cells that carry ART (any non-ground tile layer with a
    gid whose sheet piece is not fully transparent). Cells outside the set
    are void — nothing is drawn there, so nothing may spawn there.

    Cheap structural test first (a gid at the cell), falling back to the
    alpha map only when the loader already computed one (cave maps: it did).
    Returns None when the map has no tile layers at all (never block spawn)."""
    md = map_data
    if not getattr(md, "tile_layers", None):
        return None
    # Cells with ANY tile gid on ANY layer are candidates; the loader's
    # collision grid already excluded fully-opaque walls, and fully
    # transparent pieces were carved away where needed. A gid presence test
    # is the right granularity here: ground-only cells keep mobs visible.
    ok: set = set()
    for _name, grid in md.tile_layers:
        for gy, row in enumerate(grid):
            for gx, gid in enumerate(row):
                if gid:
                    ok.add((gx, gy))
    return ok


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
    # VOID GUARD ("quái spawn ra ngoài void"): walkable alone is not enough
    # on the carved Ekonia maps — the invisible-blocker carve opens no-art
    # cells (and the outer rim is void). A spawn tile must also carry ART:
    # at least one non-ground layer with an opaque pixel at the cell. We use
    # the same truth the renderer draws; cells with nothing drawn are void.
    _art_ok = _build_art_ok_set(collision.map_data)
    candidates: List[Tuple[int, int]] = []

    for y in range(height):
        for x in range(width):
            if (x, y) in occupied or not collision.is_walkable(x, y):
                continue
            if _art_ok is not None and (x, y) not in _art_ok:
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
    # ring. Still random, still never inside a viewport — and still never on
    # a no-art void cell (the art filter above applies here too).
    if not candidates:
        candidates = [
            (x, y) for y in range(height) for x in range(width)
            if (x, y) not in occupied and collision.is_walkable(x, y)
            and (_art_ok is None or (x, y) in _art_ok)
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


# ---- side-view animal facing (user 28/09: "quay mặt qua trái rồi chạy lên
# phải — khó chịu") ----------------------------------------------------------
# Minifolks animals are SIDE-VIEW art with ONE facing row: the sprite can
# only look left or right. Deriving N/S/E/W per slide and re-deriving it
# every tick made the head flip left<->right while the body kept gliding
# diagonally. Rules that fix it:
#   1. E/W only — vertical movement keeps the current face.
#   2. HYSTERESIS: a flip requires |dx| accumulated past SIDE_FLIP_THRESHOLD
#      in the OPPOSITE direction (a diagonal stroll with |dx| ≈ 0 no longer
#      strobes the face every tick).
#   3. No-face zones (blocked/idle) never re-face.
SIDE_FLIP_THRESHOLD = 0.25  # tiles of travel opposite the current face


def _side_animal_facing(z, dx: float, dy: float) -> str:
    """E/W facing for a side-view animal with anti-strobe hysteresis.

    ``dx/dy`` is the CURRENT movement step (not a target vector). Returns
    the possibly-unchanged z.facing (always "E" or "W" once an animal has
    moved at all — spawn default "S" is fixed on the first step).
    """
    cur = z.facing if z.facing in ("E", "W") else ("E" if dx >= 0 else "W")
    if abs(dx) < 1e-6:
        return cur  # pure vertical / no horizontal motion: keep the face
    want = "E" if dx > 0 else "W"
    if want == cur:
        return cur
    # Opposite direction: flip only after the accumulated counter-travel
    # proves intent (resets on every agreeing step — jitter never flips).
    trav = abs(dx)
    z._side_flip_acc = (getattr(z, "_side_flip_acc", 0.0) + trav)
    if z._side_flip_acc >= SIDE_FLIP_THRESHOLD:
        z._side_flip_acc = 0.0
        return want
    return cur


def _web_facing_towards(z: Zombie, dx: float, dy: float) -> str:
    """Facing when the mob is TARGETING a player (chase or flee).

    `_web_facing` derives facing from the ACTUAL movement vector — after a
    collision slide along a wall it can point sideways while the mob still
    bears down on the player, and a vertically-fleeding prey (single-facing
    side-view sheet!) would face neither left nor right at all. When a
    target is involved the face must track the TARGET: horizontal sign of
    (dx, dy) wins; when |dx|≈|dy| keep the current horizontal bias so the
    sprite doesn't flicker E/W on a diagonal."""
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return z.facing
    if abs(dx) > 1e-6:
        return "E" if dx > 0 else "W"
    # Pure vertical: keep the last horizontal facing (W/E); default W only
    # when the mob never had a horizontal bearing.
    return z.facing if z.facing in ("E", "W", "NE", "NW", "SE", "SW") else "W"


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
        # VOID GUARD: also require ART at the tile (same rule as the
        # spawner's _spawn_position) — the carve opens walkable no-art
        # cells around the playable region; mobs must never pop there.
        _art_ok = _build_art_ok_set(getattr(collision, "map_data", None))
        if _art_ok is not None and (tx, ty) not in _art_ok:
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


def spawn_animal_one(state, collision, players: List[object], rng: random.Random,
                     kind: Optional[str] = None) -> Optional[Zombie]:
    """Spawn ONE ambient animal (same ring rules as the hostile spawner).

    kind=None rolls from the map's ambient roster. Animals share the web
    store with ambient=True — the hostile cap never counts them.
    """
    import math as _math

    from game.mob_profiles import (
        behavior_of,
        roll_ambient_kind_for,
    )

    alive = [p for p in players if getattr(p, "alive", True)]
    if not alive:
        return None
    anchor = rng.choice(alive)
    w = getattr(getattr(collision, "map_data", None), "width", 0) or 0
    h = getattr(getattr(collision, "map_data", None), "height", 0) or 0
    if not w or not h:
        return None
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
        if not _in_bounds_float(tx + 0.5, ty + 0.5, w, h):
            continue
        if not walkable:
            continue
        _art_ok = _build_art_ok_set(getattr(collision, "map_data", None))
        if _art_ok is not None and (tx, ty) not in _art_ok:
            continue
        z = Zombie(next_animal_id(state), tx, ty)
        z.kind = kind or roll_ambient_kind_for(
            getattr(getattr(collision, "map_data", None), "map_id", "bigmap"), rng
        )
        stats = mob_stats(z.kind)
        z.hp = z.max_hp = stats["hp"]
        z.damage = stats["dmg"]
        z.web_speed = stats["speed"]
        z.web_cooldown = stats["cooldown"]
        z.x_f = float(tx) + 0.5
        z.y_f = float(ty) + 0.5
        z.ambient = True
        z.hunter = False
        # Neutral animals spawn calm; aggro is armed by taking a hit.
        z.aggro_until = 0.0
        _ = behavior_of(z.kind)  # validate the kind has a behavior row
        # Side-view sheet: start facing right ("S" would show the right row
        # anyway — face the art's native direction from birth).
        z.facing = "E"
        z.anim = "walk"
        z.anim_t = time.monotonic()
        _add_web_zombie(state, z)
        return z
    return None


def _web_ambient_upkeep(state, collision, players, rng, map_id: str,
                        second_of_day: int) -> list:
    """Spawn/despawn upkeep for the AMBIENT pool — runs inside web_tick.

    Returns the freshly spawned animals. Despawn radius mirrors the hostile
    pack so wildlife never litters abandoned corners of the map.
    """
    from game.mob_profiles import ambient_max_for, ambient_spawn_chance

    cap = ambient_max_for(map_id, second_of_day)
    if cap <= 0:
        return []
    animals = [z for z in _web_zombies(state) if getattr(z, "ambient", False)]
    # Animals farther than the hostile despawn distance from every player
    # wander off (keeps the population near the action).
    for z in animals:
        nearest = _web_nearest(z, players)
        if nearest is not None and _web_dist(z, nearest) > WEB_ZOMBIE_DESPAWN_DIST:
            remove_web_zombie(state, z.zombie_id)
    animals = [z for z in _web_zombies(state) if getattr(z, "ambient", False)]
    spawned = []
    if len(animals) < cap and (
        not animals or rng.random() < ambient_spawn_chance(map_id)
    ):
        z = spawn_animal_one(state, collision, players, rng)
        if z is not None:
            spawned.append(z)
    return spawned


def _web_ambient_move(state, collision, players, now_mono: float, dt: float,
                      result: "ZombieTurnResult") -> None:
    """Daytime HALF of web_tick: animals keep moving when the hostile gate is
    off. A cond copy of the prey/neutral steering from the night loop — kept
    separate so the hot night path never pays an extra style branch."""
    import math as _math

    rng = state.__dict__.get("_ambient_rng")
    if rng is None:
        import random as _random

        rng = _random.Random()
        state._ambient_rng = rng
    step = max(0.0, min(0.25, dt))
    from game.mob_profiles import behavior_of

    for z in list(_web_zombies(state)):
        if not getattr(z, "ambient", False):
            continue
        target = _web_nearest(z, players)
        if target is None:
            _web_set_anim(z, "idle", now_mono)
            continue
        dist = _web_dist(z, target)
        beh = behavior_of(z.kind)
        style = beh.get("style", "melee")
        dx = target.x_f - z.x_f
        dy = target.y_f - z.y_f
        length = _math.hypot(dx, dy) or 1e-6
        if style == "prey":
            flee_vision = float(beh.get("flee_vision", 4.5))
            if dist <= flee_vision:
                _web_chase_step(
                    z, -dx / length, -dy / length, collision, step, now_mono,
                    result,
                    speed=z.web_speed * float(beh.get("flee_mult", 1.3)),
                    towards=(-dx, -dy),  # fleeing: face AWAY from the player
                )
                if beh.get("fly_away"):
                    if z.aggro_until <= now_mono:
                        z.aggro_until = now_mono
                    if now_mono - z.aggro_until >= _PREY_BIRD_FLEE_DESPAWN_S:
                        removed = remove_web_zombie(state, z.zombie_id)
                        if removed is not None:
                            result.removed.append(removed)
                            result.changed = True
            else:
                z.aggro_until = 0.0
                _web_ambient_wander(z, rng, collision, step, now_mono, result, state=state)
            continue
        # neutral / others: calm wander (aggro never persists into the day
        # gate for these because the day gate only runs this function).
        _web_ambient_wander(z, rng, collision, step, now_mono, result, state=state)


def _web_ambient_wander(z, rng, collision, step, now_mono: float,
                        result: "ZombieTurnResult", state=None) -> None:
    """Calm wander with a PERSISTENT heading + FLOCK COHESION (user 28/09
    "tương tác bầy đàn"): same-kind animals within flock_r blend their
    heading toward the group's average (Reynolds alignment/cohesion, light
    touch) so deer cluster into loose herds and birds into flocks instead
    of scattering like random NPCs. Solo animals wander exactly as before.
    Walk one direction 1.5-4 s at ~35% speed, then stand 1-3 s, then pick
    again."""
    if now_mono >= getattr(z, "wander_until", 0.0):
        import math as _math

        if rng.random() < 0.6:  # 60% stroll, 40% graze
            ang = rng.uniform(0, 2 * _math.pi)
            z.recover_dx, z.recover_dy = _math.cos(ang), _math.sin(ang)
            # FLOCK BIAS: blend the fresh heading toward same-kind neighbors
            # within flock_r (cheap local pass — animal counts are tiny).
            try:
                from game.mob_profiles import behavior_of as _beh

                beh = _beh(z.kind)
                flock_r = float(beh.get("flock_r", 0.0))
                flock_w = float(beh.get("flock_w", 0.0))
            except Exception:  # noqa: BLE001 — flocking must never break the tick
                flock_r = flock_w = 0.0
            if flock_r > 0.0 and flock_w > 0.0 and state is not None:
                mates = [
                    o for o in _web_zombies(state)
                    if o is not z and getattr(o, "ambient", False)
                    and o.kind == z.kind and _web_dist(o, z) <= flock_r
                ]
                if mates:
                    # Cohesion: toward the centroid; Alignment: average of
                    # the mates' current headings (unit-weighted).
                    cx = sum(o.x_f for o in mates) / len(mates)
                    cy = sum(o.y_f for o in mates) / len(mates)
                    hx = sum(o.recover_dx for o in mates) / len(mates)
                    hy = sum(o.recover_dy for o in mates) / len(mates)
                    coh = _math.hypot(cx - z.x_f, cy - z.y_f)
                    if coh > 1e-3:
                        ux, uy = (cx - z.x_f) / coh, (cy - z.y_f) / coh
                    else:
                        ux, uy = z.recover_dx, z.recover_dy
                    hl = _math.hypot(hx, hy)
                    if hl > 1e-3:
                        ux += (hx / hl) * 0.6
                        uy += (hy / hl) * 0.6
                    ul = _math.hypot(ux, uy)
                    if ul > 1e-3:
                        ux, uy = ux / ul, uy / ul
                        z.recover_dx = (
                            z.recover_dx * (1.0 - flock_w) + ux * flock_w
                        )
                        z.recover_dy = (
                            z.recover_dy * (1.0 - flock_w) + uy * flock_w
                        )
                        # Re-normalize the blended heading.
                        bl = _math.hypot(z.recover_dx, z.recover_dy) or 1.0
                        z.recover_dx /= bl
                        z.recover_dy /= bl
            z.wander_walking = True
            z.wander_until = now_mono + rng.uniform(1.5, 4.0)
        else:
            z.wander_walking = False
            z.wander_until = now_mono + rng.uniform(1.0, 3.0)
    if getattr(z, "wander_walking", False):
        _web_slide(
            z,
            z.recover_dx * z.web_speed * 0.35 * step,
            z.recover_dy * z.web_speed * 0.35 * step,
            collision, now_mono, result,
        )
    else:
        _web_set_anim(z, "idle", now_mono)


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
        # Night gate OFF: the HOSTILE pack despawns — but ambient wildlife
        # is independent (day animals + day/night wolves keep living).
        removed_any = False
        for z in list(_web_zombies(state)):
            if getattr(z, "ambient", False):
                continue
            removed = remove_web_zombie(state, z.zombie_id)
            if removed is not None:
                result.removed.append(removed)
                result.changed = True
                removed_any = True
        if players:
            # Day upkeep: animals keep spawning/moving on their own rhythm.
            from game.mob_profiles import ambient_max_for

            map_id = getattr(getattr(collision, "map_data", None), "map_id", "bigmap")
            from rendering.daynight import ingame_seconds as _ingame_s2

            if ambient_max_for(map_id, _ingame_s2()) > 0:
                born = _web_ambient_upkeep(
                    state, collision, players, rng, map_id, _ingame_s2()
                )
                if born:
                    result.spawned.extend(born)
                    result.changed = True
            else:
                # NO-WILDLIFE maps (trade lobby + monter-trade interior):
                # the cap is 0, so also sweep out any animals already inside
                # (e.g. carried over from the roster fallback before this
                # rule, or test setups).
                removed_animals = False
                for z in list(_web_zombies(state)):
                    if not getattr(z, "ambient", False):
                        continue
                    removed = remove_web_zombie(state, z.zombie_id)
                    if removed is not None:
                        result.removed.append(removed)
                        result.changed = True
                        removed_animals = True
                if removed_animals:
                    result.visible_changed = True
        _web_ambient_move(state, collision, players, now_mono, dt, result)
        result.visible_changed = removed_any or bool(result.spawned)
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

    # ---- AMBIENT wildlife upkeep (daytime animals, own pool cap) ---------
    # The hostile night cap above counts ONLY ambient=False mobs; the animal
    # pool follows its own day/night rhythm (game/mob_profiles.py) and runs
    # in BOTH halves of this tick (day keeps the hostile gate out, animals
    # still spawn/move).
    from game.mob_profiles import ambient_max_for

    map_id = getattr(getattr(collision, "map_data", None), "map_id", "bigmap")
    from rendering.daynight import ingame_seconds as _ingame_s

    if ambient_max_for(map_id, _ingame_s()) > 0:
        born = _web_ambient_upkeep(state, collision, players, rng, map_id, _ingame_s())
        if born:
            result.spawned.extend(born)
            result.changed = True
            result.visible_changed = True
            zombies = _web_zombies(state)
    else:
        # NO-WILDLIFE maps (night half): trade zones also sweep animals
        # already inside — same rule as the day half above.
        removed_any = False
        for z in list(_web_zombies(state)):
            if not getattr(z, "ambient", False):
                continue
            removed = remove_web_zombie(state, z.zombie_id)
            if removed is not None:
                result.removed.append(removed)
                result.changed = True
                removed_any = True
        if removed_any:
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

        # ---- PREY (bunny/deer/bird): NEVER attacks — ALERT stand, then a
        # panic flight that OVERRUNS the vision rim (the old version stopped
        # dead the instant dist > flee_vision: the "chạy được đoạn dừng" look).
        # VISION x2.5 (user 28/09) + per-species flavors below.
        # STEALTH APPROACH: a QUIET player (walking/standing) is only seen at
        # WEB_SNEAK_VISION_FRAC of the flee radius — sprinting is what makes
        # the wildlife bolt from across the map.
        if style == "prey":
            base_vision = float(beh.get("flee_vision", 11.25))
            flee_vision = _effective_flee_vision(state, target, base_vision)
            alert_s = float(beh.get("alert_s", 0.8))
            overrun = float(beh.get("panic_overrun", 1.4))
            panicking = now_mono < z.panic_until
            triggered = dist <= flee_vision
            if triggered and not panicking and z.alert_until <= 0.0:
                # ALERT phase (theHunter parity): stand + face the player for
                # alert_s (atk row doubles as the bark/startle pose), and
                # SCARE nearby same-kind animals (herd/bird contagion).
                z.alert_until = now_mono + alert_s
                z.facing = _side_animal_facing(z, dx, dy)
                _web_set_anim(z, "atk", now_mono)
                herd_r = float(beh.get("herd_panic_r", 0.0))
                scatter_r = float(beh.get("scatter_r", 0.0))
                contagion_r = herd_r or scatter_r
                if contagion_r > 0.0:
                    for o in _web_zombies(state):
                        if o is z or not getattr(o, "ambient", False):
                            continue
                        if o.kind == z.kind and _web_dist(o, target) <= contagion_r:
                            o.alert_until = now_mono + max(0.3, alert_s * 0.6)
                            o.panic_until = now_mono + 2.5
                continue  # frozen stare this tick
            if z.alert_until > now_mono:
                continue  # mid-stare
            if triggered or panicking:
                # PANIC clock: re-armed while the player stays in vision;
                # keeps the run alive past the rim, then decays to a walk.
                if triggered:
                    z.panic_until = now_mono + 1.2
                if z.alert_until > 0.0:
                    z.alert_until = 0.0
                speed = z.web_speed * float(beh.get("flee_mult", 1.3))
                # BUNNY burst (O1c): first burst_s seconds sprint faster.
                burst_s = float(beh.get("burst_s", 0.0))
                if burst_s > 0.0 and z.panic_until > now_mono:
                    since = (z.panic_until - now_mono)
                    # panic_until re-arms at +1.2 each in-vision tick, so the
                    # burst reads as the fresh-start sprint.
                    if since > 1.2 - min(1.2, burst_s):
                        speed *= float(beh.get("burst_mult", 1.5))
                # BUNNY zigzag (O1b): re-roll a ±zigzag_deg heading every
                # zigzag_every_s while fleeing.
                if beh.get("zigzag") and now_mono >= z.zigzag_until:
                    import math as _mz
                    base = _mz.atan2(-dy, -dx)
                    spread = _mz.radians(float(beh.get("zigzag_deg", 40)))
                    ang = base + rng.uniform(-spread, spread)
                    z.zigzag_dx, z.zigzag_dy = _mz.cos(ang), _mz.sin(ang)
                    z.zigzag_until = now_mono + float(beh.get("zigzag_every_s", 1.0))
                if beh.get("zigzag") and (z.zigzag_dx or z.zigzag_dy):
                    _web_chase_step(
                        z, z.zigzag_dx, z.zigzag_dy, collision, step, now_mono,
                        result, speed=speed, towards=(-dx, -dy),
                    )
                else:
                    if length > 1e-6:
                        ux, uy = -dx / length, -dy / length
                        _web_chase_step(
                            z, ux, uy, collision, step, now_mono, result,
                            speed=speed, towards=(-dx, -dy),
                        )
                # BIRD hop-circle (O3b): a 0.6 s circular flare before the
                # straight flight (orbit the threat point).
                hop_s = float(beh.get("hop_circle_s", 0.0))
                if hop_s > 0.0 and z.alert_until == 0.0 and rng.random() < 0.02:
                    import math as _mc
                    ang = _mc.atan2(dy, dx) + _mc.pi / 2
                    _web_slide(
                        z, _mc.cos(ang) * z.web_speed * 1.4 * step,
                        _mc.sin(ang) * z.web_speed * 1.4 * step,
                        collision, now_mono, result,
                    )
                # BIRD fly-away REWORK (O3c): despawn only while the player
                # keeps CLOSING IN (within fly_away_close_r mid-flight).
                if beh.get("fly_away"):
                    close_r = float(beh.get("fly_away_close_r", 3.0))
                    if dist <= close_r:
                        z.aggro_until = (z.aggro_until or now_mono) if (
                            z.aggro_until > now_mono
                        ) else now_mono
                        if now_mono - z.aggro_until >= _PREY_BIRD_FLEE_DESPAWN_S:
                            removed = remove_web_zombie(state, z.zombie_id)
                            if removed is not None:
                                result.removed.append(removed)
                                result.changed = True
                    else:
                        z.aggro_until = 0.0
                continue
            # Calm again: clear clocks, wander on.
            z.panic_until = 0.0
            z.aggro_until = 0.0
            _web_ambient_wander(z, rng, collision, step, now_mono, result, state=state)
            continue

        # ---- NEUTRAL (boar/bear): wanders calmly; fights back ONLY while
        # aggro (armed by the player's attack, see rules.py).
        if style == "neutral":
            if now_mono >= getattr(z, "aggro_until", 0.0):
                z.boar_warned = False
                z.aggro_origin = None
                _web_ambient_wander(z, rng, collision, step, now_mono, result, state=state)
                continue
            # O4c/O5c DEFENSIVE LEASH: pursuit is capped around the spot
            # where the aggro began (Vintage Story boar / bear endurance).
            leash = float(beh.get("charge_leash", 0.0)) or float(beh.get("aggro_leash", 0.0))
            if leash > 0.0:
                if z.aggro_origin is None:
                    z.aggro_origin = (z.x_f, z.y_f)
                ox, oy = z.aggro_origin
                if _math.hypot(z.x_f - ox, z.y_f - oy) > leash:
                    # Broke the leash: drop aggro entirely (boar) or walk it
                    # off (bear) — no map-spanning chases.
                    z.aggro_until = 0.0
                    z.aggro_origin = None
                    _web_ambient_wander(z, rng, collision, step, now_mono, result, state=state)
                    continue
            # BOAR O4a warn-charge (user 28/09: "con lợn đâu thấy có gì khác
            # đâu" — the old version's warning was a stand-still blip; now):
            #   hit 1 -> a short PAWING stand (atk pose) that steps BACK 1 tile
            #            (real boar threat display: back up before charging),
            #   hit 2 -> a locked straight CHARGE (x2 speed) that ends in a
            #            skid past the player's tile + a bite at contact.
            if beh.get("warn_s") and z.charge_until <= now_mono:
                if not z.boar_warned:
                    z.boar_warned = True
                    z.facing = _side_animal_facing(z, dx, dy)
                    _web_set_anim(z, "atk", now_mono)
                    # Threat display: back away one step (reads as winding up).
                    if length > 1e-6:
                        _web_chase_step(
                            z, -dx / length, -dy / length, collision, step,
                            now_mono, result, speed=z.web_speed,
                            towards=(-dx, -dy),
                        )
                    z.recover_until = now_mono + float(beh.get("warn_s", 0.7))
                    continue  # the winding-up display this tick
                if z.recover_until <= now_mono and length > 1e-6:
                    # Charge window: lock the vector at launch.
                    if z.charge_until == 0.0:
                        z.charge_until = now_mono + float(beh.get("charge_s", 1.5))
                        z.charge_dx, z.charge_dy = dx / length, dy / length
                    if z.charge_until > now_mono:
                        _web_chase_step(
                            z, z.charge_dx, z.charge_dy, collision, step,
                            now_mono, result,
                            speed=z.web_speed * float(beh.get("charge_mult", 2.0)),
                            towards=(z.charge_dx, z.charge_dy),
                        )
                        continue
                    # Charge spent: a visible skid stop (atk pose flash) then
                    # the normal melee flow takes over.
                    z.charge_until = 0.0
                    _web_set_anim(z, "atk", now_mono)
                    z.recover_until = now_mono + WEB_ZOMBIE_RECOVER_S
            # BEAR O5a back-turn pursuit: a player FLEEING from an aggro bear
            # eats a speed burst (bears chase what runs). O5b two-stage rage
            # is armed in rules.py via bear_hit_at (first hit = warning only
            # damage stands, second within rage_s = full aggro).
            if beh.get("backturn_bonus"):
                moving_away = (dx * -1) and length > 1e-6
                # "Away" reads through the player's position delta vs the
                # bear: the player is the one moving; we approximate with the
                # bear's own aggro clock — if the player is beyond half the
                # leash, they are running. Speed burst applies.
                if z.aggro_origin is not None:
                    ox, oy = z.aggro_origin
                    far = _math.hypot(z.x_f - ox, z.y_f - oy) > leash * 0.5 if leash else False
                    if far and now_mono < z.backturn_until:
                        pass
                    elif far:
                        z.backturn_until = now_mono + float(beh.get("backturn_s", 3.0))
            # Aggro: falls through to the melee chase/bite below (a cornered
            # boar/bear is a melee mob until it calms down).

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
                    towards=(dx, dy),
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
            # ---- STATUS EFFECTS (user 25/09): per-kind bite roll. Zombie
            # 50% infection L1; spider 70% poison L1 (refresh: 50% -> L2);
            # bat 25% light poison L1. Refresh = reset the clock (shared).
            # Bat has its own LIGHT poison numbers (12 s, 1 dmg / 3 s).
            try:
                from game import status_effects as _se

                if z.kind == "bat":
                    if rng.random() < _se.BITE_EFFECTS["bat"]["chance"]:
                        _se.apply_bat_poison(target)
                        result.changed = True
                else:
                    eff = _se.apply_bite_effect(target, z.kind, rng)
                    if eff is not None:
                        result.changed = True
            except Exception:  # noqa: BLE001 — a status roll must never break the bite
                pass
            # Pick the recovery drift: mostly AWAY from the target with a
            # random sideways component, so packs break apart instead of
            # shuffling in lockstep.
                rec_len = max(1e-6, length)
                # SKITTISH (rat/goblin/fox): hit-and-run — a LONG retreat
                # drift straight away from the player after every bite (fox
                # O6c: retreat distance from its own behavior row).
                if style == "skittish":
                    z.recover_dx = -dx / rec_len
                    z.recover_dy = -dy / rec_len
                    z.recover_until = now_mono + max(
                        1.2, float(beh.get("hitrun_retreat", 0.0) or 1.2),
                    )
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
        # ---- FOX O6a/O6b (skittish animal, Minecraft parity) -------------
        # A WALKING player inside walk_tolerance_r only makes the fox KEEP
        # ITS GAP (back away to keep_gap, no far flee); sprinting/noisy
        # players (handled by the skittish flow below) scatter it. Away
        # from players, an unattended drop within scavenge_r lures the fox
        # over (the opportunistic scavenger steal).
        if getattr(z, "ambient", False) and style == "skittish":
            keep_r = float(beh.get("walk_tolerance_r", 4.0))
            gap = float(beh.get("keep_gap", 3.0))
            if dist <= keep_r and length > 1e-6:
                z.scavenge_target = None
                if dist < gap:
                    # Back away slowly, holding the gap (curious-wary).
                    ux, uy = -dx / length, -dy / length
                    _web_chase_step(
                        z, ux, uy, collision, step, now_mono, result,
                        speed=z.web_speed * 0.6, towards=(-dx, -dy),
                    )
                else:
                    _web_set_anim(z, "idle", now_mono)
                continue
            # Scavenger: nearest live drop within scavenge_r while no player
            # is close (the fox only steals from unattended piles).
            sc_r = float(beh.get("scavenge_r", 0.0))
            player_r = float(beh.get("scavenge_player_r", 6.0))
            if sc_r > 0.0 and dist > player_r:
                field_ = getattr(state, "drop_field", None)
                best, best_d = None, sc_r
                if field_ is not None:
                    for d in field_.drops.values():
                        if getattr(d, "phase", "idle") != "idle":
                            continue
                        dd = _math.hypot(d.x_f - z.x_f, d.y_f - z.y_f)
                        if dd < best_d:
                            best, best_d = d, dd
                if best is not None:
                    ddx, ddy = best.x_f - z.x_f, best.y_f - z.y_f
                    dl = _math.hypot(ddx, ddy)
                    if dl > 0.35:
                        _web_chase_step(
                            z, ddx / dl, ddy / dl, collision, step,
                            now_mono, result, speed=z.web_speed,
                            towards=(ddx, ddy),
                        )
                    else:
                        # Reached the drop: "eat" it — the drop is gone.
                        try:
                            from game.drops import get_drop_field as _gdf
                            fld = _gdf(state)
                            fld.drops.pop(best.drop_id, None)
                            result.changed = True
                            result.visible_changed = True
                        except Exception:  # noqa: BLE001 — a fox snack never breaks the tick
                            pass
                        _web_set_anim(z, "atk", now_mono)
                    continue
        if not sees or length <= 1e-6:
            _web_set_anim(z, "idle", now_mono)
            continue
        # Hunters are always fast; ambush pounce bursts faster; regular kinds
        # use their MOB_KINDS speed.
        speed = WEB_ZOMBIE_HUNTER_SPEED if z.hunter else z.web_speed
        if style == "ambush" and getattr(z, "ambush_armed", False) and now_mono < getattr(z, "ambush_until", 0.0):
            speed = max(speed, z.web_speed * 1.8)  # pounce!
        # BEAR O5a back-turn burst: pursuit speeds up while the back-turn
        # window is live (the player ran from an aggro bear).
        if beh.get("backturn_bonus") and now_mono < z.backturn_until:
            speed = max(speed, z.web_speed * float(beh.get("backturn_bonus", 1.4)))
        # WOLF PACK: each fellow wolf within pack_radius adds pack_bonus
        # speed (capped at pack_cap x) — wolves hunt in packs, day or night.
        wolf_pack = False
        if beh.get("pack"):
            wolf_pack = True
            fellows = sum(
                1 for o in _web_zombies(state)
                if o is not z and getattr(o, "kind", "") == z.kind
                and _web_dist(o, target) <= float(beh.get("pack_radius", 5.0))
            )
            if fellows:
                speed = min(
                    speed * (1.0 + fellows * float(beh.get("pack_bonus", 0.1))),
                    speed * float(beh.get("pack_cap", 1.5)),
                )
        # WOLF O7a KUNG-FU CIRCLE: a pack member who is NOT the designated
        # biter holds a ring position around the player instead of dogpiling
        # (the "comedy clump" fix). The relay slot (O7b) picks one biter at
        # a time; everyone else circles tangentially at circle_r.
        if wolf_pack and beh.get("circle"):
            circle_r = float(beh.get("circle_r", 3.0))
            wolves = [
                o for o in _web_zombies(state)
                if getattr(o, "kind", "") == "wolf"
                and _web_dist(o, target) <= float(beh.get("pack_radius", 5.0)) * 2
            ]
            wolves.sort(key=lambda o: o.zombie_id)
            slot = wolves.index(z) if z in wolves else 0
            # Relay bite (O7b): exactly one wolf may bite per relay window —
            # the slot rotates by wall clock so the pack takes turns.
            relay_s = float(beh.get("relay_bite_s", 1.2))
            my_turn = (slot == int(now_mono / relay_s) % max(1, len(wolves)))
            if dist > circle_r:
                pass  # close in normally via the melee flow below
            elif not my_turn:
                # Hold the ring: tangential orbit around the player.
                import math as _mk
                ang = _mk.atan2(z.y_f - target.y_f, z.x_f - target.x_f) + 0.9
                tx = target.x_f + _mk.cos(ang) * circle_r
                ty = target.y_f + _mk.sin(ang) * circle_r
                odx, ody = tx - z.x_f, ty - z.y_f
                ol = _math.hypot(odx, ody)
                if ol > 0.1:
                    _web_chase_step(
                        z, odx / ol, ody / ol, collision, step, now_mono,
                        result, speed=z.web_speed, towards=(-odx, -ody),
                    )
                else:
                    _web_set_anim(z, "idle", now_mono)
                continue
        # WOLF O7c HOWL SUMMON: the first wolf to see the player calls every
        # fellow wolf within howl_r to its position (they converge for
        # howl_summon_s). Cooldown keeps the howl a rare, readable event.
        if wolf_pack and beh.get("howl_r") and now_mono - z.howled_at > 20.0:
            howl_r = float(beh.get("howl_r", 12.0))
            called = 0
            for o in _web_zombies(state):
                if o is z or o.kind != "wolf":
                    continue
                if _web_dist(o, z) <= howl_r:
                    o.recover_dx, o.recover_dy = z.x_f - o.x_f, z.y_f - o.y_f
                    ol = _math.hypot(o.recover_dx, o.recover_dy) or 1.0
                    o.recover_dx, o.recover_dy = o.recover_dx / ol, o.recover_dy / ol
                    o.wander_walking = True
                    o.wander_until = now_mono + float(beh.get("howl_summon_s", 5.0))
                    called += 1
            if called:
                z.howled_at = now_mono
                _web_set_anim(z, "atk", now_mono)  # the howl pose
        # SWARM (bat): erratic weaving — sinusoidal sideways offset while
        # closing in so it flies in loops instead of a straight beeline.
        ux, uy = dx / length, dy / length
        if style == "swarm":
            import math as _m2
            wob = _m2.sin(now_mono * 6.0 + hash(z.zombie_id) % 7)
            ux, uy = ux - uy * wob * 0.6, uy + ux * wob * 0.6
            wl = _m2.hypot(ux, uy) or 1.0
            ux, uy = ux / wl, uy / wl
        _web_chase_step(
            z, ux, uy, collision, step, now_mono, result, speed=speed,
            towards=(dx, dy),  # chasing: face the target, not the slide vector
        )
    if result.changed:
        result.visible_changed = True
    return result


def _web_chase_step(
    z: Zombie, ux: float, uy: float, collision,
    step: float, now_mono: float, result: "ZombieTurnResult",
    speed: float,
    towards: tuple[float, float] | None = None,
) -> None:
    """One float chase step along a UNIT vector with collision + the void
    guard (shared by the melee chase, the prey flee and the ranged caster
    drift).

    `towards` = (dx, dy) to the TARGET (player or away-from-player for
    fleeing prey): when given, facing tracks the TARGET, not the actual
    post-collision movement vector — a mob sliding along a tree keeps
    looking at what it hunts, and a single-facing (side-view) animal
    moving purely vertically keeps a sensible horizontal bearing instead
    of freezing on a stale facing."""
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
        # SIDE-VIEW animals: E/W face from the ACTUAL step (hysteresis),
        # never the target vector — a diagonally-fleeing deer must not
        # strobe its head while gliding. Kaetram mobs keep target-tracking.
        if getattr(z, "ambient", False):
            z.facing = _side_animal_facing(z, ux * speed * step, uy * speed * step)
        else:
            z.facing = _web_facing_towards(z, *towards) if towards else _web_facing(ux, uy)
        _web_set_anim(z, "walk", now_mono)
        result.changed = True
    else:
        # Blocked: STILL track the target — a mob pressed against a tree
        # between bites must keep eye contact instead of drifting its face
        # to whatever direction the blocked step failed toward.
        if towards and not getattr(z, "ambient", False):
            z.facing = _web_facing_towards(z, *towards)
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
        # SIDE-VIEW animals face E/W only (hysteresis-protected); Kaetram
        # mobs keep the 8-way facing.
        if getattr(z, "ambient", False):
            z.facing = _side_animal_facing(z, dx, dy)
        else:
            z.facing = _web_facing(dx, dy)
        _web_set_anim(z, "walk", now_mono)
        result.changed = True
    else:
        # Blocked: just breathe on the idle frame instead of pressing into
        # the wall/player.
        _web_set_anim(z, "idle", now_mono)
