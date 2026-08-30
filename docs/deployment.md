# Triển khai lên Cloud (Discord bot hosting panel)

Cloud này là **hosting panel chuyên cho Discord bot** (kiểu Pterodactyl egg). Nó bị giới hạn nên ta làm việc theo đúng mẫu sau, không tự chạy shell tuỳ ý.

## Đặc điểm / hạn chế của cloud

- **Không có shell exec tự do** qua SFTP (tài khoản SFTP-only). Không chạy được `pip install`, `python bot.py`, `systemctl` từ xa.
- Panel **tự quản lý venv + cài依赖**: mỗi lần start, nó chạy một startup script tự tạo `.venv`, cài `requirements.txt`, rồi launch bot. Ta **không** cần tạo venv/thủ công.
- **SFTP là kênh upload file** duy nhất (port `2022`).
- **SSH port `30112`** chỉ để vào inspect (xem log, duyệt file `.venv`, `.data`) — từ một số mạng bị firewall nên có thể không reach được; lúc đó dùng web console của host.
- CWD mà panel chạy = **thư mục gốc SFTP của server** (startup script làm `cd /home/container`). Nên upload file thẳng vào gốc SFTP, không bọc thêm thư mục.

## Startup command của panel (đã xác nhận)

Panel chạy script này mỗi lần start (không cần ta viết):

```bash
set -e; cd /home/container; export PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
if [ -z "${DISCORD_TOKEN:-}" ]; then unset DISCORD_TOKEN; fi
if [ "${AUTO_UPDATE:-0}" = "1" ] && [ -d .git ]; then
  # git pull --ff-only (có auth qua USERNAME:ACCESS_TOKEN nếu có)
fi
if [ ! -f "${BOT_PY_FILE}" ]; then echo "ERROR: ..."; exit 1; fi
RUNTIME_ID="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
# tạo .venv nếu chưa có hoặc Python version đổi
# hash requirements -> cài lại chỉ khi đổi
exec .venv/bin/python "${BOT_PY_FILE}" ${PY_ARGUMENTS}
```

Tóm tắt: nó tự `cd /home/container` → tạo `.venv` (theo version Python) → `pip install -r ${REQUIREMENTS_FILE}` → `exec .venv/bin/python ${BOT_PY_FILE}`.

## Biến môi trường cần set trên panel

| Var | Giá trị cho dự án này | Bắt buộc |
|-----|----------------------|----------|
| `DISCORD_TOKEN` | token bot (đã lưu trong `.env` local) | ✅ |
| `BOT_PY_FILE` | `bot.py` | ✅ |
| `REQUIREMENTS_FILE` | `requirements.txt` | ✅ |
| `AUTO_UPDATE` | `0` (hoặc `1` nếu dùng git repo) | ⬜ |
| `USERNAME` / `ACCESS_TOKEN` | GitHub PAT nếu `AUTO_UPDATE=1` | ⬜ |
| `VOICE_SUPPORT` | `0` (bot không cần voice) | ⬜ |
| `PY_PACKAGES` | để trống | ⬜ |
| `PY_ARGUMENTS` | để trống | ⬜ |

> `python-dotenv` có trong `requirements.txt` và `bot.py` gọi `load_dotenv()`, nhưng trên panel biến đã được set sẵn → không cần file `.env` trên cloud (và `load_dotenv` không ghi đè env có sẵn).

## Quy trình setup (mỗi lần deploy)

1. **Set env vars** trên panel: `DISCORD_TOKEN`, `BOT_PY_FILE=bot.py`, `REQUIREMENTS_FILE=requirements.txt`.
2. **Upload file** qua SFTP lên gốc server (cùng chỗ panel chạy):
   - Dùng `scripts/upload_tree.py` (đã loại trừ `.venv/`, `data/`, `.env`, `.deploy.env`).
   - Đích = `DEPLOY_ROOT` trong `.deploy.env` (gốc SFTP của server).
3. **Start server** trên panel → tự động venv + cài deps + chạy `bot.py`.
4. Xem log trên panel; nếu lỗi thì SSH `30112` inspect (xem mục dưới).

Không upload `.venv` (panel tự tạo, nhẹ hơn và đúng version Python của container).

## SSH vào cloud (để inspect)

```bash
ssh -p 30112 conist.559348bb@node2.nexnodecloud.xyz
```

- User: `conist.559348bb` (chung account Kons)
- Port: `30112` (SSH/shell). SFTP vẫn là `2022`.
- Mật khẩu: lưu trong `.deploy.env` (`SFTP_PASS`), gitignored.
- Sau khi login: `cd /home/container` (hoặc gốc SFTP) → xem `.venv/`, `data/game.db`, log.
- ⚠️ Từ máy dev này port `30112` **bị firewall/unreachable** (Test-NetConnection = False). Dùng web console của host nếu cần shell.

## ⚠️ Cảnh báo host key (chưa xác thực)

- Bạn đưa: `SHA256:dOaeye064BZFwal3Tn0q60gjUv/K8TYOGQsWBc9jG9A`
- Thực tế `node2.nexnodecloud.xyz:2022` (ED25519): `SHA256:EdC3wIhzjodcuObcDmxe6ZnybZ1Md/WTQttRSe+OfvE`
- **Không khớp.** Có thể fingerprint bạn lưu thuộc host/port khác (ví dụ chính port `30112`). Đối chiếu thủ công trước khi trust. SFTP password-auth vẫn hoạt động trên `:2022`.

## Thông tin server (`.deploy.env`)

| Trường | Giá trị |
|--------|--------|
| SFTP host | `node2.nexnodecloud.xyz` |
| SFTP port | `2022` (reachable, auth OK) |
| SSH port | `30112` (bạn xác nhận; unreachable từ máy dev) |
| User | `conist.559348bb` |
| Deploy root | **SFTP home = container `/home/container`**. `upload_tree.py` tự lấy `sftp.getcwd()` (không dùng subdir). ⚠️ Trước đây dùng `…/discord-map-game` gây lỗi "bot.py not found" vì panel `cd /home/container` không thấy file nằm trong subdir. |

## Khôi phục sau restart

`bot.py` có `setup_hook()` đọc DB, rebuild `GameState`, và `bot.add_view(MapView(...))` cho mỗi scenario → nút cũ vẫn sống, vị trí không mất. Khi panel restart, nó chạy lại startup script, tạo lại `.venv` (nếu version đổi) và launch bot; dữ liệu nằm trong `data/game.db` (trong gốc SFTP, không bị xoá trừ khi reset server).

## Lưu ý path (đã fix)

`config.py` dùng `PROJECT_ROOT = Path(__file__).resolve().parent` → `ASSETS_DIR = <root>/assets/maps`, `DATA_DIR = <root>/data`. Khi panel chạy `bot.py` ở gốc container, path đúng là `<container>/assets/maps` và `<container>/data`. (Bản cũ dùng `parent.parent` sẽ lệch một cấp — đã sửa.)

## Kinh nghiệm thực tế (rút ra khi deploy lần đầu — session 2026-08-28)

Những lỗi sau NỔ Ở RUNTIME (không bắt được bằng `py_compile`/import), ghi lại để session sau không đi lại vết xe:

### 1. Entry-point command (HTTP 50240)
- Triệu chứng: `bot.tree.sync()` lỗi `400 Bad Request (50240): You cannot remove this app's Entry Point command in a bulk update operation`.
- Nguyên nhân: app có lệnh Entry Point (`type: 4`, tên `'launch'`) do tính năng Activities; bulk sync global không được xoá nó.
- Fix: trong `bot.py` `setup_hook`, trước `tree.sync()` gọi `bot.http.get_global_commands` → xoá các cmd `type == 4` riêng lẻ. (Hoặc tắt Activities trong Discord Developer Portal.)
- KHÔNG dùng `discord.AppCommandType.entry_point` — discord.py 2.7.1 chưa có enum này; check raw `type == 4`.

### 2. API discord.py 2.x (khác 1.x)
- `commands.slash_command` không tồn tại → dùng `@app_commands.command(name=, description=)` trên method của `commands.Cog`.
- `Button(..., callback=...)` không hợp lệ → tạo Button xong gán `btn.callback = func` (signature `cb(interaction)` chỉ 1 arg; discord gọi `item.callback(interaction)`).
- `Direction[key.upper()]` sai → dùng `DIR_BY_KEY` map `"e"→Direction.EAST` … (enum là `EAST`, không phải `E`).

### 3. SQLite: phải tạo sẵn thư mục `data/`
- `aiosqlite.connect(path)` nổ `OperationalError: unable to open database file` nếu thư mục cha chưa có.
- Fix: `Database.connect()` làm `Path(self.path).parent.mkdir(parents=True, exist_ok=True)` trước khi connect.

### 4. Avatar / font / network trên container
- Container **không có TTF font** và (nhiều khả năng) **không ra được CDN Discord** → đừng依赖 tải avatar thật hay vẽ chữ bằng font.
- Fix: `AvatarCache` vẽ token **mặt cười (face)** thuần PIL, không font/mạng. `use_network=False` mặc định; set env `USE_DISCORD_AVATARS=1` (timeout 4s) nếu muốn ảnh thật.

### 5. Độ trễ (latency)
- Mỗi bấm nút: dùng 1 lượt `interaction.response.edit_message(attachments=[...], view=self)` (tin nhắn map = tin nhắn chứa nút). KHÔNG `defer()` + `edit` (2 lượt).
- Đi vào tường (collision): truyền `result.state_changed=False` → chỉ `edit_message(view=self)`, **không re-upload ảnh** → gần tức thì.
- Bước đi thực sự vẫn ~0.5–1s do Discord xử lý lại attachment (không thể tức thì với mô hình ảnh). Muốn snappier hơn → chuyển sang grid emoji/text (hy sinh avatar thật).

### 6. Emoji nút
- `⬘️` (U+2B18) bị Discord từ chối (`Invalid emoji`) và ngữ nghĩa sai (là tây-nam, không phải đông-nam). Dùng `↘️` (U+2198) cho `se`.
- Layout: discord xếp nút trong hàng theo thứ tự thêm → thêm theo hàng (trái→phải) để lưới 3×3 đúng.

### 7. Tin nhắn map bị xoá → tự phục hồi
- `rt.message_id` lưu trong DB; nếu user xoá tin nhắn bot, mọi edit/fetch sẽ lỗi.
- Fix: `_ensure_map_message()` (và `map_view._render_and_edit`) thử `fetch_message`; nếu `NotFound`/`HTTPException` → `channel.send` tin nhắn mới, cập nhật `rt.message_id` + DB.

### 8. Biến môi trường
- `DISCORD_TOKEN`: để trong `.env` (upload lên cloud, `load_dotenv` đọc) HOẶC set trực tiếp trên panel.
- `USE_DISCORD_AVATARS=1` để bật ảnh avatar thật (mặc định tắt).

#### 9. Reset trên panel KHÔNG deploy code — phải upload_tree.py
- Triệu chứng: sau khi sửa code local, bấm **Restart/Reset** trên panel mà bot vẫn chạy code cũ (vd: vẫn ra map demo cờ vua `test-map.png`, không có lệnh mới).
- Nguyên nhân: panel chỉ **khởi động lại process đang có trên server**. Nó **không kéo code từ máy dev**. File local thay đổi không tự bay lên cloud.
- Fix đúng: chạy `scripts/upload_tree.py` (SFTP port `2022`, reachable) để mirror cây project lên `/home/container`, **sau đó** mới Restart panel.
  - ⚠️ `scripts/deploy_cloud.ps1` dùng SSH (`30112`) — doc ghi rõ port này **bị firewall chặn từ máy dev** → không dùng được từ local. Dùng `upload_tree.py`.
  - `upload_tree.py` đã loại trừ `.venv/`, `data/`, `.git`, `.deploy.env` → upload `.py`, `assets/**` (kể cả `*.png` tileset + `*.js` map), và ghi đè `.env`.
- Cách chẩn đoán nhanh "có phải code cũ không": map demo `test-map.png` là ảnh **cờ vua 3 màu** (320×320). Map Tiled (`bigmap`) render **hàng trăm màu** (grass/tòa nhà/cây). Thấy cờ vua = 100% đang chạy code cũ.

### 10. Kích thước ảnh / latency — 2旋钮 (knob)
- Ảnh lên Discord = `viewport (tile) × tile_size(32) × MAP_UPLOAD_SCALE`.
  - `rendering/camera.py`: `DEFAULT_VIEW_W` / `DEFAULT_VIEW_H` = số tile hiển thị (camera follow).
  - `rendering/renderer.py`: `MAP_UPLOAD_SCALE` = tỉ lệ thu nhỏ khi upload (palette 16 màu + `optimize=True` để nhẹ).
- Muốn ảnh to hơn → tăng `MAP_UPLOAD_SCALE` (0.25 → 0.5 → 1.0) và/hoặc `DEFAULT_VIEW_W/H`.
  - Đổi giá trị này làm ảnh nặng hơn → upload chậm hơn (trần Discord). Đây là đánh đổi rõ ràng: to hơn = chậm hơn.
  - Với `bigmap` (49×39): viewport 25×17 + scale 0.5 → ảnh upload **400×272** (đủ xem). Scale 1.0 → 800×544 (to hơn, nặng hơn).

## Lệnh làm việc hàng ngày
```bash
# local
.venv\Scripts\python -m pytest tests -q
.venv\Scripts\python bot.py
# upload lại cloud (tự đẩy vào /home/container)
.venv\Scripts\python scripts/upload_tree.py
# kiểm tra lệnh đã đăng ký (HTTP only, không ngắt bot đang chạy)
.venv\Scripts\python scripts/diag_commands.py
```
