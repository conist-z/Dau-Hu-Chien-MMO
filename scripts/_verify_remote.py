import os, socket, pathlib
import paramiko
from dotenv import load_dotenv

root = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(root / ".deploy.env")
t = paramiko.Transport((socket.gethostbyname(os.environ["SFTP_HOST"]), int(os.environ["SFTP_PORT"])))
t.start_client(timeout=30)
t.auth_password(os.environ["SSH_USER"], os.environ["SFTP_PASS"])
sftp = paramiko.SFTPClient.from_transport(t)
local = (root / "rendering" / "weather_fx.py").stat().st_size
remote = sftp.stat("/rendering/weather_fx.py").st_size
print("local :", local)
print("remote:", remote)
print("MATCH" if local == remote else "MISMATCH - re-upload needed")
sftp.close(); t.close()
