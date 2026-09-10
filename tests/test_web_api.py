"""Web API core tests: login/join/input/craft/chat without real sockets."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from web_api.core import WebHub
from web_api.protocol import SessionRegistry


@pytest.fixture()
def hub():
    from game.manager import GameManager

    assets = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"
    gm = GameManager(assets)
    gm.create_runtime(1, "test-map")
    return WebHub(gm)


def _drain(hub):
    out = []
    while not hub.outbox.empty():
        out.append(hub.outbox.get_nowait())
    return out


def test_join_with_unknown_token_errors(hub):
    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 1})
        await hub.handle_envelope({
            "type": "frame", "cid": 1,
            "frame": {"type": "join", "token": "nope", "channel_id": 1},
        })
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    assert any(m.get("frame", {}).get("code") == "bad_token" for m in msgs)


def test_input_before_join_is_rejected(hub):
    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 1})
        await hub.handle_envelope({
            "cid": 1, "frame": {"type": "input", "dx": 1, "dy": 0},
        })
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    assert any(m.get("frame", {}).get("code") == "not_joined" for m in msgs)


def test_ping_pong(hub):
    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 2})
        await hub.handle_envelope({"cid": 2, "frame": {"type": "ping", "t": 42}})
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    assert any(
        m.get("cid") == 2 and m.get("frame", {}).get("type") == "pong"
        and m["frame"]["t"] == 42 for m in msgs
    )


def test_full_join_flow_with_registered_session(hub):
    """join with a registry-created token -> welcome + snapshot pump armed."""
    sess = hub.registry.create(user_id=55, display_name="tester", channel_id=1)

    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 3})
        await hub.handle_envelope({
            "cid": 3,
            "frame": {"type": "join", "token": sess.token, "channel_id": 1},
        })
        # Let the snapshot task run one tick (it was started by join).
        await asyncio.sleep(0.05)
        await hub.stop()
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    welcome = [m for m in msgs if m.get("frame", {}).get("type") == "welcome"]
    assert welcome, "welcome frame expected"
    frame = welcome[0]["frame"]
    assert frame["map"]["id"] == "test-map"
    assert frame["self"]["id"] == 55
    assert frame["self"]["x"] == 5.5 and frame["self"]["y"] == 5.5  # spawn centre
    # Self is NEVER in `players`: the client renders itself via prediction.
    # A duplicate self entry showed up as a frozen clone at the spawn point.
    assert all(p["id"] != 55 for p in frame["players"])
    assert frame["recipes"], "recipes must be present"
    snap = [m for m in msgs if m.get("frame", {}).get("type") == "snapshot"]
    assert snap, "snapshot expected after join"


def test_input_vector_reaches_manager(hub):
    sess = hub.registry.create(user_id=66, display_name="mover", channel_id=1)

    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 4})
        await hub.handle_envelope({
            "cid": 4,
            "frame": {"type": "join", "token": sess.token, "channel_id": 1},
        })
        await hub.handle_envelope({
            "cid": 4, "frame": {"type": "input", "dx": 0.5, "dy": -0.5, "running": True},
        })
        await hub.stop()
    asyncio.new_event_loop().run_until_complete(run())
    rt = hub.manager.get_runtime(1)
    web_sess = rt.web_sessions[66]
    assert web_sess.dx == 0.5 and web_sess.dy == -0.5 and web_sess.running is True


def test_client_gone_drops_session(hub):
    sess = hub.registry.create(user_id=77, display_name="gone", channel_id=1)

    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 5})
        await hub.handle_envelope({
            "cid": 5,
            "frame": {"type": "join", "token": sess.token, "channel_id": 1},
        })
        await hub.handle_envelope({"type": "client_gone", "cid": 5})
        await hub.stop()
    asyncio.new_event_loop().run_until_complete(run())
    rt = hub.manager.get_runtime(1)
    p = rt.state.get_player(77)
    assert p is not None and p.is_web is False
    assert 77 not in rt.web_sessions
    assert hub.registry.get(sess.token) is None


def test_chat_cmd_weather_readonly(hub):
    sess = hub.registry.create(user_id=88, display_name="w", channel_id=1)

    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 6})
        await hub.handle_envelope({
            "cid": 6,
            "frame": {"type": "join", "token": sess.token, "channel_id": 1},
        })
        await hub.handle_envelope({"cid": 6, "frame": {"type": "chat_cmd", "text": "/weather"}})
        await hub.stop()
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    pushes = [m["frame"] for m in msgs if m.get("frame", {}).get("type") == "push"]
    assert any("Thời tiết" in p.get("message", "") for p in pushes)


def test_setweather_requires_admin(hub):
    sess = hub.registry.create(user_id=99, display_name="w", channel_id=1)

    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 7})
        await hub.handle_envelope({
            "cid": 7,
            "frame": {"type": "join", "token": sess.token, "channel_id": 1},
        })
        await hub.handle_envelope({
            "cid": 7, "frame": {"type": "chat_cmd", "text": "/setweather rain"},
        })
        await hub.stop()
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    pushes = [m["frame"] for m in msgs if m.get("frame", {}).get("type") == "push"]
    # No bot/guild wired in the test -> admin check fails -> denied message.
    assert any("admin" in p.get("message", "").lower() for p in pushes)
    rt = hub.manager.get_runtime(1)
    assert rt.weather_key != "rain"


def test_asset_request_rejects_traversal(hub):
    async def run():
        await hub.handle_envelope({"type": "asset_request", "cid": 8, "name": "../../secret.png"})
        await hub.handle_envelope({"type": "asset_request", "cid": 8, "name": "data.db"})
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    assets = [m for m in msgs if m.get("type") == "asset_data"]
    assert all(m.get("b64") is None for m in assets)


def test_far_click_place_rejects_without_consuming(hub):
    """A click genuinely beyond AIM_RANGE+tolerance must NOT fall back to the
    facing tile (that used to eat a block and place it where nobody clicked).
    It is rejected with out_of_range; material and block grid stay untouched."""
    sess = hub.registry.create(user_id=111, display_name="builder", channel_id=1)

    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 9})
        await hub.handle_envelope({
            "cid": 9,
            "frame": {"type": "join", "token": sess.token, "channel_id": 1},
        })
        # 5 stone in the bag, first stack (hotbar slot 0).
        inv = hub.manager.get_inventory(1, 111)
        inv.add("stone", 5)
        inv.move_to("stone", 0)
        # Spawn is (5,5) on test-map; clicking 8 tiles east is beyond lim (4).
        await hub.handle_envelope({
            "cid": 9,
            "frame": {
                "type": "action", "name": "place",
                "tx": 13, "ty": 5, "block_id": "stone",
            },
        })
        await hub.stop()
    asyncio.new_event_loop().run_until_complete(run())
    msgs = _drain(hub)
    results = [
        m["frame"] for m in msgs
        if m.get("frame", {}).get("type") == "action_result"
    ]
    assert any(r["reason"] == "out_of_range" for r in results), results
    # Nothing consumed, nothing placed.
    assert hub.manager.get_inventory(1, 111).count("stone") == 5
    assert len(hub.manager.get_runtime(1).state.blocks) == 0


def test_registry_create_get_drop():
    reg = SessionRegistry()
    s = reg.create(1, "a", 2)
    assert reg.get(s.token) is s
    reg.drop(s.token)
    assert reg.get(s.token) is None
