import os
from pathlib import Path

# Load .env BEFORE any os.getenv below — bot.py used to call load_dotenv()
# after importing this module, so .env-only vars (e.g. DISCORD_OAUTH_*) were
# read as empty. Panel env vars still win: override=False by default.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT_ROOT / "assets" / "maps"
DATA_DIR = PROJECT_ROOT / "data"
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
USE_DISCORD_AVATARS = os.getenv("USE_DISCORD_AVATARS", "") == "1"

# National weather automation (Open-Meteo, no API key). Refresh default 900s
# = 96 calls/day (~1% of Open-Meteo quota).
WEATHER_ENABLED = os.getenv("WEATHER_ENABLED", "1") == "1"
WEATHER_REFRESH_SEC = int(os.getenv("WEATHER_REFRESH_SEC", "900"))

# Night zombie simulation. Defaults are intentionally sparse; the population
# cap and spawn chance can be tuned from the panel environment without code edits.
ZOMBIES_ENABLED = os.getenv("ZOMBIES_ENABLED", "1") == "1"
ZOMBIE_MAX_COUNT = int(os.getenv("ZOMBIE_MAX_COUNT", "3"))
ZOMBIE_SPAWN_CHANCE = float(os.getenv("ZOMBIE_SPAWN_CHANCE", "0.45"))
# Autonomous world updates. Keep simulation frequent for smooth movement, while
# the Discord coalescer/edit gate decides when a message actually changes.
WORLD_TICK_SECONDS = float(os.getenv("WORLD_TICK_SECONDS", "1.0"))

# --- Pair coupling + periodic auto-refresh (screen/hub lifecycle) ---
# Hub: refreshed every HUB_REFRESH_SECONDS and on any detected state change
# (clock minute tick, weather, HP/mana, coins, inventory/hotbar...). Screen:
# slower SCREEN_REFRESH_SECONDS so the heavier map render/upload stays cheap.
HUB_REFRESH_SECONDS = float(os.getenv("HUB_REFRESH_SECONDS", "5.0"))
SCREEN_REFRESH_SECONDS = float(os.getenv("SCREEN_REFRESH_SECONDS", "8.0"))
# How often each player's screen+hub pair is verified on Discord (fetch).
PAIR_CHECK_SECONDS = float(os.getenv("PAIR_CHECK_SECONDS", "60.0"))

# Hotbar size: the FIRST N stacks of the player's ordered bag are projected
# onto the hotbar automatically (D-pad rail, hub HUD, inventory grid). There
# are no separate hotbar bindings to manage anymore.
HOTBAR_SLOTS = 6

# Per-player session watchdog. A session (one player's screen+hub pair) that
# sees no button press for SESSION_TIMEOUT_MINUTES is ended automatically so
# idle players cannot pile up rendering/loops and starve active ones. 0
# disables the watchdog.
SESSION_TIMEOUT_MINUTES = float(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
SESSION_CHECK_INTERVAL_SEC = float(os.getenv("SESSION_CHECK_INTERVAL_SEC", "60"))
# Minimum seconds between two inactivity notices for the same session, so the
# watchdog can never spam the channel.
SESSION_NOTICE_COOLDOWN_SEC = float(os.getenv("SESSION_NOTICE_COOLDOWN_SEC", "300"))

# --- Silent session repair (default policy) ---
# ANY recoverable glitch (slow load, small server error, mis-ordered stack,
# deleted hub/controls...) triggers a silent repair loop instead of a channel
# notice. The loop retries until the FULL stack screen -> D-pad -> hub exists
# in the right top-to-bottom order, for at most SESSION_REPAIR_TIMEOUT_SEC.
# Only when the whole window expires does it clean up every half-sent message
# and post ONE consolidated error. Authoritative teardowns that skip repair:
# moderator destroys a player's screen (case 2) and inactivity timeout (case 3).
SESSION_REPAIR_TIMEOUT_SEC = float(os.getenv("SESSION_REPAIR_TIMEOUT_SEC", "15"))
SESSION_REPAIR_RETRY_DELAY_SEC = float(os.getenv("SESSION_REPAIR_RETRY_DELAY_SEC", "2"))

# --- Web client (Phaser) gateway + relay ---
# The bot dials OUT to the NexNode relay (no inbound panel port needed); the
# relay fronts the WebSocket for browsers (wss + static client files).
WEB_API_ENABLED = os.getenv("WEB_API_ENABLED", "1") == "1"
# Fallback defaults: some panels drop/.mangle .env vars, so the Railway relay
# coordinates are also baked in here (token matches relay-config; rotate both).
RELAY_URL = os.getenv("RELAY_URL") or "wss://web-production-19398.up.railway.app/bot"
RELAY_TOKEN = os.getenv("RELAY_TOKEN") or "bd3b9c716b74a4abc782a0166de9f9959af83170e00f0493"
# Discord OAuth2 (the bot's own application). Client id is public; the
# secret never leaves the server env.
DISCORD_OAUTH_CLIENT_ID = os.getenv("DISCORD_OAUTH_CLIENT_ID", "")
DISCORD_OAUTH_CLIENT_SECRET = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "")
# Continuous-movement simulation: 20 server ticks/sec, walk/run tile speeds
# (server-authoritative cap — the client never sets its own speed).
WEB_TICK_HZ = float(os.getenv("WEB_TICK_HZ", "20"))
# Halved (15/09: user report — movement feels desynced at high speed; slower
# pace halves the per-tick step so prediction/server divergence shrinks).
# +10% restored (15/09 later: halve felt too sluggish at 2x zoom).
# 15/09 later: idle-converge shipped (heartbeat position is applied on the
# idle branch too), so desync decays within ~1 s even when standing — speeds
# can stay gameplay-friendly instead of being tuned around desync.
WEB_WALK_SPEED = float(os.getenv("WEB_WALK_SPEED", "2.5"))   # tiles/sec
WEB_RUN_SPEED = float(os.getenv("WEB_RUN_SPEED", "4.15"))    # tiles/sec (Shift)
# ---- Stamina (generous by design: a long sprint before it runs out, and
# running out only SOFTENS actions — never a hard gate) ----
STAMINA_MAX = float(os.getenv("STAMINA_MAX", "120"))
STAMINA_RUN_DRAIN = float(os.getenv("STAMINA_RUN_DRAIN", "4"))     # /s while sprinting (50 s of running)
STAMINA_CHOP_DRAIN = float(os.getenv("STAMINA_CHOP_DRAIN", "2"))  # /s while hitting nodes/blocks
STAMINA_REGEN = float(os.getenv("STAMINA_REGEN", "10"))           # /s refills in ~20 s
STAMINA_REGEN_DELAY_S = float(os.getenv("STAMINA_REGEN_DELAY_S", "1.0"))  # grace after exertion
STAMINA_TIRED_MULT = 0.5  # harvest damage multiplier once stamina is empty
# ---- Eating (consumables): chew window + anti-spam ----
EAT_DURATION_S = float(os.getenv("EAT_DURATION_S", "1.6"))   # chew time
EAT_COOLDOWN_S = float(os.getenv("EAT_COOLDOWN_S", "1.5"))   # Kaetram EDIBLE_COOLDOWN
EAT_SPEED_MULT = 0.5   # move speed multiplier while eating
WEB_AIM_RANGE_TOLERANCE = int(os.getenv("WEB_AIM_RANGE_TOLERANCE", "1"))  # lag slack for click targeting
# Max simultaneous web players per scenario (design ceiling; extra joins wait).
WEB_MAX_PLAYERS = int(os.getenv("WEB_MAX_PLAYERS", "15"))
# Display name of the MAIN server in the web lobby picklist (user spec:
# one world named "Demo by conist"; new players auto-join it).
DEMO_SERVER_NAME = os.getenv("DEMO_SERVER_NAME", "Demo by conist")
