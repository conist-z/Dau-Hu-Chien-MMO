import asyncio
import pathlib
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PIL import Image

from rendering.avatar import AvatarCache, is_valid_sprite_id

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"
AVATARS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "avatars"


class FakeUser:
    def __init__(self, uid=10):
        self.id = uid


def run(coro):
    return asyncio.run(coro)


# --- sprite_id validation ---

def test_sprite_id_validation():
    assert is_valid_sprite_id("")
    assert is_valid_sprite_id("twe:1f436")
    assert is_valid_sprite_id("emoji:1234567890")
    assert not is_valid_sprite_id("bogus:1")
    assert not is_valid_sprite_id("twe")


# --- bundled twemoji pack ---

def test_bundled_avatar_loads():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    img = cache._bundled_png("1f436")  # dog face ships with the pack
    assert img is not None
    assert img.size == (32, 32)
    assert img.mode == "RGBA"
    # Twemoji art has transparent corners (the dog face is round).
    assert img.getpixel((0, 0))[3] == 0
    assert img.getpixel((16, 16))[3] > 0  # opaque centre


def test_bundled_avatar_missing_returns_none():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    assert cache._bundled_png("ffff") is None
    # No avatars dir configured at all -> None, never a crash.
    assert AvatarCache(size=32)._bundled_png("1f436") is None


def test_default_avatars_manifest():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    entries = cache.default_avatars()
    assert len(entries) > 0
    first = entries[0]
    assert first["id"].startswith("twe:")
    assert "category" in first and "label" in first
    cats = {e["category"] for e in entries}
    assert {"animals", "faces", "food", "fantasy"} <= cats


def test_manifest_meta():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    meta = cache.manifest_meta()
    assert "animals" in meta["category_labels"]
    assert "CC-BY" in meta["attribution"]
    assert AvatarCache(size=32).manifest_meta() == {}


# --- token rendering per sprite ---

def test_token_uses_bundled_avatar():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    token = run(cache.get_avatar(FakeUser(), sprite_id="twe:1f436"))
    # Token IS the twemoji art (transparent corner proves it's not the face).
    assert token.getpixel((0, 0))[3] == 0


def test_token_falls_back_to_face_for_unknown_sprite():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    face = cache.fallback("A", color=(120, 160, 220))  # same default color
    token = run(cache.get_avatar(FakeUser(), sprite_id="twe:does-not-exist"))
    assert list(token.getdata()) == list(face.getdata())


def test_unknown_sprite_id_format_coerces_to_default():
    cache = AvatarCache(use_network=False, size=32)
    token = run(cache.get_avatar(FakeUser(), sprite_id="hacker:stuff"))
    face = cache.fallback("A")
    assert list(token.getdata()) == list(face.getdata())


def test_default_sprite_without_network_is_face_token():
    cache = AvatarCache(use_network=False, size=32)
    token = run(cache.get_avatar(FakeUser(), sprite_id=""))
    face = cache.fallback("A")
    assert list(token.getdata()) == list(face.getdata())


# --- server emoji source ---

def test_emoji_token_disk_cache(tmp_path):
    cache = AvatarCache(
        use_network=False, size=32,
        emoji_cache_dir=tmp_path, emoji_fetcher=AsyncMock(return_value=None),
    )
    # Pre-seed the disk cache: fetcher must never be called.
    img = Image.new("RGBA", (16, 16), (250, 120, 40, 255))
    img.save(tmp_path / "999.png")
    token = run(cache.get_avatar(FakeUser(), sprite_id="emoji:999"))
    assert token.size == (32, 32)
    assert token.getpixel((16, 16))[:3] == (250, 120, 40)
    cache.emoji_fetcher.assert_not_awaited()


def test_emoji_token_fetched_once_then_cached(tmp_path):
    png = Image.new("RGBA", (24, 24), (10, 200, 90, 255))
    import io as _io
    buf = _io.BytesIO()
    png.save(buf, "PNG")
    data = buf.getvalue()

    fetcher = AsyncMock(return_value=data)
    cache = AvatarCache(
        use_network=False, size=32,
        emoji_cache_dir=tmp_path, emoji_fetcher=fetcher,
    )
    token = run(cache.get_avatar(FakeUser(), sprite_id="emoji:777"))
    assert token.getpixel((16, 16))[:3] == (10, 200, 90)
    assert (tmp_path / "777.png").exists()
    # Second token (different user id so the token cache misses) hits disk.
    run(cache.get_avatar(FakeUser(11), sprite_id="emoji:777"))
    fetcher.assert_awaited_once()


def test_emoji_token_fetch_failure_is_face_fallback(tmp_path):
    fetcher = AsyncMock(side_effect=RuntimeError("no egress"))
    cache = AvatarCache(
        use_network=False, size=32,
        emoji_cache_dir=tmp_path, emoji_fetcher=fetcher,
    )
    token = run(cache.get_avatar(FakeUser(), sprite_id="emoji:404"))
    face = cache.fallback("A")
    assert list(token.getdata()) == list(face.getdata())


def test_emoji_token_without_fetcher_is_face_fallback(tmp_path):
    cache = AvatarCache(use_network=False, size=32, emoji_cache_dir=tmp_path)
    token = run(cache.get_avatar(FakeUser(), sprite_id="emoji:1"))
    face = cache.fallback("A")
    assert list(token.getdata()) == list(face.getdata())


# --- cache behaviour ---

def test_token_cache_keyed_by_sprite_id():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    user = FakeUser()
    t1 = run(cache.get_avatar(user, sprite_id="twe:1f436"))
    t2 = run(cache.get_avatar(user, sprite_id="twe:1f431"))
    assert list(t1.getdata()) != list(t2.getdata())  # not the same cached token


def test_invalidate_drops_all_variants():
    cache = AvatarCache(avatars_dir=AVATARS, size=32)
    user = FakeUser()
    run(cache.get_avatar(user, sprite_id="twe:1f436"))
    run(cache.get_avatar(user, sprite_id=""))
    assert any(k[0] == user.id for k in cache._cache)
    cache.invalidate(user.id)
    assert not any(k[0] == user.id for k in cache._cache)
