# Discord Map-Game Bot

Bot Discord game mà **toàn bộ màn hình game là một Discord message** (KHÔNG web, KHÔNG app ngoài).

Player bấm button 8 hướng → bot cập nhật state → render PNG mới (avatar Discord composite lên map) → `message.edit()`.

## Trực quan

```
#game-room
└── 1 message duy nhất
    ┌──────────────────────────────┐
    │          MAP IMAGE (PNG)      │
    │       🐱 Player A             │
    │                 🐱 Player B   │
    └──────────────────────────────┘
         [ ↖ ][ ↑ ][ ↗ ]
         [ ← ][ • ][ → ]
         [ ↙ ][ ↓ ][ ↘ ]
```

## Tài liệu định hướng

- [docs/ke-hoach-chi-tiet.md](docs/ke-hoach-chi-tiet.md) — kế hoạch chi tiết, phân tích từng thành phần, kiểm chứng thông tin, roadmap.
- [docs/acceptance-tests-va-tasks.md](docs/acceptance-tests-va-tasks.md) — 10 acceptance tests + 15 TASK + thứ tự code.
- [docs/phat-trien-local.md](docs/phat-trien-local.md) — chạy bot local, test, commands.
- [docs/deployment.md](docs/deployment.md) — triển khai cloud (mẫu Kons + systemd).
- [AGENTS.md](AGENTS.md) — engineering rules (guardrails cho coding agent).

## Stack (đã verify version)

| Package | Version | Ghi chú |
|---------|---------|---------|
| Python | 3.11+ | Pillow 12.3.0 cần >=3.10 |
| discord.py | 2.7.1 | PyPI, released 2026-03-03 |
| Pillow | 12.3.0 | PyPI, released 2026-07-01, MIT-CMU |
| aiosqlite | latest | SQLite async |
| aiohttp | latest | tải avatar Discord |
| python-dotenv | latest | đọc `.env` |

## Tiến trình

Phase 0 → A (skeleton) → B (static map + 1 player) → C (collision + Tiled) → D (multiplayer + avatar) → E (SQLite + restart) → F (persistent views) → G (concurrency/robustness) → H (polish).

Xem chi tiết acceptance tests trong `docs/ke-hoach-chi-tiet.md`.

## Bắt đầu (từ máy sạch / máy khác)

Yêu cầu: Python 3.11+, Git, và SSH key đã đăng ký trên GitHub (xem phần *Làm việc trên máy khác*).

```bash
# 1. Clone repo
git clone git@github.com:conist-z/Dau-Hu-Chien-MMO.git
cd Dau-Hu-Chien-MMO

# 2. Tạo venv và cài dependencies
python3 -m venv venv
source venv/bin/activate          # Windows (PowerShell): venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Tạo file .env từ mẫu (KHÔNG commit .env — đã nằm trong .gitignore)
cp .env.example .env
#   mở .env, điền: DISCORD_TOKEN=...   (token bot từ Discord Developer Portal)

# 4. Chạy bot
python bot.py
```

Biến môi trường (`.env`):

| Biến | Bắt buộc | Ý nghĩa |
|------|----------|---------|
| `DISCORD_TOKEN` | Có | Token của Discord bot (bật Privileged Intents: Server Members + Message Content) |
| `ADMIN_ROLE_ID` | Không | Role được phép `/startmap`, `/mapreset`. Để trống = Manage Channel / Administrator |
| `USE_DISCORD_AVATARS` | Không | đặt `1` để composite avatar thật của user lên map |

## Lệnh Slack (Discord)

Dùng trong kênh Discord (mỗi kênh = 1 scenario/map riêng biệt):

- `/startmap` — tạo map + message game mới trong kênh hiện tại
- `/joinmap` — tham gia map (tạo avatar từ Discord avatar của bạn)
- `/leave-map` — rời map
- `/map` — hiển thị lại message game
- `/mapreset` — reset map về trạng thái ban đầu
- `/mapinfo` — thông tin map (kích thước, tile, người chơi)

Di chuyển: bấm các nút 8 hướng (↖ ↑ ↗ ← • → ↙ ↓ ↘) trong message game.

## Cấu trúc dự án

```
game/         state.py actions.py collision.py map_loader.py rules.py manager.py
discord_ui/   commands.py map_view.py interactions.py errors.py coalescer.py
rendering/    renderer.py avatar.py layers.py camera.py
persistence/  database.py migrations.py repositories.py
tests/        test_state.py test_collision.py test_map_loader.py test_renderer.py ...
assets/maps/  file Tiled (.json) + tile PNG
```

Quy tắc kiến trúc quan trọng: game logic không phụ thuộc discord.py, rendering không mutate state,
mỗi kênh có 1 message game duy nhất, di chuyển là các Action xác định (xem `AGENTS.md`).

## Test

```bash
pip install pytest
pytest
```

## Làm việc trên máy khác (không cần context)

Repo này được thiết kế để clone về bất kỳ máy nào và code tiếp bằng CLI:

1. **Copy SSH private key.** Máy này dùng 2 key trong `~/.ssh/`:
   - `id_ed25519` — deploy key cho repo `anhkhaklkl-cell/the-demo-game`
   - `id_ed25519_dauhu` — deploy key (Write) cho repo `conist-z/Dau-Hu-Chien-MMO` (repo này)

   Copy file private key tương ứng sang `~/.ssh/` trên máy mới, rồi:
   ```bash
   chmod 600 ~/.ssh/id_ed25519_dauhu
   # thêm đoạn sau vào ~/.ssh/config
   # Host github-dauhu
   #   HostName github.com
   #   User git
   #   IdentityFile ~/.ssh/id_ed25519_dauhu
   #   IdentitiesOnly yes
   ```
2. Clone và chạy theo mục *Bắt đầu* ở trên.

> Không bao giờ commit `.env`, `*.db`, `data/`, `.venv/` — đã có trong `.gitignore`.
> Mọi quy tắc kiến trúc/guardrails cho coding agent nằm trong `AGENTS.md`.

