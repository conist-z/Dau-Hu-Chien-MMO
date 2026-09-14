# Web Client — Kinh nghiệm build & kiến trúc (session 14–15/09)

Tài liệu tổng hợp toàn bộ kinh nghiệm của session: kiến trúc movement
client-authoritative, thời tiết world-space, hotbar drag, và các cái bẫy
đã vượt qua. Đọc trước khi đụng vào net/movement/weather/UI của web client.

> Bổ trợ: `docs/client_authoritative_movement.md` (mô hình movement chi tiết),
> `docs/web_client_session_knowledge.md` (deploy fast path).

---

## 1. Kiến trúc tổng quan (dòng dữ liệu)

```
Browser (Phaser + DOM HUD)
  ├─ input.ts      : keyboard/mouse → scene.setLocalInput + net.setInput(dx,dy,run,pos)
  ├─ game.ts       : PREDICTION (client chạy trước) + replay buffer theo seq
  ├─ net.ts        : input 20Hz (kèm x,y) + welcome/snapshot/inv_delta
  ├─ weather.ts    : overlay thời tiết world-space (canvas 2D, trên Phaser canvas)
  └─ ui.ts / pixel_ui.ts : HUD DOM, hotbar, túi, craft (pixel-perfect v5 kit)

Server (Python, panel)
  ├─ web_api/core.py      : WS frame handler → manager.dispatch / web_input
  ├─ game/manager.py      : _web_tick_loop 20Hz — CONVERGE body về report client
  ├─ web_api/snapshots.py : snapshot 20Hz (players, weather, clock, blocks…)
  └─ persistence/         : SQLite (scenarios có weather_key + weather_manual)
```

### Quy tắc vàng (đừng phá)

1. **Client là nguồn sự thật cho vị trí player** (user's design). Server KHÔNG
   tự tích phân thời gian khi có report — nó **converge** thân thật về report,
   speed-capped. Mọi fix desync phải đi theo hướng này, không phục hồi
   server-side integration.
2. **Idle heartbeat bắt buộc**: timer input KHÔNG tự tắt khi đứng yên. Khi
   `!p.dirty`, `flushInput()` gửi zero-vector + vị trí hiện tại 20Hz
   (`net.idlePosHook = () => scene.getSelfPos()`). Không có heartbeat = ghost
   hitbox quay lại (đã xác nhận 3 lần).
3. **Server phải converge cả nhánh IDLE**: trong `_web_tick_runtime`, nhánh
   `dx==0 and dy==0` từng `continue` trước khi đọc report → đứng yên là trôi.
   Giờ gọi `self._converge_to_report(...)` TRƯỚC khi continue. Sửa gì ở
   nhánh moving phải nhớ nhánh idle.
4. **Không bao giờ bóp chết tick loop**: mọi vòng nền (`_web_tick_loop`,
   `_zombie_loop`, `_weather_loop`, `_smelting_loop`, `_lightning_loop`,
   `_session_loop`) phải try/except per-iteration, log `(loop survives)`.
   1 exception không được giết task (vụ `game.purse` cũ: game đơ toàn server).

---

## 2. Movement client-authoritative (trạng thái hiện tại)

### Client gửi gì
- Mỗi input flush (20Hz): `{type: MSG_INPUT, seq, dx, dy, running, x, y}` —
  x/y là vị trí predicted (tile float, làm tròn 3 số).
- Idle heartbeat: cùng frame, dx=dy=0, x/y = vị trí hiện tại.
- Seq liên tục, snapshot ack `last_seq`, scene replay unacked inputs.

### Server xử lý thế nào (`game/manager.py`)
- `web_input()` lưu `sess.report_x/y/at` (+ dx,dy,running).
- `_converge_to_report()`: kéo `player.x_f/y_f` về report với
  `step_budget = min(0.5, now - max(report_at, last_converge))`,
  `max_speed = WEB_RUN_SPEED * 1.6` (siết 15/09: converge nhanh hơn run),
  swept-collision `can_move_float`. 1 report = 1 lần áp (`report_at = 0`).
- Freshness: `0 < now - report_at < 1.0` — report cũ hơn 1s bị bỏ.
- Fallback legacy (client cũ không báo vị trí): tích phân dt-cap như cũ.
- Speeds: `config.py` — WALK 2.5, RUN 4.15 tiles/s, TICK 20Hz. KHÔNG tăng
  speed để "chữa" desync nữa — desync giờ được xử bằng heartbeat + converge.

### Những cái bẫy đã ngã (đừng lặp lại)
| Bẫy | Triệu chứng | Fix đúng |
|---|---|---|
| Nhánh idle `continue` trước converge | đứng yên lệch, đánh quái lệch | converge trong idle branch |
| Timer idle tự tắt | vị trí không được báo khi đứng | heartbeat giữ timer chạy |
| `raw_dt` không cap | socket chết âm thầm → thân chạy hàng chục ô | cap 0.5s/tick |
| last_tick không đóng dấu khi player chết/vanish | dt đông cứng → teleport khi hồi sinh | stamp `last_tick` ở mọi early-continue |
| Converge đúng 1x run speed | residual trôi chậm, "lâu lâu vẫn lệch" | 1.6x run + budget floor 2 ticks |
| Zoom camera hard-code 2.0 | lệch khi zoom đổi | hook trả `cam.zoom` live |

**Debug desync:** bật log panel — nếu thấy lệch, kiểm tra thứ tự: (1) client
có gửi x/y không (network tab WS frames), (2) server có vào converge không
(thêm log tạm trong `_converge_to_report`), (3) zoom client bao nhiêu.

---

## 3. Thời tiết world-space (`web_client/src/weather.ts`)

### Nguyên tắc
- **Lớp hạt (mưa/tuyết/gió/mây) = world-space**: offset camera
  `scroll × zoom × parallax` (gần 0.72×, xa 0.5×, mây 1.0× full anchor).
- **Khí quyển (tint, veil mây đen, gust, sét) = screen-space** — không phụ thuộc camera.
- Hook camera: `weatherFx.setCameraHook(() => ({x: cam.scrollX, y: cam.scrollY, zoom: cam.zoom}))`
  trong main.ts. **Zoom phải đọc live** — hard-code 2.0 gây lệch khi zoom khác.
- Canvas fx nằm TRÊN Phaser canvas, `pointer-events:none`, input bind vào
  `game.canvas` (KHÔNG `querySelector("#game-root canvas")` — canvas weather
  mount trước → selector bắt nhánh canvas pointer-events:none, click chết).

### Các key + hành vi
| key | hiệu ứng | mây |
|---|---|---|
| rain | sheet rain, alphaBoost 2.1 | 20 bóng |
| heavy_rain | 300px/s, boost 2.3 | 24 |
| storm | + sét (bolt pack, 4-11s, 35% distant) | 24 |
| snow/cold | **procedural** (mỗi bông riêng: size 1.6-5.2, sway 2 tần số, twinkle) | 0 |
| wind | wind master 1024px tự vẽ (parity Discord) | 0 |
| fog | procedural mist (web-only) | 0 |
| sunny/sun_clouds | không hạt; **server remap → cloudy nếu đêm** | 0 |
| cloud_shadow | CHỈ bóng mây (lệnh test /clouds) | N (cap 8) |

### Mây bóng trên đất (ekonia parity)
- Sprite `web_client/public/ui/fx/cloud/cloud.png` (80×36, **trắng gốc** —
  phải pre-tint thành `rgb(33,36,51)` bằng `source-atop` offscreen 1 lần,
  vẽ raw = blob trắng sai).
- 4 bóng mặc định; mưa 20-24 (user: dày hơn 5-6 lần); đời 28s; fade 12% đầu/cuối;
  trôi 9px/s hằng tốc độ; alpha 0.16; scale 9-16× (800-1280px — user yêu cầu to).
- **Anchor world px trong khung nhìn camera hiện tại + margin 50%** — seed theo
  screen px là biến mất khi đi (bug đã ngã). Render = `(world − scroll) × zoom`.
- **Offset camera phải modulo theo kích thước dải hạt** (`mod(off, wrap)`) —
  không modulo là offset tăng vô hạn → toàn bộ hạt trôi khỏi màn hình
  (bug "tuyết biến mất" đã ngã).

### Transition (user rule: đổi trời không được đùng 1 phát)
- 3.2s staged: veil đen dần → gust burst → hạt mới fade in dưới veil → veil tan.
- Ra nắng ngược lại: hạt tắt trước, trời sáng sau. Reuse nguyên logic cũ.

### Server guard đêm
`web_api/snapshots.py::_web_weather_key`: key `sunny`/`sun_clouds` lúc đêm
in-game (<6h hoặc ≥21h) → remap `cloudy`. Bắt mọi đường set kể cả admin
`/setweather sunny` nửa đêm. `cloud_shadow` pass qua (lệnh test).

### Persistence thời tiết (server-authoritative)
- `scenarios` bảng: `weather_key`, `weather_manual` (migration tự ADD COLUMN).
- Ghi DB tại: web `/setweather`, Discord `/setweather`, resume Tự Động,
  auto-adopt trong `_weather_loop`. Restore trong `bot.py` boot loop.
- Không có persistence = thoát/restart là mất thời tiết (user đã gặp).

### Lệnh test
- Web chat: `/clouds [0-8]` (admin) — force bóng mây không đụng thời tiết.
  `clouds_override` bay trong snapshot, client force key `cloud_shadow`.

---

## 4. Inventory / hotbar / craft

- **Hotbar mirror bag**: slot N hiển thị `bag[N]` (positional projection).
  Drag trong hotbar = `moveBag(from,to)` + debounce reorder 400ms.
- **Drag ra ngoài mọi panel** (bag HOẶC hotbar) = vức cả stack (server op
  `throw`, NO_COLLECT_WINDOW 1.2s chống nhặt lại ngay).
- **Q** = vức 1 đơn vị từ ô đang chọn (`hud.throwHeldStack()` qty=1).
- `pointInPanels()` PHẢI bao gồm `hotbarEl` — thiếu là thả đè hotbar bị vức nhầm.
- Craft grid = pure client buffer; CREATE gửi multiset 1 lần; server validate.
- Purse: drag coin/crystal vào ô cùng loại = nạp; drag icon purse ra = rút 1.
- Plank: là **BlockDef placeable** trong `game/blocks.py` (đặt được như block),
  đồng thời là ItemDef material. Icon tay cầm lấy từ texture block, không emoji.

---

## 5. Deploy loop (bắt buộc thuộc)

```powershell
# 1. Test nhanh
.venv\Scripts\python -m pytest tests -q

# 2. Web client đổi? build + copy vào relay dist (KHÔNG được quên!)
cd web_client; npm run build
cd ..; rm -rf web_client/relay/dist; cp -r web_client/dist web_client/relay/dist
# recreate web_client/relay/dist/app-config.json nếu bị xóa (client_id + redirect_uri)
git add ...; git commit; git push origin main   # Railway tự deploy

# 3. Python đổi? fast deploy từng file rồi Restart panel
.venv\Scripts\python scripts/deploy_files.py game/manager.py config.py
# hoặc full tree: .venv\Scripts\python scripts/upload_tree.py
```

**Bẫy đã ngã:**
- `cp -r dist/* relay/dist/` có thể **xóa app-config.json** nếu nó nằm trong
  dist cũ — luôn kiểm tra file tồn tại sau copy, mất là OAuth chết.
- Railway chỉ deploy những gì **commit vào `web_client/relay/dist`** — commit
  source mà quên commit dist = "không thấy thay đổi gì" (user đã gặp).
- Panel KHÔNG tự pull code — Python phải upload (deploy_files/upload_tree)
  rồi bấm Restart thủ công.

---

## 6. Kiến thức vặt đã xác nhận

- Mob sprites: mỗi kind cell size RIÊNG (skeleton 48×48, spider 35×35, bat
  32×48, rat 6 cột) — đọc `kaetram_extract` sprites.json, KHÔNG đăng ký
  grid 32×32 chung. Scale hiển thị bảng riêng (`MOB_SHEETS` trong game.ts).
- `/spawnmob <kind> [qty]` (admin web): spawn vòng 2-4 ô quanh NGƯỜI GỌI.
- Admin web check `WEB_ADMIN_IDS` (.env) TRƯỚC guild permission — cache lạnh
  guild không được chặn admin list.
- Vòng lặp crash-proof pattern: `try: ... except asyncio.CancelledError: raise
  except Exception: log.exception(...)` — 1 world lỗi không giết task chung.
- Thời tiết auto: `game/weather.py::sample_key` đã chặn sunny lúc đêm theo
  `is_day_ratio` của trạm thật; server guard snapshot là lớp 2.
- Fix 1 lỗi phải verify bằng simulation/test viết ngắn (python -c) TRƯỚC khi
  tự tin "đã fix" — heartbeat từng bị "fix" mà server bỏ report ở nhánh idle
  (lỗi nặng hơn), vì chỉ test nhánh moving.

---

## 7. Việc chưa làm / backlog nhỏ

- Converge 1.6x run speed: nếu thấy rubber-band mạnh khi mất kết nối lại,
  hạ về 1.2x hoặc thêm ease-out.
- Spider poison DoT, slime projectile — mob cơ bản đã có, nâng cấp sau.
- `docs/client_authoritative_movement.md` nên merge vào doc này để còn 1 nguồn.
