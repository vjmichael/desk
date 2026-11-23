"""FreqAI tab: the model, its training data, and backtests.

Desk (unprivileged) only reads files and records a backtest request. The watchdog (root, every 2 minutes)
starts the backtest as a separate background job, so the trading bot and the watchdog never wait on it:
  1. refresh the bot's candle files from Desk's history database,
  2. run Freqtrade backtesting with FreqAI on the chosen period, limited to 1 CPU, using its own model folder
     (the live model is never touched),
  3. write the exit code; the next watchdog run marks the job done or failed.
Results are read from Freqtrade's own result files."""
import glob
import json
import os
import shlex
import subprocess
import time
import zipfile
from datetime import datetime, timedelta, timezone

from .pairs import UNIT as PAIRS_UNIT, applied, refresh_data_script

RANGES = {"3m": ("Last 3 months", 91), "6m": ("Last 6 months", 182), "12m": ("Last 12 months", 365),
          "24m": ("Last 2 years", 730)}
UNIT = "majvaj-backtest"


def paths(user_data):
    mv = os.path.join(user_data, "majvaj")
    return {"ud": user_data, "mv": mv, "request": os.path.join(mv, "backtest_request.json"),
            "state": os.path.join(mv, "backtest_state.json"), "exit": os.path.join(mv, "backtest_exit"),
            "override": os.path.join(mv, "backtest_override.json"), "config": os.path.join(user_data, "config-4h.json"),
            "results": os.path.join(user_data, "backtest_results"), "models": os.path.join(user_data, "models")}


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def summarize(zip_path):
    """Headline numbers from one Freqtrade backtest result file."""
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith(".json") and not n.endswith("_config.json"))
        data = json.loads(z.read(name))
    st = next(iter(data["strategy"].values()))
    trades = st.get("total_trades") or 0
    return {"file": os.path.basename(zip_path), "start": st.get("backtest_start"), "end": st.get("backtest_end"),
            "trades": trades, "wins": st.get("wins"), "losses": st.get("losses"),
            "profit_pct": round((st.get("profit_total") or 0) * 100, 2), "profit_usd": round(st.get("profit_total_abs") or 0, 2),
            "market_pct": round((st.get("market_change") or 0) * 100, 2),
            "max_drawdown_pct": round((st.get("max_drawdown_account") or 0) * 100, 2),
            "winrate_pct": round((st.get("winrate") or 0) * 100, 1), "model": st.get("freqai_identifier"),
            "starting": st.get("starting_balance"), "final": st.get("final_balance")}


class Lab:
    """Desk side (read-only plus the request file)."""

    def __init__(self, user_data, clock=time.time):
        self.p, self.clock = paths(user_data), clock

    def model(self):
        cfg = _read(self.p["config"]) or {}
        fa = cfg.get("freqai") or {}
        ident = fa.get("identifier")
        trained = None
        if ident:
            subs = glob.glob(os.path.join(self.p["models"], ident, "sub-train-*"))
            if subs:
                trained = max(os.path.getmtime(s) for s in subs)
        fp = fa.get("feature_parameters") or {}
        return {"identifier": ident, "last_trained": trained, "retrain_hours": fa.get("live_retrain_hours"),
                "train_days": fa.get("train_period_days"), "label_candles": fp.get("label_period_candles"),
                "timeframes": fp.get("include_timeframes"), "pairs": applied(self.p["ud"]),
                "timeframe": cfg.get("timeframe")}

    def results(self, limit=6):
        files = sorted(glob.glob(os.path.join(self.p["results"], "backtest-result-*.zip")), reverse=True)[:limit]
        out = []
        for f in files:
            try:
                out.append(summarize(f))
            except Exception:
                out.append({"file": os.path.basename(f), "error": "could not read this result"})
        return out

    def status(self):
        return {"model": self.model(), "backtest": _read(self.p["state"]) or {"status": "idle"},
                "pending": _read(self.p["request"]), "results": self.results(), "ranges": {k: v[0] for k, v in RANGES.items()}}

    def request(self, range_key):
        if range_key not in RANGES:
            return {"ok": False, "message": "Pick one of the periods."}
        st = _read(self.p["state"]) or {}
        if st.get("status") == "running" or os.path.exists(self.p["request"]):
            return {"ok": False, "message": "A backtest is already queued or running."}
        _write(self.p["request"], {"range": range_key, "at": self.clock()})
        return {"ok": True, "message": "Queued. It starts within 2 minutes and runs in the background; "
                                       "a FreqAI backtest retrains the model as it goes, so allow 10-40 minutes."}


# ------------------------------------------------------------------ root side (watchdog)
def _unit_active(run_out):
    return run_out(["systemctl", "is-active", UNIT]).strip() in ("active", "activating")


def tick(user_data, image, desk_history="/var/lib/desk/history.db", now=None, run_out=None, notify=lambda t: None):
    """Start a queued backtest, or record how a finished one ended. Returns a short note or None."""
    p, now = paths(user_data), now or time.time()
    run_out = run_out or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout)
    state = _read(p["state"]) or {"status": "idle"}
    if state.get("status") == "running":
        if _unit_active(run_out):
            return "backtest running"
        try:
            with open(p["exit"], "r", encoding="utf-8") as f:
                code = int(f.read().strip() or 1)
        except (OSError, ValueError):
            code = 1
        state.update(status="done" if code == 0 else "failed", finished=now, exit_code=code)
        if code:
            state["message"] = "Backtest failed. Details: journalctl -u %s -n 50" % UNIT
        _write(p["state"], state)
        notify("Maj Vaj backtest %s (%s)." % ("finished" if code == 0 else "FAILED", state.get("label")))
        return "backtest " + state["status"]
    req = _read(p["request"])
    if not req:
        return None
    if run_out(["systemctl", "is-active", PAIRS_UNIT]).strip() in ("active", "activating"):
        return "backtest waiting for a coin change to finish"   # both write the bot's data folder
    os.remove(p["request"])
    if not image or req.get("range") not in RANGES:
        _write(p["state"], {"status": "failed", "finished": now, "message": "No bot image known; rerun the installer."})
        return "backtest refused"
    label, days = RANGES[req["range"]]
    start = (datetime.fromtimestamp(now, timezone.utc) - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")   # FreqAI backtests need an end date
    ident = ((_read(p["config"]) or {}).get("freqai") or {}).get("identifier") or "majvaj-4h"
    _write(p["override"], {"freqai": {"identifier": ident + "-backtest"}})
    os.chown(p["override"], 1000, 1000)
    ud = user_data
    q = shlex.quote
    pairs_cfg = "--config /freqtrade/user_data/config.pairs.json " if os.path.exists(os.path.join(ud, "config.pairs.json")) else ""
    script = " && ".join(refresh_data_script(ud, image, desk_history) + [
        "docker run --rm --name majvaj-backtest --cpus 1 --memory 2g -v %s:/freqtrade/user_data %s backtesting "
        "--config /freqtrade/user_data/config-4h.json %s--config /freqtrade/user_data/majvaj/backtest_override.json "
        "--strategy MajVajFreqAI4h --freqaimodel LightGBMRegressor --timerange %s-%s --export trades" % (q(ud), q(image), pairs_cfg, start, end),
    ])
    wrapped = "(%s); echo $? > %s" % (script, q(p["exit"]))
    try:
        os.remove(p["exit"])
    except OSError:
        pass
    run_out(["systemd-run", "--unit", UNIT, "--collect", "--quiet", "/bin/sh", "-c", wrapped])
    _write(p["state"], {"status": "running", "started": now, "range": req["range"], "label": label, "from": start})
    notify("Maj Vaj backtest started: %s (from %s). It runs in the background." % (label, start))
    return "backtest started"
