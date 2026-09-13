"""Surgical deploy: push ONLY the files changed by the build-mode feature.

Uses the same SFTP account as upload_tree.py (via .deploy.env) but skips the
full-tree __pycache__ sweep so the whole run finishes in seconds. Stale .pyc
under the touched packages is removed directly; source mtimes change on put so
Python would recompile anyway (belt and braces).
"""
import os
import pathlib
import socket
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".deploy.env")

HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SFTP_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]

FILES = [
    "game/actions.py",
    "game/state.py",
    "game/blocks.py",
    "game/rules.py",
    "game/manager.py",
    "rendering/renderer.py",
    "discord_ui/map_view.py",
]
PYCACHE_DIRS = ["game", "rendering", "discord_ui"]


def main():
    import paramiko

    t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
    t.start_client(timeout=30)
    t.auth_password(USER, PASS)
    t.set_keepalive(15)
    sftp = paramiko.SFTPClient.from_transport(t)
    sftp.get_channel().settimeout(60)

    home = (sftp.getcwd() or "/").rstrip("/")
    print("SFTP home (maps to container /home/container):", home, flush=True)

    for rel in FILES:
        sftp.put(str(ROOT / rel), home + "/" + rel)
        print("  put", rel, flush=True)

    for d in PYCACHE_DIRS:
        pc = home + "/" + d + "/__pycache__"
        try:
            for f in sftp.listdir(pc):
                if f.endswith(".pyc"):
                    sftp.remove(pc + "/" + f)
                    print("  rm pyc", d + "/__pycache__/" + f, flush=True)
        except IOError:
            pass  # no pycache dir (or already clean)

    print("verifying remote files...", flush=True)
    ok = True
    for rel in FILES:
        try:
            st = sftp.stat(home + "/" + rel)
            local_size = (ROOT / rel).stat().st_size
            mark = "OK " if st.st_size == local_size else "SIZE-MISMATCH"
            ok = ok and st.st_size == local_size
            print(" ", mark, rel, st.st_size, flush=True)
        except IOError:
            ok = False
            print("  MISSING", rel, flush=True)

    sftp.close()
    t.close()
    print("DONE" if ok else "DONE (with mismatches — re-run!)", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never swallow silently during a deploy
        import traceback

        traceback.print_exc()
        raise
