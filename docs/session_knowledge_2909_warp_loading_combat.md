# Session knowledge — 29/09/2026 (bổ sung)

## Warp icons + khóa vùng (world map)

- **Icon vùng**: `web_client/public/ui/kaetram/interface/warpicons/N.png` (0–13),
  builder `scripts/_build_warp_icons.py` — **PER-ICON TUNING** trong dict
  `ICON_TUNING` (size + dx/dy riêng từng icon); pixel hóa tích lũy 0.49,
  9 icon (0,1,2,6,7,9,10,11,12) nhân thêm 0.85 (`EXTRA_PIXEL` set).
- **Đĩa Kaetram**: dùng vòng tròn ANALYTIC (tâm 32, r29, viền đen 4px) —
  KHÔNG upscale mask 16px gốc (bị méo/không cân → motif "lệch phải/trái",
  báo cáo 3 lần). Nền = gradient vàng→cam lấy từ màu MÉP đĩa (median cả ô
  làm rò màu motif cũ — vệt xanh ngang).
- **Ổ khóa**: `padlock.png` (pixel art, KHÔNG dùng emoji `\1F512` — hệ
  không font emoji hiện nguyên "f512"). Locked: element `background-image:none`,
  icon xám tối 55% vẽ ở `::before` (filter trên element làm xám CẢ ổ khóa
  `::after` — bug "ổ khóa trắng đen"), ổ khóa ở `::after` **giữ màu vàng**
  + opacity 0.5.
- Khóa/mở: `WARP_REGIONS` trong `web_client/src/kaetram_menus.ts` (locked flag).
  Mở: 10 (khu chợ du hành giả → /khutraodoi in|out) + 12 (thảo nguyên →
  /cuahang ban do). Hook: `hud.onWarpRegion` (main.ts).

## Death screen

- KHÔNG text "Bạn đã gục ngã…" — chết dùng đúng màn loading veil
  (`TravelVeil.setDead` label rỗng, `hud.setDead` force-hide overlay).

## Loading veil "0% sau 100%" (báo cáo 2 LẦN)

- `travel_wipe.webm` khai báo 5.1s nhưng **chỉ ~3.6s decode được** (72/102
  frame); **frame cuối playable có bar = 0%** (frame lặp vòng).
- Fix cuối (`6b31163c`): park CỨNG 3.3s, chặn CẢ `timeupdate` lẫn `ended`
  (playback 3x có thể nhảy cóc thẳng tới ended). KHÔNG tính theo videoDur
  (metadata lag/triệu).

## Combat/harvest — TOOL THEO TAY CẦM (bug 29/09)

- LỚP 1 `game/resources.py apply_chop`: tool = item trong slot hotbar ĐANG
  GIỮ (`held_slot` param từ `rt.held_slots`); hết fallback best-in-bag.
- LỚP 2 `game/terrain_rules.py apply_scoop` (xẻng): như trên.
- LỚP 3 `game/rules.py _held_item_id` (damage vũ khí): đọc
  `state.held_slots` (manager stash vào GameState ở dispatch) — trước đây
  quét cả hotbar tìm weapon nào cũng tính.
- LỚP 4 `game/rules.py apply_break_block` (đập block đặt): right-tool bonus
  cũng theo slot đang giữ (state.held_slots), fallback cũ chỉ khi không có
  mirror (test thuần).
- `rt.held_slots` được mirror từ web `select_slot` frames (default 0);
  Discord players ở slot 0.
- **Tầm đánh tay**: `MELEE_ATTACK_RANGE = ATTACK_RANGE/2 = 1.5` tile
  (game/rules.py) — chỉ đường đánh MOB; đặt block vẫn `AIM_RANGE=3`.

## /kill (preview helper)

- `web_api/core.py _cmd_kill`: /kill = hp 0 + death flow chuẩn (respawn 5s),
  để test màn hồi sinh không cần quái. Ghi trong /help.
