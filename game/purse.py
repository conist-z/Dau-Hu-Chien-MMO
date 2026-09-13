"""Currency purse: coin/crystal auto-counter + 1-per-drag withdrawal.

The v5 inventory panel shows two counters (bottom row): coins (left) and
crystals (right). Any `coin`/`crystal` item that lands in the bag is
auto-converted into the player's counters — the purse, not the bag, is the
source of truth. Dragging the icon OUT of the panel pulls exactly ONE unit
back into the bag (the "kéo trực tiếp ra" interaction).

Pure game-layer helpers (no discord/web imports) + tests in
tests/test_purse.py. The web op adapter lives in web_api/core.py
(`purse_withdraw` op).
"""
from __future__ import annotations

from typing import Optional, Tuple

# The two purse currencies — item ids exactly as they appear in the item
# registry. Extending to a third currency = one entry here + a Player field.
CURRENCY_ITEM_IDS: Tuple[str, ...] = ("coin", "crystal")

# Player field that backs each currency id.
_PURSE_FIELD = {"coin": "coins", "crystal": "crystals"}


def is_currency(item_id: str) -> bool:
    return item_id in CURRENCY_ITEM_IDS


def purse_add(manager, channel_id: int, user_id: int,
              item_id: str, qty: int) -> bool:
    """Convert EXACTLY `qty` of `item_id` from the bag into the purse.

    Explicit deposit path only (purse_deposit op): drains up to `qty` from
    the bag stacks into the matching Player counter. Returns True when the
    purse changed. The bag keeps any remaining currency — it is a free
    item; nothing auto-converts it.
    """
    if not is_currency(item_id) or qty <= 0:
        return False
    rt = manager.get_runtime_for(channel_id, user_id)
    if rt is None:
        return False
    player = rt.state.get_player(user_id)
    if player is None:
        return False
    inv = manager.get_inventory(channel_id, user_id)
    take = min(int(qty), inv.count(item_id))
    if take <= 0:
        return False
    # Drain slot-by-slot in SLOT ORDER (first stack first) so the deposit
    # consumes whole stacks top-down instead of Inventory.remove's
    # later-slots-first drain, which would eat a different stack than the
    # player deposited from and reshuffle the bag.
    remaining = take
    for i, s in enumerate(inv.slots):
        if remaining <= 0:
            break
        if not s or s[0] != item_id:
            continue
        t = min(s[1], remaining)
        left = s[1] - t
        inv.slots[i] = (item_id, left) if left > 0 else None
        remaining -= t
    if remaining > 0:
        return False  # defensive; count() above should prevent this
    field = _PURSE_FIELD[item_id]
    setattr(player, field, getattr(player, field) + take)
    return True


def purse_balance(state, user_id: int, item_id: str) -> int:
    """Current purse balance of one currency (0 when unknown)."""
    player = state.get_player(user_id) if hasattr(state, "get_player") else None
    if player is None:
        return 0
    return getattr(player, _PURSE_FIELD.get(item_id, ""), 0) or 0


async def purse_withdraw(manager, channel_id: int, user_id: int,
                         item_id: str, slot: Optional[int] = None) -> bool:
    """Pull exactly ONE unit of `item_id` from the purse into the bag.

    The drag-out interaction withdraws 1 per drag. ``slot`` (0-based bag
    index) deposits the unit into that exact slot — merging onto a same-
    item stack or filling an EMPTY cell; a slot holding a different item
    rejects the deposit. Without a slot the unit goes to the first free
    cell. Fails (False) when the purse cannot afford it or the target has
    no room — the client then springs the ghost back, no state change.
    """
    if not is_currency(item_id):
        return False
    rt = manager.get_runtime_for(channel_id, user_id)
    if rt is None:
        return False
    player = rt.state.get_player(user_id)
    if player is None:
        return False
    field = _PURSE_FIELD[item_id]
    if getattr(player, field, 0) < 1:
        return False
    inv = manager.get_inventory(channel_id, user_id)
    if slot is not None:
        if not 0 <= slot < len(inv.slots):
            return False
        cell = inv.slots[slot]
        if cell is None:
            pass  # empty cell: direct deposit
        elif cell[0] == item_id:
            pass  # same-item stack: merge (cap at stack limit below)
        else:
            return False  # different item occupies the slot
        setattr(player, field, getattr(player, field) - 1)
        if cell is None:
            inv.set_slot(slot, item_id, 1)
        else:
            inv.set_slot(slot, item_id, cell[1] + 1)  # merge onto the stack
        return True
    if inv.first_free_slot() < 0:
        return False  # bag full: the unit stays in the purse
    setattr(player, field, getattr(player, field) - 1)
    inv.add(item_id, 1)
    return True


def purse_persist_pairs(state, user_id: int) -> Optional[Tuple[int, int]]:
    """(coins, crystals) snapshot for the save path — None when unknown."""
    player = state.get_player(user_id) if hasattr(state, "get_player") else None
    if player is None:
        return None
    return (getattr(player, "coins", 0) or 0,
            getattr(player, "crystals", 0) or 0)
