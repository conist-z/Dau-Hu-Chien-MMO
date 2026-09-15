# Khu vực phụ nối bằng CỔNG (dungeon / nhà / mini-map riêng)

Tài liệu này là hướng dẫn **build một khu vực phụ** kiểu `montertradebase`:
một map Tiled riêng, đi vào bằng cửa ở map chính, có va chạm/dữ liệu/tileset
riêng, hoạt động cho **cả Discord client lẫn web client**.

Đọc kèm:
`docs/web_client_session_knowledge.md` (mục 7 = probe production, mục 8 =
portal + 2 bẫy session/`input_seq`), `docs/deployment.md` (upload SFTP).

---

## 0. Khi nào dùng mô hình này

Dùng khi khu vực phụ cần **map riêng** (nội thất nhà, dungeon, hang, tầng hầm,
khu boss). Đừng dùng khi chỉ cần chặn ở một góc map chính — map mới kéo theo
tileset, collision, camera, screen/hub riêng.

Ví dụ đã chạy thật: `lobbytrade` (chợ) → cửa `(38,24)-(38,25)` →
`montertradebase` (nội thất 20×14) → thảm đất `(8,12)-(9,12)` → quay lại chợ
`(38,26)`.

---

## 1. Kiến trúc (một dòng dữ liệu, đừng phá)

```
main world   : GameManager.runtimes[channel_id]            (map chính)
side worlds  : GameManager.side_runtimes[(channel_id, map_id)]   (mọi khu phụ)
per-player   : rt.state.players[uid], rt.screens[uid], rt.web_sessions[uid]
```

- Side world là **runtime độc lập**: collision, camera, zombie, thời tiết,
  screen/hub riêng. Người chơi ở map A không thấy người ở map B.
- `move_player_between_runtimes(src, dst, uid, tile)` chuyển Player + Screen +
  **WebSession** + inventory sang runtime đích. Đây là điểm mấu chốt: sót
  WebSession là player đứng hình ở client nhưng vẫn dự đoán (bug cũ).
- Khu vực thuộc `TRADE_ZONE_MAPS` (`game/travel.py`) thì **cấm phá/đặt block**.

---

## 2. Chuẩn bị map Tiled

1. **Đường dẫn tileset**: ghi **tương đối theo file map**, ví dụ
   `"image": "../tilesets/atlas_32x.png"`. Tiled đọc path so với file `.json`,
   nên `"assets/tilesets/..."` sẽ làm Tiled báo tileset đỏ. Server chỉ lấy
   **basename** và tự tìm trong `assets/tilesets/` → game không bị ảnh hưởng.
2. **Dạng `data`**: Tiled export ra mảng **phẳng** (`width × height`) hay hàng
   lồng nhau đều OK. Server chuẩn hoá khi dựng payload; client bake theo
   **payload**, không đọc file map. Đừng viết code client đọc file map thô.
3. **Kích thước payload có thể nhỏ hơn file**: server cắt (bbox) hàng/cột rỗng
   → `montertradebase.json` 20×20 nhưng payload là 20×14. **Không hard-code
   kích thước map** — luôn lấy `welcome.map.width/height`.
4. **Collision sinh từ TÊN LAYER** (`game/map_loader.py::_is_blocking_layer`,
   ASCII-fold, bỏ dấu). Đặt tên layer theo quy ước:

   | Tên layer (đã bỏ dấu) | Kết quả |
   |---|---|
   | `khong di xuyen`, `khong di qua duoc`, `tuong`, `wall`, `building`, `tang đa`, `rock`, `cay`/`tree`, `nuoc`/`water`, `collision`, `va cham` | **CHẶN** |
   | `cua ... tuong tac` (cửa tương tác được) | **ĐI ĐƯỢC** (là cổng) |
   | `tham dat`, `tham ... ra vao` (thảm đất nơi cửa ra vào) | **ĐI ĐƯỢC** (sàn) |
   | `vung nuoc` (vũng nước mưa), `cay chet`, `cay cau` | **ĐI ĐƯỢC** |
   | layer không khớp từ khoá nào | **ĐI ĐƯỢC** (map không có layer chặn = đi khắp nơi) |

   Muốn thêm vật cản mới: đặt tên có `(không đi xuyên được)` là xong, không
   phải sửa code.
5. **Tile đồ trang trí (cây đá nấm hoa)**: nếu chúng có trong
   `RESOURCE_LAYER_NAMES` của server thì client vẽ bằng sprite động — tên
   layer phải khớp (`cay`, `tang da nho`, `nam nau`, `co`, `hoa trang`, …).

---

## 3. `assets/maps/portals.json` — cấu hình cổng

```json
{
  "lobbytrade": {
    "spawn": "spawn_layer",
    "destinations": {
      "montertradebase": {
        "map_id": "montertradebase",
        "target": "spawn_layer",
        "portal_tiles": [[38, 24], [38, 25]]
      }
    }
  },
  "montertradebase": {
    "spawn": [[8, 12], [9, 12]],
    "destinations": {
      "lobbytrade": {
        "map_id": "lobbytrade",
        "target": [38, 26],
        "portal_tiles": [[8, 12], [9, 12]]
      }
    }
  }
}
```

- `spawn` = **nơi người chơi đến khi vào map này** (`[[x,y],…]`, hoặc
  `"spawn_layer"` để lấy ô ở layer spawn).
- `destinations.<map>::portal_tiles` = **ô cửa của map nguồn**.
- `target` = **nơi đến ở map đích** (danh sách ô, hoặc `"spawn_layer"`).
- Quy tắc: **1 cụm trigger = 1 cửa**. Từng có bug 2 cụm cửa cách nhau 8 ô
  nhưng cùng trỏ vào 1 map (vẽ lẫn layer trong Tiled) → đi cửa nào cũng vào
  cùng một chỗ. Kiểm bằng script: đếm tile theo từng layer cửa xem có cụm lạ.
- Ô trigger **phải đi được** trong collision map đích/nguồn, và **không nên
  trùng** ô mà người chơi cần đứng để làm việc khác (thùng, rương, bảng).

---

## 4. Cơ chế cổng hiện tại (sau khi fix 16/09 — "cổng hơi nhạy")

`game/travel.py`:

| Thành phần | Ý nghĩa |
|---|---|
| `_touching_portal_link` | Cổng là **GATE**: phát hiện khi **hộp va chạm** chạm ô cửa, cộng `GATE_MARGIN` 0.15 để cú "đẩy vào cửa" cũng ăn (client web dừng sát mép 0.01 ô) |
| `_PORTAL_LATCH_ATTR` (`rt.on_portal_tile`) | Tập "đang chạm cổng" → dùng làm **edge detector** |
| `_GATE_COOLDOWN_SECONDS` (0.9s) | Ân hạn sau mỗi lần dịch chuyển |

Luật chạy:

1. Cổng chỉ nổ khi **chuyển trạng thái** từ *không chạm* → *chạm*. Đứng, đẩy
   hay rung nhẹ ở cửa **không bao giờ** nổ lại.
2. Người vừa dịch chuyển được **seed là "đang chạm"** ở runtime đích →
   khoảnh khắc đến không bao giờ bị dịch ngược lại.
3. `free_arrival_tile(..., portal_cfg=…)` **ưu tiên ô không phải cổng**; nếu
   ứng viên duy nhất là ô cổng (thảm đất), nó **né sang ô kề đi được** → người
   chơi đứng *cạnh* cổng ra, muốn thoát thì bước lên thảm.
4. **Không còn timer re-arm.** Bản cũ re-arm sau 2.5s kể cả khi người chơi vẫn
   đứng sát cửa vừa ra → cử động nhỏ là bị hút ngược vào (đúng lỗi người dùng
   báo). Giờ chỉ có edge + cooldown.
5. Đi đâu: muốn thoát khỏi phòng → bước lên thảm (đã ở ngoài vùng chạm) → nổ ✓.

**Chọn điểm đến**: đặt `target` cách cổng ≥2 ô để người chơi vừa ra không dính
lại cổng, và để `free_arrival_tile` có ứng viên không phải ô cổng.

---

## 5. Phía WEB CLIENT — 4 thứ BẮT BUỘC (đây là nguồn của gần hết bug)

Khi đi qua cổng, **server phải gửi lại `welcome`** cho client web. Không có
client tự biết đổi map: snapshot chỉ mang `map_id`, client **không** rebuild
world từ snapshot.

1. **Hook đổi map** (`game/manager.py`): `self.web_map_change_hook` được
   `WebHub.__init__` cắm vào; `_teleport_through_link` gọi hook ngay sau khi
   chuyển runtime ⇒ `send_map_welcome` push `build_welcome(dst_rt, uid)`.
   *Bẫy*: `conn.session` (registry) và `rt.web_sessions[uid]` (manager) là
   **2 object khác nhau**; `send_to_client_conn` so khớp bằng **identity** nên
   truyền nhầm object ⇒ welcome bị **bỏ im lặng** (không exception). Luôn lọc
   theo `user_id + channel_id`.
2. **`input_seq` handoff** (`web_api/snapshots.py::build_welcome`): welcome mang
   seq cao nhất của session. Client cũ reset `inputSeq = 0` mỗi welcome trong
   khi server vẫn đếm tiếp ⇒ `ack` nằm trước mọi input mới ⇒ replay/reconcile
   tắt ⇒ lệch vĩnh viễn sau mỗi lần đổi map.
3. **Chặn snapshot map khác** (`game.ts::applySnapshot`): snapshot có `map_id`
   ≠ map hiện tại bị **bỏ qua** cho tới khi welcome tới ⇒ hết cảnh avatar đứng
   ở map cũ rồi "nháy".
4. **Dọn visual map cũ** (`game.ts::buildWorld` khi `map.id` đổi):
   - **destroy bake cũ** (`map-bake`) — nếu không, canvas map cũ phủ luôn map mới;
   - **xoá sprite resource + `resourceTiles`**; chữ ký lớp resource phải
     **gồm map id**, vì map đích có 0 resource ⇒ `"" == ""` ⇒ guard bỏ qua dọn
     dẹp ⇒ cỏ/hoa của map cũ nằm trên map mới (lớp resource là `depth -5`, **trên**
     bake `depth -10`);
   - bake thiếu sheet thì **xin lại** (`assetFetch`, 1 lần/giây) thay vì `return`
     im lặng;
   - cache texture (`assetTextures`) **chỉ set khi decode xong** (đặt lúc gửi
     request ⇒ 1 lần decode lỗi là kẹt vĩnh viễn).

Ghi nhớ thứ tự lớp: `map-bake -10` < `resource layer -5` < block layer < player.

---

## 6. Checklist thêm một khu vực phụ mới (10 bước)

1. Tạo map mới trong Tiled (map `<id>.json`) trong `assets/maps/`.
2. Tileset: path **tương đối** (`../tilesets/<file>.png`), copy PNG vào
   `assets/tilesets/`.
3. Đặt tên layer theo quy ước ở §2.4 (chặn/đi được/cửa/thảm).
4. Vẽ cửa ở map nguồn (layer `cửa ... (tương tác được)`), và **ô ra ở map đích**
   (thảm/ô trống đi được).
5. Thêm `destinations` cho cả 2 chiều vào `assets/maps/portals.json`
   (`portal_tiles` + `target`/`spawn`).
6. Nếu là khu cấm xây: thêm map id vào `TRADE_ZONE_MAPS` (`game/travel.py`).
7. Test nhanh: `pytest tests/test_portal_gate.py tests/test_web_portal_map_switch.py -q`
   (cổng + welcome). Thêm map rồi thì chạy `pytest tests -q -k "map_loader or collision"`.
8. Upload: `.venv\Scripts\python scripts/deploy_files.py <các file .py>` cho code
   và `scripts/upload_tree.py` nếu thêm map/tileset mới (upload **phẳng** vào
   `/home/container`, không bọc thư mục con).
9. **Restart bot trên panel** (panel không tự nạp code).
10. Probe production như client thật (xem §7) trước khi báo "xong".

---

## 7. Probe production (không cần mở trình duyệt)

Dùng websocket vào relay, login guest, join channel, rồi **tự đi bộ** bằng
`input` frames (client-authoritative: server bám theo vị trí báo lên, có cap
tốc độ ⇒ phải báo từng bước nhỏ ~0.05 ô/frame):

```python
import asyncio, json, aiohttp
RELAY = "wss://web-production-19398.up.railway.app/ws"
async def main():
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(RELAY)
        await ws.send_json({"type": "guest_login", "guest_id": "912345678901234567"})
        # -> login_result{token}
        await ws.send_json({"type": "join", "token": token, "channel_id": "<id>"})
        # -> welcome{map,self,...}
        await ws.send_json({"type": "chat_cmd", "text": "/khutraodoi in"})
        # -> welcome lần 2 (map lobbytrade)
        # đi bộ tới ô cửa: gửi {"type":"input","seq":n,"dx":0,"dy":0,
        #                        "running":False,"x":..,"y":..}
        # ĐIỀU CẦN THẤY: một frame welcome MỚI với map đích
```

Dấu hiệu phân biệt nhanh:

| Hiện tượng | Kết luận |
|---|---|
| `srv` đổi sang toạ độ map mới, **không** có welcome | hook welcome không chạy / bị bỏ (bẫy identity §5.1) |
| Có welcome nhưng `pred` lệch mãi | client giữ map cũ (bundle cũ) hoặc lỗi bake (§5.4) |
| Không tele, đứng sát cửa | edge/cooldown/latch, hoặc trigger sai ô (§3) |
| Nghi process còn code cũ | welcome **không có** field `input_seq` ⇒ bản cũ |

---

## 8. Bảng lỗi thường gặp → nguyên nhân

| Triệu chứng | Nguyên nhân thật (đã gặp) |
|---|---|
| Vào map mới vẫn thấy **cỏ/hoa của map cũ** | lớp **resource** không được dọn (chữ ký không gồm map id) + bake cũ không bị destroy |
| Thấy map mới nhưng **đi xuyên tường / lệch ô** | client chưa nhận welcome (giữ collision map cũ) — lỗi hook welcome |
| **Nháy** qua chỗ này chỗ kia khi vào cửa | snapshot map mới bị áp lên map cũ trước khi welcome tới (§5.3) |
| Vừa ra khỏi cửa đã bị **hút vào lại** | timer re-arm 2.5s (đã bỏ) — giờ chỉ edge + cooldown |
| Đi cửa nào cũng vào **cùng một chỗ** | nhiều cụm trigger trỏ về 1 map (lỗi vẽ layer trong Tiled) |
| Tiled báo **tileset đỏ** | path tileset ghi theo project chứ không theo file map |
| Cửa không nổ dù đứng sát | ô trigger không đi được trong collision, hoặc client bị chặn sát mép (cần `GATE_MARGIN`) |
| Restart rồi vẫn như cũ | panel không tự pull code; hoặc file chưa upload (kiểm md5 remote vs local) |

---

## 9. Test đang khoá các luật này

- `tests/test_portal_gate.py` — edge-trigger, không bounce, cooldown, chỗ đến
  không nằm trên ô cổng, seed khi đến.
- `tests/test_web_portal_map_switch.py` — hook welcome (kể cả bẫy 2 session
  object + cách ly theo channel), welcome map đích có collision.
- `tests/test_map_loader.py` — luật collision theo tên layer.

---

## 10. VÀO GAME THẬT ĐỂ TEST (đa người chơi, không phải probe)

Probe (§7) kiểm tra **logic server**. Muốn thấy đúng như người chơi nhìn thấy —
multiplayer, sprite, va chạm bằng chuột/phím — thì phải **vào game thật**. Cách
đã dùng suốt session 16/09:

### 10.1 Chạy thử với nhiều người chơi thật

**Trick guest:** mỗi tài khoản guest là một "người chơi" độc lập — tôi có thể
đăng nhập nhiều guest cùng lúc và thấy player của nhau trên map:

1. Mở **nhiều tab/cửa sổ trình duyệt** (hoặc 1 tab thường + 1 tab ẩn danh).
2. Mỗi tab vào `https://web-production-19398.up.railway.app/`, bấm login guest
   (guest_id tự sinh, mỗi tab một id riêng ⇒ mỗi tab là 1 player).
3. Cả các tab join **cùng một channel** (cùng scenario) → thấy player của nhau,
   chat với nhau, đánh chung quái.
4. Một tab đi vào cửa monter ⇒ các tab còn lại phải thấy player đó **biến mất**
   khỏi map chợ (chuyển runtime) — đây là cách xác nhận `move_player_between_runtimes`
   hoạt động đúng về multiplayer, không chỉ về toạ độ.

Màu tên/avatar: mỗi player có 1 màu role cố định (mất lần đầu xuất hiện) —
dùng để phân biệt tab nào là player nào.

### 10.2 Test từng tính năng bằng lệnh trong game (chat)

Gõ trực tiếp vào ô chat của web client:

| Lệnh | Dùng để |
|---|---|
| `/khutraodoi in` / `out` | vào/ra khu trao đổi — test đường **lệnh** (khác đường **cửa**) |
| `/weather`, `/time` | xem thời tiết/giờ hiện tại của server |
| `/spawnmob <kind> [n]` | gọi quái ra cạnh mình để test đánh (zombie, ske, spider, slime, bat, rat) |
| `/give <item> [n]` | mint đồ (admin) — test craft/hotbar/nhặt mà không phải đi farm |
| `/setweather <key>` | đổi thời tiết ngay lập tức (mưa/tuyết/mây…) để test hiệu ứng |
| `/help` | danh sách đầy đủ |

### 10.3 Kịch bản test cổng (làm theo thứ tự)

1. **Đi vào cửa**: đẩy vào cửa monter ⇒ phải tele **ngay lập tức**, không đi
   xuyên qua, không phải đứng đúng tâm ô.
2. **Vừa đến**: đứng yên ngay chỗ đến ≥3s ⇒ **không** bị tele ngược.
3. **Giữ phím** hướng cửa sau khi đến ⇒ không bounce (edge + seed chặn).
4. **Ra bằng thảm**: bước lên thảm ⇒ về chợ, đứng **cạnh** cửa (không dính cổng).
5. **Ra rồi quay lại**: đi ra xa >2 ô, rồi đi vào cửa lại ⇒ tele lần 2 bình
   thường (cổng vẫn là cổng, không khoá vĩnh viễn).
6. **Đa người**: tab A đứng trong nhà, tab B ở chợ ⇒ B không thấy A; A bước lên
   thảm ⇒ cả hai cùng thấy A xuất hiện lại ở chợ trước cửa.
7. **Reload giữa chừng**: đứng trong nhà, F5 ⇒ vẫn ở trong nhà (server nhớ
   runtime), không bị trả về chợ.

### 10.4 Khi có lỗi: lấy dữ liệu đúng cách

- **F12 → Console**: copy **toàn bộ** log quanh lúc lỗi (có `[DESYNC] d=…`,
  `pred=… srv=…` là dữ liệu quý — ghi kèm thời điểm và thao tác vừa làm).
- **HUD debug góc trái trên** (`pred/srv/d/ack`): `d=0.00` là đồng bộ; `d` lớn
  kéo dài = lệch. Nhớ `ack` có tăng theo khi di chuyển không.
- **Log panel** (hosting bot): hiện `[WEB] frame … crashed`, traceback, portal
  fire log — copy **nguyên văn**, đừng tóm tắt.
- Chụp **cả** console + ảnh màn hình cùng khoảnh khắc (Lightshot/Win+Shift+S)
  — ảnh cho biết *nhìn thấy gì*, console cho biết *server nghĩ gì*; thiếu 1
  trong 2 là phải đoán.
