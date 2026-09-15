"""Multi-world runtime plumbing for the /khutraodoi trade lobby (game layer).

The trade lobby and the monter-trade interior are SECONDARY worlds of the
same channel scenario: the main world keeps the bigmap runtime (as saved in
``scenarios``), while ``lobbytrade``/``montertradebase`` run as lazy side
runtimes keyed by ``(channel_id, map_id)``. One lock per runtime (rule 16) —
never a shared asyncio lock.

The teleport itself is data-driven (``game/portals.py`` + ``portals.json``):
stepping on a portal tile moves the Player + PlayerScreen to the destination
runtime; the Discord screen/controls/hub messages are REUSED (re-rendered for
the new map), never re-created (rule 24/25).
"""
from __future__ import annotations

import logging
from pathlib import Path

from game.portals import PortalLink, Portals
from rendering.camera import Camera

log = logging.getLogger("GAME")

# The lobby runtime is the same channel: it just isn't the map saved in
# ``scenarios``. These maps support per-channel side worlds.
TRADE_LOBBY_MAP = "lobbytrade"
TRADE_INTERIOR_MAP = "montertradebase"

# Runtime attribute: per-player "box is TOUCHING a portal tile right now" set.
# It is the EDGE DETECTOR for the gate (see check_portal_after_move): a gate
# fires on the not-touching -> touching transition only, so standing on or
# against the door can never re-fire it.
_PORTAL_LATCH_ATTR = "on_portal_tile"
# Gate cooldown (user 16/09): a short grace window after every teleport. The
# old model re-armed with a 2.5 s timer — which cleared while the player was
# still standing next to the door they came out of, and the next tiny step
# pulled them straight back in ("vừa ra khỏi khu vực đã bị dịch chuyển vào
# lại"). Edge detection + this cooldown replaces it: no timer, no bounce.
_GATE_COOLDOWN_SECONDS = 0.9

# TRADE ZONES: maps where building is forbidden (user rule 15/09 — "ở khu
# trao đổi thì cấm phá block"). Both the Discord path and the web path go
# through GameManager.dispatch, so a gate there covers every client.
TRADE_ZONE_MAPS = frozenset({TRADE_LOBBY_MAP, TRADE_INTERIOR_MAP})


def is_trade_zone(rt) -> bool:
    """True when ``rt`` is a trade-zone world (lobby / monter interior).

    Side worlds carry the map_id on their map_data; main worlds simply don't
    match the frozenset."""
    try:
        return rt.map_data.map_id in TRADE_ZONE_MAPS
    except AttributeError:
        return False


def load_portals(assets_dir: Path) -> Portals:
    """Load portals.json. ``assets_dir`` is the project's assets root (or the
    maps dir itself — both resolve to the same config file)."""
    base = Path(assets_dir) if assets_dir is not None else None
    if base is None:
        return Portals({})  # headless/tests: no assets dir, no portals
    if base.is_file():
        # Caller passed the portals.json path itself.
        return Portals.load(base)
    if base.name == "maps" or (base / "portals.json").exists():
        return Portals.load(base / "portals.json")
    return Portals.load(base / "maps" / "portals.json")


def resolve_spawn_tiles(rt, portal_cfg: Portals, map_id: str):
    """Arrival tiles for ``map_id``: the portal config's explicit list, else
    the map's spawn-layer tile (the lobbytrade note), else map_data.spawn."""
    cfg = portal_cfg.for_map(map_id)
    if cfg is not None and cfg.spawn:
        return list(cfg.spawn)
    spawn = getattr(rt.map_data, "spawn", (0, 0))
    return [tuple(spawn)]


def resolve_link_target(
    rt, link: PortalLink, portal_cfg: Portals
):
    """Real arrival tiles for a portal link (spawn_layer marker resolved
    against the DESTINATION runtime's map data)."""
    if not link.target_is_layer:
        return list(link.target)
    return resolve_spawn_tiles(rt, portal_cfg, link.map_id)


def free_arrival_tile(rt, candidates, occupied, portal_cfg=None) -> tuple:
    """Pick the arrival tile: walkable, unoccupied, and — when ``portal_cfg``
    is given — NOT a gate tile of the destination map.

    Arriving exactly ON the exit tile (the interior mat) is what made the gate
    feel hair-triggered: the player spawned standing on the trigger. When the
    only candidate is a gate tile (that mat), nudge to a free walkable
    neighbour so arrivals land just inside the room and a deliberate step
    back onto the mat is what exits.
    """

    def is_trigger(x: int, y: int) -> bool:
        return (
            portal_cfg is not None
            and portal_cfg.link_at(rt.map_data.map_id, x, y) is not None
        )

    def pick(pred) -> tuple | None:
        for x, y in candidates:
            if pred(x, y):
                return (x, y)
        return None

    chosen = (
        pick(lambda x, y: rt.collision.is_walkable(x, y)
             and (x, y) not in occupied and not is_trigger(x, y))
        or pick(lambda x, y: rt.collision.is_walkable(x, y)
                and (x, y) not in occupied)
        or pick(lambda x, y: rt.collision.is_walkable(x, y))
        or (tuple(candidates[0]) if candidates else (0, 0))
    )
    if not is_trigger(*chosen):
        return chosen
    for dx, dy in ((0, -1), (0, 1), (1, 0), (-1, 0)):
        nx, ny = chosen[0] + dx, chosen[1] + dy
        if (rt.collision.is_walkable(nx, ny) and (nx, ny) not in occupied
                and not is_trigger(nx, ny)):
            return (nx, ny)
    return chosen


def portal_link_for(rt, portal_cfg: Portals, x: int, y: int) -> PortalLink | None:
    """The teleport link triggered by standing on (x, y) of rt's map."""
    return portal_cfg.link_at(rt.map_data.map_id, x, y)


def _touching_portal_link(rt, portal_cfg: Portals, player):
    """The link whose trigger tile the player's COLLISION BOX touches.

    User 16/09: the door is a PORTAL GATE, not a floor marker — touching it
    must teleport immediately and the player must never walk through it.
    The old center-tile check let the swept float box slide across the
    1-tile-deep door between two int syncs (the "đi xuyên cửa" bug). The
    box is FLOAT_BOX_HALF wide around the continuous position; when only
    int coords exist (Discord pack) the box reduces to the own tile.
    """
    import math

    from game.collision import FLOAT_BOX_HALF

    r = FLOAT_BOX_HALF
    # GATE MARGIN: the web client predicts collision with its own tile/mask
    # copy and stops its reported position a hair short of the door sprite
    # (observed: box edge 0.01 tiles from the trigger row). Expand the box by
    # a small margin so PUSHING AGAINST the gate fires the teleport — the
    # door acts like a pressure plate in front of it, and walking through
    # stays impossible (any crossing path touches the expanded box).
    GATE_MARGIN = 0.15
    rr = r + GATE_MARGIN
    x_f = getattr(player, "x_f", None)
    y_f = getattr(player, "y_f", None)
    if x_f is None or y_f is None:
        x_f, y_f = player.x + 0.5, player.y + 0.5
    x0 = int(math.floor(x_f - rr))
    x1 = int(math.floor(x_f + rr))
    y0 = int(math.floor(y_f - rr))
    y1 = int(math.floor(y_f + rr))
    map_id = rt.map_data.map_id
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            link = portal_cfg.link_at(map_id, tx, ty)
            if link is not None:
                return link
    return None


def check_portal_after_move(rt, portal_cfg: Portals, user_id: int,
                            moved_off_portal: bool = False):
    """Called after a move/position report. Returns ``(link, player)`` when a
    teleport must fire, else None.

    EDGE-TRIGGERED gate (user 16/09 — "cổng đang hơi nhạy"): the gate fires
    only on the not-touching -> touching transition of the player's collision
    box, plus a _GATE_COOLDOWN_SECONDS grace window after any teleport.

    Why the old latch+timer was wrong: it re-armed on a 2.5 s timer, so a
    player who had just come out of the door — still standing right next to it
    — had the gate armed again under their feet, and the next tiny movement
    (or holding the key they came out with) yanked them straight back into the
    map they had just left. Timers cannot express "I have walked away".

    Consequences of the edge model, all intended:
      * arriving AT the gate (the interior mat) never re-fires — the arrival
        is seeded as touching (move_player_between_runtimes) and/or nudged
        off the trigger (free_arrival_tile);
      * stepping off the gate and walking back onto it teleports again —
        that is what a door does, and it now needs a deliberate move;
      * standing, pushing or jittering against the door never re-fires.

    ``moved_off_portal`` is accepted for the web tick's call signature and is
    no longer load-bearing (edge detection derives that state itself).
    """
    import time as _time

    player = rt.state.get_player(user_id)
    if player is None:
        return None
    touching = getattr(rt, _PORTAL_LATCH_ATTR, None)
    if touching is None:
        touching = set()
        setattr(rt, _PORTAL_LATCH_ATTR, touching)
    link = _touching_portal_link(rt, portal_cfg, player)
    if link is None:
        touching.discard(user_id)  # left the gate: the next touch is an edge
        return None
    if user_id in touching:
        return None  # already in contact: no re-fire (and no bounce)
    last = getattr(rt, "_portal_teleport_at", {}).get(user_id)
    if last is not None and (_time.monotonic() - last) < _GATE_COOLDOWN_SECONDS:
        return None  # just teleported: grace window
    touching.add(user_id)
    if not hasattr(rt, "_portal_teleport_at"):
        rt._portal_teleport_at = {}
    rt._portal_teleport_at[user_id] = _time.monotonic()
    return link, player


def move_player_between_runtimes(
    src_rt, dst_rt, user_id: int, to_tile: tuple
):
    """Move one Player + PlayerScreen from ``src_rt`` to ``dst_rt``.

    Pure state move (no Discord I/O): the player object migrates wholesale,
    the screen keeps its Discord message ids and gets a fresh camera for the
    destination map. Panels are dropped (they belong to the old world).
    """
    player = src_rt.state.players.pop(user_id, None)
    if player is None:
        return None
    player.x, player.y = int(to_tile[0]), int(to_tile[1])
    # Teleports are tile-based: re-centre the continuous (web) position and
    # clear the continuous-moved flag so the save writes the new tile.
    player.float_moved = False
    player.sync_float_from_int()
    dst_rt.state.players[user_id] = player
    # Previous-position bookkeeping belongs to the old world.
    dst_rt.state.previous_positions.pop(user_id, None)

    member = src_rt.members.pop(user_id, None)
    if member is not None:
        dst_rt.members[user_id] = member

    screen = src_rt.screens.pop(user_id, None)
    if screen is not None:
        screen.camera = Camera.auto(dst_rt.map_data)
        screen.composite = None
        screen.clear_panels()
        screen.travel_steps = 0
        dst_rt.screens[user_id] = screen
    # Arriving ON a gate tile (the interior mat IS the arrival spot): seed the
    # mover as ALREADY in contact so the edge detector in
    # check_portal_after_move cannot fire on arrival (no instant bounce).
    # Stepping off clears it; touching the gate again teleports.
    if not hasattr(dst_rt, _PORTAL_LATCH_ATTR):
        setattr(dst_rt, _PORTAL_LATCH_ATTR, set())
    getattr(dst_rt, _PORTAL_LATCH_ATTR).add(user_id)

    # The source runtime keeps running (its other players are unaffected);
    # nothing else to clean — inventories are keyed by user and stay on the
    # channel-level inventory maps (each side runtime carries its own dict,
    # so mirror the bag across both worlds).
    src_inv = src_rt.inventories.get(user_id)
    if src_inv is not None:
        dst_rt.inventories[user_id] = src_inv
    # WEB SESSION MIGRATION: the 20Hz control loop integrates input in the
    # runtime holding the session. Leaving the session behind (this was the
    # bug) froze the player server-side while their client kept predicting
    # forward — every click "Quá xa", reload "snapped" them far back.
    sess = getattr(src_rt, "web_sessions", {}).pop(user_id, None)
    if sess is not None:
        sess.last_tick = 0.0  # re-anchored on the next tick, no stale dt
        dst_rt.web_sessions[user_id] = sess
    return player
