"""Micro-breakdown of finalize_for_upload stages + PNG size check."""
import asyncio
import io
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ASSETS_DIR  # noqa: E402
from game.manager import GameManager  # noqa: E402
from rendering.avatar import AvatarCache  # noqa: E402
from rendering.camera import Camera  # noqa: E402
from rendering.renderer import Renderer, finalize_for_upload  # noqa: E402
from game.resources import render_kwargs as resource_render_kwargs  # noqa: E402

N = 20


def avg(v):
    return statistics.mean(v)


async def main():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(99_000_002, "bigmap")
    rt.state.add_player(1, "Bench", 30, 20)

    class _FakeMember:
        display_name = "Bench"
        id = 1
    rt.members[1] = _FakeMember()
    cam = Camera.auto(rt.map_data)
    cam.center_on(30, 20, rt.map_data.width, rt.map_data.height)

    renderer = Renderer(ASSETS_DIR, AvatarCache(use_network=False))
    kw = dict(
        members=rt.members, camera=cam, full=True, focus_user_id=1,
        **resource_render_kwargs(rt),
    )
    result = await renderer.render(rt.state, rt.map_data, **kw)
    img = result.image  # RGBA 1344x960

    resize_ms, quant_ms, conv_ms = [], [], []
    for _ in range(N):
        t0 = time.perf_counter()
        small = img.resize((672, 480), 0)  # NEAREST = 0
        resize_ms.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        bg = io.BytesIO()
        # replicate finalize: RGBA flatten + adaptive quantize
        from PIL import Image
        rgb = Image.alpha_composite(
            Image.new("RGBA", small.size, (20, 28, 40, 255)), small
        ).convert("RGB")
        conv_ms.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        p = rgb.convert("P", palette=Image.ADAPTIVE, colors=64)
        quant_ms.append((time.perf_counter() - t0) * 1000)

        buf = io.BytesIO()
        p.save(buf, "PNG", optimize=True)
        size = len(buf.getvalue())

    print(f"input {img.size} {img.mode} -> upload {small.size}, PNG {size/1024:.0f} KB")
    print(f"resize NEAREST 0.5x : avg {avg(resize_ms):6.2f} ms")
    print(f"flatten RGBA->RGB   : avg {avg(conv_ms):6.2f} ms")
    print(f"adaptive quantize64 : avg {avg(quant_ms):6.2f} ms")

    # Alternative quantizers on the small RGB image
    from PIL import Image
    for colors in (64, 32):
        t0 = time.perf_counter()
        rgb.quantize(colors=colors, method=Image.Quantize.MAXCOVERAGE)
        mc = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        rgb.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
        fo = (time.perf_counter() - t0) * 1000
        print(f"quantize({colors}) MAXCOVERAGE: {mc:6.2f} ms | FASTOCTREE: {fo:6.2f} ms")


asyncio.run(main())
