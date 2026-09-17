"""Shared list of map ids available for web-client preview sessions.

Mirrors MapCog._list_map_ids (discord_ui/commands.py) but importable without
discord — the web API has no discord.py. Scan is lazy (called per request)
so newly dropped map JSONs appear immediately.
"""
import glob
import os
from pathlib import Path

_CACHE: list[str] | None = None
_CACHE_MTIME: float = 0.0


def list_map_ids(assets_dir: Path | str = "assets/maps") -> list[str]:
    """All playable map ids: relative path minus .json, skipping companion
    files (*.solids, *.npcs). Cached until any JSON mtime changes."""
    global _CACHE, _CACHE_MTIME
    root = Path(assets_dir)
    newest = 0.0
    paths: list[str] = []
    for p in root.glob("*.json"):
        paths.append(str(p))
        newest = max(newest, p.stat().st_mtime)
    for sub in root.iterdir():
        if sub.is_dir():
            for p in sub.glob("*.json"):
                paths.append(str(p))
                newest = max(newest, p.stat().st_mtime)
    if _CACHE is not None and newest == _CACHE_MTIME:
        return _CACHE
    ids = set()
    for p in paths:
        rel = os.path.relpath(p, root).replace("\\", "/")
        mid = rel[: -len(".json")]
        if "." in mid:
            continue
        ids.add(mid)
    _CACHE = sorted(ids)
    _CACHE_MTIME = newest
    return _CACHE
