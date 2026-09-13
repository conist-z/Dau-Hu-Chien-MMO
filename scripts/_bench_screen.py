"""Benchmark: average ms per stage of a screen render (bigmap, follow camera).

Run:  .venv/Scripts/python scripts/_bench_screen.py [iterations]

Stages measured (mirroring the real movement path in flush_render_batch):
  1. renderer.render(...)          full=True follow-camera frame
     -> split into: _base_layer (first vs cached), compose, daynight/lighting
  2. finalize_for_upload(...)      0.5x shrink + adaptive palette quantize
  3. PNG encode                    optimize=True (what _screen_bytes does)
  4. PNG encode optimize=False     comparison
"""
import asyncio
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

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20


def timed(fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return (time.perf_counter() - t0) * 1000.0, out


async def main():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(99_000_001, "bigmap")
    rt.state.add_player(1, "Bench", 30, 20)
    if 1 not in rt.members:
        class _FakeMember:
            display_name = "Bench"
            id = 1
        rt.members[1] = _FakeMember()
    cam = rt.camera if rt.camera and rt.camera.follow else Camera.auto(rt.map_data)
    cam.center_on(30, 20, rt.map_data.width, rt.map_data.height)

    renderer = Renderer(ASSETS_DIR, AvatarCache(use_network=False))
    kw = dict(
        members=rt.members, camera=cam, full=True, focus_user_id=1,
        **resource_render_kwargs(rt),
    )

    # Warm-up (caches: base layer, avatar, resource tiles)
    result = await renderer.render(rt.state, rt.map_data, **kw)

    render_ms, finalize_ms, enc_opt_ms, enc_fast_ms = [], [], [], []
    for _ in range(N):
        ms, result = timed(lambda: asyncio.ensure_future(
            renderer.render(rt.state, rt.map_data, **kw)
        ).__await__()) if False else (0, None)
        t0 = time.perf_counter()
        result = await renderer.render(rt.state, rt.map_data, **kw)
        render_ms.append((time.perf_counter() - t0) * 1000.0)

        finalize_ms.append(timed(finalize_for_upload, result.image)[0])
        import io
        img = finalize_for_upload(result.image)
        buf = io.BytesIO()
        t0 = time.perf_counter()
        img.save(buf, "PNG", optimize=True)
        enc_opt_ms.append((time.perf_counter() - t0) * 1000.0)
        buf2 = io.BytesIO()
        t0 = time.perf_counter()
        img.save(buf2, "PNG", optimize=False)
        enc_fast_ms.append((time.perf_counter() - t0) * 1000.0)

    # Stage breakdown inside render: base crop + compose (sync parts) timed
    # separately by monkeypatching run_image_task calls — approximate via
    # a second pass with a pre-warmed base cache vs a cold renderer.
    cold = Renderer(ASSETS_DIR, AvatarCache(use_network=False))
    t0 = time.perf_counter()
    await cold.render(rt.state, rt.map_data, **kw)
    cold_ms = (time.perf_counter() - t0) * 1000.0

    def stats(v):
        return f"avg {statistics.mean(v):7.1f}  min {min(v):7.1f}  max {max(v):7.1f}  p50 {statistics.median(v):7.1f}"

    print(f"=== Screen render benchmark (n={N}) ===")
    print(f"viewport: {cam.view_w}x{cam.view_h} tiles, zoom {cam.zoom}, "
          f"internal {result.image.size}")
    print(f"full cold render (first frame, caches empty): {cold_ms:7.1f} ms")
    print(f"render()            : {stats(render_ms)}")
    print(f"finalize_for_upload : {stats(finalize_ms)}")
    print(f"PNG encode opt=True : {stats(enc_opt_ms)}")
    print(f"PNG encode opt=False: {stats(enc_fast_ms)}")
    total = [r + f + e for r, f, e in zip(render_ms, finalize_ms, enc_opt_ms)]
    print(f"TOTAL (render+finalize+encode): {stats(total)}")


asyncio.run(main())
