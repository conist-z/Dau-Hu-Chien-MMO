"""Web hand (plan A) tests: held slot -> snapshot.held -> hand+tool icon."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from web_api.core import WebHub
from web_api.snapshots import _held_of, build_snapshot, build_welcome


def _gm(cid=781):
    from game.manager import GameManager

    assets = pathlib.Path(__file__).resolve().parent.parent / "assets" / "maps"
    gm = GameManager(assets)
    gm.create_runtime(cid, "test-map")
    return gm


def test_held_empty_by_default():
    gm = _gm(781)
    rt = gm.get_runtime(781)
    p = rt.state.add_player(1, "A", 5, 5)
    p.sync_float_from_int()
    gm.get_inventory(781, 1)
    assert _held_of(rt, 1) is None


def test_held_follows_hotbar_slot():
    gm = _gm(782)
    rt = gm.get_runtime(782)
    p = rt.state.add_player(41, "T", 5, 5)
    p.sync_float_from_int()
    inv = gm.get_inventory(782, 41)
    inv.add("dirt_pickaxe", 1)
    inv.add("stone", 5)
    # slot 0 -> first stack, slot 1 -> second stack.
    assert _held_of(rt, 41) == "dirt_pickaxe"
    rt.held_slots[41] = 1
    assert _held_of(rt, 41) == "stone"


def test_snapshot_and_welcome_carry_held():
    gm = _gm(783)
    rt = gm.get_runtime(783)
    me = rt.state.add_player(1, "Me", 5, 5)
    me.sync_float_from_int()
    other = rt.state.add_player(2, "Other", 6, 5)
    other.sync_float_from_int()
    inv_me = gm.get_inventory(783, 1)
    inv_me.add("dirt_pickaxe", 1)
    inv_other = gm.get_inventory(783, 2)
    inv_other.add("stone", 2)
    rt.held_slots[2] = 0
    w = build_welcome(rt, 1)
    assert w["held"] == "dirt_pickaxe"
    s = build_snapshot(rt, 1, 1)
    assert s["self"]["held"] == "dirt_pickaxe"
    by_id = {pl["id"]: pl for pl in s["players"]}
    assert by_id[2]["held"] == "stone"


def test_select_slot_mirrors_held_slots_and_echoes():
    gm = _gm(784)
    hub = WebHub(gm)
    sess = hub.registry.create(41, "T", channel_id=784)

    async def run():
        await hub.handle_envelope({"type": "client_connected", "cid": 1})
        await hub.handle_envelope({
            "cid": 1,
            "frame": {"type": "join", "token": sess.token, "channel_id": 784},
        })
        inv = hub.manager.get_inventory(784, 41)
        inv.add("dirt_pickaxe", 1)
        inv.add("stone", 5)
        await hub.handle_envelope({
            "cid": 1, "frame": {"type": "select_slot", "slot": 1},
        })
        await hub.stop()

    asyncio.new_event_loop().run_until_complete(run())
    assert hub.manager.get_runtime(784).held_slots.get(41) == 1
    out = []
    while not hub.outbox.empty():
        out.append(hub.outbox.get_nowait())
    helds = [m["frame"] for m in out if m.get("frame", {}).get("type") == "held"]
    assert helds and helds[-1]["item_id"] == "stone"
    s = build_snapshot(hub.manager.get_runtime(784), 41, 1)
    assert s["self"]["held"] == "stone"
