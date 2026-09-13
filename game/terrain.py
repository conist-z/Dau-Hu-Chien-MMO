"""Terrain overlay grid — scoopable grass ground ("mặt đất" layer).

The bigmap stacks THREE ground layers (bottom → top):
  1. "mặt đất không cỏ" — bare-dirt base, ALWAYS rendered (part of the map
     base layer; never excluded, never overlay).
  2. "mặt đất" — the grass ground. This is the scoopable layer. It STAYS in
     the renderer's baked base (so the map keeps its normal look and layer
     ordering); scooping a tile pastes the bare-dirt tile on top of it
     (user rule: khi đào lớp mặt đất đi thì show mặt đất không cỏ).
  3. "cỏ" — sparse grass tufts, decorative: excluded from the base and
     re-drawn as overlays so a scooped tile loses its tuft too.

Overlay blits (tufts + dirt covers) are composed UNDER the resource/tree
blits so trees never disappear behind the ground.

Pure game logic: no discord, no IO (AGENTS.md principles 2/4). Layer matching
is data-driven by ASCII-folded Tiled layer names — no hard-coded coordinates
(rule 17) and no hard-coded map sizes (rule 18).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Tiled layers that form the scoopable grass ground (exact folded names).
GROUND_LAYER_NAMES = frozenset({"mat dat", "grass", "ground"})
# Decorative tuft layers drawn above the ground (exact folded names); they
# vanish together with the ground tile they sit on.
TUFT_LAYER_NAMES = frozenset({"co", "grass tuft", "tuft"})

# The bare-dirt base underneath the grass ground ("mặt đất không cỏ"). It is
# NOT scoopable and NOT an overlay — the renderer must always keep it in the
# base so scooped ground tiles reveal it. Listed explicitly (rather than
# "anything else") so future ground-ish layers never silently escape the
# overlay treatment.
DIRT_BASE_LAYER_NAMES = frozenset({"mat dat khong co", "bare dirt", "dirt"})

# The item the player gets from scooping one grass tile (game/items.py).
GRASS_DROP_ITEM = "dirt"


def _fold(name: str) -> str:
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", name or "")
    # NFKD does NOT decompose Vietnamese đ (a distinct Unicode letter), so
    # transliterate it explicitly before stripping the combining marks.
    nfkd = nfkd.replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def is_dirt_base_layer(name: str) -> bool:
    """The always-rendered bare-dirt base ("mặt đất không cỏ") — never
    excluded, never drawn as an overlay."""
    return _fold(name or "").strip() in DIRT_BASE_LAYER_NAMES


def is_grass_layer(name: str) -> bool:
    """True for the scoopable ground layer AND its decorative tufts — both are
    excluded from the renderer's base and drawn as overlays. The bare-dirt
    base ("mặt đất không cỏ") is NOT a grass layer: it must always stay in
    the base render so scooped tiles reveal it."""
    nl = _fold(name or "").strip()
    return nl in TUFT_LAYER_NAMES


class TerrainGrid:
    """Per-scenario scoopable-ground state. Pure, no IO.

    The GRASS GROUND stays in the renderer's baked base (so the map keeps
    its normal look and layer ordering). Only the decorative tufts are
    excluded from the base and re-drawn as overlays. Scooping a ground tile
    makes the overlay paste the bare-dirt tile (gid taken from the "mặt đất
    không cỏ" base layer) ON TOP of that tile, covering the grass — the
    ground around it is untouched, and resources/tufts still paint above.
    """

    def __init__(self):
        # Ground tiles (from the "mặt đất" layer) — these are scoopable.
        self._ground_tiles: set = set()
        # (x, y) -> gid of the decorative tuft sprite on that tile.
        self._tile_gids: Dict[Tuple[int, int], int] = {}
        # (x, y) -> gid of the bare-dirt tile (from "mặt đất không cỏ") used
        # to COVER a scooped ground tile.
        self._bare_gids: Dict[Tuple[int, int], int] = {}
        # Lower-cased names of the tuft layers found (the renderer excludes
        # exactly these layers from its ground base; they come back as
        # overlays so a scooped tile's tuft vanishes with it).
        self.layer_names: set = set()
        # Scooped tiles (dirt cover applied).
        self.scooped: set = set()

    @classmethod
    def from_map(cls, map_data) -> "TerrainGrid":
        g = cls()
        for name, grid in getattr(map_data, "tile_layers", None) or []:
            nl = _fold(name or "").strip()
            if nl in TUFT_LAYER_NAMES:
                g.layer_names.add((name or "").strip().lower())
                for y, row in enumerate(grid):
                    for x, gid in enumerate(row):
                        if gid:
                            g._tile_gids[(x, y)] = gid
            elif nl in GROUND_LAYER_NAMES:
                for y, row in enumerate(grid):
                    for x, gid in enumerate(row):
                        if gid:
                            g._ground_tiles.add((x, y))
            elif nl in DIRT_BASE_LAYER_NAMES:
                for y, row in enumerate(grid):
                    for x, gid in enumerate(row):
                        if gid:
                            g._bare_gids[(x, y)] = gid
        return g

    # ----- lookups -----

    def is_grass(self, x: int, y: int) -> bool:
        return (x, y) in self._ground_tiles

    def is_scooped(self, x: int, y: int) -> bool:
        return (x, y) in self.scooped

    def scoopable(self, x: int, y: int) -> bool:
        """A tile can be scooped only when it is an UNSCOOPED ground tile
        (user rule: chỉ dùng được trên ô cỏ trống)."""
        return self.is_grass(x, y) and not self.is_scooped(x, y)

    def gid_at(self, x: int, y: int) -> Optional[int]:
        return self._tile_gids.get((x, y))

    def visible_tiles(self) -> List[Tuple[int, int, int]]:
        """Overlay blits, two groups:

        1. decorative tufts on tiles that are NOT scooped (the base no longer
           draws them, the overlay does — and a scooped tile loses its tuft);
        2. the bare-dirt COVER tile for every scooped ground tile (pasted on
           top of the base's grass ground).
        """
        out: List[Tuple[int, int, int]] = [
            (x, y, gid)
            for (x, y), gid in self._tile_gids.items()
            if (x, y) not in self.scooped
        ]
        out.extend(
            (x, y, self._bare_gids[(x, y)])
            for (x, y) in self.scooped
            if (x, y) in self._bare_gids
        )
        return out

    # ----- state changes -----

    def scoop(self, x: int, y: int) -> None:
        self.scooped.add((x, y))

    def reset(self) -> None:
        self.scooped.clear()


def render_kwargs(rt) -> dict:
    """Extra renderer kwargs so every screen render hides scooped grass.

    UI-agnostic on purpose: ``rt`` is duck-typed (needs ``.terrain``).
    Mirrors game/resources.render_kwargs. Missing terrain (test runtimes,
    maps without grass) renders nothing extra."""
    grid = getattr(rt, "terrain", None)
    if grid is None:
        return {"terrain_tiles": []}
    return {"terrain_tiles": grid.visible_tiles()}
