"""Grep-like helper (findstr is unreliable through this shell)."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
pattern = sys.argv[1].strip('"')
paths = [a.strip('"') for a in sys.argv[2:]] or ["discord_ui", "game", "rendering", "bot.py"]
rx = re.compile(pattern)
for base in paths:
    p = ROOT / base
    files = [p] if p.is_file() else sorted(p.rglob("*.py"))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                print(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
