# Discord Map Game — Engineering Rules

You are implementing a Discord-native multiplayer map game.

The game screen MUST live entirely inside a Discord message. Do NOT create or require an external web frontend.

## Architecture

```
Discord UI → GameManager → GameState / Actions / Rules → Persistence → Renderer
```

## Core principles

1. Discord is a frontend/transport layer only.
2. Game logic must not depend on discord.py objects.
3. Rendering must not mutate game state.
4. Persistence must not know about Discord UI.
5. Commands and button callbacks must be thin adapters.
6. All scenarios are isolated by channel_id.
7. Each player has a personal screen message (map image + their own D-pad) and a personal hub message (HUD image + inventory/settings buttons). The screen+hub pair must stay adjacent; any message posted between them is deleted by `discord_ui/adjacency.py`. Each player's screen+hub pair is independent, enabling per-interaction-token edits that bypass the channel message-edit bucket entirely.
8. Movement is represented by Actions.
9. State transitions are deterministic.
10. All maps are data-driven.
11. Tiled JSON is the source format for map data.
12. Avatar images must be cached.
13. Persistent Discord Views must use `timeout=None` and explicit `custom_id` values that include the player's user_id for per-player scopes.
14. Restore persistent views after restart — including per-player HubView and MapView bound to each player's saved screen_message_id / hub_message_id.
15. Never use a global asyncio lock for all maps.
16. Use one runtime/queue/lock per channel.
17. Never hard-code collision coordinates.
18. Never hard-code map dimensions.
19. Never store image binaries in SQLite.
20. Never swallow exceptions.
21. Never block the async event loop with synchronous network I/O.
22. Do not assume hard-coded Discord rate limits.
23. Handle 429/retry behavior through the Discord library/API semantics.
24. Never create a new Discord message for each player movement.
25. Prefer editing the existing game message.
26. Keep files small and responsibilities isolated.
27. Write tests for every game rule.
28. Never introduce unnecessary abstractions.
29. Implement the smallest working vertical slice first.
30. Do not implement future RPG systems before the movement/map MVP is stable.

## MVP features

- /startmap, /joinmap, /leave-map, /map, /mapreset, /mapinfo
- 8-direction movement
- Tiled map loading
- tile collision
- multiple players
- Discord avatar tokens
- SQLite persistence
- persistent buttons
- restart recovery

## Suggested modules

```
game/         state.py actions.py collision.py map_loader.py rules.py manager.py
discord_ui/   commands.py map_view.py hub_view.py interactions.py errors.py
rendering/    renderer.py avatar.py layers.py camera.py hub_renderer.py
persistence/  database.py migrations.py repositories.py
tests/        test_state.py test_collision.py test_map_loader.py test_renderer.py test_database.py
```

## Deployment (cloud hosting panel)

The bot runs on a Discord-bot hosting panel (Pterodactyl-style). Full detail in
`docs/deployment.md` and `docs/trien-khai.md`; the essentials every session must know:

- **Panel auto-manages venv + deps**: on Start it `cd /home/container`, creates `.venv`
  (per Python version), `pip install -r ${REQUIREMENTS_FILE}`, then `exec .venv/bin/python ${BOT_PY_FILE}`.
  We NEVER create a venv or run the bot remotely ourselves.
- **Upload channel = SFTP port `2022`** (reachable). SSH port `30112` is firewalled
  from the dev machine — do NOT use `scripts/deploy_cloud.ps1`/`.py` (SSH); use `scripts/upload_tree.py`.
- **Upload target = SFTP home = container `/home/container`** (startup does `cd /home/container`).
  Upload the project tree flat to root; do NOT wrap in a subfolder (caused past `bot.py not found`).
- **Restart/Reset on the panel does NOT pull code** — it only relaunches the existing process.
  To ship a code change: upload first, then Restart.

### Deploy steps (every deploy)
1. Set panel env vars: `DISCORD_TOKEN`, `BOT_PY_FILE=bot.py`, `REQUIREMENTS_FILE=requirements.txt`.
   (Token may instead live in uploaded `.env`; `load_dotenv()` won't override panel env.)
2. Upload from project root:
   ```powershell
   .venv\Scripts\python scripts/upload_tree.py   # SFTP :2022 -> /home/container
   ```
   (`upload_tree.py` already excludes `.venv/`, `data/`, `.git`, `.deploy.env`; it uploads
   `.py`, `assets/**` incl. `*.png` tilesets + `*.js`/Tiled maps, and overwrites `.env`.)
3. Press **Start/Restart** on the panel. Expect log line
   `Discord.py runtime ready; launching bot.` then `[READY] <bot>#xxxx` and `[BOOT] restored N scenario(s)`.
4. In Discord: `/startmap` → `/joinmap` → move; map image updates and messages self-heal if deleted.

### Local dev loop
```powershell
cd "D:\dự án mini build bot discord mmo event"
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\python bot.py                          # needs .env with DISCORD_TOKEN
.venv\Scripts\python scripts/upload_tree.py          # push to cloud (FULL, slow — first deploy / many files)
.venv\Scripts\python scripts/diag_commands.py        # verify registered commands (HTTP, no bot restart)
```

### FAST deploy (default for small changes — seconds, not minutes)
```powershell
# Python only (e.g. web_api/core.py, game/rules.py):
.venv\Scripts\python scripts/deploy_files.py web_api/core.py game/rules.py
# then Restart bot on the panel. Details: docs/web_client_session_knowledge.md

# Web client (TypeScript) — Railway auto-deploys on git push:
cd web_client; npm run build
rm -rf relay/dist; cp -r dist relay/dist
# recreate web_client/relay/dist/app-config.json (client_id + redirect_uri) if wiped
cd ..; git add ...; git commit; git push github-dauhu main; git push origin main
```

### Gotchas (runtime errors NOT caught by py_compile — do not repeat)
- Entry-point command `HTTP 50240`: `bot.py setup_hook` prunes global commands with `type == 4`
  (raw check; discord.py 2.7.1 has no `AppCommandType.entry_point`) before `tree.sync()`.
- discord.py 2.x: use `@app_commands.command` on a Cog method; assign `btn.callback` after
  creating the Button (signature `cb(interaction)`); direction keys via `DIR_BY_KEY`, not `Direction["E"]`.
- `se` arrow is `↘️` (U+2198), never `⬘️`.
- SQLite needs `data/` folder pre-created (`Database.connect` does `mkdir(parents=True, exist_ok=True)`).
- Container has NO TTF font and likely no Discord CDN egress → `AvatarCache` draws a pure-PIL
  face token (`use_network=False` default); opt-in real avatars via `USE_DISCORD_AVATARS=1`.
- Map image size = `viewport_tiles × 32 × MAP_UPLOAD_SCALE` (`MAP_UPLOAD_SCALE` in `rendering/renderer.py`,
  `DEFAULT_VIEW_W/H` in `rendering/camera.py`); bigger = slower upload.
- Hub bar image width is forced to match the screen image's displayed width: `HubRenderer` renders
  at the screen's pre-finalize size (`screen_internal_size`) so the shared 0.5 upload shrink yields
  an identical displayed width. Height is fixed in `hub_renderer.HUB_DISPLAY_H` (currently 66); never
  hard-code hub width/height constants against this intent. Player HP/mana bars use the
  `assets/healthbar` Pixel2 sprite pack (Background + HealthBars fill + Border + Emblem layers).
- Hotbar slot icons on the hub are REAL item icons: bundled Twemoji PNGs under
  `assets/gui/items/<codepoint>.png` (mapping `ITEM_ICON_CODEPOINTS` in `rendering/hub_renderer.py`,
  fetched once by `scripts/fetch_item_icons.py` — no runtime network). Unknown items fall back to
  the letter glyph. When adding an item/block, add its codepoint BOTH to the fetch script and the
  renderer map. The D-pad hotbar rail is kept fresh on every coalesced screen flush
  (`flush_render_batch` re-sends the controls view with `_apply_hotbar_labels`), and zombie loot
  grants call `_notify_inventory_change` — never rely on the 5s hub beat for bag changes.
- Weather FX (animated map overlays): OFF BY DEFAULT via the per-scenario gate
  `rt.weather_fx_enabled` (see `rendering.renderer.effective_weather_key`) — every render
  call site must pass `weather_key=_wx_key_of(rt)` (helpers in map_view/commands/hub_view/
  session_recovery), never the raw `rt.weather_key`, or the screen silently renders the
  heavy 6-frame GIF again. When the gate is ON, `rendering/weather_fx.py` tiles the CraftPix
  pack frames under `assets/fx/**` (rain/snow/wind/thunder) across the screen viewport;
  animated keys (rain, heavy_rain, snow, cold, wind, storm) force a FULL render and return a
  6-frame GIF (`RenderResult.frames` + `filename="map.gif"`), encoded by
  `renderer.encode_upload_gif`. The `composite` stays overlay-free so incremental caching is
  never poisoned. The gate is toggled ONLY by admins: Settings panel → Thời tiết tab
  (per-scenario, fanned out to all screens+hubs). Players see weather status read-only.
  `_lightning_loop` skips scenarios with the gate OFF (no wasted re-renders). Regenerate
  assets with `scripts/convert_weather_fx.py` (one-time, source pack path inside); preview with
  `scripts/_preview_fx.py`. Admin/test commands: `/setweather` (admin; incl. "Tự Động" = resume
  real weather), `/weatherfetch` (admin), `/weatherinfo` (reports gate state).
- Screen+hub pair coupling & auto-refresh (`discord_ui/refresh.py`): the hub ALWAYS lives
  directly under a live screen. A dead screen drops its hub + clears both persisted ids
  (`clear_pair`); a dead/stale hub id is forgotten and re-created under the same screen
  (also fixed in `flush_hub_batch` NotFound + `create_hub_message` guard). Screen self-heals
  that mint a NEW screen call `reattach_hub_under_screen` (old hub now floats above -> drop,
  hub lane re-sends below). `RefreshScheduler` (one shared task, started in bot.py,
  `refresh_scheduler`) paces per player: hub re-render every `HUB_REFRESH_SECONDS` (5s) AND
  sooner on any `hub_signature` change (clock minute, weather key/snapshot, lightning seed,
  players, HP/mana/coins, bag, hotbar); screen re-render every `SCREEN_REFRESH_SECONDS` (8s,
  skipped while `screen.rendering` or a flush is pending); pair liveness (fetch screen first,
  then hub, snowflake-order check) every `PAIR_CHECK_SECONDS` (60s). Transient fetch errors
  are NEVER treated as deletions. Inverted-pair snowflake check assumes real Discord ids —
  tests use increasing ints; keep it that way.

## Workflow per step

1. inspect existing code
2. explain the intended change briefly
3. implement the smallest coherent change
4. run tests
5. run a syntax/type/import check
6. report changed files
7. report any remaining risks

Do not proceed to the next phase until the current acceptance tests pass.

## Luật cứng (hard rules — always apply)

1. **LUÔN trả lời bằng tiếng Việt** — mọi câu trả lời, giải thích, báo cáo cho
   người dùng đều bằng tiếng Việt (code, log, comment giữ nguyên tiếng Anh).
2. **LUÔN deploy code lên cloud khi xong việc** — sau mỗi thay đổi code đã
   kiểm tra xong, chạy `scripts/upload_tree.py` (SFTP :2022 → /home/container)
   rồi báo người dùng bấm Restart trên panel (panel KHÔNG tự pull code).
3. **Không quá đà kiểm thử lý thuyết** — chỉ chạy test nhanh để bắt lỗi cú
   pháp/import/logic nghiêm trọng; không dành quá nhiều thời gian cho test
   khi tính năng đã ổn. Người dùng sẽ test thực tế trên Discord để xác nhận.
