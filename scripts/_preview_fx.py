"""Dev preview: render the map with each animated weather FX as a looping GIF
at the project root (``temp_fx_<key>.gif``) plus a 2-frame check strip
(``temp_fx_<key>_check.png``). Not shipped - mirrors the other ``_*.py``
dev scripts in this folder.

    .venv\\Scripts\\python scripts/_preview_fx.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from config import ASSETS_DIR
from game.manager import GameManager
from rendering.avatar import AvatarCache
from rendering.camera import Camera
from rendering.renderer import Renderer, encode_upload_gif

ROOT = Path(__file__).resolve().parent.parent


async def main() -> None:
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(42, "bigmap")
    rt.state.add_player(1, "A", 30, 20)
    renderer = Renderer(ASSETS_DIR, AvatarCache(use_network=False))
    cam = Camera(mode="follow", view_w=21, view_h=15, zoom=1.0)
    cam.center_on(30, 20, rt.map_data.width, rt.map_data.height)

    for key in ("storm", "rain", "snow", "wind"):
        res = await renderer.render(
            rt.state, rt.map_data, camera=cam, full=True, weather_key=key
        )
        data = encode_upload_gif(res.frames, res.duration_ms)
        out = ROOT / f"temp_fx_{key}.gif"
        out.write_bytes(data)
        print(key, res.filename, len(res.frames), "frames,", len(data), "bytes")

    for key in ("storm", "snow", "wind"):
        gif = Image.open(ROOT / f"temp_fx_{key}.gif")
        frames = []
        try:
            i = 0
            while True:
                gif.seek(i)
                frames.append(gif.convert("RGBA"))
                i += 1
        except EOFError:
            pass
        pick = frames[2] if key == "storm" else frames[0]
        w, h = pick.size
        strip = Image.new("RGBA", (w * 2 + 8, h), (30, 30, 30, 255))
        strip.paste(frames[0], (0, 0))
        strip.paste(pick, (w + 8, 0))
        strip.convert("RGB").save(ROOT / f"temp_fx_{key}_check.png")
        print(key, len(frames), "frames extracted")


if __name__ == "__main__":
    asyncio.run(main())

