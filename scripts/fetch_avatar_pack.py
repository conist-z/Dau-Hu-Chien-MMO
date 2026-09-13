"""One-time fetch of the bundled default avatar pack (dev machine only).

Downloads a curated set of Twemoji PNGs (repo: jdecked/twemoji, CC-BY 4.0)
into ``assets/avatars/twemoji/`` and writes ``assets/avatars/manifest.json``
so the bot can offer them as default player avatars WITHOUT any runtime
network access (the hosting container has no trusted egress).

Usage (local):
    .venv/Scripts/python scripts/fetch_avatar_pack.py

The generated files are committed with the project; players never run this.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "avatars" / "twemoji"
MANIFEST = ROOT / "assets" / "avatars" / "manifest.json"

BASE = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72"

# Curated defaults: (unicode, Vietnamese label, category).
PACK = [
    # --- Động vật ---
    ("1f436", "Mặt chó", "animals"),
    ("1f431", "Mặt mèo", "animals"),
    ("1f43b", "Gấu", "animals"),
    ("1f428", "Koala", "animals"),
    ("1f437", "Mặt lợn", "animals"),
    ("1f43c", "Gấu trúc", "animals"),
    ("1f427", "Chim cánh cụt", "animals"),
    ("1f98a", "Cáo", "animals"),
    ("1f438", "Ếch", "animals"),
    ("1f435", "Khỉ", "animals"),
    ("1f434", "Mặt ngựa", "animals"),
    ("1f42f", "Mặt hổ", "animals"),
    ("1f981", "Sư tử", "animals"),
    ("1f430", "Mặt thỏ", "animals"),
    ("1f433", "Cá voi", "animals"),
    ("1f42d", "Chuột", "animals"),
    # --- Khuôn mặt ---
    ("1f600", "Cười tươi", "faces"),
    ("1f60e", "Lạnh lùng", "faces"),
    ("1f973", "Mặt quái dị", "faces"),
    ("1f92a", "Điên điên", "faces"),
    ("1f608", "Mặt quỷ", "faces"),
    ("1f47b", "Con ma", "faces"),
    ("1f9d1-200d-1f9d2", "Gia đình", "faces"),
    ("1f916", "Robot", "faces"),
    ("1f60d", "Mắt trái tim", "faces"),
    ("1f621", "Tức giận", "faces"),
    ("1f976", "Choáng lạnh", "faces"),
    ("1f635", "Xỉu", "faces"),
    ("1f602", "Cười chảy nước mắt", "faces"),
    ("1f921", "Mặt hề", "faces"),
    ("1f47e", "Quái vật game", "faces"),
    ("1f634", "Buồn ngủ", "faces"),
    # --- Đồ ăn ---
    ("1f354", "Hamburger", "food"),
    ("1f355", "Pizza", "food"),
    ("1f32d", "Bánh mì kẹp", "food"),
    ("1f35f", "Khoai tây chiên", "food"),
    ("1f363", "Sushi", "food"),
    ("1f363", "Sushi", "food"),
    ("1f35c", "Mì tô", "food"),
    ("1f35a", "Cơm", "food"),
    ("1f366", "Kem", "food"),
    ("1f370", "Bánh kem", "food"),
    ("1f34e", "Táo đỏ", "food"),
    ("1f34c", "Chuối", "food"),
    ("1f347", "Nho", "food"),
    ("1f345", "Cà chua", "food"),
    ("1f951", "Bơ", "food"),
    ("1f351", "Đào", "food"),
    ("1f36d", "Kẹo gậy", "food"),
    ("1f36b", "Sô-cô-la", "food"),
    # --- Fantasy ---
    ("1f9da", "Tiên", "fantasy"),
    ("1f9dc", "Nàng tiên cá", "fantasy"),
    ("1f9db", "Vampire", "fantasy"),
    ("1f9df", "Zombie", "fantasy"),
    ("1f9cc", "Troll", "fantasy"),
    ("1f409", "Rồng", "fantasy"),
    ("1f9e9", "Mảnh ghép", "fantasy"),
    ("1f531", "Trident", "fantasy"),
    ("26ea", "Nhà thờ", "fantasy"),
    ("1f3f0", "Lâu đài", "fantasy"),
    ("1f9ff", "Gậy phép", "fantasy"),
    ("1f31f", "Sao lấp lánh", "fantasy"),
    ("1f4a5", "Nổ", "fantasy"),
    ("1f525", "Lửa", "fantasy"),
    ("2728", "Lấp lánh", "fantasy"),
    ("1f48e", "Đá quý", "fantasy"),
    ("1f5ff", "Tượng Moai", "fantasy"),
]

# Curated list review pass: drop accidental duplicates while keeping one entry.
_seen = set()
_unique = []
for codepoint, label, cat in PACK:
    if codepoint in _seen:
        continue
    _seen.add(codepoint)
    _unique.append((codepoint, label, cat))
PACK = _unique

ATTRIBUTION = (
    "Twemoji by Twitter/jdecked fork — CC-BY 4.0 — https://github.com/jdecked/twemoji"
)


def codepoint_to_file(codepoint: str) -> str:
    # Twemoji file naming strips VS16 ('fe0f') when the base codepoint exists.
    parts = codepoint.split("-")
    if len(parts) > 1 and parts[-1] == "fe0f":
        stripped = "-".join(parts[:-1])
        if (OUT_DIR / f"{stripped}.png").exists():
            return f"{stripped}.png"
    return f"{codepoint}.png"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    failed = []
    for codepoint, label, cat in PACK:
        fname = codepoint_to_file(codepoint)
        path = OUT_DIR / fname
        if not path.exists():
            url = f"{BASE}/{codepoint}.png"
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    data = resp.read()
                path.write_bytes(data)
                print("fetched", fname, len(data), "bytes")
                time.sleep(0.05)
            except Exception as e:  # noqa: BLE001 — one bad emoji must not abort the pack
                print("MISS", codepoint, e)
                failed.append(codepoint)
                continue
        entries.append(
            {"id": f"twe:{fname[:-4]}", "unicode": codepoint, "label": label, "category": cat}
        )
    manifest = {
        "attribution": ATTRIBUTION,
        "source": "https://github.com/jdecked/twemoji (CC-BY 4.0)",
        "categories": ["animals", "faces", "food", "fantasy"],
        "category_labels": {
            "animals": "Động vật",
            "faces": "Khuôn mặt",
            "food": "Đồ ăn",
            "fantasy": "Fantasy",
        },
        "avatars": entries,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"manifest written: {len(entries)} avatars ({len(failed)} failed)")


if __name__ == "__main__":
    main()
