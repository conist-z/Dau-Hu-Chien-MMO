import discord
import logging

log = logging.getLogger("GAME")


async def enforce_adjacency(
    channel, screen_msg_id: int, hub_msg_id: int, preserve_ids=(), suppress_ids=None
) -> int:
    """Ensure one player's message stack is contiguous.

    The valid split layout is ``screen → controls → hub``. Callers must pass
    every message that belongs to the stack in ``preserve_ids``; otherwise a
    repair sweep would mistake the controls message for an orphan and delete
    it, which then starts another recovery cycle.
    ``suppress_ids`` (optional set) receives every id this sweep deletes so
    bot.py's deleted-message hook never misreads the bot's OWN cleanup as
    "someone deleted a player's screen" (that false positive ended live
    sessions and left a lone hub behind — the reported bug).
    Returns the number of messages removed.

    Requires the bot to have Manage Messages in the channel; callers should
    treat a permission error as non-fatal (skip, log).
    """
    if not screen_msg_id or not hub_msg_id:
        return 0
    preserved = {int(mid) for mid in preserve_ids if mid}
    removed = 0
    try:
        async for msg in channel.history(
            limit=50,
            before=discord.Object(id=hub_msg_id),
            after=discord.Object(id=screen_msg_id),
        ):
            if msg.id in preserved:
                continue
            try:
                await msg.delete()
                if suppress_ids is not None:
                    suppress_ids.add(msg.id)
                removed += 1
            except Exception:
                pass
    except Exception as e:
        log.warning("[ADJ] cannot sweep between %s..%s: %s", screen_msg_id, hub_msg_id, e)
    return removed
