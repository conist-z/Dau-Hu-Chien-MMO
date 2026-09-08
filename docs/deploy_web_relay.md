# TỪNG BƯỚC: Tạo app Node.js trên NexNode (~10 phút)

> Bạn làm theo đúng thứ tự, copy-paste các giá trị chuẩn bị sẵn.

## Giá trị chuẩn bị sẵn

| Thứ | Giá trị | Dùng ở đâu |
|---|---|---|
| RELAY_TOKEN | `bd3b9c716b74a4abc782a0166de9f9959af83170e00f0493` | Env của relay **và** env panel (2 nơi phải GIỐNG nhau) |
| DISCORD_OAUTH_CLIENT_ID | *(bạn lấy ở Bước 4)* | Env của relay |
| Thư mục push lên Git | `web_client/relay/` (đã có sẵn `dist/` build xong) | NexNode "Deploy from Git" |

---

## Bước 1 — Tạo repo GitHub cho relay (2 phút)

Relay phải là repo **riêng, public** (NexNode "Deploy from Git" cần public repo).
**KHÔNG chứa bot token hay asset** — chỉ có code relay + file client đã build.

Cách nhanh (dùng GitHub web, không cần git command):

1. Vào github.com → **New repository**
2. Tên: `discord-map-relay` → chọn **Public** → Create
3. Bấm link **"uploading an existing file"** trên trang repo mới
4. Kéo thả **toàn bộ file trong thư mục** `web_client/relay/` vào (bao gồm
   `relay.js`, `package.json`, `package-lock.json`, và cả thư mục `dist/`)
5. **Commit changes**

## Bước 2 — Tạo app Node.js trên NexNode (3 phút)

1. Đăng nhập panel NexNode → **Create a website**
2. **Application**: chọn **Node.js** (KHÔNG chọn Static site — static không chạy được WS relay)
3. **Deploy from Git**: trỏ vào repo `discord-map-relay` vừa tạo
4. Runtime: Node **20** trở lên (nếu có chọn version)
5. Start command: `npm start` (nó chạy `node relay.js` theo package.json)
6. Confirm tạo app → chờ provision xong → **ghi lại URL** nó cấp,
   dạng `https://discord-map-relay-xxxx.nexnodecloud.xyz`
   → đây chính là **app-nexnode** mà bạn hỏi ở câu 4!

## Bước 3 — Set env cho app relay trên NexNode

Trong phần cài đặt app (mục Environment Variables), thêm 2 biến:

| Key | Value |
|---|---|
| `RELAY_TOKEN` | `bd3b9c716b74a4abc782a0166de9f9959af83170e00f0493` |
| `DISCORD_OAUTH_CLIENT_ID` | *(dán Client ID — lấy ở Bước 4)* |

Rồi **Restart/Redeploy** app. Test nhanh: mở
`https://<url-app>/config.json` — phải thấy `{"client_id":"...", ...}`.

## Bước 4 — Lấy Client ID + set Redirect (2 phút)

1. Vào **discord.com/developers/applications** → chọn app của bot
   (cùng app với cái bot token bạn vừa reset)
2. Tab **OAuth2** → copy **Client ID** (dãy số) → dán vào env
   `DISCORD_OAUTH_CLIENT_ID` của relay (Bước 3), redeploy
3. Vẫn tab OAuth2 → **Redirects** → **Add Redirect**:
   `https://<url-app-từ-bước-2>/` (đúng origin, có `https://`, có `/` cuối)
4. **Save Changes**

## Bước 5 — Nối bot về relay (env trên PANEL hosting bot)

Thêm 2 biến env vào panel (chỗ nào bạn thấy `DISCORD_TOKEN` hiện tại thì thêm cạnh đó):

| Key | Value |
|---|---|
| `RELAY_URL` | `wss://<url-app-từ-bước-2>/bot` |
| `RELAY_TOKEN` | `bd3b9c716b74a4abc782a0166de9f9959af83170e00f0493` |

(Nhớ: token bot đã reset ở Bước 4 cũng phải cập nhật `DISCORD_TOKEN` tại đây.)

## Bước 6 — Restart + xác nhận (2 phút)

1. **Restart** bot trên panel
2. Xem log panel — phải thấy một trong hai:
   - `[WEB] relay connected: wss://...` ✅ thành công
   - `[WEB] relay error (...); retrying in Ns` → kiểm tra lại `RELAY_URL`/`RELAY_TOKEN`
3. Mở `https://<url-app>/` trên browser → thấy màn hình login
   **"Đăng nhập bằng Discord"** → login → chọn map → chơi!

## Sơ đồ cuối cùng

```
Browser ──wss──> NexNode relay (Bước 2) ──ws /bot──> Bot Python (panel)
   https://<app>/        RELAY_TOKEN khớp                RELAY_URL + RELAY_TOKEN
```

## Nếu lỗi

| Triệu chứng | Nguyên nhân thường gặp |
|---|---|
| `/config.json` trả client_id rỗng | Quên `DISCORD_OAUTH_CLIENT_ID` env / chưa redeploy |
| Log bot `relay error 401` | `RELAY_TOKEN` 2 bên không giống nhau |
| Log bot `relay error ... ECONNREFUSED` | Sai `RELAY_URL` (thiếu `wss://` hoặc `/bot`) |
| Login xong không thấy map list | Chưa `/startmap` trên Discord, hoặc chưa Restart bot |
