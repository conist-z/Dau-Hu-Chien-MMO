# Mô hình di chuyển web: Client-Authoritative (+ crash-proof loops)

> Quyết định thiết kế của chủ game (2026-09-14). Ghi lại để các phiên sau
> hiểu CHÍNH XÁC cách di chuyển web hoạt động trước khi sửa bất cứ thứ gì.

## 1. Mô hình client-authoritative là gì?

Web client là **nguồn sự thật** cho vị trí thân của chính nó:

```
Client predict mỗi frame ──┐
                           ├─ input flush (≤30/s): {seq, dx, dy, running, x, y}
Server nhận ───────────────┘
  → KHÔNG tự tích phân thời gian nữa
  → KÉO thân thật (player.x_f/y_f) về vị trí client báo
  → Snapshot 20Hz echo lại vị trí đã hội tụ
```

Trước đây: server tự tính `raw_dt × speed` theo input vector — bị lệch
tích lũy (player đứng đây, hitbox bị cắn tít bên kia) mỗi lần thoát/ra vào,
đã fix nhiều lần không khỏi bệnh. Mô hình mới **loại bỏ hẳn lớp lỗi đó**:
server luôn hội tụ về đúng cái client hiển thị.

## 2. Luồng code (đừng phá những cái này)

| File | Nội dung |
|---|---|
| `web_client/src/net.ts` | `setInput(dx,dy,running,pos?)` — vị trí predicted đèo theo mỗi input frame; `flushInput()` gửi `{x, y}` làm tròn 3 số lẻ |
| `web_client/src/game.ts` | `getSelfPos()` trả vị trí predicted (`selfX/selfY`) — main.ts gọi khi set input |
| `web_client/src/main.ts` | `onVector` gọi `net.setInput(..., scene.getSelfPos())` |
| `web_api/core.py` | `_handle_input` đọc `frame["x"]/"y"` → `manager.web_input(..., report_x=, report_y=)` |
| `game/manager.py` | `WebSession` dataclass: `report_x/report_y/report_at/last_converge`; `web_input()` lưu report; `_web_tick_runtime` hội tụ (xem dưới) |

## 3. Các van an toàn trong `_web_tick_runtime` (KHÔNG bỏ đi)

1. **Tốc độ hội tụ tối đa = `WEB_RUN_SPEED`** (2× walk; ×0.5 khi đang ăn).
   Client hack vẫn bị ghìm — tối đa nhanh gấp đôi, không teleport tức thì.
2. **Swept collision** (`can_move_float`) vẫn chạy server-side → không
   xuyên tường được dù client báo vị trí trong tường.
3. **1 report = 1 lần áp dụng**: `report_at = 0.0` sau khi consume; hội tụ
   budget = thời gian từ report trước, cap 0.5s.
4. **Report hết hạn sau 1s** (socket chết im) → rơi về legacy fallback.
5. **Legacy fallback**: client cũ không báo vị trí → tích phân vector như
   cũ (có cap dt 0.5s, stamina gate, ăn chậm).
6. Cờ `running` ở input vẫn qua gate stamina server-side.

## 4. Crash-proof background loops (cùng đợt fix)

Mọi task nền dài hạn trong `game/manager.py` phải **không được chết im
lặng** — một exception thoát ra là toàn hệ thống đóng băng đến khi restart
(đã từng xảy ra: `ModuleNotFoundError: game.purse` giết `_web_tick_loop`):

- `_web_tick_loop` — isolate per-runtime, log `[WEB] tick failed ... (loop survives)`
- `_zombie_loop` — `[ZOMBIE]`
- `_smelting_loop` — `[SMELT]`
- `_weather_loop` — `[WEATHER]`
- `_lightning_loop` — per-runtime isolation
- `_session_loop` — `sessions.due()` bọc try riêng

Mẫu chuẩn khi thêm loop mới:

```python
while True:
    try:
        ...work...
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("[TÊN] tick failed (loop survives)")
    await asyncio.sleep(interval)
```

`RefreshScheduler` (discord_ui/refresh.py) và coalescer đã tự trị sẵn.

## 5. Kiến thức vận hành

- Client không gửi vị trí → server tự dùng legacy fallback, không crash.
- Đổi `WEB_RUN_SPEED`/`WEB_WALK_SPEED` ở game config là thay đổi cả tốc độ
  hội tụ lẫn tốc độ đi — chỉ cần chỉnh 1 chỗ.
- Log `[WEB] tick failed (loop survives)` xuất hiện = có lỗi thật cần sửa
  tận gốc (đừng xem nhẹ — nó đang nuốt exception để giữ loop sống).
- Restart panel sau khi upload `game/manager.py` / `web_api/core.py`;
  web client deploy qua Railway (git push).

## 6. Đảo chiều 180° (reversal) — trạng thái hiện tại (session 27/09)

Trong mô hình này server body TỤT LẠI sau prediction một khoảng
**latency × tốc độ** (~1 ô khi run). Khi player đảo chiều, khoảng tụt đó
nằm "trước mặt" theo trục mới — mọi cơ chế kéo prediction về server lúc
này đều là cú giật LÙI. Đã thử và BỎ: latch vị trí về server pos khi lật
(tàn dư thời server-integration, chính nó gây "dịch 1 ô khi spam 2 hướng
trái ngược khi run" — PC Shift lẫn mobile joystick).

Cơ chế duy nhất còn lại (game.ts):
- `setLocalInput`: phát hiện lật bằng **per-axis sign history**
  (`lastNonzeroDxSign/DySign`) — KHÔNG so với mẫu liền trước vì bàn phím
  phát mẫu (0,0) khi 2 phím trái chiều giữ cùng lúc. Lật → stamp
  `lastReversalAt`. Vị trí KHÔNG bị đụng tới.
- `applySnapshot`: trong 450 ms sau lật, phép thử `srvAhead` stand-down
  (residual = echo hợp lệ, giữ prediction, server tự hội tụ). Lỗi thật
  (jump > 4, portal, chết) vẫn snap cứng.

**LUẬT: đừng thêm cơ chế kéo/latch vị trí khi đảo chiều nữa.** Lớp echo
duy nhất cần chặn là rewind+replay trong reconcile, đã chặn ở trên.

## 7. Tests

- `tests/test_web_movement.py` — vector clamp, capped speed, portal
  migrate, orphan rebind (đã pass với mô hình mới).
- Chạy `pytest tests -q` sau MỌI thay đổi manager/core.
