import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class NPCDef:
    id: str
    name: str
    emoji: str
    x: int
    y: int
    dialogue: Optional[str] = None


@dataclass
class DialogueOption:
    label: str
    next: Optional[str] = None
    effect: Dict[str, str] = field(default_factory=dict)


@dataclass
class DialogueNode:
    text: str
    options: List[DialogueOption] = field(default_factory=list)


@dataclass
class NpcMap:
    npcs: List[NPCDef]
    dialogues: Dict[str, DialogueNode]


def load_npcs(map_id: str, assets_dir: Path) -> NpcMap:
    path = assets_dir / f"{map_id}.npcs.json"
    if not path.exists():
        return NpcMap([], {})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return NpcMap([], {})
    npcs = [NPCDef(**n) for n in data.get("npcs", [])]
    dialogues: Dict[str, DialogueNode] = {}
    for k, v in data.get("dialogues", {}).items():
        opts = [
            DialogueOption(o.get("label", ""), o.get("next"), o.get("effect", {}))
            for o in v.get("options", [])
        ]
        dialogues[k] = DialogueNode(v.get("text", ""), opts)
    return NpcMap(npcs, dialogues)


def npc_adjacent(npcs: List[NPCDef], px: int, py: int) -> Optional[NPCDef]:
    """Return an NPC exactly one cardinal step away from (px, py)."""
    for n in npcs:
        if abs(n.x - px) + abs(n.y - py) == 1:
            return n
    return None


def get_node(npc_map: NpcMap, node_id: Optional[str]) -> Optional[DialogueNode]:
    if node_id is None:
        return None
    return npc_map.dialogues.get(node_id)
