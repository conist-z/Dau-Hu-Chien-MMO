"""Relay client: the bot dials OUT to the NexNode relay over WebSocket.

Why dial-out: the hosting panel has no openable public inbound port (its only
allocation is the firewalled SSH port), so the relay cannot connect in. An
outbound connection is never blocked, and the relay can push browser frames
down the same socket. One multiplexed socket serves every web client: each
envelope carries a relay-side connection id (``cid``).

Reconnects with capped backoff forever — the web client survives panel
restarts by simply waiting for the bot to dial back in.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import aiohttp

from config import RELAY_TOKEN, RELAY_URL

log = logging.getLogger("WEB")

BACKOFF_START = 2.0
BACKOFF_MAX = 60.0


class RelayClient:
    """Bot-side end of the relay socket. Feeds envelopes into ``WebHub`` and
    drains ``WebHub.outbox`` back to the relay."""

    def __init__(self, hub):
        self.hub = hub
        self._task: Optional[asyncio.Task] = None
        self._ws = None
        self._closing = False

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._closing = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._closing = True
        task = self._task
        self._task = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        if not RELAY_URL:
            # print() instead of log: the panel console may swallow INFO logs.
            print("[WEB] RELAY_URL empty — web gateway disabled", flush=True)
            return
        backoff = BACKOFF_START
        while not self._closing:
            try:
                session = aiohttp.ClientSession()
                try:
                    headers = {"X-Relay-Token": RELAY_TOKEN} if RELAY_TOKEN else {}
                    async with session.ws_connect(
                        RELAY_URL, headers=headers, heartbeat=20.0,
                    ) as ws:
                        self._ws = ws
                        print(f"[WEB] relay connected: {RELAY_URL}", flush=True)
                        backoff = BACKOFF_START
                        await self._pump(ws)
                finally:
                    self._ws = None
                    await session.close()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — reconnect forever
                print(f"[WEB] relay error ({e}); retrying in {backoff:.0f}s", flush=True)
            if self._closing:
                return
            await asyncio.sleep(backoff)
            backoff = min(BACKOFF_MAX, backoff * 2)

    async def _pump(self, ws) -> None:
        """Both directions concurrently until the socket dies."""
        sender = asyncio.create_task(self._send_loop(ws))
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        envelope = json.loads(msg.data)
                    except (TypeError, ValueError):
                        continue
                    await self.hub.handle_envelope(envelope)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                ):
                    break
        finally:
            sender.cancel()
            try:
                await sender
            except asyncio.CancelledError:
                pass

    async def _send_loop(self, ws) -> None:
        """Drain the hub outbox to the relay (never raises into _pump)."""
        while True:
            envelope = await self.hub.outbox.get()
            try:
                await ws.send_str(json.dumps(envelope))
            except (ConnectionError, RuntimeError, TypeError) as e:
                log.warning("[WEB] relay send failed: %s", e)
                await self.hub.outbox.put(envelope)  # put it back; socket died
                raise ConnectionError("relay send failed") from e


def build_relay_client(manager) -> "RelayClient":
    """Factory used by bot.py (keeps web_api import surface minimal there)."""
    from web_api.core import WebHub

    hub = WebHub(manager)
    return RelayClient(hub)
