# KẾ HOẠCH CHI TIẾT — Discord Map-Game Bot (bản đầy đủ)

> Tổng hợp & phân tích từ `doc não dự án thô.txt` (4203 dòng).
> Session này: **chỉ** phân tích, kiểm chứng, và định hướng — chưa viết code.

---

## 0. Góc nhìn trực quan (TL;DR)

Game screen sống **toàn bộ trong 1 Discord message** (KHÔNG web/app ngoài/Discord Activity).

```
DISCORD CHANNEL #game-room
└── Bot gửi 1 message duy nhất
    ┌──────────────────────────────┐
    │          MAP IMAGE (PNG)      │
    │       🐱 Player A             │
    │                 🐱 Player B   │
    │   🏠      🌳                  │
    └──────────────────────────────┘
         [ ↖ ][ ↑ ][ ↗ ]
         [ ← ][ • ][ → ]
         [ ↙ ][ ↓ ][ ↘ ]

Player bấm button → GameManager.dispatch(Action) → GameState.apply()
        ↓
Collision check → SQLite save → Renderer(Pillow) → PNG → message.edit()
```

**Luồng xử lý cốt lõi:**

```
Discord interaction (button)
        ↓
Interaction Layer (adapter mỏng — chỉ validate + tạo Action)
        ↓
GameManager.dispatch(action)
        ↓
GameState.apply(action)  +  Collision.can_move()
        ↓
Persistence (SQLite)
        ↓
Renderer (Pillow) → RenderResult(image, filename, content_type)
        ↓
Discord adapter → discord.File → message.edit(attachment, view)
```

**Triết lý:** Discord = frontend/transport. Game engine độc lập với discord.py.

---

## 1. Mục tiêu cuối cùng (Definition of Vision)

- Discord message = game screen.
- Ảnh map 2D (tilemap/pixel art), nhiều player cùng hiện trên 1 map.
- Avatar Discord của từng player được composite lên map (bo tròn, có viền).
- Điều khiển bằng Discord Buttons (8 hướng), mỗi user bấm → bot biết user_id.
- Mỗi bước đi → render PNG mới → `message.edit()` (KHÔNG tạo message mới).
- 1 scenario (world) độc lập theo từng channel.
- Khôi phục được sau restart bot (nút vẫn sống, vị trí không mất).

---

## 2. Phân tích repo đã khảo sát (chi tiết)

### 2.1 samclane/Discordia
- **Loại:** Full MUD server (không phải bot đơn giản). Python 3.7+, discord.py, SQLite.
- **Cấu trúc xác nhận:** `GameLogic/GameSpace.py` (class `World`), `Interface/Database.py` (class `Database`, `.load()/.save()`), `Interface/DiscordInterface.py` (bot wrapper, tick 5s + autosave 60s), `Interface/Rendering/DesktopApp.py` (debug window desktop — KHÔNG phải renderer Discord).
- **Lệnh:** `/register`, `/look` (render ảnh theo FOV), `/equipment`, `/move`, `/attack` (8 hướng kể cả chéo), `/town`.
- **Sprite:** Kenney RPG Urban Pack (free).
- **License:** GPL-3.0 ✅.
- **Bài học:** có sẵn SQLite persistence + image renderer theo FOV → tận dụng pattern. NHƯNG repo dormant từ 2023 → code cũ cần adapt discord.py 2.x.
- **Quyết định:** được phép port/inspire trực tiếp (miễn chưa public source project của bạn).

### 2.2 bruno963852/dtre
- **Loại:** Tabletop RPG engine cho Discord. Python (discord.py + Pillow khả năng cao).
- **Lệnh xác nhận:** `?r.create <tên> <px> <link ảnh>` (nhận link ảnh bất kỳ làm nền), `?r.ac <token> <x> <y> <link ảnh>` (gán ảnh riêng từng token), `?r.m` (move bằng hướng).
- **Pattern:** 1 channel = 1 scenario; tạo mới ghi đè cái cũ.
- **License:** KHÔNG CÓ (null) ❌ → all rights reserved. Không copy code, chỉ học pattern.

### 2.3 Call-Me-Daisy/DnDaisies
- **Loại:** Discord bot vẽ map tabletop bằng Canvas. 100% JavaScript, discord.js.
- **Cấu trúc:** `arena.js bot.js styles.js parsers.js utils.js`.
- **Xác nhận:** multi-layer + background image tuỳ chọn, token tô màu hex (có alpha), auto-rebuild khi thay đổi. Dùng thread riêng + xoá message cũ để đỡ spam.
- **License:** KHÔNG CÓ (null) ❌ → chỉ học pattern (layer-compositing, UX).

### 2.4 DrSkunk/discord-plays-pokemon
- **Loại:** Bot chơi Game Boy, post screenshot, lắng nghe reaction. TypeScript/Node, có Docker.
- **Cơ chế:** reaction vote, 2 mode Anarchy/Democracy, có lệnh `/map` render vị trí riêng.
- **License:** GitHub detect "Other", README badge ghi ISC ⚠️ → coi là permissive nhưng cẩn thận.
- **Bài học:** loop reaction → render → post trong message (đúng concept, khác chỗ cả server điều khiển CHUNG 1 nhân vật).

### 2.5 Carto-Discord/carto
- **Loại:** Grid map tracker cho DMs/Players. Monorepo npm workspaces, TS, backend serverless GCP (Terraform), CI/CD.
- **License:** MIT ✅.
- **Bài học:** grid map + movement abstraction. NHƯNG hạ tầng nặng → không fork, chỉ học ý tưởng.

**Kết luận chung:** Không repo nào làm SẴN "avatar Discord composite lên map chung + mỗi user tự di chuyển + 1 message duy nhất edit". Mảnh đó tự viết. Hướng: **clean-room** (học kiến trúc, tự viết Python).

---

## 3. Kiến trúc chính thức

### 3.1 Module layout
```
discord-map-game/
├── bot.py                  # bootstrap: load .env, load cogs, add_view() lúc startup
├── config.py               # đọc DISCORD_TOKEN từ .env
├── requirements.txt
├── .env / .gitignore
├── game/                   # THUẦN PYTHON, không import discord
│   ├── state.py            # Player, GameState
│   ├── actions.py          # MoveAction, ActionResult
│   ├── collision.py        # is_walkable, can_move
│   ├── map_loader.py       # Tiled JSON → MapData
│   ├── rules.py            # quy tắc chuyển trạng thái
│   └── manager.py          # GameManager, ScenarioRuntime
├── discord_ui/             # adapter mỏng
│   ├── commands.py         # /startmap, /joinmap, ...
│   ├── map_view.py         # MapView (8 button, persistent)
│   ├── interactions.py     # xử lý button → tạo Action
│   └── errors.py           # GameError hierarchy
├── rendering/              # Pillow, PURE
│   ├── renderer.py         # render_map(state, map_data, assets, camera) → RenderResult
│   ├── avatar.py           # AvatarCache
│   ├── layers.py           # layer pipeline
│   └── camera.py           # FULL_MAP / FOLLOW_PLAYER
├── persistence/            # chỉ số/chuỗi/bool/timestamp
│   ├── database.py         # aiosqlite connection
│   ├── migrations.py       # tạo bảng
│   └── repositories.py     # load/save scenario, player
├── assets/maps/            # Tiled export (test-map.json + test-map.png)
├── tests/                  # unit tests
└── data/game.db
```

### 3.2 Nguyên tắc (30 rules — xem AGENTS.md)
Tóm tắt cốt lõi: Discord chỉ là transport; game logic không phụ thuộc discord.py; renderer không mutate state; persistence không biết Discord UI; mỗi scenario = 1 message + 1 persistent View (`timeout=None`, `custom_id` chứa `channel_id`); isolation per channel; movement = Action; map data-driven (Tiled JSON); avatar cache; không global lock; không hard-code dimension/collision/spawn; không lưu image trong SQLite; không swallow exception; không blocking I/O; không giả định fixed rate limit; ưu tiên edit message.

---

## 4. Mô hình dữ liệu & Schema

### 4.1 Scenario
```json
{ "channel_id": 123, "message_id": 456, "map_id": "whale_island",
  "created_at": "...", "updated_at": "..." }
```

### 4.2 Player
```
user_id, display_name, x, y, direction, sprite_id, visible
```
Mở rộng sau (KHÔNG làm MVP): hp, max_hp, level, class_id, inventory, status_effects.

### 4.3 GameState (API)
```
add_player / remove_player / get_player(user_id)
move_player(user_id, direction)
can_move(user_id, direction)
get_visible_players()
```
Renderer KHÔNG được sửa GameState.

### 4.4 Direction system (8 hướng)
```
class Direction(Enum): NORTH, SOUTH, EAST, WEST, NE, NW, SE, SW
N=(0,-1) NE=(1,-1) E=(1,0) SE=(1,1) S=(0,1) SW=(-1,1) W=(-1,0) NW=(-1,-1)
```
Không hard-code từng nút vào game logic.

### 4.5 SQLite schema (MVP)
```sql
CREATE TABLE scenarios (
    channel_id  INTEGER PRIMARY KEY,
    message_id  INTEGER NOT NULL,
    map_id      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE players (
    channel_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    direction   TEXT NOT NULL,
    sprite_id   TEXT NOT NULL,
    visible     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (channel_id, user_id)
);
```
DB không lưu PIL.Image / avatar / Discord object / asyncio.Lock.

### 4.6 MapData (Tiled abstraction)
```
class MapData:
    width, height, tile_width, tile_height
    collision          # tile layer 0=walkable, 1=blocked
    spawn_points       # từ JSON
    layers             # terrain/decoration/object
    metadata           # map_id, display_name, default_camera
```

### 4.7 Tiled JSON cần dùng (MVP)
`width, height, tilewidth, tileheight, layers`. Collision = tile layer (0/1) hoặc object layer (sau này). Spawn định nghĩa trong data:
```json
{ "map_id": "whale_island", "name": "Đảo Cá Voi",
  "spawn": {"x": 15, "y": 10}, "camera": {"mode": "full"} }
```

---

## 5. Rendering (chi tiết)

### 5.1 Layer pipeline
`BASE → OBJECTS → PLAYERS → EFFECTS → UI`. Player luôn trên terrain.

### 5.2 Player token
Avatar Discord: download → resize → crop vuông → circular mask → border → (tùy chọn) tên rút gọn. Không render username dài trên map (dùng "A"/"B" hoặc tên rút gọn).

### 5.3 AvatarCache
```
class AvatarCache:
    async def get_avatar(user) -> PIL.Image
    # chưa cache → download; cache<TTL → reuse; expired → refresh
    # key = user_id; value = (PIL.Image, timestamp, avatar_hash/url)
    # TTL mặc định 10 phút; sau này thêm LRU
    # fallback: colored circle nếu tải lỗi
```

### 5.4 Renderer pure
```
image = renderer.render(state, map_data, assets, camera)  →  RenderResult
# RenderResult: image: PIL.Image, filename, content_type
# Discord adapter tự convert PIL.Image → BytesIO → discord.File
```
Renderer KHÔNG move_player, KHÔNG save DB.

### 5.5 Camera
```
class Camera: mode, viewport_w, viewport_h, follow_player, center_x, center_y
# FULL_MAP (mặc định map nhỏ) | FOLLOW_PLAYER (crop quanh player cho map lớn)
```

### 5.6 Giới hạn kích thước
`MAX_RENDER_WIDTH/HEIGHT ~ 800–1400px`, `MAX_FILE_SIZE`. Tránh PNG quá khủng làm chậm upload.

### 5.7 Multi-player overlap
2 player cùng tile → renderer offset chéo / stacked tokens (KHÔNG overwrite ngẫu nhiên).

---

## 6. Concurrency & Robustness

### 6.1 ScenarioRuntime
```
@dataclass ScenarioRuntime:
    state, map_data, message_id, channel_id
    lock: asyncio.Lock
    dirty: bool
    render_task: asyncio.Task | None
```

### 6.2 Input queue (thay lock đơn giản)
```
Button click → enqueue Action → worker xử lý → state update → render → message edit
```
Không có 2 callback cùng sửa state.

### 6.3 Per-channel isolation
`runtime[channel_id].lock` — KHÔNG global lock.

### 6.4 Render coalescing
N click (100–300ms) → xử lý hết state → render 1 frame. MVP có thể render mỗi action thành công (sau đó mới batching).

### 6.5 Crash safety order
`apply state → save DB → render → Discord edit` (an toàn nhất). Nếu Discord edit fail → state vẫn đúng, có thể render lại lúc restart. Không rollback DB chỉ vì Discord lỗi.

### 6.6 Failure handling
- Render fail (Pillow crash): state + DB giữ nguyên, log `render failed`, bot không chết.
- Avatar fail: fallback colored circle, game tiếp tục.
- Message bị xóa: runtime detect → đánh dấu scenario invalid → bot tiếp tục chạy.
- Discord API fail: bot không crash, state không corrupt.

### 6.7 Rate limit
KHÔNG hard-code số lần/giây. Dựa vào discord.py + xử lý 429/retry. Có thể thêm debounce ~300–500ms ở app layer.

### 6.8 Error hierarchy
`GameError → MovementError, CollisionError, MapError, RenderError, PersistenceError`. CẤM `except Exception: pass`.

### 6.9 Debug logging
```
[GAME] [DISCORD] [RENDER] [DATABASE] [ASSET]
Ví dụ: [GAME] user=123 action=MOVE dir=N result=SUCCESS
       [RENDER] channel=456 render_ms=38
       [DISCORD] channel=456 edit_ms=210
```
Metrics: render_ms, save_ms, discord_edit_ms, queue_depth.

---

## 7. Discord UI

### 7.1 MapView (persistent)
```
class MapView(discord.ui.View):
    super().__init__(timeout=None)
    # 8 button: custom_id = "mg:{channel_id}:n/ne/e/se/s/sw/w/nw"
    # MVP thêm: 🔄 Refresh (sau: 🎒 Inventory — chưa làm logic)
```

### 7.2 Persistent view restore
Bot startup → connect DB → load scenarios → với mỗi scenario: rebuild GameState → `bot.add_view(MapView(state))`. Nút cũ còn sống sau restart.

### 7.3 Message lifecycle
Mỗi scenario = 1 message (attachment=map.png, view=MapView). Move → edit, không tạo message mới.

### 7.4 Interaction flow
```
Button → interaction → validate scenario → validate player
      → create MoveAction → GameManager.dispatch()
      → GameState.apply() → persist → render → edit message → interaction response
```

### 7.5 Interaction response
`await interaction.response.defer()` rồi render/update; cuối `followup` hoặc edit message. Xử lý exception nếu interaction expired.

### 7.6 Commands (MVP)
```
/startmap   → tạo scenario (quyền Manage Channel / admin role)
/joinmap    → join scenario (everyone)
/leave-map  → remove player
/map        → refresh map image
/mapreset   → admin reset (administrator)
/mapinfo    → hiển thị map/players/size/status
```
Spawn: player đầu tiên bấm move → nếu chưa có → spawn (hoặc dùng /joinmap rõ ràng).

---

## 8. Roadmap (Phase A–I)

| Phase | Nội dung |
|-------|----------|
| A | Bot boot → /ping → /startmap |
| B | Map PNG → GameState → 1 player → 8 buttons |
| C | Collision + Tiled |
| D | Multi-player + Avatar |
| E | SQLite + Restart restore |
| F | Persistent Views + Message recovery |
| G | Queue + Locks + Debounce + Error handling |
| H | Polish + Content pipeline |
| I | RPG systems (sau MVP) |

Chi tiết TASK 01–15 và 10 acceptance tests → xem `docs/acceptance-tests-va-tasks.md`.

---

## 9. Những điều TUYỆT ĐỐI không làm

Không: nhét mọi thứ vào bot.py; coupling state với discord.py; tải avatar mỗi render; tạo message mới mỗi bước; global lock; hard-code dimension/collision/spawn; lưu image trong SQLite; `except: pass`; blocking I/O; absolute path; renderer mutate state; DB biết Discord UI; callback chứa game rule; giả định fixed rate limit; dựa vào message content để biết state; implement RPG trước khi MVP ổn định.

---

## 10. Kiểm chứng thông tin (quan trọng)

| Claim file thô | Kiểm chứng | Đánh giá |
|------|------|------|
| discord.py==2.7.1 (3/2026) | PyPI v2.7.1, 2026-03-03, Python>=3.8 | ✅ ĐÚNG |
| Pillow==12.3.0 | PyPI v12.3.0, 2026-07-01, **Python>=3.10** | ✅ ĐÚNG (cần 3.10+) |
| Discordia GPL-3.0 | GitHub xác nhận GPL-3.0 | ✅ ĐÚNG |
| Discordia "update 27/08/2026" | GitHub: last push **2023-07-08** | ❌ SAI (dormant từ 2023) |
| DrSkunk ISC | GitHub "Other" nhưng README badge ISC | ⚠️ coi permissive, cẩn thận |
| Carto MIT | GitHub xác nhận MIT | ✅ ĐÚNG |
| dtre không license | GitHub null | ✅ ĐÚNG |
| DnDaisies không license | GitHub null | ✅ ĐÚNG |

**Lưu ý:** Discordia dormant → port code phải adapt discord.py 2.x. dtre/DnDaisies không license → chỉ học ý tưởng. Mảnh avatar-composite là giá trị tự xây.

---

*Đây là định hướng. Implement bắt đầu từ Phase A (vertical slice nhỏ nhất), chỉ qua phase sau khi acceptance tests phase trước pass.*
