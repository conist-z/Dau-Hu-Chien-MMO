from dataclasses import dataclass
from typing import Literal, Tuple


DEFAULT_VIEW_W = 21
DEFAULT_VIEW_H = 15

# True pixel zoom for the follow viewport: tiles are rendered larger so the
# focused player occupies more of the frame. Applied on top of the upload
# shrink, so net tiles stay crisp and the CDN image stays small.
FOLLOW_ZOOM = 2.0


@dataclass
class Camera:
    """Viewport over a tile map.

    - mode "full": render the entire map (small maps).
    - mode "follow": keep a fixed-size window centred on a target tile; the
      world scrolls beneath the player while the frame size stays constant.
      Near map edges the window is clamped so it never shows out-of-bounds.
    """

    mode: Literal["full", "follow"] = "full"
    view_w: int = 0
    view_h: int = 0
    center_x: int = 0
    center_y: int = 0
    zoom: float = 1.0

    @property
    def follow(self) -> bool:
        return self.mode == "follow"

    @staticmethod
    def auto(map_data) -> "Camera":
        if map_data.width > DEFAULT_VIEW_W or map_data.height > DEFAULT_VIEW_H:
            cam = Camera(mode="follow", view_w=DEFAULT_VIEW_W, view_h=DEFAULT_VIEW_H, zoom=FOLLOW_ZOOM)
        else:
            cam = Camera(mode="full", view_w=map_data.width, view_h=map_data.height)
        return cam

    def center_on(self, x: int, y: int, map_w: int, map_h: int) -> None:
        self.center_x = x
        self.center_y = y
        self._clamp(map_w, map_h)

    def _clamp(self, map_w: int, map_h: int) -> None:
        if self.view_w <= 0 or self.view_h <= 0:
            return
        half_w = self.view_w // 2
        half_h = self.view_h // 2
        if map_w <= self.view_w:
            self.center_x = map_w // 2
        else:
            self.center_x = max(half_w, min(map_w - half_w - 1, self.center_x))
        if map_h <= self.view_h:
            self.center_y = map_h // 2
        else:
            self.center_y = max(half_h, min(map_h - half_h - 1, self.center_y))

    def top_left(self, map_w: int, map_h: int) -> Tuple[int, int]:
        self._clamp(map_w, map_h)
        if self.view_w <= 0 or self.view_h <= 0:
            return 0, 0
        half_w = self.view_w // 2
        half_h = self.view_h // 2
        if map_w <= self.view_w:
            x0 = 0
        else:
            x0 = max(0, min(map_w - self.view_w, self.center_x - half_w))
        if map_h <= self.view_h:
            y0 = 0
        else:
            y0 = max(0, min(map_h - self.view_h, self.center_y - half_h))
        return x0, y0
