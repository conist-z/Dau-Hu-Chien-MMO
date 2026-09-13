import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.inventory import Inventory
from game.items import apply_effect
from game.state import Player


def test_add_remove():
    inv = Inventory()
    inv.add("potion_hp", 2)
    assert inv.count("potion_hp") == 2
    assert inv.remove("potion_hp", 1)
    assert inv.count("potion_hp") == 1
    assert inv.remove("potion_hp", 1)
    assert inv.is_empty()


def test_remove_more_than_have():
    inv = Inventory()
    inv.add("potion_hp", 1)
    assert not inv.remove("potion_hp", 5)
    assert inv.count("potion_hp") == 1


def test_use_consumable():
    inv = Inventory()
    inv.add("potion_hp", 1)
    p = Player(1, "A", hp=40)
    ok, reason = inv.use("potion_hp", p)
    assert ok and p.hp == 70
    assert inv.is_empty()


def test_use_empty():
    inv = Inventory()
    p = Player(1, "A")
    ok, reason = inv.use("potion_hp", p)
    assert not ok and reason == "empty"
