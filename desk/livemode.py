"""Paper / live switch (Maj Vaj TB-LIVE-01).

Paper is the default and the safe direction. Going live needs ALL of:
  - every readiness check passing (it stays locked until then),
  - the exact warning acknowledged, a typed confirmation phrase, and
  - the approval passphrase, which is set only from the server console, never from the web page.
The trade-enabled Kraken key is also entered only from the server console. Desk never sees it.

Desk only records an approved request. The root-side applier (run by the watchdog) performs the switch.
Returning to paper stops new real entries at once and keeps managing open real trades
("live-exit-only") until they are closed; only then does the bot go back to paper. Paper and live
keep separate trade databases. Nothing here closes positions or moves money.

Console commands (as root):  python3 -m desk.livemode set-passphrase | set-trade-key | status"""
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time

ETC = "/etc/majvaj"
APPROVAL_FILE = ETC + "/approval.json"
KEY_HINT_FILE = ETC + "/live_key_hint.json"
STATE_FILE = "/var/lib/majvaj/mode_state.json"
WATCHDOG_STATE = "/var/lib/majvaj/watchdog.json"
FT_UD = "/opt/majvaj/ft/user_data"
MODE_CONFIG = FT_UD + "/config.mode.json"
LIVE_SECRET = "/etc/majvaj/live_exchange.json"
WARNING_VERSION = "TB-LIVE-01 v1"
PHRASE_LIVE, PHRASE_PAPER = "ENABLE LIVE TRADING", "RETURN TO PAPER"
MIN_PAPER_DAYS, MIN_PAPER_TRADES, MIN_CHECK_OK = 14, 10, 0.9
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


# ------------------------------------------------------------------ passphrase
def hash_passphrase(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return {"salt": salt.hex(), "hash": h.hex(), "set_at": time.time()}


def check_passphrase(pw, record):
    if not record or not isinstance(pw, str) or not pw:
        return False
    try:
        h = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(record["salt"]), n=2 ** 14, r=8, p=1, dklen=32)
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(h.hex(), record.get("hash", ""))


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
def readiness(now, paper_since, closed_paper_trades, checks_7d, watchdog_state, money, approval, key_hint):
    """List of (name, ok, detail). Live stays locked unless every item is ok."""
    ok_checks = [c for c in checks_7d if c.get("ok")]
    rate = len(ok_checks) / len(checks_7d) if checks_7d else 0.0
    giveups = [t for k, t in ((watchdog_state or {}).get("alerted") or {}).items()
               if k.endswith("_giveup") and now - t < 7 * 86400]
    days = (now - paper_since) / 86400 if paper_since else 0
    return [
        ("Approval passphrase set from the server console", bool(approval), "set" if approval else "run: python3 -m desk.livemode set-passphrase"),
        ("Trade-enabled Kraken key installed from the console", bool(key_hint),
         ("key ...%s" % key_hint.get("hint", "")) if key_hint else "run: python3 -m desk.livemode set-trade-key"),
        ("Paper trading for at least %d days" % MIN_PAPER_DAYS, days >= MIN_PAPER_DAYS, "%.1f days so far" % days),
        ("At least %d closed paper trades" % MIN_PAPER_TRADES, closed_paper_trades >= MIN_PAPER_TRADES,
         "%d so far" % closed_paper_trades),
        ("Fundamentals checks working (90%% over 7 days)", len(checks_7d) >= 7 and rate >= MIN_CHECK_OK,
         "%d of %d ok" % (len(ok_checks), len(checks_7d))),
        ("Watchdog never had to give up in 7 days", not giveups, "%d give-up(s)" % len(giveups)),
        ("Money rules set (bankroll, loss limit)", bool(money and money.get("bankroll", 0) > 0),
         "bankroll %.2f USD" % (money or {}).get("bankroll", 0)),
    ]


def locked(checks):
    return not all(ok for _, ok, _ in checks)


# ------------------------------------------------------------------ desk side
class ModeDesk:
    """Used by the Desk page. Validates and records requests; never touches the bot's config."""

    def __init__(self, data_dir, meta, clock=time.time, approval_file=APPROVAL_FILE, hint_file=KEY_HINT_FILE,
                 state_file=STATE_FILE, watchdog_file=WATCHDOG_STATE):
        self.request_file = os.path.join(data_dir, "mode_request.json")
        self.audit_file = os.path.join(data_dir, "mode_audit.jsonl")
        self.meta, self.clock = meta, clock
        self.approval_file, self.hint_file, self.state_file, self.watchdog_file = approval_file, hint_file, state_file, watchdog_file

    def state(self):
        return _read(self.state_file) or {"mode": "paper"}

    def checks(self, closed_paper_trades, checks_7d, money):
        paper_since = float(self.meta("paper_since") or 0)
        return readiness(self.clock(), paper_since, closed_paper_trades, checks_7d, _read(self.watchdog_file),
                         money, _read(self.approval_file), _read(self.hint_file))

    def _audit(self, entry):
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(entry, at=self.clock())) + "\n")

    def _attempts_ok(self):
        now = self.clock()
        tries = [t for t in json.loads(self.meta("pass_tries") or "[]") if now - t < 3600]
        return tries, len(tries) < 5

    def request(self, to, phrase, passphrase, ack, checks, scope):
        now = self.clock()
        if to == "paper":
            if (phrase or "").strip() != PHRASE_PAPER or not ack:
                return {"ok": False, "message": 'Type "%s" and tick the box to confirm.' % PHRASE_PAPER}
            req = {"to": "paper", "at": now, "warning_version": WARNING_VERSION}
            _write(self.request_file, req, 0o644)
            self._audit({"decision": "return_to_paper", "warning_version": WARNING_VERSION})
            return {"ok": True, "message": "New real entries are being stopped now. Open live trades stay managed until "
                                           "they close; then the bot returns to paper."}
        if to != "live":
            return {"ok": False, "message": "Unknown mode."}
        if locked(checks):
            self._audit({"decision": "live_refused_locked", "warning_version": WARNING_VERSION})
            return {"ok": False, "message": "Live trading is locked until every readiness check passes."}
        tries, allowed = self._attempts_ok()
        if not allowed:
            return {"ok": False, "message": "Too many passphrase attempts. Try again in an hour."}
        if (phrase or "").strip() != PHRASE_LIVE or not ack:
            return {"ok": False, "message": 'Type "%s" exactly and tick the acknowledgement.' % PHRASE_LIVE}
        if not check_passphrase(passphrase, _read(self.approval_file)):
            self.meta("pass_tries", json.dumps(tries + [now]))
            self._audit({"decision": "live_refused_passphrase", "warning_version": WARNING_VERSION})
            return {"ok": False, "message": "Approval passphrase is wrong."}
        req = {"to": "live", "at": now, "warning_version": WARNING_VERSION, "scope": scope}
        _write(self.request_file, req, 0o644)
        self._audit({"decision": "live_approved", "warning_version": WARNING_VERSION, "scope": scope})
        return {"ok": True, "message": "Approved. The switch to live is applied within 2 minutes."}


# ------------------------------------------------------------------ root side (watchdog)
def apply_pending(ft, request_file, now=None, run=None, notify=lambda t: None, state_file=STATE_FILE,
                  mode_config=MODE_CONFIG, live_secret=LIVE_SECRET, approval_file=APPROVAL_FILE):
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
        secret = _read(live_secret)
        if now - float(req.get("at", 0)) > REQUEST_MAX_AGE or not _read(approval_file) or not secret:
            notify("Maj Vaj: a live request was ignored (too old, or passphrase / trade key missing on the server).")
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
    cmd = argv[0] if argv else "status"
    if os.geteuid() != 0:
        print("Run as root.")
        return 1
    if cmd == "set-passphrase":
        a, b = getpass.getpass("New approval passphrase (12+ characters): "), getpass.getpass("Again: ")
        if a != b or len(a) < 12:
            print("Not set: the two entries differ or it is shorter than 12 characters.")
            return 1
        import grp
        _write(APPROVAL_FILE, hash_passphrase(a), 0o640, (0, grp.getgrnam("desk").gr_gid))
        print("Approval passphrase set. It is stored only as a salted hash.")
        return 0
    if cmd == "set-trade-key":
        print("Create a Kraken key with ONLY: Query funds, Query open/closed orders and trades, Create & modify orders,")
        print("Cancel/close orders. NEVER withdraw. The key is stored for the bot only; Desk never sees it.")
        key, sec = input("Kraken API key: ").strip(), getpass.getpass("Kraken API secret: ").strip()
        if len(key) < 20 or len(sec) < 40:
            print("That does not look like a Kraken key and secret. Nothing saved.")
            return 1
        import grp
        _write(LIVE_SECRET, {"key": key, "secret": sec, "set_at": time.time()}, 0o600)
        _write(KEY_HINT_FILE, {"hint": key[-4:], "set_at": time.time()}, 0o640, (0, grp.getgrnam("desk").gr_gid))
        print("Trade key stored. Live trading still needs every readiness check and your approval on the page.")
        return 0
    if cmd == "status":
        print(json.dumps({"state": _read(STATE_FILE) or {"mode": "paper"}, "passphrase_set": bool(_read(APPROVAL_FILE)),
                          "trade_key": bool(_read(KEY_HINT_FILE))}, indent=2))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
