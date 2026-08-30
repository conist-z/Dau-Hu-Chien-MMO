# Phát triển local

## Yêu cầu
- Python 3.11+ (đã verify: 3.12.10 trên máy này).
- `discord.py==2.7.1`, `Pillow==12.3.0`, `aiosqlite`, `aiohttp`, `python-dotenv`.
- Một Discord bot token trong `.env` (`DISCORD_TOKEN=...`).

## Setup
```powershell
cd "D:\dự án mini build bot discord mmo event"
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Tạo asset map test
```powershell
python scripts/make_test_map.py   # sinh assets/maps/test-map.png (320x320)
```

## Chạy bot
```powershell
python bot.py
```

## Slash commands (MVP)
- `/startmap` — tạo map cho kênh, gửi 1 message + 8 nút.
- `/joinmap` — tham gia, spawn tại tile định nghĩa trong `test-map.json`.
- `/leave-map` — rời map.
- `/map` — làm mới ảnh.
- `/mapreset` — xoá players (admin).
- `/mapinfo` — xem thông tin.

Bấm nút ↖↑↗ ←↓→ ↙↘🔄 để di chuyển / refresh. Mỗi bước → render PNG → `message.edit()`.

## Test
```powershell
python -m pytest tests -q
```
Test thuần (không cần Discord): `test_state`, `test_collision`, `test_map_loader`.

## Cấu trúc
```
game/          logic thuần (không import discord)
rendering/     Pillow renderer + avatar cache (async)
persistence/   SQLite (aiosqlite)
discord_ui/    commands + MapView (adapter mỏng)
bot.py         bootstrap
```

Xem `docs/ke-hoach-chi-tiet.md` và `AGENTS.md` cho kiến trúc & guardrails.
