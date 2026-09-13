"""Render the hub HUD to a PNG + preview inventory content (no Discord needed)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from game.manager import GameManager  # noqa: E402
from rendering.hub_renderer import HubRenderer  # noqa: E402

ASSETS = ROOT / "assets" / "maps"
mgr = GameManager(ASSETS)
rt = mgr.create_runtime(1, "test-map")
rt.state.add_player(10, "Tester", 3, 3)

renderer = HubRenderer(ASSETS)
result = renderer.render(rt, rt.map_data, rt.npc_map)
print("frames:", len(result.frames), "size:", result.image.size)
out = ROOT / "temp_hub_preview.png"
result.frames[0].save(out)
print("saved", out)

# --- Inventory preview -----------------------------------------------------
from discord_ui.inventory_view import InventoryView  # noqa: E402


class _FakeManager:
    """Minimal stand-in exposing only what InventoryView touches."""

    class _Inv:
        items = {"potion_hp": 3, "potion_mp": 1, "key_stone": 2}

    class _RT:
        pass

    def __init__(self):
        self._inv = self._Inv()
        self._rt = self._RT()

    def get_inventory(self, channel_id, user_id):
        return self._inv

    def get_runtime(self, channel_id):
        return self._rt


view = InventoryView(1, 10, _FakeManager())
content, embed = view._render_content()
print("--- inventory content (no selection) ---")
print(content)
print("embed:", embed.title, "|", embed.description)
view._selected_item = "potion_hp"
content, embed = view._render_content()
print("--- inventory content (selected potion_hp) ---")
print(content)
print("embed:", embed.title, "|", embed.description, "| fields:",
      [(f.name, f.value) for f in embed.fields])

