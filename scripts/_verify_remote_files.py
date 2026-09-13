"""Force-re-upload the given files, then byte-compare with local copies."""
import io
import os
import pathlib
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".deploy.env")

import paramiko  # noqa: E402
import socket  # noqa: E402

HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SFTP_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]

FILES = sys.argv[1:] or [
    "discord_ui/inventory_view.py",
    "discord_ui/hub_view.py",
    "discord_ui/panels.py",
    "game/manager.py",
    "rendering/hub_renderer.py",
    "scripts/upload_tree.py",
]

t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
t.start_client(timeout=30)
t.auth_password(USER, PASS)
sftp = paramiko.SFTPClient.from_transport(t)
sftp.get_channel().settimeout(60)

bad = []
for rel in FILES:
    local_path = ROOT / rel
    sftp.put(str(local_path), "/" + rel)
    buf = io.BytesIO()
    sftp.getfo("/" + rel, buf)
    remote = buf.getvalue()
    local = local_path.read_bytes()
    status = "MATCH" if remote == local else "DIFF!"
    if remote != local:
        bad.append(rel)
    print(f"{status:6s} {rel}  (remote {len(remote)}B / local {len(local)}B)")
sftp.close()
t.close()
print("RESULT:", "ALL MATCH" if not bad else f"MISMATCH: {bad}")
sys.exit(1 if bad else 0)
