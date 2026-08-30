import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = PROJECT_ROOT / "assets" / "maps"
DATA_DIR = PROJECT_ROOT / "data"
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
USE_DISCORD_AVATARS = os.getenv("USE_DISCORD_AVATARS", "") == "1"
