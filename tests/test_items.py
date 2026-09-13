import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.items import ITEM_REGISTRY, apply_effect, get_item
from game.state import Player


def test_get_item_known():
    assert get_item("potion_hp") is not None
    assert get_item("nope") is None


def test_apply_effect_heals_within_max():
    p = Player(1, "A", hp=50)
    ok, reason = apply_effect(p, "potion_hp")
    assert ok and reason == "ok"
    assert p.hp == 80  # 50 + 30


def test_apply_effect_caps_at_max():
    p = Player(1, "A", hp=95)
    apply_effect(p, "potion_hp")
    assert p.hp == 100


def test_apply_effect_unknown():
    p = Player(1, "A")
    ok, reason = apply_effect(p, "ghost")
    assert not ok and reason == "unknown_item"


def test_registry_types():
    assert ITEM_REGISTRY["potion_hp"].type == "consumable"
    assert ITEM_REGISTRY["key_stone"].type == "key"
