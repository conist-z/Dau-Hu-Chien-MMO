"""Quick single-file SFTP put: rendering/hub_renderer.py -> server.

Same connection settings as upload_tree.py (SFTP port 2022 channel), but
skips the full tree walk so it finishes within the tool timeout.
"""
import os
import pathlib
import socket
import sys

from dotenv import load_dotenv
import paramiko

load_dotenv(".deploy.env")
HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SFTP_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]

REMOTE = sys.argv[1] if len(sys.argv) > 1 else "rendering/hub_renderer.py"
local = pathlib.Path(REMOTE)

t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
t.start_client(timeout=30)
t.auth_password(USER, PASS)
sftp = paramiko.SFTPClient.from_transport(t)
sftp.put(str(local), REMOTE.replace("\\", "/"))
st = sftp.stat(REMOTE.replace("\\", "/"))
print(f"put {REMOTE} -> remote size {st.st_size} (local {local.stat().st_size})")
sftp.close()
t.close()