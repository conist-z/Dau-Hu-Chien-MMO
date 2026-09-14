# Icon Pipeline — ÉP BUỘC (Hard Rules) — v2

> **v2 (14/09)**: viết lại sau khi phát hiện 2 lỗi pipeline thật:
> (1) `plank` từng là **item thường** → KHÔNG đặt ra map được dù có icon +
> hand sheet; (2) sheet cầm tay từng regen từ **icon 16×16 thay vì texture
> block 32×32** → art cầm tay sai tỷ lệ. Doc này là nguồn chuẩn duy nhất.

## Nguyên tắc số 0 — ITEM vs BLOCK là 2 Decide KHÁC NHAU

Trước khi làm bất cứ asset nào, trả lời câu hỏi: **vật phẩm này có ĐẶT RA
MAP được không?**

| | Item thường (thịt, thuốc, gậy…) | Block (đá, khúc gỗ, ván gỗ…) |
|---|---|---|
| Đặt ra map | ❌ | ✅ `placeable` |
| Đăng ký ở | `game/items.py` `ITEM_REGISTRY` | `game/blocks.py` `BLOCK_REGISTRY` (**+ là item trong túi qua `get_item` fallback**) |
| Block texture `assets/blocks/<id>.png` | Không cần | **BẮT BUỘC** (32×32) |
| Icon inv | `make_item_icons.py` `SOURCES` | `("block", id)` — icon CHÍNH LÀ texture block |
| Hand sheet | `make_held_sheet.py` KHÔNG `--block` | `make_held_sheet.py --block` |
| Đặt ra map (server) | `apply_place_block` trả `not_placeable` | OK — trừ 1 nguyên liệu túi |
| Đặt ra map (client web) | bị chặn: không nằm trong `blocks_catalog` | OK — client tra `welcome.blocks_catalog` |

⚠️ **Muốn một thứ vừa là nguyên liệu vừa đặt ra được → nó PHẢI là block.**
`get_item()` tự fallback qua `BLOCK_REGISTRY` nên block tự động có tên +
icon trong túi — KHÔNG cần (và không nên) đăng ký trùng trong
`ITEM_REGISTRY`.

Cảnh giác với quirk: `apply_place_block` trừ túi theo `action.block_id`
nên block id trong túi phải trùng block id. `inventory.remove("plank", 1)`
khớp vì block id = item id trong túi (cùng chuỗi).

## Ba asset bắt buộc cho mỗi id

| Asset | Đường dẫn | Hiển thị ở đâu | Sinh bởi |
|---|---|---|---|
| **Icon inv** | `assets/gui/icons/<id>.png` + `web_client/public/ui/icons/<id>.png` (16×16) | Túi, hotbar, hub, craft grid | `scripts/make_item_icons.py` |
| **Hand sheet** | `assets/players/weapon/<stem>.png` (192×432, 4×9 frame 48px) | Nhân vật cầm trên tay (Discord + web paperdoll) | `scripts/make_held_sheet.py` |
| **Block texture** (chỉ block) | `assets/blocks/<id>.png` (32×32) | Block đặt ra map (PIL renderer + Phaser) | 16×16 nguồn → ×2 NEAREST |

**Nguồn của hand sheet PHẢI là texture block 32×32** (không phải icon
16×16) — LỖI THẬT: regen từ icon làm art cầm tay mờ/sai scale.

## Ba mapping bắt buộc

1. `game/appearance.py` → `WEAPON_SHEETS`: `item_id → sheet stem`.
2. `web_client/src/appearance_client.ts` → `WEAPON_SHEETS`: mirror y hệt
   (test `tests/test_appearance.py` so khớp).
3. `assets/players/players_manifest.json` → `weapons`: entry
   `{file: "weapon/<stem>.png", frame_w: 48, frame_h: 48, cols: 4,
   rows: 9, offset_x: -8, offset_y: -24}`.

## Checklist thêm 1 id mới (chạy đúng thứ tự)

**Block (đặt ra được):**
1. `game/blocks.py`: thêm `BlockDef` vào `BLOCK_REGISTRY` → tự động vào
   `PLACEABLE_BLOCK_IDS` → tự động vào web `blocks_catalog`.
2. Tạo `assets/blocks/<id>.png` **32×32** (16×16 nguồn ×2 NEAREST).
3. `scripts/make_item_icons.py`: thêm `"id": ("block", id)` vào `SOURCES`
   → chạy script → icon 16×16 ra CẢ 2 thư mục icons.
4. `python scripts/make_held_sheet.py --icon assets/blocks/<id>.png
   --stem <id> --block`  ← flag `--block`, icon = texture 32×32.
5. Mapping `id: stem` vào **CẢ HAI** `WEAPON_SHEETS` (py + ts).
6. Entry `weapons` vào `players_manifest.json`.
7. `pytest tests/test_appearance.py tests/test_blocks.py tests/test_items.py`.

**Item thường:** bỏ bước 1–2; bước 4 KHÔNG có `--block`; nguồn icon là
kaetram/twemoji/block tuỳ loại.

## Cách hiển thị khi đặt ra map

- **Discord**: `rendering/renderer.py:block_texture_image` đọc
  `assets/blocks/<id>.png` scale NEAREST về tile; thiếu file → ô màu phẳng
  từ `BlockDef.color`.
- **Web**: Phaser fetch `blocks/<id>.png` qua relay `asset_request`;
  `onBlockTexture` đăng ký rồi vẽ full tile.
- Texture phải VUÔNG full-tile (không viền trong suốt) nếu không block
  hiển thị "nhỏ".

## Kiểm tra nhanh "còn thiếu gì" (phải rỗng cả 4)

```python
from pathlib import Path
from game.items import ITEM_REGISTRY
from game.blocks import BLOCK_REGISTRY
from game.appearance import WEAPON_SHEETS
import json
all_ids = set(ITEM_REGISTRY) | set(BLOCK_REGISTRY)
blocks = set(BLOCK_REGISTRY)
icons = {p.stem for p in Path("assets/gui/icons").glob("*.png")}
texs  = {p.stem for p in Path("assets/blocks").glob("*.png")}
mani  = set(json.load(open("assets/players/players_manifest.json"))["weapons"])
sheets = {p.stem for p in Path("assets/players/weapon").glob("*.png")}
print("thiếu hand mapping:", sorted(all_ids - set(WEAPON_SHEETS)))
print("thiếu icon:       ", sorted(all_ids - icons))
print("block thiếu texture:", sorted(blocks - texs))
print("sheet mapped thiếu PNG: ", sorted(set(WEAPON_SHEETS.values()) - sheets))
print("sheet mapped thiếu manifest: ", sorted(set(WEAPON_SHEETS.values()) - mani))
```

(Block cũng cần icon vì block hiện diện trong túi.)

## Lịch sử

- **14/09 (v2)**: `plank` đổi thành BLOCK (đặt ra được, Minecraft parity);
  texture `assets/blocks/plank.png` = floor +25% sáng; icon + hand sheet
  regen từ texture mới. 11 item thiếu hand sheet được bổ sung trước đó
  cùng ngày (`charcoal, crafting_table, dirt, floor, furnace, key_stone,
  mushroom_brown, mushroom_purple, potion_mp, raw_meat, seed`).
