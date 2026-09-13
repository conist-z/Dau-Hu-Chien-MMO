# Sandbox — đặt khối / phá khối (đã triển khai)

> Bề mặt map hiện tại là **ground** (nền đất của Tiled map). Khối người chơi
> đặt là **lớp phủ (overlay) per-tile**: đặt khối → phủ kín ô đó; phá khối →
> lớp phủ biến mất và ground lộ ra lại. Không có gì sửa trong map gốc.

## 1. Cách hoạt động

```
🧱 nút ĐẶT  ──▶ PlaceBlockAction ──▶ rules.apply_place_block ──▶ BlockGrid.place()
📦 nút CHỌN ──▶ đổi khối đang chọn (đá ➜ gỗ ➜ lá ➜ đuốc ➜ đá…)
🔨 nút PHÁ  ──▶ BreakBlockAction  ──▶ rules.apply_break_block  ──▶ BlockGrid.remove()
```

- Hành động tác động lên **ô player đang quay mặt tới** (facing tile).
- **Chế độ sinh tồn đang BẬT** (`CREATIVE_MODE = False` trong `game/blocks.py`):
  **Đặt** yêu cầu ô ground đi được, còn trống, không có người chơi đứng, và túi
  có ≥1 nguyên liệu (trừ 1); **Phá** hoàn lại +1 vào túi.
- Muốn khối vô tận (creative): đổi cờ thành `True` — đặt/phá không đụng túi.
- Khối `solid` (đá/gỗ/lá) **chặn di chuyển**; khối trang trí (đuốc) không chặn.

## 2. Files

| File | Vai trò |
|---|---|
| `game/blocks.py` | `BlockDef` + `BLOCK_REGISTRY` (data-driven) + `BlockGrid` (overlay thuần) |
| `game/actions.py` | `PlaceBlockAction`, `BreakBlockAction` |
| `game/rules.py` | `apply_place_block` / `apply_break_block` (logic, deterministic) |
| `game/collision.py` | `Collision(map, blocks)` — khối solid chặn move |
| `game/manager.py` | `dispatch` xử lý 2 action mới + persist + `load_blocks` |
| `rendering/renderer.py` | `block_tile_image()` + overlay trong `_compose_full/_compose_follow` |
| `discord_ui/map_view.py` | 3 nút persistent `b_place/b_block/b_break` (custom_id per player) |
| `persistence/` | bảng `blocks (channel_id, x, y, block_id)` + repos |
| `tests/test_blocks.py` | 12 test |

## 3. Tuỳ biến

**Thêm khối mới** — chỉ sửa `BLOCK_REGISTRY` trong `game/blocks.py`:

```python
"glass": BlockDef("glass", "Kính", "🪟", (180, 220, 235), solid=True),
```

Renderer tự vẽ (fill màu + viền tối + highlight), collision tự áp dụng,
nút 📦 tự đưa vào vòng chọn. Không sửa engine.

**Bật/tắt chế độ nguyên liệu** (`CREATIVE_MODE` trong `game/blocks.py`,
đang `False` = sinh tồn): `True` = đặt/phá không đụng túi (khối vô tận).

## 4b. Định hướng hướng mặt (facing indicators)

🧱/🔨 tác động lên **ô player đang quay mặt tới**. Ba chỉ dẫn giúp ngắm chuẩn:

1. **Turn-in-place**: bấm hướng = xoay mặt luôn; trống thì bước, bị chặn thì
   chỉ xoay (`apply_move`) — không thể bị "kẹt hướng" nữa.
2. **Highlight ô mục tiêu**: viền vàng nhạt mềm trên facing tile của chính bạn
   (`renderer.render(..., focus_user_id=uid)`) — nhìn là biết 🧱/🔨 đánh vào đâu.
3. **Mũi tên nhỏ trên token** player chỉ hướng đang nhìn (trắng mờ + viền tối).

Style cố tình trầm (`ARROW_*`, `HIGHLIGHT_*` trong `rendering/renderer.py`).

## 4c. Hotbar — 9 nút item trên D-pad (đã triển khai)

- **9 nút hotbar** ở row 3-4 của D-pad (Discord View cho đúng 5×5 nút — vừa khít).
  Mỗi nút: emoji item + `x{qty}`; ô trống disabled hiện số ô.
- **Gắn item**: mở 🎒 Inventory trên hub → chọn item ở dropdown → chọn
  "🎯 Gắn item đang chọn vào ô hotbar: Ô 1..9" (hoặc "Gỡ"). D-pad tự cập nhật
  qua edit gate + re-register persistent view.
- **Dùng item**: bấm nút hotbar trên D-pad → trừ qty, refresh hub HUD (coalesced),
  báo kết quả ephemeral. Hết item → giữ binding, label `x0`, bấm báo "hết".
- **Hub**: 9 ô hotbar (64px, giữa hub) hiển thị chữ cái đầu tên item + badge
  `xN` + số ô mờ (Tahoma không có glyph emoji màu — emoji thật nằm trên D-pad).
- **Persist**: bảng `hotbar (channel_id, user_id, slot, item_id)`, boot restore
  qua `manager.load_hotbars(rt)` — binding sống sót qua restart (rule 14).
- API: `manager.get_hotbar / set_hotbar_slot`; UI: `_handle_hotbar`,
  `_apply_hotbar_labels` (map_view), `_on_assign_slot` (inventory_view).

## 4d. Build Mode — con trỏ xây + build theo bước đi (đã triển khai)

Xây base nhanh hơn: thay vì đi 1 bước / bấm hotbar 1 lần cho từng ô, có 3 công cụ
mới trên D-pad (vẫn đúng 25 nút — tái dùng ô giữa + 3 slot trống):

- **🔧 Build Mode** (row 4): ON → D-pad **di chuyển con trỏ ô mục tiêu** (±3 ô quanh
  player, kẹp trong map) thay vì di chuyển player; ô giữa D-pad thành **✅ Đặt**
  (đặt khối đang chọn vào ô con trỏ); bấm hướng khi con trỏ chạm biên = ACK rẻ,
  không re-upload. Renderer vẽ **viền + ghost khối mờ 45%** trên ô mục tiêu
  (`_ghost_tile` / `_aim_target`) — biết trước khối gì rơi vào đâu.
- **↻ Xoay tại chỗ** (row 1): xoay mặt theo chiều kim đồng hồ qua 8 hướng mà
  **không bước** (`TurnAction` + `next_clockwise`).
- **🧱 Follow-build** (row 2): ON → **mỗi bước đi tự đặt khối đang chọn vào ô vừa
  bước qua** (trail — đi vòng là xong tường, dùng `floor` (Sàn gỗ, non-solid) để
  lát sàn/cầu). Cả đi tay lẫn auto-run 🎦. Lỗi (hết nguyên liệu, ô chặn) bị bỏ
  qua im lặng — đi bộ không bao giờ dừng.

- State: `PlayerScreen.build_mode / follow_build` (runtime, không persist);
  con trỏ là offset **tương đối** trên `Player.aim_dx/dy/aim_block`
  (`AimAction`/`AimResetAction`, clamp `AIM_RANGE = 3` trong `game/rules.py`).
- `PlaceBlockAction` nhận `dx/dy` tuỳ chọn: None = ô facing (hotbar thường),
  có offset = ô con trỏ (Build Mode / trail). Hotbar gắn khối trong Build Mode
  cũng đặt vào ô con trỏ và cập nhật khối đang chọn cho ✅/🧱.
- Aim là **view state** — không đánh dấu `rt.dirty` (không persist vô nghĩa).
- Test: `tests/test_building.py` (13 test).

## 4. Persistence & recovery

- Mỗi khối đặt = 1 dòng trong bảng `blocks`; phá = xoá dòng. SQLite chỉ lưu
  primitives (rule 19).
- Boot restore: `bot.py` gọi `manager.load_blocks(rt)` cho mỗi scenario →
  khối sống sót qua restart (rule 14).
- `/mapreset` và đổi map (`/startmap` map khác, `/bigmap`) xoá sạch blocks
  của kênh — ground trở lại nguyên trạng.
