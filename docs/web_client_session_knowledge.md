# Web Client — Kiến thức tổng hợp (session 09/2026)

# ╔══════════════════════════════════════════════════════════════════╗
# ║  🚨🚨🚨  DISCORD CLIENT ĐANG TẠM NGƯNG PHÁT TRIỂN  🚨🚨🚨          ║
# ║                                                                  ║
# ║  TẤT CẢ các thay đổi UI / rendering / tính năng MỚI chỉ nhắm     ║
# ║  vào WEB CLIENT (web_client/, web_api/, relay).                  ║
# ║                                                                  ║
# ║ discord_ui/, rendering/ (Discord renderer), message D-pad, hub   ║
# ║ message, screen/hub pair trên Discord — CHỈ giữ ở mức "không     ║
# ║ hỏng" (bugfix sống còn), KHÔNG thêm tính năng mới vào đó.        ║
# ║                                                                  ║
# ║ Nếu task có vẻ liên quan Discord UI → ĐỌC LẠI YÊU CẦU: 99% là    ║
# ║ người dùng muốn bên WEB. Đừng sửa discord_ui/rendering để thêm   ║
# ║ tính năng — chỉ chạm khi game engine game/ dùng chung.           ║
# ╚══════════════════════════════════════════════════════════════════╝

> Tài liệu gói toàn bộ kiến thức, kiến trúc, bug đã fix và quy trình debug của
> session xây web client. Đọc trước khi đụng vào `web_client/`, `web_api/`,
> `web_client/relay/` hoặc thay đổi luồng Discord bot ↔ web.

## 0. ⚠️ THẢM HỌA 15/09 — MẤT MẶT ĐẤT 2 TIẾNG: BÀI HỌC ĐỌC TRƯỚC KHI SỬA BAKE

**ĐÂY LÀ LỖI ĐẮT NHẤT SESSION — mọi fix gid/tilecount/cache đều là hướng SAI
vì thủ phạm nằm ngoài chúng hết.**

### Diễn biến
- Triệu chứng: **mặt đất + building biến mất hoàn toàn** trên web client;
  cây/đá/cỏ/nấm VẪN hiện bình thường. Fix gid (commit `af6c611`, `2754051`,
  `4c19767` với tilecount) xong vẫn đen;怀疑 cache,怀疑 payload,怀疑 asset lane —
  **2 TIẾNG không ra**.

### Nguyên nhân gốc (1 dòng bị xoá)
- Commit **`37cdec2`** (fix chop-lag: "node tiles never baked") viết lại **phần
  đuôi** của `bakeMapIfReady` và **VÔ TÌNH XOÁ MẤT dòng
  `this.textures.addCanvas("map-bake", canvas)`**.
- Chuỗi hậu quả: canvas VẪN được vẽ ĐÚNG 100% (mọi gid đúng, mọi crop đúng —
  nên mọi mô phỏng/kiểm chứng offline đều "pass") → nhưng image `map-bake`
  tham chiếu texture **KHÔNG TỒN TẠI** trong Phaser → Phaser im lặng vẽ
  **KHÔNG GÌ** cho image đó → mặt đất biến mất.
- **Cây vẫn hiện** vì chúng đi ĐƯỜNG KHÁC: server gửi `resources` [x,y,gid] →
  client vẽ sprite riêng trong `updateResourceLayer` (không qua map-bake).
  Chính sự "nửa đen nửa hiện" này đã **CHE MẤT** bản chất lỗi: mọi dữ liệu
  nhìn đều đúng, chỉ thiếu đúng 1 dòng đăng ký texture.

### Vì sao debug 2 tiếng không ra (bài học quy trình)
1. **Mọi verification đều đi theo hướng dữ liệu** (gid → tilecount → asset →
   cache) vì các bước vẽ canvas nhìn "đúng" trong code — không ai nghĩ rằng
   canvas vẽ xong rồi nhưng KHÔNG ĐƯỢC ĐĂNG KÝ vào Phaser.
2. **Mô phỏng offline không phát hiện được**: mô phỏng PIL/Python vẽ canvas
   xong xuôi rồi thoát — không có tầng Phaser để nói "texture không tồn tại".
   Mô phỏng chỉ verify dữ liệu, KHÔNG verify pipeline render.
3. **Không có runtime instrumentation**: nếu sớm hơn có 1 dòng
   `console.assert(this.textures.exists("map-bake"), "BAKE NOT REGISTERED")`
   sau bake thì ra ngay trong 2 phút.
4. **Merge nhiều session đụng chung `game.ts`**: session khác sửa phần đuôi
   bake cùng lúc, dòng addCanvas nằm trong vùng bị viết lại → mất âm thầm.

### LUẬT CỨNG khi đụng `bakeMapIfReady` (web_client/src/game.ts)
1. **Canvas PHẢI được `this.textures.addCanvas("map-bake", canvas)` SAU CÙNG
   khi vẽ, TRƯỚC khi `add.image`/`setTexture` dùng key đó.** Không dòng này
   = mặt đất biến mất âm thầm, KHÔNG có error nào cả.
2. Rebake an toàn: `if (this.textures.exists(key)) this.textures.remove(key);`
   trước `addCanvas` (addCanvas lên texture đang tồn tại là no-op + warning).
3. **Sửa phần đuôi bake xong PHẢI chạy tay qua checklist:**
   `(a) canvas vẽ xong? (b) addCanvas gọi? (c) image/setTexture dùng đúng key?`
4. Khi "mặt đất mất nhưng cây còn": **nghi ngờ pipeline đăng ký texture trước**,
   đừng lao vào gid/payload. Cây sống = resource-sprite path (không qua bake);
   bake chết = chỉ nền chết. Đó chính là signature của lỗi này.
5. Thêm instrumentation rẻ để bắt hồi quy: sau bake, `console.assert(
   this.textures.exists("map-bake") && this.mapBake?.texture.key === "map-bake",
   "[BAKE] texture không được đăng ký — mặt đất sẽ không render")`.

### Kiến thức phụ rút ra cùng đợt (đều đã fix + deploy)
- **`ERR_HTTP_HEADERS_SENT` giết relay trên Railway**: `res.setHeader()` SAU
  `res.writeHead()` là throw → relay chết ngay request đầu tiên → Railway báo
  "Application failed to respond". Header phải đưa VÀO map writeHead
  (`res.writeHead(200, { "Content-Type": …, "Cache-Control": … })`). Đã test
  cục bộ trước khi push (`node relay.js` + curl header check).
- **Cache-Control cho relay static**: `index.html` = `no-store` (shell cũ trỏ
  bundle đã xoá → SPA fallback trả HTML về cho request JS → map đen dai dẳng
  dù Ctrl+F5), `/assets/*` = `immutable, max-age=1 năm` (tên file có hash —
  an toàn tuyệt đối). SPA fallback (không tìm thấy file) cũng phải `no-store`.
- **`app-config.json` là file OAuth CỦA CLIENT**: redirect_uri PHẢI là URL
  trang web (`https://web-production-…up.railway.app/`), KHÔNG PHẢI URL
  `discord.com/oauth2/authorize?…`. Nhét nhầm URL authorize vào redirect_uri
  → Discord báo "Invalid OAuth2" khi login. File này biến mất mỗi lần
  `rm -rf relay/dist` — phải tạo lại ĐÚNG NỘI DUNG ngay sau copy dist.
- **Vite dev proxy để debug client local**: target phải `https://` +
  `changeOrigin: true` (target `wss://` và thiếu changeOrigin đều fail SNI
  "Host: localhost is not in cert's altnames"). Xem `web_client/vite.config.ts`.
- **E2E probe từ máy dev** (không cần browser): python aiohttp ws →
  `guest_login` → `list` → `join` → đón `welcome`, rồi kiểm tra payload thật
  của server (tilesets có tilecount? layers nested? assets về đủ?). Probe
  `asset_request` từng sheet để xác nhận lane. KHÔNG cần đoán mò khi probe
  được trực tiếp.

---
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

## 1b. 📱 MOBILE WEB CLIENT — LUẬT ĐỒNG BỘ PC ↔ MOBILE (21/09)

**Web client là MỘT codebase duy nhất phục vụ CẢ desktop (PC) và mobile
(điện thoại/tablet). KHÔNG có 2 client riêng.** Mọi thay đổi khi làm việc
với web PC đều phải được kiểm tra trên mobile — và ngược lại. Đây là luật,
không phải gợi ý.

### Luật cứng khi đụng web_client/
1. **Một tính năng mới trên PC = phải dùng được trên mobile** (hoặc bị
   ẩn có chủ đích trên mobile). Fix bug trên PC → kiểm tra bug tương đương
   trên mobile. Fix bug trên mobile (touch, viewport, z-index…) → kiểm tra
   không vỡ desktop (lớp mobile ẩn hoàn toàn trên PC).
2. **UI/UX viết theo mobile-first khi chạm vào HUD/overlay**: bất kỳ element
   DOM mới nào phủ màn hình phải tự hỏi "trên điện thoại nó đè lên gì?".
   Z-index trên mobile: game canvas < weather/daynight (1) < #overlay HUD (2)
   < #mobile-controls (3, chỉ bật trong game) < HUD pieces cao (4–6) <
   popup/gate (cao hơn). Một lớp phủ màn hình đặt sai z-index sẽ nuốt click
   đăng nhập (bug đã gặp — xem bên dưới).
3. **Input phải đi qua CÙNG chuỗi cho cả 2 nền tảng**: touch controls tái
   dùng đúng hook của bàn phím/chuột (MobileControls → KeyboardInput keys →
   emit() → Net). KHÔNG BAO GIỜ viết đường input song song riêng cho mobile
   — prediction, seq'd inputs, server path phải giống hệt PC.
4. **Tap/long-press = click trái/phải**: mọi handler chuột mới (onCanvasAction
   primary/secondary) phải có đường tap/long-press tương đương trong
   `mobile_controls.ts`. Thêm action chuột mới trên PC mà quên mapping tap
   là mất tính năng trên mobile.
5. **Camera API dùng chung**: zoom/pan của mobile (`applyPinchZoom`,
   `applyLookPan` trong game.ts) hoạt động cả trên PC nếu gọi — không viết
   logic camera riêng cho desktop mà mobile không thấy được.
6. **Viewport meta**: `index.html` có `viewport-fit=cover + user-scalable=no`
   — pinch trên điện thoại điều khiển CAMERA (pinch-zoom trong
   mobile_controls.ts), KHÔNG BAO GIỜ là page-zoom của trình duyệt. Đừng xóa
   meta này khi sửa head.
7. **Test cả 2 trước khi push**: build xong, mở Chrome devtools → device
   emulation (iPhone/Pixel) kiểm: (a) login gate bấm được, (b) D-pad hiện ra
   sau khi vào game, (c) tap đập block, (d) pinch zoom. Desktop kiểm: không
   có nút cảm ứng nào hiện, WASD/chuột không bị ảnh hưởng.

### Kiến trúc mobile (file liên quan)
- `web_client/src/mobile_controls.ts` — toàn bộ lớp cảm ứng: D-pad 8 hướng
  + nút ⚔️/🎒 + mặt LOOK (tap = primary, long-press = secondary, drag = pan,
  pinch = zoom). Inject DOM bằng JS, không đụng index.html.
- CSS: block `#mobile-controls` cuối `styles.css` — ẨN mặc định, chỉ hiện
  khi `@media (pointer: coarse), (max-width: 820px)` VÀ có class `.mc-on`
  (bật trong `onWelcome`, tắt trong `onConnectionChange(false)` — vì gate/
  lobby nằm trong #overlay DƯỚI lớp này; bật sớm = nuốt nút đăng nhập —
  bug đã sửa commit `7bb0717`).
- `game.ts` camera API: `applyPinchZoom(factor)` (1.2–4.0×),
  `applyLookPan(dx,dy)` (offset follow-point, tự trôi về player khi đi bộ),
  `resetLookPan()`.
- `main.ts`: `MobileControls` hooks — `setDir` ghi vào KeyboardInput.keys,
  `onTapWorld`/`onLongPressWorld` chạy CÙNG pipeline với
  `onCanvasAction` primary/secondary của PC.

### Deploy mobile = deploy PC (một thể)
Mobile không có build/deploy riêng: `npm run build` → copy `relay/dist/` →
`git push` (Railway) là ra cả 2. Không bao giờ tách nhánh mobile.

```powershell
# Quy trình chuẩn sau mỗi thay đổi web client:
cd web_client; npm run build
rm -rf relay/dist; cp -r dist relay/dist
# ⚠️ BẮT BUỘC tạo lại web_client/relay/dist/app-config.json (lệnh rm -rf + cp
# xoá nó biến mất — thiếu/sai file này = Discord OAuth "Invalid OAuth2"):
# { "client_id": "965153822861307914",
#   "redirect_uri": "https://web-production-19398.up.railway.app/" }
# redirect_uri = URL TRANG WEB, KHÔNG PHẢI URL discord.com/authorize!
cd ..; .venv\Scripts\python scripts\deploy_files.py web_api\core.py   # nếu đổi Python
git add … ; git commit; git push github-dauhu main; git push origin main
```

### Cấu hình relay (panel NexNode KHÔNG có UI env variables)
- `relay-config.json` cạnh relay.js + **fallback hardcoded** trong relay.js
  (`client_id = 965153822861307914`). `/config.json` cũ đã bỏ — client đọc
  **file tĩnh `dist/app-config.json`** (sửa không cần restart).
- `.env` trên panel bot có `RELAY_TOKEN` + `RELAY_URL` (config.py có fallback
  cứng vì panel có lúc không load được .env).

## 1c. 🖥️ LAG WEB PC — HỒ SƠ HOÀN CHỈNH (21–22/09, ĐÃ CHỐT)

> **Tình trạng: ĐÃ FIX SẠCH và VERIFY bằng đo đạc trên máy thật** (commit
> `a49bbba`). Mục này là hồ sơ đầy đủ để session sau: (1) hiểu đúng bài học,
> (2) không lặp lại các đợt fix đi vòng, (3) biết ngay phải đo gì khi có
> report lag mới. **Đọc từ đầu đến cuối trước khi đụng vào render/netcode.**

### Triệu chứng người dùng báo

- **Di chuyển trên PC thì giật/gừn ("lúc nhanh lúc chậm"), đứng yên thì mượt.**
  Đặt/đập block, mở túi, kéo item… đều bình thường — CHỈ movement bị.
- **Mobile (cùng map, cùng server) mượt** → loại trừ server, mạng, netcode
  ngay từ đầu. Bài toán nằm 100% ở client PC.
- **bigmap và ekonia/forest đều bị** (forest nặng nhất vì nhiều tán lá).
- Con số loading `3/0` trên PC vs `1/0` trên mobile từng bị nghi là lỗi —
  xem mục "Con số x/0" bên dưới: KHÔNG phải lỗi.

### Vì sao khó tìm: JS logic không hề chậm

Đo bằng stack local (`scripts/_local_game_stack.py` + preview thật):

- `stepSelf` (prediction mỗi frame): **0.056 ms**
- Toàn bộ `scene.update`: **0.77 ms**
- Toàn bộ `scene.render`: **0.22 ms**

Mọi thứ trong JS đều rẻ. Nhưng rAF (khung hình trình duyệt) rơi từ 60 → 2–4 fps
khi đi. Tức là thời gian bị nuốt ở tầng **nằm ngoài JS**: compositor/GPU.
Đây là lý do mọi đợt "đọc code tìm hàm chậm" đều bế tắc — không có hàm nào
chậm cả khi code review.

### Bài học số 1 (quan trọng nhất): PHẢI ĐO, đừng đọc-code-suy-diễn

Chuỗi sự kiện thật của 2 session: 6+ lượt fix dựa trên suy đoán (watchdog,
dedupe bake, held-key input, replay-rewind, converge kẹt tường, powerPreference)
— mỗi cái đều hợp lý trên giấy và đều KHÔNG phải gốc rễ. Cái chốt bệnh là
**instrument từng hàm bằng `performance.now()` trong game đang chạy thật**:

```js
// Đo từng hàm của scene khi đang di chuyển (chạy trong console):
const proto = Object.getPrototypeOf(scene);
const targets = ['stepSelf','updateOccluderFade','updateDrops','updateSplats',
  'syncZombies','updateZombieFrames','updateHoverSquare','findNearestNpc'];
const stats = {};
for (const name of targets) {
  const fn = proto[name];
  if (typeof fn !== 'function') continue;
  let total = 0, n = 0, max = 0;
  proto[name] = function(...a) {
    const t0 = performance.now();
    const r = fn.apply(this, a);
    const dt = performance.now() - t0;
    total += dt; n++; if (dt > max) max = dt;
    return r;
  };
  stats[name] = { get total() { return total; }, get n() { return n; }, get max() { return max; } };
}
// → đi bộ 3 giây, rồi đọc stats: hàm nào totalMs vọt lên chính là thủ phạm
```

**Quy tắc cứng: report "lag" KHÔNG được fix gì trước khi có số đo chỉ tay
vào đúng hàm.** Số liệu sau cùng:

| Chỉ số | Trước fix | Sau fix |
|---|---|---|
| `updateOccluderFade` (mỗi bước ~0.25 ô) | **59 ms** | **1.1 ms** |
| Tổng fade trong 5s đi bộ | 163 ms | 10 ms |
| Frame spike khi đi | ~1000 ms | 66 ms |

### Thủ phạm thật: fast-path upload GPU bị vô hiệu bởi tên field sai

Bối cảnh: map Ekonia có lớp tán lá (`map-above`, canvas 2D **3584×2896** ≈
41 MB pixel) được bơm lên texture GPU. Mỗi khi player di chuyển ~0.25 ô, hàm
`updateOccluderFade` (game.ts) tính lại vùng mờ quanh player rồi đưa lên GPU.
Nó có 2 đường:

1. **Đường nhanh (đúng thiết kế):** chỉ upload vùng 96×96 px quanh player qua
   `gl.texSubImage2D` — vài trăm micro-giây.
2. **Đường fallback (thảm họa):** `renderer.updateCanvasTexture` — re-upload
   **TOÀN BỘ 41 MB** mỗi bước chân.

Code chọn đường bằng cách probe raw WebGLTexture:

```ts
// TRƯỚC (sai, âm thầm):
const raw = wrapper?.glTexture;        // → undefined trên Phaser 3.60+

// SAU (đúng, commit a49bbba):
const raw = wrapper?.webGLTexture ?? wrapper?.glTexture;
```

**Nguyên nhân gốc: Phaser 3.60 đổi tên field** — `WebGLTextureWrapper` expose
`.webGLTexture`, không còn `.glTexture`. Từ ngày project nâng Phaser lên 3.90,
phép probe luôn `undefined` → **luôn** rơi vào đường fallback, re-upload 41 MB
mỗi 0.25 ô di chuyển. Không crash, không warning, chỉ lag — bug câm điếc điển
hình của API-drift.

**Vì sao mobile mượt mà PC lag:** chi phí re-upload + compositor scale theo
kích thước cửa sổ/DPR. Mobile màn nhỏ → mỗi lần upload "chỉ" tốn vài ms, không
thấy. PC cửa sổ lớn → hàng chục ms mỗi lần = giật thấy rõ. Kèm thêm GC dồn
đống lên upload lớn tạo spike ~1 giây.

**Bài học:** khi upgrade engine/framework, mọi đoạn code chạm property nội bộ
của engine phải được verify lại (log 1 lần lúc boot: "fast path ON/OFF"). Một
câu `console.assert(raw, "occluder fast path dead")` đã chặn được bug này từ
đầu.

### Các fix đã lắp kèm (đều verify, giữ nguyên)

1. **Day/night tint chuyển vào trong canvas Phaser** (`daynight_phaser.ts`,
   commit `7b50177`): trước đây tint ngày/đêm là 1 canvas DOM 2D phủ toàn cửa
   sổ trên canvas WebGL — browser phải blend 2 lớp mỗi frame khi di chuyển
   (đứng yên màn tĩnh thì bỏ qua recomposite → nên "chỉ giật khi đi"). Giờ là
   2 `Rectangle` GPU scroll-immune (depth 2000/2001) trong scene, cùng công
   thức màu (`tintFactor`/`castColor` export từ `daynight.ts`). Canvas DOM bị
   bỏ hẳn → chỉ còn 1 canvas cho compositor.
2. **Weather canvas dpr = 1** (commit `a49bbba`): lớp 2D cuối cùng còn lại
   trên game canvas; hạt mưa/tuyết mềm, không cần device pixel — dpr 2 chỉ
   nhân 4 chi phí blend.
3. **Movement throttle cho fade**: recompute mỗi 0.25 ô thay vì mỗi frame
   (gradient trượt mượt mà mắt không phân biệt, chi phí /6).
4. **Stationary fast path**: đứng yên thì skip toàn bộ fade work (đã có từ
   trước, giữ nguyên).

### Con số x/0 khi load map — KHÔNG PHẢI LỖI, đừng fix theo

`beginLoadTracking` đếm số blocking asset (tileset PNG + block faces) chưa có
trong cache texture của browser. PC cache khác mobile nên số khác nhau: PC
thấy `3/0`, mobile `1/0` — cả hai đều bình thường, counter tự ẩn khi đủ sheet
(có safety-timer 12s). **Đã có 1 session đốt 3 lượt fix (watchdog gate
`everWelcomed`, dedupe bake theo sig, defer-when-loading) đuổi con ma "triple
map-load" diễn giải từ con số này.** Những fix đó vô hại (phòng ngừa reconnect
oan thật sự) nhưng KHÔNG phải gốc của bất kỳ report lag nào.

**Bài học: hiểu ý nghĩa của một counter/đồng hồ trước khi coi nó là lỗi.**
`x/0` = "x asset đã xong / tổng chưa được set" — đọc `beginLoadTracking` +
`tickLoading` trong ui.ts trước khi kết luận.

### Bug `?fx=` tắt oan weather (đã sửa, đừng lặp)

Khi viết công tắc bisect `perf.ts` (xem mục "Cầu chì"), bản đầu viết:
`weather: raw === null || raw === "0" ? false : ...` — URL KHÔNG có tham số
(`raw === null`) rơi vào nhánh **false** → mặc định TẮT SẠCH weather/daynight/
cave. Người dùng mất hiệu ứng thời tiết mà nguyên nhân nằm ở "file debug vô
hại". Đã sửa (`3434a4d`): `null` = bật hết; chỉ `?fx=0`/`?fx=off` mới tắt.

**Quy tắc: mọi kill-switch debug phải default-ON khi param vắng mặt, và phải
test ngay 1 lần sau khi viết (load trang không tham số → kiểm tra FX còn).**

### Cầu chì bisect có sẵn (dùng TRƯỚC khi sửa code)

- **`?fx=` trên URL** (`web_client/src/perf.ts`): `?fx=0` tắt mọi overlay •
  `?fx=weather` / `?fx=daynight` / `?fx=cave` chỉ bật đúng lớp đó • không
  tham số = bật hết. F3 in `fx=...` để xác nhận lớp nào đang sống.
- **F3 debug line**: `fps`, `pred/srv/d=` (drift prediction↔server),
  `in=/ack=/rtt=` (throughput input), `snap=…/s`.
- **Stack local có sẵn**: `scripts/_local_game_stack.py` (chỉnh `LATENCY_MS`
  để mô phỏng trễ relay) + mở client thật → đo trên chính máy dev, không cần
  chờ user test. Lưu ý console Windows cần `PYTHONIOENCODING=utf-8`.

### Checklist khi có report "lag/giật" (làm theo thứ tự)

1. **Hỏi triệu chứng theo hành vi**: chỉ movement? chỉ thao tác? mọi lúc?
   "Chỉ movement" → thủ phạm chạy theo vị trí player (fade, camera, mask…).
   "Mọi lúc" → overlay/compositor. "Thỉnh thoảng" → GC/resize/bake.
2. **Hỏi fps lúc giật (F3)**: fps cao mà giật = mạng/netcode; fps thấp = render.
3. **Test mobile cùng map**: mượt = client PC; lag cả hai = server/mạng.
4. **Instrument từng hàm** (đoạn code ở trên) khi di chuyển — tìm hàm có
   `totalMs` vọt lên. KHÔNG fix gì trước khi có số.
5. **Bisect `?fx=`** để khoanh vùng lớp overlay nếu instrument không chỉ ra.
6. Với bug GPU/upload: kiểm tra **fast path có đang chạy không** (log/assert
   lúc boot) — đừng tin rằng nó chạy chỉ vì code "có vẻ đúng".
7. Đừng fix theo `[DESYNC] d=1.5–3` ở IDLE — hành vi chấp nhận từ trước
   (mục 5b). Đừng "fix" counter `x/0` — nó không phải lỗi.

### Tóm tắt 1 dòng cho session sau

> Lag PC "chỉ khi di chuyển" = `updateOccluderFade` upload cả canvas 41 MB
> mỗi bước vì probe `wrapper.glTexture` chết sau khi Phaser 3.60 đổi thành
> `webGLTexture` — PHẢI đo từng hàm khi đi bộ thật để bắt; đọc code không
> bao giờ thấy.



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
  1 lần → **`textures.addCanvas("map-bake", canvas)`** → 1 texture. Từng là
  ~30k `add.image` → GPU chết (siêu lag). KHÔNG quay lại per-tile images cho
  nền. **⚠️ addCanvas là DÒNG SỐNG CÒN — mất nó = mất mặt đất âm thầm, xem
  mục 0 ở đầu tài liệu (thảm hoạ 15/09) TRƯỚC khi sửa hàm này.**
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

### Chết / hồi sinh (hp 0) — "bị nhốt ở vòng tròn vô hình"
- `Player.alive` = `hp > 0 AND dead_until is None`. `dead_until` là **EPHEMERAL,
  KHÔNG persist** và respawn 5s do một task in-memory giữ → bot restart / runtime
  bị dẹp / chết trong **side world** đều để lại `hp 0` + không deadline + không
  task ⇒ `alive` False VĨNH VIỄN: đứng chôn chân, mọi action trả `"dead"`, overlay
  web đếm từ 0. Đã fix bằng `Player.revive_if_expired(now)` gọi ở 3 chỗ: boot
  (`bot.py`, cứu mọi row hp<=0), `GameManager.dispatch` (action kế tiếp tự hồi) và
  `_web_tick_runtime` (tự hồi trong 1 tick khi đang di chuyển). **Thêm chỗ chết
  mới ⇒ gọi helper này**, đừng chỉ dựa vào task respawn.
- `_respawn_after` phải tìm world bằng `runtime_of(channel_id, user_id)`: tìm
  `runtimes[channel_id]` không thấy player trong side runtime → return, chết luôn.
- Phía client: snapshot có `self.dead`/`respawn_s` → `stepSelf` đóng băng
  prediction tại vị trí authority + `hud.setDead` (đừng để ghost tự đi rồi bị
  reconcile kéo về — đó chính là "vòng tròn vô hình").

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

### Mouse / hover / click (session 10/09 — mấy lần "mất ô chuột")
- **CANVAS ĐÚNG = `game.canvas` (Phaser), TUYỆT ĐỐI KHÔNG `querySelector("#game-root canvas")`**:
  canvas weather-fx được mount vào `#game-root` **TRƯỚC** khi Phaser khởi tạo
  → selector lấy canvas ĐẦU TIÊN = canvas thời tiết (có `pointer-events: none`)
  → MỌI mouse listener gắn nhầm: không hover box, không click, chuột phải lọt
  menu Chrome (menu Chrome hiện ra = dấu hiệu chắc chắn listener sai canvas).
  Fix: `main.ts` bind input vào `game.canvas` trực tiếp (chờ `game.events.once(READY)`).
- **Nguồn vị trí chuột**: DOM `mousemove` trên canvas là nguồn gốc; hover box
  derive lại tile **mỗi frame** qua `camera.getWorldPoint` (không cache tile —
  camera cuộn dưới con trỏ đứng yên làm cache stale). Click (attack/chop/
  break/place) tính ô từ **tọa độ của chính sự kiện mousedown** — không đọc
  cache, không dùng `activePointer` của Phaser làm nguồn chính (đã thử, vỡ
  tương tác).
- Hover box: `ensureHoverSquare` tái tạo mỗi frame nếu thiếu, depth 100,
  `setPosition + visible = true` mỗi frame. Không khoanh theo map bounds —
  bounds chỉ chặn click.
- `screenToTile` phải public/export để `main.ts` dùng lúc click.
- **Bài học debug**: mình dò code nhiều vòng không thấy lỗi vì chain nhìn
  đúng — chốt bằng hiện tượng thực tế người dùng (menu chuột phải Chrome) mới
  ra manh mối. Khi không reproduce được từ code, HỎI triệu chứng cụ thể ở
  browser (F12 console, hành vi context menu) trước khi đoán tiếp.

### Weather
- Server sample key từ bộ ĐẦY ĐỦ: `sun_clouds, sunny, cloudy, heavy_clouds,
  rain, heavy_rain, storm, snow, cold, wind` (+`sun, clouds, fog` legacy).
  Client WEATHER_ICONS phải map đủ 13 key — thiếu là hiện ❓ ("lúc được lúc
  không" thực ra là key lạ).
- **Web weather overlay KHÔNG có gate tắt/mở** (khác Discord `weather_fx_enabled`
  mặc định OFF): web client vẽ particle trực tiếp từ `snapshot.weather` qua
  `weatherFx.setWeather()` mỗi 20 Hz — key animated (rain/heavy_rain/storm/
  snow/cold/wind/fog) là hiện hạt ngay. "Bị tắt" trên web thực chất là 1 trong:
  (a) server đang ở key tĩnh (sun_clouds/sunny/cloudy/heavy_clouds — đúng là
  KHÔNG có hạt, theo thiết kế); (b) fetch Open-Meteo fail → fallback clear;
  (c) admin /setweather bị auto-fetch 15 phút sau đè lại (ĐÃ FIX 11/09 bằng
  `rt.weather_manual` pin — xem game/manager.py `_weather_loop`); (d) scenario
  mới mở vẫn ở placeholder sun_clouds (ĐÃ FIX: seed từ `_latest_weather`).
- **Bug weather đè màn hình nuốt click — ĐÃ FIX 3 lớp, ĐỪNG PHÁ** (từng bị:
  querySelector lấy nhầm canvas thời tiết): (1) weather/daynight canvas có
  `pointer-events:none` cả inline (TS constructor + mount) lẫn CSS `!important`,
  z-index:1 (dưới #overlay z-index:2); (2) main.ts bind input vào `game.canvas`
  (Phaser), TUYỆT ĐỐI KHÔNG `querySelector("#game-root canvas")`; (3) canvas
  weather PHẢI nằm TRÊN game canvas mới thấy hạt (game render opaque) — đừng
  "fix click" bằng cách chèn xuống dưới, sẽ làm mất hạt mà click vẫn vậy.
- **WEATHER_KEYS web (core.py) phải cover đủ 13 key** như client (ĐÃ FIX 11/09:
  thêm heavy_rain/sunny/cloudy/heavy_clouds/cold) — thiếu là admin không demo
  được thời tiết đó trên web, cảm giác như "bị tắt".

## 5. Test / debug nhanh (không đoán mò)
### Chéo deploy nhiều session (vụ "vào game nhưng map đơ" 13/09)
- **Triệu chứng:** web vào được (chat/hotbar OK) nhưng map không hiện, không
  di chuyển. Restart + Ctrl+Shift+R không ăn.
- **Nguyên nhân gốc:** NHIỀU session song song — một session upload cả WIP
  chưa commit lên panel, một session upload commit riêng lẻ → panel chạy
  HỖN HỢP: một nửa WIP (file mới như game/drops.py tồn tại) + một nửa cũ →
  import/call lệch nhau nổ trong snapshot loop.
- **Cách rà vét 2 phút (không đoán mò):** so MD5 từng file .py trên panel vs
  worktree vs HEAD qua SFTP (script paramiko + hashlib, chỉ ĐỌC — không ghi
  lên panel khi chưa rõ). Kết quả `panel==worktree≠HEAD` = có session upload
  WIP; `panel≠cả hai` = file hỏng nửa chừng.
- **Luật:** khi có WIP dở của session khác trong working tree, KHÔNG upload
  từng file lẻ cho fix của mình — dùng deploy_files.py chỉ với file mình sửa
  và ghi rõ trong báo cáo; nếu nghi lệch, rà MD5 TOÀN BỘ .py trước khi upload
  tiếp.
- Output `OK` của deploy_files.py KHÔNG đảm bảo file tới nơi khớp — luôn
  verify bằng MD5 sau upload khi debugging triệu chứng run-time.
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

## 5b. Session 14–15/09: multiplayer + movement + UI (tổng hợp nhanh)
- **Client-authoritative movement** (docs/client_authoritative_movement.md):
  client gửi input + vị trí dự đoán, server nhận làm truth. Lệch ổn định 1.5–3 ô
  ở IDLE là ACCEPTED (đã thử ép 0 = giật lùi khó chịu, bỏ). DESYNC log trên
  console (`[DESYNC] d=… pred=… srv=… ack=… pendingInputs=…`) bật F3 — dùng để
  chẩn đoán, đừng fix theo từng dòng log.
- **Màu tên player = role color vĩnh viễn** (`player.name_color`, mint 1 lần
  lúc tạo, lưu DB) — đi kèm CẢ welcome lẫn snapshot 20Hz cho self + remote
  (từng thiếu ở self → "mình trắng, người khác thấy màu khác").
- **Swing broadcast**: mọi attack/chop/break server bắn frame `swing` kèm
  actor uid + tile → client chơi animation ĐÚNG NGƯỜI. Từng dùng "đoán theo
  khoảng cách" (nearest player trong 3.5 ô) → người đứng gần cây bị vung tay
  oan. KHÔNG quay lại đoán.
- **Drop magnet**: server gửi `target_id` trong drops — client kéo linh khí về
  ĐÚNG người nhặt (từng bay vào người xem gần nhất).
- **Profile popup** (click player khác): dùng bộ asset V5 (`inv_frame`,
  `close_small`, `btn_*` 4 state: rest sáng/hover TỐI/pressed/disabled).
  Hover trong kit này là TỐI ĐI, không phải sáng lên. Sprite `btn_*.png` gốc
  có chữ CREATE bake sẵn — đã mổ pixel xoá chữ, giữ nguyên `create_btn.png`
  cho panel craft. Phải để hit-square remote player `alpha 0.001` (KHÔNG
  `setVisible(false)` — invisible = mất input hit test trong Phaser).
- **Popup in-game không phải popup web**: DOM overlay con của `#overlay`,
  không veil toàn màn hình (veil làm Chrome hiện tab-switch UI khi click),
  chặn `contextmenu` trên `#game-root` (trước đó chỉ chặn canvas → chuột phải
  vào popup vẫn ra menu Chrome).
- **Bake resource tiles**: tile thuộc LIVE node KHÔNG bake vào map-bake
  (sprite path xử lý); tile KHÔNG node (cây decor chợ) PHẢI bake (nếu không →
  block vô hình). Bake chạy lại chỉ khi SỐ tile resource visible đổi (sig
  guard); khi node GỤC thì chỉ clearRect đúng bbox (O(node), không rebake cả
  map — rebake cả map = khựng 300–600ms đúng lúc cây đổ).
- **Server tileset payload PHẢI có `tilecount`** (từ Tiled) + client resolve
  gid theo khoảng `[firstgid, firstgid+tilecount)` — map có thể đăng ký CÙNG
  1 sheet ở 2 firstgid khác nhau (BaseChip@577 + @5337) nên "largest firstgid
  ≤ gid" đơn thuần là SAI.

## 6. Ý tưởng tiếp theo (không có bug đang treo — bug được fix riêng ở session khác)
- **OAuth Discord thật**: Railway có auto-deploy nên có thể bật lại, đổi
  redirect_uri trong app-config.json + Dev Portal (bot đã có `web_api/auth.py`).
- Block selector trên web (phím B) thay vì set 🧱 bên Discord.
- Shake animation + particle khi chặt (Kaetram có `resource.shake()`).
- Avatar player thật (cần OAuth) — đang là ô vuông màu (xanh=self, cam=Discord
  player, xanh lá=web player khác).
- **OAuth Discord thật**: Railway có auto-deploy nên có thể bật lại, đổi
  redirect_uri trong app-config.json + Dev Portal (bot đã có `web_api/auth.py`).
- Block selector trên web (phím B) thay vì set 🧱 bên Discord.
- Shake animation + particle khi chặt (Kaetram có `resource.shake()`).
- Avatar player thật (cần OAuth) — đang là ô vuông màu (xanh=self, cam=Discord
  player, xanh lá=web player khác).


## 7. E2E PROBE: log vào server PRODUCTION như client thật (session 16/09)

Kỹ thuật quyết định khi debug mọi thứ liên quan movement/portal/chat — không
cần trình duyệt, không cần đoán: kết nối thẳng websocket vào relay Railway.

### 7.1 Frame shapes (đọc từ web_client/src/net.ts + web_api/core.py)
- Login: `{"type":"guest_login","guest_id":"<19-digit ≥ 900e15>"}` → chờ
  `login_result` (lấy `token`).
- List scenario: `{"type":"list"}` → frame `scenario_list`/có "list" trong type;
  lấy `channel_id`.
- Join: `{"type":"join","token":token,"channel_id":str(ch)}` → chờ `welcome`.
- **Chat/lệnh**: `{"type":"chat_cmd","text":"/khutraodoi in"}` — là
  **envelope TỐT cao**, KHÔNG PHẢI `{"type":"action","frame":{...}}` (gửi sai
  → server trả `error: bad_action`).
- Input/movement: `{"type":"input","seq":n,"dx","dy","running","x","y"}`
  (x,y = vị trí predict; gửi mỗi ~50ms; dx=dy=0 + x/y vẫn là idle heartbeat).
- Teleport vào chợ đổi map → chờ frame `welcome` MỚI (map khác).

### 7.2 Mô hình server-side cần nắm trước khi debug portal
- Portal check chạy trong `_web_tick_runtime` ở **2 nhánh**: nhánh MOVING
  (dx/dy ≠ 0) và nhánh IDLE (dx=dy=0, converge theo report). **Nhánh IDLE từng
  bị `continue` nhảy qua portal check** — push vào cửa = idle → không bao giờ
  tele. Đã fix (manager.py, user 16/09): idle path có portal check riêng.
- `check_portal_after_move` phát hiện bằng **hộp va chạm mở rộng 0.15 ô**
  (GATE_MARGIN) chạm tile cửa — không phải tâm đứng giữa tile.
- Latch chống bounce: sau khi tele, player bị latch cho tới khi bước khỏi
  mọi tile cửa của map đó.
- `_converge_to_report` CHỈ chạy khi report còn tươi
  (`0 < now - report_at < 1.0`); report bị consume (`report_at=0`) sau 1 lần
  áp — harness probe phải gửi report mới mỗi tick.

### 7.3 Pitfall đã mất 2h
- Probe gửi input sai shape (dx/dy≠0 cùng lúc với report) → LEGACY time
  integration + converge CHẠY ĐỒNG THỜI, body bay lung tung (thấy (25.5,21.3)
  dù report (38.5,25.4)) → kết luận probe vô nghĩa. Luôn dùng dx=0,dy=0.
- File trên đĩa remote đúng ≠ process đang chạy code mới: **bắt buộc Restart
  panel sau deploy**, và probe lại production để verify ("old code in RAM").
- `game/travel.py` deployed 01:47; `manager.py` (idle portal fix) 02:17 —
  Restart phải SAU mốc deploy mới nhất.

### 7.4 Checklist debug portal (theo thứ tự)
1. Probe production: đẩy vào cửa bằng idle-heartbeat reports → có `welcome`
   map mới không? Capture **đích thực** (map id + toạ độ), đừng chỉ tìm
   "montertradebase".
2. Remote md5 các file: travel.py, manager.py, portals.json == local?
3. Restart sau deploy chưa? (mtime remote file vs thời điểm Restart).
4. Local full-manager harness: tạo runtime lobby qua
   `get_or_create_side_runtime`, đẩy report tới tile cửa, xem player đổi
   runtime không.
5. Nếu local fire mà production không → RAM cũ (Restart). Nếu cả hai không
   fire → đọc lại 7.2 (idle path, latch, GATE_MARGIN, report freshness).

## 8. Portal qua cửa WALK phải re-send WELCOME (session 16/09 — bug "tele
## nhưng client kẹt map cũ")

### Hiện tượng
- Server tele đúng (log client: `d=32.90 pred=(38.64,25.68) srv=(8.50,12.50)` —
  srv đã ở interior, pred còn ở lobby), nhưng sau đó client dự đoán bằng
  collision map CŨ (`pred=(8.50,14.38) srv=(8.50,12.70)` d=1.68 LẶP VÔ HẠN —
  y=14.38 là sàn lobby, y=12.70 là tường interior). Client KHÔNG BAO GIỜ nhận
  payload map đích khi đi cửa.

### Root cause
- `/khutraodoi in/out` (chat) có `WebHub._maybe_teleport_welcome` gửi welcome
  mới → đi lệnh ổn. Đi cửa thì server tele trong
  `GameManager._teleport_through_link` — đường này KHÔNG có web I/O, snapshot
  chỉ mang `map_id` (client KHÔNG rebuild world từ snapshot) → client giữ
  layer + collision map nguồn mãi mãi.

### Fix (2 đầu, web-safe)
- `GameManager.web_map_change_hook: Optional[Callable[[rt, user_id], Awaitable]]`
  (default None — game layer không import web).
- `WebHub.__init__` tự cắm: `manager.web_map_change_hook = self.send_map_welcome`
  → `send_map_welcome` push `build_welcome(dst_rt, uid)` (session đã được
  `move_player_between_runtimes` migrate sang `dst_rt.web_sessions`).
- Client `onWelcome → buildWorld` đã rebuild sẵn (reset pred/selfX/selfY +
  collision) — không cần sửa web_client cho phần map.
- Lock: tests/test_web_portal_map_switch.py (hook register, welcome map đích
  có collision, Discord-only player không crash hook).

### ⚠️ BẪY 8.1 — HAI object session (fix đầu tiên KHÔNG ăn)
Mỗi client web có **2 object WebSession khác nhau**:
1. `conn.session` = session của `SessionRegistry` (web_api tạo lúc
   login/join; giữ token/display_name).
2. `rt.web_sessions[user_id]` = session do `GameManager.register_web_session`
   tạo (giữ dx/dy/report_x/report_y/report_at/input_seq — cái tick dùng).

`send_to_client_conn(sess, frame)` so khớp bằng **identity** (`conn.session is
sess`) → luồn manager-session vào là KHÔNG khớp object nào ⇒ welcome bị bỏ
IM LẶNG (không exception, không log). Triệu chứng: mọi thứ "đúng" mà client
vẫn kẹt map cũ. **Đúng luôn:** lọc theo `sess.user_id == uid and
sess.channel_id == rt.channel_id` (kèm guard `uid in rt.web_sessions` để
player Discord-only không bị gửi).

### ⚠️ BẪY 8.2 — client reset inputSeq=0 ở MỖI welcome
`net.ts` cũ: `case "welcome" → this.inputSeq = 0` (đúng cho join mới, SAI cho
welcome giữa phiên). Session server vẫn đếm tiếp (vd 2020) ⇒ snapshot echo
`ack=2020` trong khi client gửi seq 1,2,3 → mọi input mới bị coi là "đã ack"
⇒ replay/reconcile TẮT (`pendingInputs=0` dù đang đi) ⇒ lệch dai dẳng sau mỗi
lần đổi map. **Fix:** welcome mang `input_seq` (server: `build_welcome` đọc
`rt.web_sessions[uid].input_seq`) → client `this.inputSeq = frame.input_seq ?? 0`
và `buildWorld` đặt `this.lastAckedSeq = welcome.input_seq ?? -1`.

### Checklist xác minh (đã dùng thật)
1. Probe production: space → `/khutraodoi in` → đi bộ vào cửa, in ra
   `*** WELCOME MID-WALK -> <map>` (trước fix: chỉ thấy tele, không welcome).
2. Kiểm code mới đã nằm trong RAM chưa: welcome có field `input_seq` không
   (field này chỉ có ở bản mới) — nhanh hơn đoán "đã Restart chưa".
3. Nhớ: đi bộ trong probe phải theo kiểu report dần (client-authoritative),
   server converge có cap tốc độ; dừng report là server đứng im.

## 9. Discord client NGƯNG phát triển (quyết định 17/09)

**Discord UI (hub message, D-pad, hub image, persistent views phía bot) tạm
ngưng build tiếp.** Mọi tính năng UI mới chỉ làm trên **web client**
(`web_client/`). Phía bot giữ nguyên hiện trạng: chỉ sửa bug nghiêm trọng,
không thêm tính năng mới.

## 10. Hub bar dọc Kaetram (web client) — kiến trúc + sprite data

**Đổi hướng 18/09: phase 1 (bar tự viết, page tự render đơn giản) bị
chủ project từ chối — yêu cầu là CHÉP NGUYÊN UI gốc Kaetram cho từng page:
đúng HTML structure (game.astro), đúng CSS (scss/game/impl/*.scss), đúng
logic (menu/*.ts), đủ chức năng gốc.** Repo clone tại `_kaetram_ref/`
(MPL 2.0, chấp nhận theo chủ project). Reference files:
- HTML gốc: `packages/client/components/game.astro` (tất cả page containers:
  quests, achievements, settings-page, leaderboards, bank, trade, crafting,
  equipments, map-frame, guilds, friends-container, store, enchant…)
- CSS gốc: `packages/client/scss/game/impl/` (_quests, _achievements,
  _settings, _leaderboards, _bank, _crafting, _trade, _equipments, _map,
  _profile, _guilds, _friends…) + `abstracts/_sprite.scss` + `_slice.scss`
  (9-slice kit: slice-container, slice-inner-container, slice-list-item,
  slice-dialog, slice-tab, slice-input, slice-button, close-container…)
- Logic gốc: `packages/client/src/menu/*.ts` (quests, achievements,
  settings, leaderboards, bank, crafting, trade, equipments, warp=map-frame,
  guilds, friends…) — mỗi menu = super(selector, closeSelector, buttonSelector)

Thanh nút dọc sát mép phải, ngay trên khung chat, mở các "page" kiểu Kaetram.
Code: `web_client/src/hub_bar.ts` (bar + sprite mapping), `hub_pages.ts`
(nội dung từng trang), CSS `#hud-hub / #hub-bar / #hub-page` trong
`styles.css`, container nằm **bên trong `#hud-chat`** để neo tự động sát mép
trên khung chat + lề phải màn hình.

### Bản chất mô hình (Kaetram parity)
- Bar = tập nút cố định; bấm nút = **toggle** trang; mở trang này tự đóng
  trang kia; 1 trang chính mở tại 1 thời điểm; nút có active state.
- Trang là **DOM overlay** chồng trên canvas game (KHÔNG vẽ trong canvas).
- Inventory KHÔNG nằm ở đây — client đã có sẵn panel bag riêng (nút "Túi đồ"
  route sang panel đó). Nút "Chat" chỉ focus `#chat-input`.

### Sprite `hud_buttons.png` (assets Kaetram, đã tải về
`web_client/public/ui/kaetram/interface/`)
- Layout: **3 cột trạng thái × 14 hàng nút, cell 22×25** — sheet thật là
  **66×350** (ĐÃ VERIFY bằng render grid; doc cũ ghi 66×250 là SAI và làm
  méo toàn bộ icon 1.4× — đừng lặp lại). Rows 0–9 = 10 nút, rows 10–12
  TRỐNG, row 13 = khung nút trống màu xanh (dùng làm nền cho nút Trang bị
  + đè icon `equipment/weapon.png`). Cột: thường / hover / active.
  Background-size tổng: **132×700px** (scale ×2).
- Mapping hàng ↔ nút (nguồn `_buttons.scss` gốc của Kaetram, hàng từ trên
  xuống): `0` inventory/bag, `1` chat, `2` leaderboard, `3` map/warp,
  `4` settings, `5` profile, `6` quests, `7` guilds, `8` friends,
  `9` achievements, `13` khung trống (equipment). Mapping trong
  `hub_bar.ts` (`ROW_BY_PAGE`), vị trí cắt = `col * 44px, row * 50px`
  (đơn vị đã scale).
- Khung panel dùng `slices/container.png` (9-slice): `border-image-slice` cần
  **`44%`** (KHÔNG phải `44`) thì góc mới không gãy.
- Khung trang được neo TRÊN NÚT bar bằng CSS var
  `--hub-bar-h` (đo thật từ bar trong `hub_bar.ts`, ghi vào `#hud-hub`) —
  KHÔNG hard-code px vì bar cao ~530px, đoán sai là trang đè lên nút giữa.
- PNG gốc có màu lỗi khi tải trực tiếp (`hud_buttons.png` từ branch develop
  decode sai màu) → đang dùng bản `hud_buttons_rgb.png` đã convert RGB. Nếu
  tải lại, nhớ kiểm màu.

### Wire-up (điểm cắm)
- `ui.ts`: build bar + container trong `buildHud`, expose
  `setHubScene/gameData/...`; bar ẩn/hiện theo gate (`hideGate` khi vào game,
  `showGate` khi ra lobby).
- `main.ts`: sau khi có scene + snapshot → feed dữ liệu (map id/name/size,
  player quanh, HP/mana/xu/tinh thể, ping) → `hubPages.render()` khi mở.
- Nút bar gọi callback `onPage` trong `hub_pages.ts` — return `true` = đã tự
  xử lý (inventory/chat, không mở container), `false` = render trang vào
  `#hub-page`.
- Đóng trang: nút X, hoặc bấm lại đúng nút đang active, hoặc mở nút khác.
