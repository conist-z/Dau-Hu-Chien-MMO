import asyncio
import io
from pathlib import Path

from config import ASSETS_DIR
from game.manager import GameManager
from rendering.avatar import AvatarCache
from rendering.camera import Camera
from rendering.renderer import Renderer, finalize_for_upload


def _new_renderer():
    return Renderer(ASSETS_DIR, AvatarCache(use_network=False))


def _runtime():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(999000111, "test-map")
    return rt


def _render(renderer, rt, **kw):
    return asyncio.run(renderer.render(rt.state, rt.map_data, **kw))


def test_full_render_places_tokens():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    result = _render(_new_renderer(), rt)
    assert result.composite is not None
    assert result.image.size[0] == rt.map_data.width * 32


def test_follow_camera_renders_viewport():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(123000, "bigmap")
    rt.state.add_player(7, "P", 30, 20)
    rt.camera.center_on(30, 20, rt.map_data.width, rt.map_data.height)
    result = _render(_new_renderer(), rt, camera=rt.camera)
    # Viewport matches the camera window, scaled by the follow zoom factor.
    exp_w = max(1, round(rt.camera.view_w * 32 * rt.camera.zoom))
    exp_h = max(1, round(rt.camera.view_h * 32 * rt.camera.zoom))
    assert result.image.size == (exp_w, exp_h)
    # Player pixel falls inside the viewport at its scrolled offset.
    x0, y0 = rt.camera.top_left(rt.map_data.width, rt.map_data.height)
    assert (30 - x0) * 32 * rt.camera.zoom < result.image.size[0]
    assert (20 - y0) * 32 * rt.camera.zoom < result.image.size[1]


def test_follow_camera_zoom_scales_up():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(123001, "bigmap")
    rt.state.add_player(7, "P", 30, 20)
    cam = Camera(mode="follow", view_w=21, view_h=15, zoom=2.0)
    cam.center_on(30, 20, rt.map_data.width, rt.map_data.height)
    result = _render(_new_renderer(), rt, camera=cam)
    assert result.image.size == (21 * 32 * 2, 15 * 32 * 2)


def test_follow_camera_zoom_one_is_unscaled():
    mgr = GameManager(ASSETS_DIR)
    rt = mgr.create_runtime(123002, "bigmap")
    rt.state.add_player(7, "P", 30, 20)
    cam = Camera(mode="follow", view_w=21, view_h=15, zoom=1.0)
    cam.center_on(30, 20, rt.map_data.width, rt.map_data.height)
    result = _render(_new_renderer(), rt, camera=cam)
    assert result.image.size == (21 * 32, 15 * 32)


def test_incremental_render_matches_full():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    renderer = _new_renderer()

    full = _render(renderer, rt).composite
    rt.composite = full

    prev = (rt.state.get_player(1).x, rt.state.get_player(1).y)
    rt.state.get_player(1).x += 1  # move east (assume walkable at spawn+1)

    inc = _render(
        renderer,
        rt,
        prev_composite=rt.composite,
        moved_user_id=1,
        prev_pos=prev,
    ).composite

    fresh_full = _render(renderer, rt).composite

    assert inc.tobytes() == fresh_full.tobytes()


def test_incremental_falls_back_when_overlapping():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    rt.state.add_player(2, "B", *rt.map_data.spawn)
    renderer = _new_renderer()

    full = _render(renderer, rt).composite
    rt.composite = full

    prev = (rt.state.get_player(1).x, rt.state.get_player(1).y)
    rt.state.get_player(1).x += 1

    result = _render(
        renderer,
        rt,
        prev_composite=rt.composite,
        moved_user_id=1,
        prev_pos=prev,
    )
    # No exception and a valid composite is returned.
    assert result.composite is not None


def test_finalize_shrinks_png():
    rt = _runtime()
    rt.state.add_player(1, "A", *rt.map_data.spawn)
    result = _render(_new_renderer(), rt)

    raw = io.BytesIO()
    result.image.save(raw, "PNG")
    raw_size = len(raw.getvalue())

    small = io.BytesIO()
    finalize_for_upload(result.image).save(small, "PNG", optimize=True)
    small_size = len(small.getvalue())

    assert small_size < raw_size
