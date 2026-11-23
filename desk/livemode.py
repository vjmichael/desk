"""Paper / live switch (Maj Vaj TB-LIVE-01).

Paper is the default. The switch is on the Maj Vaj page and the decision is the owner's: going live needs
only the trade-enabled Kraken key (saved on the Keys page), the full warning acknowledged and the typed
phrase. The readiness checks (paper days, closed trades, Grok reliability, watchdog) are shown as advice,
not as a lock.

Desk only records the confirmed request. The root-side applier (started within seconds by a path unit; the 2-minute watchdog round is the fallback)
performs the switch. Returning to paper stops new real entries at once and keeps managing open real trades
("live-exit-only") until they are closed; only then does the bot go back to paper. Paper and live keep
separate trade databases. Nothing here closes positions or moves money."""
import json
import os
import sys
import time

ETC = "/etc/majvaj"
DESK_SECRETS = "/var/lib/desk/secrets.json"
STATE_FILE = "/var/lib/majvaj/mode_state.json"
WATCHDOG_STATE = "/var/lib/majvaj/watchdog.json"
FT_UD = "/opt/majvaj/ft/user_data"
MODE_CONFIG = FT_UD + "/config.mode.json"
LIVE_SECRET = "/etc/majvaj/live_exchange.json"
WARNING_VERSION = "TB-LIVE-01 v1"
PHRASE_LIVE, PHRASE_PAPER = "ENABLE LIVE TRADING", "RETURN TO PAPER"
MIN_PAPER_DAYS, MIN_PAPER_TRADES, MIN_CHECK_OK = 7, 10, 0.9  # advice; 7 days of live paper trading: AI_Pirate
REQUEST_MAX_AGE = 900

WARNING_OFF = ("Enable live trading? Maj Vaj will be authorized to place actual orders using real money in the selected "
               "account, within your approved capital and risk limits. Trading can result in losses, including loss of "
               "all capital committed to trading. Maj Vaj does not guarantee profit, returns, or execution at any "
               "particular price. Protective orders may fail, be delayed or execute at worse prices. Exchange, market, "
               "network and software failures can affect results. Review the account, authorized amount, risk limits "
               "and open obligations before confirming. Paper balances and positions are not real funds. They will not "
               "transfer to LIVE.")
WARNING_ON = ("Return to paper trading? New real-money entries will cease on confirmation. Existing live orders and "
              "positions may still expose you to financial loss. Canceling pending orders does not necessarily cancel "
              "fills that have already occurred. Closing positions may realize losses and incur fees; leaving positions "
              "unmanaged can cause further losses. Paper trading will not manage your real assets. Existing protective "
              "management continues (live-exit-only) until all live positions are closed.")

PAPER_MODE = {"dry_run": True, "db_url": "sqlite:////freqtrade/user_data/tradesv3.dryrun.sqlite"}
LIVE_DB = "sqlite:////freqtrade/user_data/tradesv3.live.sqlite"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj, mode=0o640, owner=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.chmod(tmp, mode)
    if owner:
        os.chown(tmp, *owner)
    os.replace(tmp, path)


# ------------------------------------------------------------------ readiness (pure)
def readiness(now, paper_since, closed_paper_trades, checks_7d, watchdog_state, money):
    """Advice before going live: list of (name, ok, detail). None of these block the switch."""
    ok_checks = [c for c in checks_7d if c.get("ok")]
    rate = len(ok_checks) / len(checks_7d) if checks_7d else 0.0
    giveups = [t for k, t in ((watchdog_state or {}).get("alerted") or {}).items()
               if k.endswith("_giveup") and now - t < 7 * 86400]
    days = (now - paper_since) / 86400 if paper_since else 0
    return [
        ("Paper trading for at least %d days" % MIN_PAPER_DAYS, days >= MIN_PAPER_DAYS, "%.1f days so far" % days),
        ("At least %d closed paper trades" % MIN_PAPER_TRADES, closed_paper_trades >= MIN_PAPER_TRADES,
         "%d so far" % closed_paper_trades),
        ("Fundamentals checks working (90%% over 7 days)", len(checks_7d) >= 7 and rate >= MIN_CHECK_OK,
         "%d of %d ok" % (len(ok_checks), len(checks_7d))),
        ("Watchdog never had to give up in 7 days", not giveups, "%d give-up(s)" % len(giveups)),
    ]


def blockers(trade_key_saved, money, untrained=(), pairs_pending=False):
    """The only things that stop the switch: without them live trading cannot work at all, or a coin could go live
    before its model is trained."""
    out = []
    if not trade_key_saved:
        out.append("Save a trade-enabled Kraken key on the Keys page (section 'Kraken trade key').")
    if not (money and money.get("bankroll", 0) > 0):
        out.append("Set an authorized bankroll in Money rules.")
    if untrained:
        out.append("Wait until every coin has a trained model (still training: %s)." % ", ".join(untrained))
    if pairs_pending:
        out.append("Wait until the coin change on the FreqAI tab is applied.")
    return out


# ------------------------------------------------------------------ desk side
class ModeDesk:
    """Used by the Desk page. Validates and records requests; never touches the bot's config."""

    def __init__(self, data_dir, meta, clock=time.time, state_file=STATE_FILE, watchdog_file=WATCHDOG_STATE):
        self.request_file = os.path.join(data_dir, "mode_request.json")
        self.audit_file = os.path.join(data_dir, "mode_audit.jsonl")
        self.meta, self.clock = meta, clock
        self.state_file, self.watchdog_file = state_file, watchdog_file

    def state(self):
        return _read(self.state_file) or {"mode": "paper"}

    def checks(self, closed_paper_trades, checks_7d, money):
        paper_since = float(self.meta("paper_since") or 0)
        return readiness(self.clock(), paper_since, closed_paper_trades, checks_7d, _read(self.watchdog_file), money)

    def _audit(self, entry):
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(entry, at=self.clock())) + "\n")

    def request(self, to, phrase, ack, blocking, scope, advice_failed=()):
        now = self.clock()
        if to == "paper":
            if (phrase or "").strip().upper() != PHRASE_PAPER or not ack:
                return {"ok": False, "message": 'Nothing changed. Type "%s" and tick the box to confirm.' % PHRASE_PAPER}
            _write(self.request_file, {"to": "paper", "at": now, "warning_version": WARNING_VERSION}, 0o644)
            self._audit({"decision": "return_to_paper", "warning_version": WARNING_VERSION})
            return {"ok": True, "message": "New real entries are being stopped now. Open live trades stay managed until "
                                           "they close; then the bot returns to paper."}
        if to != "live":
            return {"ok": False, "message": "Unknown mode."}
        if blocking:
            return {"ok": False, "message": "Not switched: " + " ".join(blocking)}
        if (phrase or "").strip().upper() != PHRASE_LIVE or not ack:
            return {"ok": False, "message": 'Nothing changed. Type "%s" and tick the acknowledgement.' % PHRASE_LIVE}
        _write(self.request_file, {"to": "live", "at": now, "warning_version": WARNING_VERSION, "scope": scope}, 0o644)
        self._audit({"decision": "live_confirmed", "warning_version": WARNING_VERSION, "scope": scope,
                     "advice_not_met": list(advice_failed)})
        return {"ok": True, "message": "Confirmed. The bot restarts on LIVE now; it is back in about a minute."}


# ------------------------------------------------------------------ root side (watchdog)
def trade_key(desk_secrets=DESK_SECRETS, live_secret=LIVE_SECRET):
    """The trade-enabled key in use on the Keys page, or an older console-installed file."""
    from .store import active_value, load_entries
    entries = load_entries(desk_secrets)
    k, s = active_value(entries, "kraken_trade_key"), active_value(entries, "kraken_trade_secret")
    if k and s:
        return {"key": k, "secret": s}
    s = _read(live_secret) or {}
    return {"key": s["key"], "secret": s["secret"]} if s.get("key") and s.get("secret") else None


def apply_pending(ft, request_file, now=None, run=None, notify=lambda t: None, state_file=STATE_FILE,
                  mode_config=MODE_CONFIG, live_secret=LIVE_SECRET, desk_secrets=DESK_SECRETS):
    """Carry out a recorded request. Returns a short description of what happened, or None."""
    now = now or time.time()
    req, state = _read(request_file), _read(state_file) or {"mode": "paper"}
    if not req:
        if state.get("mode") == "live_exit_only":
            return _finish_exit_only(ft, state, run, notify, state_file, mode_config)
        return None
    to = req.get("to")
    if to == "paper":
        try:
            ft.pause()
        except Exception:
            pass
        os.remove(request_file)
        if state.get("mode") == "paper":
            return "already paper"
        state = {"mode": "live_exit_only", "since": now}
        _write(state_file, state, 0o644)
        notify("Maj Vaj: returning to paper. No new real entries; open live trades are still managed until they close.")
        return _finish_exit_only(ft, state, run, notify, state_file, mode_config)
    if to == "live":
        os.remove(request_file)
        secret = trade_key(desk_secrets, live_secret)
        if now - float(req.get("at", 0)) > REQUEST_MAX_AGE or not secret:
            notify("Maj Vaj: a live request was ignored (older than 15 minutes, or no trade key saved).")
            return "live request refused"
        mode = {"dry_run": False, "db_url": LIVE_DB, "exchange": {"key": secret["key"], "secret": secret["secret"]}}
        _write(mode_config, mode, 0o600, (1000, 1000))
        _write(state_file, {"mode": "live", "since": now, "scope": req.get("scope")}, 0o644)
        if run:
            run(["systemctl", "restart", "majvaj-freqtrade"])
        notify("Maj Vaj is now LIVE with real money, within: %s" % json.dumps(req.get("scope")))
        return "switched to live"
    os.remove(request_file)
    return "unknown request dropped"


def _finish_exit_only(ft, state, run, notify, state_file, mode_config):
    try:
        ft.pause()
        open_trades = ft.status()
    except Exception:
        return "live-exit-only: bot not answering, will retry"
    if open_trades:
        return "live-exit-only: %d live trade(s) still open" % len(open_trades)
    _write(mode_config, PAPER_MODE, 0o600, (1000, 1000))
    _write(state_file, {"mode": "paper", "since": time.time()}, 0o644)
    if run:
        run(["systemctl", "restart", "majvaj-freqtrade"])
    notify("Maj Vaj: all live trades closed; the bot is back on paper.")
    return "returned to paper"


# ------------------------------------------------------------------ console
def main(argv):
    """Read-only status for the console: python3 -m desk.livemode (run in /opt/desk)."""
    print(json.dumps({"state": _read(STATE_FILE) or {"mode": "paper"}, "trade_key_saved": bool(trade_key())}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
