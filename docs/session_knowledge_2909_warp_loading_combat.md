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

## Loading veil "0% sau 100%" (báo cáo 3 LẦN — fix cuối `8e7d66bc`)

- `travel_wipe.webm` khai báo 5.1s nhưng **chỉ decode tới t=3.5s** (frame 71
  là frame rỗng/wrap, bar 0%). Bar fill TUYẾN TÍNH frame 12→70 (đầy ở 70,
  t=3.5s).
- Thử 1+2 THẤT BẠI: park 96%/97% theo videoDur + park 3.3s chặn
  timeupdate+ended — vẫn lộ 0% vì (a) `timeupdate` chỉ bắn mỗi ~250ms REAL →
  rate cao overshoot tới 0.37s vào vùng hỏng; (b) **seek về trong webm hỏng
  đuôi có thể FAIL NGẦM** — video vẫn nằm ở frame xấu.
- **CÔNG THỨC CUỐI: PLAY SLOW + PARK EARLY + KHÔNG SEEK.** rate cố định
  1.25 (overshoot tối đa 0.31s), pause ở 3.0s (bar ~85%) → vùng tới hạn
  3.31s vẫn trong đoạn lành. `ended` là chốt an toàn cuối. Nếu sửa video
  webm mới: phải kiểm tra decode-được-bao-lâu bằng OpenCV trước khi đẻ
  logic theo thời lượng khai báo.

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
