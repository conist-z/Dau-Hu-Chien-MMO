"""One-shot relay pipe smoke test (local, no game server needed).

Starts relay.js, dials a fake bot into /bot, opens a fake browser /ws, sends
a join frame browser->bot and a reply bot->browser. Prints PASS/FAIL.
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

RELAY_DIR = Path(__file__).resolve().parent.parent / "web_client" / "relay"
PORT = 8899


async def main() -> int:
    proc = subprocess.Popen(
        ["node", "relay.js"],
        cwd=RELAY_DIR,
        env={"PORT": str(PORT), "PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )
    await asyncio.sleep(1.0)
    try:
        session = aiohttp.ClientSession()
        # Fake bot dials in.
        bot_ws = await session.ws_connect(f"http://127.0.0.1:{PORT}/bot")
        # Fake browser connects.
        browser_ws = await session.ws_connect(f"http://127.0.0.1:{PORT}/ws")

        # Browser -> relay -> bot: client_connected arrives first, then {cid, frame}.
        await browser_ws.send_str(json.dumps({"type": "ping", "t": 7}))
        first = json.loads((await asyncio.wait_for(bot_ws.receive(), 5)).data)
        assert first["type"] == "client_connected", first
        cid = first["cid"]
        envelope = json.loads((await asyncio.wait_for(bot_ws.receive(), 5)).data)
        assert envelope["frame"]["type"] == "ping", envelope
        assert envelope["frame"]["t"] == 7
        assert envelope["cid"] == cid

        # Bot -> relay -> browser.
        await bot_ws.send_str(json.dumps({"cid": cid, "frame": {"type": "welcome", "ok": 1}}))
        reply = json.loads((await asyncio.wait_for(browser_ws.receive(), 5)).data)
        assert reply["type"] == "welcome", reply

        # Static shell served.
        async with session.get(f"http://127.0.0.1:{PORT}/config.json") as resp:
            cfg = await resp.json()
        assert "client_id" in cfg

        await bot_ws.close()
        await browser_ws.close()
        await session.close()
        print("RELAY_SMOKE_PASS")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"RELAY_SMOKE_FAIL: {e}")
        return 1
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
