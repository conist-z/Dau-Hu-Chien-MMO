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

# Runtime attribute: per-player "standing on a portal tile" latch (anti-loop).
_PORTAL_LATCH_ATTR = "on_portal_tile"

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


def free_arrival_tile(rt, candidates, occupied) -> tuple:
    """First walkable, unoccupied arrival tile; any walkable tile as fallback;
    the first candidate as last resort (interiors are mostly walkable, so the
    first branch wins in practice)."""
    for x, y in candidates:
        if rt.collision.is_walkable(x, y) and (x, y) not in occupied:
            return (x, y)
    for x, y in candidates:
        if rt.collision.is_walkable(x, y):
            return (x, y)
    return tuple(candidates[0]) if candidates else (0, 0)


def portal_link_for(rt, portal_cfg: Portals, x: int, y: int) -> PortalLink | None:
    """The teleport link triggered by standing on (x, y) of rt's map."""
    return portal_cfg.link_at(rt.map_data.map_id, x, y)


def check_portal_after_move(rt, portal_cfg: Portals, user_id: int,
                            moved_off_portal: bool = False):
    """Called after a successful move. Returns the (link, player) pair when a
    teleport must fire, else None.

    Anti-loop latch: arriving ON a portal tile (the interior mat IS the
    arrival AND the trigger) does not re-fire until the player steps off
    every portal tile of the current map.

    ``moved_off_portal`` (web tick path): True when the player's PREVIOUS
    position was off every portal tile — web movement reports a target
    every flush, so "standing still on the door, wanting the teleport" is
    a legitimate re-entry; the latch alone would swallow it forever (the
    "đi xuyên cửa" bug). With this the latch only blocks BACK-TO-BACK
    teleports within continuous portal contact, same as Discord.
    """
    player = rt.state.get_player(user_id)
    if player is None:
        return None
    link = portal_link_for(rt, portal_cfg, player.x, player.y)
    if not hasattr(rt, _PORTAL_LATCH_ATTR):
        setattr(rt, _PORTAL_LATCH_ATTR, set())
    latched = getattr(rt, _PORTAL_LATCH_ATTR)
    if link is None:
        # Off every portal tile: re-arm.
        if user_id in latched:
            latched.discard(user_id)
        return None
    if user_id in latched and not moved_off_portal:
        return None
    latched.add(user_id)
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
    # Arriving ON a portal tile (the interior mat IS the arrival spot): latch
    # the mover so the portal does not instantly bounce them back; it re-arms
    # when they step off every portal tile (check_portal_after_move).
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
