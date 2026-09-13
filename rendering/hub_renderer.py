import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from game.map_loader import MapData
from game.npc import NpcMap
from game.state import GameState
from rendering.daynight import ingame_seconds
from rendering.renderer import MAP_UPLOAD_SCALE, finalize_for_upload

log = logging.getLogger("GAME")

# Displayed (post-finalize) hub height. Vertical stays fixed; the width is
# forced to match the screen image's displayed width by the UI layer.
HUB_DISPLAY_H = 66

# Per-player widget assembled from the AI-Ready Pixel2 runtime layers. The emblem
# container (32x34) sits at the left; the HP bar (border 106x10 + green fill
# 105x7) and Resource bar (border 106x10 + blue fill 105x7) are placed to the
# RIGHT of the emblem so they never overlap the diamond.
WIDGET_W, WIDGET_H = 140, 34
BAR_WIDTH = 105
EMBLEM_POS = (0, 0)
HP_BORDER_POS = (25, 5)
HP_FILL_POS = (26, 7)
RES_BORDER_POS = (25, 16)
RES_FILL_POS = (26, 18)
# Stamina: half-height bar under the mana bar.
SP_BORDER_POS = (25, 26)
SP_FILL_POS = (26, 27)

# Player avatar badge pasted ON TOP of the emblem diamond (its centre is
# opaque). The badge is a circle-masked avatar with a gold ring, sized to sit
# inside the 32x34 emblem container.
AVATAR_BADGE_SIZE = 28
AVATAR_BADGE_POS = (0, 1)  # badge+ring canvas is 32x32 inside the 32x34 emblem


@dataclass
class HubRenderResult:
    image: Image.Image
    frames: Optional[List[Image.Image]] = None
    filename: str = "hub.png"
    content_type: str = "image/png"
    duration_ms: int = 333


BG = (20, 28, 40)

# Coin counter (top-right corner): coin sprite + pixel number.
# The number is drawn as pure-PIL pixel digits (3x5 cell, scaled up) so it
# needs NO TTF font — the host container has none (see docs/deployment.md §4).
COIN_SIZE = 28
COIN_MARGIN = 16         # right-edge inset
COIN_Y = 2               # top-edge inset for the coin counter
COIN_GAP = 4
COIN_GOLD = (255, 214, 92)
COIN_SHADOW = (0, 0, 0)
COIN_EXTRA_PUSH = 2      # extra px the coin is pushed left per digit beyond 1
                        # (so tens/hundreds push the coin further from the edge)
DIGIT_SCALE = 4          # each pixel cell is DIGIT_SCALE x DIGIT_SCALE px
DIGIT_W = 3 * DIGIT_SCALE
DIGIT_H = 5 * DIGIT_SCALE
DIGIT_GAP = 2           # spacing between digits

# Weather widget (right edge, below the coin counter): loops at 3fps. Kept
# short enough that the digital clock can sit underneath it at the bottom-right.
WEATHER_SIZE = 70
WEATHER_Y = 30
WEATHER_MARGIN = 3

# Hotbar: 4 item slots drawn as the TOPMOST layer, overlaid on top of the
# HP/mana widgets, coin counter, weather and clock. Subtle, background-matched
# palette so it reads as a quiet frame rather than a loud UI element.
HOTBAR_SLOTS = 6
HOTBAR_SLOT = 84            # square size (internal px); even so downscale stays uniform
HOTBAR_GAP = 12             # even gap -> uniform parity across slots after 0.5 shrink
HOTBAR_SHIFT_X = 60       # shift the whole bar 35px further right (was 25)
HOTBAR_FILL = (26, 36, 52)       # a hair lighter than BG (20,28,40)
HOTBAR_BORDER = (44, 56, 76)     # quiet outline
HOTBAR_HI = (40, 52, 72)         # top/left bevel highlight
HOTBAR_SHADOW = (12, 18, 28)     # bottom/right bevel shadow
HOTBAR_TEXT = (235, 242, 255)    # item glyph colour (matches the clock)
HOTBAR_QTY = (255, 244, 179)     # count badge (gold, matches coins)
HOTBAR_KEYNUM = (86, 100, 122)   # faint slot number top-left
HOTBAR_CHIP = (12, 18, 28, 200)  # dark chip behind the qty badge (legibility)

# Item icons: real Twemoji PNGs bundled under assets/gui/items/<codepoint>.png
# (fetched once by scripts/fetch_item_icons.py; no runtime network — the
# container has no trusted egress). item_id -> codepoint; ids missing here
# (or with a missing file) fall back to the letter glyph.
ITEM_ICON_CODEPOINTS = {
    # items (game/items.py)
    "potion_hp": "1f48a",
    "potion_mp": "1f7e6",
    "key_stone": "1f511",
    "apple": "1f34e",
    "plank": "1f7eb",
    "stick": "1f962",
    "rotten_flesh": "1f969",
    "coin": "1fa99",
    # smelting chain (game/smelting.py)
    "iron_ore": "1f348",
    "coal": "26ab",
    "iron_ingot": "1f948",
    "charcoal": "1f311",
    "raw_meat": "1f356",
    "cooked_meat": "1f357",
    # blocks (game/blocks.py)
    "stone": "1faa8",
    "wood": "1fab5",
    "leaves": "1f33f",
    "torch": "1f56f",
    "floor": "1f7e4",
    "crafting_table": "1f6e0",
    "furnace": "1f525",
}
# Tool families: every tier shares the family emoji (no per-tier art in the
# pack; tiers differ by name/tooltip). Added via fetch_item_icons.py too.
from game.tools import TOOL_FAMILIES, tool_item_id as _tool_item_id  # noqa: E402

_TOOL_FAMILY_CP = {
    "axe": "1fa93",     # 🪓
    "pickaxe": "26cf",  # ⛏️
    "sword": "1f5e1",   # 🗡️
    "shovel": "1f944",  # 🥄
}
for _mat in ("dirt", "wood", "stone", "iron"):
    for _fam, _cp in _TOOL_FAMILY_CP.items():
        ITEM_ICON_CODEPOINTS[_tool_item_id(_fam, _mat)] = _cp
# Icon size inside a slot: ~60% of the square, even so the 0.5 upload shrink
# maps art pixels cleanly (72px source -> 50px internal -> 25 displayed).
ITEM_ICON_SIZE = 50

# All weather icon sets (key -> Vietnamese label). The key is also the subfolder
# name under assets/gui/weather/. The label is drawn left of the icon in gold.
WEATHER: Dict[str, str] = {
    "sun_clouds": "Nắng Dịu",
    "sunny": "Nắng Vàng",
    "cloudy": "Nhiều Mây",
    "heavy_clouds": "Trời Âm U",
    "rain": "Mây Thưa",
    "heavy_rain": "Mưa Tầm Tã",
    "snow": "Tuyết Rơi",
    "storm": "Giông Bão",
    "cold": "Giá Lạnh",
    "wind": "Gió Nhẹ",
}
# Pixel-style label height (in internal px, pre-upload shrink) — matches the coin.
WEATHER_NAME_SIZE = 26

# Digital clock (bottom-right corner, UNDER the weather widget / "hình thoi"):
# Saigon local time, drawn with the same pure-PIL pixel digits (no TTF needed).
# The colon blinks by toggling between GIF frames (see render()).
CLOCK_SCALE = 4          # each pixel cell is CLOCK_SCALE x CLOCK_SCALE px
CLOCK_GAP = 3            # spacing between characters
CLOCK_MARGIN = 3         # right/bottom inset
# The digits are raised this many internal px (~3.5 displayed) above the
# bottom margin so the clock's vertical centre lines up with the sun/moon
# icon's half-height (the icon itself stays anchored at CLOCK_MARGIN).
CLOCK_RAISE = 7
CLOCK_COLOR = (235, 242, 255)
CLOCK_SHADOW = (0, 0, 0)

# 3x5 bitmap font for digits 0-9 ('1' = lit pixel, top row first).
_DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    # Colon: two dots (drawn only on "on" frames for the blink effect).
    ":": ("000", "010", "000", "010", "000"),
}

# --- Day/night icon (sun/moon) pasted next to the digital clock ------------
# ONE pixel-art PNG per period of the in-game day ("buổi"): the sun rises in
# the morning, shines at noon, sets on the horizon in the evening, and the
# moon takes over at night. Static per period — no frame cycling.
# Source tiles are 17x17 pixel art; the icon slot is exactly 2 internal px
# per art pixel, i.e. 17 displayed px after the 0.5 upload shrink, so every
# art pixel maps 1:1 onto a displayed pixel — nothing blurs or breaks.
ICON_SIZE = 34   # 2 internal px per art pixel -> 17 displayed px (1:1 art)
ICON_GAP = 3

# Palette sampled from the asset tiles (see scripts/gen_daynight_icons.py).
SUN_COLOR = (251, 202, 54)      # gold core of the sun tiles
MOON_COLOR = (155, 173, 183)    # silver of the moon tiles
TWILIGHT_COLOR = (251, 202, 54)  # evening reuses the sun gold (horizon sun)


class HubRenderer:
    """Pure: renders a visual HUD (per-player HP/mana bars) as a Pillow image.

    No text is drawn for player widgets (the host container has no TTF font);
    textual status is supplied as a Discord embed by the UI layer. The coin
    counter draws its number with a built-in 3x5 pixel-digit renderer (pure
    PIL, no font file), matching the pixel-art style and the no-font constraint.

    Uses the AI-Ready Pixel2 runtime layers (see INTEGRATION_CHECKLIST.md):
    the green HP fill and blue resource fill are CLIPPED to the current/max
    ratio (never horizontally stretched), borders are fixed, and the whole
    widget is scaled by an INTEGER factor (pixel-art rule). The rendered width
    is forced to match the screen image's displayed width (both images pass
    through the same ``finalize_for_upload`` 0.5 shrink) so the hub bar always
    aligns with the map above it.
    """

    def __init__(self, assets_dir: Path):
        self.assets_dir = assets_dir
        rt = assets_dir.parent / "healthbar" / "runtime"
        self._emblem = Image.open(rt / "emblem_space.png").convert("RGBA")
        self._hp_border = Image.open(rt / "borders" / "hp_border.png").convert("RGBA")
        self._res_border = Image.open(rt / "borders" / "resource_border.png").convert("RGBA")
        self._hp_fill = Image.open(rt / "bars" / "hp_fill.png").convert("RGBA")
        self._res_fill = Image.open(rt / "bars" / "resource_fill.png").convert("RGBA")
        # Stamina reuses the HP border clipped to half height (clean pixel
        # frame without a third asset).
        self._stamina_border = self._hp_border.crop((0, 0, self._hp_border.width, 6))
        self._coin = Image.open(
            assets_dir.parent / "gui" / "coin1_frames" / "frame_00.png"
        ).convert("RGBA")
        # Load every weather icon set; strip the opaque black border frame so the
        # hub background shows through instead of a black box.
        self._weather: Dict[str, dict] = {}
        weather_root = assets_dir.parent / "gui" / "weather"
        for key, name in WEATHER.items():
            d = weather_root / key
            frames = []
            if d.is_dir():
                for p in sorted(d.glob("frame_*.png")):
                    im = Image.open(p).convert("RGBA")
                    px = im.load()
                    w, h = im.size
                    for y in range(h):
                        for x in range(w):
                            r, g, b, a = px[x, y]
                            if a > 128 and r < 40 and g < 40 and b < 40:
                                px[x, y] = (0, 0, 0, 0)
                    frames.append(im)
            if frames:
                self._weather[key] = {"name": name, "frames": frames}

        # Pixel-style weather label font (bundled TTF; the container has no system
        # fonts). Rendered gold to match the coin counter.
        font_path = assets_dir.parent / "fonts" / "tahoma.ttf"
        self._name_font = None
        if font_path.is_file():
            self._name_font = ImageFont.truetype(str(font_path), WEATHER_NAME_SIZE)
        # Hotbar slot fonts: item glyph (first letter of the name) + small
        # count/hotkey labels. Guarded: the hub still renders without a font.
        self._hotbar_font = None
        self._hotbar_small = None
        if font_path.is_file():
            self._hotbar_font = ImageFont.truetype(str(font_path), HOTBAR_SLOT // 2)
            self._hotbar_small = ImageFont.truetype(str(font_path), 18)

        # Bundled item icons. PRIMARY source: the generated Kaetram icon set
        # (assets/gui/icons/<item_id>.png — scripts/make_item_icons.py; pixel
        # art that needs no emoji font). Fallback: legacy Twemoji pack
        # (assets/gui/items/<codepoint>.png). A missing/corrupt file just
        # leaves that item on the letter-glyph fallback.
        self._item_icons: Dict[str, Image.Image] = {}
        for _root in (assets_dir.parent / "gui" / "icons", assets_dir.parent / "gui" / "items"):
            if not _root.is_dir():
                continue
            for p in sorted(_root.glob("*.png")):
                try:
                    self._item_icons[p.stem] = Image.open(p).convert("RGBA")
                except Exception as e:  # noqa: BLE001 — corrupt asset must not crash a render
                    log.warning("[HUB] item icon %s unreadable: %s", p.name, e)
        # (Both dirs use disjoint filenames — item_id vs codepoint — so no
        # priority shadowing is needed between them.)

        # Tool icons (shovel/pickaxe/axe/sword per material) from the user's
        # Items_24x24 pack, copied to assets/gui/tools/<material>_<family>.png.
        # Kaetram-generated icons already win (loaded above with priority).
        tool_root = assets_dir.parent / "gui" / "tools"
        if tool_root.is_dir():
            for p in sorted(tool_root.glob("*.png")):
                try:
                    self._item_icons[p.stem] = Image.open(p).convert("RGBA")
                except Exception as e:  # noqa: BLE001
                    log.warning("[HUB] tool icon %s unreadable: %s", p.name, e)

        # Day/night phase icons (sun / moon / star) loaded as PNG images and
        # pasted (with transparency) instead of drawn from bitmap patterns.
        # One pixel-art icon per period of the in-game day ("buổi"): morning
        # rising sun, noon sun, evening sunset, night moon. Static per period
        # — the icon switches when the in-game hour enters the next period.
        self._daynight_icons: Dict[str, Image.Image] = {}
        icon_root = assets_dir.parent / "gui" / "daynight"
        for phase, filename in [
            ("morning", "sun_morning.png"),
            ("day", "sun_noon.png"),
            ("evening", "sun_dusk.png"),
            ("night", "moon_night.png"),
        ]:
            p = icon_root / filename
            if p.is_file():
                self._daynight_icons[phase] = (
                    Image.open(p).convert("RGBA").resize((ICON_SIZE, ICON_SIZE), Image.NEAREST)
                )

    def _player_unit(self, hp_frac: float, mp_frac: float,
                     avatar: Optional[Image.Image] = None,
                     sp_frac: float = 1.0) -> Image.Image:
        u = Image.new("RGBA", (WIDGET_W, WIDGET_H))
        hpw = max(0, min(BAR_WIDTH, int(round(BAR_WIDTH * hp_frac))))
        mpw = max(0, min(BAR_WIDTH, int(round(BAR_WIDTH * mp_frac))))
        if hpw > 0:
            crop = self._hp_fill.crop((0, 0, hpw, 7))
            u.paste(crop, HP_FILL_POS, crop)
        if mpw > 0:
            crop = self._res_fill.crop((0, 0, mpw, 7))
            u.paste(crop, RES_FILL_POS, crop)
        u.paste(self._hp_border, HP_BORDER_POS, self._hp_border)
        u.paste(self._res_border, RES_BORDER_POS, self._res_border)
        # STAMINA bar: green, HALF the height of the HP/mana bars, tucked
        # directly under the mana bar (user request "dày bằng 1 nữa 2 thanh").
        spw = max(0, min(BAR_WIDTH, int(round(BAR_WIDTH * sp_frac))))
        if spw > 0:
            crop = self._hp_fill.crop((0, 0, spw, 7))
            # Recolor the green HP fill to a fresh stamina green + shrink to
            # 3px tall (half-ish of the 7px bars, pixel-art friendly).
            green = crop.copy()
            px = green.load()
            for yy in range(green.height):
                for xx in range(green.width):
                    r, g, b, a = px[xx, yy]
                    if a:
                        px[xx, yy] = (int(r * 0.45), min(255, int(g * 1.05)), int(b * 0.55), a)
            thin = green.crop((0, 0, spw, 4))
            u.paste(thin, SP_FILL_POS, thin)
        u.paste(self._stamina_border, SP_BORDER_POS, self._stamina_border)
        # Emblem drawn LAST so the bars tuck underneath it.
        u.paste(self._emblem, EMBLEM_POS, self._emblem)
        # The player's chosen avatar (if any) sits framed on the emblem.
        if avatar is not None:
            badge = self._emblem_avatar_badge(avatar)
            u.paste(badge, AVATAR_BADGE_POS, badge)
        return u

    @staticmethod
    def _emblem_avatar_badge(avatar: Image.Image) -> Image.Image:
        """Circle-masked avatar with a gold ring, sized for the emblem slot."""
        s = AVATAR_BADGE_SIZE
        badge = avatar.convert("RGBA").resize((s, s), Image.NEAREST)
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, s - 1, s - 1), fill=255)
        badge.putalpha(mask)
        ring = Image.new("RGBA", (s + 4, s + 4), (0, 0, 0, 0))
        d = ImageDraw.Draw(ring)
        d.ellipse((0, 0, s + 3, s + 3), fill=COIN_GOLD + (255,))
        # Punch the badge hole (ImageDraw REPLACES pixels on RGBA, so this
        # really cuts the donut).
        d.ellipse((2, 2, s + 1, s + 1), fill=(0, 0, 0, 0))
        out = Image.new("RGBA", (s + 4, s + 4), (0, 0, 0, 0))
        out.paste(badge, (2, 2), badge)
        out.alpha_composite(ring)
        return out

    def render(
        self,
        rt,
        map_data: MapData,
        npc_map: NpcMap,
        focused_user_id: Optional[int] = None,
        target_internal_w: Optional[int] = None,
        hotbar: Optional[Dict[int, tuple]] = None,
        avatars: Optional[Dict[int, Image.Image]] = None,
    ) -> HubRenderResult:
        if target_internal_w and target_internal_w > 0:
            internal_w = target_internal_w
        else:
            internal_w = 840
        internal_h = max(1, int(HUB_DISPLAY_H / MAP_UPLOAD_SCALE))

        base = Image.new("RGB", (internal_w, internal_h), BG)

        players: List = rt.state.get_visible_players()
        if players:
            n = min(len(players), 8)
            gap = max(2, internal_h // 22)
            # Integer scale factor (pixel-art rule): capped by height, then by fit.
            max_k_h = internal_h // WIDGET_H
            k = min(max_k_h, (internal_w - (n - 1) * gap) // (WIDGET_W * n))
            k = max(1, k)
            unit_w, unit_h = WIDGET_W * k, WIDGET_H * k

            for i, p in enumerate(players[:n]):
                x = i * (unit_w + gap)
                y = (internal_h - unit_h) // 2
                hp_frac = p.hp / p.max_hp if p.max_hp else 0
                mp_frac = p.mana / p.max_mana if p.max_mana else 0
                sp_frac = (
                    p.stamina / p.max_stamina
                    if getattr(p, "max_stamina", 0) else 1.0
                )
                avatar = avatars.get(p.user_id) if avatars else None
                unit = self._player_unit(
                    hp_frac, mp_frac, avatar, sp_frac
                ).resize((unit_w, unit_h), Image.NEAREST)
                base.paste(unit, (x, y), unit)

            self._draw_coins(base, rt, focused_user_id)

        # Pick the active weather (falls back to the first available one).
        key = getattr(rt, "weather_key", None) or "sun_clouds"
        weather = self._weather.get(key) or next(iter(self._weather.values()), None)

        # One base frame per weather icon (or a single static frame when there is
        # no weather asset). The digital clock is then stamped on every frame.
        if weather is not None:
            self._draw_weather_name(base, weather["name"], internal_w, internal_h)
            weather_frames = weather["frames"]
        else:
            weather_frames = [None]

        base_frames = []
        for wf in weather_frames:
            f = base.copy()
            if wf is not None:
                self._draw_weather(f, wf, internal_w, internal_h)
            base_frames.append(f)

        # Interleave colon-on / colon-off so the colon blinks while the weather
        # (if any) keeps animating. The hub is always a looping GIF so the clock
        # colon animates even with no weather.
        phase = self._daynight_phase()

        frames = []
        fi = 0
        for f in base_frames:
            f_on = f.copy()
            self._draw_clock(f_on, colon_on=True, phase=phase)
            self._draw_hotbar(f_on, internal_w, internal_h, hotbar)   # topmost layer
            fi += 1
            f_off = f.copy()
            self._draw_clock(f_off, colon_on=False, phase=phase)
            self._draw_hotbar(f_off, internal_w, internal_h, hotbar)  # topmost layer
            fi += 1
            frames.append(f_on)
            frames.append(f_off)
        return HubRenderResult(
            image=frames[0],
            frames=frames,
            filename="hub.gif",
            content_type="image/gif",
            duration_ms=400,
        )

    def _draw_hotbar(
        self,
        img: Image.Image,
        internal_w: int,
        internal_h: int,
        hotbar: Optional[Dict[int, tuple]] = None,
    ) -> None:
        # Topmost layer: centred on the hub image, overlaid on everything below.
        # ``hotbar`` maps slot index -> (item_id, qty) for bound slots.
        slot = HOTBAR_SLOT
        total_w = HOTBAR_SLOTS * slot + (HOTBAR_SLOTS - 1) * HOTBAR_GAP
        x0 = int((internal_w - total_w) / 2) + HOTBAR_SHIFT_X
        y0 = (internal_h - slot) // 2
        # Paint a uniform BG behind the whole group (gaps included) so the slots
        # never reveal the HP/mana widget (or anything else) showing through the
        # gaps -> every slot reads identically. Still an overlay (drawn last).
        draw = ImageDraw.Draw(img)
        draw.rectangle([x0, y0, x0 + total_w - 1, y0 + slot - 1], fill=BG)
        for s in range(HOTBAR_SLOTS):
            x = x0 + s * (slot + HOTBAR_GAP)
            self._draw_slot(img, x, y0, slot)
        if not hotbar:
            return
        for s, content in hotbar.items():
            if not (0 <= s < HOTBAR_SLOTS) or not content:
                continue
            item_id, qty = content
            x = x0 + s * (slot + HOTBAR_GAP)
            self._draw_slot_item(img, x, y0, slot, s, item_id, qty)

    def _draw_slot_item(self, img: Image.Image, x: int, y: int, slot: int,
                        s: int, item_id: str, qty: int) -> None:
        """Item content inside one hotbar slot: the REAL item icon (bundled
        Twemoji PNG, centred) with a qty chip bottom-right — dark rounded chip
        + gold digits so the count stays legible on any fill. Items without a
        bundled icon fall back to the name's initial letter glyph."""
        from game.items import get_item

        item = get_item(item_id)
        draw = ImageDraw.Draw(img)
        icon = self._item_icons.get(item_id)
        if icon is not None:
            art = icon.resize((ITEM_ICON_SIZE, ITEM_ICON_SIZE), Image.NEAREST)
            img.paste(art, (x + (slot - ITEM_ICON_SIZE) // 2,
                            y + (slot - ITEM_ICON_SIZE) // 2), art)
        elif self._hotbar_font is not None:
            letter = (item.name if item else item_id)[:1].upper()
            bbox = draw.textbbox((0, 0), letter, font=self._hotbar_font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text(
                (x + (slot - tw) // 2 - bbox[0], y + (slot - th) // 2 - bbox[1]),
                letter, font=self._hotbar_font, fill=HOTBAR_TEXT,
            )
        if self._hotbar_small is None:
            return
        # Faint hotkey number top-left (mirrors the D-pad button order).
        key = str(s + 1)
        draw.text((x + 5, y + 4), key, font=self._hotbar_small, fill=HOTBAR_KEYNUM)
        if qty <= 0:
            return
        # Qty chip: dark pill + gold digits — high contrast on every ground.
        t = f"x{qty}"
        bbox = draw.textbbox((0, 0), t, font=self._hotbar_small)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = 5, 3
        cw, ch = tw + pad_x * 2, th + pad_y * 2
        cx1 = x + slot - 3
        cy1 = y + slot - 3
        draw.rounded_rectangle(
            [cx1 - cw, cy1 - ch, cx1, cy1], radius=ch // 2, fill=HOTBAR_CHIP,
        )
        draw.text(
            (cx1 - cw + pad_x - bbox[0], cy1 - ch + pad_y - bbox[1]),
            t, font=self._hotbar_small, fill=HOTBAR_QTY,
        )

    def _draw_slot(self, img: Image.Image, x: int, y: int, slot: int) -> None:
        draw = ImageDraw.Draw(img)
        # Quiet fill, a touch lighter than the background.
        draw.rectangle([x, y, x + slot - 1, y + slot - 1], fill=HOTBAR_FILL)
        # Bevel: highlight top/left, shadow bottom/right for a soft 3D edge.
        draw.rectangle([x, y, x + slot - 1, y + 1], fill=HOTBAR_HI)
        draw.rectangle([x, y, x + 1, y + slot - 1], fill=HOTBAR_HI)
        draw.rectangle([x, y + slot - 2, x + slot - 1, y + slot - 1], fill=HOTBAR_SHADOW)
        draw.rectangle([x + slot - 2, y, x + slot - 1, y + slot - 1], fill=HOTBAR_SHADOW)
        # Outer outline (2px so it survives the 0.5 upload shrink and stays uniform).
        draw.rectangle([x, y, x + slot - 1, y + slot - 1], outline=HOTBAR_BORDER, width=2)
        # Faint inner frame so the empty slot still reads as a slot.
        inset = max(3, slot // 8)
        draw.rectangle(
            [x + inset, y + inset, x + slot - 1 - inset, y + slot - 1 - inset],
            outline=HOTBAR_BORDER,
            width=2,
        )

    def _draw_coins(self, img: Image.Image, rt, focused_user_id: Optional[int]) -> None:
        player = rt.state.get_player(focused_user_id) if focused_user_id else None
        if player is None:
            visible = rt.state.get_visible_players()
            player = visible[0] if visible else None
        coins = player.coins if player else 0

        text = str(coins)
        draw = ImageDraw.Draw(img)

        num_w = len(text) * DIGIT_W + max(0, len(text) - 1) * DIGIT_GAP
        group_w = COIN_SIZE + COIN_GAP + num_w
        # Right-anchor the group; as the number grows the coin is pushed left.
        # COIN_EXTRA_PUSH adds an explicit per-digit nudge for tens/hundreds.
        push = max(0, len(text) - 1) * COIN_EXTRA_PUSH
        x = img.width - COIN_MARGIN - group_w - push
        y = COIN_Y
        coin = self._coin.resize((COIN_SIZE, COIN_SIZE), Image.NEAREST)
        img.paste(coin, (x, y), coin)

        dx = x + COIN_SIZE + COIN_GAP
        dy = y + (COIN_SIZE - DIGIT_H) // 2
        for ch in text:
            self._draw_digit(draw, dx, dy, ch)
            dx += DIGIT_W + DIGIT_GAP

    def _draw_weather(self, img: Image.Image, wframe: Image.Image, internal_w: int, internal_h: int) -> None:
        w = WEATHER_SIZE
        x = internal_w - WEATHER_MARGIN - w
        y = WEATHER_Y
        icon = wframe.resize((w, w), Image.NEAREST)
        img.paste(icon, (x, y), icon)

    def _draw_weather_name(self, img: Image.Image, name: str, internal_w: int, internal_h: int) -> None:
        if self._name_font is None:
            return
        draw = ImageDraw.Draw(img)
        icon_x = internal_w - WEATHER_MARGIN - WEATHER_SIZE
        icon_y = WEATHER_Y
        # Right-aligned so the text hugs the icon (<=3px gap), vertically centred.
        bbox = draw.textbbox((0, 0), name, font=self._name_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = icon_x - 3 - tw
        if x < WEATHER_MARGIN:
            x = WEATHER_MARGIN
        y = icon_y + (WEATHER_SIZE - th) // 2 - bbox[1]
        draw.text((x, y), name, font=self._name_font, fill=(255, 255, 255))

    @staticmethod
    def _draw_bitmap(draw, x: int, y: int, pattern, scale: int, color, shadow) -> None:
        """Draw any 5×N bitmap pattern at scale. Accepts both '1' and '#' as
        lit pixels so it works for digit glyphs and icon patterns alike."""
        for row_i, row in enumerate(pattern):
            for col_i, bit in enumerate(row):
                if bit not in ("1", "#"):
                    continue
                px = x + col_i * scale
                py = y + row_i * scale
                draw.rectangle(
                    [px + 1, py + 1, px + scale, py + scale],
                    fill=shadow,
                )
                draw.rectangle(
                    [px, py, px + scale - 1, py + scale - 1],
                    fill=color,
                )

    @staticmethod
    def _draw_pixel_char(draw, x: int, y: int, ch: str, scale: int, color, shadow) -> None:
        glyph = _DIGITS.get(ch, _DIGITS["0"])
        HubRenderer._draw_bitmap(draw, x, y, glyph, scale, color, shadow)

    @staticmethod
    def _draw_digit(draw, x: int, y: int, ch: str) -> None:
        HubRenderer._draw_pixel_char(draw, x, y, ch, DIGIT_SCALE, COIN_GOLD, COIN_SHADOW)

    @staticmethod
    def _daynight_phase() -> str:
        """Return the current period of the in-game day ("buổi").

        Each period owns ONE fixed icon: 05-10 = ``morning`` (rising sun),
        11-16 = ``day`` (noon sun), 17-18 = ``evening`` (sunset on the
        horizon), otherwise ``night`` (moon). The HUD clock shows this same
        in-game time, so the icon always matches the displayed hour."""
        hour = ingame_seconds() // 3600
        if 5 <= hour <= 10:
            return "morning"
        if 11 <= hour <= 16:
            return "day"
        if 17 <= hour <= 18:
            return "evening"
        return "night"

    def _draw_icon(self, img: Image.Image, x: int, y: int, phase: str) -> None:
        """Paste the period icon (sun/moon) at *x*,*y*.

        ``phase`` is 'morning'/'day'/'evening'/'night' — one fixed icon per
        period, no animation frames."""
        icon = self._daynight_icons.get(phase)
        if icon is not None:
            img.paste(icon, (x, y), icon)

    def _draw_clock(
        self, img: Image.Image, colon_on: bool,
        phase: Optional[str] = None,
    ) -> None:
        """Draw the in-game digital clock at the bottom-right.

        When ``phase`` is provided, the period's sun/moon icon is drawn
        immediately to the LEFT of the clock text (e.g. "☀ 12:00"),
        bottom-aligned with the digits. The colon is only painted when
        ``colon_on`` is True, so the caller toggles it across GIF frames to
        make it blink."""
        sec = ingame_seconds()
        hour = sec // 3600
        minute = (sec % 3600) // 60
        text = f"{hour:02d}:{minute:02d}"
        draw = ImageDraw.Draw(img)
        dw = 3 * CLOCK_SCALE
        dh = 5 * CLOCK_SCALE
        total = sum((dw if c != ":" else dw) + CLOCK_GAP for c in text) - CLOCK_GAP
        x = img.width - CLOCK_MARGIN - total
        y = img.height - CLOCK_MARGIN - dh - CLOCK_RAISE

        if phase is not None:
            icon_x = x - ICON_GAP - ICON_SIZE
            # The icon keeps its own anchor at CLOCK_MARGIN (it does NOT
            # follow the raised digits).
            base_y = img.height - CLOCK_MARGIN - dh
            icon_y = base_y + dh - ICON_SIZE
            self._draw_icon(img, icon_x, icon_y, phase)

        for ch in text:
            if ch == ":":
                if colon_on:
                    self._draw_pixel_char(draw, x, y, ch, CLOCK_SCALE, CLOCK_COLOR, CLOCK_SHADOW)
                x += dw + CLOCK_GAP
            else:
                self._draw_pixel_char(draw, x, y, ch, CLOCK_SCALE, CLOCK_COLOR, CLOCK_SHADOW)
                x += dw + CLOCK_GAP


def encode_hub(result: HubRenderResult) -> bytes:
    """Serialize a HubRenderResult into upload bytes (PNG, or a looping GIF)."""
    if result.frames and len(result.frames) > 1:
        small = []
        for f in result.frames:
            w, h = f.size
            nw, nh = max(1, int(w * MAP_UPLOAD_SCALE)), max(1, int(h * MAP_UPLOAD_SCALE))
            small.append(f.resize((nw, nh), Image.NEAREST))
        # One shared palette across every frame -> no colour flicker between frames.
        base_p = small[0].convert("P", palette=Image.ADAPTIVE, colors=256)
        gif = [s.quantize(palette=base_p) for s in small]
        buf = io.BytesIO()
        gif[0].save(
            buf,
            "GIF",
            save_all=True,
            append_images=gif[1:],
            loop=0,
            duration=result.duration_ms,
            optimize=True,
        )
        return buf.getvalue()
    img = finalize_for_upload(result.image)
    buf = io.BytesIO()
    # optimize=False: hub bar is a tiny palette PNG; optimize pass is ~5ms
    # of CPU per refresh for negligible size gain.
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()
