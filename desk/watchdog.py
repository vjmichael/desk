"""Maj Vaj watchdog. Run every 2 minutes by a systemd timer, as root.

It may only: restart a service, prune unused Docker images, pause or resume new entries,
and send alerts. It never changes settings, never closes positions and never touches money.

Checks: Freqtrade answers and keeps processing; Desk answers; the fundamentals verdict is
fresh; disk and memory. If everything is fine it pings an outside heartbeat URL (if set), so a
dead droplet is noticed from outside. It also answers Recovery-bot commands from your chat:
/status /pause /resume /restart /help."""
import json
import os
import shutil
import subprocess
import sys
import time

from . import connectors, livemode, net
from .ftapi import FreqtradeAPI, FtError
from .store import Store

STATE = "/var/lib/majvaj/watchdog.json"
MAX_RESTARTS_PER_HOUR = 3
FT_STALE_MIN = 20
ALERT_GAP = {"default": 1800, "verdict": 3 * 3600, "giveup": 2 * 3600}


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = json.load(f)
            return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, s):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f)
    os.replace(tmp, path)


def assess(obs, state, now):
    """Pure decision step. obs keys: ft_ok, ft_age_min, desk_ok, verdict_left_min, disk_pct.
    Returns (actions, alerts, healthy, state). actions: ("restart", unit) or ("prune",)."""
    s = json.loads(json.dumps(state or {}))
    actions, alerts, healthy = [], [], True

    def recent(unit):
        r = [t for t in s.get("restarts", {}).get(unit, []) if now - t < 3600]
        s.setdefault("restarts", {})[unit] = r
        return r

    def alert(key, text, gap="default"):
        last = s.setdefault("alerted", {}).get(key, 0)
        if now - last >= ALERT_GAP.get(gap, ALERT_GAP["default"]):
            s["alerted"][key] = now
            alerts.append(text)

    for unit, ok, why in (("majvaj-freqtrade", obs["ft_ok"] and (obs.get("ft_age_min") is None or obs["ft_age_min"] <= FT_STALE_MIN),
                           "not answering" if not obs["ft_ok"] else "stuck (no processing for %s min)" % obs.get("ft_age_min")),
                          ("desk", obs["desk_ok"], "not answering")):
        key = unit + "_fail"
        if ok:
            if s.get(key, 0) >= 2:
                alerts.append("Recovered: %s is working again." % unit)
            s[key] = 0
            continue
        healthy = False
        s[key] = s.get(key, 0) + 1
        if s[key] < 2:  # one miss can be a blip; act on the second in a row
            continue
        if len(recent(unit)) < MAX_RESTARTS_PER_HOUR:
            s["restarts"][unit].append(now)
            actions.append(("restart", unit))
            alert(unit + "_restart", "Watchdog: %s was %s. Restarting it (attempt %d this hour)." % (unit, why, len(s["restarts"][unit])))
        else:
            alert(unit + "_giveup", "Watchdog: %s is still %s after %d restarts this hour. I've stopped restarting it; "
                                    "it needs you." % (unit, why, MAX_RESTARTS_PER_HOUR), "giveup")

    left = obs.get("verdict_left_min")
    if left is None or left <= 0:
        alert("verdict", "Watchdog: no fresh fundamentals verdict, so the bot is standing down (no new trades). "
                         "Check the Grok key and the Desk page.", "verdict")

    if obs.get("disk_pct", 0) >= 90:
        healthy = False
        actions.append(("prune",))
        alert("disk", "Watchdog: disk %d%% full. Removed unused Docker images; check logs and old models." % obs["disk_pct"])
    return actions, alerts, healthy, s


def observe(cfg, ft, now):
    obs = {"ft_ok": False, "ft_age_min": None, "desk_ok": False, "verdict_left_min": None,
           "disk_pct": 0, "ft_state": None}
    try:
        h = ft.health()
        obs["ft_ok"] = True
        if h.get("last_process_ts"):
            obs["ft_age_min"] = int((now - h["last_process_ts"]) // 60)
        obs["ft_state"] = ft.show_config().get("state")
    except FtError:
        pass
    try:
        status, _ = net.http_json("GET", "http://127.0.0.1:%d/" % int(cfg.get("desk_port", 8765)),
                                  {"Host": "127.0.0.1:%d" % int(cfg.get("desk_port", 8765))}, timeout=10)
        obs["desk_ok"] = status == 200
    except net.NetError:
        pass
    try:
        with open(cfg["verdict_file"], "r", encoding="utf-8") as f:
            obs["verdict_left_min"] = int((float(json.load(f)["expires"]) - now) // 60)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    du = shutil.disk_usage("/")
    obs["disk_pct"] = int(du.used * 100 / du.total)
    return obs


def remember_choice(cfg, trading):
    """Record a /pause or /resume so Desk keeps the bot that way after restarts."""
    path = os.path.join(os.path.dirname(cfg["money_file"]), "control.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    c.update(trading=trading, changed_at=time.time(), changed_by="Recovery bot")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def handle_commands(store, ft, state, obs, run, cfg=None):
    """Answer Recovery-bot commands, only from the saved chat. Returns replies sent."""
    token, chat = store.get("telegram_recovery_token"), store.get("telegram_recovery_chat_id")
    if not token or not chat:
        return []
    try:
        status, data = connectors._tg(token, "getUpdates", {"offset": state.get("tg_offset", 0), "timeout": 0, "limit": 20})
    except net.NetError:
        return []
    if status != 200 or not isinstance(data, dict):
        return []
    replies = []
    for upd in data.get("result", []):
        state["tg_offset"] = upd.get("update_id", 0) + 1
        msg = upd.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat):
            continue  # ignore everyone else
        cmd = (msg.get("text") or "").strip().split()[0].lower() if msg.get("text") else ""
        if cmd == "/status":
            reply = "Bot: %s (%s), last processed %s min ago\nDesk: %s\nVerdict: %s min left\nDisk: %d%%" % (
                "up" if obs["ft_ok"] else "DOWN", obs.get("ft_state"), obs.get("ft_age_min"),
                "up" if obs["desk_ok"] else "DOWN", obs.get("verdict_left_min"), obs["disk_pct"])
        elif cmd == "/pause":
            try:
                ft.pause()
                if cfg:
                    remember_choice(cfg, "paused")
                reply = "Paused: no new entries. Open trades are still managed. Send /resume to undo."
            except FtError as e:
                reply = "Could not pause: %s" % e
        elif cmd == "/resume" and (livemode._read(livemode.STATE_FILE) or {"mode": "paper"}).get("mode") not in ("paper", "live"):
            reply = "Not resumed: the bot is winding down live trades on its way back to paper. It resumes by itself after."
        elif cmd == "/resume":
            try:
                ft.start()
                if cfg:
                    remember_choice(cfg, "running")
                reply = "Resumed: the bot may open trades again (within your money rules and the verdict)."
            except FtError as e:
                reply = "Could not resume: %s" % e
        elif cmd == "/restart":
            run(["systemctl", "restart", "majvaj-freqtrade"])
            reply = "Restarting the trading bot. Open trades are kept in its database."
        elif cmd in ("/help", "/start"):
            reply = "Commands: /status /pause /resume /restart. Nothing here can move money or change limits."
        else:
            continue
        try:
            connectors._tg(token, "sendMessage", {"chat_id": chat, "text": reply})
        except net.NetError:
            pass
        replies.append(reply)
    return replies


def ping_heartbeat(url, healthy):
    if not url:
        return
    try:
        net.http_json("GET", url if healthy else url.rstrip("/") + "/fail", timeout=10)
    except net.NetError:
        pass


def main(config_path="/etc/majvaj/majvaj.json"):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    store = Store(cfg.get("desk_data_dir", "/var/lib/desk"))
    ft = FreqtradeAPI(cfg.get("ft_url", "http://127.0.0.1:8080"), cfg["ft_username"], cfg["ft_password"])
    now = time.time()
    state = load_state(STATE)
    obs = observe(cfg, ft, now)
    actions, alerts, healthy, state = assess(obs, state, now)

    def run(cmd):
        subprocess.run(cmd, check=False, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for a in actions:
        if a[0] == "restart":
            run(["systemctl", "restart", a[1]])
        elif a[0] == "prune":
            run(["docker", "image", "prune", "-f"])
    tok, chat = store.get("telegram_recovery_token"), store.get("telegram_recovery_chat_id")
    for text in alerts:
        if tok and chat:
            try:
                connectors._tg(tok, "sendMessage", {"chat_id": chat, "text": text[:3500]})
            except net.NetError:
                pass
    handle_commands(store, ft, state, obs, run, cfg)

    def recovery(text):
        if tok and chat:
            try:
                connectors._tg(tok, "sendMessage", {"chat_id": chat, "text": text[:3500]})
            except net.NetError:
                pass
    try:
        state["mode_last"] = livemode.apply_pending(
            ft, os.path.join(cfg.get("desk_data_dir", "/var/lib/desk"), "mode_request.json"), now, run, recovery)
    except Exception as e:  # never let a mode problem stop the watchdog
        recovery("Watchdog: mode switch step failed (%s); no change was made." % type(e).__name__)
    ping_heartbeat(cfg.get("heartbeat_url"), healthy)
    state["last_run"] = now
    state["last_obs"] = obs
    save_state(STATE, state)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
