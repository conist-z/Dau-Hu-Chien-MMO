"""Grep the deploy log for the code files we care about + crash context."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
log = ROOT / "deploy_resume_log.txt"
lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
TARGETS = [
    "discord_ui/inventory_view.py",
    "discord_ui/hub_view.py",
    "discord_ui/panels.py",
    "game/manager.py",
    "rendering/hub_renderer.py",
    "scripts/upload_tree.py",
]
for t in TARGETS:
    hits = [ln for ln in lines if t in ln]
    print(t, "->", hits[-1] if hits else "NOT IN LOG")
actions = [ln for ln in lines if ("  put " in ln or "  skip" in ln)]
print("last 5 action lines before crash:")
for ln in actions[-5:]:
    print("  ", ln)
print("total lines:", len(lines), "| last:", lines[-1] if lines else "-")

