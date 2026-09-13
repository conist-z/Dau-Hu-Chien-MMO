import asyncio
import io
import json
import logging
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import aiohttp
from PIL import Image, ImageDraw

log = logging.getLogger("GAME")

# sprite_id prefixes (stored on Player.sprite_id, persisted in SQLite):
#   ""           -> legacy behavior (Discord avatar if use_network, else face)
#   "twe:<hex>"  -> bundled default pack PNG under assets/avatars/twemoji/
#   "emoji:<id>" -> server emoji image, disk cache under data/avatar_cache/
TWE_PREFIX = "twe:"
EMOJI_PREFIX = "emoji:"


def is_valid_sprite_id(sprite_id: str) -> bool:
    """True when ``sprite_id`` is a known format (empty = default is valid)."""
    return sprite_id == "" or sprite_id.startswith((TWE_PREFIX, EMOJI_PREFIX))


class AvatarCache:
    """Renders a player token.

    Sprite sources (``player.sprite_id``):
    - ``""`` (default): Discord avatar if ``use_network`` else the drawn
      "face" token (works with no network/font).
    - ``twe:<codepoint>``: bundled default-avatar PNG from the Twemoji pack
      (assets/avatars/twemoji/, shipped with the bot — no runtime network).
    - ``emoji:<id>``: server custom emoji. Resolved via the injected
      ``emoji_fetcher`` coroutine (discord.py, set by bot.py) and cached as
      a PNG file on disk — never in SQLite (rule 19). On any failure the
      legacy face token is used instead (never swallow, always warn).

    Never blocks the event loop: all network I/O is async. Tokens are cached
    per ``(user_id, sprite_id)`` so switching avatars shows up immediately.
    """

    def __init__(
        self,
        ttl_seconds: int = 600,
        size: int = 48,
        use_network: bool = False,
        avatars_dir: Optional[Path] = None,
        emoji_cache_dir: Optional[Path] = None,
        emoji_fetcher: Optional[Callable] = None,
    ):
        # Backwards-compatible shorthand used by the renderer/tests:
        # ``AvatarCache(assets_dir)`` means the bundled avatar directory, not a
        # TTL. Keep the explicit keyword form available for runtime callers.
        if isinstance(ttl_seconds, (str, Path)) and avatars_dir is None:
            avatars_dir = Path(ttl_seconds)
            ttl_seconds = 600
        self.ttl = float(ttl_seconds)
        self.size = size
        self.use_network = use_network
        self.avatars_dir = Path(avatars_dir) if avatars_dir else None
        self.emoji_cache_dir = Path(emoji_cache_dir) if emoji_cache_dir else None
        self.emoji_fetcher = emoji_fetcher
        # (user_id, sprite_id) -> (cached_at, token_image)
        self._cache: Dict[Tuple[int, str], Tuple[float, Image.Image]] = {}
        self._emoji_fetch_locks: Dict[str, asyncio.Lock] = {}
        self._manifest: Optional[list] = None

    # ----- manifest (bundled default avatars) -----

    def _load_manifest(self) -> list:
        if self._manifest is not None:
            return self._manifest
        self._manifest = []
        if self.avatars_dir is None:
            return self._manifest
        mf = self.avatars_dir / "manifest.json"
        if not mf.is_file():
            log.warning("[AVATAR] manifest.json missing in %s", self.avatars_dir)
            return self._manifest
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            self._manifest = list(data.get("avatars", []))
        except Exception as e:  # noqa: BLE001 — a broken manifest must not crash renders
            log.warning("[AVATAR] cannot read manifest %s: %s", mf, e)
        return self._manifest

    def default_avatars(self) -> list:
        """Bundled default avatar entries (id/unicode/label/category)."""
        return list(self._load_manifest())

    def manifest_meta(self) -> dict:
        """Manifest metadata (categories, category_labels, attribution)."""
        if self.avatars_dir is None:
            return {}
        mf = self.avatars_dir / "manifest.json"
        if not mf.is_file():
            return {}
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("[AVATAR] cannot read manifest %s: %s", mf, e)
            return {}
        return {
            "categories": data.get("categories", []),
            "category_labels": data.get("category_labels", {}),
            "attribution": data.get("attribution", ""),
        }

    def invalidate(self, user_id: int) -> None:
        """Drop every cached token variant of ``user_id`` (after avatar change)."""
        for key in [k for k in self._cache if k[0] == user_id]:
            self._cache.pop(key, None)

    # ----- public API -----

    async def get_avatar(
        self, user, label: Optional[str] = None, default_color=(120, 160, 220),
        sprite_id: str = "",
    ) -> Image.Image:
        """Token for ``user`` honoring ``sprite_id`` (default = legacy)."""
        if not is_valid_sprite_id(sprite_id or ""):
            log.warning("[AVATAR] unknown sprite_id %r — using default", sprite_id)
            sprite_id = ""
        uid = getattr(user, "id", None)
        if uid is None:
            uid = getattr(user, "user_id", 0)
        now = time.time()
        cached = self._cache.get((uid, sprite_id))
        if cached and (now - cached[0]) < self.ttl:
            return cached[1]
        token = await self._render_token(user, label, default_color, sprite_id or "")
        self._cache[(uid, sprite_id)] = (now, token)
        return token

    def fallback(self, label: Optional[str] = None, color=(120, 160, 220)) -> Image.Image:
        return self._draw_face(color)

    # ----- token rendering per source -----

    async def _render_token(
        self, user, label, default_color, sprite_id: str,
    ) -> Image.Image:
        if sprite_id.startswith(TWE_PREFIX):
            img = self._bundled_png(sprite_id[len(TWE_PREFIX):])
            if img is not None:
                return img
            log.warning("[AVATAR] bundled avatar %r not found — face fallback", sprite_id)
            return self._draw_face(default_color)
        if sprite_id.startswith(EMOJI_PREFIX):
            emoji_id = sprite_id[len(EMOJI_PREFIX):]
            img = await self._emoji_png(emoji_id)
            if img is not None:
                return img
            log.warning("[AVATAR] server emoji %r unavailable — face fallback", sprite_id)
            return self._draw_face(default_color)
        # Legacy: real Discord avatar (opt-in) or the drawn face token.
        if not self.use_network:
            return self._draw_face(default_color)
        url = self._avatar_url(user)
        return await self._download(url, default_color)

    def _bundled_png(self, codepoint: str) -> Optional[Image.Image]:
        if self.avatars_dir is None:
            return None
        p = self.avatars_dir / "twemoji" / f"{codepoint}.png"
        if not p.is_file():
            return None
        try:
            img = Image.open(p).convert("RGBA")
        except Exception as e:  # noqa: BLE001 — corrupt asset must not crash a render
            log.warning("[AVATAR] cannot open %s: %s", p, e)
            return None
        if img.size != (self.size, self.size):
            img = img.resize((self.size, self.size), Image.NEAREST)
        return img

    async def _emoji_png(self, emoji_id: str) -> Optional[Image.Image]:
        """Server emoji token: disk cache first, fetch through the injected
        coroutine on miss, face fallback on any failure."""
        if not emoji_id or not self.emoji_cache_dir:
            return None
        cache_path = self.emoji_cache_dir / f"{emoji_id}.png"
        if cache_path.is_file():
            try:
                img = Image.open(cache_path).convert("RGBA")
            except Exception as e:  # noqa: BLE001 — corrupt cache entry: refetch
                log.warning("[AVATAR] corrupt emoji cache %s: %s", cache_path, e)
                cache_path.unlink(missing_ok=True)
            else:
                if img.size != (self.size, self.size):
                    img = img.resize((self.size, self.size), Image.NEAREST)
                return img
        if self.emoji_fetcher is None:
            return None
        lock = self._emoji_fetch_locks.setdefault(emoji_id, asyncio.Lock())
        async with lock:
            # Another task may have finished the fetch while we awaited the lock.
            if cache_path.is_file():
                try:
                    img = Image.open(cache_path).convert("RGBA")
                    if img.size != (self.size, self.size):
                        img = img.resize((self.size, self.size), Image.NEAREST)
                    return img
                except Exception:  # noqa: BLE001
                    cache_path.unlink(missing_ok=True)
            try:
                data = await self.emoji_fetcher(emoji_id)
            except Exception as e:  # noqa: BLE001 — unresolvable emoji -> fallback
                log.warning("[AVATAR] emoji fetch %s failed: %s", emoji_id, e)
                return None
            if not data:
                return None
            try:
                img = Image.open(io.BytesIO(data)).convert("RGBA")
            except Exception as e:  # noqa: BLE001
                log.warning("[AVATAR] emoji %s not an image: %s", emoji_id, e)
                return None
            try:
                self.emoji_cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(data)
            except OSError as e:
                log.warning("[AVATAR] emoji cache write failed: %s", e)
            if img.size != (self.size, self.size):
                img = img.resize((self.size, self.size), Image.NEAREST)
            return img

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
