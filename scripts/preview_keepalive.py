"""Keeper for the preview stack: restarts it if it dies and prints the URL.

Run this instead of running _preview_stack.py directly:
    .venv/Scripts/python scripts/preview_keepalive.py

Why: Freebuff restarts kill background processes (the stack included) but the
Preview tab registration then points at a dead port — "preview mat". This
keeper loops forever, restarting the stack within ~2s of any crash, so a
re-register of the same URL is all a new session ever needs.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
STACK = ROOT / "scripts" / "_preview_stack.py"
LOG = ROOT / ".preview_stack.log"

URL = "http://127.0.0.1:8898/?preview=1"

print(f"[keepalive] preview stack keeper running — URL stays {URL}", flush=True)
while True:
    started = time.time()
    try:
        with open(LOG, "w", encoding="utf-8") as log:
            proc = subprocess.run(
                [str(PY), str(STACK)],
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT),
            )
        code = proc.returncode
    except KeyboardInterrupt:
        print("[keepalive] bye", flush=True)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        code = f"spawn-error {exc!r}"
    uptime = time.time() - started
    print(f"[keepalive] stack exited code={code} after {uptime:.0f}s — restarting in 2s", flush=True)
    if uptime < 1.0:
        time.sleep(2)
