# Discord Map — Web Client + Relay

Web client 60fps (Phaser 3 + Vite + TypeScript) cho Discord Map Game, chơi
song song cùng player trên Discord qua cùng một bot server.

## Kiến trúc

```
[Browser] --wss--> [NexNode relay (thư mục này)] <--ws-- [Bot Python (dial-out /bot)]
```

- Relay **không chứa game logic** — chỉ là đường ống frame + serve file tĩnh.
- Bot **chủ động kết nối ra** relay (`/bot`, header `X-Relay-Token`) nên panel
  KHÔNG cần mở port inbound.
- Browser connect `/ws`, nói trực tiếp protocol của `web_api/protocol.py`.

## Deploy lên NexNode (app Node.js)

1. Build client:
   ```powershell
   cd web_client
   npm install
   npm run build
   ```
2. Copy bản build vào relay:
   ```powershell
   Copy-Item -Recurse -Force dist relay\dist
   ```
3. Push thư mục `web_client/relay/` lên Git repo public (NexNode deploy từ Git).
4. Trên NexNode tạo app **Node.js** → trỏ về repo → setup:
   - Build/start command: `npm start`
   - Env: `RELAY_TOKEN`, `DISCORD_OAUTH_CLIENT_ID` (public, dán client id của
     bot app vào đây).
5. Trên panel bot, thêm env:
   - `RELAY_URL=wss://<ten-app>.nexnode.../bot`
   - `RELAY_TOKEN=<cùng giá trị với relay>`
6. Discord Developer Portal → app của bot → OAuth2 → thêm Redirect URL:
   `https://<ten-app>.nexnode.../` (origin của web).

## Dev cục bộ

```powershell
cd web_client
npm run dev        # Vite proxy /ws -> ws://localhost:8787
node relay/relay.js  # terminal khác
```
