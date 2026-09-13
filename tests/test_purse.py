"""Server tests: coin/crystal auto-purse + drag-out withdrawal.

- Any `coin`/`crystal` item landing in the bag is auto-converted into the
  player's purse counters (coins/crystals) — never sits as a stack.
- `purse_withdraw` pulls exactly 1 coin (or crystal) per request back into
  the bag, only when the counter can afford it.
"""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)

import pytest

from game.purse import CURRENCY_ITEM_IDS, purse_add, purse_withdraw

from game.inventory import Inventory


class FakeRuntime:
    def __init__(self) -> None:
        self.channel_id = 1
        self.inventories = {7: Inventory()}
        self.state = _FakeState()


class FakeManager:
    def __init__(self) -> None:
        self.rt = FakeRuntime()
        self.notified: list[int] = []

    def get_runtime_for(self, _cid, _uid):
        return self.rt

    def get_inventory(self, _cid, uid):
        return self.rt.inventories[uid]

    def _notify_inventory_change(self, _cid, uid):
        self.notified.append(uid)


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


def test_currency_ids() -> None:
    assert set(CURRENCY_ITEM_IDS) == {"coin", "crystal"}


def test_purse_add_converts_stack_to_counter() -> None:
    m = FakeManager()
    inv = m.get_inventory(1, 7)
    inv.add("coin", 25)
    changed = purse_add(m, 1, 7, "coin", 25)
    assert changed  # conversion happened -> caller must notify
    assert inv.count("coin") == 0  # drained from the bag
    p = m.rt.state.get_player(7)
    assert p.coins == 25


def test_purse_add_ignores_normal_items() -> None:
    m = FakeManager()
    inv = m.get_inventory(1, 7)
    inv.add("wood", 5)
    purse_add(m, 1, 7, "wood", 5)
    assert inv.count("wood") == 5  # untouched
    p = m.rt.state.get_player(7)
    assert p.coins == 0 and p.crystals == 0


def test_withdraw_one_coin() -> None:
    m = FakeManager()
    p = m.rt.state.get_player(7)
    p.coins = 3
    loop = asyncio.new_event_loop()
    ok = loop.run_until_complete(purse_withdraw(m, 1, 7, "coin"))
    loop.close()
    assert ok
    inv = m.get_inventory(1, 7)
    assert inv.count("coin") == 1
    assert p.coins == 2


def test_withdraw_empty_purse_fails() -> None:
    m = FakeManager()
    p = m.rt.state.get_player(7)
    p.coins = 0
    loop = asyncio.new_event_loop()
    ok = loop.run_until_complete(purse_withdraw(m, 1, 7, "coin"))
    loop.close()
    assert not ok
    inv = m.get_inventory(1, 7)
    assert inv.count("coin") == 0


def test_withdraw_crystal_uses_crystal_counter() -> None:
    m = FakeManager()
    p = m.rt.state.get_player(7)
    p.coins = 10
    p.crystals = 2
    loop = asyncio.new_event_loop()
    ok = loop.run_until_complete(purse_withdraw(m, 1, 7, "crystal"))
    loop.close()
    assert ok
    p2 = m.rt.state.get_player(7)
    assert p2.crystals == 1 and p2.coins == 10  # coins untouched


def test_withdraw_into_specific_slot() -> None:
    m = FakeManager()
    p = m.rt.state.get_player(7)
    p.coins = 5
    inv = m.get_inventory(1, 7)
    inv.slots[3] = ("coin", 4)   # existing coin stack at slot 3
    ok = _run(
        purse_withdraw(m, 1, 7, "coin", slot=3))
    assert ok
    assert inv.slots[3] == ("coin", 5)   # merged onto the stack
    assert p.coins == 4


def test_withdraw_into_empty_slot() -> None:
    m = FakeManager()
    p = m.rt.state.get_player(7)
    p.crystals = 2
    inv = m.get_inventory(1, 7)
    ok = _run(
        purse_withdraw(m, 1, 7, "crystal", slot=1))
    assert ok
    assert inv.slots[1] == ("crystal", 1)
    assert p.crystals == 1


def test_withdraw_rejected_on_different_item_slot() -> None:
    m = FakeManager()
    p = m.rt.state.get_player(7)
    p.coins = 3
    inv = m.get_inventory(1, 7)
    inv.slots[0] = ("wood", 9)
    ok = _run(
        purse_withdraw(m, 1, 7, "coin", slot=0))
    assert not ok
    assert inv.slots[0] == ("wood", 9)   # untouched
    assert p.coins == 3                   # purse untouched
