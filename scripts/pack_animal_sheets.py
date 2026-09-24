"""Pack the Minifolks Forest Animals frames (frame-per-PNG) into the Kaetram
5-col x N-row mob sheet layout the game already renders (see game.ts
MOB_SHEETS / util.ts getDefaultAnimations "mobs").

Row map (row-major 5 cols):
  row 0 = atk   (minifolks 04_atk / fly / walk_2, pad to >= 3)
  row 1 = walk  (02_walk / 01_walk / 02_fly)
  row 2 = idle  (01_idle / fly / walk, pad to 2)
  row 3 = dead  (dead frames, used by the death fade)

Idle frames repeat to 2 (0,1,0,1...) client-side; walk/atk play from the
sheet directly. All sprites face RIGHT by default (Minifolks source art);
the client flips for left/up/down like every other mob.

One-time run:  .venv\\Scripts\\python scripts/pack_animal_sheets.py
Output:        assets/mobs/<kind>.png  (served to the web client + used by
               the Discord renderer's animal tokens).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# User 25/09: use the OUTLINE cut (nicer sprite edge) from their Downloads
# folder. The old NoOutline path is kept only as a comment for history.
# User 25/09: use the OUTLINE cut (nicer sprite edge) from their Downloads
# folder. The old NoOutline path is kept only as a comment for history.
SRC = Path(r"C:\Users\phant\Downloads\MinifolksForestAnimals_Cut_Outline")
# SRC = Path(r"C:\Users\phant\Downloads\MinifolksForestAnimals_Cut_Outline")
OUT = Path(__file__).resolve().parent.parent / "assets" / "mobs"
OUT.mkdir(parents=True, exist_ok=True)

# Minifolks art faces RIGHT natively (wolf ears / bunny ears / deer head sit
# on the RIGHT of the body; atk thrusts extend +7px RIGHT) — NO mirror needed:
# the client flips the sheet itself for W/NW/SW facing. The bird is the one
# LEFT-facing exception — flip ONLY it so every sheet is right-facing.
FLIP_ONLY = {"bird"}

# Per-kind ART fill (fraction of the 32px cell the animal's height covers):
# one blanket 88% turned the bunny into a giant — small critters must stay
# small. Pairs with MOB_SHEETS.size in web_client/src/game.ts.
ART_FILL = {
    "bear": 0.95, "deer2": 0.82, "deer": 0.75, "wolf": 0.72,
    "boar": 0.66, "fox": 0.56, "bunny": 0.42, "bird": 0.34,
}

# out_kind -> (source folder, preferred walk subdir)
KINDS = {
    "bear":   ("MiniBear",  "02_walk"),
    "bird":   ("MiniBird",  "02_fly"),
    "boar":   ("MiniBoar",  "02_walk"),
    "bunny":  ("MiniBunny", "02_walk_2"),
    "deer":   ("MiniDeer1", "02_walk"),
    "deer2":  ("MiniDeer2", "02_walk"),
    "fox":    ("MiniFox",   "02_walk"),
    "wolf":   ("MiniWolf",  "02_walk"),
}

# Per-kind row overrides (source subdir priority lists). The bunny pack ships
# NO idle dir: pixel-verified 25/09 — 01_walk is a SITTING ear-twitch loop
# (the real idle), 02_walk_2 is the actual HOP (walk), 03_hit the hit pose.
# The generic fallbacks picked 01_walk for BOTH walk and idle, so the rabbit
# "walked" as a sitting bunny twitching its ears (user: "frame walk 1 thật
# chất ra là idle đấy, walk 2 mới đúng").
ROW_OVERRIDES = {
    "bunny": {
        "walk": ["02_walk_2"],
        "idle": ["01_walk"],
        "atk":  ["03_hit", "02_walk_2"],
    },
}


def frames(folder: Path, subs: list[str]) -> list[Image.Image]:
    """First existing subdir's frames (sorted) as RGBA images."""
    for sub in subs:
        d = folder / sub
        if d.is_dir():
            return [
                Image.open(p).convert("RGBA")
                for p in sorted(d.glob("frame_*.png"))
            ]
    return []


def pad(frames_list: list[Image.Image], minimum: int) -> list[Image.Image]:
    """Pad a row by repeating its frames until >= minimum cells."""
    if not frames_list:
        raise ValueError("no frames to pad")
    out = list(frames_list)
    i = 0
    while len(out) < minimum:
        out.append(out[i % len(frames_list)])
        i += 1
    return out


def pad_static(frames_list: list[Image.Image], minimum: int) -> list[Image.Image]:
    """Pad a row by REPEATING ITS FIRST frame (idle must not fake-bob when
    the species ships no idle dir — a cycling walk row reads as walking
    in place while the mob stands still)."""
    if not frames_list:
        raise ValueError("no frames to pad")
    out = list(frames_list)
    while len(out) < minimum:
        out.append(frames_list[0])
    return out


def normalize_rows(rows: list[list[Image.Image]], cell: int,
                   art_fill: float, flip: bool = False) -> list[list[Image.Image]]:
    """Per-anim processing: EACH anim row is tight-cropped to its OWN bbox
    (a shared species bbox let the wide atk thrust shift every other frame's
    anchor — sprites visibly slid around), optionally MIRRORED (bird only —
    the other species already face right), scaled to `art_fill` of the cell
    height and bottom-centre anchored so feet stay on the tile.

    All rows are normalized to the SAME anchor because the crop origin is
    per-row: the walk bob survives (walk frames differ within their own
    row), but a row switch (idle->walk) can pop ±1px — invisible at 2x zoom.
    """
    out = []
    for row in rows:
        if not row:
            raise ValueError("empty anim row")
        # Union bbox across THIS row's frames only.
        box = None
        for f in row:
            b = f.getbbox()
            if b is None:
                continue
            box = b if box is None else (
                min(box[0], b[0]), min(box[1], b[1]),
                max(box[2], b[2]), max(box[3], b[3]),
            )
        if box is None:
            out.append(row)
            continue
        art_h = max(1, box[3] - box[1])
        art_w = max(1, box[2] - box[0])
        target_h = max(1, round(cell * art_fill))
        # Width must fit the cell too (a wide boar must never spill into the
        # neighbouring frame): the binding dimension wins.
        scale = min(target_h / art_h, (cell - 2) / art_w)
        target_h = max(1, round(art_h * scale))
        target_w = max(1, round(art_w * scale))
        norm = []
        for f in row:
            f2 = f.crop(box)
            if flip:
                f2 = f2.transpose(Image.FLIP_LEFT_RIGHT)
            f2 = f2.resize((target_w, target_h), Image.NEAREST)
            canvas = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
            canvas.paste(
                f2,
                ((cell - target_w) // 2, cell - target_h),  # bottom-centre
            )
            norm.append(canvas)
        out.append(norm)
    return out


def pack_sheet(kind: str, folder_name: str, walk_sub: str) -> None:
    folder = SRC / folder_name
    # Per-kind row overrides first (bunny rows are verified differently —
    # see ROW_OVERRIDES), then the generic dir fallbacks. Bird has no walk
    # dir — fly doubles as atk.
    ov = ROW_OVERRIDES.get(kind, {})
    atk = frames(folder, ov.get("atk", ["04_atk", "02_fly", "02_walk_2", walk_sub]))
    walk = frames(folder, ov.get("walk", [walk_sub, "01_walk", "02_fly"]))
    idle = frames(folder, ov.get("idle", ["01_idle", "02_fly", walk_sub, "02_walk_2"]))
    dead = frames(folder, ["08_dead", "07_dead", "06_dead", "04_dead", "03_dead"])

    CELL = 32
    atk, walk, idle, dead = normalize_rows(
        [atk, walk, idle, dead], CELL, ART_FILL[kind],
        flip=kind in FLIP_ONLY,
    )

    # Bird fly frames alternate UP/DOWN wing poses — frame_03 duplicates
    # frame_02's down-stroke; keep frames [0,1] only so the walk loop does
    # not stutter (user: "frame animation ko đúng").
    if kind == "bird" and len(walk) > 2:
        walk = walk[:2]

    n_atk, n_walk = len(atk), len(walk)
    atk = pad(atk, 3)
    walk = pad(walk, 3)
    # Idle: STATIC pad (no fake bobbing for species without an idle dir).
    idle = pad_static(idle, 2)

    rows = [atk, walk, idle, dead]
    sheet_w = 5 * CELL
    sheet_h = len(rows) * CELL
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    for r, row in enumerate(rows):
        for c, f in enumerate(row):
            sheet.paste(f, (c * CELL, r * CELL))
    out_path = OUT / f"{kind}.png"
    sheet.save(out_path)
    print(f"{kind}: {CELL}px cells, atk={n_atk} walk={n_walk} "
          f"idle={len(idle)} dead={len(dead)} -> {out_path.name}")


def main() -> None:
    if not SRC.is_dir():
        print(f"source pack not found: {SRC}", file=sys.stderr)
        raise SystemExit(1)
    for kind, (folder, walk_sub) in KINDS.items():
        pack_sheet(kind, folder, walk_sub)


if __name__ == "__main__":
    main()
