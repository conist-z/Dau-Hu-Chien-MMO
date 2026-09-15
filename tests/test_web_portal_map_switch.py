"""Portal crossing must re-send the DESTINATION world payload (web client).

Bug (user 16/09): walking through the monster-trade door moved the server body
into ``montertradebase`` while the browser kept rendering AND colliding
against the source map (``lobbytrade``). Snapshots then carried destination
coordinates, so the client predicted into walls forever — the permanent
d=1.68..33 desync that looked like "tele qua cửa nhà cũ".

The ``/khutraodoi`` chat command already re-sent the welcome through
``WebHub._maybe_teleport_welcome``; the walked-through portal path
(``GameManager._teleport_through_link``) never did. These tests lock the fix:
the game layer calls the web hook, and the hub pushes a fresh welcome for the
destination map.
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from game.manager import GameManager
from game.travel import TRADE_LOBBY_MAP
from web_api.core import ClientConnection, WebHub

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"

# The trade lobby's right-hand monster door (portals.json).
DOOR_TILE = (38, 24)


def _lobby_gm_with_web_player(uid: int = 42):
    gm = GameManager(ASSETS)
    gm.create_runtime(1, TRADE_LOBBY_MAP)
    rt = gm.runtimes[1]
    assert gm.register_web_session(1, uid, "tester"), "web session must attach"
    return gm, rt


def test_webhub_registers_map_change_hook():
    gm = GameManager(ASSETS)
    assert gm.web_map_change_hook is None
    hub = WebHub(gm)
    assert gm.web_map_change_hook == hub.send_map_welcome


def test_portal_step_pushes_welcome_for_destination_map():
    """End-to-end: door tile -> hook fires -> welcome(montertradebase) queued."""

    async def run():
        uid = 42
        gm, rt = _lobby_gm_with_web_player(uid)
        hub = WebHub(gm)
        sess = rt.web_sessions[uid]
        hub.connections[7] = ClientConnection(cid=7, session=sess, joined=True)

        link = gm.portals.link_at(TRADE_LOBBY_MAP, *DOOR_TILE)
        assert link is not None, "portals.json must define the monster door"

        await gm._teleport_through_link(1, rt, uid, link)

        dst_rt = gm.side_runtimes[(1, link.map_id)]
        # Session migrated with the player (the hub reaches it via dst_rt).
        assert dst_rt.state.get_player(uid) is not None
        assert uid in dst_rt.web_sessions

        frames = []
        while not hub.outbox.empty():
            frames.append(await hub.outbox.get())
        welcomes = [f["frame"] for f in frames if f["frame"].get("type") == "welcome"]
        assert welcomes, "a portal step must re-send the world payload"
        assert welcomes[-1]["map"]["id"] == link.map_id
        # The payload carries the DESTINATION collision, not the source map's —
        # this is exactly what kept the client desynced before.
        assert welcomes[-1]["map"]["collision"]

    asyncio.run(run())


def test_portal_step_without_web_session_is_harmless():
    """Discord-only players crossing a portal must not break the hook."""

    async def run():
        gm = GameManager(ASSETS)
        gm.create_runtime(1, TRADE_LOBBY_MAP)
        rt = gm.runtimes[1]
        rt.state.add_player(9, "chat-only", *DOOR_TILE)
        hub = WebHub(gm)
        link = gm.portals.link_at(TRADE_LOBBY_MAP, *DOOR_TILE)
        await gm._teleport_through_link(1, rt, 9, link)
        assert hub.outbox.empty(), "no web conn -> no welcome frame"

    asyncio.run(run())
