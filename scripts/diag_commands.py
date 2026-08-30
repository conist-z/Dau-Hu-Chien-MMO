"""Diagnose which commands are registered for this bot's application.

Uses the HTTP API only (no gateway login) so it does not disconnect the
running cloud bot.
"""
import asyncio
import os

from dotenv import load_dotenv
import aiohttp

load_dotenv(".env")
TOKEN = os.environ["DISCORD_TOKEN"]
API = "https://discord.com/api/v10"
HEADERS = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}


async def main():
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{API}/users/@me", headers=HEADERS) as r:
            me = await r.json()
        app_id = me["id"]
        print("APP ID:", app_id, "| username:", me.get("username"))

        async with s.get(f"{API}/applications/{app_id}/commands", headers=HEADERS) as r:
            g = await r.json()
        print(f"\nGLOBAL COMMANDS ({len(g)}):")
        for c in g:
            print("  -", c.get("name"), "(type", c.get("type"), ")")

        async with s.get(f"{API}/users/@me/guilds", headers=HEADERS) as r:
            guilds = await r.json()
        for g_ in guilds:
            gid = g_["id"]
            async with s.get(f"{API}/applications/{app_id}/guilds/{gid}/commands", headers=HEADERS) as r:
                gc = await r.json()
            print(f"\nGUILD {g_['name']} ({gid}) COMMANDS ({len(gc)}):")
            for c in gc:
                print("  -", c.get("name"), "(type", c.get("type"), ")")


asyncio.run(main())
