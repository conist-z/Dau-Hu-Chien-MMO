"""Day/night cycle ported from the Godot `daynightcycle2d` addon.

The original Godot asset drives a ``CanvasModulate`` whose ``color`` is sampled
from a 24h gradient (night -> dawn -> day -> dusk -> night). A CanvasModulate
multiplies the whole canvas by that colour, so the gradient colours already
encode both tint AND brightness: night is a dark blue ``(0x27,0x26,0x4c)``,
midday a warm near-white ``(0xff,0xf1,0xd0)``.

We replicate the exact same gradient stops and apply the resulting colour as a
per-channel multiply on the rendered map image, using the real Saigon
(Asia/Ho_Chi_Minh, UTC+7, no DST) local time so the in-game lighting matches the
player's wall clock.
"""

import os
import time
from datetime import datetime, timezone, timedelta
from typing import Iterable, List, Tuple

from PIL import Image, ImageDraw, ImageFilter

# Vietnam observes UTC+7 all year (no daylight saving), so a fixed offset is
# both correct and avoids any tzdata/zoneinfo dependency on the host container.
SAIGON_UTC_OFFSET_HOURS = 7

# Gradient stops (RGB 0-255), copied verbatim from DayNightCanvasModulate.gd.
_C_NIGHT = (0x27, 0x26, 0x4C)
_C_DAWN = (0x49, 0x46, 0x88)
_C_DAY = (0xFF, 0xF1, 0xD0)
_C_DUSK = (0x85, 0x46, 0x46)

SECONDS_PER_DAY = 86400
_DAWN_S = 6 * 3600
_DUSK_S = 21 * 3600

# (fraction_of_day, (r, g, b)) -- mirrors the Gradient.add_point() calls in
# DayNightCanvasModulate._ready().
_GRADIENT: List[Tuple[float, Tuple[int, int, int]]] = [
    (0.0, _C_NIGHT),
    ((_DAWN_S - 3600) / SECONDS_PER_DAY, _C_NIGHT),
    ((_DAWN_S + 3600) / SECONDS_PER_DAY, _C_DAWN),
    (0.5, _C_DAY),
    ((_DUSK_S - 3600) / SECONDS_PER_DAY, _C_DAY),
    ((_DUSK_S + 3600) / SECONDS_PER_DAY, _C_DUSK),
    (0.99999, _C_NIGHT),
]


def saigon_now() -> datetime:
    """Current time in Ho Chi Minh City (Saigon), UTC+7."""
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=SAIGON_UTC_OFFSET_HOURS))
    )


def saigon_time_of_day() -> int:
    """Seconds since local midnight in Saigon (0..86399)."""
    n = saigon_now()
    return n.hour * 3600 + n.minute * 60 + n.second


# --- In-game day/night clock -------------------------------------------------
# The MAP lighting runs on its OWN accelerated clock, independent of real-world
# daylight (real darkness outside does NOT make the map dark). Default cycle = 30
# real minutes for one full in-game day: the clock starts at 00:00 (midnight) and
# reaches 12:00 (noon, peak brightness) at the 15-minute mark. Override with the
# DAY_LENGTH_SECONDS env var (real seconds per in-game day). The HUD digital
# clock shows this same accelerated in-game clock (hours/minutes since the
# cycle started), not real-world time: see ``ingame_seconds``.
GAME_DAY_LENGTH_REAL_SECONDS = float(os.environ.get("DAY_LENGTH_SECONDS", "1800"))

_GAME_EPOCH = time.time()
_GAME_BASE_SEC = 0  # cycle starts at 00:00 -> 12:00 (noon) at the 15-min mark

# Manual time override (web chat /time set night|day|...): a signed offset in
# ACCELERATED in-game seconds applied on top of the natural clock. 0 = follow
# the natural cycle. /time set normal resets it.
_GAME_OFFSET_SEC = 0.0


def ingame_seconds() -> int:
    """Current in-game second-of-day (0..86399), accelerated vs real time."""
    elapsed = time.time() - _GAME_EPOCH
    mult = SECONDS_PER_DAY / GAME_DAY_LENGTH_REAL_SECONDS
    return int((_GAME_BASE_SEC + elapsed * mult + _GAME_OFFSET_SEC) % SECONDS_PER_DAY)


def set_ingame_time(second_of_day: int | None) -> int:
    """Pin the in-game clock to ``second_of_day`` (0..86399) by adjusting the
    manual offset; the clock keeps advancing normally from there. ``None``
    clears the override (back to the natural cycle). Returns the resulting
    second-of-day."""
    global _GAME_OFFSET_SEC
    if second_of_day is None:
        _GAME_OFFSET_SEC = 0.0
        return ingame_seconds()
    elapsed = time.time() - _GAME_EPOCH
    mult = SECONDS_PER_DAY / GAME_DAY_LENGTH_REAL_SECONDS
    natural = _GAME_BASE_SEC + elapsed * mult
    _GAME_OFFSET_SEC = (float(second_of_day) - natural) % SECONDS_PER_DAY
    return ingame_seconds()


def tint_factor(sec: int) -> Tuple[float, float, float]:
    """Multiply factors (0..1) for the given second-of-day, interpolated from
    the day/night gradient."""
    frac = (sec % SECONDS_PER_DAY) / SECONDS_PER_DAY
    for i in range(len(_GRADIENT) - 1):
        f0, c0 = _GRADIENT[i]
        f1, c1 = _GRADIENT[i + 1]
        if f0 <= frac <= f1:
            t = 0.0 if f1 == f0 else (frac - f0) / (f1 - f0)
            return (
                (c0[0] + (c1[0] - c0[0]) * t) / 255.0,
                (c0[1] + (c1[1] - c0[1]) * t) / 255.0,
                (c0[2] + (c1[2] - c0[2]) * t) / 255.0,
            )
    return (c / 255.0 for c in _C_NIGHT)  # type: ignore


def apply_entity_lighting(
    img: Image.Image,
    sec: int | None = None,
    light_boost: float = 0.0,
    light_color: Tuple[int, int, int] = (255, 190, 92),
) -> Image.Image:
    """Apply a restrained day/night tint to a player or creature sprite.

    Entities still belong to the scene's lighting, but they must retain enough
    contrast and colour that their original pixel art remains readable. The
    ambient tint therefore only applies 35% of the terrain's darkening. A local
    light source restores that lost brightness and adds a very small warm cast.
    Alpha is copied byte-for-byte and no filtering is performed.
    """
    if sec is None:
        sec = ingame_seconds()
    r, g, b = tint_factor(sec)
    strength = 0.35
    boost = max(0.0, min(1.0, float(light_boost)))
    lc = tuple(max(0.0, min(1.0, c / 255.0)) for c in light_color)
    factors = []
    for ambient, warm in zip((r, g, b), lc):
        gentle = 1.0 - strength * (1.0 - ambient)
        factors.append(min(1.0, gentle + (1.0 - gentle) * boost * warm))

    rgba = img.convert("RGBA")
    red, green, blue, alpha = rgba.split()
    luts = [[min(255, int(i * factor)) for i in range(256)]
            for factor in factors]
    return Image.merge("RGBA", (
        red.point(luts[0]), green.point(luts[1]), blue.point(luts[2]), alpha,
    ))


def light_strength_at(
    px: float,
    py: float,
    sources: Iterable[Tuple[float, float, float, float, Tuple[int, int, int]]],
) -> Tuple[float, Tuple[int, int, int]]:
    """Return the strongest local light at a pixel/entity centre.

    Sources are ``(x, y, radius_px, intensity, RGB)``. Squared falloff keeps a
    torch bright at its tile and makes its influence disappear smoothly instead
    of creating a hard circular cut-out.
    """
    best = (0.0, (255, 190, 92))
    for sx, sy, radius, intensity, color in sources:
        radius = max(1.0, float(radius))
        distance = ((px - sx) ** 2 + (py - sy) ** 2) ** 0.5
        if distance >= radius:
            continue
        strength = max(0.0, min(1.0, float(intensity)))
        strength *= (1.0 - distance / radius) ** 2
        if strength > best[0]:
            best = (strength, color)
    return best


def apply_local_lighting(
    img: Image.Image,
    sources: Iterable[Tuple[float, float, float, float, Tuple[int, int, int]]],
) -> Image.Image:
    """Paint soft, low-alpha local glows over the already ambient-lit scene."""
    out = img.convert("RGBA")
    for sx, sy, radius, intensity, color in sources:
        radius = max(1.0, float(radius))
        strength = max(0.0, min(1.0, float(intensity)))
        if strength <= 0.0:
            continue
        glow = Image.new("RGBA", out.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(glow)
        x, y = float(sx), float(sy)
        outer = radius
        # Two restrained rings read as light without washing the terrain out.
        draw.ellipse(
            (x - outer, y - outer, x + outer, y + outer),
            fill=(*color, int(22 * strength)),
        )
        inner = outer * 0.42
        draw.ellipse(
            (x - inner, y - inner, x + inner, y + inner),
            fill=(*color, int(38 * strength)),
        )
        glow = glow.filter(ImageFilter.GaussianBlur(max(1, int(radius * 0.08))))
        out = Image.alpha_composite(out, glow)
    return out


def apply_daynight(img: Image.Image, sec: int | None = None) -> Image.Image:
    """Return a copy of ``img`` tinted by the in-game day/night clock.

    ``sec`` is the in-game second-of-day; when omitted it is taken from the
    accelerated in-game clock (``ingame_seconds``), NOT real-world time.

    Preserves the alpha channel (map tokens are RGBA with transparent corners),
    so only the colour channels are multiplied. Pure: does not mutate the input
    and never touches Discord/game state.
    """
    if sec is None:
        sec = ingame_seconds()
    r, g, b = tint_factor(sec)
    img = img.convert("RGBA")
    bands = img.split()
    lut_r = [min(255, int(i * r)) for i in range(256)]
    lut_g = [min(255, int(i * g)) for i in range(256)]
    lut_b = [min(255, int(i * b)) for i in range(256)]
    tinted = (
        bands[0].point(lut_r),
        bands[1].point(lut_g),
        bands[2].point(lut_b),
        bands[3],
    )
    return Image.merge("RGBA", tinted)
