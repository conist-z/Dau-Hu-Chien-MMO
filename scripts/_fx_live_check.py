import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import asyncio
from PIL import Image
from config import ASSETS_DIR
from game.manager import GameManager
from rendering.avatar import AvatarCache
from rendering.camera import Camera
from rendering.renderer import Renderer, encode_upload_gif

ROOT = Path.cwd()

async def main():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(42, "bigmap")
    rt.state.add_player(1, "A", 30, 20)
    renderer = Renderer(ASSETS_DIR, AvatarCache(use_network=False))
    cam = Camera.auto(rt.map_data)   # live config: follow 21x15 zoom 2.0
    cam.center_on(30, 20, rt.map_data.width, rt.map_data.height)
    jobs = (("rain", 0), ("storm", 777), ("snow", 0), ("wind", 0), ("heavy_rain", 0))
    for key, seed in jobs:
        res = await renderer.render(rt.state, rt.map_data, camera=cam, full=True,
                                    weather_key=key, fx_seed=seed)
        (ROOT / f"temp_fx_live_{key}.gif").write_bytes(
            encode_upload_gif(res.frames, res.duration_ms))
        print("rendered", key, len(res.frames), "frames")

    for key, _ in jobs:
        gif = Image.open(ROOT / f"temp_fx_live_{key}.gif")
        frames = []
        try:
            i = 0
            while True:
                gif.seek(i)
                frames.append(gif.convert("RGB"))
                i += 1
        except EOFError:
            pass
        w, h = frames[0].size
        strip = Image.new("RGB", (w * 2 + 8, h), (30, 30, 30))
        strip.paste(frames[0], (0, 0))
        strip.paste(frames[2], (w + 8, 0))
        strip.resize(((w * 2 + 8) * 2 // 3, h * 2 // 3)).save(ROOT / f"temp_fx_live_{key}_check.png")
        print("strip", key, len(frames), "frames")

asyncio.run(main())
