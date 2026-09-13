"""One-time converter: CraftPix "Weather Effects Assets Pack Pixel Art"
-> runtime-ready frames under ``assets/fx/``.

The pack is side-view; every source file is ONE animation frame of a
seamlessly tileable particle sheet, which is exactly what the top-down
overlay in ``rendering/weather_fx.py`` needs:

    6 Weather/Rain/1..10.png   256x32  -> fx/rain/rain_00..09.png
    6 Weather/Snow1.png        256x32  -> fx/snow/snow_tile.png
    5 Wind/Wind1..4.png        512x32  -> fx/wind/wind_00..03.png
    4 Thunder/Thunder.png      576x192 -> 6 columns 96x192; keep the ones
                                          with real bolt artwork (drop the
                                          blank/backdrop columns), anchor
                                          content to the column TOP so the
                                          bolt always strikes from the sky
                                          -> fx/thunder/bolt_00..0N.png

Run from the project root (default source path can be overridden):

    .venv\\Scripts\\python scripts/convert_weather_fx.py [source_dir]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image

DEFAULT_SRC = Path(
    r"D:\UserData\Downloads\New folder\gui\Weather Effects Assets Pack Pixel Art"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = PROJECT_ROOT / "assets" / "fx"


def convert_copy(src_dir: Path, pattern: str, out_dir: Path, prefix: str) -> int:
    """Copy straight frames (one file = one frame), zero-padded."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(src_dir.glob(pattern))
    n = 0
    for i, p in enumerate(files):
        img = Image.open(p).convert("RGBA")
        img.save(out_dir / f"{prefix}_{i:02d}.png")
        n += 1
    return n


def convert_thunder(src_file: Path, out_dir: Path) -> int:
    """Split the 576x192 sheet into 96-wide columns, drop blank/backdrop
    columns, trim each to its alpha bbox anchored at the column TOP."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet = Image.open(src_file).convert("RGBA")
    w, h = sheet.size
    col_w = 96
    kept: list[Image.Image] = []
    for cx in range(0, w, col_w):
        col = sheet.crop((cx, 0, cx + col_w, h))
        alpha = col.getchannel("A")
        bbox = alpha.getbbox()
        if bbox is None:
            continue
        # Backdrop column: opaque coverage ~ 100% (a solid plate), not a bolt.
        hist = alpha.histogram()
        opaque = sum(hist[200:])
        coverage = opaque / (col_w * h)
        if coverage > 0.95:
            continue
        trimmed = col.crop(bbox)
        kept.append(trimmed)
    n = 0
    for i, bolt in enumerate(kept):
        # Re-anchor onto a transparent canvas: top of the frame = top of the
        # strike, horizontally centred in the original column width.
        canvas = Image.new("RGBA", (col_w, max(bolt.height, 1)), (0, 0, 0, 0))
        canvas.paste(bolt, ((col_w - bolt.width) // 2, 0), bolt)
        canvas.save(out_dir / f"bolt_{i:02d}.png")
        n += 1
    return n


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.is_dir():
        raise SystemExit(f"source pack not found: {src}")
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)

    rain = convert_copy(src / "6 Weather" / "Rain", "*.png", OUT_ROOT / "rain", "rain")
    snow_dir = OUT_ROOT / "snow"
    snow_dir.mkdir(parents=True, exist_ok=True)
    Image.open(src / "6 Weather" / "Snow1.png").convert("RGBA").save(
        snow_dir / "snow_tile.png"
    )
    wind = convert_copy(src / "5 Wind", "Wind*.png", OUT_ROOT / "wind", "wind")
    bolts = convert_thunder(src / "4 Thunder" / "Thunder.png", OUT_ROOT / "thunder")

    print(f"[FX] rain frames: {rain}")
    print(f"[FX] snow tile:   1 ({(OUT_ROOT / 'snow' / 'snow_tile.png').is_file()})")
    print(f"[FX] wind frames: {wind}")
    print(f"[FX] bolt frames: {bolts}")
    print(f"[FX] wrote {OUT_ROOT}")


if __name__ == "__main__":
    main()
