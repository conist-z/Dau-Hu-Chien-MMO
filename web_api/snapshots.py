"""State serialization for the web client: welcome payloads + 20 Hz snapshots.

Pure read-only projection of ScenarioRuntime (rendering must never mutate
state, rule 3 — this module only reads). Positions are floats for web
rendering plus the int tile so the client can mirror chat-client visuals.
"""
from __future__ import annotations

import time as _time

from typing import Dict, List

from game.crafting import RECIPE_REGISTRY
from game.manager import ScenarioRuntime
from game.zombies import iter_web_zombies
from rendering.daynight import ingame_seconds

_PLAYERS_MANIFEST_CACHE: Dict | None = None


def _tilesets_payload(rt: ScenarioRuntime) -> List[dict]:
    """Tileset image references for the client (fetched via asset_request)."""
    out = []
    for i, ts in enumerate(rt.map_data.tilesets or []):
        img_path = ts.get("image_path")
        out.append({
            "index": i,
            "firstgid": ts.get("firstgid", 1),
            "columns": ts.get("columns", 1),
            "tilewidth": ts.get("tilewidth", 32),
            # Basename only: the client requests <name>.png through the
            # asset_request frame (no filesystem paths leak to the client).
            "image": img_path.name if img_path is not None else None,
        })
    return out


def _held_of(rt: ScenarioRuntime, user_id: int) -> str | None:
    """Item id currently HELD by ``user_id`` (hotbar slot they point at).

    Read-only projection: the held slot comes from ``rt.held_slots`` (mirrored
    from web select_slot frames, default 0); the item is that slot of the
    ordered-bag hotbar projection. None = empty hand (still renders the hand
    dot, just no tool icon — plan A).
    """
    slot = 0
    try:
        slot = int(getattr(rt, "held_slots", {}).get(user_id, 0) or 0)
    except (TypeError, ValueError):
        slot = 0
    inv = rt.inventories.get(user_id)
    if inv is None:
        return None
    try:
        return inv.hotbar().get(max(0, slot))
    except Exception:
        return None


def _zombies_payload(rt: ScenarioRuntime) -> List[list]:
    """WEB-pack zombies: [id, x, y, hp, max_hp, kind, facing, anim].

    Float x/y (tile units) so the client interpolates smoothly at 60 fps.
    facing (N/S/E/W/NE/NW/SE/SW) + anim ("walk"|"idle"|"atk") are
    server-authoritative: the client cuts the matching row/frame from its own
    zombie sheet copy instead of stretching the whole sheet.
    """
    out = []
    for z in iter_web_zombies(rt.state):
        try:
            out.append([
                z.zombie_id,
                round(z.x_f, 3),
                round(z.y_f, 3),
                int(z.hp),
                int(z.max_hp),
                "hunter" if getattr(z, "hunter", False) else "walker",
                str(getattr(z, "facing", "S")),
                str(getattr(z, "anim", "idle")),
            ])
        except Exception:
            continue
    return out


def _players_payload(rt: ScenarioRuntime, exclude_user_id: int = 0) -> List[dict]:
    out = []
    for p in rt.state.get_visible_players():
        if p.user_id == exclude_user_id:
            continue  # self is rendered client-side (prediction) — never as a remote body
        out.append({
            "id": p.user_id,
            "name": p.display_name,
            "x": round(p.x_f, 3),
            "y": round(p.y_f, 3),
            "tile": [p.x, p.y],
            "dir": p.direction,
            "sprite": p.sprite_id,
            "web": p.is_web,
            "held": _held_of(rt, p.user_id),
        })
    return out


def _blocks_payload(rt: ScenarioRuntime) -> List[list]:
    return [[x, y, bid] for (x, y), bid in rt.state.blocks.items()]


def _inventory_payload(rt: ScenarioRuntime, user_id: int) -> dict:
    inv = rt.inventories.get(user_id)
    items: Dict[str, int] = inv.items if inv is not None else {}
    hotbar = inv.hotbar() if inv is not None else {}
    return {
        "bag": [{"id": iid, "qty": qty} for iid, qty in items.items()],
        "hotbar": [hotbar.get(s) for s in range(len(hotbar))],
    }


def _recipes_payload() -> List[dict]:
    return [
        {
            "id": r.id,
            "name": r.name,
            "emoji": r.emoji,
            "inputs": [{"id": iid, "qty": qty} for iid, qty in r.inputs],
            "output": {"id": r.output[0], "qty": r.output[1]},
            "needs_table": r.requires_table,
            "description": r.description,
        }
        for r in RECIPE_REGISTRY.values()
    ]


def _item_emojis_payload() -> Dict[str, str]:
    """Item id -> emoji, straight from the server registries (data-driven).

    The web client renders bag/hotbar icons from this map so every item the
    player has EVER received shows its proper icon — the client-side map can
    never drift out of sync with game/items.py + game/blocks.py again.
    """
    from game.blocks import BLOCK_REGISTRY
    from game.items import ITEM_REGISTRY

    out: Dict[str, str] = {}
    for iid, item in ITEM_REGISTRY.items():
        out[iid] = item.emoji
    for bid, block in BLOCK_REGISTRY.items():
        out.setdefault(bid, block.emoji)
    return out


def build_welcome(rt: ScenarioRuntime, user_id: int) -> dict:
    """The full initial payload after join: map + self + economy + recipes."""
    md = rt.map_data
    player = rt.state.get_player(user_id)
    return {
        "type": "welcome",
        # Paperdoll manifest: frame grid + animation rows for the player
        # sheets (assets/players). Static data — read once per welcome.
        "players_manifest": _players_manifest_payload(),
        "map": {
            "id": md.map_id,
            "name": md.display_name or md.map_id,
            "width": md.width,
            "height": md.height,
            "tile_width": md.tile_width,
            "tile_height": md.tile_height,
            "collision": md.collision,
            "layers": [
                {"name": name, "data": grid}
                for name, grid in md.tile_layers
            ],
            "tilesets": _tilesets_payload(rt),
            "spawn": list(md.spawn),
        },
        "self": {
            "id": user_id,
            "name": player.display_name if player else "",
            "x": round(player.x_f, 3) if player else 0.5,
            "y": round(player.y_f, 3) if player else 0.5,
            "hp": player.hp if player else 0,
            "max_hp": player.max_hp if player else 100,
            "mana": player.mana if player else 0,
            "max_mana": player.max_mana if player else 50,
            "coins": player.coins if player else 0,
            "walk_speed": _walk_speed(),
            "run_speed": _run_speed(),
            "dir": player.direction if player else "SOUTH",
        },
        "inventory": _inventory_payload(rt, user_id),
        "recipes": _recipes_payload(),
        # Item id -> emoji for the client's inventory/hotbar icons.
        "item_emojis": _item_emojis_payload(),
        # What THIS player holds right now (hotbar slot -> item id). The
        # client renders its own hand instantly from the local hotbar, but
        # the echo + snapshot copy keep reconnects/welcome in sync.
        "held": _held_of(rt, user_id),
        # Placeable block catalog (id + emoji + name) for the build UI.
        "blocks_catalog": _blocks_catalog_payload(),
        "blocks": _blocks_payload(rt),
        # Resource node tiles (trees/bushes/ore) the client renders as a
        # separate layer so chopped nodes can disappear per node.
        "resources": _resource_tiles_payload(rt),
        "res_progress": _resource_progress_payload(rt),
        "res_felled": _resource_felled_payload(rt),
        "players": _players_payload(rt, user_id),
        "zombies": _zombies_payload(rt),
    }


def _players_manifest_payload() -> dict:
    """Paperdoll manifest (frame grid + rows + speeds) for the web client.

    Pure static data from assets/players/players_manifest.json; read lazily
    and cached so welcome building stays cheap.
    """
    global _PLAYERS_MANIFEST_CACHE
    if _PLAYERS_MANIFEST_CACHE is None:
        import json
        from config import ASSETS_DIR

        path = ASSETS_DIR.parent / "players" / "players_manifest.json"
        try:
            with open(path, encoding="utf-8") as f:
                _PLAYERS_MANIFEST_CACHE = json.load(f)
        except OSError:
            _PLAYERS_MANIFEST_CACHE = {}
    return _PLAYERS_MANIFEST_CACHE


def _blocks_catalog_payload() -> List[dict]:
    """Placeable blocks for the web build UI (emoji + display name)."""
    from game.blocks import BLOCK_REGISTRY, PLACEABLE_BLOCK_IDS

    return [
        {
            "id": bid,
            "emoji": BLOCK_REGISTRY[bid].emoji,
            "name": BLOCK_REGISTRY[bid].name,
        }
        for bid in PLACEABLE_BLOCK_IDS
    ]


def build_snapshot(rt: ScenarioRuntime, user_id: int, seq: int) -> dict:
    """One 20 Hz world snapshot (per connected client, self-view included)."""
    player = rt.state.get_player(user_id)
    return {
        "type": "snapshot",
        "seq": seq,
        "map_id": rt.map_data.map_id,
        "clock": ingame_seconds() % 86400,
        "weather": rt.weather_key,
        "players": _players_payload(rt, user_id),
        "blocks": _blocks_payload(rt),
        "zombies": _zombies_payload(rt),
        "self": {
            "hp": player.hp if player else 0,
            "max_hp": player.max_hp if player else 100,
            "mana": player.mana if player else 0,
            "max_mana": player.max_mana if player else 50,
            "coins": player.coins if player else 0,
            "x": round(player.x_f, 3) if player else 0.5,
            "y": round(player.y_f, 3) if player else 0.5,
            # Dead flag + respawn countdown: the web client freezes its own
            # prediction and shows a death overlay instead of letting the
            # predicted marker keep walking (the server ignores dead inputs,
            # so reconciliation kept snapping the ghost back to a walkable
            # ring — the "vòng tròn vô hình" the player saw).
            "dead": bool(player is not None and not player.alive),
            "respawn_s": (
                max(0, round(player.dead_until - _time.time(), 1))
                if player is not None and player.dead_until is not None else 0
            ),
            # The client must target relative to THIS (its predicted float
            # position drifts; using it for offsets misplaced blocks).
            "tile": [player.x, player.y] if player else [0, 0],
            # Facing/aim data so the web client can draw its target square:
            # the player's 8-way direction plus the active Build-Mode cursor
            # offset when one exists.
            "dir": player.direction if player else "SOUTH",
            "aim": (
                {"dx": player.aim_dx, "dy": player.aim_dy}
                if player is not None and player.aim_active
                else None
            ),
            # What I hold (echo of the hotbar slot). Remote hands come from
            # each entry of `players[].held`; self uses this (no clone entry).
            "held": _held_of(rt, user_id),
        },
        "inventory": _inventory_payload(rt, user_id),
        # Resource nodes (trees/bushes/ore): every VISIBLE tile as (x, y, gid)
        # plus per-node chop progress "ax,ay" -> hits landed. The client draws
        # resource tiles from this (so chopped nodes disappear) and shows a
        # progress bar over the node being harvested.
        "resources": _resource_tiles_payload(rt),
        "res_progress": _resource_progress_payload(rt),
        "res_felled": _resource_felled_payload(rt),
        # Night zombies (Kaetram-style mob): [id, x, y, hp, max_hp, kind].
        # Client interpolates + draws the sprite sheet (mobs/zombie.png via
        # asset_request) with walk/attack/death animation by state.
        "zombies": _zombies_payload(rt),
    }


def _resource_tiles_payload(rt) -> List[list]:
    grid = getattr(rt, "resources", None)
    if grid is None:
        return []
    return [[x, y, gid] for x, y, gid in grid.visible_tiles()]


def _resource_felled_payload(rt) -> List[list]:
    """Every tile covered by a FELLED node: [x, y, anchor_x, anchor_y].

    The client marks these tiles WALKABLE in its local prediction (mirrors
    the server's Collision rule: a felled tree/ore frees its tiles until it
    regrows) and can re-show the correct visual state on rebake.
    """
    grid = getattr(rt, "resources", None)
    if grid is None:
        return []
    out: List[list] = []
    for anchor in grid.chopped_at:
        node = grid.nodes.get(anchor)
        if node is None:
            continue
        for x, y in node.tiles:
            out.append([x, y, anchor[0], anchor[1]])
    return out


def _resource_progress_payload(rt) -> dict:
    """Chop progress per node in HARVEST: "ax,ay" -> [hits, needed, bbox].

    ``needed`` is the BASE hit count (bare hands); ``bbox`` is the node's
    full tile list (a big tree covers 2x2) so the client centres the progress
    bar over the WHOLE node — never just the anchor tile — and knows which
    tiles to animate when the tree falls. The action_result echo carries the
    tool-adjusted needed count, which the client prefers when present.
    """
    grid = getattr(rt, "resources", None)
    if grid is None:
        return {}
    from game.resources import NODE_DEFS

    out = {}
    for (ax, ay), hits in grid.progress.items():
        node = grid.nodes.get((ax, ay))
        if node is None or hits <= 0:
            continue
        out[f"{ax},{ay}"] = [hits, NODE_DEFS[node.kind].hits, node.tiles]
    return out


def _walk_speed() -> float:
    from config import WEB_WALK_SPEED

    return WEB_WALK_SPEED


def _run_speed() -> float:
    from config import WEB_RUN_SPEED

    return WEB_RUN_SPEED
