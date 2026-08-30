import io
import time
from typing import Dict, Optional, Tuple

import aiohttp
from PIL import Image, ImageDraw

from game.state import Player


class AvatarCache:
    """Renders a player token.

    - If the Discord avatar downloads successfully, it is used (circular mask).
    - Otherwise a font-free "face" token is drawn (works with no network/font).

    Never blocks the event loop: aiohttp is async. Tokens are cached per user.
    """

    def __init__(self, ttl_seconds: int = 600, size: int = 48, use_network: bool = False):
        self.ttl = ttl_seconds
        self.size = size
        self.use_network = use_network
        self._cache: Dict[int, Tuple[float, Image.Image, str]] = {}

    async def get_avatar(self, user, label: Optional[str] = None, default_color=(120, 160, 220)) -> Image.Image:
        if not self.use_network:
            return self.fallback(label, default_color)
        url = self._avatar_url(user)
        now = time.time()
        cached = self._cache.get(user.id)
        if cached and (now - cached[0]) < self.ttl and cached[2] == url:
            return cached[1]
        img = await self._download(url, default_color)
        self._cache[user.id] = (now, img, url)
        return img

    def fallback(self, label: Optional[str] = None, color=(120, 160, 220)) -> Image.Image:
        return self._draw_face(color)

    @staticmethod
    def _avatar_url(user) -> str:
        av = getattr(user, "display_avatar", None) or getattr(user, "avatar", None)
        if av is None:
            return ""
        return str(av.url) if hasattr(av, "url") else str(av)

    async def _download(self, url: str, default_color) -> Image.Image:
        if not url:
            return self.fallback(None, default_color)
        try:
            timeout = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, timeout=timeout) as resp:
                    data = await resp.read()
            avatar = Image.open(io.BytesIO(data)).convert("RGBA").resize((self.size, self.size))
            return self._make_circle(avatar)
        except Exception:
            return self.fallback(None, default_color)

    @staticmethod
    def _make_circle(img: Image.Image) -> Image.Image:
        size = img.size[0]
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        img.putalpha(mask)
        return img

    def _draw_face(self, color) -> Image.Image:
        s = self.size
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        m = int(s * 0.10)
        d.ellipse([m, m, s - m, s - m], fill=color + (255,),
                  outline=(255, 255, 255, 255), width=max(1, s // 16))
        ey = int(s * 0.40)
        edx = int(s * 0.18)
        er = max(2, int(s * 0.07))
        cx = s // 2
        for sx in (-1, 1):
            d.ellipse([cx + sx * edx - er, ey - er, cx + sx * edx + er, ey + er],
                      fill=(255, 255, 255, 255))
            d.ellipse([cx + sx * edx - 1, ey - 1, cx + sx * edx + 1, ey + 1],
                      fill=(0, 0, 0, 255))
        d.arc([cx - int(s * 0.18), int(s * 0.55), cx + int(s * 0.18), int(s * 0.72)],
              20, 160, fill=(255, 255, 255, 255), width=max(1, s // 20))
        return img
