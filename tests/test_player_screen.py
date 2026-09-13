import asyncio
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

path = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(path))

from discord_ui.map_view import MapView, flush_render_batch  # noqa: E402
from game.manager import PlayerScreen  # noqa: E402
from game.state import ActionResult, GameState  # noqa: E402
from game.npc import NpcMap  # noqa: E402
from rendering.camera import Camera  # noqa: E402
from rendering.hub_renderer import HubRenderResult  # noqa: E402
from rendering.renderer import RenderResult  # noqa: E402


def make_interaction(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name="P"),
        response=SimpleNamespace(
            is_done=MagicMock(return_value=False),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
            send_message=AsyncMock(),
        ),
        followup=AsyncMock(),
        channel=SimpleNamespace(
            id=100,
            send=AsyncMock(return_value=SimpleNamespace(id=888)),
            get_partial_message=MagicMock(return_value=SimpleNamespace(delete=AsyncMock())),
        ),
        original_response=AsyncMock(return_value=SimpleNamespace(id=999)),
        edit_original_response=AsyncMock(),
    )


def make_rt() -> SimpleNamespace:
    rt = SimpleNamespace(
        channel_id=100,
        message_id=None,
        hub_message_id=None,
        state=GameState(100, "t"),
        map_data=SimpleNamespace(map_id="t", width=20, height=20, spawn=(2, 2), display_name="T"),
        members={},
        screens={},
        camera=None,
        composite=None,
        npc_map=NpcMap([], {}),
        # Hotbar bindings + inventories (read by MapView._apply_hotbar_labels).
        hotbars={},
        inventories={},
    )
    rt.state.add_player(42, "P", 2, 2)
    rt.screens[42] = PlayerScreen(user_id=42, camera=Camera.auto(rt.map_data))
    return rt


class FakeGate:
    def __init__(self):
        self.calls = []

    async def edit_message(self, *args, **kwargs):
        self.calls.append(kwargs)


class FakeRenderer:
    def __init__(self):
        self.avatar_cache = SimpleNamespace(get_avatar=AsyncMock())
        self.tile_size = 32

    async def render(self, *args, **kwargs):
        return RenderResult(image=Image.new("RGB", (32, 32), (10, 20, 30)))


class FakeHubRenderer:
    def __init__(self):
        self.assets_dir = pathlib.Path("assets")

    def render(self, rt, map_data, npc_map, focused_user_id=None, target_internal_w=None, **kwargs):
        img = Image.new("RGB", (160, 66), (20, 28, 40))
        return HubRenderResult(image=img, filename="hub.png")


class FakeManager:
    def __init__(self, rt):
        self.rt = rt
        self.edit_gate = FakeGate()
        self.renderer = FakeRenderer()
        self.hub_renderer = FakeHubRenderer()
        self.coalescer = None
        self.hub_coalescer = None
        self.db = None
        self.bot_ref = None
        self.dispatch_calls = 0

    def get_runtime(self, cid):
        return self.rt

    def get_runtime_for(self, cid, user_id=None):
        return self.rt

    def touch_session(self, channel_id, user_id):
        pass  # session tracking is orthogonal to these tests

    async def close_side_panels(self, channel_id, user_id):
        pass  # panel closing is orthogonal to these tests

    def ensure_screen(self, rt, user_id):
        screen = rt.screens.get(user_id)
        if screen is None:
            screen = PlayerScreen(user_id=user_id, camera=Camera.auto(rt.map_data))
            rt.screens[user_id] = screen
        return screen

    async def dispatch(self, cid, action):
        self.dispatch_calls += 1
        p = self.rt.state.get_player(action.user_id)
        p.x += 1  # actually move so state changes
        return self.rt, ActionResult(True, state_changed=True)


def test_custom_ids_scoped_per_player():
    view = MapView(100, FakeManager(None), 42)
    for item in view.children:
        assert item.custom_id.startswith("mg:100:42:"), item.custom_id


def test_press_by_non_owner_rejected_without_dispatch():
    rt = make_rt()
    mgr = FakeManager(rt)
    view = MapView(100, mgr, 42)
    inter = make_interaction(99)
    asyncio.run(view._handle_move(inter, "n"))
    inter.response.send_message.assert_awaited_once()
    assert inter.response.edit_message.await_count == 0
    assert mgr.dispatch_calls == 0
    assert mgr.edit_gate.calls == []


def test_owner_press_edits_via_interaction_token_not_gate():
    rt = make_rt()
    mgr = FakeManager(rt)
    view = MapView(100, mgr, 42)
    inter = make_interaction(42)
    asyncio.run(view._handle_move(inter, "n"))
    inter.response.edit_message.assert_awaited_once()
    kwargs = inter.response.edit_message.await_args.kwargs
    assert "attachments" in kwargs and "view" in kwargs
    # The press lane must NOT touch the channel message-edit bucket.
    assert mgr.edit_gate.calls == []


def test_busy_screen_coalesces_press_with_latest_wins():
    rt = make_rt()
    mgr = FakeManager(rt)
    rt.screens[42].rendering = True
    view = MapView(100, mgr, 42)
    coalescer = SimpleNamespace(schedule=MagicMock())
    mgr.coalescer = coalescer
    inter = make_interaction(42)
    asyncio.run(view._handle_move(inter, "n"))
    inter.response.defer.assert_awaited_once()
    coalescer.schedule.assert_called_once()
    assert coalescer.schedule.call_args.args[0] == (100, 42)


def test_flush_prefers_per_interaction_webhook_endpoint():
    rt = make_rt()
    mgr = FakeManager(rt)
    rt.screens[42].message_id = 777
    view = MapView(100, mgr, 42)
    inter = make_interaction(42)
    inter.response.is_done.return_value = True
    batch = [{"interaction": inter, "view": view, "user_id": 42}]
    asyncio.run(flush_render_batch(mgr, (100, 42), batch))
    inter.edit_original_response.assert_awaited_once()
    assert mgr.edit_gate.calls == []


def test_tool_labels_read_from_screen():
    rt = make_rt()
    mgr = FakeManager(rt)
    rt.screens[42].step_size = 3
    view = MapView(100, mgr, 42)
    assert view.tool_step.label == "x3"


def test_legacy_press_mints_personal_screen():
    rt = make_rt()
    mgr = FakeManager(rt)
    view = MapView(100, mgr, None)
    inter = make_interaction(7)
    asyncio.run(view._handle_move(inter, "n"))
    inter.response.send_message.assert_awaited_once()
    assert 7 in rt.screens
    assert rt.screens[7].message_id == 999
