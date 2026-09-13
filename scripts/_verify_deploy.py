"""Upload specific files (avoid pushing concurrently-edited files)."""
import hashlib
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import os
from dotenv import load_dotenv

load_dotenv(".deploy.env")
import paramiko
import socket

HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SFTP_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]

FILES = [
    "discord_ui/map_view.py",
]

t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
t.start_client(timeout=30)
t.auth_password(USER, PASS)
sftp = paramiko.SFTPClient.from_transport(t)

root = pathlib.Path(__file__).resolve().parent.parent
for rel in FILES:
    sftp.put(str(root / rel), "/" + rel)
    print("put", rel, flush=True)

ok = True
for rel in FILES:
    local_hash = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    remote_hash = hashlib.sha256(sftp.open("/" + rel, "rb").read()).hexdigest()
    match = local_hash == remote_hash
    ok = ok and match
    print(rel, "MATCH" if match else "MISMATCH!", flush=True)
sftp.close()
t.close()
print("ALL OK" if ok else "CHECK FAILED")

