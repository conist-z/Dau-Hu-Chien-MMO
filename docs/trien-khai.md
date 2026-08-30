# Kế hoạch triển khai (rollout) — Discord Map Game MMO

Trạng thái: **Phase A/B (vertical slice) đã implement xong và giờ deploy được lên cloud**. Phần còn lại theo pha C–I. Tài liệu design đầy đủ: `docs/ke-hoach-chi-tiet.md`, `docs/acceptance-tests-va-tasks.md`.

## 0. Trạng thái (cập nhật 2026-08-28)

- ✅ **ĐÃ DEPLOY XANH** trên cloud Pterodactyl: `[READY] Đậu Hũ Chiên#0768`, 6 lệnh slash hiện (global + guild sync).
- ✅ Code vertical slice: `game/`, `rendering/`, `persistence/`, `discord_ui/`, `bot.py`. Compile sạch, 11/11 unit test pass.
- ✅ `/startmap` → ảnh map + 9 nút d-pad; `/joinmap` vẽ nhân vật; bấm di chuyển update ảnh; tin nhắn map tự phục hồi khi bị xoá.
- ⚠️ Host key fingerprint chưa khớp (`dOaey…` vs `EdC3…`), port `30112` unreachable từ máy dev → dùng web console host nếu cần shell.

### Lỗi runtime đã fix trong quá trình deploy (ghi nhớ để session sau không lặp lại)
1. `bot.py not found` → upload sai subdir. Fix: upload lên SFTP home = `/home/container`.
2. `AttributeError: slash_command` → discord.py 2.x dùng `@app_commands.command`.
3. `TypeError: Button callback` → gán `btn.callback` sau khi tạo, signature `cb(interaction)`.
4. `sqlite3 unable to open database file` → `Database.connect` mkdir thư mục `data/`.
5. `HTTP 50240 Entry Point` → prune cmd `type==4` trước `tree.sync()` (AppCommandType.entry_point chưa có ở 2.7.1).
6. `KeyError: 'E'` khi bấm move → `DIR_BY_KEY` map key→Direction.
7. `Invalid emoji 50035` → `se` dùng `↘️` (U+2198) thay `⬘️`.
8. Avatar trắng/trơn → container thiếu font + CDN; vẽ face token thuần PIL, `use_network=False`.
9. Độ trễ → 1 lượt `edit_message` cho nút; blocked-move không re-upload ảnh.
10. Tin nhắn map bị xoá → `_ensure_map_message` tự tạo lại.

Chi tiết từng cái: `docs/deployment.md` §"Kinh nghiệm thực tế".

## 1. Bước ngay (xác nhận deploy)

1. Trên panel: set env `DISCORD_TOKEN` (hoặc để `.env` đã upload lo). `BOT_PY_FILE=bot.py`, `REQUIREMENTS_FILE=requirements.txt` là default.
2. Bấm **Start**. Panel chạy startup script → tạo `.venv` (Python 3.14.7) → cài deps → launch.
3. Kiểm tra log: phải thấy `Discord.py runtime ready; launching bot.` rồi `[READY] <bot>#xxxx` và `[BOOT] restored 0 scenario(s)`.
4. Trong Discord: `/startmap` → `/joinmap` → bấm nút di chuyển → ảnh map update.

### Rủi ro version (Python 3.14.7 trên panel)
- `Pillow==12.3.0` cần Python ≥3.10 → 3.14 OK (có wheel).
- `discord.py==2.7.1` chính thức support tới 3.13; trên 3.14 có thể warn nhưng thường install được. **Nếu cài fail**, mở `requirements.txt` bỏ pin: `discord.py` (latest 2.x), `Pillow` (latest). Script panel chỉ cài lại khi hash requirements đổi.

## 2. Pha triển khai (tham chiếu repo đã xác minh)

| Pha | Nội dung | Tham chiếu repo | Trạng thái |
|-----|----------|-----------------|------------|
| A | Project scaffold + venv/startup tương thích panel | — | ✅ |
| B | State/Action/Movement 8-hướng + collision + SQLite | Discordia (grid MUD, 8-dir `n/e/s/w/up/right/down/left`) | ✅ |
| C | Renderer: composite avatar lên tilemap + camera + layers | Carto (grid token render), Discordia (FOV image) | 🟡 cơ bản có, cần polish sprites/tile |
| D | Discord UI: `/startmap /joinmap /leave-map /map /mapreset /mapinfo` + nút persistent `timeout=None` | Carto (message + token UI) | ✅ slash + 8 nút; thiếu info/reset polish |
| E | Multi-player + isolation theo `channel_id` + 1 message/scenario | Discordia (multi-user) | 🟡 runtime per channel có, chưa test nhiều người |
| F | Avatar cache (async, không lưu binary vào SQLite) | — | ✅ `AvatarCache` |
| G | Restart recovery (rebuild view/state từ DB) | — | ✅ `setup_hook` |
| H | Map data-driven (Tiled JSON) + collision từ layer | Carto (tile grid) | 🟡 loader có, thiếu pipeline Tiled chuẩn |
| I | Tests đầy đủ + lint + typecheck | — | 🟡 11 test; thiếu renderer/db/integration |

### Ghi chú bản quyền (quan trọng)
- **Discordia = GPL-3.0 (copyleft)**: chỉ học *thiết kế* (grid MUD, 8-dir, FOV). **KHÔNG copy code** vào repo này.
- **Carto = MIT**: có thể học + adapt tự do (nhưng là TypeScript, ta viết Python riêng).
- **dtre / DnDaisies = không license** (all rights reserved): chỉ đọc ý tưởng.
- **discord-plays-pokemon**: coi là permissive, cẩn thận.

## 3. Thứ tự ưu tiên tiếp theo (sau khi deploy xanh)

1. **Verify deploy xanh** (bước 1) — nếu lỗi version → nới pin `requirements.txt`.
2. **Phase C/H**: chuẩn hoá Tiled JSON pipeline + tile render đúng kích thước (hiện `test-map.json` tự tạo, chưa qua Tiled editor).
3. **Phase D**: hoàn thiện `/mapinfo`, `/mapreset` (xóa DB scenario + xoá message cũ an toàn).
4. **Phase E**: test 2+ user cùng channel, xác nhận isolate và edit 1 message.
5. **Phase I**: thêm test `test_renderer.py`, `test_database.py`, chạy `ruff`/typecheck.

## 4. Lệnh dev local

```bash
cd "D:\dự án mini build bot discord mmo event"
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\python bot.py        # cần .env có DISCORD_TOKEN
```

Upload lại cloud:
```bash
.venv\Scripts\python scripts/upload_tree.py   # tự đẩy vào container /home/container
```
