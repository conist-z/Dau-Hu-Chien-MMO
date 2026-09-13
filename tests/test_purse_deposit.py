"""Purse deposit op + free-move currency regression tests."""
from __future__ import annotations

import asyncio

from game.purse import purse_add, purse_withdraw

from game.inventory import Inventory


class _FakeState:
    def __init__(self) -> None:
        self.players = {7: FakePlayer()}

    def get_player(self, uid):
        return self.players.get(uid)


class FakePlayer:
    def __init__(self) -> None:
        self.user_id = 7
        self.coins = 0
        self.crystals = 0


class FakeRuntime:
    def __init__(self) -> None:
        self.channel_id = 1
        self.inventories = {7: Inventory()}
        self.state = _FakeState()


class FakeManager:
    def __init__(self) -> None:
        self.rt = FakeRuntime()

    def get_runtime_for(self, _cid, _uid):
        return self.rt

    def get_inventory(self, _cid, uid):
        return self.rt.inventories[uid]

    def _notify_inventory_change(self, _cid, _uid):
        pass


def test_deposit_exact_qty_only() -> None:
    """purse_add banks EXACTLY qty from the FIRST stack (slot order) — other
    bag currency stays exactly where the player put it."""
    m = FakeManager()
    inv = m.get_inventory(1, 7)
    inv.slots[0] = ("coin", 5)
    inv.slots[3] = ("coin", 3)
    assert purse_add(m, 1, 7, "coin", 5)  # deposit the 5-stack
    assert inv.slots[0] is None           # first stack fully drained
    assert inv.slots[3] == ("coin", 3)    # untouched
    assert m.rt.state.get_player(7).coins == 5


def test_deposit_more_than_owned_clamps() -> None:
    m = FakeManager()
    inv = m.get_inventory(1, 7)
    inv.slots[0] = ("coin", 2)
    assert purse_add(m, 1, 7, "coin", 99)
    assert inv.count("coin") == 0
    assert m.rt.state.get_player(7).coins == 2


def test_currency_stack_survives_reorder_like_normal_item() -> None:
    """Currency in the bag is free: a move (simulated slot swap) keeps both
    stacks exactly where the player put them; the purse never changes."""
    m = FakeManager()
    inv = m.get_inventory(1, 7)
    inv.slots[0] = ("coin", 5)
    inv.slots[2] = ("wood", 9)
    # simulate a drag: coin from 0 -> 5 (swap with empty)
    inv.slots[5] = inv.slots[0]
    inv.slots[0] = None
    assert inv.slots[5] == ("coin", 5)
    assert m.rt.state.get_player(7).coins == 0  # never auto-banked


def test_withdraw_roundtrip() -> None:
    m = FakeManager()
    p = m.rt.state.get_player(7)
    p.coins = 2
    assert asyncio.run(purse_withdraw(m, 1, 7, "coin"))
    assert asyncio.run(purse_withdraw(m, 1, 7, "coin"))
    assert not asyncio.run(purse_withdraw(m, 1, 7, "coin"))  # purse empty
    inv = m.get_inventory(1, 7)
    assert inv.count("coin") == 2
    assert p.coins == 0
