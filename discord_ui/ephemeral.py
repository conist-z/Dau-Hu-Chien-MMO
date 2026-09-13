"""Shared auto-delete lifetimes for ephemeral ("Chỉ bạn mới có thể nhìn thấy")
messages. discord.py supports ``delete_after`` on interaction responses natively
(2.x), so each send just passes one of these constants."""

# A — errors / validation ("Chưa có map.", "Chọn item trước.", ...)
EPHEMERAL_WARN = 6.0
# B — transient combat / action feedback (attack hits, chop results)
EPHEMERAL_ACTION = 3.5
# C — success confirmations ("Đã rời map.", "Đã đổi thời tiết.", ...)
EPHEMERAL_OK = 7.0


async def send_ephemeral_followup(interaction, content: str, delete_after: float) -> None:
    """discord.py 2.7.1: ``Webhook.send`` (interaction.followup) has NO
    delete_after — only ``InteractionResponse`` does. Send the ephemeral
    followup, then schedule the delete manually (WebhookMessage.delete
    supports the interaction-token webhook delete endpoint)."""
    import asyncio

    msg = await interaction.followup.send(content, ephemeral=True)
    if msg is None or delete_after is None:
        return

    async def _del():
        await asyncio.sleep(delete_after)
        try:
            await msg.delete()
        except (discord.NotFound, discord.HTTPException, asyncio.CancelledError):
            pass

    asyncio.create_task(_del())
