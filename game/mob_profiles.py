"""Per-map night-mob spawn profiles + per-kind combat behavior.

The user wants the Ekonia side maps to feel DIFFERENT from the bigmap:
each map picks its own mob pool, spawn weights, population caps and
active-hours window from these data tables, and each mob KIND fights with
its own behavior (not one shared bite routine).

Profile keys (all optional — missing keys fall back to the bigmap defaults):
- kinds:        {kind: weight} spawn table (weights are relative).
- max_count:    live-mob cap for the whole map population upkeep.
- spawn_chance: chance the spawner tries a refill this tick (0..1).
- always_active: True = mobs roam day AND night (cave is always dark).
- day_scale:    multiplier over the base cap during DAY hours when not
                always_active (forest: mobs prowl at reduced density).
- min_spawn_dist / despawn_dist / leash / vision: behavior tuning mirrors.

Behavior flags per kind (see game.zombies MOB_KINDS + behavior helpers):
- style:        "melee" | "ranged" | "ambush" | "skittish" | "swarm"
                | "prey" | "neutral"
- ranged_range: attack distance in tiles for style == "ranged".
- ambush_*:     camouflage params (see _web_ambush_gate in zombies.py).
- skittish:     hits-and-run (retreat window after each bite).
- prey:         DAYTIME ANIMAL — never attacks; flees from players inside
                flee_vision tiles at speed * flee_mult. bird also sets
                fly_away: flee long enough and it despawns ("bay mất").
- neutral:      DAYTIME ANIMAL — wanders idly; only chases/bites back for
                aggro_s seconds AFTER the player hits it first.
- pack:         (wolf) chase speed grows with nearby fellow wolves.

AMBIENT animal pool (separate from the hostile night pool): each profile may
carry ambient_* keys — ambient_kinds {kind: weight}, ambient_max (own cap),
ambient_chance (per-tick refill), ambient_day_scale / ambient_night_scale.
Animals are densest at DAY (when hostile mobs thin out) and thin to
ambient_night_scale at night; maps without ambient_kinds (the cave) get no
animals at all. Animals live in the SAME web store as night mobs
(state.web_zombies, flag ambient=True) so drops/snapshots/attacks reuse the
whole existing pipeline.
"""

from __future__ import annotations

from typing import Dict

# Spawn profiles keyed by map_id (the Tiled map path relative to assets/maps).
MOB_PROFILES: Dict[str, dict] = {
    # MAIN world: unchanged mixed night pack (user kept the current roster).
    "bigmap": {
        "kinds": {
            "zombie": 38, "skeleton": 22, "spider": 16,
            "slime": 12, "bat": 8, "rat": 4,
        },
        "max_count": 4,          # NIGHT_MOB_MAX_COUNT baseline (Discord pack)
        "spawn_chance": 0.45,
        "always_active": False,
        "day_scale": 0.15,
    },
    # CAVE: perpetually dark -> mobs roam day AND night; bat swarm dominant,
    # skeleton2 bruisers, rare spectre. Denser than the surface overall.
    "ekonia/cave_area1": {
        "kinds": {"bat": 50, "skeleton2": 30, "spectre": 8},
        "max_count": 6,
        "spawn_chance": 0.6,
        "always_active": True,
        "day_scale": 1.0,
    },
    # FOREST: goblin warband (weak, common) + hobgoblin bruiser; spiders are
    # the rare ambush predator. Mostly a DAYTIME threat, quiet at deep night.
    "ekonia/forest": {
        "kinds": {"goblin": 50, "hobgoblin": 28, "spider": 12},
        "max_count": 5,
        "spawn_chance": 0.5,
        "always_active": False,
        "day_scale": 0.85,       # prowls all day, thins out at night
    },
}

# ---- AMBIENT animal pool (daytime wildlife, Minifolks pack) ---------------
# Densest at day, thinned at night (user: "ban ngày xuất hiện, ban đêm vẫn
# có nhưng ít hơn"). Maps WITHOUT ambient_kinds (the cave) get no animals.
# wolf is HOSTILE day AND night (user choice: "ngày đêm như nhau") but rides
# the ambient pool so its population is independent of the night-mob cap.
AMBIENT_DEFAULTS = {
    # USER 28/09: "số lượng khá thưa thớt" — modest bump (6 -> 10 / 5 -> 8)
    # + flock cohesion in the wander steering (same-kind animals drift
    # toward each other and share heading) instead of adding many more mobs.
    "ambient_max": 10,
    "ambient_chance": 0.30,
    "ambient_day_scale": 1.0,
    "ambient_night_scale": 0.35,
}

MOB_PROFILES["bigmap"].update({
    "ambient_kinds": {
        "bunny": 30, "deer": 18, "bird": 16, "fox": 10,
        "boar": 12, "deer2": 5, "wolf": 6, "bear": 2,
    },
})
MOB_PROFILES["ekonia/forest"].update({
    "ambient_kinds": {
        "bunny": 36, "deer": 22, "bird": 18, "fox": 10, "boar": 8,
    },
    "ambient_max": 8,
})
# ekonia/cave_area1: NO ambient key — a cave has no daytime wildlife.
# TRADE ZONES (user 28/09: "chợ không nên có động vật bên trong"): the
# lobby + the monter-trade interior get their OWN profiles so they stop
# falling back to the bigmap roster (whose ambient_kinds spawned bunnies/
# deer/wolves inside the market). No ambient_kinds -> ambient_max_for()
# returns 0 -> zero animals, and web_tick despawns any leftovers.
# Hostile mobs were already blocked here (manager.py: trade zones force
# night=False); this closes the ANIMAL side for good.
MOB_PROFILES["lobbytrade"] = {
    "kinds": {},             # hostile pack stays empty (also enforced in manager)
    "max_count": 0,
    "spawn_chance": 0.0,
    "always_active": False,
    "day_scale": 0.0,
}
MOB_PROFILES["montertradebase"] = dict(MOB_PROFILES["lobbytrade"])


# Map ids of the trade zone (mirror of game/travel.TRADE_ZONE_MAPS — kept
# here as data so mob_profiles stays import-light and Discord-free).
TRADE_ZONE_PROFILE_MAPS = frozenset({"lobbytrade", "montertradebase"})


def profile_for(map_id: str) -> dict:
    """Spawn profile for a map (falls back to the bigmap one)."""
    return MOB_PROFILES.get(map_id) or MOB_PROFILES["bigmap"]


def kind_weights(map_id: str) -> Dict[str, float]:
    """{kind: weight} table for a map (falls back to the bigmap roster)."""
    return dict(profile_for(map_id).get("kinds") or MOB_PROFILES["bigmap"]["kinds"])


def roll_kind_for(map_id: str, rng) -> str:
    """Weighted kind roll restricted to the map's own roster."""
    import random as _random

    weights = kind_weights(map_id)
    items = list(weights.items())
    total = sum(w for _k, w in items) or 1.0
    r = rng.uniform(0.0, total)
    acc = 0.0
    for kind, w in items:
        acc += w
        if r <= acc:
            return kind
    return items[0][0] if items else "zombie"


def ambient_profile_for(map_id: str) -> dict:
    """Merged ambient profile for a map ({} when the map has no wildlife).

    The roster itself rides along in the merged dict (read via
    .get('ambient_kinds')) so callers need one lookup."""
    prof = profile_for(map_id)
    roster = prof.get("ambient_kinds")
    if not roster:
        return {}
    merged = dict(AMBIENT_DEFAULTS)
    for key in AMBIENT_DEFAULTS:
        if key in prof:
            merged[key] = prof[key]
    merged["ambient_kinds"] = dict(roster)
    return merged


def ambient_kind_weights(map_id: str) -> Dict[str, float]:
    """{kind: weight} animal table for a map ({} = no wildlife)."""
    return dict(profile_for(map_id).get("ambient_kinds") or {})


def roll_ambient_kind_for(map_id: str, rng) -> str:
    """Weighted animal roll for a map ('bunny' fallback, caller guards)."""
    weights = ambient_kind_weights(map_id)
    items = list(weights.items())
    if not items:
        return "bunny"
    total = sum(w for _k, w in items) or 1.0
    r = rng.uniform(0.0, total)
    acc = 0.0
    for kind, w in items:
        acc += w
        if r <= acc:
            return kind
    return items[0][0]


def ambient_cap_scale(map_id: str, second_of_day: int) -> float:
    """Animal population multiplier: day = day_scale, night = night_scale."""
    from game.zombies import is_night

    prof = ambient_profile_for(map_id)
    if not prof:
        return 0.0
    if is_night(second_of_day):
        return float(prof["ambient_night_scale"])
    return float(prof["ambient_day_scale"])


def ambient_max_for(map_id: str, second_of_day: int) -> int:
    """Live-animal cap at this in-game hour (0 = no wildlife on this map)."""
    import math as _math

    prof = ambient_profile_for(map_id)
    if not prof:
        return 0
    return max(1, _math.ceil(
        int(prof["ambient_max"]) * ambient_cap_scale(map_id, second_of_day)
    ))


def is_trade_zone_map(map_id: str) -> bool:
    """True for the trade lobby + monter-trade interior (NO wildlife rule)."""
    return map_id in TRADE_ZONE_PROFILE_MAPS


def ambient_spawn_chance(map_id: str) -> float:
    """Per-tick animal refill chance for a map."""
    prof = ambient_profile_for(map_id)
    return float(prof.get("ambient_chance", 0.0)) if prof else 0.0


def _mob_spawn_chance(map_id: str) -> float:
    """Per-tick refill chance for a map (manager helper)."""
    return float(profile_for(map_id).get("spawn_chance", 0.45))


def active_at(map_id: str, second_of_day: int) -> bool:
    """Is the mob population active at this in-game time on this map?

    - Cave: 24/7 (underground darkness).
    - Forest: prowl day (scaled by day_scale) AND night — user asked for a
      different rhythm than bigmap, which stays night-only.
    - Bigmap: night only (legacy rule).
    """
    from game.zombies import is_night

    prof = profile_for(map_id)
    if prof.get("always_active"):
        return True
    night = is_night(second_of_day)
    if night:
        return True
    # Day: active only when day_scale > 0 (forest 0.85 -> active, bigmap 0.15
    # keeps the LEGACY night-only behavior because the manager treats
    # day_scale < 0.5 as "night-only" for the old cap ramp).
    return float(prof.get("day_scale", 0.15)) >= 0.5


def day_cap_scale(map_id: str, second_of_day: int) -> float:
    """Population multiplier for the current hour (manager multiplies
    max_count by this)."""
    from game.zombies import is_night

    prof = profile_for(map_id)
    if prof.get("always_active"):
        return 1.0
    if is_night(second_of_day):
        return 1.0
    return float(prof.get("day_scale", 0.15))


# ---- per-kind combat behaviors ---------------------------------------------
# style drives BOTH packs: the web realtime pack steers/bites with these
# numbers, the Discord turn pack uses the same cooldown/vision tuning.
MOB_BEHAVIORS: Dict[str, dict] = {
    "zombie":    dict(style="melee"),
    "skeleton":  dict(style="melee"),
    "slime":     dict(style="melee"),
    "rat":       dict(style="skittish"),
    "spider":    dict(style="ambush", ambush_bonus_vision=6.0),  # pounce trigger radius (see zombies.py x0.5) — USER 29/09: x2 = 12
    "bat":       dict(style="swarm"),
    "skeleton2": dict(style="melee", cooldown=2.6, damage_mult=1.4),
    "spectre":   dict(style="ranged", ranged_range=4.0, cooldown=2.4),
    "goblin":    dict(style="skittish", cooldown=2.0),
    "hobgoblin": dict(style="melee", damage_mult=1.3),
    # ---- daytime wildlife (Minifolks Forest Animals) --------------------
    # VISION x2.5 (user 28/09) + per-species behavior (research-backed —
    # docs hồ sơ: theHunter CotW alert/flight-initiation, Minecraft fox
    # sneak-parity, Valheim boar warn-charge, boids scatter, wolf kung-fu
    # circle).
    # FLOCKING (user 28/09: "tương tác bầy đàn"): flock_r = cohesion radius
    # (same-kind animals drift toward the group centroid + share heading);
    # flock_w = blend weight of the cohesion pull (0 = lone wolf behavior).
    "flock_r": 7.0, "flock_w": 0.35,
    # prey: never attacks; ALERT stand (alert_s) THEN flees, and keeps
    # running to panic_overrun x flee_vision before calming down.
    # USER 29/09 ("khó đánh bọn nó vãi chưởng"): vision x1.5 pushed the
    # flight trigger past MELEE_ATTACK_RANGE reach entirely — the animal
    # bolted while the player was still ~17 tiles away and overran past
    # every chase. Back to the tuned x2.5 vision (11.25 base) and a
    # modest overrun so melee can actually CATCH them.
    "bunny":     dict(style="prey", flee_vision=11.25, flee_mult=1.35,
                      alert_s=0.6, panic_overrun=1.4,
                      zigzag=True, zigzag_deg=40, zigzag_every_s=1.0,
                      burst_s=2.0, burst_mult=1.5),   # freeze->zigzag burst
    "deer":      dict(style="prey", flee_vision=11.25, flee_mult=1.30,
                      alert_s=1.0, panic_overrun=1.4,
                      bark=True, herd_panic_r=8.0,
                      stotting_s=2.0),               # bark + stotting flee
    # deer2 = the "easy species" (theHunter easy/hard parity): vision x2 only,
    # no bark.
    "deer2":     dict(style="prey", flee_vision=8.0, flee_mult=1.25,
                      alert_s=0.7, panic_overrun=1.4),
    "bird":      dict(style="prey", flee_vision=13.75, flee_mult=1.50,
                      alert_s=0.4, panic_overrun=1.5,
                      scatter_r=6.0, hop_circle_s=0.6,
                      # fly_away REWORK: despawn only when the player keeps
                      # CLOSING IN (within 3 tiles) mid-flight — with the old
                      # always-despawn the map emptied of birds entirely.
                      fly_away=True, fly_away_close_r=3.0),
    # neutral: wanders; only fights back for aggro_s seconds after a hit.
    "boar":      dict(style="neutral", aggro_s=15.0,
                      # O4a warn-charge + O4c defensive leash (Vintage Story
                      # parity: pursuit is DEFENSIVE, distance-capped).
                      # USER 29/09 ("gấu chả đánh trả"): no two-stage rage on
                      # the BOAR — one hit charges it immediately. The staged
                      # rage stays BEAR-only below.
                      warn_s=0.7, charge_s=1.5, charge_mult=2.0,
                      charge_leash=6.0),
    "bear":      dict(style="neutral", aggro_s=20.0,
                      # O5a back-turn pursuit + O5c endurance cap. O5b
                      # two-stage rage SIMPLIFIED (user 29/09): the first hit
                      # still aggravates but the bear now CHARGES right away —
                      # the old warning-hit-then-never-fight-back path (the
                      # early return never armed aggro_until) read as the
                      # bear ignoring the player completely.
                      backturn_bonus=1.4, backturn_s=3.0,
                      rage_s=30.0, aggro_leash=8.0),
    # fox: Minecraft parity — tolerant of WALKING players (keeps its
    # distance), flees from SPRINTING/noisy ones; opportunistic scavenger
    # that walks to unattended drops; hit-and-run bite rhythm.
    "fox":       dict(style="skittish", cooldown=2.0,
                      flee_vision=11.25, flee_mult=1.5,
                      walk_tolerance_r=4.0, keep_gap=3.0,
                      scavenge_r=6.0, scavenge_player_r=6.0,
                      hitrun_retreat=2.0),
    # wolf: hostile day AND night; pack=True gives +10% speed per fellow
    # wolf within pack_radius (capped at 1.5x) — wolves hunt in packs.
    # O7a kung-fu circle + O7b relay bite + O7c howl summon.
    "wolf":      dict(style="melee", pack=True, pack_radius=5.0,
                      pack_bonus=0.10, pack_cap=1.5,
                      circle=True, circle_r=3.0,
                      relay_bite_s=1.2,
                      howl_r=12.0, howl_summon_s=5.0),
}


def behavior_of(kind: str) -> dict:
    """Behavior dict for a kind (zombie fallback = plain melee)."""
    return MOB_BEHAVIORS.get(kind) or MOB_BEHAVIORS["zombie"]
