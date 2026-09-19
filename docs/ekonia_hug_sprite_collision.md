# Ekonia Hug-the-Sprite Collision — Kiến thức triển khai đầy đủ

> Mục tiêu: khi player đi cạnh cây/đá/vật thể trên map Ekonia (web client), box chặn
> **ôm sát hình sprite nhìn thấy được** (thân cây chữ L, cạnh đá cong, rễ cây…)
> thay vì bị chặn bởi ô vuông ảo ("vật to mà box nhỏ ở giữa", "cây thừa viền",
> "đá box quá to"). Tài liệu này mô tả TOÀN BỘ pipeline + gotchas đã tốn nhiều
> session mới mò ra, để lần sau áp dụng cho map pack mới là việc copy-theo-checklist.

---

## 1. Nguyên lý (tại sao làm vậy)

- **Ekonia gốc (Godot)**: mỗi tile có **physics polygon** sub-tile (thân cây = L-shape
  nửa ô, đá = hình theo mép). Godot chặn theo polygon, KHÔNG chặn theo ô vuông.
- **Bản convert cũ**: chặn FULL SQUARE mọi ô mà polygon chạm dù chỉ 1px viền
  → "cây thừa viền", "đá box to hơn hình".
- **Bản hiện tại** (3 lớp, từ dữ liệu gốc → web):
  1. **Tile vẫn block square** ở tầng collision grid (Discord side không đổi,
     logic game server không đổi).
  2. **Web refinement qua `tile_masks`** (8×8 sub-cell/ô): ô poly được override
     mask = **silhouette sprite thật** (`poly_albedo`) → box player đi vào ô rồi
     bị `correct()` đẩy ra theo hình → ôm sát cạnh đá/thân cây.
  3. **Trim ô "chạm mép"**: poly không bao giờ đi vào vùng TRUNG TÂM 4×4
     sub-cell (nơi box 0.6 ô của player ở) thì CẮT khỏi collision — player
     không bao giờ chạm tới nó, giữ lại chỉ tạo cảm giác "bị chặn hụt".

Box player web: `FLOAT_BOX_HALF = 0.3` (game/collision.py) → chiếm 60% tâm ô
→ mọi phán đoán "có thể chạm tới" xoay quanh **tâm 4×4 sub-cell** (sub-cell
index 2..5 trên cả 2 trục của lưới 8×8).

---

## 2. Sơ đồ data flow

```
Ekonia .tscn/.tres (Godot source)
   │  scripts/convert_ekonia_maps.py
   │  ├─ bake art 16px -> assets/maps/ekonia/tiles/ekonia_baked.png (sheet DUY NHẤT, global)
   │  ├─ <map>.json        (Tiled JSON, grid normalized về (0,0) bằng (minx,miny))
   │  └─ <map>.solids.json (PER-MAP snapshot, cùng shift (minx,miny)):
   │        solid_cells  — chặn tile vuông (Godot flag)
   │        poly_cells   — ô có physics polygon (đã TRIM ô chạm-mép)
   │        poly_masks   — [x,y,mask] polygon tác giả 8×8 (thường là rect)
   │        poly_albedo  — [x,y,mask] SILHOUETTE sprite (alpha≥128, không nền)  ← thắng cuối
   │        above_cells  — tán cây y-sort (canvas OVER-player, fade occluder)
   ▼
game/map_loader.py  load_map('ekonia/<name>')
   ├─ OR solid_cells + poly_cells vào collision grid (square — Discord side)
   ├─ poly_masks + poly_albedo → dict {(x,y): mask}
   ▼
rendering/tile_masks.py  build_map_masks(md, blocking_gids, extra_masks=poly_masks)
   ├─ mask alpha GID thường (map khác)  — ngưỡng ≥40, dùng cho sprite riêng lẻ
   ├─ extra_masks OVERRIDE sau cùng     — Ekonia poly/albedo
   ▼
MapData.tile_masks  →  web_api/snapshots.py  "tile_masks" payload
   ▼
web_client/src/game.ts  tileMasks (Map "x,y" → mask)
   ├─ prediction: swept tile clamp (giống server byte-for-byte)
   └─ resolveSolidOverlap() = bản mirror của MapTileMasks.correct()
```

**Chỉ mask thôi là chưa đủ**: `correct()`/`resolveSolidOverlap()` CHẠY SAU swept
clamp, và chỉ đẩy player RA KHỎI pixel đục bên trong ô — ô poly vẫn phải nằm
trong collision grid (square) để sweep không đi xuyên mất.

---

## 3. Converter — `scripts/convert_ekonia_maps.py`

### 3.1. Các fix đã cài (KHÔNG được phá khi sửa)

**a) Reset cell-set theo từng map** (đầu `convert_map()`):
```python
BAKER._solid_cells.clear()
BAKER._poly_cells.clear()
BAKER._poly_core_cells.clear()
BAKER._poly_edge_cells.clear()
BAKER._poly_masks.clear()
BAKER.poly_albedo.clear()
BAKER._ysort_cells.clear()
```
`BAKER` là global chia sẻ để **sheet bake tích lũy gid ổn định xuyên map** — nhưng
cell sets là per-map. Không clear = map sau ăn cắp cells map trước
(overworld từng bị dính 8218 ô solid của map khác!).

**b) Snapshot cells NGAY TRONG `convert_map()`** và trả về
`(fname, tjson, (minx, miny), snap)` — `main()` ghi file SAU vòng lặp; nếu ghi từ
`BAKER` lúc đó thì mọi `.solids.json` nhận cells của **map cuối cùng** trong loop.
`poly_cells` ghi từ `_poly_core_cells` (đã trim), KHÔNG dùng `_poly_cells`.

**c) Trim ô chạm-mép** — trong vòng xử lý polygon:
```python
total, center, submask = _poly_solid_cells(poly, c)
if total == 0: continue
if center == 0:               # polygon không vào tâm 4×4 → player không bao giờ chạm
    BAKER._poly_edge_cells.add(c)
else:
    BAKER._poly_core_cells.add(c)
    if submask: BAKER._poly_masks[c] = submask
BAKER._poly_cells.add(c)      # set đầy đủ (chưa trim) — cho debug
BAKER._ysort_cells.add(c)
```

**d) `poly_albedo` (silhouette sprite)**: khi 1 tile CÓ polygon được bake, các
piece 16px của nó được composite vào `BAKER.poly_albedo[key]` (không có nền đất
dưới nó vì chỉ composite các piece của tile đó). Ghi ra bằng `_albedo_mask()`
(PIL BOX-resize alpha về 8×8, ngưỡng **≥128** = pixel đặc thật).

> **Vì sao không dùng alpha của sheet bake?** Sheet bake chứa CẢ NỀN ĐẤT đục
> dưới mọi sprite → alpha always-full → mask vô dụng (đã thử, fail). `poly_albedo`
> là cách duy nhất giữ alpha GỐC của sprite: composite piece của CHÍNH tile đó,
> không bao giờ composite ground.

**e) `_poly_solid_cells()` trả `(total, center, mask)`** — 3 giá trị, KHÔNG phải
bool. Hai exit sớm bbox-reject phải trả `(0, 0, 0)`.

### 3.2. Chạy

```powershell
# Từ project root (converter hardcode ROOT = thư mục ekonia-(copy)):
.venv\Scripts\python -X utf8 scripts/convert_ekonia_maps.py            # TẤT CẢ 25 map
.venv\Scripts\python -X utf8 scripts/convert_ekonia_maps.py forest     # 1 map (name .tscn)
```

---

## 4. Loader — `game/map_loader.py`

- `_load_ekonia_cells()` trả **5 thứ**:
  `(solid_cells, poly_cells, above_cells, poly_masks, poly_albedo)`.
  `_load_solids_cells()` wrap `[0]` nên vẫn OK cho caller cũ.
- Trong `load_map()`:
  ```python
  # square block (Discord + sweep):
  collision[gy][gx] = 1  cho mọi poly cell
  # web refinement:
  poly_exact_masks[(gx,gy)] = mask_từ_poly_albedo   # ALBEDO thắng (ôm hình nhìn thấy)
                                     # fallback: poly_masks (polygon tác giả)
  ...
  tile_masks = build_map_masks(md_stub, blocking_gids,
                               extra_masks=poly_exact_masks or None)
  ```
- **Shift toạ độ**: mọi cell đều `cx - ox, cy - oy` với `(ox, oy) = (minx, miny)`
  — cùng shift với layers. Sai shift = collision lệch cả khối (bệnh đã từng gặp
  với forest: lệch (34,54) rồi (15,12)).

---

## 5. tile_masks — `rendering/tile_masks.py`

- `MASK_RES = 8` (64 bit/ô, bit = `my*8 + mx`, mỗi sub-cell = 2px @16px tile).
- `build_map_masks(map_data, blocking_gids, extra_masks=None)`:
  - `blocking_gids` → mask alpha per-GID (ngưỡng ≥40, dùng cho bigmap/Kaetram
    nơi sprite đứng RIÊNG 1 tile, nền trong suốt).
  - `extra_masks` **override sau cùng** (Ekonia). Mask `None`/0 bị bỏ qua.
- `MapTileMasks.correct(x_f, y_f, box_half, collision)` — đẩy player ra theo
  "axis of least penetration", tối đa 4 pass, chỉ bên trong ô ĐÃ bị chặn square.
  Client mirror: `WorldScene.resolveSolidOverlap()` trong `web_client/src/game.ts`
  (SỬA 1 CÁI PHẢI SỬA CẢ HAI — parity byte-for-byte).

---

## 6. Web client — `web_client/src/game.ts`

- `tileMasks: Map<string, number>` nạp từ `welcome.map.tile_masks`
  (`{res, tiles:{y:{x:mask}}}` sparse — snapshot.py `to_payload()`).
- Flow mỗi frame: `stepSelf()` (swept clamp + wall slide, mirrors
  `game/collision.py`) → `resolveSolidOverlap()` (mask push-out, mirrors
  `correct()`). Bật F3 để vẽ ô đỏ kiểm chứng.
- Sub-cell size client tính từ `res` payload (không hardcode 8).

---

## 7. CHECKLIST áp dụng cho map pack MỚI (Kaetram/world_full, pack khác…)

1. **Converter** (viết/repair theo mẫu `convert_ekonia_maps.py`):
   - [ ] Bake art → MỘT sheet duy nhất; JSON Tiled normalized (0,0) bằng
         `(minx,miny)`; GHI KÈM shift vào tên biến/return.
   - [ ] Emit `<map>.solids.json` với đủ 5 key (solid/poly/poly_masks/
         poly_albedo/above). **Poly_albedo bắt buộc phải từ alpha của piece
         tile đó (không nền)** — đừng tái diễn vụ đọc alpha trên sheet bake.
   - [ ] **Clear per-map cell sets** đầu convert-map; **snapshot trong
         convert-map**, ghi file từ snapshot (không từ BAKER).
2. **Kiểm số liệu ngay sau convert** (trước khi deploy):
   ```
   python -c "import json; d=json.load(open('assets/maps/<m>.solids.json')); \
     print(len(d['solid_cells']), len(d['poly_cells']), len(d['poly_masks']), len(d['poly_albedo']))"
   ```
   Hai map KHÁC nhau phải ra số KHÁC nhau. Bằng nhau = leak.
3. **Loader**: pack mới dùng `_load_ekonia_cells` + `_resolve_tilesets` như
   Ekonia; nếu pack không có poly thì chỉ cần `solid_cells` — pipeline đã
   có sẵn phần còn lại.
4. **Deploy — CẶP ĐỒI XỨNG** (bỏ 1 trong 2 là vỡ texture toàn map!):
   - [ ] `assets/maps/<pack>/<m>.json` + `.solids.json` (gid MỚI)
   - [ ] `assets/maps/<pack>/tiles/<pack>_baked.png` (sheet MỚI cùng lượt)
   - [ ] copy sheet sang `assets/tilesets/<pack>_baked.png` (lane web client
         `asset_request` tìm ở đây) + upload — md5 hai bản phải KHỚP
   - [ ] `scripts/deploy_files.py game/map_loader.py rendering/tile_masks.py`
   - [ ] Restart panel; web Ctrl+F5
5. **Verify** (không cần vào game):
   - Overlay đỏ collision lên art (script ở §9) — đỏ phải BÁM sprite.
   - `pytest tests/test_map_loader.py tests/test_collision.py tests/test_web_movement.py -q`
   - Vào web, F3 xem ô đỏ quanh 1 cây/đá: phải ôm hình, không ô vuông lơ lửng.

---

## 8. Gotchas (mỗi cái từng "đốt" ≥1 session)

| Gotcha | Triệu chứng | Fix đã cài |
|---|---|---|
| Cell-set leak xuyên map | mọi `.solids.json` cùng số cell | clear + snapshot trong `convert_map` |
| Ghi file sau loop | mọi file nhận cells map CUỐI | snapshot per-map + trả về trong tuple |
| Đọc alpha trên sheet bake | mask full-rác (nền đục dưới mọi sprite) | `poly_albedo` composite per-tile, không nền |
| Đổi sheet mà quên đổi JSON/PNG cặp đôi | sàn nhiễu texture rác như "noise" toàn map | checklist §7.4 |
| Lane web `tilesets/` không thấy sheet trong `maps/<pack>/tiles/` | map đen thui | fallback `rglob` trong `web_api/core.py` `_handle_asset_request` + copy sang `assets/tilesets/` |
| Spawn rơi ngoài void | đứng giữa đen, đi không được | `_centered_walkable` + `_art_mask` (map_loader) |
| Sai nhỏ giữa `poly_cells` (chưa trim) và `_poly_core_cells` (trim) | "vẫn thừa viền" dù đã trim | ghi từ `_poly_core_cells`, giữ `_poly_cells` chỉ để debug |
| Edit `_poly_solid_cells` trả bool/2-giá trị | `TypeError: cannot unpack` | trả `(total, center, mask)`, exit sớm `(0,0,0)` |
| Thêm offset/lpx thiếu `math.floor(lpx/TS)` | vật lệch nửa ô theo layer offset | giữ đúng công thức `x + floor(lpx/TS) + gx` |

---

## 9. Công cụ verify nhanh

**Overlay collision đỏ lên art** (chạy local, lưu PNG ra `docs/_kaetram_previews/`):

```powershell
.venv\Scripts\python -X utf8 -c "..."
```
(Mẫu đầy đủ nằm trong lịch sử session — logic: render mọi layer từ sheet bake
→ copy — vẽ rect đỏ 2px cho mỗi bit mask trong `md.tile_masks.grid[y][x]` →
crop quanh spawn, resize NEAREST ×4.)

**Smoke test loader cả pack:**

```powershell
.venv\Scripts\python -X utf8 -c "import glob,sys,os; sys.path.insert(0,'.'); from pathlib import Path; from game.map_loader import load_map; [load_map('ekonia/'+os.path.basename(f)[:-5], Path('assets/maps')) for f in glob.glob('assets/maps/ekonia/*.json') if not f.endswith('.solids.json')]"
```

**Unit tests:** `pytest tests/test_map_loader.py tests/test_collision.py tests/test_web_movement.py -q`
(50 pass tại thời điểm viết doc).

---

## 10. Liên quan nhưng khác pipeline

- **Fade tán cây (occluder)**: `above_cells` → canvas OVER-player (depth 30) →
  fade gradient per-pixel `updateOccluderFade()` + re-upload GL `texSubImage2D`
  (handle đúng nằm ở `tex.source[0].glTexture.glTexture`; fallback
  `renderer.updateCanvasTexture`). Xem `web_client/src/game.ts`.
- **Y-sort**: `above_cells` đã trừ hàng thân cây (poly row) — player đứng TRƯỚC
  cây vẽ đè thân; tán phía trên đè player.
- Cơ chế collision ở đây KHÔNG đụng `game/resources.py` (node khai thác) và
  KHÔNG đụng Discord renderer — chỉ web.
