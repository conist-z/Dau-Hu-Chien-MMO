# Web Client — Kiến thức tổng hợp (session 09/2026)

> Tài liệu gói toàn bộ kiến thức, kiến trúc, bug đã fix và quy trình debug của
> session xây web client. Đọc trước khi đụng vào `web_client/`, `web_api/`,
> `web_client/relay/` hoặc thay đổi luồng Discord bot ↔ web.

## 1. Kiến trúc tổng thể

```
Browser (Phaser 3 + TypeScript, Vite build)
   │  wss://…/ws
   ▼
Railway relay (Node.js — web_client/relay/relay.js)
   │  • serve static dist/ (web client đã build)
   │  • /ws  = WebSocket browser (không auth, chỉ định tuyến)
   │  • /bot = WebSocket bot (auth header X-Relay-Token)
   ▼  1 socket multiplexed, envelope có "cid" = connection id browser
Bot Python (panel Pterodactyl, dial OUT tới relay — không mở port inbound)
   │  web_api/relay_client.py → WebHub (web_api/core.py)
   ▼
GameManager → GameState/Actions/Rules → SQLite
```

- **Bot CHỦ ĐỘNG quay ra kết nối relay** (`RELAY_URL=wss://…/bot`) — panel bot
  không mở được port inbound. Reconnect backoff vĩnh viễn.
- Mỗi frame browser đi trong envelope `{"cid": N, "frame": {...}}`; reply đi
  ngược lại cùng hình dạng. Chỉ `client_connected`/`client_gone` là bookkeeping.
- `web_api/core.py` không đụng socket — `WebHub` nhận/trả dict; test được mà
  không cần WebSocket thật.

### Deploy 2 chỗ — NHỚ: 2 host khác nhau
| Thay đổi | Đẩy lên đâu | Kích hoạt |
|---|---|---|
| Code Python (`web_api/`, `game/`, `config.py`…) | Panel bot qua `scripts/deploy_files.py <file…>` (vài giây) hoặc `scripts/upload_tree.py` (đầy đủ, lâu) | **Bấm Restart bot** trên panel |
| Web client TS / relay | `cd web_client && npm run build` → copy `dist/` vào `web_client/relay/dist/` (NHỚ tạo lại `app-config.json` — lệnh `rm -rf + cp` nó biến mất) → `git push` | Railway **tự deploy** ~2 phút |

```powershell
# Quy trình chuẩn sau mỗi thay đổi web client:
cd web_client; npm run build
rm -rf relay/dist; cp -r dist relay/dist
# tạo lại web_client/relay/dist/app-config.json (client_id + redirect_uri)
cd ..; .venv\Scripts\python scripts\deploy_files.py web_api\core.py   # nếu đổi Python
git add … ; git commit; git push github-dauhu main; git push origin main
```

### Cấu hình relay (panel NexNode KHÔNG có UI env variables)
- `relay-config.json` cạnh relay.js + **fallback hardcoded** trong relay.js
  (`client_id = 965153822861307914`). `/config.json` cũ đã bỏ — client đọc
  **file tĩnh `dist/app-config.json`** (sửa không cần restart).
- `.env` trên panel bot có `RELAY_TOKEN` + `RELAY_URL` (config.py có fallback
  cứng vì panel có lúc không load được .env).

## 2. Auth hiện tại
- **Quick-play guest**: browser sinh `guest_id` (900xxx…) lưu localStorage →
  `guest_login` frame → token `guest:<id>`. Token **chỉ sống trong RAM bot** —
  bot restart là chết, client PHẢI re-login (đã handle).
- **Discord OAuth** (code xong nhưng panel relay không restart được nên chưa
  chạy thật): flow PKCE, `login` frame → bot đổi code. Redirect phải khớp
  Dev Portal. Có thể bật lại khi chuyển relay sang Railway.

## 3. Client boot flow (main.ts) — tự phục hồi
1. URL có `?code=` → OAuth path. Ngược lại `connect()` WS.
2. Có `web_token` guest → **luôn re-login** (token server-side chết sau restart).
3. Có token OAuth cũ (không phải guest) → gửi `list` → nếu server trả
   `not_joined`/`bad_token` → **xóa token + re-arm quick-play** (không treo).
4. 10s không join → hiện lỗi cụ thể thay vì treo vĩnh viễn.

## 4. Các bug ĐÃ FIX (đừng lặp lại)

### Transport / protocol
- **Snowflake precision**: Discord channel id > 2^53 — JSON.parse của Node làm
  tròn đuôi `…00` → join sai kênh → `scenario_missing_or_full`. **Mọi id
  Discord qua relay phải là STRING**, server parse `int(str(...))`. Ghi vào
  AGENTS.md mental model: id = string xuyên suộc web.
- **`list` bị chặn sau cổng `not_joined`**: frame `list` (chọn map) là bước
  TRƯỚC join nhưng bị gate `conn.joined` → không bao giờ tới. Pre-join frames
  (`list`, `select_slot`) phải xử lý trước gate, chỉ cần có session.
- **Session không bind vào connection lúc guest login** → reply route theo
  `conn.session` không tìm thấy → reply biến mất. Bind NGAY lúc login.
- Stale OAuth token + bot restart = dead-end "đang kết nối". Phải wipe + re-arm.

### Rendering (Phaser)
- **Vẽ map = bake 1 canvas duy nhất** (`bakeMapIfReady`): vẽ tile bằng Canvas2D
  1 lần → 1 texture. Từng là ~30k `add.image` → GPU chết (siêu lag). KHÔNG
  quay lại per-tile images cho nền.
- **Texture tới SAU khi build world**: tileset PNG đi qua relay asynchronously.
  Mọi code build bằng texture phải có cơ chế rebuild khi `onTilesetLoaded` —
  và **reset signature cache** khi rebuild (bug "mất cây": layer cây build lúc
  chưa có texture → mọi tile skip → sig cache chặn rebuild vĩnh viễn).
- **Cây/bụi/quặng = layer động riêng** (`updateResourceLayer`, ~310 tile image
  là OK) từ payload `resources` (visible tiles x,y,gid do server gửi). Nền bake
  **loại trừ** các tile này (resourceSet) nếu không cây bị chặt sẽ thành ghost
  trong canvas. Chặt xong server gửi lại list → layer sync → cây biến mất thật.
- Camera zoom IN = `setZoom(1.6)`. Zoom < 1 là zoom OUT (đã hiểu nhầm 1 lần).

### Movement / input
- **Client-side prediction**: self di chuyển local 60fps (`stepSelf`), va chạm
  tile axis-separated, tốc độ mirror server (walk 4 / run 6 ô/s). Server
  snapshot chỉ reconcile khi lệch > 2 ô (teleport/trap) hoặc kéo nhẹ khi lệch
  lâu — KHÔNG chống lại chuyển động bình thường (đó là lý do từng có lag
  50-100ms).
- Camera `startFollow(selfMarker)` — marker trắng VIÊN CHỐT được prediction
  lái, không phải container nội suy của remote players.
- **Chuyển màn hình→world tile PHẢI dùng `camera.getWorldPoint()`** — math thủ
  công scrollX + x/zoom bị lệch khi zoom ≠ 1 (bug "khung xanh lệch nặng").
- Khung hover xanh: chỉ `setPosition` khi **đổi ô tile** + throttle 50ms —
  theo từng pixel chuột là lag/drift.
- Diagonal chuẩn hóa; `_web_direction` server dùng dominant-axis 8-way —
  client `dominantDir` mirror y hệt.

### Tương tác (chặt/đập/đặt)
- Mô hình học từ **Kaetram-Open** (MPL2.0, chỉ học mô hình không copy code):
  hover cursor theo ngữ cảnh, target = ô facing/aim, resource có progress +
  exhausted state.
- Server đã có sẵn: `ChopAction` (target = aim cursor khi Build Mode, else
  facing tile; tool tốt nhất tự chọn từ túi; ore cần cúp tier dirt+),
  `BreakBlockAction`, `PlaceBlockAction` (AIM_RANGE=3, cấm ô mình đứng),
  `AimAction`/`AimResetAction` (cursor offset theo player), `TurnAction`.
- Web actions: `attack`, `chop`, `break`, `shovel`, `place` (block_id rỗng →
  server tự lấy `aim_block` hoặc nguyên liệu placeable đầu tiên trong túi),
  `turn` (8-way).
- `action_result` frame echo về client: ok/reason/tx,ty/kind/drops → client
  toast lý do fail (too_hard, regrowing, no_material…) + loot khi hạ node.
- `res_progress` trong snapshot: `"ax,ay" -> hits` → client vẽ **thanh tiến độ
  xanh** trên node đang bị chặt.
- Hotbar: chọn slot (1-8 / wheel / click) = **đổi tay cầm, KHÔNG tự dùng đồ**,
  không spam chat; server echo frame `held`.
- **Layer `cây` bigmap từng KHÔNG chặn**: normalize NFKD biến "cây"→"cay" nhưng
  code so `"cây" in nl` → cây thành bàn đạp. Giờ `"cay" in nl` chặn (trừ
  "cay chet" cây chết + "cay cau" cầu — giữ nguyên behavior test pin).

### Weather
- Server sample key từ bộ ĐẦY ĐỦ: `sun_clouds, sunny, cloudy, heavy_clouds,
  rain, heavy_rain, storm, snow, cold, wind` (+`sun, clouds, fog` legacy).
  Client WEATHER_ICONS phải map đủ 13 key — thiếu là hiện ❓ ("lúc được lúc
  không" thực ra là key lạ).

## 5. Test / debug nhanh (không đoán mò)
- **Smoke test live flow** (chạy từ máy dev, không cần browser):
  ```python
  # ws → railway /ws → guest_login → list → join → welcome (như các lần debug)
  ```
  Xác định lỗi nằm ở: relay (frame không xuống /bot), bot (frame tới nhưng
  không reply), hay client (nhận nhưng không render).
- **Hub test offline**: `WebHub.handle_envelope` với fake manager — test flow
  guest_login → list → join mà không cần socket (web_api test đã có).
- Log panel: mọi dòng quan trọng dùng `print(..., flush=True)` vì console
  panel nuốt INFO logging (`[WEB] relay connected` là print).
- `scripts/deploy_files.py <file…>`: deploy vài file Python trong ~3 giây qua
  SFTP — KHÔNG dùng upload_tree cho thay đổi nhỏ.
- Railway tự deploy mỗi git push; kiểm tra bản served bằng cách fetch JS bundle
  và tìm chuỗi đặc trưng (vd `channel_id:String`).

## 6. Ý tưởng tiếp theo (không có bug đang treo — bug được fix riêng ở session khác)
- **OAuth Discord thật**: Railway có auto-deploy nên có thể bật lại, đổi
  redirect_uri trong app-config.json + Dev Portal (bot đã có `web_api/auth.py`).
- Block selector trên web (phím B) thay vì set 🧱 bên Discord.
- Shake animation + particle khi chặt (Kaetram có `resource.shake()`).
- Avatar player thật (cần OAuth) — đang là ô vuông màu (xanh=self, cam=Discord
  player, xanh lá=web player khác).
