#!/bin/bash
# Desk server setup / update for Debian. Run as root. Safe to run again: it updates the code and restarts.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run this as root."; exit 1; }
REPO_ZIP="https://github.com/vjmichael/desk/archive/refs/heads/main.zip"
APP=/opt/desk
DATA=/var/lib/desk

id desk >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin desk
mkdir -p "$APP" "$DATA"
chown desk:desk "$DATA"; chmod 700 "$DATA"

TMP=$(mktemp -d)
python3 - "$REPO_ZIP" "$TMP" <<'PY'
import sys, urllib.request, zipfile, io
data = urllib.request.urlopen(sys.argv[1], timeout=60).read()
zipfile.ZipFile(io.BytesIO(data)).extractall(sys.argv[2])
PY
SRC=$(ls -d "$TMP"/desk-*)
rm -rf "$APP/desk" "$APP/tests"
cp -r "$SRC/desk" "$APP/desk"
cp -r "$SRC/tests" "$APP/tests"
cp "$SRC/README.md" "$APP/README.md"
rm -rf "$TMP"
chown -R root:root "$APP"

cat > /etc/systemd/system/desk.service <<UNIT
[Unit]
Description=Desk paper trading
After=network-online.target
Wants=network-online.target

[Service]
User=desk
WorkingDirectory=$APP
ExecStart=/usr/bin/python3 -m desk --no-browser --port 8765 --data-dir $DATA --token-file $DATA/token --resume
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable desk >/dev/null 2>&1
systemctl restart desk
sleep 3
systemctl is-active desk
VER=$(python3 -c "import sys; sys.path.insert(0,'$APP'); import desk; print(desk.__version__)")
IP=$(hostname -I | awk '{print $1}')
echo
echo "Desk $VER is running as a service (restarts itself, resumes paper trading after a reboot)."
echo "On your Windows PC, open a PowerShell window and run:"
echo "  ssh -L 8765:127.0.0.1:8765 root@<DROPLET-IP>"
echo "then open this address in your browser:"
echo "  http://127.0.0.1:8765/?t=$(cat "$DATA/token")"
echo "(the droplet's address may be $IP; use the one shown in DigitalOcean)"
