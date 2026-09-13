# Quy trình cắt sprite sheet thành frame (điều chỉnh cho project này)

> Nguồn: `CATTOWN_SPRITE_ANIMATION_GUIDE.md` (bản chung của ChatGPT, thư mục
> `D:\UserData\Downloads\New folder\`), đã điều chỉnh cho **Dự án mini build bot
> discord MMO event** — game tile 32px, client web = Phaser, render Discord = PIL,
> sprite nguồn = Kaetram/BrowserQuest trong `kaetram_extract/01_mobs_sprites/`
> (bản clone gốc còn ở `_kaetram_src/`, kèm metadata `client/data/sprites.json`).

## 0. Khi nào dùng doc này

Mỗi khi thêm/đổi một mob sprite (zombie, skeleton, rat, boss...) từ
`kaetram_extract/01_mobs_sprites/` (hoặc sheet mới bất kỳ), doc này là quy trình
chuẩn để:

1. Cắt sheet thành frame PNG riêng từng animation.
2. Đăng ký texture + animation cho **web client** (Phaser, `web_client/src/game.ts`).
3. Đảm bảo **Discord renderer** (PIL) dùng đúng cùng một frame reference.

Kết quả cuối cùng luôn là: **một PNG sheet duy nhất nằm ở `assets/mobs/<id>.png`**
(như hiện tại `assets/mobs/zombie.png` 160×288), client tự crop theo grid —
KHÔNG băm sheet thành hàng loạt PNG frame nhỏ gửi qua wire.

## 1. Quy tắc cốt lõi (giữ nguyên từ guide gốc)

- **Thứ tự sequence trong mỗi hướng: `ATTACK → WALK → IDLE`** (hàng trên xuống
  dưới của sheet). Đây là tín hiệu chính; tên file gốc Kaetram KHÔNG đáng tin.
- **KHÔNG mặc định số frame.** Số frame là tùy asset (zombie: 6/3/2 mỗi hướng;
  scorpion: 6/4/4; dark skeleton: 3/3/3). Phải đếm/kiểm tra từng sheet.
- **KHÔNG trim/crop sát thân sprite.** Mỗi frame giữ nguyên ô grid gốc
  (canvas + pivot + transparency) — nếu không animation sẽ rung/nhảy/lệch.
- **KHÔNG bịa hướng.** Asset có 3 hướng (side/front/back) thì chỉ dùng 3;
  hướng còn lại (nếu cần) dùng `flipX`/`flipY` ở engine, không vẽ thêm.
- Khi visual và thứ tự mâu thuẫn: ưu tiên **(1) cấu trúc sequence → (2) chuyển
  động trực quan → (3) tên file**. Nếu mơ hồ, giữ nguyên sequence và đánh dấu
  "cần xác minh" — không tự đổi.

## 2. FPS + loop chuẩn project

| Animation | FPS | Loop | Ghi chú |
|---|---|---|---|
| `idle` | **2** | ON | thở/nhúc nhích nhẹ |
| `walk` | **6** | ON | vòng chân lặp |
| `attack` | **6** | **OFF** | chạy đúng 1 lần rồi về idle/walk |

- FPS là **cố định toàn project**; chỉ số frame là tùy asset.
- Web client chuyển FPS thành ms/frame: idle 500ms, walk ~167ms, attack ~167ms.
- Attack "1 lần" khớp cơ chế sẵn có: `setAnimation('atk', ..., 1)` kiểu Kaetram —
  server gửi anim `atk`, client chạy 1 nhịp rồi tự về idle/walk theo snapshot kế.

## 3. Quy trình 11 bước cho một sheet mới

```text
STEP 1  Mở sheet trong kaetram_extract/01_mobs_sprites/<mob>.png, xác định
        frame size + grid (Kaetram/BQ thường là 32×32; sheet zombie hiện tại
        160×288 = 5 cột × 9 hàng).
STEP 2  Xác định các CỤM hướng trong sheet: mỗi hướng là một khối cột/hàng
        riêng (side / front / back). KHÔNG coi cả sheet là một animation.
STEP 3  Trong từng hướng, tách theo thứ tự ATTACK → WALK → IDLE, đếm frame
        từng cụm (KHÔNG giả định).
STEP 4  Kiểm chứng bằng chuyển động: attack = vung/lao/đập; walk = chân lặp;
        idle = gần đứng, chỉ thở. Mơ hồ thì đánh dấu, không bịa.
STEP 5  Crop frame theo grid: x = col*W, y = row*H, giữ nguyên ô + RGBA.
STEP 6  Đặt tên frame: <animation>_<direction>_<2-chữ-số>.png
        (attack_side_01.png, walk_front_02.png, idle_back_01.png...) —
        đánh số từ 01, filesystem sort đúng thứ tự.
STEP 7  Đưa vào cấu trúc thư mục: <mob>/attack|walk|idle/<side|front|back>/.
STEP 8  Copy sheet GỐC (nguyên vẹn) vào assets/mobs/<mob>.png — đây là file
        duy nhất server nắm giữ và serve qua asset_request.
STEP 9  Đăng ký ở web client (game.ts): map anim → (row, frameCount):
        ví dụ zombie = { atk: row 0, 5f } / { walk: row 1, 4f } /
        { idle: row 2, 2f }, 32px, crop bằng setCrop, không stretch.
STEP 10 Áp FPS/loop bảng mục 2; attack OFF loop, về idle sau khi xong.
STEP 11 Thêm test nhỏ (nếu có registry mới) + chạy pytest -q tests rồi
        deploy: upload assets lên panel + push web client.
```

## 4. Ví dụ đã kiểm chứng: zombie (assets/mobs/zombie.png)

Sheet Kaetram 160×288 = **5 cột × 9 hàng của ô 32×32** (160/32=5, 288/32=9).

> ⚠️ Cần xác minh lại bằng mắt trước khi sửa code: ghi chú cũ trong
> `web_client/src/game.ts` từng chép "mob rows: 0 = atk 5f, 1 = walk 4f,
> 2 = idle 2f" — nhưng phân loại thủ công trong
> `zombie_frames_final` (ChatGPT cắt) cho ra **Attack 6 / Walk 3 / Idle 2**
> mỗi hướng, tức mỗi hướng chiếm 11 ô = 5.5 hàng → sheet 9 hàng này KHÔNG
> chứa đủ 3 hướng × 11; khả năng cao đây là sheet chỉ có MỘT hướng với các
> hàng rời rạc, hoặc grid thực tế khác 32×32. **Quy tắc: đọc lại STEP 1–4
> trên chính file PNG trước khi đổi code.**

## 5. Sai lầm cần tránh (đã gặp thật)

- Coi cả sheet là một dãy frame duy nhất → animation nhảy loạn các hướng.
- Mặc định "mọi attack 6 frame" → tràn sang hàng walk (idle nhảy người).
- Trim frame theo bounding box → sprite rung từng pixel khi phát.
- Stretch sheet nguyên vẹn vào ô 32px (mẹo cũ) → zombie méo, không phải
  animation. Phải `setCrop` đúng ô rồi mới `setDisplaySize`.
- Tự tạo hướng mới bằng biến dạng sprite (mirror dọc...) — chỉ được flipX.

## 6. Prompt chuẩn khi nhờ agent cắt sheet mới

```text
SPRITE ANIMATION RULES — project này (tile 32px, Phaser web + PIL Discord)

1. Determine the sprite's fixed grid/frame size first (usually 32×32).
2. Crop every frame using the original grid cell.
3. NEVER trim/crop individual frames to the visible sprite bounds.
4. Preserve each frame's original canvas size, transparency and alignment.
5. If multiple directions/facings exist, identify each direction's block
   BEFORE classifying animations.
6. Within each direction, sequence order is: ATTACK → WALK → IDLE.
7. Do NOT assume fixed frame counts; count them per sheet.
8. Verify with visual motion (attack=big action, walk=leg cycle, idle=breathe).
9. Ambiguous → keep the sheet order and flag for manual review; do not invent.
10. Output: the ORIGINAL full sheet copied to assets/mobs/<id>.png plus a
    row/col/frame-count map (this doc, section 4 format). The web client
    crops at runtime; do not ship many small frame PNGs.
11. FPS: idle 2 / walk 6 / attack 6. Loop: idle ON, walk ON, attack OFF.
```
