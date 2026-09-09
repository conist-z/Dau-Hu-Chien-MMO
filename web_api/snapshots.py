"""State serialization for the web client: welcome payloads + 20 Hz snapshots.

Pure read-only projection of ScenarioRuntime (rendering must never mutate
state, rule 3 — this module only reads). Positions are floats for web
rendering plus the int tile so the client can mirror chat-client visuals.
"""
from __future__ import annotations

from typing import Dict, List

from game.crafting import RECIPE_REGISTRY
from game.manager import ScenarioRuntime
from rendering.daynight import ingame_seconds


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
        # Placeable block catalog (id + emoji + name) for the build UI.
        "blocks_catalog": _blocks_catalog_payload(),
        "blocks": _blocks_payload(rt),
        # Resource node tiles (trees/bushes/ore) the client renders as a
        # separate layer so chopped nodes can disappear per node.
        "resources": _resource_tiles_payload(rt),
        "res_progress": _resource_progress_payload(rt),
        "players": _players_payload(rt, user_id),
    }


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
        "self": {
            "hp": player.hp if player else 0,
            "max_hp": player.max_hp if player else 100,
            "mana": player.mana if player else 0,
            "max_mana": player.max_mana if player else 50,
            "coins": player.coins if player else 0,
            "x": round(player.x_f, 3) if player else 0.5,
            "y": round(player.y_f, 3) if player else 0.5,
            # AUTHORITATIVE int tile the server resolves actions against.
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
        },
        "inventory": _inventory_payload(rt, user_id),
        # Resource nodes (trees/bushes/ore): every VISIBLE tile as (x, y, gid)
        # plus per-node chop progress "ax,ay" -> hits landed. The client draws
        # resource tiles from this (so chopped nodes disappear) and shows a
        # progress bar over the node being harvested.
        "resources": _resource_tiles_payload(rt),
        "res_progress": _resource_progress_payload(rt),
    }


def _resource_tiles_payload(rt) -> List[list]:
    grid = getattr(rt, "resources", None)
    if grid is None:
        return []
    return [[x, y, gid] for x, y, gid in grid.visible_tiles()]


def _resource_progress_payload(rt) -> dict:
    grid = getattr(rt, "resources", None)
    if grid is None:
        return {}
    return {
        f"{ax},{ay}": hits for (ax, ay), hits in grid.progress.items()
    }


def _walk_speed() -> float:
    from config import WEB_WALK_SPEED

    return WEB_WALK_SPEED


def _run_speed() -> float:
    from config import WEB_RUN_SPEED

    return WEB_RUN_SPEED
