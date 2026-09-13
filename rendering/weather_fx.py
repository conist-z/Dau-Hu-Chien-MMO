"""Weather particle overlays for the top-down map screen.

Each animated weather key has its own visual identity:

- rain / heavy_rain / storm: slanted pixel streaks (fast, dense, plus a mood
  tint), drawn big enough to survive the 0.5 upload shrink and the 64-colour
  GIF palette.
- snow / cold: chunky diamond flakes falling slowly.
- wind: long horizontal gust dashes.
- storm lightning is an EVENT, not part of the loop: the base storm GIF has no
  bolt; ``GameManager._lightning_loop`` fires every 5-13 s, randomises a seed
  (position / size / flip / distant-flicker) and re-renders the screens, so
  strikes never repeat in the same spot at the same size.

Particles are drawn (seeded RNG) into tileable master sheets; two layers
scroll at different phases so the field reads organic. The loop is seamless
because ``n_frames * speed`` wraps the master height exactly.

Pure PIL: no discord.py, no state mutation (rules #2/#3).
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

ANIMATED_KEYS = {"rain", "heavy_rain", "snow", "cold", "wind", "storm"}

# GIF frames per weather loop (renderer composes this many).
FX_FRAMES = 6


@dataclass(frozen=True)
class _Style:
    near: int          # particle count, near layer
    far: int           # particle count, far layer (drawn fainter)
    speed: int         # scroll px per frame (n * speed wraps master height)
    colors: tuple      # candidate particle colours (pack palette)
    alpha: tuple       # (min, max) per-particle alpha
    length: tuple      # streak length / flake radius range
    width: tuple       # streak thickness range
    slant: tuple       # horizontal drift across one streak
    tint: tuple        # full-screen mood tint (RGBA), applied every frame
    master: tuple      # master sheet (w, h)


_STYLES: Dict[str, _Style] = {
    # Near and far layers intentionally use different densities and fields.
    # This keeps effects readable instead of drawing the same particles twice.
    "rain": _Style(8, 3, 32, ((196, 219, 255), (170, 200, 255)), (42, 86),
                   (12, 24), (1, 1), (0, 1), (12, 18, 34, 7), (512, 192)),
    "heavy_rain": _Style(14, 6, 48, ((206, 226, 255), (186, 211, 255)), (58, 106),
                         (18, 32), (1, 2), (1, 3), (8, 12, 22, 12), (512, 192)),
    "storm": _Style(14, 6, 48, ((202, 224, 255), (180, 207, 255)), (64, 114),
                    (20, 36), (2, 3), (2, 4), (5, 9, 18, 16), (512, 192)),
    "snow": _Style(6, 3, 16, ((248, 250, 255), (232, 240, 255)), (58, 106),
                   (2, 3), (2, 3), (0, 0), (250, 252, 255, 4), (512, 192)),
    "cold": _Style(4, 1, 16, ((228, 240, 255), (210, 228, 252)), (46, 82),
                   (1, 2), (1, 2), (0, 0), (185, 214, 255, 6), (512, 192)),
    "wind": _Style(10, 4, 0, ((248, 252, 255), (232, 240, 252), (212, 230, 250)), (52, 100),
                   (16, 44), (1, 2), (0, 0), (238, 245, 255, 4), (1024, 256)),
}

# Stable per-key RNG seeds: chaotic LOOK, reproducible builds (rule #9).
_MASTER_SEEDS: Dict[str, int] = {
    "rain": 101, "heavy_rain": 102, "storm": 103, "snow": 104, "cold": 105, "wind": 106,
}


def _scale_alpha(img: Image.Image, factor: float) -> Image.Image:
    """Multiply an RGBA image's alpha channel by ``factor`` (clamped)."""
    r, g, b, a = img.split()
    a = a.point(lambda v: int(v * factor))
    return Image.merge("RGBA", (r, g, b, a))


class WeatherFx:
    """Builds full-viewport weather overlays (drawn sprites + pack palette)."""

    def __init__(self, assets_dir: Path):
        # Renderer/HubRenderer receive ``assets/maps``; FX live next to gui/.
        self.root = Path(assets_dir).parent / "fx"
        self._rain = self._load_seq("rain", "rain_*.png")
        self._snow = self._load_seq("snow", "snow_tile.png")
        self._wind = self._load_seq("wind", "wind_*.png")
        self._bolts = self._load_seq("thunder", "bolt_*.png")
        self._master_cache: Dict[str, Tuple[Image.Image, Image.Image]] = {}
        # All screens normally share one weather key and viewport size. Keep
        # only the current overlay set: this avoids rebuilding the same full
        # viewport for every player without retaining many large RGBA frames.
        self._overlay_cache_key = None
        self._overlay_cache_frames: Optional[Tuple[Image.Image, ...]] = None
        self._overlay_cache_lock = threading.Lock()

    def _load_seq(self, sub: str, pattern: str) -> List[Image.Image]:
        d = self.root / sub
        if not d.is_dir():
            return []
        frames = []
        for p in sorted(d.glob(pattern)):
            try:
                frames.append(Image.open(p).convert("RGBA"))
            except Exception:  # corrupt frame -> skip it, keep the rest
                continue
        return frames

    # ------------------------------------------------------------------ API

    @staticmethod
    def is_animated(key: Optional[str]) -> bool:
        return key in ANIMATED_KEYS

    def available(self) -> bool:
        """True when at least the rain sheet loaded (the primary effect)."""
        return bool(self._rain)

    def missing_sets(self) -> List[str]:
        """Names of FX sets that failed to load (for /weatherinfo diagnosis)."""
        out = []
        if not self._rain:
            out.append("rain")
        if not self._snow:
            out.append("snow")
        if not self._wind:
            out.append("wind")
        if not self._bolts:
            out.append("thunder")
        return out

    def build_overlays(
        self, key: Optional[str], size: Tuple[int, int], n: int = FX_FRAMES,
        seed: int = 0,
    ) -> List[Image.Image]:
        """Return ``n`` full-viewport RGBA overlays for ``key``.

        Empty list for non-animated keys. ``seed`` (from the manager's
        lightning loop) spawns one storm lightning strike into the frames;
        without it the storm loop is pure rain + mood tint (no flashbang).
        """
        if not self.is_animated(key) or n <= 0:
            return []
        style = _STYLES.get(key)
        if style is None:
            return []
        # Dense fast keys use fewer frames (smaller GIF); slow keys use more
        # frames so their master sheet can be taller (less visible repetition).
        if key in ("heavy_rain", "storm"):
            n = min(n, 4)
        elif key in ("snow", "cold"):
            n = max(n, 12)
        cache_key = (key, size, n, seed if key == "storm" else 0)
        # Build under the lock so simultaneous player renders do not all spend
        # hundreds of milliseconds generating the same viewport overlays.
        with self._overlay_cache_lock:
            if self._overlay_cache_key == cache_key and self._overlay_cache_frames is not None:
                return list(self._overlay_cache_frames)
            frames = self._scroll_overlays(size, n, key, style)
            if key == "storm":
                frames = self._add_bolt(frames, size, seed)
            self._overlay_cache_key = cache_key
            self._overlay_cache_frames = tuple(frames)
            return list(self._overlay_cache_frames)

    # ------------------------------------------------------- random masters

    def _rng(self, key: str, layer: int) -> random.Random:
        return random.Random(_MASTER_SEEDS.get(key, 0) * 10 + layer)

    def _draw_particle(
        self, draw: ImageDraw.ImageDraw, key: str, style: _Style,
        rng: random.Random, mw: int, mh: int,
    ) -> None:
        """Stamp one particle with wrap-around (4 copies) so the master
        sheet tiles perfectly in both axes."""
        x, y = rng.randrange(mw), rng.randrange(mh)
        color = rng.choice(style.colors) + (rng.randint(*style.alpha),)
        for ox in (0, -mw):
            for oy in (0, -mh):
                px, py = x + ox, y + oy
                if key in ("snow", "cold"):
                    r = rng.randint(*style.length)
                    draw.polygon([(px, py - r), (px + r, py), (px, py + r), (px - r, py)],
                                 fill=color)
                elif key == "wind":
                    ln = rng.randint(*style.length)
                    wd = rng.randint(*style.width)
                    draw.line([(px, py), (px + ln, py)], fill=color, width=wd)
                    # Fainter tail behind the dash (gusts drift RIGHT, so the
                    # tail trails LEFT) so each dash reads as motion, not noise.
                    draw.line([(px - ln // 2, py), (px, py)],
                              fill=color[:3] + (max(12, color[3] // 3),), width=wd)
                else:  # falling streaks
                    ln = rng.randint(*style.length)
                    draw.line([(px, py), (px + rng.randint(*style.slant), py + ln)],
                              fill=color, width=rng.randint(*style.width))

    def _get_masters(self, key: str, style: _Style) -> Tuple[Image.Image, Image.Image]:
        """(near, far) master sheets for a key: seeded random particle fields,
        drawn once and cached. Far layer = same field, different phase+fainter."""
        if key not in self._master_cache:
            near = Image.new("RGBA", style.master, (0, 0, 0, 0))
            dnear = ImageDraw.Draw(near)
            rng_near = self._rng(key, 0)
            for _ in range(style.near):
                self._draw_particle(dnear, key, style, rng_near, *style.master)

            # Build a genuinely independent far field. Reusing the near sheet
            # made every streak/flake appear twice at the same coordinates,
            # which read as muddy noise after Discord's upload quantization.
            far = Image.new("RGBA", style.master, (0, 0, 0, 0))
            dfar = ImageDraw.Draw(far)
            rng_far = self._rng(key, 1)
            for _ in range(style.far):
                self._draw_particle(dfar, key, style, rng_far, *style.master)

            # Apply layer opacity once to the small master sheets. Scaling a
            # full viewport on every frame was the dominant CPU cost here.
            near = _scale_alpha(near, 0.72)
            far = _scale_alpha(far, 0.34)
            self._master_cache[key] = (near, far)
        return self._master_cache[key]

    @staticmethod
    def _tile_paste(canvas: Image.Image, sheet: Image.Image, ox: int, oy: int) -> None:
        """Tile ``sheet`` across ``canvas``, wrapped by the sheet size.

        Increasing ``oy`` shifts the sheet DOWN and increasing ``ox`` shifts
        it RIGHT (the paste origin grows with the offset), so particles fall
        toward the ground as frames advance.

        Uses alpha_composite, NOT paste(sheet, pos, sheet): pasting an RGBA
        sheet through its own alpha mask squares the alpha (a dash at 90
        lands at ~32), which made sparse effects like wind invisible."""
        w, h = sheet.size
        y = (oy % h) - h
        while y < canvas.height:
            x = (ox % w) - w
            while x < canvas.width:
                canvas.alpha_composite(sheet, (x, y))
                x += w
            y += h

    def _scroll_overlays(
        self, size: Tuple[int, int], n: int, key: str, style: _Style
    ) -> List[Image.Image]:
        """Compose ``n`` frames: two master layers scrolled at different
        phases + the mood tint. Seamless by construction (n * speed wraps
        the master height)."""
        near, far = self._get_masters(key, style)
        mh = near.height
        tint = Image.new("RGBA", size, style.tint)
        out = []
        for i in range(n):
            if key == "wind":
                base, rem = divmod(near.width, n)
                offsets = [base + (1 if j < rem else 0) for j in range(n)]
                ox, oy = sum(offsets[:i]), 0
                # Far layer drifts slower (parallax) at a different height.
                far_ox, far_oy = int(ox * 0.6) + 512, 96
            else:
                ox, oy = 0, (style.speed * i) % mh
                far_ox, far_oy = 137, oy + mh // 3
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            # The master sheets already carry their layer opacity, so paste
            # directly into the output and avoid two full-size intermediate
            # canvases plus two full-size alpha-channel transforms per frame.
            self._tile_paste(canvas, far, far_ox, far_oy)
            self._tile_paste(canvas, near, ox, oy)
            # Mood tint is kept as a low-opacity third layer. It never changes
            # particle alpha and therefore cannot turn a sparse effect into a
            # full-screen noisy veil.
            canvas.alpha_composite(tint)
            out.append(canvas)
        return out

    # ------------------------------------------------------------ lightning

    def _add_bolt(
        self, frames: List[Image.Image], size: Tuple[int, int], seed: int
    ) -> List[Image.Image]:
        """Storm lightning EVENT (requires a seed from the manager's lightning
        loop). Position, size, mirror and frame timing all derive from the
        seed, so no two strikes look alike. ~35% of strikes are "distant":
        a soft flicker with no bolt at all. The flash is a gentle brighten,
        never the old full-screen white flashbang."""
        if not seed or not self._bolts:
            return frames
        rng = random.Random(seed)
        bolt = self._bolts[rng.randrange(len(self._bolts))].copy()
        scale = rng.uniform(0.55, 1.2)
        bw = max(2, int(bolt.width * scale))
        bh = max(4, int(bolt.height * scale))
        bolt = bolt.resize((bw, bh), Image.NEAREST)
        if rng.random() < 0.5:
            bolt = bolt.transpose(Image.FLIP_LEFT_RIGHT)
        bx = rng.randrange(0, max(1, size[0] - bw))
        by = rng.randrange(0, max(1, int(size[1] * 0.22)))
        glow = _scale_alpha(bolt.filter(ImageFilter.GaussianBlur(4)), 0.9)
        strike = rng.randrange(1, max(2, len(frames) - 2))
        soft = Image.new("RGBA", size, (232, 240, 255, 11))
        faint = Image.new("RGBA", size, (232, 240, 255, 5))
        out = list(frames)
        nxt = Image.alpha_composite(out[strike + 1].convert("RGBA"), faint)
        if rng.random() < 0.35:  # distant strike: flicker only, no bolt
            out[strike] = Image.alpha_composite(out[strike].convert("RGBA"), soft)
            out[strike + 1] = nxt
            return out
        f = out[strike].copy()
        f.alpha_composite(glow, (bx, by))
        f.alpha_composite(bolt, (bx, by))
        f.alpha_composite(soft)
        out[strike] = f
        out[strike + 1] = nxt
        return out

