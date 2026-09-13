"""Mirror the whole project tree to the cloud via SFTP (no remote exec needed).

The conist.559348bb@node2.nexnodecloud.xyz:2022 account only allows the SFTP
subsystem (remote exec is blocked), so we push files directly. Once exec/SSH
access is available (e.g. port 30112 from a reachable network, or the host
panel), run:

    cd /home/conist.559348bb/discord-map-game
    python3 -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
    python bot.py        # or enable the systemd unit in infra/
"""
import os
import pathlib
import socket

from dotenv import load_dotenv

load_dotenv(".deploy.env")

HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SFTP_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]
ROOT = os.environ["DEPLOY_ROOT"]

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".venv", "data", ".git", "__pycache__", ".pytest_cache",
    "web_client",  # web client deploys to NexNode (relay + static), never the panel
    "node_modules",
}
# Volatile local files that must never ship to the server (they change or
# vanish while a deploy is in flight -> FileNotFoundError / corrupted upload).
SKIP_FILES = {
    "deploy_log.txt",
    "deploy_resume_log.txt",
    "audit_log.txt",
    "temp_hub_preview.png",
    "temp_upload_log.txt",
    "temp_upload_log.err",
    "temp_fx_wind_check.png",
}


def _remote_same_size(sftp, local: pathlib.Path, remote: str) -> bool:
    """True when the remote file already exists with the same size.

    Makes a re-run after a dropped connection cheap: fully-uploaded files are
    skipped, so the deploy resumes where it stopped (idempotent mirror)."""
    try:
        st = sftp.stat(remote)
    except IOError:
        return False
    try:
        return st.st_size == local.stat().st_size
    except OSError:
        return False


def upload(local: pathlib.Path, remote: str, sftp):
    if local.is_dir():
        try:
            sftp.mkdir(remote)
        except IOError:
            pass
        for child in sorted(local.iterdir()):
            if child.name in SKIP_DIRS or child.name in SKIP_FILES:
                continue
            if child.name.startswith("."):
                continue
            upload(child, remote + "/" + child.name, sftp)
    else:
        if _remote_same_size(sftp, local, remote):
            print("  skip (already up to date)", remote, flush=True)
            return
        try:
            sftp.put(str(local), remote)
        except FileNotFoundError:
            print("  skip (vanished mid-deploy)", remote, flush=True)
            return
        print("  put", remote, flush=True)


def upload_top_level_files(local: pathlib.Path, remote: str, sftp):
    """Upload only files directly inside ``local`` (no recursion)."""
    for child in sorted(local.iterdir()):
        if child.is_file() and not child.name.startswith("."):
            sftp.put(str(child), remote + "/" + child.name)
            print("  put", remote + "/" + child.name, flush=True)


def clear_remote_pycache(sftp, remote: str):
    """Delete __pycache__ dirs and *.pyc to invalidate stale bytecode
    (PYTHONDONTWRITEBYTECODE=1 prevents NEW .pyc but old ones persist
    and may have future mtime due to SFTP put)."""
    try:
        entries = sftp.listdir(remote)
    except IOError:
        return
    for entry in entries:
        full = remote + "/" + entry
        try:
            if entry == "__pycache__":
                files = sftp.listdir(full)
                for f in files:
                    if f.endswith(".pyc"):
                        sftp.remove(full + "/" + f)
                sftp.rmdir(full)
            elif entry.endswith(".pyc"):
                sftp.remove(full)
            elif "." not in entry:  # subdir
                clear_remote_pycache(sftp, full)
        except (IOError, OSError):
            pass


def main():
    import argparse

    import paramiko

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="upload only this relative subdir (repeatable). '.' = top-level files only. No flag = full tree.",
    )
    args = ap.parse_args()

    t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
    t.start_client(timeout=30)
    t.auth_password(USER, PASS)
    t.set_keepalive(15)
    sftp = paramiko.SFTPClient.from_transport(t)
    # Never block forever on a dead connection: any SFTP op that sees no data
    # for 60s raises, so a stalled deploy can simply be re-run (files already
    # uploaded are skipped via the same-size check in upload()).
    sftp.get_channel().settimeout(60)

    # Pterodactyl: the SFTP home IS the container's /home/container.
    # Do NOT nest under an extra subdir or the panel won't find bot.py.
    home = (sftp.getcwd() or "/").rstrip("/")
    if not home:
        home = "/"
    print("SFTP home (maps to container /home/container):", home)

    if args.only:
        for rel in args.only:
            if rel == ".":
                print("mirroring top-level files ->", home)
                upload_top_level_files(ROOT_DIR, home, sftp)
            else:
                src = ROOT_DIR / rel
                print("mirroring", rel, "->", home + "/" + rel)
                upload(src, home + "/" + rel, sftp)
    else:
        print("mirroring tree ->", home)
        upload(ROOT_DIR, home, sftp)
    sftp.put(str(ROOT_DIR / ".env"), home + "/.env")
    sftp.chmod(home + "/.env", 0o600)
    print("clearing stale __pycache__ on remote...")
    clear_remote_pycache(sftp, home)
    print("verifying remote tree at", home)
    for rel in ["bot.py", "requirements.txt", "game/state.py", "game/resources.py", "game/session.py",
                "discord_ui/commands.py", "discord_ui/session_end.py",
                "rendering/renderer.py", "rendering/weather_fx.py", "rendering/hub_renderer.py",
                "assets/fx/rain/rain_00.png", "assets/maps/test-map.json", ".env"]:
        try:
            sftp.stat(home + "/" + rel)
            print("  OK ", rel)
        except IOError:
            print("  MISSING", rel)
    sftp.close()
    t.close()
    print("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never swallow silently during a deploy
        import traceback

        traceback.print_exc()
        raise
