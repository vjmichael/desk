#!/bin/bash
# Maj Vaj full install / update (Debian, run as root). Safe to run again; keeps keys, trades and settings.
#   Desk (web page, Grok fundamentals, reports)  ->  systemd service "desk"
#   Freqtrade + FreqAI in dry-run (paper)         ->  systemd service "majvaj-freqtrade" (Docker)
#   Watchdog every 2 minutes                      ->  systemd timer "majvaj-watchdog"
# Optional: HEARTBEAT_URL=https://hc-ping.com/<your-id> bash majvaj-install.sh   (outside dead-droplet alarm)
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run this as root."; exit 1; }
IMAGE="freqtradeorg/freqtrade:stable_freqai"
FT=/opt/majvaj/ft; UD=$FT/user_data; MV=$UD/majvaj; ETC=/etc/majvaj; CFG=$ETC/majvaj.json

echo "== 1/8 Desk (code from GitHub)"
python3 -c "import urllib.request as u; open('/root/setup.sh','w').write(u.urlopen('https://raw.githubusercontent.com/vjmichael/desk/main/deploy/setup.sh').read().decode())"
bash /root/setup.sh >/dev/null
SRC=/opt/desk/freqtrade

echo "== 2/8 Docker and swap"
command -v docker >/dev/null || { apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io >/dev/null; }
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || echo '{"log-driver":"journald"}' > /etc/docker/daemon.json
systemctl enable --now docker >/dev/null 2>&1
if ! swapon --show | grep -q .; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/majvaj.conf
systemctl restart systemd-journald

echo "== 3/8 Freqtrade image"
docker pull -q "$IMAGE" >/dev/null
DIGEST=$(docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE")
echo "   $(docker run --rm "$DIGEST" --version | tail -1)  ($DIGEST)"

echo "== 4/8 Strategy, configs and secrets"
mkdir -p "$UD/strategies" "$UD/tools" "$UD/data/kraken" "$UD/logs" "$MV" "$FT/work" "$ETC"
cp "$SRC/user_data/config-4h.json" "$UD/config-4h.json"
cp "$SRC/user_data/strategies/"*.py "$UD/strategies/"
cp "$SRC/user_data/tools/"*.py "$UD/tools/"
if [ ! -f "$UD/config.private.json" ]; then
  python3 - "$UD/config.private.json" "$CFG" "${HEARTBEAT_URL:-}" <<'PY'
import json, secrets, sys, os
priv, cfg, hb = sys.argv[1], sys.argv[2], sys.argv[3]
user, pw = "majvaj", secrets.token_urlsafe(24)
json.dump({"dry_run": True, "api_server": {"enabled": True, "listen_ip_address": "0.0.0.0", "listen_port": 8080,
           "verbosity": "error", "enable_openapi": False, "jwt_secret_key": secrets.token_hex(32),
           "ws_token": secrets.token_urlsafe(24), "CORS_origins": [], "username": user, "password": pw}},
          open(priv, "w"), indent=2)
c = {}
if os.path.exists(cfg):
    c = json.load(open(cfg))
c.update({"ft_url": "http://127.0.0.1:8080", "ft_username": user, "ft_password": pw})
c.setdefault("verdict_file", "/opt/majvaj/ft/user_data/majvaj/verdict.json")
c.setdefault("money_file", "/opt/majvaj/ft/user_data/majvaj/money.json")
c.setdefault("desk_data_dir", "/var/lib/desk"); c.setdefault("desk_port", 8765); c.setdefault("evening_hour", 21)
c.setdefault("heartbeat_url", "")
json.dump(c, open(cfg, "w"), indent=2)
PY
fi
if [ -n "${HEARTBEAT_URL:-}" ]; then
  python3 -c "import json,sys; c=json.load(open('$CFG')); c['heartbeat_url']=sys.argv[1]; json.dump(c,open('$CFG','w'),indent=2)" "$HEARTBEAT_URL"
fi
chown -R 1000:1000 "$UD"; chmod 600 "$UD/config.private.json"
chown desk:1000 "$MV"; chmod 755 "$MV"
chown root:desk "$CFG"; chmod 640 "$CFG"

echo "== 5/8 Strategy self-test (money rules, profit protection, fundamentals gate)"
docker run --rm --entrypoint python -v "$SRC:/src:ro" -w /src "$DIGEST" -m unittest discover -s tests -t . 2>&1 | tail -n 3

echo "== 6/8 History for the model (from Desk's Kraken download)"
if [ -f /var/lib/desk/history.db ]; then
  python3 -c "import sqlite3; s=sqlite3.connect('file:/var/lib/desk/history.db?mode=ro',uri=True); d=sqlite3.connect('$FT/work/history.db'); s.backup(d); d.close()"
  chmod 644 "$FT/work/history.db"
  docker run --rm --entrypoint python -v "$UD:/freqtrade/user_data" -v "$FT/work:/work:ro" "$DIGEST" \
    /freqtrade/user_data/tools/convert_history.py /work/history.db /freqtrade/user_data/data/kraken
  chown -R 1000:1000 "$UD/data"
else
  echo "   No Desk history yet: press Download history on the Desk page, then run this again."; exit 1
fi

echo "== 7/8 Services"
# Size the bot to this machine: leave half a CPU and 1.5 GB for Desk and the system.
CPUS=$(python3 -c "import os; print(max(1.0, (os.cpu_count() or 1) - 0.5))")
MEM=$(python3 -c "import os; t=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')/2**20; print('%dm' % max(1024, min(5120, t-1536)))")
JOBS=$(python3 -c "import os; print(max(1, (os.cpu_count() or 1)))")
python3 -c "import json,sys; p=sys.argv[1]; c=json.load(open(p)); c['freqai']['model_training_parameters']['n_jobs']=int(sys.argv[2]); json.dump(c,open(p,'w'),indent=4)" "$UD/config-4h.json" "$JOBS"
echo "   bot limits: $CPUS CPUs, $MEM memory, $JOBS training threads"
cat > /etc/systemd/system/majvaj-freqtrade.service <<UNIT
[Unit]
Description=Maj Vaj trading bot (Freqtrade + FreqAI, dry-run)
Requires=docker.service
After=docker.service network-online.target desk.service
Wants=network-online.target
StartLimitIntervalSec=3600
StartLimitBurst=6

[Service]
ExecStartPre=-/usr/bin/docker rm -f majvaj-ft
ExecStart=/usr/bin/docker run --rm --name majvaj-ft --memory $MEM --cpus $CPUS -p 127.0.0.1:8080:8080 \\
  -v $UD:/freqtrade/user_data $DIGEST trade \\
  --config /freqtrade/user_data/config-4h.json --config /freqtrade/user_data/config.private.json \\
  --strategy MajVajFreqAI4h --freqaimodel LightGBMRegressor \\
  --db-url sqlite:////freqtrade/user_data/tradesv3.dryrun.sqlite
ExecStop=/usr/bin/docker stop -t 60 majvaj-ft
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/majvaj-watchdog.service <<UNIT
[Unit]
Description=Maj Vaj watchdog (restart, pause, alert, heartbeat)

[Service]
Type=oneshot
WorkingDirectory=/opt/desk
ExecStart=/usr/bin/python3 -m desk.watchdog $CFG
TimeoutStartSec=300
UNIT
cat > /etc/systemd/system/majvaj-watchdog.timer <<UNIT
[Unit]
Description=Run the Maj Vaj watchdog every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl restart desk
systemctl enable majvaj-freqtrade >/dev/null 2>&1
systemctl reset-failed majvaj-freqtrade 2>/dev/null || true
systemctl restart majvaj-freqtrade
systemctl enable --now majvaj-watchdog.timer >/dev/null 2>&1

echo "== 8/8 Check"
sleep 20
for u in desk majvaj-freqtrade majvaj-watchdog.timer; do printf '   %-24s %s\n' "$u" "$(systemctl is-active $u)"; done
echo
echo "Done. The bot is training its model on startup (several minutes) and needs a fresh Grok verdict"
echo "before it may open trades. Watch it on the Desk page -> 'Maj Vaj trading bot'."
echo "Update later by running this same script again."
