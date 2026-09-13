"""Smoke-check: HubRenderer loads the regenerated day/night icons."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from rendering.hub_renderer import HubRenderer

root = pathlib.Path(__file__).resolve().parent.parent
r = HubRenderer(root / "assets" / "maps")
for phase, icon in r._daynight_icons.items():
    print(phase, icon.size)