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

# Always run the newest installer: fetch it from GitHub, then hand over to it once.
if [ "${MAJVAJ_FRESH:-}" != 1 ]; then
  python3 -c "import urllib.request as u; open('/root/.majvaj-install.new','w').write(u.urlopen('https://raw.githubusercontent.com/vjmichael/desk/main/deploy/majvaj-install.sh').read().decode())" \
    && bash -n /root/.majvaj-install.new && mv /root/.majvaj-install.new /root/majvaj-install.sh \
    && MAJVAJ_FRESH=1 exec bash /root/majvaj-install.sh "$@"
  echo "   Could not fetch the newest installer; continuing with this copy."
fi

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
# The coins the bot trades (chosen on the Desk's FreqAI tab; the watchdog rewrites this file on a change).
[ -f "$UD/config.pairs.json" ] || python3 -c "import json,sys; c=json.load(open(sys.argv[1])); json.dump({'exchange': {'pair_whitelist': c['exchange']['pair_whitelist']}}, open(sys.argv[2],'w'))" "$UD/config-4h.json" "$UD/config.pairs.json"
# Strategy settings chosen on the Desk's FreqAI tab (entry threshold, stop, take-profit, look-ahead). Desk rewrites this file.
[ -f "$MV/config.strategy.json" ] || echo '{}' > "$MV/config.strategy.json"
[ -f "$UD/config.mode.json" ] || echo '{"dry_run": true, "db_url": "sqlite:////freqtrade/user_data/tradesv3.dryrun.sqlite"}' > "$UD/config.mode.json"
chown -R 1000:1000 "$UD"; chmod 600 "$UD/config.private.json" "$UD/config.mode.json"
chown desk:1000 "$MV"; chmod 775 "$MV"   # Desk writes settings and verdicts here; the bot (uid 1000) writes its decision log
python3 -c "import json,sys; p=sys.argv[1]; c=json.load(open(p)); c['ft_image']=sys.argv[2]; json.dump(c,open(p,'w'),indent=2)" "$CFG" "$DIGEST"
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
# A saved model only fits the strategy version that trained it. Tie the model name to the strategy code, so a
# strategy update trains a fresh model instead of crashing on the old one ("different features").
MODEL_ID="majvaj-4h-$(cat "$UD/strategies/"*.py | sha256sum | cut -c1-10)"
python3 -c "import json,sys; p=sys.argv[1]; c=json.load(open(p)); c['freqai']['model_training_parameters']['n_jobs']=int(sys.argv[2]); c['freqai']['identifier']=sys.argv[3]; json.dump(c,open(p,'w'),indent=4)" "$UD/config-4h.json" "$JOBS" "$MODEL_ID"
# A look-ahead chosen on the Desk page gives the model its own name (base name + -L<candles>). The base name changes
# whenever the strategy code does, so recompute that suffix now, or a stale name would reuse a model that no longer fits.
python3 -c "import json,os,sys; p=sys.argv[1]; c=json.load(open(p)) if os.path.exists(p) else {}; fa=c.setdefault('freqai',{}); n=(fa.get('feature_parameters') or {}).get('label_period_candles'); (fa.__setitem__('identifier', sys.argv[2]+'-L%d'%n) if n and n!=18 else fa.pop('identifier',None)); json.dump(c,open(p,'w'),indent=1)" "$MV/config.strategy.json" "$MODEL_ID"
echo "   bot limits: $CPUS CPUs, $MEM memory, $JOBS training threads; model $MODEL_ID"
cat > /etc/systemd/system/majvaj-freqtrade.service <<UNIT
[Unit]
Description=Maj Vaj trading bot (Freqtrade + FreqAI; paper unless switched live from the Desk page)
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
  --config /freqtrade/user_data/config.mode.json --config /freqtrade/user_data/config.pairs.json \\
  --config /freqtrade/user_data/majvaj/config.strategy.json --strategy MajVajFreqAI4h --freqaimodel LightGBMRegressor
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
# Instant apply: the moment Desk records a request (paper/live, coin change, restart button), run the apply steps
# instead of waiting for the next 2-minute watchdog round.
cat > /etc/systemd/system/majvaj-apply.service <<UNIT
[Unit]
Description=Maj Vaj: apply a request from the Desk page (paper/live, coins, restart)

[Service]
Type=oneshot
WorkingDirectory=/opt/desk
ExecStart=/usr/bin/python3 -m desk.watchdog --apply $CFG
TimeoutStartSec=300
UNIT
cat > /etc/systemd/system/majvaj-apply.path <<UNIT
[Unit]
Description=Watch for requests from the Maj Vaj Desk page

[Path]
PathExists=/var/lib/desk/mode_request.json
PathExists=/var/lib/desk/pairs_request.json
PathExists=/var/lib/desk/restart_request.json
Unit=majvaj-apply.service

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl restart desk
systemctl enable majvaj-freqtrade >/dev/null 2>&1
systemctl reset-failed majvaj-freqtrade 2>/dev/null || true
systemctl restart majvaj-freqtrade
systemctl enable --now majvaj-watchdog.timer >/dev/null 2>&1
systemctl enable majvaj-apply.path >/dev/null 2>&1; systemctl restart majvaj-apply.path

echo "== 8/8 Check"
sleep 20
for u in desk majvaj-freqtrade majvaj-watchdog.timer majvaj-apply.path; do printf '   %-24s %s\n' "$u" "$(systemctl is-active $u)"; done
echo
echo "Done. If the strategy did not change, the bot reuses its saved model and is up in about a minute."
echo "If it changed, it trains a new model first (a few minutes). It also needs a fresh Grok verdict"
echo "before it may open trades. Watch it on the Desk page."
echo "Update later by running this same script again (it fetches its own newest version first)."
echo "Live trading: add a trade-enabled Kraken key on the Keys tab, then use the switch on the Paper settings tab."
echo "Coins: choose them on the FreqAI tab (BTC and ETH always stay). New coins train before they can trade."
