"""Quality check: ADAPTIVE (median cut) vs FASTOCTREE quantize on a real frame."""
import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ASSETS_DIR  # noqa: E402
from game.manager import GameManager  # noqa: E402
from rendering.avatar import AvatarCache  # noqa: E402
from rendering.camera import Camera  # noqa: E402
from rendering.renderer import Renderer  # noqa: E402
from game.resources import render_kwargs as resource_render_kwargs  # noqa: E402
from PIL import Image  # noqa: E402


async def main():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(99_000_003, "bigmap")
    rt.state.add_player(1, "Bench", 30, 20)

    class _M:
        display_name = "Bench"
        id = 1
    rt.members[1] = _M()
    cam = Camera.auto(rt.map_data)
    cam.center_on(30, 20, rt.map_data.width, rt.map_data.height)

    r = Renderer(ASSETS_DIR, AvatarCache(use_network=False))
    res = await r.render(
        rt.state, rt.map_data, members=rt.members, camera=cam,
        full=True, focus_user_id=1, **resource_render_kwargs(rt),
    )
    small = res.image.resize((672, 480), Image.NEAREST)
    rgb = Image.alpha_composite(
        Image.new("RGBA", small.size, (20, 28, 40, 255)), small
    ).convert("RGB")

    a = rgb.quantize(colors=64)  # ADAPTIVE / median cut
    f = rgb.quantize(colors=64, method=Image.Quantize.FASTOCTREE)

    ar, fr = a.convert("RGB"), f.convert("RGB")
    from PIL import ImageChops
    bbox = ImageChops.difference(ar, fr).getbbox()
    h = ar.histogram()
    fh = fr.histogram()
    # mean abs difference per channel
    total = 0
    n = ar.size[0] * ar.size[1]
    gray_a = ar.convert("L").tobytes()
    gray_f = fr.convert("L").tobytes()
    mad = sum(abs(x - y) for x, y in zip(gray_a, gray_f)) / n
    print(f"diff bbox (None=identical): {bbox}")
    print(f"mean |luma diff|: {mad:.3f} / 255")
    for img, name in ((a, "ADAPTIVE"), (f, "FASTOCTREE")):
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=False)
        print(f"{name}: PNG {len(buf.getvalue())/1024:.1f} KB, {len(img.getcolors(65536) or [])} distinct")
    ar.save("temp_bench_adaptive.png")
    fr.save("temp_bench_fastoctree.png")


asyncio.run(main())
