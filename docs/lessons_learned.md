# Bài học từ lỗi thật (lessons learned) — ĐỌC TRƯỚC KHI DEBUG

> Mục đích: mỗi mục là một lỗi **đã xảy ra thật** trong project này, kèm triệu
> chứng → root cause → cách tránh. Khi gặp bug lạ, **soi danh sách này trước
> khi đoán mò** — phần lớn lỗi lặp lại theo mẫu quen thuộc.

## Quy tắc vàng rút ra

1. **Feature gate phải khớp dữ liệu production.** Gate viết theo id môi trường
   preview/test (`"ekonia/overworld"`) nhưng production chạy id khác
   (`"bigmap"`) → feature im lặng mãi mãi trên cloud. Mọi `== "<id>"` so sánh
   map/kind/channel phải được test với GIÁ TRỊ CỦA MÔI TRƯỜNG THẬT, không chỉ
   giá trị của preview harness. Cách tránh: chấp nhận cả bộ id (`in (...)`),
   hoặc gom hằng số chung ở một nơi (map_catalog), và luôn có lệnh debug dump
   state (kiểu `/meteorinfo`) từ đầu.
2. **Im lặng khác không tồn tại.** Vòng tick bọc try/except "crash-proof" ăn
   exception là chạy tiếp — feature chết mà không log nào. Một lệnh `/xxinfo`
   dump counter (`last_night=-1` = "tick chưa từng chạy") định vị root cause
   trong 1 câu. Mỗi scheduler/beat nên có counter + lệnh dump ngay khi viết.
3. **State vĩnh viễn trong payload = animation vĩnh viễn.** Event đã eta=0
   nhưng không bị xoá khỏi snapshot → client loop animation mỗi 1-2s. Client
   phải được thiết kế an toàn với "state kẹt" (eta=0 quá lâu → tự hủy), và
   server phải đảm bảo lifecycle: spawn → active → landed → REMOVE.
4. **Preview pass ≠ production pass.** Preview harness chạy map id, env var,
   asset set KHÁC cloud. Trước khi báo "xong", list khác biệt preview vs prod
   (map_id, clock scale, admin ids, network) và tự hỏi feature còn chạy không.
5. **Một triệu chứng có thể là 2 bug.** "Animation loop + thiếu ore" cùng
   đứng từ một root cause duy nhất (tick không chạy). Tìm điểm chung trước khi
   vá từng triệu chứng.

## Case 1 — Meteor: scheduler không chạy trên production (2026-09)

- **Triệu chứng:** `/meteor` trên web client cloud: đếm 8s đúng, xong
  animation rơi LOOP mỗi 1-2s; quặng không xuất hiện. Preview thì mọi thứ ổn.
- **Chẩn đoán:** `/meteorinfo` → `night_id=1` nhưng `last_night=-1`
  (tick chưa từng chạy), `active: 1, eta: 0s` (event kẹt), `ore nodes: 0`.
- **Root cause:** `is_meteor_map()` chỉ chấp nhận `"ekonia/overworld"` (id
  preview); production bigmap chạy id `"bigmap"` → `tick_meteors` bị gate bỏ
  qua vĩnh viễn. Event không landed → không xoá khỏi payload (loop) và không
  spawn ore (thiếu).
- **Fix:** gate chấp nhận cả hai id. Commit `82065e4`.
- **Bài học:** quy tắc 1, 2, 4 ở trên.

## Case 2 — Meteor: quặng vẽ thành 4 cục (2026-09)

- **Triệu chứng:** node 2×2 (gid -77 trên 4 tile) hiển thị 4 sprite đá nhỏ.
- **Root cause:** renderer client vẽ 1 sprite/tile; 4 sprite giống hệt nhau
  đọc thành 4 cục đá riêng.
- **Fix:** `updateResourceLayer` (game.ts) nhóm 4 tile → 1 sprite lớn anchor
  tại tile không có tile above-left, `setDisplaySize(2*tilePx)`.
- **Bài học:** node đa tile cần CONTRACT HIỂN THỊ rõ (một sprite/cụm) ngay khi
  thêm gid mới; mọi renderer (Discord PIL + Phaser) phải theo cùng contract.

## Case 3 — Build stamp cũ trên cloud sau khi đã deploy (2026-09)

- **Triệu chứng:** `/help` vẫn báo build cũ dù code đã push/deploy.
- **Root cause:** panel KHÔNG tự pull code và KHÔNG restart; deploy_files.py
  chỉ ghi file. Process cũ chạy code trong bộ nhớ.
- **Fix/bài học:** mỗi lần đổi code Python: `scripts/deploy_files.py <files>`
  rồi **Restart panel thủ công**; kèm một stamp build trong lệnh debug
  (`/meteorinfo` in `build=<hash>`) để biết process đang chạy bản nào. Web
  client thì ngược lại: push git → Railway tự build → verify bằng
  `curl .../ | grep -oE 'index-[^"]+\.js'` so bundle name.

## Case 4 — Lệnh debug tự chết vì chưa restart (2026-09)

- **Triệu chứng:** `/meteorinfo` vừa deploy xong → "Lệnh không rõ".
- **Root cause:** giống Case 3 — lệnh mới chỉ sống sau Restart.
- **Bài học:** khi thêm lệnh debug để chẩn đoán bug X, NHẮC Restart trước khi
  hỏi người dùng chạy lệnh đó, hoặc bug sẽ "không chẩn đoán được".

## Checklist chặn lỗi tương tự (đọc trước khi ship feature có scheduler/gate)

- [ ] Gate id/kind so sánh với GIÁ TRỊ PRODUCTION đã verify (in log/lệnh debug)?
- [ ] Có lệnh debug dump state cho hệ thống mới (counters, active list)?
- [ ] Lifecycle payload rõ: entry được REMOVE khỏi snapshot khi kết thúc?
- [ ] Client an toàn với state kẹt (stale eta/pos quá lâu → tự hủy)?
- [ ] Đã test cả preview VÀ một vòng thật trên cloud (Restart trước)?
- [ ] Node/resource mới: contract hiển thị đa tile thống nhất cả 2 renderer?
