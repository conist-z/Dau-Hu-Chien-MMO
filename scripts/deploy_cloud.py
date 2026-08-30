"""Deploy the Discord map-game bot to the cloud via paramiko (password auth).

Mirrors the Kons cloud pattern (zip -> upload -> venv -> pip install -> systemd),
but uses paramiko because this account only has password auth (no SSH key here).

Usage:
    .venv/Scripts/python.exe scripts/deploy_cloud.py            # upload + install + import check
    .venv\Scripts\python.exe scripts/deploy_cloud.py --service  # also install+enable systemd unit
"""
import os
import pathlib
import sys
import zipfile

from dotenv import load_dotenv

load_dotenv(".deploy.env")

HOST = os.environ["SFTP_HOST"]
PORT = int(os.environ["SSH_PORT"])
USER = os.environ["SSH_USER"]
PASS = os.environ["SFTP_PASS"]
ROOT = os.environ["DEPLOY_ROOT"]

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent


def package() -> pathlib.Path:
    out = ROOT_DIR / "data" / "deploy_package.zip"
    out.parent.mkdir(exist_ok=True)
    blocked = {"__pycache__", ".venv", "data", ".git", "logs", "reports"}
    suffixes = {".pyc", ".db"}
    files = []
    for p in ROOT_DIR.rglob("*.py"):
        if any(b in p.parts for b in blocked):
            continue
        files.append(p)
    for name in ["bot.py", "config.py", "requirements.txt", "README.md", "AGENTS.md"]:
        pp = ROOT_DIR / name
        if pp.exists():
            files.append(pp)
    for p in (ROOT_DIR / "assets").rglob("*"):
        if p.is_file():
            files.append(p)
    files = sorted({p for p in files})
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(ROOT_DIR).as_posix())
    return out


def run(client, cmd, timeout=600):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    print(f"$ {cmd}")
    if out.strip():
        print(out)
    if err.strip():
        print("[err]", err)
    return stdout.channel.recv_exit_status()


def main():
    pkg = package()
    print("packaged", pkg, pkg.stat().st_size, "bytes")

    import socket

    import paramiko

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"connecting {USER}@{HOST}:{PORT} ...")
    # SSHClient.connect stalls on this host; the Transport path works.
    t = paramiko.Transport((socket.gethostbyname(HOST), PORT))
    t.start_client(timeout=30)
    t.auth_password(USER, PASS)
    c._transport = t
    sftp = c.open_sftp()
    print("uploading package + .env ...")
    sftp.put(str(pkg), f"{ROOT}/deploy_package.zip")
    sftp.put(str(ROOT_DIR / ".env"), f"{ROOT}/.env")
    sftp.chmod(f"{ROOT}/.env", 0o600)
    sftp.close()

    run(c, f"mkdir -p {ROOT}")
    run(c, f"python3 -m zipfile -e {ROOT}/deploy_package.zip {ROOT}")
    run(c, f"cd {ROOT} && python3 -m venv .venv")
    run(c, f"cd {ROOT} && . .venv/bin/activate && python -m pip install --upgrade pip")
    run(c, f"cd {ROOT} && . .venv/bin/activate && python -m pip install -r requirements.txt")
    rc = run(c, f"cd {ROOT} && . .venv/bin/activate && python -c \"import bot; print('IMPORT_OK')\"", timeout=120)
    print("import check rc =", rc)

    if "--service" in sys.argv:
        print("installing systemd service (best-effort) ...")
        svc_local = ROOT_DIR / "infra" / "discord-map-game.service"
        sftp2 = c.open_sftp()
        sftp2.put(str(svc_local), "/tmp/discord-map-game.service")
        sftp2.close()
        run(c, f"sudo cp /tmp/discord-map-game.service /etc/systemd/system/discord-map-game.service")
        run(c, "sudo systemctl daemon-reload")
        run(c, "sudo systemctl enable discord-map-game.service")
        run(c, "sudo systemctl restart discord-map-game.service")
        run(c, "systemctl is-active discord-map-game.service")

    c.close()
    print("DONE")


if __name__ == "__main__":
    main()
