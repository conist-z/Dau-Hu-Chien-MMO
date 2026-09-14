# Hướng dẫn xây dựng vật thể tài nguyên (Resource Node Building Guide)

> Cách thêm nấm/cây/đá/hoa/cỏ... đập được vào game. Đọc 1 lần rồi cứ theo
> checklist — không cần đọc lại code.

## Kiến trúc tổng quan (flow 1 node đập được)

```
Tiled layer (map JSON)
  → game/resources.py  (TILE_NODE_PARTS + NODE_DEFS + RESOURCE_LAYER_NAMES)
  → game/collision.py  (FORAGE_KINDS — node có chắn đường hay không)
  → game/items.py      (ITEM_REGISTRY — item rơi ra)
  → scripts/make_item_icons.py  (icon cho bag/hotbar/hub)
  → web_client/src/game.ts  (FORAGE_GIDS — animation vỡ + walk-through,
                             RESOURCE_LAYERS — loại khỏi bake nền)
  → tests/test_resources.py
```

**NGUYÊN TẮC VÀNG:** một tile tài nguyên phải xuất hiện ở đúng 1 nơi duy nhất
trên màn hình — qua `ResourceGrid.visible_tiles()` → snapshot `resources`.
Mọi bản "in sẵn" khác (bake nền map) đều phải loại layer đó ra, nếu không
đập xong tile vẫn nằm đó ("đập rồi vẫn còn" bug — đã mất 2 lần fix).

## Checklist thêm 1 loại node mới (ví dụ: hoa hồng gid 60)

### 1. Xác định GID trên map

```bash
.venv/Scripts/python -c "
import json, sys; sys.stdout.reconfigure(encoding='utf-8')
d = json.load(open('assets/maps/bigmap.json', encoding='utf-8'))
for layer in d.get('layers', []):
    if layer.get('type') != 'tilelayer': continue
    data = layer.get('data')
    if not isinstance(data, list): continue
    gids = {}
    for i, g in enumerate(data):
        g &= 0xFFFFFF
        if g: gids[g] = gids.get(g, 0) + 1
    if gids: print(layer['name'], '->', dict(sorted(gids.items())))
"
```

Lưu ý: layer Tiled data là MẢNG PHẲNG (không phải mảng 2 chiều) — index
`i` ⇒ x = `i % width`, y = `i // width`.

### 2. game/resources.py — 3 chỗ

**a) `TILE_NODE_PARTS`:** map gid → (kind, dx, dy). Node 1 ô: `(kind, 0, 0)`.

**b) `NODE_DEFS`:** định nghĩa node:
```python
"mushroom_brown": ResourceDef(
    "mushroom_brown",
    "Nấm nâu",
    hits=1,               # số đấm để vỡ
    respawn_s=120.0,      # giây hồi sinh
    drops=[("item_id", 0.10, 1)],  # (item, tỉ lệ, số lượng) — nhiều dòng = roll nhiều lần
),
```

**c) `RESOURCE_LAYER_NAMES`:** thêm tên layer Tiled (kèm bản không dấu).
Layer nào KHÔNG nằm ở đây thì tile của nó không bao giờ thành node.

### 3. game/collision.py — node có chắn đường không?

- **Chắn** (cây, đá, bụi): KHÔNG làm gì — mặc định node sống chắn đường.
- **Đi xuyên được** (cỏ, hoa, nấm — decor thấp): thêm kind vào `FORAGE_KINDS`.

### 4. game/items.py — item rơi ra

Thêm vào `ITEM_REGISTRY` (nếu item chưa có):
```python
"seed": ItemDef("seed", "Hạt giống", "🌱", "material", {}, "Mô tả"),
```

### 5. Icon — scripts/make_item_icons.py

```python
"seed": ("kaetram", "seed"),        # sprite trong kaetram_extract/04_items/sprites/
"stone": ("block", "stone"),        # hoặc lấy chính asset block
"potion_hp": ("kaetram", "flask", (1.0, 0.32, 0.32)),  # có tint màu
```
Rồi chạy:
```bash
.venv/Scripts/python scripts/make_item_icons.py
```
Sinh ra `assets/gui/icons/<id>.png` + `web_client/public/ui/icons/<id>.png`.
Nhớ thêm id vào `ITEM_ICONS` (web_client/src/pixel_ui.ts) — không thì web
fallback emoji.

### 6. web_client/src/game.ts — 2 chỗ

**a) `RESOURCE_LAYERS` (trong hàm bake nền map, ~dòng 544):** thêm tên layer
(đã fold dấu, lowercase) vào Set — nếu không, bản bake nền sẽ in node
VĨNH VIỄN và đập không mất. **Đây chính là bug "đập rồi vẫn còn".**

**b) `FORAGE_GIDS` (~dòng 68):** node nào đi xuyên được + vỡ thành cát thì
thêm gid. Node chắn đường + ngã kiểu cây thì KHÔNG thêm (tự dùng
`playFallAnimation`).

Cả 2 đều phải đồng bộ với `game/resources.py` `TILE_NODE_PARTS` — sai gid
là node không vỡ hoặc không xuyên được.

### 7. Đồng bộ Discord renderer (không cần sửa)

`rendering/renderer.py` tự loại layer qua `resource_layer_names` (từ
`grid.layer_names`) — thêm layer ở bước 2c là chạy.

### 8. Tests — tests/test_resources.py

Các assert số tile/node dùng con số TỔNG trên bigmap — thêm node mới thì
update: `len(g.nodes)`, `len(g.visible_tiles())`,
`len(kw["resource_tiles"])`, set `resource_layer_names`.

## Animation vỡ (client, game.ts)

- **Forage (vỡ thành cát):** `playShatterAnimation` — 8 hạt fan lên, tint
  lấy dominant color của tile texture (`sampleTextureTint`). ~0.5s.
- **Cây/đá (ngã):** `playFallAnimation` — rung → nghiêng 14° → mờ.

Chọn animation: tile gid của node nằm trong `FORAGE_GIDS` → shatter,
ngược lại → fall.

## Đập — flow server (đã có sẵn, không cần sửa)

`apply_chop` (game/resources.py): progress += 1 mỗi đấm (tired = +0.5), đủ
`hits` → `grid.chop()` → tile biến khỏi `visible_tiles()` → snapshot nặng
đổi signature → client rebuild resource layer + chạy animation. Drop roll
từ `drops` → spawn "linh khí" (game/drops.py).

Đi xuyên / chắn đường: server `game/collision.py` `is_walkable` (cả movement
grid lẫn swept float — dùng chung hàm), client `solidAt` (prediction).
**Phải sửa cả 2 bên** — chỉ sửa server thì client vẫn thấy bị chặn (prediction
chặn trước khi server kịp báo).

## Deploy sau khi thêm node

```bash
.venv/Scripts/python -m pytest tests -q -p no:warning       # 441/441
.venv/Scripts/python scripts/deploy_files.py game/resources.py game/items.py [game/collision.py]
# web (nếu đổi game.ts/icons):
cd web_client && npm run build
rm -rf relay/dist/assets && cp -r dist/* relay/dist/
git add ... && git commit && git push origin main
```
Panel: bấm Restart. Web: Railway tự build ~2 phút.
**Lưu ý:** `cp -r dist/* relay/dist/` có thể XÓA `relay/dist/app-config.json`
nếu dist không có — luôn kiểm tra file này còn sau khi copy (đã mất 1 lần).

## Lỗi đã gặp (đừng lặp lại)

| Bug | Nguyên nhân | Fix |
|---|---|---|
| Đập rồi tile vẫn còn | Layer bị bake vào ảnh nền map (client bake + Discord renderer base) | Thêm layer vào `RESOURCE_LAYERS` client + kiểm tra `resource_layer_names` server |
| Đập rồi tile vẫn còn (lần 2) | Client bake nền không loại layer forage mới | Thêm vào `RESOURCE_LAYERS` — luôn thêm khi thêm layer mới |
| Player không đi xuyên được | Client prediction `solidAt` chặn mọi node đứng, không phân biệt kind | Thêm gid vào `FORAGE_GIDS` (client) + kind vào `FORAGE_KINDS` (server) |
| Test fail đột ngột sau khi thêm node | Số tile tổng trong test là cứng | Update số trong test_resources.py |
| relay app-config.json biến mất | `cp -r dist/*` ghi đè thư mục relay/dist | Khôi phục từ git (đã có sẵn commit backup) |

## Refs trong code

- Node registry: `game/resources.py` (`TILE_NODE_PARTS`, `NODE_DEFS`, `RESOURCE_LAYER_NAMES`, `apply_chop`)
- Collision: `game/collision.py` (`FORAGE_KINDS`, `is_walkable`)
- Item: `game/items.py` (`ITEM_REGISTRY`)
- Icon pipeline: `scripts/make_item_icons.py`
- Client bake: `web_client/src/game.ts` `buildWorld` (RESOURCE_LAYERS)
- Client animation: `web_client/src/game.ts` (`playShatterAnimation`, `playFallAnimation`, `FORAGE_GIDS`, `sampleTextureTint`, `gidOf`)
- Client prediction: `web_client/src/game.ts` `solidAt`
