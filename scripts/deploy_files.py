"""Deploy a handful of files in seconds (SFTP put, same account as
upload_tree.py). Usage:

    .venv/Scripts/python scripts/deploy_files.py config.py bot.py web_api/core.py

Paths are project-root relative; remote dirs are created as needed.
"""
import os
import pathlib
import sys

import paramiko
from dotenv import load_dotenv

load_dotenv(".deploy.env")

HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SFTP_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]

ROOT = pathlib.Path(__file__).resolve().parent.parent


def find_remote_root(sftp) -> str:
    """The dir holding bot.py (panel layout may vary)."""
    for base in ("/", "/home/container", "/home/container/discord-map-game"):
        try:
            names = sftp.listdir(base)
        except IOError:
            continue
        if "bot.py" in names or ".env" in names:
            return base.rstrip("/") or "/"
    return "/"


def main() -> int:
    files = sys.argv[1:]
    if not files:
        print("usage: deploy_files.py <file> [file...]")
        return 1
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, PORT, username=USER, password=PASS,
              timeout=15, look_for_keys=False, allow_agent=False)
    sftp = c.open_sftp()
    root = find_remote_root(sftp)
    made_dirs = set()
    for f in files:
        local = ROOT / f
        if not local.is_file():
            print("SKIP (no such file):", f)
            continue
        parts = pathlib.PurePosixPath(f).parts
        remote_dir = root if len(parts) == 1 else root + "/" + "/".join(parts[:-1])
        if remote_dir not in made_dirs:
            # mkdir -p for nested dirs
            cur = root
            for seg in (pathlib.PurePosixPath(f).parts[:-1]):
                cur = cur + "/" + seg
                if cur not in made_dirs:
                    try:
                        sftp.mkdir(cur)
                    except IOError:
                        pass
                    made_dirs.add(cur)
            made_dirs.add(remote_dir)
        remote = root + "/" + f.replace("\\", "/")
        sftp.put(str(local), remote)
        print("OK", f)
    sftp.close()
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
