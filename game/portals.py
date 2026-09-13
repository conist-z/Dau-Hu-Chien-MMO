"""Data-driven portal/travel config (the /khutraodoi trade lobby).

The config lives in ``assets/maps/portals.json`` (rule 10: all maps are
data-driven) and describes, per map:

- ``spawn``: where players arrive. Either ``"spawn_layer"`` (use the map's
  first tile on its ``spawn(...)`` Tiled layer) or an explicit ``[[x, y]]``
  list of arrival tiles.
- ``destinations``: teleport links keyed by the DESTINATION map id. Each link
  has the ``portal_tiles`` of THIS map that trigger it (stepping on one is a
  teleport, RPG-portal style) and the ``target`` arrival spot on the other
  side (``"spawn_layer"`` or one ``[x, y]`` tile).

Example (the MVP wiring):

    lobbytrade doors (30,24)(30,25)(38,24)(38,25)
      -> montertradebase thảm đất (8,15)(9,15)
    montertradebase thảm đất -> lobbytrade (34,26)   (in front of the door)

Teleport anti-loop: after arriving ON a portal tile of the destination map,
the portal does not fire again until the player steps OFF every portal tile
of that map (see ``Portals`` below).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class PortalLink:
    """One teleport link FROM a map TO another."""
    map_id: str
    # Arrival tiles on the destination map.
    target: Tuple[Tuple[int, int], ...]
    # Tiles on the SOURCE map that trigger this link when stepped on.
    portal_tiles: Tuple[Tuple[int, int], ...]
    # True when ``target`` is the "spawn_layer" marker: the caller resolves
    # the real arrival tile from the destination map's spawn Tiled layer.
    target_is_layer: bool = False


@dataclass
class MapPortals:
    """Portal config for one map."""
    # Arrival tiles ("" spawn_layer marker already resolved by the loader).
    spawn: Tuple[Tuple[int, int], ...] = ()
    # Destination map id -> link.
    destinations: Dict[str, PortalLink] = field(default_factory=dict)
    # All trigger tiles of this map (union of every link's portal_tiles).
    trigger_tiles: frozenset = frozenset()


class Portals:
    """Reads portals.json and answers portal queries. Pure data, no Discord."""

    def __init__(self, config: dict):
        self.maps: Dict[str, MapPortals] = {}
        for map_id, cfg in (config or {}).items():
            spawn_cfg = cfg.get("spawn")
            spawn: Tuple[Tuple[int, int], ...] = ()
            if isinstance(spawn_cfg, list):
                spawn = tuple((int(x), int(y)) for x, y in spawn_cfg)
            destinations: Dict[str, PortalLink] = {}
            triggers = set()
            for dest_id, link in (cfg.get("destinations") or {}).items():
                target_cfg = link.get("target")
                if target_cfg == "spawn_layer":
                    # Resolved lazily by the caller (needs the map loaded).
                    target: Tuple[Tuple[int, int], ...] = ()
                    target_is_layer = True
                elif target_cfg is None:
                    # No explicit target: arrival = the destination map's
                    # spawn layer (same lazy resolution as "spawn_layer").
                    target = ()
                    target_is_layer = True
                else:
                    target = (tuple(int(v) for v in target_cfg),)
                    target_is_layer = False
                tiles = tuple(
                    tuple(int(v) for v in t) for t in link.get("portal_tiles", [])
                )
                destinations[dest_id] = PortalLink(
                    map_id=link.get("map_id", dest_id),
                    target=target,
                    portal_tiles=tiles,
                    target_is_layer=target_is_layer,
                )
                triggers.update(tiles)
            self.maps[map_id] = MapPortals(
                spawn=spawn,
                destinations=destinations,
                trigger_tiles=frozenset(triggers),
            )

    @classmethod
    def load(cls, path: Path) -> "Portals":
        if not Path(path).exists():
            return cls({})
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def for_map(self, map_id: str) -> Optional[MapPortals]:
        return self.maps.get(map_id)

    def link_at(self, map_id: str, x: int, y: int) -> Optional[PortalLink]:
        """The link triggered by stepping on (x, y) of ``map_id``, if any."""
        mp = self.maps.get(map_id)
        if mp is None:
            return None
        if (x, y) not in mp.trigger_tiles:
            return None
        for link in mp.destinations.values():
            if (x, y) in link.portal_tiles:
                return link
        return None

    def is_trigger_tile(self, map_id: str, x: int, y: int) -> bool:
        mp = self.maps.get(map_id)
        return mp is not None and (x, y) in mp.trigger_tiles
