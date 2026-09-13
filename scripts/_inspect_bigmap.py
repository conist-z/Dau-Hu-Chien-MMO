import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import asyncio
from pathlib import Path
from game.manager import GameManager
from game.resources import render_kwargs
from rendering.renderer import build_chop_frames, encode_upload_gif
from rendering.avatar import AvatarCache

ASSETS = Path("assets/maps")
mgr = GameManager(ASSETS)
from rendering.renderer import Renderer

mgr.renderer = Renderer(ASSETS, AvatarCache(ASSETS, use_network=False))
rt = mgr.create_runtime(7, "bigmap")
rt.state.add_player(10, "A", 5, 5)


async def main():
    from game.actions import ChopAction
    from game.resources import NODE_DEFS

    p = rt.state.get_player(10)
    # chop the tree at (20,0) from below
    p.x, p.y = 20, 1
    p.direction = "NORTH"
    last = None
    for _ in range(NODE_DEFS["tree"].hits):
        _rt, last = await mgr.dispatch(7, ChopAction(10))
    assert last.drops is not None, "felling should drop"
    kw = render_kwargs(rt)
    res = await mgr.renderer.render(
        rt.state, rt.map_data, members=rt.members, full=True,
        focus_user_id=10, **kw,
    )
    print("render after chop OK:", res.image.size, "tiles visible:", len(kw["resource_tiles"]))
    frames = build_chop_frames(res.image, [], 8)
    print("build_chop_frames empty parts -> frames:", len(frames))
    # parts as if the 4 tree tiles faded at their tile px on a 49*32 map
    img = mgr.renderer._resource_tile_image(rt.map_data, 9)
    parts = [(20 * 32, 0 * 32, img), (21 * 32, 0 * 32, img),
             (20 * 32, 1 * 32, img), (21 * 32, 1 * 32, img)]
    frames = build_chop_frames(res.image, parts, 6)
    data = encode_upload_gif(frames, 120, 1)
    print("chop GIF bytes:", len(data), "frames:", len(frames))


asyncio.run(main())