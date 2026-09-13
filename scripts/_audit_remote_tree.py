"""Read-only audit: walk the local tree (same skip rules as upload_tree.py)
and sha256-compare every file with its remote SFTP counterpart.

Usage:  .venv/Scripts/python scripts/_audit_remote_tree.py
Exit code 0 = all match; 1 = stale/missing files found (printed).
"""
import hashlib
import io
import os
import pathlib
import socket
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".deploy.env")

import paramiko  # noqa: E402

HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SFTP_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]

SKIP_DIRS = {".venv", "data", ".git", "__pycache__", ".pytest_cache", ".freebuff", ".kilo"}
SKIP_FILES = {
    "deploy_log.txt", "deploy_resume_log.txt", "audit_log.txt",
    "temp_hub_preview.png", "temp_upload_log.txt", "temp_upload_log.err",
}

t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
t.start_client(timeout=30)
t.auth_password(USER, PASS)
sftp = paramiko.SFTPClient.from_transport(t)
sftp.get_channel().settimeout(60)
home = (sftp.getcwd() or "/").rstrip("/")

stale, missing, checked = [], [], 0


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def walk(local: pathlib.Path, rel: str = ""):
    global checked
    for child in sorted(local.iterdir()):
        if child.is_dir():
            if child.name in SKIP_DIRS or child.name.startswith("."):
                continue
            walk(child, rel + child.name + "/")
            continue
        if child.name.startswith(".") or child.name in SKIP_FILES:
            continue
        remote_path = home + "/" + rel + child.name
        try:
            buf = io.BytesIO()
            sftp.getfo(remote_path, buf)
            remote_hash = sha256(buf.getvalue())
        except IOError:
            missing.append(rel + child.name)
            continue
        checked += 1
        if remote_hash != sha256(child.read_bytes()):
            stale.append(rel + child.name)


walk(ROOT)
print(f"checked {checked} files; stale={len(stale)} missing={len(missing)}")
for name in stale:
    print("  STALE  ", name)
for name in missing:
    print("  MISSING", name)
sftp.close()
t.close()
sys.exit(1 if (stale or missing) else 0)
