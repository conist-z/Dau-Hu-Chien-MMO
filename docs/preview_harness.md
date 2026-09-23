# Preview Harness — xem cơ chế ngay trên tab Preview (không cần vào Discord)

> Session 24/09/2026. Mục tiêu: sau khi làm xong 1 cơ chế (meteor, quái,
> weather, …), mở ngay 1 bản preview **chạy game THẬT** (GameManager + 20 Hz
> tick + snapshot thật, client Phaser thật — chỉ bỏ Discord) lên tab Preview
> của Freebuff để người dùng bấm nút kiểm tra từng case.

## Khởi động (mỗi session, ~30 giây)

```bash
# 0. NẾU CHƯA CÓ STACK ĐANG CHẠY — dùng keeper (tự hồi sinh khi chết):
.venv/Scripts/python scripts/preview_keepalive.py > .preview_keepalive.log 2>&1 &
#    keeper loop restart _preview_stack.py trong ~2s mỗi lần nó crash/đ bị kill
#    (Freebuff restart giết background process = nguyên nhân "preview mất").

# 1. Build client nếu src đổi (dist đã commit thì bỏ qua)
cd web_client && npm run build && cd ..
rm -rf web_client/relay/dist && cp -r web_client/dist web_client/relay/dist
# ⚠️ tạo lại app-config.json (bị rm xoá):
echo '{ "client_id": "965153822861307914", "redirect_uri": "https://web-production-19398.up.railway.app/" }' \
  > web_client/relay/dist/app-config.json

# 2. (nếu chưa chạy keeper ở bước 0) chạy stack thẳng:
.venv/Scripts/python scripts/_preview_stack.py > .preview_stack.log 2>&1 &

# 3. Mở tab Preview của Freebuff
#    register_preview -> url = http://127.0.0.1:8898/?preview=1, pid = PID
#    của process LISTENING :8898 (netstat -ano | grep :8898).
#    Nếu preview "mất" sau khi Freebuff/agent restart: STACK CÓ THỂ VẪN SỐNG
#    (keeper giữ nó) — chỉ cần register_preview LẠI với cùng URL + pid mới,
#    KHÔNG rebuild, KHÔNG đợi; mất ~10s.
```

`?preview=1` là bắt buộc: client auto guest-login + vào bigmap solo + hiện
panel 🧪 PREVIEW. Không có flag = client thường (OAuth như production).

## Kiến trúc (2 nửa, bắt tay qua WS frame `preview_cmd`)

```
Freebuff Preview tab
  └─ web_client (build thật)  ──?preview=1──>  auto guest + previewMap + panel
        │  preview_cmd {cmd, value}                        ▲
        ▼                                                  │ push (toast) +
scripts/_preview_stack.py (PreviewStack extends LocalStack) │ preview_state
        │  gọi thẳng vào game code (không qua Discord)     │
        ▼───────────────────────────────────────────────────┘
  GameManager 20Hz → build_welcome / build_snapshot (gói meteors, zombies…)
```

- `scripts/_local_game_stack.py` — nền: in-process GameManager + WS `/ws` +
  serve `relay/dist` + asset lane (`asset_request` → b64 PNG). Đã fix: routes
  đăng ký TRƯỚC `add_static("/")` (static nuốt route = 403), guest_id lạ rơi
  về PREVIEW_USER.
- `scripts/_preview_stack.py` — thêm `preview_cmd` (xem bảng dưới) + push
  log `[preview] …` + frame `preview_state`.
- `web_client/src/preview_panel.ts` — panel DOM nổi (z-index 3000, chỉ build
  khi `?preview=1`), feed log, status line. `net.sendRaw` gửi frame tuỳ ý;
  `net.onPreviewState` nhận status dump.

## Lệnh preview_cmd hiện có

| cmd | value | tác dụng |
|---|---|---|
| `clock` | `day`(12h)/`dusk`(19h)/`night`(21h)/`midnight`/`dawn`/`normal`/số giây | ghim giờ in-game (`set_ingame_time`) — mở gate đêm cho quái+meteor, đổi tint map |
| `meteor` | `here`/`rand`/`auto`/`off` | triệu hồi tại chỗ đứng / cách 6–12 ô / bật-tắt scheduler |
| `weather` | `rain`,`heavy_rain`,`storm`,`snow`,`cold`,`wind`,`sun_clouds`,`cloud_shadow`,`normal` | ép `rt.weather_key` |
| `zombies` | `pack`(spawn 10 quanh player)/`none` | đầy đủ 6 loài bigmap + cơ chế riêng từng loại |
| `map` | `ekonia/overworld`/`forest`/`cave_area1` | xoá + tạo lại runtime map khác (bộ quái theo profile) |
| `state` | — | hỏi status → `preview_state` (map, giờ, night, weather, số quái, ☄️ active, felled_tonight) |

## Thêm cơ chế mới vào panel (quy trình session sau)

1. **Server** (`scripts/_preview_stack.py`): viết `async def _cmd_<ten>`
   theo chữ ký `(cid, uid, rt, value)`, đăng ký vào dict `handler` trong
   `_preview_cmd`. Chỉ gọi game code có sẵn (spawn/summon/setter) — không
   tự viết logic game ở đây (game code phải sống trong `game/` để Discord
   cũng dùng được).
2. **Client** (`web_client/src/preview_panel.ts`): thêm 1 section + nút
   `btn("Nhãn", () => this.send("<ten>", "<value>"))` — 3 dòng.
3. Build lại client nếu bước 2 sửa → restart stack → mở `?preview=1`.
4. Người dùng bấm nút, xem ngay; log `[preview]` hiện trong panel.

## Lỗi đã gặp (đừng lặp lại)

- **Root 403**: `add_static("/")` đăng ký trước `add_get("/")` → mọi route
  sau nó bị nuốt. Luôn đăng ký route tường minh trước static.
- **`int('preview')` crash**: `guest_login` guest_id phải là số — client
  gửi `"9000000000000000042"`, stack vẫn try/except về PREVIEW_USER.
- **Frame gửi lúc module load** rơi vào void (WS chưa open) → client retry
  join bằng `setInterval` 200ms tới khi `net.isConnected`.
- **Phaser `cam.shake()` phá pixel art**: intensity là FRACTION viewport
  (0.05 = 64px!) và offset subpixel làm vỡ px + lộ map thô. Fix chuẩn trong
  `meteors.ts`: KHÔNG dùng cam.shake — tự jitter bằng `setFollowOffset`
  bo tròn số nguyên (max ±2px), decay exponential, reset về (0,0) khi xong.
- **Tab ẩn → rAF throttle**: demo/preview có thể đóng băng khi tab không
  focus; stack vẫn chạy (loop Python độc lập), chỉ client render chậm lại.
- **Preview "mất" sau restart**: Freebuff giết background process nhưng
  registration tab Preview trỏ vào port chết → dùng `preview_keepalive.py`
  (keeper tự restart stack trong 2s) + `register_preview` lại cùng URL.
- **Unicode console (cp1252)**: print tiếng Việt trong stack script bị
  `UnicodeEncodeError` → encode ascii/replace cho dòng print banner.
