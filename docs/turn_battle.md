# Turn Battle — phân tích pokemon-battle-bot & engine battle/ của dự án

> Nguồn tham khảo: https://github.com/xbandrade/pokemon-battle-bot (MIT).
> Tài liệu này tổng kết repo đó, chỉ ra cái gì lấy / cái gì bỏ, và mô tả
> package `battle/` đã được thêm vào dự án (thuần logic, không Pokémon,
> không item, không discord.py).

## 1. Repo gốc làm gì

Bot Discord cho battle Pokémon 1v1 qua slash command `/battle`, `/random`, `/smart`.
Ảnh battle + các nút move hiển thị trong **một message được edit liên tục**
(không spam message — trùng với rule 24/25 của ta).

Cấu trúc repo gốc:

| File | Nội dung |
|---|---|
| `bot.py` | Slash commands, tạo `Pokemon` + `CustomView`, callbacks các nút move |
| `custom_view.py` | Discord `View`: nút HP, 4 nút move, Run Away; **toàn bộ flow turn nằm ở đây** |
| `bot_play.py` | AI chọn move: chấm điểm theo type effectiveness + trọng số |
| `pokemon.py` | `Pokemon` / `PokemonMove` dataclass (stats, IV/EV, PP...) |
| `utils/damage_calc.py` | Công thức damage Pokémon (Bulbapedia) |
| `utils/typechart.py` | Ma trận 18 hệ dưới dạng numpy array |

## 2. Điểm đáng lấy / điểm phải bỏ

### Lấy lại (đã đưa vào `battle/`)

- **Công thức damage dạng nhân tử**: `(((2*level/5 + 2) * power * atk/def) / 50 + 2) * multipliers`.
  Dễ chỉnh từng hệ số, tách bạch "base damage" và "modifier".
- **Ma trận tương khắc là DATA thuần** — thêm/sửa hệ không cần sửa logic.
- **Turn order theo speed**, move có `priority` để chen lượt.
- **Giới hạn số lượt dùng skill** (analog của PP) + nút mờ đi khi hết.
- **AI chấm điểm move** (`bot_play.check_moves`) — mẫu tham khảo khi làm AI sau này.
- **Edit 1 message thay vì spam** — khớp rule 24/25.

### Bỏ / viết lại (vi phạm rules của dự án ta)

- **Toàn bộ logic battle nằm trong Discord `View`** → phá rule 2, 3, 5
  (logic không được phụ thuộc discord.py; View chỉ là adapter mỏng).
- Hard-code 4 move, chỉ 1v1, không có state machine, không tách state/rules.
- **Không có test** — không kiểm chứng được công thức.
- Bỏ qua accuracy trong bot gốc (data có nhưng không roll) — ta có roll.
- Dùng numpy chỉ cho 1 ma trận → ta dùng dict thuần.
- **Concept Pokémon (STAB, ability, IV/EV) và item** → bỏ hết theo yêu cầu.

## 3. Package `battle/` đã thêm vào dự án

Thuần Python stdlib, **không import discord.py, không IO** (rule 2, 3).
Không được map game import — nó là module độc lập, sẵn sàng cắm vào sau.

```
battle/
  state.py    Unit, BattleEvent, BattleState (units, round, phase, winner, pending)
  skills.py   SkillDef, BattleConfig, registry skill mặc định, tính damage/heal
  rules.py    ElementChart — ma trận tương khắc dạng dict (thay numpy)
  actions.py  UseSkill(unit_id, skill_id), Flee(unit_id)
  engine.py   BattleEngine: submit() → run_round() → List[BattleEvent]
tests/test_battle.py  11 test (chạy: .venv\Scripts\python -m pytest tests/test_battle.py -q)
```

### Nguyên tắc thiết kế

1. **Deterministic**: mọi random đi qua `rng: random.Random` được inject →
   seed cố định = replay/test được (`BattleEngine(state, rng=random.Random(42))`).
2. **Data-driven**: thêm skill/hệ/chỉnh số = sửa data, **không sửa engine**.
3. **Events thay vì string render**: `run_round()` trả `List[BattleEvent]`
   (kind: `damage | miss | heal | ko | flee | round_end | ended | invalid`),
   tầng Discord sau chỉ format text/ảnh từ events → render không mutate state.
4. **Actions**: adapter Discord chỉ tạo `UseSkill`/`Flee` rồi `submit()`.

### Ví dụ dùng

```python
import random
from battle.engine import BattleEngine
from battle.actions import UseSkill
from battle.state import BattleState, Unit

state = BattleState("b1")
state.add_unit(Unit("u1", "Hero", "player", level=5, hp=100, max_hp=100,
                    mana=50, max_mana=50, atk=20, dfn=15, spd=12,
                    elements=["fire"], skill_uses={"slash": 10, "firebolt": 5}))
state.add_unit(Unit("u2", "Slime", "enemy", level=5, hp=80, max_hp=80,
                    atk=12, dfn=10, spd=8, elements=["nature"]))

engine = BattleEngine(state, rng=random.Random(42))
engine.submit("player", UseSkill("u1", "firebolt"))
engine.submit("enemy", UseSkill("u2", "strike"))
for ev in engine.run_round():
    print(ev.text)

# engine.state.phase == "ended" và engine.state.winner khi một bên hết unit
```

## 4. Cách tùy biến (điểm mấu chốt)

### Thêm/sửa skill — chỉ sửa `DEFAULT_SKILL_REGISTRY` trong `battle/skills.py`

```python
SkillDef(id="thunder", name="Thunder", element="thunder", power=55,
         accuracy=0.8, priority=0, mana_cost=15, max_uses=4,
         crit_bonus=0.05, effects={})
```

- `power == 0` → skill hỗ trợ; `effects` là dict tự do
  (`{"heal": 25}` đã có sẵn; sau này thêm `"buff_atk"`, `"poison"`... chỉ cần
  mở rộng `resolve_skill`/`_act` — một chỗ duy nhất).
- `max_uses=None` → không giới hạn (đây là "basic attack" vô hạn).
- Hết usable skill → engine tự dùng `config.fallback_skill_id` (mặc định `"strike"`).

### Thêm hệ tương khắc — chỉ sửa dict trong `battle/rules.py`

```python
ElementChart({"thunder": {"water": 2.0, "nature": 0.5}})
# pair không khai báo = 1.0; hệ lạ = neutral; multiplier 0 = miễn nhiễm
```

### Chỉnh cân bằng game — `BattleConfig` (không đụng engine)

`crit_mult`, `variance_min` (biến thiên damage), `same_element_bonus`
(bonus khi hệ skill trùng hệ unit — mặc định 1.0 = tắt), `flee_success`.

### Nạp từ JSON (khi cần)

Registry là dict thường → dễ viết loader kiểu `game/npc.py`:
`json.loads(...)` rồi build `SkillDef(**row)` / `ElementChart(data)`.
Engine không cần đổi gì.

## 5. Kế hoạch tích hợp Discord (khi làm — hiện CHƯA code)

Theo đúng rules của dự án:

1. **Adapter mỏng**: slash command (vd `/duel`) tạo `BattleState` + units
   từ stats của `Player` (hp/mana/level đã có sẵn trong `game/state.py`),
   lưu trong runtime **per-channel** (rule 15/16 — KHÔNG dùng lock toàn cục).
2. **1 message duy nhất cho cả battle** (ảnh + nút): nút skill sinh
   `custom_id` chứa `user_id` (rule 13), View `timeout=None`,
   callback = `submit()` + `run_round()` + edit message (rule 24/25).
3. **Restart recovery**: persist phase/winner/hp/mana/uses vào SQLite như
   bảng scenario hiện tại (rule 14, 19 — chỉ primitives, không binary).
4. **AI bot play**: tham khảo `bot_play.check_moves` — chấm điểm skill theo
   `ElementChart.effectiveness` rồi `random.choices(weights=...)`.
5. Nút hết lượt dùng → disabled trong View (như PP button của repo gốc).

## 6. Trạng thái hiện tại

- `battle/` hoàn chỉnh, 11 test pass; **chưa có lệnh Discord nào gọi nó**
  (tuân thủ rule 30 — không đụng vào MVP map/movement đang ổn định).
- Không có status effect, item, multi-unit party, hay AI — là extension points
  có chủ đích, làm khi cần (mỗi cái một file nhỏ, rule 26/28).

