"""Probe the LIVE production relay as a fake web client (diagnostics only).

Connects to the real relay (wss), does a guest login + scenario join, then:
- counts welcome / snapshot frames for N seconds,
- sends held input frames (with seq) and checks the snapshot ack (last_seq)
  and self position drift => is movement ACTUALLY working server-side?

Zero game logic: speaks the same frames web_client/src/net.ts speaks.
Usage:
  .venv/Scripts/python scripts/probe_web_session.py            # auto: list + join first
  .venv/Scripts/python scripts/probe_web_session.py --channel 123456789
  .venv/Scripts/python scripts/probe_web_session.py --move 3   # hold RIGHT 3s then report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

try:
    import websockets  # type: ignore
except ImportError:
    print("pip install websockets first", file=sys.stderr)
    sys.exit(2)

RELAY_WSS = os.environ.get("PROBE_RELAY_URL", "wss://rcatmmo.nexnodesite.xyz/ws")
GUEST_ID = "910000000000000042"  # 9xx… range, probe-only identity


def now() -> float:
    return asyncio.get_event_loop().time()


async def run(channel_id: str | None, move_seconds: float) -> int:
    print(f"[probe] connecting {RELAY_WSS} ...")
    async with websockets.connect(RELAY_WSS, max_size=None) as ws:
        print("[probe] connected -> guest_login")
        await ws.send(json.dumps({"type": "guest_login", "guest_id": GUEST_ID}))
        token = None
        items = None
        # wait for login_result
        deadline = now() + 10
        while now() < deadline:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if frame.get("type") == "login_result":
                if not frame.get("ok"):
                    print("[probe] LOGIN FAILED:", frame)
                    return 1
                token = frame.get("token")
                print(f"[probe] login ok (user {frame.get('user_id')})")
                break
            print("[probe] (pre-login frame)", frame.get("type"))
        if token is None:
            print("[probe] no login_result in time")
            return 1

        # list scenarios if no channel given
        if channel_id is None:
            await ws.send(json.dumps({"type": "list"}))
            deadline = now() + 10
            while now() < deadline:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if frame.get("type") == "scenario_list":
                    items = frame.get("items") or []
                    break
            if not items:
                print("[probe] no scenarios returned")
                return 1
            channel_id = str(items[0].get("channel_id") or items[0].get("id"))
            print(f"[probe] joining first scenario: {channel_id} of {len(items)}")

        await ws.send(
            json.dumps({"type": "join", "token": token, "channel_id": channel_id})
        )

        welcomed = False
        snapshots = 0
        errors: list[str] = []
        last_self = None
        last_seq_ack = None
        sent_seq = 0
        move_until = now() + move_seconds if move_seconds > 0 else 0.0
        start_x = start_y = None

        deadline = now() + (move_seconds + 8 if move_seconds > 0 else 8)
        while now() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline - now()))
            except asyncio.TimeoutError:
                break
            frame = json.loads(raw)
            t = frame.get("type")
            if t == "welcome":
                welcomed = True
                s = frame.get("self", {})
                print(
                    f"[probe] WELCOME ok: map={frame.get('map', {}).get('id')} "
                    f"self=({s.get('x')},{s.get('y')}) manifest={'players_manifest' in frame}"
                )
            elif t == "snapshot":
                snapshots += 1
                s = frame.get("self", {})
                last_self = (s.get("x"), s.get("y"))
                last_seq_ack = s.get("last_seq")
                if start_x is None:
                    start_x, start_y = last_self
            elif t == "error":
                errors.append(frame.get("code", "?"))
            else:
                print("[probe] frame:", t)

            # pump input while in the move window
            if move_until > 0 and now() < move_until:
                sent_seq += 1
                await ws.send(
                    json.dumps(
                        {
                            "type": "input",
                            "seq": sent_seq,
                            "dx": 1.0, "dy": 0.0, "running": False,
                        }
                    )
                )
                await asyncio.sleep(0.1)

        moved = None
        if start_x is not None and last_self is not None:
            moved = (
                (last_self[0] - start_x) ** 2 + (last_self[1] - start_y) ** 2
            ) ** 0.5

        print("---- PROBE RESULT ----")
        print(f"welcomed:        {welcomed}")
        print(f"snapshots in {8 if move_seconds <= 0 else move_seconds + 8:.0f}s: {snapshots}")
        print(f"input frames sent: {sent_seq}")
        print(f"last last_seq ack: {last_seq_ack}")
        print(f"errors:          {errors or 'none'}")
        print(f"self start:      ({start_x}, {start_y})")
        print(f"self last:       {last_self}")
        print(f"drift (tiles):   {moved if moved is not None else 'n/a'}")
        verdict = []
        if not welcomed:
            verdict.append("NO WELCOME -> join rejected / hub dead")
        if snapshots == 0:
            verdict.append("NO SNAPSHOTS -> snapshot loop dead server-side")
        elif snapshots < 20:
            verdict.append(f"few snapshots ({snapshots}) -> loop struggling")
        if sent_seq > 0 and (last_seq_ack in (None, -1, 0)) and snapshots > 0:
            verdict.append("input seq NEVER acked -> input frames dying (handler error?)")
        if moved is not None and moved < 0.1 and sent_seq > 10:
            verdict.append("NO MOVEMENT despite input -> server rejects/integrates nothing")
        print("VERDICT:", "; ".join(verdict) if verdict else "ALL GREEN — server-side fine")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=None)
    ap.add_argument("--move", type=float, default=0.0, help="hold RIGHT for N seconds")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.channel, args.move)))
