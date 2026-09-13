"""Show the tail of deploy_resume_log.txt + whether upload is still running."""
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
log = ROOT / "deploy_resume_log.txt"
if not log.exists():
    print("NO LOG YET")
else:
    text = log.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    print("LINES:", len(lines))
    print("\n".join(lines[-25:]))
    # crude liveness: has the log grown in the last 3 seconds?
    s1 = log.stat().st_size
    time.sleep(3)
    s2 = log.stat().st_size
    print("GROWING" if s2 != s1 else ("DONE" if "DONE" in lines[-3:] else "STALLED?"))
