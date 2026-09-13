import asyncio
import logging
from typing import Dict, Optional, Tuple

import discord

log = logging.getLogger("GAME")

# Minimum spacing between edits to the SAME message (seconds). Discord's PATCH
# /channels/{id}/messages/{id} bucket is ~5 edits per 5 seconds (~1 edit/s
# sustained) — the original 0.2s assumption was 5x too aggressive and produced
# 429s with a 5s stall (see the [RATELIMIT]/429 logs). Player button presses do
# NOT use this lane at all (they go through the per-interaction token endpoint,
# see MapView._instant_frame); this gate only paces background updates
# (auto-move loops, hub re-renders). 1.1s = ~0.9 edits/s, safely under the
# ~1/s refill with the initial burst of 5 intact.
MIN_EDIT_SPACING = 1.1
# Defensive cap: never block an edit lane longer than this (should never trigger).
MAX_WAIT = 2.0


class ChannelEditGate:
    """Serialize Discord message edits per (channel, message) and space them out.

    Many button presses, the auto-move loop, and the hub re-render all edit the
    SAME map (or hub) message. Discord's per-(channel,message) edit bucket is
    small; concurrent hits 429 (the warning you saw). This gate funnels every real
    edit through one lane per message:

    - edits to the SAME message are serialized (one at a time),
    - consecutive edits are spaced by ``MIN_EDIT_SPACING``,
    - edits to DIFFERENT messages (map vs hub) run in parallel.

    The interaction ACKs themselves are moved to ``interaction.response.defer()``
    (a different, per-interaction endpoint) so they never touch this bucket.
    """

    def __init__(self, spacing: float = MIN_EDIT_SPACING):
        self.spacing = spacing
        self._locks: Dict[Tuple[int, int], asyncio.Lock] = {}
        self._last: Dict[Tuple[int, int], float] = {}

    def _key(self, channel_id: int, message_id: int) -> Tuple[int, int]:
        return (channel_id, message_id)

    def _lock(self, key: Tuple[int, int]) -> asyncio.Lock:
        lk = self._locks.get(key)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[key] = lk
        return lk

    async def edit_message(
        self,
        channel,
        message_id: Optional[int],
        *,
        attachments=None,
        embed=None,
        view=None,
        content=None,
    ) -> None:
        if message_id is None or channel is None:
            return
        key = self._key(channel.id, message_id)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        async with self._lock(key):
            elapsed = loop.time() - self._last.get(key, 0.0)
            wait = self.spacing - elapsed
            if wait > 0:
                await asyncio.sleep(min(wait, MAX_WAIT))
            gate_wait = loop.time() - t0
            # PartialMessage: no fetch round-trip. The message_id is known
            # (persisted in SQLite), so GET /channels/{id}/messages/{id} before
            # every PATCH wasted ~100-300ms per frame. PATCH works on partials.
            msg = channel.get_partial_message(message_id)
            t1 = loop.time()
            edit_kwargs = {"embed": embed, "view": view, "content": content}
            # discord.py expects an iterable when attachments is supplied;
            # omitting it preserves the existing image on component-only
            # edits (for example refreshing the lobby hotbar labels).
            if attachments is not None:
                edit_kwargs["attachments"] = attachments
            await msg.edit(**edit_kwargs)
            edit_secs = loop.time() - t1
            self._last[key] = loop.time()
            if gate_wait > self.spacing + 0.5 or edit_secs > 1.5:
                log.info(
                    "[FPS] chan=%s msg=%s gate_wait=%.2fs edit=%.2fs (upload heavy "
                    "or rate-limited — check concurrent editors)",
                    key[0], key[1], gate_wait, edit_secs,
                )
