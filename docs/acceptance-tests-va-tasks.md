# ACCEPTANCE TESTS & TASK BREAKDOWN

Phần chi tiết bổ sung cho `docs/ke-hoach-chi-tiet.md`. Lấy từ file thô (mục 56–90).

---

## 1. Acceptance Tests (MVP phải pass hết)

**#1 — Bot mới chạy, `/startmap`**
- Kết quả: 1 message, map image, 8 movement buttons.
- Pass nếu: KHÔNG có external website/app.

**#2 — 2 player join**
- User A `/joinmap`, User B `/joinmap` → 2 avatars hiện trên map.

**#3 — Move độc lập**
- A bấm ↑ → A.y -= 1, B không đổi.

**#4 — Spam không crash**
- A spam ↑↑↑↑↑ → không crash, không duplicate message, không tạo 5 map messages.

**#5 — Restart recovery**
- Bot restart → old message vẫn có buttons, bấm vẫn works.

**#6 — Đi vào tường**
- Player đi vào tile blocked → position unchanged, map unchanged.

**#7 — Discord API tạm fail**
- Bot không crash, state không corrupt.

**#8 — Avatar API fail**
- Fallback token, game continues.

**#9 — Message bị xóa**
- Runtime detect missing message → scenario marked broken/inactive → bot continues.

**#10 — 2 channel cùng chạy**
- #map-a và #map-b → state không leak qua lại.

---

## 2. Unit Test Strategy (tests/)

| File | Test |
|------|------|
| test_state.py | spawn, move 8 hướng, invalid player, out-of-bound |
| test_collision.py | walkable, blocked, edge, diagonal |
| test_map_loader.py | valid Tiled JSON, missing layer, invalid dimensions |
| test_renderer.py | player visible, 2 players, overlap, fallback avatar |
| test_database.py | save, load, update, delete, restart restore |

---

## 3. TASK breakdown (cho vibe-coding)

Mỗi task có: Goal / Files / Constraints / Acceptance / Tests.

- **TASK 01** — Project skeleton (bot.py, config.py, requirements.txt, .env.example, .gitignore, README).
- **TASK 02** — GameState + Player + move (game/state.py, game/actions.py).
- **TASK 03** — Tiled MapLoader (game/map_loader.py, assets/maps/test-map.json).
- **TASK 04** — Collision (game/collision.py).
- **TASK 05** — Pillow renderer (rendering/renderer.py, layers.py).
- **TASK 06** — Discord /startmap (discord_ui/commands.py).
- **TASK 07** — Buttons 8 hướng (discord_ui/map_view.py).
- **TASK 08** — Button → GameManager.dispatch (discord_ui/interactions.py, game/manager.py).
- **TASK 09** — SQLite (persistence/database.py, migrations.py, repositories.py).
- **TASK 10** — Restart restore (bot.py setup_hook add_view).
- **TASK 11** — Avatar cache (rendering/avatar.py).
- **TASK 12** — Concurrency queue + per-channel lock (game/manager.py ScenarioRuntime).
- **TASK 13** — Tests (tests/*).
- **TASK 14** — Load-test 2/10/50 simulated players.
- **TASK 15** — Polish UI (camera, names, HUD).

---

## 4. Vertical slice đầu tiên (khuyên làm trước tiên)

KHÔNG làm DB ngay. Làm:
```
/startmap → GameState → Pillow renderer → Discord image → 8 buttons
        → move → render → edit
```
Khi chạy ngon:
```
Discord UI → Game State → Render → Discord PNG
```
thì mới nối SQLite. Cách này hạn chế AI "đi xa khỏi vấn đề".

---

## 5. Thứ tự code khuyên dùng (Day/Phase)

```
A: Bot boot → /ping → /startmap
B: Map PNG → GameState → 1 player → 8 buttons
C: Collision + Tiled
D: Multi-player + Avatar
E: SQLite + Restart restore
F: Persistent Views + Message recovery
G: Queue + Locks + Debounce + Error handling
H: Polish + Content pipeline
I: RPG systems
```

---

## 6. Điểm thay đổi so với plan cũ (từ file thô)

| Plan cũ | Chốt mới |
|---------|----------|
| bot.py → mọi thứ | bot.py → bootstrap only |
| lock chống race là đủ | Action Queue + per-channel runtime |
| renderer = 1 hàm khổng lồ | layer pipeline |
| SQLite = chứa tất cả | persistence chỉ canonical state |
| Discord callback = game logic | callback = adapter |
| full map forever | Camera abstraction |
| avatar download mỗi render | AvatarCache |
| hard-code rate limit | Discord lib + retry |
| chỉ test tay | automated game-unit tests |

---

## 7. Mở rộng sau MVP (Version 1→3)

```
MVP → NPC → interaction → inventory → combat → quests
    → maps → dungeons → boss → events
```
Nếu đông: SQLite → PostgreSQL (architecture không đổi). Discord layer vẫn chỉ là frontend/transport.
