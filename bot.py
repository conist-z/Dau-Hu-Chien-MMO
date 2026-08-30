import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import ASSETS_DIR, DATA_DIR, DISCORD_TOKEN, USE_DISCORD_AVATARS
from discord_ui.commands import MapCog
from discord_ui.coalescer import RenderCoalescer
from discord_ui.map_view import MapView, flush_render_batch
from game.manager import GameManager
from persistence.database import Database
from persistence.migrations import migrate
from persistence.repositories import load_players, load_scenarios
from rendering.avatar import AvatarCache
from rendering.renderer import Renderer

load_dotenv()

intents = discord.Intents.default()


class GameBot(commands.Bot):
    async def close(self):
        # Flush any debounced player saves before the event loop tears down.
        await manager.flush_all()
        await super().close()


bot = GameBot(command_prefix="!", intents=intents)

manager = GameManager(ASSETS_DIR)
avatar_cache = AvatarCache(use_network=USE_DISCORD_AVATARS)
renderer = Renderer(ASSETS_DIR, avatar_cache)
db = Database(str(DATA_DIR / "game.db"))


@bot.event
async def setup_hook():
    await db.connect()
    await migrate(db)
    manager.renderer = renderer
    manager.db = db
    # Coalesce rapid emoji presses into a single render+upload per channel.
    manager.coalescer = RenderCoalescer(
        delay=0.08,
        on_flush=lambda cid, batch: flush_render_batch(manager, cid, batch),
    )

    for sc in await load_scenarios(db):
        rt = manager.create_runtime(sc["channel_id"], sc["map_id"], sc["message_id"])
        for p in await load_players(db, sc["channel_id"]):
            player = rt.state.add_player(p["user_id"], p.get("display_name", ""), p["x"], p["y"])
            player.direction = p["direction"]
            player.sprite_id = p["sprite_id"]
            player.visible = p["visible"]
            # Restored coords may be out of bounds / inside a wall after a map
            # edit (the map is remapped to its asset bounding box on load).
            if not rt.map_data.is_walkable(player.x, player.y):
                player.x, player.y = rt.map_data.spawn
        bot.add_view(MapView(sc["channel_id"], manager))

    await bot.add_cog(MapCog(bot, manager, renderer, db))

    # Discord apps may carry an "Entry Point" command (raw API type 4, used by
    # Activities/App-launch) that a bulk global sync is forbidden from removing.
    # Delete it separately first, otherwise tree.sync() raises HTTP 50240.
    try:
        existing = await bot.http.get_global_commands(bot.application_id)
        for cmd in existing:
            if cmd.get("type") == 4:
                await bot.http.delete_global_command(bot.application_id, cmd["id"])
                print(f"[BOOT] removed entry-point command {cmd.get('name')!r}")
    except Exception as e:
        print(f"[WARN] could not prune entry-point command: {e}")

    await bot.tree.sync()
    print(f"[BOOT] restored {len(manager.runtimes)} scenario(s)")


@bot.event
async def on_ready():
    print(f"[READY] {bot.user} ({bot.user.id})")
    # Sync per-guild so commands appear instantly (global commands can take
    # minutes to propagate). This also replaces any stale guild commands.
    for g in bot.guilds:
        try:
            await bot.tree.sync(guild=discord.Object(id=g.id))
        except Exception as e:
            print(f"[WARN] guild sync failed for {g.id}: {e}")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN missing in .env")
    bot.run(DISCORD_TOKEN)
