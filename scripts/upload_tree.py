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
SKIP_DIRS = {".venv", "data", ".git", "__pycache__", ".pytest_cache"}


def upload(local: pathlib.Path, remote: str, sftp):
    if local.is_dir():
        try:
            sftp.mkdir(remote)
        except IOError:
            pass
        for child in sorted(local.iterdir()):
            if child.name in SKIP_DIRS or child.name.startswith("."):
                continue
            upload(child, remote + "/" + child.name, sftp)
    else:
        sftp.put(str(local), remote)


def main():
    import paramiko

    t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
    t.start_client(timeout=30)
    t.auth_password(USER, PASS)
    sftp = paramiko.SFTPClient.from_transport(t)

    # Pterodactyl: the SFTP home IS the container's /home/container.
    # Do NOT nest under an extra subdir or the panel won't find bot.py.
    home = (sftp.getcwd() or "/").rstrip("/")
    if not home:
        home = "/"
    print("SFTP home (maps to container /home/container):", home)

    print("mirroring tree ->", home)
    upload(ROOT_DIR, home, sftp)
    sftp.put(str(ROOT_DIR / ".env"), home + "/.env")
    sftp.chmod(home + "/.env", 0o600)
    print("verifying remote tree at", home)
    for rel in ["bot.py", "requirements.txt", "game/state.py", "discord_ui/commands.py",
                "rendering/renderer.py", "assets/maps/test-map.json", ".env"]:
        try:
            sftp.stat(home + "/" + rel)
            print("  OK ", rel)
        except IOError:
            print("  MISSING", rel)
    sftp.close()
    t.close()
    print("DONE")


if __name__ == "__main__":
    main()
