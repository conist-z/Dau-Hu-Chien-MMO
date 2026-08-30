param(
    [switch]$InstallService
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# load .deploy.env
$envVars = @{}
foreach ($line in Get-Content (Join-Path $root ".deploy.env")) {
    if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$') { $envVars[$matches[1]] = $matches[2] }
}
$host_ = $envVars["SFTP_HOST"]
$port = $envVars["SSH_PORT"]
$user = $envVars["SSH_USER"]
$remote = $envVars["DEPLOY_ROOT"]

Set-Location $root

# 1) package source (exclude venv/data/secrets/build artifacts)
$package = Join-Path $root "data\deploy_package.zip"
$py = @'
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
root = Path.cwd()
out = root / "data" / "deploy_package.zip"
out.parent.mkdir(exist_ok=True)
blocked = {"__pycache__", ".venv", "data", ".git", "logs", "reports"}
suffixes = {".pyc", ".db", ".png"}
files = []
for p in root.rglob("*.py"):
    if any(b in p.parts for b in blocked): continue
    files.append(p)
for name in ["bot.py", "config.py", "requirements.txt", "README.md", "AGENTS.md"]:
    pp = root / name
    if pp.exists(): files.append(pp)
for p in root.rglob("assets/**/*"):
    if p.is_file() and p.suffix != ".png": files.append(p)
# keep the json map, drop png; renderer falls back to drawn base if png missing (or include png explicitly):
for p in (root / "assets").rglob("*.png"):
    files.append(p)
with ZipFile(out, "w", ZIP_DEFLATED) as zf:
    for p in sorted(set(files)):
        zf.write(p, p.relative_to(root).as_posix())
print(out)
'@
& .venv\Scripts\python.exe -c $py

# 2) upload package + .env (secret, chmod 600) + service
scp -P $port "$package" "${user}@${host_}:${remote}/deploy_package.zip"
scp -P $port (Join-Path $root ".env") "${user}@${host_}:${remote}/.env"
ssh -p $port "${user}@${host_}" "chmod 600 '${remote}/.env'"

# 3) install on server
$remoteSetup = @"
set -e
mkdir -p '$remote'
python3 -m zipfile -e '$remote/deploy_package.zip' '$remote'
cd '$remote'
if ! python3 -m venv .venv >/dev/null 2>&1; then
  echo 'python3-venv missing'; exit 3
fi
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
"@
ssh -p $port "${user}@${host_}" $remoteSetup

if ($InstallService) {
    scp -P $port (Join-Path $root "infra\discord-map-game.service") "${user}@${host_}:/tmp/discord-map-game.service"
    $svc = @"
set -e
sudo cp /tmp/discord-map-game.service /etc/systemd/system/discord-map-game.service
sudo systemctl daemon-reload
sudo systemctl enable discord-map-game.service
sudo systemctl restart discord-map-game.service
systemctl is-active discord-map-game.service
"@
    ssh -p $port "${user}@${host_}" $svc
}

Write-Output "Deploy done. SSH port used: $port"
