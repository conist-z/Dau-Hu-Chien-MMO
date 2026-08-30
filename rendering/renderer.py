import colorsys
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from PIL import Image, ImageDraw

from game.map_loader import MapData
from game.state import GameState
from rendering.avatar import AvatarCache
from rendering.camera import Camera


@dataclass
class RenderResult:
    image: Image.Image
    filename: str = "map.png"
    content_type: str = "image/png"
    composite: Optional[Image.Image] = None


# Aggressive upload shrink: render the map internally at full tile resolution
# (so coordinates/collision stay correct), then downscale + palette-quantize the
# PNG actually uploaded to Discord's CDN. This is the dominant latency factor.
MAP_UPLOAD_SCALE = 0.5
MAP_PALETTE_COLORS = 16
UPLOAD_BG_COLOR = (20, 28, 40)


def _token_color(user_id: int):
    h = hashlib.md5(str(user_id).encode()).digest()[0] / 255.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.55, 0.85)
    return (int(r * 255), int(g * 255), int(b * 255))


def finalize_for_upload(img: Image.Image) -> Image.Image:
    """Shrink + palette-quantize a rendered map for fast CDN upload."""
    w, h = img.size
    if MAP_UPLOAD_SCALE != 1.0:
        nw, nh = max(1, int(w * MAP_UPLOAD_SCALE)), max(1, int(h * MAP_UPLOAD_SCALE))
        img = img.resize((nw, nh), Image.NEAREST)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, UPLOAD_BG_COLOR)
        img = Image.alpha_composite(bg.convert("RGBA"), img).convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img.convert("P", palette=Image.ADAPTIVE, colors=MAP_PALETTE_COLORS)


class Renderer:
    """Pure: consumes GameState + MapData, produces a RenderResult.

    Must NOT mutate GameState and must NOT touch Discord or the database.
    """

    def __init__(self, assets_dir: Path, avatar_cache: AvatarCache, tile_size: int = 32):
        self.assets_dir = assets_dir
        self.avatar_cache = avatar_cache
        self.tile_size = tile_size
        self._base_cache: Dict[str, Image.Image] = {}

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
    ) -> RenderResult:
        tile = self.tile_size
        w = map_data.width * tile
        h = map_data.height * tile
        base = self._base_layer(map_data, w, h, tile)

        # Camera follow: render only a fixed window centred on the target tile.
        # The frame size is constant while the world scrolls, so every step is a
        # fresh viewport render (incremental patching does not apply here). The
        # small window also keeps the uploaded image tiny -> fast CDN upload.
        if camera is not None and camera.follow:
            x0, y0 = camera.top_left(map_data.width, map_data.height)
            vw, vh = camera.view_w * tile, camera.view_h * tile
            comp = base.crop((x0 * tile, y0 * tile, x0 * tile + vw, y0 * tile + vh)).copy()
            for p in state.get_visible_players():
                if x0 <= p.x < x0 + camera.view_w and y0 <= p.y < y0 + camera.view_h:
                    token = await self._make_token(p, members, tile, color=_token_color(p.user_id))
                    comp.paste(token, ((p.x - x0) * tile, (p.y - y0) * tile), token)
            # True zoom: upscale the whole viewport (tokens included) with
            # nearest-neighbour so pixel art stays crisp. This is what makes the
            # focused player appear larger on screen.
            if camera.zoom != 1.0:
                zt = max(1, int(round(vw * camera.zoom)))
                zh = max(1, int(round(vh * camera.zoom)))
                comp = comp.resize((zt, zh), Image.NEAREST)
            return RenderResult(image=comp, composite=comp)

        # Incremental patch: only the moved player's old/new tile change, so
        # re-blit the base tile under the old position and draw the token at the
        # new one. Falls back to a full render when tiles overlap other players
        # or when no cached composite is available.
        if (
            not full
            and prev_composite is not None
            and moved_user_id is not None
            and prev_pos is not None
        ):
            mover = state.get_player(moved_user_id)
            if mover is not None:
                new_pos = (mover.x, mover.y)
                if prev_pos != new_pos and not self._tiles_overlap_other_player(
                    state, moved_user_id, prev_pos, new_pos
                ):
                    comp = prev_composite
                    ox, oy = prev_pos
                    comp.paste(
                        base.crop((ox * tile, oy * tile, (ox + 1) * tile, (oy + 1) * tile)),
                        (ox * tile, oy * tile),
                    )
                    token = await self._make_token(mover, members, tile, color=_token_color(mover.user_id))
                    comp.paste(token, (mover.x * tile, mover.y * tile), token)
                    return RenderResult(image=comp, composite=comp)

        # Full render.
        comp = base.copy()
        for p in state.get_visible_players():
            token = await self._make_token(p, members, tile, color=_token_color(p.user_id))
            comp.paste(token, (p.x * tile, p.y * tile), token)
        return RenderResult(image=comp, composite=comp)

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
        member = members.get(p.user_id) if members else None
        if member is not None:
            token = await self.avatar_cache.get_avatar(
                member, label=p.display_name[:1], default_color=color
            )
        else:
            token = self.avatar_cache.fallback(p.display_name[:1], color=color)
        return token.resize((tile, tile))

    def _base_layer(self, map_data: MapData, w: int, h: int, tile: int) -> Image.Image:
        cached = self._base_cache.get(map_data.map_id)
        if cached is not None and cached.size == (w, h):
            return cached
        if map_data.image_path and Path(map_data.image_path).exists():
            base = Image.open(map_data.image_path).convert("RGBA").resize((w, h))
            self._base_cache[map_data.map_id] = base
            return base

        base = Image.new("RGBA", (w, h), (30, 40, 60, 255))
        tw = map_data.tile_width
        th = map_data.tile_height
        tileset = map_data.tileset
        sheet = None
        if tileset and tileset.get("image_path") and Path(tileset["image_path"]).exists():
            sheet = Image.open(tileset["image_path"]).convert("RGBA")
            cols = tileset["columns"]
            firstgid = tileset["firstgid"]

        if sheet is not None:
            for _name, grid in map_data.tile_layers:
                for y, row in enumerate(grid):
                    for x, gid in enumerate(row):
                        if gid == 0:
                            continue
                        idx = gid - firstgid
                        sx = (idx % cols) * tw
                        sy = (idx // cols) * th
                        tile_img = sheet.crop((sx, sy, sx + tw, sy + th))
                        base.paste(tile_img, (x * tw, y * th), tile_img)
        else:
            # No tileset PNG yet: draw gray blocks where any tile exists.
            draw = ImageDraw.Draw(base)
            if map_data.tile_layers:
                for _name, grid in map_data.tile_layers:
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
        self._base_cache[map_data.map_id] = base
        return base

    @staticmethod
    def to_bytes(result: RenderResult) -> io.BytesIO:
        buf = io.BytesIO()
        result.image.save(buf, "PNG")
        buf.seek(0)
        return buf
