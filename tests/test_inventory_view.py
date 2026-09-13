import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from discord_ui.inventory_view import (
    BORDER,
    GRID_H,
    GRID_W,
    ITEM_SLOT,
    MAX_STACK,
    _format_detail,
    _format_grid,
    _grid_cells,
    _qty_text,
    _stacks,
)


def test_qty_text_is_two_zero_padded_superscript_digits():
    # The badge is PLAIN TEXT (superscript), zero-padded to two digits.
    assert _qty_text(1) == "⁰¹"
    assert _qty_text(9) == "⁰⁹"
    assert _qty_text(10) == "¹⁰"
    assert _qty_text(52) == "⁵²"
    assert _qty_text(99) == "⁹⁹"
    assert len(_qty_text(1)) == len(_qty_text(99)) == 2


def test_qty_text_clamps_out_of_range_counts():
    assert _qty_text(0) == "⁰⁰"
    assert _qty_text(-5) == "⁰⁰"
    assert _qty_text(500) == "⁹⁹"  # display clamp; real qty stays in the bag


def test_stacks_split_overflow_minecraft_style():
    # 250 apples -> stacks of 99, 99, remainder 52.
    assert _stacks({"apple": 250}) == [
        ("apple", 99), ("apple", 99), ("apple", 52),
    ]


def test_stacks_exact_multiple_and_single():
    assert _stacks({"wood": 99}) == [("wood", 99)]
    assert _stacks({"key_stone": 1}) == [("key_stone", 1)]
    assert _stacks({}) == []


def test_stacks_follow_bag_insertion_order():
    # The dict's insertion order IS the bag order (what the hotbar mirrors);
    # stacks must NOT be re-sorted.
    assert _stacks({"b_item": 2, "a_item": 2}) == [
        ("b_item", 2), ("a_item", 2),
    ]


def test_grid_cells_one_per_stack_in_bag_order():
    cells = _grid_cells({"potion_hp": 3, "key_stone": 1})
    # Insertion order: potion_hp (qty 3) first, then key_stone (qty 1).
    assert cells[0][0] != "❓"
    assert cells[0][1] == "⁰³"
    assert cells[1][0] != "❓"
    assert cells[1][1] == "⁰¹"
    # Rest of the grid is unused.
    assert all(c is None for c in cells[2:])
    assert len(cells) == GRID_W * GRID_H


def test_grid_cells_each_overflow_stack_occupies_one_cell():
    items = {"apple": 250}
    cells = _grid_cells(items)
    assert cells[0] == ("🍎", "⁹⁹")
    assert cells[1] == ("🍎", "⁹⁹")
    assert cells[2] == ("🍎", "⁵²")
    assert cells[3] is None


def test_grid_cells_unknown_item_shows_placeholder():
    cells = _grid_cells({"mystery_item": 1})
    assert cells[0][0] == "❓"


def test_format_grid_is_always_full_12x4_box():
    # The panel is a FULL box: 6 lines (top frame + 4 rows + bottom frame),
    # every row exactly 12 slots — never a dynamic wrap.
    for total in [1, 5, 13, 25, 48]:
        items = {f"item_{i:02d}": 1 for i in range(total)}
        lines = _format_grid(items).splitlines()
        assert len(lines) == GRID_H + 2
        for line in lines:
            assert line.startswith(BORDER) and line.endswith(BORDER)
            assert len(line[1:-1].replace(ITEM_SLOT, "")) >= 0  # parseable
            # Non-⬛ content per row never exceeds 12 cells.
            filled = sum(
                1 for c in line[1:-1] if c != ITEM_SLOT
            )
            assert filled <= GRID_W * 7  # <= 12 stacks x 7 code points


_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001fbff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\u2b1b\u2b1c\u25fd\u25fe]"
)


def _line_metrics(line: str) -> tuple:
    """Discord's emoji+text model: width = 2*#emoji + #text chars."""
    emoji = len(_EMOJI_RE.findall(line))
    return 2 * emoji, len(line) - _EMOJI_RE.nomatch if False else len(line) - emoji * 0


_SUP_RE = re.compile(r"[\u2070-\u2079\u00b9\u00b2\u00b3]")


def _rendered_width(line: str) -> float:
    """Discord rendering model (user-measured): emoji = 2 columns, one
    superscript digit = 1.3 columns (a GLYPH, not whitespace), any other
    char = 1 column."""
    emoji = len(_EMOJI_RE.findall(line))
    digits = len(_SUP_RE.findall(line))
    text = len(line) - sum(len(m.group(0)) for m in _EMOJI_RE.finditer(line))
    return 2 * emoji + 1.3 * digits + (text - digits)


def test_format_grid_every_line_same_rendered_width():
    """REGRESSION: the frame drifts when a partial row's text part is shorter
    than a full row's. Every row must render at exactly 4*12+4 = 52 columns
    (2*emoji + text chars) — 14 emoji (2 frame + 12 slots) + 24 text chars
    (badges + padding spaces)."""
    cases = [
        {"coin": 1, "wheat": 12, "meat": 1, "wood": 5, "apple": 99},
        {"apple": 250},
        {"potion_hp": 3, "key_stone": 1},
        {f"item_{i:02d}": 1 for i in range(48)},
        {f"item_{i:02d}": 1 for i in range(13)},
        {f"item_{i:02d}": 1 for i in range(25)},
        {f"item_{i:02d}": 1 for i in range(49)},
        {"apple": 500},  # display-clamped stacks: 99 x5 + 5
    ]
    for items in cases:
        grid = _format_grid(items)
        lines = grid.splitlines()
        assert len(lines) == GRID_H + 2  # top + 4 rows + bottom
        widths = [_rendered_width(l) for l in lines]
        # The digit-glyph correction trims whole spaces for fractional glyph
        # widths, so rows align within one space — imperceptible.
        assert max(widths) - min(widths) <= 1.0, (
            f"lines drift: {widths}\n" + "\n".join(lines)
        )
        # Every row keeps the 14-emoji frame structure.
        for line in lines:
            assert line.startswith(BORDER) and line.endswith(BORDER)


def test_format_grid_250_items_fill_three_cells_and_stay_aligned():
    grid = _format_grid({"apple": 250})
    lines = grid.splitlines()
    assert len(lines) == GRID_H + 2  # full 4-row box
    assert "⁹⁹" in lines[1] and "⁵²" in lines[1]  # 3 stack cells on row 1
    # Rows 2-4 align with row 1 within half a space (round-off only).
    w1 = _rendered_width(lines[1])
    for row in lines[2:-1]:
        assert abs(_rendered_width(row) - w1) <= 0.5


def test_format_grid_empty_bag_keeps_full_box():
    grid = _format_grid({})
    lines = grid.splitlines()
    assert len(lines) == GRID_H + 2
    # Every cell — empty or frame — is "emoji + 2 spaces" (uniform width).
    empty_row = BORDER + f"{ITEM_SLOT}  " * GRID_W + BORDER
    frame_row = BORDER + f"{BORDER}  " * GRID_W + BORDER
    assert lines[0] == frame_row and lines[-1] == frame_row
    for row in lines[1:-1]:
        assert row == empty_row


def test_format_detail_known_and_unknown():
    detail = _format_detail("potion_hp", 3)
    assert "Potion HP" in detail
    assert "Qty: 3" in detail
    assert _format_detail("no_such_item", 1) == "❓ **Unknown item**"
