import asyncio
import colorsys
import hashlib
import io
import math
import os
import unicodedata as _unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageChops, ImageDraw

from game.blocks import BlockDef, BlockGrid, get_block
from game.map_loader import MapData
from game.state import Direction, GameState
from rendering.avatar import AvatarCache
from rendering.camera import Camera
from rendering.daynight import (
    apply_daynight,
    apply_entity_lighting,
    apply_local_lighting,
    ingame_seconds,
    light_strength_at,
)
from rendering.weather_fx import WeatherFx


# Keep Pillow work bounded: many simultaneous GIF encodes can starve the
# asyncio scheduler even when they run outside the event-loop thread.
_IMAGE_WORKERS = max(1, min(2, os.cpu_count() or 1))
_IMAGE_EXECUTOR = ThreadPoolExecutor(
    max_workers=_IMAGE_WORKERS, thread_name_prefix="map-render"
)


async def run_image_task(func, *args, **kwargs):
    """Run one pure image operation in the bounded render worker pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _IMAGE_EXECUTOR, partial(func, *args, **kwargs)
    )


@dataclass
class RenderResult:
    image: Image.Image
    filename: str = "map.png"
    content_type: str = "image/png"
    composite: Optional[Image.Image] = None
    # Weather FX: when active, ``frames`` holds the full animated GIF frame
    # sequence (``image`` is the last frame; ``composite`` stays overlay-free
    # so the incremental patch cache is never poisoned by particles).
    frames: Optional[List[Image.Image]] = None
    duration_ms: int = 160


# Animated weather overlays (rain/snow/wind/storm...) compose this many GIF
# frames per screen render. 6 @ 160ms ~= one 0.96s weather loop.
WEATHER_FX_FRAMES = 6


def effective_weather_key(rt) -> Optional[str]:
    """The weather key a screen render should use, or None when the scenario
    FX gate is OFF (the default). The gate is the single performance switch
    for the animated weather overlays: when it is closed, screens render a
    fast static PNG instead of composing the 6-frame GIF (the dominant CPU
    cost of busy scenarios). The hub HUD weather icon is unaffected — it is
    part of the small hub image, not the screen render."""
    if not getattr(rt, "weather_fx_enabled", False):
        return None
    return getattr(rt, "weather_key", None)


# ---------------------------------------------------------------------------
# Weather-conditional and player-overlay Tiled layers (matched ASCII-folded
# on layer names, so maps stay data-driven — no hard-coded coordinates).
# ---------------------------------------------------------------------------
#: Rain-conditional layers (the bigmap's puddles: "chỉ xuất hiện khi có mưa").
RAIN_ONLY_MARKERS = ("chi xuat hien khi co mua", "rain only")
#: Layers drawn ABOVE player/entity tokens ("bên trên player" overlays).
ABOVE_PLAYER_MARKERS = ("ben tren player", "above player")


def normalize_layer_name(name: str) -> str:
    """ASCII-fold a Tiled layer name (public helper; mirrors map_loader)."""
    nfkd = _unicodedata.normalize("NFKD", name or "")
    return "".join(ch for ch in nfkd if not _unicodedata.combining(ch)).lower()


def is_rain_only_layer(name: str) -> bool:
    nl = normalize_layer_name(name)
    return any(m in nl for m in RAIN_ONLY_MARKERS)


def is_above_player_layer(name: str) -> bool:
    nl = normalize_layer_name(name)
    return any(m in nl for m in ABOVE_PLAYER_MARKERS)


#: Weather keys that count as "raining" for rain-only layers.
RAIN_WEATHER_KEYS = frozenset({"rain", "heavy_rain", "storm"})


def weather_excluded_layers(map_data, weather_key: Optional[str]) -> frozenset:
    """Layer names the base pass must SKIP for this weather.

    Rain-only layers (puddles) are skipped whenever the sky is dry. They are
    drawn back as an overlay (see ``_overlay_layers``) while it rains.
    """
    if weather_key in RAIN_WEATHER_KEYS:
        return frozenset()
    return frozenset(
        (name or "").strip().lower()
        for name, _grid in getattr(map_data, "tile_layers", ()) or ()
        if is_rain_only_layer(name)
    )


def _overlay_layer_images(map_data, sheet, cols, firstgid, tw, th,
                          want_above: bool):
    """(name, [(px, py, gid), ...]) for conditional overlay layers.

    ``want_above=True`` yields the "bên trên player" layers (drawn after
    entity tokens); ``want_above=False`` yields the rain-only layers (drawn
    right after the base, before tokens). Tiles are reported as sheet gids so
    the caller can crop (and optionally fade) them per frame.
    """
    layers = []
    for name, grid in getattr(map_data, "tile_layers", ()) or []:
        if want_above != is_above_player_layer(name):
            continue
        if not want_above and not is_rain_only_layer(name):
            continue
        blits = []
        for y, row in enumerate(grid):
            for x, gid in enumerate(row):
                if gid == 0:
                    continue
                blits.append((x * tw, y * th, gid))
        if blits:
            layers.append((name, blits))
    return layers


# Aggressive upload shrink: render the map internally at full tile resolution
# (so coordinates/collision stay correct), then downscale + palette-quantize the
# PNG actually uploaded to Discord's CDN. This is the dominant latency factor.
MAP_UPLOAD_SCALE = 0.5
# Keep enough palette entries for avatar/mob colours and weather highlights.
# Dither remains disabled, so extra colours improve fidelity without adding
# the grain/noise that Floyd-Steinberg would introduce over the terrain.
# 64+ entries: a large flat-colour mob (e.g. a green zombie tile) dominates
# an adaptive palette's dark cluster, and with too few slots nearby avatar
# pixels visibly snap toward the mob's hue ("avatar turns green near mobs").
MAP_PALETTE_COLORS = 64
MAP_GIF_PALETTE_COLORS = 128
UPLOAD_BG_COLOR = (20, 28, 40)


def _token_color(user_id: int):
    h = hashlib.md5(str(user_id).encode()).digest()[0] / 255.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.55, 0.85)
    return (int(r * 255), int(g * 255), int(b * 255))


def block_tile_image(bdef: BlockDef, tile: int) -> Image.Image:
    """Pure: one placed-block tile drawn over the ground (fill + border +
    top highlight). The ground shows again as soon as the block is removed."""
    img = Image.new("RGBA", (tile, tile), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, g, b = bdef.color
    d.rectangle([0, 0, tile - 1, tile - 1], fill=(r, g, b, 255))
    # Darker frame so the block reads as a solid object on the ground.
    dark = (int(r * 0.6), int(g * 0.6), int(b * 0.6), 255)
    d.rectangle([0, 0, tile - 1, tile - 1], outline=dark, width=max(1, tile // 16))
    # Top-left highlight for a subtle 3D face.
    light = (min(255, int(r * 1.25)), min(255, int(g * 1.25)), min(255, int(b * 1.25)), 255)
    d.line([(1, 1), (tile - 2, 1)], fill=light, width=max(1, tile // 16))
    return img


def block_texture_image(assets_dir: Path, block_id: str, tile: int) -> Optional[Image.Image]:
    """Optional per-block sprite: ``assets/blocks/<block_id>.png``.

    The PNG is authored at 16x16 (Minecraft-style block face) and is scaled
    NEAREST to the tile size so pixels stay crisp. Returns None when no
    texture exists — the caller falls back to :func:`block_tile_image`.
    Pure: reads a file, never mutates game state.
    """
    # Renderer receives assets/maps; block sprites live in assets/blocks.
    base = assets_dir.parent if assets_dir.name == "maps" else assets_dir
    path = base / "blocks" / f"{block_id}.png"
    if not path.is_file():
        return None
    try:
        tex = Image.open(path).convert("RGBA")
    except OSError:
        # Corrupt sprite must never take down a render — fall back to the
        # flat-color tile instead (rule 20: report, never swallow silently).
        print(f"[RENDER] corrupt block texture ignored: {path}")
        return None
    if tex.size != (tile, tile):
        tex = tex.resize((tile, tile), Image.NEAREST)
    return tex


def dead_screen_image(
    size: tuple, reason: str = "", font_path: Optional[Path] = None
) -> Image.Image:
    """Build a centered, readable death screen at the internal render size.

    Discord receives a 0.5x image, so all typography is deliberately sized
    before upload. ``font_path`` points at the bundled Tahoma file; the fallback
    still works on a source checkout without assets.
    """
    w, h = max(160, int(size[0])), max(120, int(size[1]))
    img = Image.new("RGBA", (w, h), (12, 7, 14, 255))
    d = ImageDraw.Draw(img)
    scale = max(1.0, min(w / 672.0, h / 480.0))
    border = max(3, int(7 * scale))
    d.rectangle([border, border, w - border - 1, h - border - 1],
                outline=(186, 45, 64, 255), width=border)

    card_w = int(w * 0.76)
    card_h = int(h * 0.86)
    left = (w - card_w) // 2
    top = (h - card_h) // 2
    d.rounded_rectangle(
        [left, top, left + card_w, top + card_h],
        radius=max(8, int(18 * scale)),
        fill=(29, 18, 29, 245),
        outline=(116, 39, 57, 255),
        width=max(2, int(4 * scale)),
    )

    def load_font(px: int):
        if font_path is not None and font_path.is_file():
            from PIL import ImageFont
            return ImageFont.truetype(str(font_path), max(10, px))
        return None

    title_font = load_font(int(40 * scale))
    body_font = load_font(int(22 * scale))
    timer_font = load_font(int(25 * scale))

    cx = w // 2
    skull_cy = top + int(card_h * 0.25)
    radius = max(24, int(min(card_w, card_h) * 0.16))
    d.ellipse(
        [cx - radius, skull_cy - radius, cx + radius, skull_cy + radius],
        fill=(83, 88, 98, 255), outline=(235, 235, 235, 255),
        width=max(2, int(3 * scale)),
    )
    eye_r = max(4, radius // 6)
    for ex in (cx - radius // 3, cx + radius // 3):
        d.ellipse([ex - eye_r, skull_cy - eye_r, ex + eye_r, skull_cy + eye_r],
                  fill=(31, 20, 35, 255))
    d.arc(
        [cx - radius // 2, skull_cy, cx + radius // 2, skull_cy + radius // 2],
        15, 165, fill=(31, 20, 35, 255), width=max(2, int(3 * scale)),
    )

    def centered(text: str, y: int, font, fill):
        bbox = d.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        d.text((cx - tw // 2, y), text, font=font, fill=fill,
               stroke_width=max(0, int(scale)), stroke_fill=(8, 5, 10, 255))

    title_y = top + int(card_h * 0.52)
    centered("BAN DA CHET", title_y, title_font, (255, 91, 105, 255))
    reason_text = (reason or "Bi zombie tan cong")[:42]
    centered(f"Ly do: {reason_text}", title_y + int(48 * scale), body_font,
             (231, 211, 218, 255))
    centered("HOI SINH SAU", title_y + int(80 * scale), body_font,
             (255, 222, 137, 255))
    centered("5 GIAY", title_y + int(108 * scale), timer_font,
             (255, 239, 176, 255))
    return img


def zombie_tile_image(tile: int, hp_ratio: float = 1.0) -> Image.Image:
    """Pure procedural zombie token; keeps the map usable without new assets."""
    img = Image.new("RGBA", (tile, tile), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inset = max(2, tile // 8)
    x0, y0, x1, y1 = inset, inset, tile - 1 - inset, tile - 1 - inset
    # Rounded silhouette with a face, rather than the old opaque square token.
    body = (86, 148, 92, 255)
    shade = (38, 76, 48, 255)
    eye = (180, 42, 42, 255)
    d.ellipse([x0, y0, x1, y1], fill=body, outline=shade, width=max(1, tile // 16))
    d.rectangle([x0 + tile // 4, y1 - max(2, tile // 6), x1 - tile // 4, y1], fill=shade)
    eye_size = max(2, tile // 10)
    eye_y = y0 + max(2, tile // 4)
    left_x = x0 + max(2, tile // 5)
    right_x = x1 - max(2, tile // 5) - eye_size
    d.rectangle([left_x, eye_y, left_x + eye_size, eye_y + eye_size], fill=eye)
    d.rectangle([right_x, eye_y, right_x + eye_size, eye_y + eye_size], fill=eye)
    mouth_y = y0 + max(4, tile // 2)
    d.line([(x0 + tile // 4, mouth_y), (x1 - tile // 4, mouth_y)], fill=shade,
           width=max(1, tile // 16))

    # Only damaged zombies get a health bar. This avoids making every nearby
    # zombie appear to share the damaged one's HP state.
    if hp_ratio < 1.0:
        bar_y = max(0, y0 - max(2, tile // 16) - 1)
        bar_h = max(2, tile // 16)
        d.rectangle([x0, bar_y, x1, bar_y + bar_h], fill=(75, 25, 25, 255))
        fill_w = max(0, int((x1 - x0 + 1) * hp_ratio))
        if fill_w:
            d.rectangle([x0, bar_y, x0 + fill_w - 1, bar_y + bar_h], fill=(218, 72, 62, 255))
    return img


# Facing indicator style: a single soft bead at the token edge — small,
# anti-aliased (supersampled), calm. No chevrons, no frames.
ARROW_FILL = (255, 250, 240, 225)
ARROW_EDGE = (20, 20, 20, 70)  # faint grounding halo under the bead
# Target-tile marker: four thin corner ticks (warm white, low alpha) instead
# of a full frame — precise, but nearly silent to the eye.
HIGHLIGHT_COLOR = (255, 244, 179, 150)


def _draw_facing_dot(img: Image.Image, direction: str, tile: int,
                     alpha_scale: float = 1.0) -> None:
    """Stamp one small bead at the token edge pointing at `direction`.

    Drawn on a 4x supersampled overlay then downscaled with LANCZOS so the
    dot is perfectly round and anti-aliased (no crude pixel block).
    `alpha_scale` < 1 fades the bead (used while the player is travelling)."""
    try:
        dx, dy = Direction[direction].vector
    except (KeyError, TypeError):
        return
    fill = (*ARROW_FILL[:3], int(ARROW_FILL[3] * alpha_scale))
    halo = (*ARROW_EDGE[:3], int(ARROW_EDGE[3] * alpha_scale))
    ss = 4
    overlay = Image.new("RGBA", (tile * ss, tile * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    ang = math.atan2(dy, dx)
    c = tile * ss / 2.0
    r = tile * ss * 0.34
    x = c + r * math.cos(ang)
    y = c + r * math.sin(ang)
    dot_r = tile * ss * 0.050   # ~1.6 px at tile=32
    halo_r = dot_r * 2.4        # soft grounding halo
    d.ellipse([x - halo_r, y - halo_r, x + halo_r, y + halo_r], fill=halo)
    d.ellipse([x - dot_r, y - dot_r, x + dot_r, y + dot_r], fill=fill)
    img.alpha_composite(overlay.resize((tile, tile), Image.LANCZOS))


def _draw_tile_highlight(comp: Image.Image, px: int, py: int, tile: int,
                         alpha_scale: float = 1.0) -> None:
    """Four thin corner ticks marking the tile 🧱/🔨 will act on — subtle.

    Drawn on a transparent overlay then alpha-composited: ImageDraw would
    REPLACE pixels on an RGBA image (no blending), which made the ticks look
    harsh. The overlay keeps the low-alpha softness."""
    a = int(HIGHLIGHT_COLOR[3] * alpha_scale)
    if a <= 0:
        return
    overlay = Image.new("RGBA", (tile, tile), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    inset = max(1, tile // 16)
    L = max(3, tile // 5)
    c = (*HIGHLIGHT_COLOR[:3], a)
    x0, y0 = inset, inset
    x1, y1 = tile - 1 - inset, tile - 1 - inset
    # Top-left / top-right / bottom-left / bottom-right L-ticks.
    d.line([(x0, y0), (x0 + L, y0)], fill=c, width=1)
    d.line([(x0, y0), (x0, y0 + L)], fill=c, width=1)
    d.line([(x1 - L, y0), (x1, y0)], fill=c, width=1)
    d.line([(x1, y0), (x1, y0 + L)], fill=c, width=1)
    d.line([(x0, y1), (x0 + L, y1)], fill=c, width=1)
    d.line([(x0, y1 - L), (x0, y1)], fill=c, width=1)
    d.line([(x1 - L, y1), (x1, y1)], fill=c, width=1)
    d.line([(x1, y1 - L), (x1, y1)], fill=c, width=1)
    comp.alpha_composite(overlay, dest=(px, py))


def encode_upload_gif(frames: List[Image.Image], duration_ms: int = 160,
                      loop: int = 0, optimize: bool = True) -> bytes:
    """Encode frames into upload bytes with one shared adaptive palette.

    ``optimize=False`` skips Pillow's expensive delta-frame optimisation; it is
    useful for frequently refreshed weather screens where CPU latency matters
    more than the smaller attachment. ``loop=1`` plays once (pickup/chop).
    """
    small = []
    for f in frames:
        w, h = f.size
        nw, nh = max(1, int(w * MAP_UPLOAD_SCALE)), max(1, int(h * MAP_UPLOAD_SCALE))
        small.append(f.resize((nw, nh), Image.NEAREST))
    # Dither-free quantization: Floyd-Steinberg would sprinkle noise over
    # every flat map region (huge GIFs + a grainy look on screen).
    base_p = small[0].quantize(colors=MAP_GIF_PALETTE_COLORS)
    gif = [s.quantize(palette=base_p, dither=Image.Dither.NONE) for s in small]
    buf = io.BytesIO()
    gif[0].save(
        buf,
        "GIF",
        save_all=True,
        append_images=gif[1:],
        loop=loop,
        duration=duration_ms,
        optimize=optimize,
    )
    return buf.getvalue()


def build_pickup_frames(base: Image.Image, from_px: tuple, to_px: tuple,
                        item_img: Image.Image, n_frames: int = 12,
                        tile: int = 32) -> List[Image.Image]:
    """Break→drop→pickup animation frames (pure PIL, no IO).

    Phase 1 (25%): the item pops out of the broken block's tile and hops up
    (a little "drop" bounce). Phase 2 (65%): it arcs toward the nearest
    player while shrinking and fading. Final frame: gone — only the ground.
    ``base`` is the post-break screen frame (RGB); ``from_px``/``to_px`` are
    viewport-relative pixel centres."""
    frames: List[Image.Image] = []
    fw, fh = base.size
    fx, fy = from_px
    tx, ty = to_px
    big = item_img.size[0]
    for i in range(n_frames):
        t = i / max(1, n_frames - 1)
        frame = base.copy().convert("RGBA")
        if t < 0.25:
            # Pop out of the block with a little hop.
            k = t / 0.25
            scale = 1.0 - 0.4 * k
            px, py = fx, fy - int(tile * 0.4 * math.sin(k * math.pi))
        elif t < 0.9:
            k = (t - 0.25) / 0.65
            px = fx + (tx - fx) * k
            py = fy + (ty - fy) * k - int(tile * 0.5 * math.sin(k * math.pi))
            scale = 0.6 - 0.3 * k
        else:
            # Final frame: the item has been picked up — nothing left to draw.
            frames.append(frame.convert("RGB"))
            continue
        alpha = 255 if t < 0.7 else int(255 * (1.0 - (t - 0.7) / 0.2))
        size = max(3, int(big * scale))
        sprite = item_img.resize((size, size), Image.NEAREST).copy()
        a = sprite.getchannel("A").point(lambda v: v * alpha // 255)
        sprite.putalpha(a)
        frame.alpha_composite(sprite, (int(px - size / 2), int(py - size / 2)))
        frames.append(frame.convert("RGB"))
    return frames


def build_chop_frames(after: Image.Image, parts, n_frames: int = 8) -> List[Image.Image]:
    """Fade-out frames for a felled node (pure PIL, no IO).

    ``after`` is the screen frame WITHOUT the node (rendered right after the
    felling swing landed — the ground/base is already tree-free). ``parts`` is
    a list of ``(px, py, tile_img)`` in frame pixel coords covering the old
    sprite. Each frame redraws those tiles with linearly decreasing alpha, so
    the tree/bush visibly fades out and vanishes (only the chopped node — the
    caller picks exactly its tiles).
    """
    if not parts:
        return [after]
    frames = []
    base = after.convert("RGBA")
    count = max(2, n_frames)
    for i in range(count):
        t = i / max(1, count - 1)
        alpha = int(255 * (1.0 - t))
        frame = base.copy()
        for px, py, tile_img in parts:
            sprite = tile_img.convert("RGBA").copy()
            a = sprite.getchannel("A").point(lambda v: v * alpha // 255)
            sprite.putalpha(a)
            frame.alpha_composite(sprite, (px, py))
        frames.append(frame.convert("RGB"))
    return frames


def finalize_for_upload(img: Image.Image) -> Image.Image:
    """Shrink + palette-quantize a rendered map for fast CDN upload.

    Quantization uses FASTOCTREE: measured ~4x faster than the ADAPTIVE
    (median-cut) default on real frames with a mean luma deviation of only
    ~0.7/255 (invisible), and it produces a slightly smaller PNG because
    octree colour clustering maps flat map regions onto fewer distinct
    palette indices.
    """
    w, h = img.size
    if MAP_UPLOAD_SCALE != 1.0:
        nw, nh = max(1, int(w * MAP_UPLOAD_SCALE)), max(1, int(h * MAP_UPLOAD_SCALE))
        img = img.resize((nw, nh), Image.NEAREST)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        # Fast path: a fully-opaque frame (plain terrain, no token corners)
        # needs no flatten — the alpha composite would be a no-op that still
        # costs ~4ms on a viewport-sized image.
        if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] == 255:
            rgb = img.convert("RGB")
        else:
            bg = Image.new("RGB", img.size, UPLOAD_BG_COLOR)
            rgb = Image.alpha_composite(bg.convert("RGBA"), img).convert("RGB")
        img = rgb
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img.quantize(colors=MAP_PALETTE_COLORS, method=Image.Quantize.FASTOCTREE)


def screen_internal_size(rt, tile_size: int = 32) -> tuple:
    """Pre-finalize pixel size of the screen/map image (what ``render`` returns
    before ``finalize_for_upload`` shrinks it). Using this as the hub's internal
    width guarantees the hub's displayed width equals the screen's displayed
    width exactly (the same 0.5 shrink is applied to identical input widths).
    """
    cam = getattr(rt, "camera", None)
    if cam is not None and cam.follow:
        w = max(1, int(round(cam.view_w * tile_size * cam.zoom)))
        h = max(1, int(round(cam.view_h * tile_size * cam.zoom)))
    else:
        w = max(1, rt.map_data.width * tile_size)
        h = max(1, rt.map_data.height * tile_size)
    return w, h


def screen_internal_size_for_camera(camera, rt, tile_size: int = 32) -> tuple:
    """Like ``screen_internal_size`` but driven by a specific player's camera
    (per-player follow camera) instead of the shared ``rt.camera``.

    This is what makes each player's hub bar align to their own screen width
    when follow-zoom or a different viewport is active.
    """
    if camera is not None and camera.follow:
        w = max(1, int(round(camera.view_w * tile_size * camera.zoom)))
        h = max(1, int(round(camera.view_h * tile_size * camera.zoom)))
    else:
        w = max(1, rt.map_data.width * tile_size)
        h = max(1, rt.map_data.height * tile_size)
    return w, h


def screen_upload_size(rt, tile_size: int = 32) -> tuple:
    """Displayed (post-finalize) pixel size of the screen/map image.

    Matches the exact transform applied in ``finalize_for_upload`` so the hub
    bar can be rendered to the same width. Vertical is left to the caller.
    """
    cam = getattr(rt, "camera", None)
    if cam is not None and cam.follow:
        w = int(cam.view_w * tile_size * cam.zoom * MAP_UPLOAD_SCALE)
        h = int(cam.view_h * tile_size * cam.zoom * MAP_UPLOAD_SCALE)
    else:
        w = int(rt.map_data.width * tile_size * MAP_UPLOAD_SCALE)
        h = int(rt.map_data.height * tile_size * MAP_UPLOAD_SCALE)
    return max(1, w), max(1, h)


class Renderer:
    """Pure: consumes GameState + MapData, produces a RenderResult.

    Must NOT mutate GameState and must NOT touch Discord or the database.
    """

    def __init__(self, assets_dir: Path, avatar_cache: AvatarCache, tile_size: int = 32):
        self.assets_dir = assets_dir
        self.avatar_cache = avatar_cache
        self.tile_size = tile_size
        self._base_cache: Dict[tuple, Image.Image] = {}
        self._block_tile_cache: Dict[str, Image.Image] = {}
        self._zombie_tile_cache: Dict[tuple, Image.Image] = {}
        # Tileset sheet per map + cropped resource tiles (trees/bushes).
        self._sheet_cache: Dict[tuple, Image.Image] = {}
        self._gid_crop_cache: Dict[tuple, Image.Image] = {}
        self._resource_tile_cache: Dict[tuple, Image.Image] = {}
        # Faded above-player overlay tiles per (map_id, gid): when an entity
        # stands on an overlay tile, the sprite is drawn at 45% alpha (the
        # classic RPG walk-under-canopy fade) so the token shows through.
        self._faded_overlay_cache: Dict[tuple, Image.Image] = {}
        # Weather particle overlays (assets/fx/**), preloaded once.
        self.weather_fx = WeatherFx(assets_dir)
        # Static per-map scan of "bên trên player" overlay layers (above-layer
        # blits are pure map data — computed once, reused every frame).
        self._above_layer_cache: Dict[str, list] = {}
        # Per-player sprite tokens keyed by (user_id, direction, tile): the
        # token only changes when the player turns, so consecutive frames of a
        # moving player reuse the exact same RGBA image instead of re-scaling
        # and re-drawing the facing bead on every render.
        self._token_cache: Dict[tuple, Image.Image] = {}
        self._token_cache_max = 256

    def _block_tile(self, block_id: str) -> Optional[Image.Image]:
        """Cached per-block tile image at the renderer's tile size."""
        cached = self._block_tile_cache.get(block_id)
        if cached is not None:
            return cached
        bdef = get_block(block_id)
        if bdef is None:
            return None
        # Textured blocks win when a sprite exists; flat fill is the fallback.
        img = block_texture_image(self.assets_dir, block_id, self.tile_size)
        if img is None:
            img = block_tile_image(bdef, self.tile_size)
        self._block_tile_cache[block_id] = img
        return img

    def _zombie_tile(self, zombie) -> Image.Image:
        """Cached zombie token with a health bar once damaged."""
        max_hp = max(1, int(getattr(zombie, "max_hp", 1)))
        hp_ratio = max(0.0, min(1.0, float(getattr(zombie, "hp", 0)) / max_hp))
        # Keep exact HP in the cache key: two zombies with different damage
        # must never reuse a shared visual state.
        key = (self.tile_size, max_hp, int(getattr(zombie, "hp", 0)))
        cached = self._zombie_tile_cache.get(key)
        if cached is None:
            cached = zombie_tile_image(self.tile_size, hp_ratio)
            self._zombie_tile_cache[key] = cached
        return cached

    def _ghost_tile(self, block_id: str) -> Optional[Image.Image]:
        """Faded (45% alpha) copy of a block tile for the Build Mode preview."""
        cached = self._block_tile_cache.get(f"ghost:{block_id}")
        if cached is not None:
            return cached
        img = self._block_tile(block_id)
        if img is None:
            return None
        ghost = img.convert("RGBA").copy()
        alpha = ghost.getchannel("A").point(lambda a: a * 45 // 100)
        ghost.putalpha(alpha)
        self._block_tile_cache[f"ghost:{block_id}"] = ghost
        return ghost

    def _sheet_for(self, map_data: MapData, firstgid: int = 0) -> Optional[Image.Image]:
        """Cached tileset sheet for a map (None if no bundled PNG).

        Multi-sheet maps (lobbytrade) key their sheets by firstgid; a plain
        single-sheet map keeps its map_id key (bigmap behaviour unchanged)."""
        key = (map_data.map_id, firstgid) if firstgid else map_data.map_id
        cached = self._sheet_cache.get(key)
        if cached is not None:
            return cached
        tileset = None
        if firstgid:
            for ts in getattr(map_data, "tilesets", []) or []:
                if ts.get("firstgid") == firstgid:
                    tileset = ts
                    break
        if tileset is None:
            tileset = getattr(map_data, "tileset", None)
        if not tileset:
            return None
        ip = tileset.get("image_path")
        if not ip or not Path(ip).exists():
            return None
        sheet = Image.open(ip).convert("RGBA")
        self._sheet_cache[key] = sheet
        return sheet

    def _sheet_for_gid(self, map_data: MapData, gid: int) -> Optional[Image.Image]:
        """Sheet whose firstgid range covers ``gid`` (multi-tileset maps)."""
        best = None
        for ts in getattr(map_data, "tilesets", []) or []:
            if ts.get("firstgid", 1) <= gid and (
                best is None or ts["firstgid"] > best["firstgid"]
            ):
                best = ts
        if best is None:
            return self._sheet_for(map_data)
        return self._sheet_for(map_data, best["firstgid"]) or self._sheet_for(map_data)

    def _resource_tile_image(self, map_data: MapData, gid: int) -> Optional[Image.Image]:
        """Cached crop of ONE resource tile (tree/bush) from the tileset."""
        key = (map_data.map_id, gid)
        cached = self._resource_tile_cache.get(key)
        if cached is not None:
            return cached
        img = self._crop_gid(map_data, gid)
        if img is not None:
            self._resource_tile_cache[key] = img
        return img

    def _resource_blits(
        self, map_data: MapData, resource_tiles, x0: int, y0: int,
        x1: int, y1: int, ox: int = 0, oy: int = 0,
    ) -> list:
        """(px, py, tile_img) for the STILL-VISIBLE resource tiles inside the
        viewport. Chopped nodes are already excluded from ``resource_tiles``
        (the caller passes ``ResourceGrid.visible_tiles()``)."""
        if not resource_tiles:
            return []
        blits = []
        for x, y, gid in resource_tiles:
            if not (x0 <= x < x1 and y0 <= y < y1):
                continue
            img = self._resource_tile_image(map_data, gid)
            if img is None:
                continue
            blits.append(((x - ox) * self.tile_size, (y - oy) * self.tile_size, img))
        return blits

    def _aim_target(self, state: GameState, focus_user_id: Optional[int]):
        """Build Mode aim cursor of the screen owner: (tile, ghost_img) or None.

        The ghost tile preview shows exactly which block lands where BEFORE
        it is placed; the tile also becomes the highlight ring target.
        """
        if focus_user_id is None:
            return None
        p = state.get_player(focus_user_id)
        if p is None or not getattr(p, "aim_active", False):
            return None
        ghost = self._ghost_tile(getattr(p, "aim_block", None) or "stone")
        return ((p.x + p.aim_dx, p.y + p.aim_dy), ghost)

    def _zombie_blits(self, zombies, x0: int, y0: int, x1: int, y1: int,
                      ox: int = 0, oy: int = 0) -> list:
        """Return visible zombie sprites in crop-relative pixel coordinates."""
        if not zombies:
            return []
        blits = []
        for zombie in zombies:
            if not getattr(zombie, "alive", True):
                continue
            if not (x0 <= zombie.x < x1 and y0 <= zombie.y < y1):
                continue
            blits.append(((zombie.x - ox) * self.tile_size,
                          (zombie.y - oy) * self.tile_size,
                          self._zombie_tile(zombie)))
        return blits

    def _block_blits(self, blocks: Optional[BlockGrid], x0: int = 0, y0: int = 0,
                     x1: Optional[int] = None, y1: Optional[int] = None,
                     ox: int = 0, oy: int = 0) -> list:
        """(px, py, tile_img) for blocks inside the viewport.

        Coordinates are RELATIVE to the crop origin ``(ox, oy)`` — in pixels of
        the final image. Full-map render uses ox=oy=0 (map coords); the follow
        camera passes ox=x0, oy=y0 so every block stays grid-anchored at its
        exact world tile no matter where the camera scrolls."""
        if blocks is None:
            return []
        blits = []
        for (bx, by), bid in blocks.items():
            if not (x0 <= bx < x1 and y0 <= by < y1):
                continue
            img = self._block_tile(bid)
            if img is not None:
                blits.append(((bx - ox) * self.tile_size, (by - oy) * self.tile_size, img))
        return blits

    async def render(
        self,
        state: GameState,
        map_data: MapData,
        members: Optional[dict] = None,
        camera: Optional[Camera] = None,
        prev_composite: Optional[Image.Image] = None,
        moved_user_id: Optional[int] = None,
        prev_pos: Optional[tuple] = None,
        full: bool = False,
        focus_user_id: Optional[int] = None,
        weather_key: Optional[str] = None,
        indicator_dim: bool = False,
        fx_seed: int = 0,
        resource_tiles: Optional[list] = None,
        resource_layer_names: Optional[set] = None,
        terrain_tiles: Optional[list] = None,
        light_sources: Optional[list] = None,
    ) -> RenderResult:
        tile = self.tile_size
        w = map_data.width * tile
        h = map_data.height * tile
        focused = state.get_player(focus_user_id) if focus_user_id is not None else None
        if focused is not None and not focused.alive:
            if camera is not None and camera.follow:
                dead_size = (
                    max(1, camera.view_w * tile),
                    max(1, camera.view_h * tile),
                )
            else:
                dead_size = (w, h)
            dead = dead_screen_image(
                dead_size,
                focused.death_reason or "Bi zombie tan cong",
                self.assets_dir.parent / "fonts" / "tahoma.ttf",
            )
            return RenderResult(image=dead, composite=None)
        excluded = frozenset(resource_layer_names or ())
        # Grass-tuft layers ("cỏ") are overlay terrain: they are excluded
        # from the base and drawn from the TerrainGrid's visible tiles so a
        # scooped tile loses its tuft. The grass ground STAYS in the base;
        # scooped tiles get a bare-dirt cover pasted on top (game/terrain).
        from game.terrain import is_dirt_base_layer, is_grass_layer

        excluded |= frozenset(
            (name or "").strip().lower()
            for name, _grid in getattr(map_data, "tile_layers", ()) or ()
            if is_grass_layer(name) and not is_dirt_base_layer(name)
        )
        # Rain-only layers (the bigmap puddles) are skipped from the base when
        # the sky is dry, and composited back while it rains.
        excluded |= weather_excluded_layers(map_data, weather_key)
        # Loading/cropping a full map image can also take hundreds of
        # milliseconds on the first render; keep it off the event loop too.
        base = await run_image_task(
            self._base_layer, map_data, w, h, tile, excluded=excluded
        )
        # Placed-block overlay (read-only): covers ground tiles; breaking a
        # block simply removes it so the ground shows again.
        blocks: Optional[BlockGrid] = getattr(state, "blocks", None)
        world_lights = self._light_sources(state, blocks, tile)
        world_lights.extend(self._coerce_light_sources(light_sources, tile))
        # One timestamp per frame keeps terrain and entity lighting in lockstep
        # when the accelerated clock crosses a minute/phase boundary.
        lighting_sec = ingame_seconds()
        raw_zombies = getattr(state, "zombies", None)
        zombies = list(raw_zombies.values()) if isinstance(raw_zombies, dict) else list(raw_zombies or [])
        # Facing-tile highlight of the screen owner: shows where 🧱/🔨 land.
        # Build Mode aim cursor (active) replaces it and adds a ghost preview
        # of the selected block on the target tile.
        aim = self._aim_target(state, focus_user_id)
        ghost_img = aim[1] if aim is not None else None
        hl_tile = aim[0] if aim is not None else self._facing_tile(state, focus_user_id)
        hl_px = None
        ghost_px = None
        # Animated weather (rain/snow/wind/storm): forces the full render path
        # (an incremental tile patch could never repaint the whole overlay).
        fx_active = self.weather_fx.available() and self.weather_fx.is_animated(
            weather_key
        )
        # While the player is travelling (auto-run / long move streak) the
        # facing indicators fade to ~35% — present, but quiet.
        alpha_scale = 0.35 if indicator_dim else 1.0

        # Camera follow: render only a fixed window centred on the target tile.
        # The frame size is constant while the world scrolls, so every step is a
        # fresh viewport render (incremental patching does not apply here). The
        # small window also keeps the uploaded image tiny -> fast CDN upload.
        if camera is not None and camera.follow:
            x0, y0 = camera.top_left(map_data.width, map_data.height)
            vw, vh = camera.view_w * tile, camera.view_h * tile
            # Gather tokens on the event loop (avatar cache may await network);
            # the PIL composition (crop/paste/resize/day-night) runs in a worker
            # thread so a burst of frames never blocks interaction handling.
            blits = []
            for p in state.get_visible_players():
                if x0 <= p.x < x0 + camera.view_w and y0 <= p.y < y0 + camera.view_h:
                    token = await self._make_token(p, members, tile, color=_token_color(p.user_id))
                    blits.append(((p.x - x0) * tile, (p.y - y0) * tile, token))
            zombie_blits = self._zombie_blits(
                zombies, x0, y0, x0 + camera.view_w, y0 + camera.view_h,
                ox=x0, oy=y0,
            )
            block_blits = self._block_blits(
                blocks, x0, y0, x0 + camera.view_w, y0 + camera.view_h,
                ox=x0, oy=y0,
            )
            resource_blits = self._resource_blits(
                map_data, resource_tiles, x0, y0,
                x0 + camera.view_w, y0 + camera.view_h, ox=x0, oy=y0,
            )
            terrain_blits = self._resource_blits(
                map_data, terrain_tiles, x0, y0,
                x0 + camera.view_w, y0 + camera.view_h, ox=x0, oy=y0,
            )
            # Above-player tiles under an entity fade to 45% alpha so the
            # token stays readable (walk-under-canopy behaviour).
            occluded_tiles = frozenset(
                (p.x, p.y) for p in state.get_visible_players()
            ) | frozenset((z.x, z.y) for z in zombies if getattr(z, "alive", True))
            above_blits = self._above_player_blits(map_data, occluded_tiles)
            if hl_tile is not None and (
                x0 <= hl_tile[0] < x0 + camera.view_w
                and y0 <= hl_tile[1] < y0 + camera.view_h
            ):
                hl_px = ((hl_tile[0] - x0) * tile, (hl_tile[1] - y0) * tile)
                if ghost_img is not None:
                    ghost_px = hl_px
            # True zoom: upscale the whole viewport (tokens included) with
            # nearest-neighbour so pixel art stays crisp. This is what makes the
            # focused player appear larger on screen.
            local_lights = self._local_light_sources(world_lights, x0 * tile, y0 * tile)
            # Entity alpha masks are only consumed by the weather-FX compositing
            # path (_weather_overlay_behind_entities). Building them costs a
            # getchannel+copy per entity per frame; skip it entirely when FX is
            # off (the default) so the common frame pays nothing.
            entity_regions = (
                [(px, py, img.width, img.height, img.getchannel("A"))
                 for px, py, img in zombie_blits + blits]
                if fx_active else None
            )
            comp, image = await run_image_task(
                self._compose_follow, base, x0, y0, tile, vw, vh, camera.zoom,
                zombie_blits + blits, block_blits, terrain_blits + resource_blits,
                hl_px, alpha_scale,
                (ghost_px[0], ghost_px[1], ghost_img) if ghost_px is not None else None,
                local_lights, lighting_sec, above_blits,
            )
            if fx_active and camera.zoom != 1.0:
                entity_regions = [
                    (
                        int(px * camera.zoom), int(py * camera.zoom),
                        int(w * camera.zoom), int(h * camera.zoom),
                        mask.resize((
                            max(1, int(w * camera.zoom)),
                            max(1, int(h * camera.zoom)),
                        ), Image.NEAREST),
                    )
                    for px, py, w, h, mask in entity_regions
                ]
            return await self._finish(
                comp, image, weather_key, fx_active, fx_seed, entity_regions
            )

        # Incremental patch: only the moved player's old/new tile change, so
        # re-blit the base tile under the old position and draw the token at the
        # new one. Falls back to a full render when tiles overlap other players,
        # when no cached composite is available, when a block overlay exists
        # (the patched base crop would not carry the placed blocks), when a
        # facing highlight is active (the patch would erase it), or when an
        # animated weather overlay is on (a tile patch can't repaint it).
        has_above_layers = any(
            is_above_player_layer(name) for name, _ in map_data.tile_layers
        )
        if (
            not full
            and not fx_active
            and not resource_tiles
            and not terrain_tiles
            and not has_above_layers  # overlay art must be re-faded per move
            and prev_composite is not None
            and moved_user_id is not None
            and prev_pos is not None
            and blocks is not None
            and len(blocks) == 0
            and hl_tile is None
            and not zombies
        ):
            mover = state.get_player(moved_user_id)
            if mover is not None:
                new_pos = (mover.x, mover.y)
                if prev_pos != new_pos and not self._tiles_overlap_other_player(
                    state, moved_user_id, prev_pos, new_pos
                ):
                    token = await self._make_token(
                        mover, members, tile, color=_token_color(mover.user_id)
                    )
                    comp, image = await run_image_task(
                        self._patch_incremental,
                        prev_composite, prev_pos, new_pos, tile, base, token,
                        world_lights, lighting_sec,
                    )
                    return RenderResult(image=image, composite=comp)

        # Full render.
        blits = []
        for p in state.get_visible_players():
            token = await self._make_token(p, members, tile, color=_token_color(p.user_id))
            blits.append((p.x * tile, p.y * tile, token))
        zombie_blits = self._zombie_blits(
            zombies, 0, 0, map_data.width, map_data.height,
        )
        block_blits = self._block_blits(blocks, 0, 0, map_data.width, map_data.height)
        resource_blits = self._resource_blits(
            map_data, resource_tiles, 0, 0, map_data.width, map_data.height
        )
        terrain_blits = self._resource_blits(
            map_data, terrain_tiles, 0, 0, map_data.width, map_data.height
        )
        # Above-player tiles under an entity fade to 45% alpha (full path).
        occluded_tiles = frozenset(
            (p.x, p.y) for p in state.get_visible_players()
        ) | frozenset((z.x, z.y) for z in zombies if getattr(z, "alive", True))
        above_blits = self._above_player_blits(map_data, occluded_tiles)
        if hl_tile is not None and 0 <= hl_tile[0] < map_data.width and 0 <= hl_tile[1] < map_data.height:
            hl_px = (hl_tile[0] * tile, hl_tile[1] * tile)
            if ghost_img is not None:
                ghost_px = hl_px
        entity_blits = zombie_blits + blits
        entity_regions = (
            [(px, py, img.width, img.height, img.getchannel("A"))
             for px, py, img in entity_blits]
            if fx_active else None
        )
        comp, image = await run_image_task(
            self._compose_full, base, entity_blits, block_blits,
            terrain_blits + resource_blits,
            hl_px, tile, alpha_scale,
            (ghost_px[0], ghost_px[1], ghost_img) if ghost_px is not None else None,
            world_lights, lighting_sec, above_blits,
        )
        return await self._finish(
            comp, image, weather_key, fx_active, fx_seed, entity_regions
        )

    async def _finish(
        self,
        comp: Image.Image,
        image: Image.Image,
        weather_key: Optional[str],
        fx_active: bool,
        fx_seed: int = 0,
        entity_regions: Optional[list] = None,
    ) -> RenderResult:
        """Compose animated weather off the event loop."""
        if not fx_active:
            return RenderResult(image=image, composite=comp)
        return await run_image_task(
            self._finish_sync, comp, image, weather_key, fx_active, fx_seed,
            entity_regions,
        )

    def _finish_sync(
        self,
        comp: Image.Image,
        image: Image.Image,
        weather_key: Optional[str],
        fx_active: bool,
        fx_seed: int = 0,
        entity_regions: Optional[list] = None,
    ) -> RenderResult:
        """Worker-thread implementation of the weather-frame composition.

        ``image`` is the post-day/night RGB frame; overlays are full-viewport
        RGBA sheets composited per GIF frame. ``comp`` (the overlay-free
        composite) is returned untouched so incremental caching keeps working.
        """
        if not fx_active:
            return RenderResult(image=image, composite=comp)
        overlays = self.weather_fx.build_overlays(
            weather_key, image.size, n=WEATHER_FX_FRAMES, seed=fx_seed or 0
        )
        if len(overlays) < 2:
            return RenderResult(image=image, composite=comp)
        base_rgba = image.convert("RGBA")
        frames = [
            Image.alpha_composite(
                base_rgba, self._weather_overlay_behind_entities(ov, entity_regions)
            ).convert("RGB")
            for ov in overlays
        ]
        return RenderResult(
            image=frames[-1],
            filename="map.gif",
            content_type="image/gif",
            composite=comp,
            frames=frames,
            duration_ms=160,
        )

    @staticmethod
    def _weather_overlay_behind_entities(overlay, entity_regions=None):
        """Keep particles behind entity sprites instead of painting over them."""
        if not entity_regions:
            return overlay
        protected = Image.new("L", overlay.size, 0)
        draw = ImageDraw.Draw(protected)
        for region in entity_regions:
            if len(region) == 5:
                x, y, w, h, mask = region
                protected.paste(mask, (x, y))
            else:
                x, y, w, h = region
                draw.rectangle(
                    (x, y, x + max(0, w - 1), y + max(0, h - 1)), fill=255
                )
        alpha = ImageChops.multiply(overlay.getchannel("A"), ImageChops.invert(protected))
        out = overlay.copy()
        out.putalpha(alpha)
        return out

    @staticmethod
    def _local_light_sources(sources, ox: float, oy: float):
        return [
            (sx - ox, sy - oy, radius, intensity, color)
            for sx, sy, radius, intensity, color in sources
        ]

    @staticmethod
    def _coerce_light_sources(raw_sources, tile: int):
        """Convert optional renderer light tuples/dicts into pixel coordinates."""
        out = []
        for raw in raw_sources or ():
            try:
                if isinstance(raw, dict):
                    x, y = raw["x"], raw["y"]
                    radius = raw.get("radius_tiles", raw.get("radius", 0.0))
                    intensity = raw.get("intensity", 0.0)
                    color = tuple(raw.get("color", (255, 190, 92)))
                else:
                    x, y, radius, intensity, color = raw
                out.append((
                    float(x) * tile + tile / 2,
                    float(y) * tile + tile / 2,
                    float(radius) * tile,
                    float(intensity),
                    tuple(color),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _light_sources(state, blocks, tile: int):
        """Collect data-driven block and optional state light sources.

        State-provided sources use ``(x, y, radius_tiles, intensity, RGB)`` or
        a dict with those names. Blocks such as torches provide their own light
        metadata through ``BlockDef``; no map coordinates are hard-coded.
        """
        sources = []
        if blocks is not None:
            for (x, y), block_id in blocks.items():
                bdef = get_block(block_id)
                if bdef is None or bdef.light_radius <= 0 or bdef.light_intensity <= 0:
                    continue
                sources.append((
                    x * tile + tile / 2,
                    y * tile + tile / 2,
                    bdef.light_radius * tile,
                    bdef.light_intensity,
                    bdef.light_color,
                ))
        sources.extend(
            Renderer._coerce_light_sources(
                getattr(state, "light_sources", ()), tile
            )
        )
        return sources

    @staticmethod
    def _entity_lit_token(token, px, py, sources, sec=None):
        strength, color = light_strength_at(
            px + token.width / 2, py + token.height / 2, sources
        )
        return apply_entity_lighting(
            token, sec=sec, light_boost=strength, light_color=color
        )

    def _facing_tile(self, state: GameState, focus_user_id: Optional[int]) -> Optional[tuple]:
        """Tile the screen owner is looking at (where 🧱/🔨 land), or None."""
        if focus_user_id is None:
            return None
        p = state.get_player(focus_user_id)
        if p is None or not getattr(p, "direction", None):
            return None
        try:
            dx, dy = Direction[p.direction].vector
        except (KeyError, TypeError):
            return None
        return (p.x + dx, p.y + dy)

    def _faded_overlay_tile(self, map_data, gid: int) -> Optional[Image.Image]:
        """Cached 45%-alpha copy of ONE above-player overlay tile.

        The faded sprite is what players see while an entity stands on the
        tile: the overlay art dims instead of hiding the token beneath it
        (the classic RPG walk-under-canopy fade).
        """
        key = (map_data.map_id, gid)
        cached = self._faded_overlay_cache.get(key)
        if cached is not None:
            return cached
        sheet = self._sheet_for_gid(map_data, gid)
        if sheet is None:
            return None
        tileset = self._tileset_for_gid(map_data, gid) or {}
        cols = tileset.get("columns", 1)
        firstgid = tileset.get("firstgid", 1)
        tw = map_data.tile_width or self.tile_size
        th = map_data.tile_height or self.tile_size
        idx = gid - firstgid
        sx = (idx % cols) * tw
        sy = (idx // cols) * th
        img = sheet.crop((sx, sy, sx + tw, sy + th)).convert("RGBA")
        alpha = img.getchannel("A").point(lambda a: a * 45 // 100)
        img.putalpha(alpha)
        self._faded_overlay_cache[key] = img
        return img

    def _above_player_blits(self, map_data, occluded_tiles=None) -> list:
        """(px, py, tile_img) blits for "bên trên player" overlay layers.

        Viewport-agnostic full-map pixel coords; the compose workers just
        alpha-composite them after the entity tokens. Tiles listed in
        ``occluded_tiles`` (an entity is standing there) render their sprite
        at 45% alpha so the token stays visible through the art; every other
        tile keeps its full-opacity sprite. Empty when the sheet or such
        layers are missing (the common case).
        """
        sheet = self._sheet_for(map_data)
        if sheet is None and not getattr(map_data, "tilesets", None):
            return []
        occluded = frozenset(occluded_tiles or ())
        tw = map_data.tile_width or self.tile_size
        th = map_data.tile_height or self.tile_size
        out = []
        for _name, blits in self._above_layer_blits(map_data):
            for px, py, gid in blits:
                if (px // tw, py // th) in occluded:
                    img = self._faded_overlay_tile(map_data, gid)
                else:
                    img = self._crop_gid(map_data, gid)
                if img is not None:
                    out.append((px, py, img))
        return out

    def _tileset_for_gid(self, map_data: MapData, gid: int) -> Optional[dict]:
        """Tiled tileset entry covering ``gid`` (highest firstgid <= gid)."""
        best = None
        for ts in getattr(map_data, "tilesets", []) or []:
            if ts.get("firstgid", 1) <= gid and (
                best is None or ts["firstgid"] > best["firstgid"]
            ):
                best = ts
        return best or getattr(map_data, "tileset", None)

    def _crop_gid(self, map_data: MapData, gid: int) -> Optional[Image.Image]:
        """Cached 32x32 crop of any tile GID, picking the right sheet."""
        key = (map_data.map_id, gid)
        cached = self._gid_crop_cache.get(key)
        if cached is not None:
            return cached
        sheet = self._sheet_for_gid(map_data, gid)
        if sheet is None:
            return None
        tileset = self._tileset_for_gid(map_data, gid) or {}
        cols = tileset.get("columns", 1)
        firstgid = tileset.get("firstgid", 1)
        tw = map_data.tile_width or self.tile_size
        th = map_data.tile_height or self.tile_size
        idx = gid - firstgid
        sx = (idx % cols) * tw
        sy = (idx // cols) * th
        img = sheet.crop((sx, sy, sx + tw, sy + th)).copy()
        self._gid_crop_cache[key] = img
        return img

    def _above_layer_blits(self, map_data: MapData) -> list:
        """Cached ``(name, [(px, py, gid), ...])`` scan of above-player layers.

        The scan itself walks every cell of every above-player layer on each
        call (~1.5ms on bigmap); the result is static per map, so compute it
        once and reuse it for every frame.
        """
        cached = self._above_layer_cache.get(map_data.map_id)
        if cached is None:
            tw = map_data.tile_width or self.tile_size
            th = map_data.tile_height or self.tile_size
            cached = _overlay_layer_images(
                map_data, None, 1, 1, tw, th, want_above=True
            )
            self._above_layer_cache[map_data.map_id] = cached
        return cached

    def _compose_follow(self, base, x0, y0, tile, vw, vh, zoom, blits,
                        block_blits=None, resource_blits=None,
                        highlight=None, alpha_scale=1.0,
                        ghost=None, light_sources=None, lighting_sec=None,
                        above_blits=None):
        """Thread-worker: compose a raw scene and a gently lit display frame.

        The raw scene remains the cacheable ``composite``; the display frame
        lights terrain and entities through separate paths so avatar pixels are
        not multiplied by the strong terrain night tint.
        """
        # crop() already returns an independent image; no extra .copy() needed.
        scene = base.crop((x0 * tile, y0 * tile, x0 * tile + vw, y0 * tile + vh))
        for px, py, img in block_blits or []:
            scene.alpha_composite(img, (px, py))
        for px, py, img in resource_blits or []:
            scene.alpha_composite(img, (px, py))
        if ghost is not None:
            scene.alpha_composite(ghost[2], (ghost[0], ghost[1]))
        if highlight is not None:
            _draw_tile_highlight(scene, highlight[0], highlight[1], tile, alpha_scale)

        comp = scene.copy()
        image = apply_local_lighting(
            apply_daynight(scene, sec=lighting_sec), light_sources or ()
        )

        for px, py, token in blits:
            comp.alpha_composite(token, (px, py))
            lit_token = self._entity_lit_token(
                token, px, py, light_sources or (), lighting_sec
            )
            image.alpha_composite(lit_token, (px, py))
        if zoom != 1.0:
            zt = max(1, int(round(vw * zoom)))
            zh = max(1, int(round(vh * zoom)))
            comp = comp.resize((zt, zh), Image.NEAREST)
            image = image.resize((zt, zh), Image.NEAREST)
        return comp, image

    def _compose_full(self, base, blits, block_blits=None, resource_blits=None,
                      highlight=None, tile=32, alpha_scale=1.0, ghost=None,
                      light_sources=None, lighting_sec=None, above_blits=None):
        """Thread-worker: compose raw entities separately from lit terrain."""
        scene = base.copy()
        for px, py, img in block_blits or []:
            scene.alpha_composite(img, (px, py))
        for px, py, img in resource_blits or []:
            scene.alpha_composite(img, (px, py))
        if ghost is not None:
            scene.alpha_composite(ghost[2], (ghost[0], ghost[1]))
        if highlight is not None:
            _draw_tile_highlight(scene, highlight[0], highlight[1], tile, alpha_scale)

        comp = scene.copy()
        image = apply_local_lighting(
            apply_daynight(scene, sec=lighting_sec), light_sources or ()
        )

        for px, py, token in blits:
            comp.alpha_composite(token, (px, py))
            lit_token = self._entity_lit_token(
                token, px, py, light_sources or (), lighting_sec
            )
            image.alpha_composite(lit_token, (px, py))
        # Above-player overlay layers draw AFTER tokens (data-driven).
        for px, py, img in above_blits or []:
            comp.alpha_composite(img, (px, py))
            image.alpha_composite(img, (px, py))
        return comp, image


    def _patch_incremental(
        self, prev_composite, prev_pos, new_pos, tile, base, token,
        light_sources=None, lighting_sec=None,
    ):
        """Thread-worker: update raw scene and rebuild the safe display frame."""
        comp = prev_composite.copy()
        ox, oy = prev_pos
        old_tile = base.crop((ox * tile, oy * tile, (ox + 1) * tile, (oy + 1) * tile))
        comp.alpha_composite(old_tile, (ox * tile, oy * tile))
        comp.alpha_composite(token, (new_pos[0] * tile, new_pos[1] * tile))

        image = apply_local_lighting(
            apply_daynight(comp, sec=lighting_sec), light_sources or ()
        )
        # The ambient pass started from the raw scene, so restore the mover's
        # old tile before placing its separately lit sprite at the new tile.
        image.alpha_composite(
            apply_local_lighting(
                apply_daynight(old_tile, sec=lighting_sec), light_sources or ()
            ),
            (ox * tile, oy * tile),
        )
        new_tile = base.crop((
            new_pos[0] * tile, new_pos[1] * tile,
            (new_pos[0] + 1) * tile, (new_pos[1] + 1) * tile,
        ))
        image.alpha_composite(
            apply_local_lighting(
                apply_daynight(new_tile, sec=lighting_sec), light_sources or ()
            ),
            (new_pos[0] * tile, new_pos[1] * tile),
        )
        lit_token = self._entity_lit_token(
            token, new_pos[0] * tile, new_pos[1] * tile,
            light_sources or (), lighting_sec
        )
        image.alpha_composite(lit_token, (new_pos[0] * tile, new_pos[1] * tile))
        return comp, image

    @staticmethod
    def _tiles_overlap_other_player(
        state: GameState, mover_id: int, prev_pos: tuple, new_pos: tuple
    ) -> bool:
        for p in state.get_visible_players():
            if p.user_id == mover_id:
                continue
            if (p.x, p.y) == prev_pos or (p.x, p.y) == new_pos:
                return True
        return False

    async def _make_token(self, p, members, tile, color) -> Image.Image:
        # Token images depend on (player, facing, tile size) only; consecutive
        # frames of a moving player hit this cache (see _token_cache).
        direction = getattr(p, "direction", None)
        key = (p.user_id, direction, tile)
        cached = self._token_cache.get(key)
        if cached is not None:
            return cached
        member = members.get(p.user_id) if members else None
        # Sprite-aware: the player's chosen avatar (bundled pack / server
        # emoji) replaces the Discord-avatar/face token entirely. Use the
        # Player as a small user-like fallback so a missing member cache does
        # not silently replace a selected sprite with the generic face.
        sprite_id = getattr(p, "sprite_id", "") or ""
        token = await self.avatar_cache.get_avatar(
            member or p, label=p.display_name[:1], default_color=color,
            sprite_id=sprite_id,
        )
        # Keep the default (bicubic) resampler here: avatar/emoji art is
        # anti-aliased, and NEAREST downscale (48->32) jags the silhouette,
        # which then reads as blur after the 0.5x upload shrink.
        token = token.resize((tile, tile)).convert("RGBA")
        # Small facing bead so the player can read their own orientation.
        if direction:
            _draw_facing_dot(token, direction, tile)
        if len(self._token_cache) >= self._token_cache_max:
            self._token_cache.clear()
        self._token_cache[key] = token
        return token

    def _base_layer(self, map_data: MapData, w: int, h: int, tile: int,
                    excluded: set = frozenset()) -> Image.Image:
        """Render the map ground WITHOUT the harvestable resource layers or
        the grass-tuft layers (both are drawn as overlays so chopped trees
        can vanish and scooped grass can reveal the bare-dirt base)."""
        cache_key = (map_data.map_id, tuple(sorted(excluded)))
        cached = self._base_cache.get(cache_key)
        if cached is not None and cached.size == (w, h):
            return cached
        if map_data.image_path and Path(map_data.image_path).exists():
            base = Image.open(map_data.image_path).convert("RGBA").resize((w, h))
            self._base_cache[cache_key] = base
            return base

        base = Image.new("RGBA", (w, h), (30, 40, 60, 255))
        tw = map_data.tile_width
        th = map_data.tile_height
        sheet = self._sheet_for(map_data)

        if sheet is not None or getattr(map_data, "tilesets", None):
            multi = bool(getattr(map_data, "tilesets", None))
            for name, grid in map_data.tile_layers:
                if (name or "").strip().lower() in excluded:
                    continue
                for y, row in enumerate(grid):
                    for x, gid in enumerate(row):
                        if gid == 0:
                            continue
                        if multi:
                            tile_img = self._crop_gid(map_data, gid)
                            if tile_img is None:
                                continue
                        else:
                            tileset = map_data.tileset
                            cols = tileset["columns"]
                            firstgid = tileset["firstgid"]
                            idx = gid - firstgid
                            sx = (idx % cols) * tw
                            sy = (idx // cols) * th
                            tile_img = sheet.crop((sx, sy, sx + tw, sy + th))
                        base.paste(tile_img, (x * tw, y * th), tile_img)
        else:
            # No tileset PNG yet: draw gray blocks where any tile exists.
            draw = ImageDraw.Draw(base)
            if map_data.tile_layers:
                for name, grid in map_data.tile_layers:
                    if (name or "").strip().lower() in excluded:
                        continue
                    for y, row in enumerate(grid):
                        for x, gid in enumerate(row):
                            if gid != 0:
                                draw.rectangle(
                                    [x * tile, y * tile, (x + 1) * tile, (y + 1) * tile],
                                    fill=(90, 90, 90, 255),
                                )
            else:
                for y in range(map_data.height):
                    for x in range(map_data.width):
                        if not map_data.is_walkable(x, y):
                            draw.rectangle(
                                [x * tile, y * tile, (x + 1) * tile, (y + 1) * tile],
                                fill=(90, 90, 90, 255),
                            )
        self._base_cache[cache_key] = base
        return base

    @staticmethod
    def to_bytes(result: RenderResult) -> io.BytesIO:
        buf = io.BytesIO()
        result.image.save(buf, "PNG")
        buf.seek(0)
        return buf
