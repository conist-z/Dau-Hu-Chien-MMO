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

## Bắt đầu

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install discord.py==2.7.1 Pillow==12.3.0 aiosqlite aiohttp python-dotenv
```
