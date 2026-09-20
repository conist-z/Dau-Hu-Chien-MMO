"""Smoke-test: load every Ekonia map with the current loader."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from game.map_loader import load_map  # noqa: E402

assets = Path(__file__).resolve().parents[1] / "assets" / "maps"
ok = fail = 0
for p in sorted(assets.glob("ekonia/**/*.json")):
    if p.name.endswith(".solids.json"):
        continue
    mid = p.relative_to(assets).with_suffix("").as_posix()
    try:
        md = load_map(mid, assets)
        ok += 1
        if mid.endswith("forest"):
            n_masks = sum(1 for row in md.tile_masks.grid for m in row if m is not None) if md.tile_masks else 0
            print(f"forest: ysort={len(md.ysort_cells)} partial_masks={n_masks}")
    except Exception as e:  # noqa: BLE001
        fail += 1
        print("FAIL", mid, e)
print(f"{ok} ok, {fail} fail")
