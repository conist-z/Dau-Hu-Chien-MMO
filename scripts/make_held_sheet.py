"""Generate a Kaetram-style HELD-ITEM sheet from any 16x16-ish icon.

Input:  a square-ish RGBA icon (Kaetram item icon, Twemoji, block face...).
Output: a 192x432 sprite sheet — 48x48 frames, 4 cols x 9 rows
        (idle/walk/atk x down/right/up) — plus a manifest entry dict,
        drop-in for assets/players/players_manifest.json "weapons".

The placement table (scripts/held_anchor_table.json) was MEASURED from the
12 real Kaetram weapon sheets: the art is pasted so its centre lands in the
"mandatory grip core" (the pixel region covered by ALL sheets — where the
hand is), with the same per-frame breathing (idle/walk) and swing motion
(atk: windup -> strike with rotation + streak) as the real art.

Usage:
  python scripts/make_held_sheet.py --icon <png> --stem <name> [--out assets/players/weapon]
  python scripts/make_held_sheet.py --icon <png> --stem <name> --print-entry   # manifest JSON
  python scripts/make_held_sheet.py --icon <png> --stem <name> --base assets/players/base.png \
      --contact assets/_contact.png    # render player + item for visual check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TABLE_PATH = ROOT / "scripts" / "held_anchor_table.json"
ROW_NAMES = [
    "0_idle_down", "1_idle_right", "2_idle_up",
    "3_walk_down", "4_walk_right", "5_walk_up",
    "6_atk_down", "7_atk_right", "8_atk_up",
]
# Manifest rows[] uses Kaetram names (idle_down..atk_up) — same order.
MANIFEST_ROW_NAMES = [
    "idle_down", "idle_right", "idle_up",
    "walk_down", "walk_right", "walk_up",
    "atk_down", "atk_right", "atk_up",
]
FRAME = 48
COLS = 4


def load_icon(path: Path, size: int = 16) -> Image.Image:
    """Load the icon, snap to a size x size RGBA art block (pixel-art look)."""
    im = Image.open(path).convert("RGBA")
    im = im.resize((size, size), Image.LANCZOS)
    # Pixel-art alpha snap: Kaetram art is binary alpha; keep hard edges.
    px = im.load()
    for y in range(size):
        for x in range(size):
            r, g, b, a = px[x, y]
            px[x, y] = (r, g, b, 255 if a > 96 else 0)
    # Trim to the art bbox then keep it centred (icons rarely fill 16x16).
    bbox = im.getbbox()
    if bbox:
        art = im.crop(bbox)
        side = max(art.size)
        if side > size:  # extreme aspect: clamp
            art = art.resize((size, max(1, art.size[1] * size // art.size[0])), Image.LANCZOS)
            side = max(art.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(art, ((side - art.size[0]) // 2, (side - art.size[1]) // 2))
        return canvas
    return im


def paste_art(frame: Image.Image, art: Image.Image, cx: int, cy: int) -> None:
    """Paste the art block with its CENTRE at (cx, cy) — absolute frame px."""
    frame.alpha_composite(art, (cx - art.size[0] // 2, cy - art.size[1] // 2))


def draw_streak(frame: Image.Image, streak: dict) -> None:
    """The swing motion-streak Kaetram paints behind the strike frame."""
    r, g, b = streak["color"]
    lo, hi = streak["alpha"]
    n = len(streak["px"])
    for i, (x, y) in enumerate(streak["px"]):
        a = lo + (hi - lo) * (1 - i / max(1, n - 1))
        frame.putpixel((x, y), (r, g, b, int(a)))


def build_sheet(icon: Path, size: int = 16) -> tuple[Image.Image, list[list[Image.Image]]]:
    table = json.loads(TABLE_PATH.read_text())
    art = load_icon(icon, size)

    sheet = Image.new("RGBA", (FRAME * COLS, FRAME * len(ROW_NAMES)), (0, 0, 0, 0))
    frames: list[list[Image.Image]] = []
    for ri, row_name in enumerate(ROW_NAMES):
        row = table["rows"][row_name]
        centres = row["centre12"]
        rots = row.get("rot") or [None] * COLS
        frames.append([])
        for c in range(COLS):
            frame = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
            centre = centres[c]
            if centre is None:
                sheet.paste(frame, (c * FRAME, ri * FRAME))  # empty frame (atk_up f2/f3)
                frames[-1].append(frame)
                continue
            a = art
            rot = rots[c] if c < len(rots) else None
            if rot:
                a = art.rotate(rot, expand=True, resample=Image.NEAREST)
            paste_art(frame, a, *centre)
            if ri == 7 and c == int(row.get("streak", {}).get("frame", -1)):
                draw_streak(frame, row["streak"])
            sheet.paste(frame, (c * FRAME, ri * FRAME))
            frames[-1].append(frame)
    return sheet, frames


def manifest_entry(stem: str) -> dict:
    """Drop-in entry for players_manifest.json 'weapons' (same schema as the
    real Kaetram weapon sheets: offset bakes the 48-frame over the 32 body)."""
    return {
        "file": f"weapon/{stem}.png",
        "frame_w": FRAME,
        "frame_h": FRAME,
        "cols": COLS,
        "rows": 9,
        "offset_x": -8,
        "offset_y": -24,
    }


def contact_sheet(sheet: Image.Image, base_path: Path, out: Path, scale: int = 3) -> None:
    """Player base + our item composited per frame, laid out 9 rows x 4 cols
    for eyeball verification of the grip quality."""
    base = Image.open(base_path).convert("RGBA")  # 32x32 x 4 cols x 12 rows
    cell = FRAME * scale
    out_im = Image.new(
        "RGBA", (cell * COLS + 40, cell * len(ROW_NAMES) + 30), (34, 34, 40, 255)
    )
    from PIL import ImageDraw

    draw = ImageDraw.Draw(out_im)
    for ri, row_name in enumerate(ROW_NAMES):
        for c in range(COLS):
            comp = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
            body = base.crop((c * 32, ri * 32, (c + 1) * 32, (ri + 1) * 32))
            # centre the 32x32 body in the 48x48 frame like Kaetram does
            comp.alpha_composite(body, (8, 16))
            comp.alpha_composite(sheet.crop((c * FRAME, ri * FRAME, (c + 1) * FRAME, (ri + 1) * FRAME)))
            big = comp.resize((cell, cell), Image.NEAREST)
            out_im.alpha_composite(big, (20 + c * cell, 15 + ri * cell))
        draw.text((2, 15 + ri * cell + cell // 2), row_name, fill=(240, 240, 240, 255))
    out_im.save(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--icon", required=True, type=Path)
    ap.add_argument("--stem", required=True, help="sheet stem, e.g. apple")
    ap.add_argument("--size", type=int, default=16, help="art block size (px)")
    ap.add_argument("--out", type=Path, default=ROOT / "assets" / "players" / "weapon")
    ap.add_argument("--base", type=Path, default=ROOT / "assets" / "players" / "base.png")
    ap.add_argument("--contact", type=Path, default=None)
    ap.add_argument("--print-entry", action="store_true")
    args = ap.parse_args(argv)

    sheet, _ = build_sheet(args.icon, args.size)
    args.out.mkdir(parents=True, exist_ok=True)
    out_png = args.out / f"{args.stem}.png"
    sheet.save(out_png)
    if args.print_entry:
        print(json.dumps(manifest_entry(args.stem), indent=2))
    if args.contact:
        contact_sheet(sheet, args.base, args.contact)
        print("contact sheet ->", args.contact.name)
    print(f"sheet -> {out_png.name} ({sheet.size[0]}x{sheet.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
