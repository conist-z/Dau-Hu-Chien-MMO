from typing import Dict, Iterator, List, Optional, Tuple

from config import HOTBAR_SLOTS
from game.items import ITEM_REGISTRY, apply_effect


class Inventory:
    """Per-player item bag — a TRUE SLOT GRID (the V5 inventory panel).

    ``slots`` is a sparse list: index = pixel-grid slot (0..19), ``None`` =
    empty slot. A stack NEVER moves on its own: new stacks land in the first
    free slot, merging only happens when the caller explicitly drops onto the
    same kind. The old "auto-tidy" compaction (dict keyed by item id) is gone:
    the player decides where everything sits.

    Legacy compat: ``Inventory({"stone": 3})`` builds a dense bag from a dict,
    and the ``items`` property projects the grid back to {item_id: qty} in
    slot order (all the old Discord-side call sites keep working).

    ``version`` is a monotonic change counter (web snapshot diffing).
    """

    BAG_SLOTS = 20

    def __init__(self, items: Optional[Dict[str, int]] = None) -> None:
        self.slots: List[Optional[Tuple[str, int]]] = [None] * self.BAG_SLOTS
        self.version: int = 0
        # Spillover when all 20 slots are occupied (never loses loot).
        self.overflow: Dict[str, int] = {}
        if items:
            for iid, qty in items.items():
                if qty > 0:
                    self.add(iid, qty)

    def _bump(self) -> None:
        self.version += 1

    # ------------------------------------------------------------------ reads

    def stacks(self) -> List[Tuple[int, str, int]]:
        """[(slot, item_id, qty), ...] in slot order — the canonical view."""
        return [(i, s[0], s[1]) for i, s in enumerate(self.slots) if s]

    @property
    def items(self) -> Dict[str, int]:
        """Legacy view: {item_id: total qty} in slot order (overflow last)."""
        out: Dict[str, int] = {}
        for _i, iid, qty in self.stacks():
            out[iid] = out.get(iid, 0) + qty
        for iid, qty in self.overflow.items():
            if qty > 0:
                out[iid] = out.get(iid, 0) + qty
        return out

    def count(self, item_id: str) -> int:
        total = sum(q for _i, iid, q in self.stacks() if iid == item_id)
        return total + self.overflow.get(item_id, 0)

    def first_free_slot(self) -> int:
        for i, s in enumerate(self.slots):
            if s is None:
                return i
        return -1

    def is_empty(self) -> bool:
        return all(s is None for s in self.slots) and not any(
            q > 0 for q in self.overflow.values())

    def __iter__(self) -> Iterator[Tuple[int, str, int]]:
        return iter(self.stacks())

    # ------------------------------------------------------------- mutations

    def add(self, item_id: str, qty: int = 1) -> None:
        """Add qty of ``item_id``.

        Merge into an EXISTING stack of the same item first (its own slot —
        the natural "picked up more of what I already carry" case), then
        overflow into the first free slot. Slots the player deliberately
        left empty in the middle are never backfilled by later pickups —
        no compaction, ever.
        """
        if qty <= 0:
            return
        for i, s in enumerate(self.slots):
            if s and s[0] == item_id:
                self.slots[i] = (item_id, s[1] + qty)
                self._bump()
                return
        free = self.first_free_slot()
        if free >= 0:
            self.slots[free] = (item_id, qty)
        else:
            self.overflow[item_id] = self.overflow.get(item_id, 0) + qty
        self._bump()

    def set_slot(self, index: int, item_id: Optional[str], qty: int) -> None:
        """Write one grid cell directly (reorder/split/collect paths).

        ``item_id=None`` or ``qty<=0`` clears the slot. Bumps version only
        when the cell actually changed.
        """
        if not 0 <= index < len(self.slots):
            return
        new: Optional[Tuple[str, int]] = (
            None if item_id is None or qty <= 0 else (item_id, qty))
        if self.slots[index] != new:
            self.slots[index] = new
            self._bump()

    def remove(self, item_id: str, qty: int = 1) -> bool:
        """Remove ``qty`` of ``item_id`` across its stacks (later slots first
        so the first stack — the hotbar one — survives longest; overflow is
        drained before any slot). Returns False when short."""
        if self.count(item_id) < qty:
            return False
        remaining = qty
        ov = self.overflow.get(item_id, 0)
        if ov > 0:
            take = min(ov, remaining)
            ov -= take
            remaining -= take
            if ov > 0:
                self.overflow[item_id] = ov
            else:
                self.overflow.pop(item_id, None)
        if remaining > 0:
            for i, _iid, _q in sorted(self.stacks(), reverse=True):
                if remaining <= 0:
                    break
                if self.slots[i][0] != item_id:
                    continue
                s = self.slots[i]
                take = min(s[1], remaining)
                left = s[1] - take
                self.slots[i] = (item_id, left) if left > 0 else None
                remaining -= take
        self._bump()
        return True

    def move_slot(self, src: int, dst: int) -> bool:
        """Drag & drop primitive: swap src and dst cells.

        Same item in both = merge src into dst (nothing stays in src).
        Different/empty = plain swap. Pure grid edit: totals never change.
        """
        if not (0 <= src < len(self.slots) and 0 <= dst < len(self.slots)):
            return False
        a, b = self.slots[src], self.slots[dst]
        if a is None:
            return False
        if b and b[0] == a[0]:
            self.slots[dst] = (b[0], b[1] + a[1])
            self.slots[src] = None
        else:
            self.slots[src], self.slots[dst] = b, a
        self._bump()
        return True

    def split_slot(self, index: int) -> bool:
        """Right-click split: half of the stack moves to the first free slot.

        Pure: total qty preserved. False when empty/singleton or the bag is
        full."""
        if not 0 <= index < len(self.slots):
            return False
        s = self.slots[index]
        if not s or s[1] < 2:
            return False
        free = self.first_free_slot()
        if free < 0:
            return False
        half = s[1] - s[1] // 2  # ceil half moves out
        self.slots[index] = (s[0], s[1] - half)
        self.slots[free] = (s[0], half)
        self._bump()
        return True

    # ------------------------------------------------------------ legacy APIs

    def move_to(self, item_id: str, target_index: int) -> None:
        """Legacy hotbar assign: reposition ``item_id``'s FIRST stack from
        its occupied-slot ordinal to ``target_index`` (stacks in between
        shift by one ordinal — old insert semantics)."""
        occ = [i for i, s in enumerate(self.slots) if s]
        src_ord = next(
            (k for k, i in enumerate(occ) if self.slots[i][0] == item_id), None)
        if src_ord is None or not occ:
            return
        target = max(0, min(target_index, len(occ) - 1))
        cur = src_ord
        while cur != target:
            nxt = cur + 1 if target > cur else cur - 1
            a, b = occ[cur], occ[nxt]
            self.slots[a], self.slots[b] = self.slots[b], self.slots[a]
            cur = nxt
        if src_ord != target:
            self._bump()

    def hotbar(self, slots: int = None) -> Dict[int, Optional[str]]:
        """The hotbar PROJECTION: slot N = BAG SLOT N (positional mapping).

        Hotbar slot N shows exactly what sits in bag slot N — empty bag slot
        = empty hotbar slot. Moving a stack OUT of the first HOTBAR_SLOTS
        bag slots therefore really removes it from the hotbar (the old
        "Nth occupied stack" ordinal projection kept showing it wherever
        the player dragged it inside the bag).
        """
        if slots is None:
            slots = HOTBAR_SLOTS
        return {
            n: (self.slots[n][0] if n < len(self.slots) and self.slots[n] else None)
            for n in range(slots)
        }

    def swap_to_slot(self, item_id: str, target: int) -> None:
        """Move ``item_id``'s FIRST stack into bag slot ``target`` (hotbar
        assign, positional). Whatever occupies the target slot swaps into the
        freed position — no stack is ever lost, no shifting of others."""
        occ = [i for i, s in enumerate(self.slots) if s and s[0] == item_id]
        if not occ:
            return
        src = occ[0]
        target = max(0, min(target, len(self.slots) - 1))
        if src == target:
            return
        self.slots[src], self.slots[target] = self.slots[target], self.slots[src]
        self._bump()

    def use(self, item_id: str, player) -> tuple:
        """Consume one unit of a consumable from this bag (manager.use_item
        calls this). Returns (ok, reason) — "unknown_item"/"not_usable" for
        non-consumables, "empty" when the bag holds none."""
        if item_id not in ITEM_REGISTRY:
            return False, "unknown_item"
        if self.count(item_id) <= 0:
            return False, "empty"
        changed, reason = apply_effect(player, item_id)
        if not changed:
            return False, reason
        self.remove(item_id, 1)
        return True, reason
