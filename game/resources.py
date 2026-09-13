"""Harvestable map nodes (trees / bushes) — pure game logic, no discord.

The resource nodes live in the Tiled layers whose name matches
``RESOURCE_LAYER_NAMES`` (the bigmap ships one named "cây"). A node is a
small tile cluster: a Pipoya big tree is a 2x2 block (gids 9,10 top row /
17,18 bottom row) and a bush is a single tile (gid 41). Chopping works per
NODE (anchor tile), never per coordinate — rule 17 (no hard-coded coords).

State per scenario: chop progress and regrow deadlines. Persistence and
Discord UI live in other layers (persistence/, discord_ui/).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from game.actions import ChopAction
from game.state import ActionResult, Direction, GameState

# Tiled layers that hold harvestable nodes (matched case/space-insensitively).
RESOURCE_LAYER_NAMES = {
    "cây", "cay", "tree", "trees", "resources",
    # Ore/rock veins live on the bigmap's misc-item layer.
    "vật phẩm ko liên quan", "vat pham ko lien quan", "ore", "ores", "mine",
    # Mineable rock nodes (bigmap layers "tảng đá nhỏ" / "tảng đá lớn").
    "tảng đá nhỏ", "tang da nho", "tảng đá lớn", "tang da lon",
    # NOTE: the dead-tree layer ("cây chết") is deliberately NOT here — its
    # art reuses the same Pipoya gids as the misc-item layer, so scanning it
    # would resurrect the minable-dead-tree bug (gid 47/48).
    # "cây cầu(đi qua được)" (the lobbytrade bridge) must never match "cây":
    # it is decor art on a walkable carve, not harvestable nodes.
}

# Data-driven tile registry: resource-layer GID -> (kind, dx, dy from anchor).
TILE_NODE_PARTS: Dict[int, Tuple[str, int, int]] = {
    9: ("tree", 0, 0),
    10: ("tree", 1, 0),
    17: ("tree", 0, 1),
    18: ("tree", 1, 1),
    41: ("bush", 0, 0),
    # Ore/rock vein tiles ("vật phẩm ko liên quan" layer on the bigmap):
    # mined with the ⛏️ pickaxe morph instead of the axe.
    # Gids 47/48 also draw dead-tree tiles on the "cây chết" layer (same
    # Pipoya art reused), so mapping them made dead trees minable by mistake.
    # Only keep the clearly-ore-only gids (44, 46).
    44: ("ore", 0, 0),
    46: ("ore", 0, 0),
    # Mineable rocks (bigmap layers "tảng đá nhỏ/gross"):
    # 65 = small rock, 66 = big rock (yields 1.5x stone, 1.4x hits).
    65: ("rock_small", 0, 0),
    66: ("rock_big", 0, 0),
}


@dataclass
class ResourceDef:
    """One harvestable node kind. Data-driven: tune without touching logic."""

    kind: str
    name: str
    hits: int  # axe presses needed to fell the node
    respawn_s: float  # seconds until the node regrows
    drops: List[Tuple[str, float, int]]  # (item_id, chance 0..1, qty)


# Ore/rock vein tiles ("vật phẩm ko liên quan") + mineable rocks.
NODE_DEFS: Dict[str, ResourceDef] = {
    "ore": ResourceDef(
        "ore",
        "Quặng",
        hits=5,
        respawn_s=420.0,
        # Always 1 stone, +60% for a second; smelting chain adds iron ore and
        # coal (game/smelting.py) plus the rare key_stone bonus.
        drops=[
            ("stone", 1.0, 1),
            ("stone", 0.6, 1),
            ("iron_ore", 0.4, 1),
            ("coal", 0.25, 1),
            ("key_stone", 0.08, 1),
        ],
    ),
    # Small rock (gid 65): user tune 14/09 — 12 wood-pickaxe swings or 65
    # bare-hand swings. Recipe table intentionally EMPTY (coming later).
    "rock_small": ResourceDef(
        "rock_small",
        "Đá",
        hits=12,
        respawn_s=420.0,
        drops=[("stone", 1.0, 1)],
    ),
    # Big rock (gid 66): +0.4x hits (17) and 1.5x yield (user rule).
    "rock_big": ResourceDef(
        "rock_big",
        "Đá lớn",
        hits=17,  # round(12 * 1.4)
        respawn_s=420.0,
        drops=[
            ("stone", 1.0, 1),
            ("stone", 0.5, 1),
        ],
    ),
    "tree": ResourceDef(
        "tree",
        "Cây",
        # Bare-hand swings to fell a tree (user tune 13/09: 12). Holding an
        # axe replaces this via axe_hits(material) — dirt 9, wood 6, stone 4,
        # iron 3 — so tools stay the fast path.
        hits=12,
        respawn_s=300.0,
        # Always 1 wood, +50% for a second; leaves/apples are chance rolls.
        drops=[("wood", 1.0, 1), ("wood", 0.5, 1), ("leaves", 0.6, 1), ("apple", 0.3, 1)],
    ),
    # Bushes give the same item KINDS but less (lower chances, no 2nd wood).
    "bush": ResourceDef(
        "bush",
        "Bụi cây",
        hits=3,
        respawn_s=180.0,
        drops=[("wood", 0.7, 1), ("leaves", 0.35, 1), ("apple", 0.12, 1)],
    ),
}


@dataclass
class ResourceNode:
    kind: str
    anchor: Tuple[int, int]  # top-left tile; chop progress is keyed by this
    tiles: List[Tuple[int, int]]  # every tile the sprite covers
class ResourceGrid:
    """Per-scenario harvestable-node state. Pure, no IO.

    Nodes are indexed once from the map (``from_map``); chopping progress and
    regrow deadlines are keyed by the node anchor tile.
    """

    def __init__(self):
        self.nodes: Dict[Tuple[int, int], ResourceNode] = {}
        self._tile_index: Dict[Tuple[int, int], ResourceNode] = {}
        self._tile_gids: Dict[Tuple[int, int], int] = {}
        # Lower-cased names of the resource layers found in the map (the
        # renderer excludes exactly these layers from its ground base).
        self.layer_names: set = set()
        self.progress: Dict[Tuple[int, int], int] = {}
        self.chopped_at: Dict[Tuple[int, int], float] = {}
        # Durability regen (user rule 13/09): a node nobody hits for
        # PROGRESS_REGEN_DELAY_S seconds slowly "rewinds" its chop progress
        # (one hit lost every PROGRESS_REGEN_STEP_S). Timestamps per anchor:
        # ``progress_hit_at`` = last hit (drives the delay),
        # ``progress_checked_at`` = last regen step (drives the drain).
        self.progress_hit_at: Dict[Tuple[int, int], float] = {}
        self.progress_checked_at: Dict[Tuple[int, int], float] = {}

    @classmethod
    def from_map(cls, map_data) -> "ResourceGrid":
        g = cls()
        for name, grid in getattr(map_data, "tile_layers", None) or []:
            if (name or "").strip().lower() not in RESOURCE_LAYER_NAMES:
                continue
            if "cau" in name:  # "cây cầu(đi qua được)" — bridge decor, not nodes
                continue
            g.layer_names.add((name or "").strip().lower())
            for y, row in enumerate(grid):
                for x, gid in enumerate(row):
                    if not gid:
                        continue
                    part = TILE_NODE_PARTS.get(gid)
                    if part is None:
                        continue
                    kind, dx, dy = part
                    anchor = (x - dx, y - dy)
                    if anchor not in g.nodes:
                        if kind == "tree":
                            ax, ay = anchor
                            tiles = [
                                (ax, ay), (ax + 1, ay),
                                (ax, ay + 1), (ax + 1, ay + 1),
                            ]
                        else:
                            tiles = [anchor]
                        g.nodes[anchor] = ResourceNode(kind, anchor, tiles)
                        for t in tiles:
                            g._tile_index[t] = g.nodes[anchor]
                    g._tile_gids[(x, y)] = gid
        return g

    # ----- lookups -----

    def node_at(self, x: int, y: int) -> Optional[ResourceNode]:
        return self._tile_index.get((x, y))

    def is_chopped(self, anchor: Tuple[int, int]) -> bool:
        return anchor in self.chopped_at

    def progress_at(self, anchor: Tuple[int, int]) -> int:
        return self.progress.get(anchor, 0)

    def visible_tiles(self) -> List[Tuple[int, int, int]]:
        """(x, y, gid) for every resource tile NOT on a chopped node."""
        out = []
        for (x, y), node in self._tile_index.items():
            if node.anchor in self.chopped_at:
                continue
            out.append((x, y, self._tile_gids.get((x, y), 0)))
        return out

    def node_tiles_gids(self, node: ResourceNode) -> List[Tuple[int, int, int]]:
        """(x, y, gid) for every tile of a node — lets the UI fade the EXACT
        sprite that was chopped (rule 17: no hard-coded tile lists)."""
        out = []
        for x, y in node.tiles:
            gid = self._tile_gids.get((x, y), 0)
            if gid:
                out.append((x, y, gid))
        return out

    # ----- state changes -----

    def chop(self, anchor: Tuple[int, int], ts: float) -> None:
        self.chopped_at[anchor] = float(ts)
        self.progress.pop(anchor, None)
        self.progress_hit_at.pop(anchor, None)
        self.progress_checked_at.pop(anchor, None)

    def note_progress_hit(self, anchor: Tuple[int, int], now: float) -> None:
        """Record a landed hit on ``anchor`` (restarts the 5 s regen delay)."""
        self.progress_hit_at[anchor] = float(now)

    def decay_progress(self, now: Optional[float] = None) -> None:
        """Durability regen beat (call every tick): after PROGRESS_REGEN_DELAY_S
        without a hit, a node's chop progress "rewinds" — one hit lost every
        PROGRESS_REGEN_STEP_S (smooth reverse, not a sudden wipe).
        """
        if not self.progress:
            return
        if now is None:
            now = time.time()
        for anchor in list(self.progress.keys()):
            hit = self.progress_hit_at.get(anchor)
            if hit is None:
                # Restored-from-DB progress without a hit stamp: just arm the
                # clock (never wipe it on the first beat).
                self.progress_hit_at[anchor] = now
                self.progress_checked_at[anchor] = now
                continue
            if now - hit <= PROGRESS_REGEN_DELAY_S:
                continue
            checked = self.progress_checked_at.get(anchor, hit + PROGRESS_REGEN_DELAY_S)
            start = max(checked, hit + PROGRESS_REGEN_DELAY_S)
            steps = int((now - start) // PROGRESS_REGEN_STEP_S)
            if steps <= 0:
                continue
            remaining = self.progress.get(anchor, 0) - steps
            if remaining <= 0:
                self.progress.pop(anchor, None)
                self.progress_hit_at.pop(anchor, None)
                self.progress_checked_at.pop(anchor, None)
            else:
                self.progress[anchor] = remaining
                self.progress_checked_at[anchor] = start + steps * PROGRESS_REGEN_STEP_S

    def mark_chopped(self, anchor: Tuple[int, int], ts: float) -> None:
        """Restore a persisted chop (boot recovery) without touching progress."""
        self.chopped_at[anchor] = float(ts)

    def regrow(self, anchor: Tuple[int, int]) -> None:
        self.chopped_at.pop(anchor, None)
        self.progress.pop(anchor, None)
        self.progress_hit_at.pop(anchor, None)
        self.progress_checked_at.pop(anchor, None)

    def regrow_ready(self, now: float) -> List[Tuple[int, int]]:
        """Anchors whose respawn deadline has elapsed at unix ``now``."""
        out = []
        for anchor, ts in list(self.chopped_at.items()):
            node = self.nodes.get(anchor)
            if node is None or now >= ts + NODE_DEFS[node.kind].respawn_s:
                out.append(anchor)
        return out

    def reset(self) -> None:
        self.progress.clear()
        self.chopped_at.clear()
        self.progress_hit_at.clear()
        self.progress_checked_at.clear()


def _facing_tile(player) -> Tuple[int, int]:
    dx, dy = Direction[player.direction].vector
    return player.x + dx, player.y + dy


CHOP_RANGE = 3
# Web mouse-tile offset clamp (mirrors rules.AIM_RANGE without a cycle).
AIM_RANGE = 3

# Node durability regen (user rule 13/09): after PROGRESS_REGEN_DELAY_S
# without a landed hit, chop progress "rewinds" one hit every
# PROGRESS_REGEN_STEP_S (a 5-hit node fully resets 5 s + 5 s later). Tune
# here without touching logic.
PROGRESS_REGEN_DELAY_S = 5.0
PROGRESS_REGEN_STEP_S = 1.0  # one hit lost per this many seconds

# Node kinds harvested with a PICKAXE instead of the axe (ore/rock veins).
ORE_NODE_KINDS = {"ore", "rock", "stone_node", "iron_ore", "coal",
                  "rock_small", "rock_big"}


def is_ore_kind(kind: str) -> bool:
    """True when a node kind is mined with the pickaxe (⛏️) tool morph."""
    return kind in ORE_NODE_KINDS


# Pickaxe swings to mine ONE rock, per pickaxe material (user tune 14/09:
# wood = 12, NOT the generic pickaxe ladder's 5). Big rock scales 1.4x.
ROCK_PICKAXE_HITS = {"wood": 12, "iron": 8, "gold": 5, "steel": 3}


def _target_tile(player) -> Tuple[int, int]:
    """The square the player aims at: the Build-Mode aim cursor tile while one
    is active (the renderer highlights exactly this square), otherwise the
    facing tile."""
    if getattr(player, "aim_active", False):
        return player.x + player.aim_dx, player.y + player.aim_dy
    return _facing_tile(player)


def _nearest_node_in_range(grid: ResourceGrid, player, max_range: int):
    nodes = [
        node for node in grid.nodes.values()
        if not grid.is_chopped(node.anchor)
        and any(
            max(abs(x - player.x), abs(y - player.y)) <= max_range
            for x, y in node.tiles
        )
    ]
    return min(
        nodes,
        key=lambda node: min(
            max(abs(x - player.x), abs(y - player.y)) for x, y in node.tiles
        ),
        default=None,
    )


def apply_chop(state: GameState,
    action: ChopAction,
    grid: Optional[ResourceGrid],
    inventory,
    rng=None,
    now: Optional[float] = None,
) -> ActionResult:
    """One swing at the node on the target tile.

    The tool family follows the node kind: ore/rock veins are mined with a
    PICKAXE, trees/bushes are felled with an AXE. The strongest held tool of
    that family (action.tool_id when explicitly given) decides the number of
    presses: each tier up cuts the required hits (game/tools.py).

    STONE GATE (user rule): ore nodes can only be mined while the player
    holds a pickaxe of dirt tier or better — anyone else gets the
    "too_hard" reason and the UI shows "Quá cứng để đập bằng tay".

    Each landed press adds progress (``state_changed=True`` so the UI can
    refresh the % label). At ``hits`` the node is felled: its tiles are
    hidden until the respawn deadline elapses and the drop table rolls into
    the player's bag (``drops`` is set only on the felling swing).
    """
    player = state.get_player(action.user_id)
    if player is None:
        return ActionResult(False, "no_player")
    if not player.alive:
        return ActionResult(False, "dead")
    if not player.visible:
        return ActionResult(False, "invisible")
    if grid is None:
        return ActionResult(False, "no_node")

    # Chop acts on the TARGET square only (the highlighted tile): the
    # aim-cursor tile while Build Mode aims elsewhere, the mouse tile when
    # the web client passes an offset, otherwise the facing tile — never
    # "nearest node in range".
    cdx = getattr(action, "dx", None)
    cdy = getattr(action, "dy", None)
    if cdx is not None or cdy is not None:
        dx = int(cdx or 0)
        dy = int(cdy or 0)
        if max(abs(dx), abs(dy)) > AIM_RANGE:
            return ActionResult(False, "out_of_range")
        tx, ty = player.x + dx, player.y + dy
    else:
        tx, ty = _target_tile(player)
    node = grid.node_at(tx, ty)
    if node is None:
        return ActionResult(False, "no_node")
    if grid.is_chopped(node.anchor):
        now = time.time() if now is None else now
        return ActionResult(False, "regrowing")

    from game import tools as tool_mod
    from game.tools import parse_tool_id

    ore = is_ore_kind(node.kind)
    family = "pickaxe" if ore else "axe"
    tool_id = action.tool_id
    if tool_id is not None:
        td = parse_tool_id(tool_id)
        if td is None or td.family != family or inventory.count(tool_id) <= 0:
            tool_id = None
    if tool_id is None:
        tool_id = tool_mod.best_tool_of_family(inventory, family)

    # Pickaxe GATE applies to ORE veins only: rocks (rock_small/rock_big)
    # are mineable BARE-HANDED — just brutally slow (65/91 swings, user rule
    # 14/09: "vẫn tay không đập được nhưng mà cực lâu").
    if node.kind == "ore" and not tool_mod.has_pickaxe_tier(inventory, "wood"):
        return ActionResult(False, "too_hard")

    tool = parse_tool_id(tool_id) if tool_id else None
    if node.kind in ("rock_small", "rock_big"):
        # Rocks use their OWN per-kind hit table (user tune 14/09: 12 swings
        # with a WOOD pickaxe, 65 bare-hand) — NOT the generic pickaxe ladder
        # (whose wood tier is 5). Each tier up still mines faster.
        scale = 1.4 if node.kind == "rock_big" else 1.0
        if tool is not None:
            hits = round(ROCK_PICKAXE_HITS.get(tool.material, 12) * scale)
        else:
            hits = round((65 if node.kind == "rock_small" else 65 * 1.4))
    else:
        hits_fn = tool_mod.pickaxe_hits if ore else tool_mod.axe_hits
        if tool is not None:
            hits = hits_fn(tool.material)
        else:
            hits = NODE_DEFS[node.kind].hits

    now = time.time() if now is None else now
    rng = rng if rng is not None else random
    grid.progress[node.anchor] = grid.progress_at(node.anchor) + 1
    # Durability regen: stamp the hit so the 5 s rewind clock restarts.
    grid.note_progress_hit(node.anchor, now)
    if grid.progress[node.anchor] < hits:
        return ActionResult(
            True, state_changed=True, pos=(tx, ty), block_id=node.kind, needed=hits,
        )

    grid.chop(node.anchor, now)
    drops: List[Tuple[str, int]] = []
    for item_id, chance, qty in NODE_DEFS[node.kind].drops:
        if rng.random() < chance:
            drops.append((item_id, qty))
    # Felled: the loot pops out as "linh khí" drop entities at the node
    # centre (the manager grants the bag only when a player collects them).
    from game.drops import spawn_drops

    if drops:
        cx = sum(x for x, _y in node.tiles) / len(node.tiles)
        cy = sum(y for _x, y in node.tiles) / len(node.tiles)
        spawn_drops(state, cx, cy, drops, rng=rng)
    return ActionResult(
        True, state_changed=True, pos=(tx, ty), block_id=node.kind, drops=drops,
        needed=hits,
    )


def render_kwargs(rt) -> dict:
    """Extra renderer kwargs so every screen render hides chopped nodes.

    UI-agnostic on purpose: ``rt`` is duck-typed (needs ``.resources``).
    """
    grid = getattr(rt, "resources", None)
    if grid is None:
        return {}
    return {
        "resource_tiles": grid.visible_tiles(),
        "resource_layer_names": grid.layer_names,
    }