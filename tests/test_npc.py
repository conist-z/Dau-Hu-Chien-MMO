import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from pathlib import Path

from game.npc import load_npcs, npc_adjacent

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "maps"


def test_load_npcs_bigmap():
    nm = load_npcs("bigmap", ASSETS)
    assert len(nm.npcs) == 1
    elder = nm.npcs[0]
    assert elder.id == "elder" and elder.x == 1 and elder.y == 0
    assert "elder_intro" in nm.dialogues


def test_npc_adjacent():
    nm = load_npcs("bigmap", ASSETS)
    # player at spawn (0,0) is adjacent to NPC at (1,0)
    assert npc_adjacent(nm.npcs, 0, 0).id == "elder"
    assert npc_adjacent(nm.npcs, 5, 5) is None


def test_load_npcs_missing():
    nm = load_npcs("does_not_exist_map", ASSETS)
    assert nm.npcs == [] and nm.dialogues == {}
