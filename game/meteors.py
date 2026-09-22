"""Meteor shower events (Trần Giang bigmap): night scheduler + admin summons.

Design (session 09/2026):
- Meteors fall ONLY on the bigmap (ekonia overworld), ONLY at night
  (20:00-06:00 in-game, same window as the zombie pack).
- Per-night schedule: each scheduler beat rolls BASE_CHANCE; every meteor
  that ALREADY fell tonight halves the next one's chance
  (20% -> 10% -> 5% -> 2.5% ...). A new night resets the chance.
- A meteor event = warning telegraph (8 s, client draws the danger ring at
  the target tile) -> falling meteor (client-side animation) -> impact:
  directional camera shake (server sends impact + player distances), then
  rare-ore spawns land near the crater (ore hook TODO in the mining pass).

Transport: the 20 Hz snapshot carries `meteors` — the ACTIVE event list.
Clients that never see a tile just ignore it; everyone in range animates.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --- tuning -----------------------------------------------------------------
WARNING_SECONDS = 8.0          # telegraph window before the impact lands
IMPACT_SHAKE_RADIUS = 14.0     # tiles: beyond this the camera does not shake
IMPACT_MAX_SHAKE = 22.0        # px of the first violent jolt at distance 0
SCHEDULER_BEAT_SECONDS = 20.0  # one spawn roll every N real seconds at night
BASE_CHANCE = 0.20             # first meteor of the night: 20%
HALVING = 0.5                  # each prior meteor tonight halves the chance
MAX_METEORS_PER_NIGHT = 6      # hard cap — the halving rarely reaches it


def _night_id(second_of_day: int) -> int:
    """Night counter for the halving reset.

    Nights span 20:00 -> 06:00 (crossing midnight), same as zombies.py. The
    id is the count of day boundaries since the epoch adjusted so that a
    night starting at 20:00 shares one id through to 06:00 the next morning:
    shift the clock back 20h, then divide by full days. Any stable monotonic
    day-ish counter works — the value only needs to CHANGE at 06:00.
    """
    shifted = (second_of_day - 6 * 3600) % 86400  # 0 at 06:00
    return int(shifted // 60)  # coarse bucket: changes shortly after 06:00


@dataclass
class MeteorEvent:
    """One falling meteor scheduled to hit (tx, ty)."""
    id: int
    tx: int
    ty: int
    dir: str                      # "left" | "right" — flight direction
    warned_at: float              # monotonic second the warning started
    impact_at: float              # monotonic second of the impact
    impact_sent: bool = False     # server already reported the impact shake


@dataclass
class MeteorState:
    """Per-scenario scheduler state (lives on the ScenarioRuntime)."""
    last_night: int = -1                    # night bucket already reset for
    felled_tonight: int = 0                 # meteors already landed tonight
    last_beat: float = 0.0                  # monotonic of the last spawn roll
    next_id: int = 1
    active: List[MeteorEvent] = field(default_factory=list)


def is_meteor_map(map_id: str) -> bool:
    """Only the bigmap overworld gets meteor showers."""
    return map_id == "ekonia/overworld"


def meteor_chance(felled_tonight: int) -> float:
    """Current spawn chance given how many meteors already fell tonight.

    20% base, halved per prior meteor: 20% -> 10% -> 5% -> 2.5% ...
    """
    chance = BASE_CHANCE
    for _ in range(felled_tonight):
        chance *= HALVING
    return chance


def snapshot_payload(state: MeteorState, now: float) -> List[list]:
    """Active events for the 20 Hz snapshot: [id, tx, ty, dir, impact_in_s].

    `impact_in_s` is a countdown so every client animates the same timeline
    regardless of when its snapshot landed.
    """
    out: List[list] = []
    for m in state.active:
        if m.impact_sent:
            continue  # already exploded: the client keeps its local FX
        out.append([
            m.id, m.tx, m.ty, m.dir,
            round(max(0.0, m.impact_at - now), 2),
        ])
    return out


def tick_meteors(
    state: MeteorState,
    now: float,
    second_of_day: int,
    is_night_now: bool,
    pick_target,                    # () -> (tx, ty) walkable tile
    rng: Optional[random.Random] = None,
) -> List[MeteorEvent]:
    """One scheduler beat: roll spawns, expire landed meteors.

    Returns the meteors that JUST landed this tick (impact just happened —
    the caller fans out the shake payload and spawns the ore)."""
    rng = rng or random.Random()
    landed: List[MeteorEvent] = []

    # Night rollover: reset the halving counter the moment the night id
    # changes (06:00). Daytime the counter is irrelevant (no spawns).
    nid = _night_id(second_of_day)
    if nid != state.last_night:
        state.last_night = nid
        state.felled_tonight = 0

    # Expire landed events (they stay in `active` until the client beat saw
    # the impact; impact_sent marks them and this pass drops them).
    state.active = [
        m for m in state.active
        if not (m.impact_sent and now >= m.impact_at)
    ]

    for m in state.active:
        if not m.impact_sent and now >= m.impact_at:
            m.impact_sent = True
            landed.append(m)

    if not is_night_now:
        return landed
    if state.felled_tonight >= MAX_METEORS_PER_NIGHT:
        return landed
    if now - state.last_beat < SCHEDULER_BEAT_SECONDS:
        return landed
    state.last_beat = now

    chance = meteor_chance(state.felled_tonight)
    if rng.random() < chance:
        tx, ty = pick_target()
        if tx is not None:
            state.active.append(MeteorEvent(
                id=state.next_id,
                tx=tx, ty=ty,
                dir=rng.choice(["left", "right"]),
                warned_at=now,
                impact_at=now + WARNING_SECONDS,
            ))
            state.next_id += 1
            # The spawn counts immediately (halves the NEXT roll) — the
            # meteor is definitely coming once the warning is out.
            state.felled_tonight += 1

    return landed


def summon(state: MeteorState, now: float, tx: int, ty: int,
           dir: Optional[str] = None, rng: Optional[random.Random] = None,
           near_random: bool = False) -> MeteorEvent:
    """Admin /meteor: force one meteor now.

    near_random=True picks a tile 6-12 tiles away from (tx, ty) — the
    "random around the caller" summon; otherwise the meteor hits (tx, ty)
    exactly ("ngay tại chỗ đứng").
    """
    if near_random:
        ang = (rng or random).uniform(0, 6.283185)
        import math
        dist = (rng or random).uniform(6.0, 12.0)
        tx = int(math.floor(tx + math.cos(ang) * dist))
        ty = int(math.floor(ty + math.sin(ang) * dist))
    m = MeteorEvent(
        id=state.next_id, tx=tx, ty=ty,
        dir=dir or (rng or random).choice(["left", "right"]),
        warned_at=now, impact_at=now + WARNING_SECONDS,
    )
    state.next_id += 1
    state.active.append(m)
    return m


def shake_for_player(m: MeteorEvent, px: float, py: float) -> Tuple[float, float, float]:
    """Directional shockwave for one player.

    Returns (shake_x, shake_y, magnitude) — the vector points FROM the
    impact TOWARD the player (the ground heaves outward from the crater);
    magnitude fades linearly to zero at IMPACT_SHAKE_RADIUS tiles.
    """
    dx = px - m.tx
    dy = py - m.ty
    dist = (dx * dx + dy * dy) ** 0.5
    if dist < 0.001 or dist >= IMPACT_SHAKE_RADIUS:
        return (0.0, 0.0, 0.0)
    mag = IMPACT_MAX_SHAKE * (1.0 - dist / IMPACT_SHAKE_RADIUS)
    return (dx / dist, dy / dist, mag)
