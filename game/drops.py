"""Spirited drop entities ("hạt linh khí") — the shared drop system.

Every gameplay drop (broken blocks, felled trees, mined ore, slain zombies)
spawns a drop entity instead of teleporting into the bag. Each drop is a
LIVING particle with behaviour:

- spawn: pops out with a small random arc (vx/vy), bounces once on landing
- idle: floats with a gentle bob, awaiting a collector
- magnet: a player inside MAGNET_RADIUS pulls the drop with acceleration
  (vortex feel — the drop curves in, never a straight teleport)
- collect: inside COLLECT_RADIUS the drop is granted to the player and
  despawns (the client plays a sparkle burst on the collect echo)
- despawn: uncollected drops fade out after DESPAWN_SECONDS

Pure game state (no Discord, no IO, rule 2/3). Physics integrates in the
web realtime tick (game/manager._web_tick_runtime) at 20 Hz; the Discord
turn pack keeps its old direct-to-bag behaviour (unchanged).
"""

from __future__ import annotations

import math as _math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# ---- tuning (all in tile units / seconds) ----------------------------------
MAGNET_RADIUS = 1.8        # a player this close starts pulling the drop
MAGNET_GRACE_S = 0.7       # keep chasing this long after leaving the radius
                           # (a running player who brushes the radius still
                           # collects — the old accel-only drop fell behind)
COLLECT_RADIUS = 0.6       # this close = collected (into the bag)
MAGNET_MAX_SPEED = 11.0    # homing speed — well above run speed (6 t/s) so
                           # the drop ALWAYS catches its collector
MAGNET_STEER = 10.0        # exponential homing rate: v -> dir*max_speed
                           # (pure acceleration from rest could never overtake
                           # a walking player — the "đi ngang qua không nhặt" bug)
MAGNET_PULL_ACCEL = 22.0   # kept for far-away curvature blending
ARC_GRAVITY = 26.0         # tiles/s^2 — snappy pop, short flight
ARC_LAUNCH_VY = 4.2        # initial upward pop
ARC_LAUNCH_VX = 1.6        # horizontal scatter speed
BOUNCE_DAMPING = 0.35      # energy kept after the ground bounce
GROUND_Y_OFFSET = 0.5      # rest height inside the tile (centre)
DESPAWN_SECONDS = 60.0     # uncollected drops vanish
MAX_DROPS_PER_STATE = 200  # hard safety cap (perf) — oldest despawn first


@dataclass
class DropEntity:
    drop_id: str
    item_id: str
    qty: int
    x_f: float
    y_f: float
    # physics
    vx: float = 0.0
    vy: float = 0.0
    z: float = 0.0            # height above ground (arc/bounce)
    vz: float = 0.0
    landed: bool = False
    bounced: int = 0
    # lifecycle
    born_at: float = field(default_factory=time.monotonic)
    # "idle" | "magnet" | "collected" — server-authoritative for the client
    phase: str = "idle"
    # player currently pulling this drop (set when magnet starts)
    target_id: Optional[int] = None
    # monotonic() when the drop last saw its target inside MAGNET_RADIUS —
    # magnet keeps homing for MAGNET_GRACE_S after this (pass-by pickup)
    last_seen: float = 0.0
    # monotonic() of the moment collection completed (client burst timing)
    collected_at: float = 0.0
    # collected drops linger briefly so the client can play the burst, then
    # the manager prunes them
    collected_by: Optional[int] = None


class DropField:
    """All live drop entities of one scenario + the id sequencer."""

    def __init__(self) -> None:
        self.drops: Dict[str, DropEntity] = {}
        self.seq: int = 0

    def __len__(self) -> int:
        return len(self.drops)

    def spawn(
        self,
        item_id: str,
        qty: int,
        x_f: float,
        y_f: float,
        rng: Optional[random.Random] = None,
        scatter: bool = True,
    ) -> DropEntity:
        """Spawn one drop popping out of (x_f, y_f) with a small arc."""
        rng = rng or random.Random()
        self.seq += 1
        ang = rng.uniform(0, 2 * 3.14159265)
        speed = ARC_LAUNCH_VX * rng.uniform(0.6, 1.4) if scatter else 0.0
        d = DropEntity(
            drop_id=f"drop-{self.seq}",
            item_id=item_id,
            qty=max(1, int(qty)),
            x_f=x_f,
            y_f=y_f,
            vx=speed * _math.cos(ang),
            vy=speed * _math.sin(ang),
            vz=ARC_LAUNCH_VY * rng.uniform(0.85, 1.15),
            born_at=time.monotonic(),
            phase="idle",
        )
        self.drops[d.drop_id] = d
        # enforce the safety cap: oldest drops go first
        while len(self.drops) > MAX_DROPS_PER_STATE:
            oldest = min(
                self.drops.values(), key=lambda d: d.born_at, default=None
            )
            if oldest is None:
                break
            del self.drops[oldest.drop_id]
        return d

    def spawn_many(
        self,
        items: Sequence[Tuple[str, int]],
        x_f: float,
        y_f: float,
        rng: Optional[random.Random] = None,
    ) -> List[DropEntity]:
        out = []
        for item_id, qty in items:
            out.append(self.spawn(item_id, qty, x_f, y_f, rng=rng))
        return out

    def remove(self, drop_id: str) -> Optional[DropEntity]:
        return self.drops.pop(drop_id, None)

    def prune_collected(self, now: float, linger: float = 0.6) -> int:
        """Remove drops whose collect burst window has elapsed."""
        gone = [
            did for did, d in self.drops.items()
            if d.phase == "collected" and now - d.collected_at >= linger
        ]
        for did in gone:
            del self.drops[did]
        return len(gone)

    def prune_expired(self, now: float) -> int:
        """Despawn uncollected drops older than DESPAWN_SECONDS."""
        gone = [
            did for did, d in self.drops.items()
            if d.phase != "collected"
            and now - d.born_at >= DESPAWN_SECONDS
        ]
        for did in gone:
            del self.drops[did]
        return len(gone)


def _ensure_field(state) -> DropField:
    field_ = getattr(state, "drop_field", None)
    if field_ is None:
        field_ = DropField()
        state.drop_field = field_
    return field_


def get_drop_field(state) -> DropField:
    return _ensure_field(state)


def spawn_drops(
    state,
    x: float,
    y: float,
    items: Sequence[Tuple[str, int]],
    rng: Optional[random.Random] = None,
) -> List[DropEntity]:
    """Public API: every drop source calls this instead of inventory.add.

    Spawns one drop per (item_id, qty) pair popping out of tile (x, y).
    """
    if not items:
        return []
    field_ = _ensure_field(state)
    return field_.spawn_many(items, float(x) + 0.5, float(y) + 0.5, rng=rng)


def tick_drops(
    state,
    players: Sequence[object],
    dt: float,
    collision=None,
    rng: Optional[random.Random] = None,
) -> Tuple[List[Tuple[int, str, int]], int]:
    """One physics + magnet + collect beat.

    Returns (collections, pruned) where collections is a list of
    (user_id, item_id, qty) GRANTED this tick — the caller (manager) persists
    the inventory change and notifies the hub. Drops themselves are already
    removed here.
    """
    import math as _math

    rng = rng or random.Random()
    field_ = _ensure_field(state)
    collections: List[Tuple[int, str, int]] = []
    now = time.monotonic()
    step = max(0.0, min(0.25, dt))
    alive_players = [
        p for p in players
        if getattr(p, "alive", True) and getattr(p, "visible", True)
    ]

    for d in list(field_.drops.values()):
        if d.phase == "collected":
            continue

        # ---- magnet: nearest player inside the radius pulls the drop -----
        # HOMING (exponential steering), not raw acceleration: the drop's
        # velocity converges to dir*MAGNET_MAX_SPEED every tick, so it always
        # overtakes even a running player. The old accel-from-rest model let a
        # walking player stroll straight past a drop without collecting it,
        # and the leftover speed made drops glide toward players far away.
        target = None
        best = MAGNET_RADIUS
        for p in alive_players:
            dist = _math.hypot(p.x_f - d.x_f, p.y_f - d.y_f)
            if dist < best:
                best = dist
                target = p
        if target is not None:
            d.last_seen = now
        elif d.phase == "magnet" and now - d.last_seen <= MAGNET_GRACE_S:
            # Just left the radius: lock onto the last target for the grace
            # window — the pass-by collect still lands.
            for p in alive_players:
                if p.user_id == d.target_id:
                    target = p
                    break
        if target is not None:
            d.phase = "magnet"
            d.target_id = target.user_id
            dx = target.x_f - d.x_f
            dy = target.y_f - d.y_f
            length = max(1e-6, _math.hypot(dx, dy))
            # Desired homing velocity; blend in fast (MAGNET_STEER/s).
            blend = min(1.0, MAGNET_STEER * step)
            d.vx += ((dx / length) * MAGNET_MAX_SPEED - d.vx) * blend
            d.vy += ((dy / length) * MAGNET_MAX_SPEED - d.vy) * blend
            # Slight curvature while FAR from the target (vortex feel); near
            # the player the homing is straight so collection never misses.
            if length > 1.0:
                angle = 0.5 * step
                cos_a, sin_a = _math.cos(angle), _math.sin(angle)
                d.vx, d.vy = (
                    d.vx * cos_a - d.vy * sin_a,
                    d.vx * sin_a + d.vy * cos_a,
                )
        else:
            if d.phase == "magnet":
                d.phase = "idle"
                d.target_id = None
            # idle drift: velocity decays (friction), bob is client-side
            d.vx *= max(0.0, 1.0 - 2.0 * step)
            d.vy *= max(0.0, 1.0 - 2.0 * step)

        # ---- ground physics (arc + bounce) -------------------------------
        nx = d.x_f + d.vx * step
        ny = d.y_f + d.vy * step
        # swept per-axis blocked check when a collision grid is available —
        # drops never tunnel through walls
        blocked_x = blocked_y = False
        if collision is not None:
            try:
                blocked_x = not collision.is_walkable(int(_math.floor(nx)), int(_math.floor(d.y_f)))
                blocked_y = not collision.is_walkable(int(_math.floor(d.x_f)), int(_math.floor(ny)))
            except Exception:
                blocked_x = blocked_y = False
        if blocked_x:
            d.vx = -d.vx * BOUNCE_DAMPING
        else:
            d.x_f = nx
        if blocked_y:
            d.vy = -d.vy * BOUNCE_DAMPING
        else:
            d.y_f = ny

        # vertical arc: launch up, gravity down, bounce once on landing
        if not d.landed:
            d.vz -= ARC_GRAVITY * step
            d.z += d.vz * step
            if d.z <= 0.0:
                d.z = 0.0
                d.bounced += 1
                if d.bounced <= 1 and abs(d.vz) > 0.8:
                    d.vz = -d.vz * BOUNCE_DAMPING
                else:
                    d.landed = True
                    d.vz = 0.0

        # ---- collect -------------------------------------------------------
        if target is not None and best <= COLLECT_RADIUS:
            d.phase = "collected"
            d.collected_at = now
            d.collected_by = target.user_id
            collections.append((target.user_id, d.item_id, d.qty))

    field_.prune_collected(now)
    field_.prune_expired(now)
    return collections, 0


def drops_payload(
    state,
    center_x: float,
    center_y: float,
    cull_radius: float = 24.0,
) -> List[list]:
    """Viewport-culled wire format: [id, item_id, qty, x, y, z, phase]."""
    field_ = getattr(state, "drop_field", None)
    out: List[list] = []
    if field_ is None:
        return out
    import math as _math

    for d in field_.drops.values():
        if _math.hypot(d.x_f - center_x, d.y_f - center_y) > cull_radius:
            continue
        out.append([
            d.drop_id,
            d.item_id,
            d.qty,
            round(d.x_f, 3),
            round(d.y_f, 3),
            round(d.z, 3),
            d.phase,
        ])
    return out
