"""Which coins the bot trades (FreqAI tab, "Pairs").

You pick the coins from Kraken's own list of USD pairs. BTC and ETH always stay: the model uses them as reference
markets for every coin, so keeping them fixed means adding or removing another coin never retrains the rest.

Desk side (unprivileged):
  - reads Kraken's public pair list and 24-hour prices (no key) and marks which coins can be picked and why not;
  - on Apply, downloads the history of the new coins into Desk's history database and refuses any coin with less
    history than the model needs, then records the request.
Root side (the watchdog): copies the history into the bot's data folder in a background job, writes the bot's pair
list (config.pairs.json), and restarts the bot. A new coin cannot trade until FreqAI has trained a model for it;
the strategy itself refuses entries on a coin without a trained model, in paper and in live.
Coins you remove keep any open trade managed to its normal exit; they just get no new entries."""
import glob
import json
import os
import re
import shlex
import time

from . import history, market, net

LOCKED = ("BTC/USD", "ETH/USD")
DEFAULT = ("BTC/USD", "ETH/USD", "SOL/USD")
MAX_PAIRS = 8                    # every coin adds training time; 8 fits a small droplet
MIN_VOLUME_USD = 100_000         # thinner markets slip too much
REQUIRED_DAYS = 400              # the model learns from 365 days, plus room for its longest indicators
MIN_ORDER_BUFFER = 1.1           # room for fees and Kraken's rounding
CATALOG_MAX_AGE = 6 * 3600
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,12}/USD$")
RENAME = {"XBT": "BTC", "XDG": "DOGE"}           # Kraken's own names -> the names the bot uses
# Not trading candidates: stablecoins and currencies (they barely move), and coins the bot's exchange library renames.
EXCLUDE = {"USDT", "USDC", "DAI", "PYUSD", "USDG", "USDE", "TUSD", "USDS", "FDUSD", "RLUSD", "USDQ", "USDR", "USD1",
           "EURT", "EURR", "EUROC", "EURC", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USD", "UST", "LUNA", "LUNA2",
           "REP", "REPV2", "TBTC", "WBTC", "CBBTC"}
UNIT = "majvaj-pairs"


# ------------------------------------------------------------------ files
def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj, mode=0o644, owner=None):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.chmod(tmp, mode)
    if owner:
        os.chown(tmp, *owner)
    os.replace(tmp, path)


def desk_paths(data_dir):
    return {"selection": os.path.join(data_dir, "pairs.json"), "catalog": os.path.join(data_dir, "pairs_catalog.json"),
            "request": os.path.join(data_dir, "pairs_request.json")}


def bot_paths(user_data):
    mv = os.path.join(user_data, "majvaj")
    return {"config": os.path.join(user_data, "config.pairs.json"), "state": os.path.join(mv, "pairs_state.json"),
            "exit": os.path.join(mv, "pairs_exit"), "base_config": os.path.join(user_data, "config-4h.json"),
            "models": os.path.join(user_data, "models")}


def valid_list(pairs):
    return (isinstance(pairs, list) and 0 < len(pairs) <= MAX_PAIRS and len(set(pairs)) == len(pairs)
            and all(isinstance(p, str) and SYMBOL_RE.match(p) for p in pairs) and all(p in pairs for p in LOCKED))


def selection(data_dir):
    """(pairs, Kraken codes) chosen on this Desk. Also teaches the history downloader the Kraken code of each coin."""
    s = _read(desk_paths(data_dir)["selection"]) or {}
    pairs = s.get("pairs") if valid_list(s.get("pairs")) else list(DEFAULT)
    codes = {k: v for k, v in (s.get("codes") or {}).items() if isinstance(v, str) and re.match(r"^[A-Z0-9.]{2,20}$", v)}
    for p in pairs:
        if p in codes:
            market.KNOWN.setdefault(p, codes[p])
    return pairs, codes


def coins_text(data_dir):
    """'BTC, ETH, SOL' for Grok's prompts."""
    return ", ".join(p.split("/")[0] for p in selection(data_dir)[0])


def trained_coins(user_data):
    """Coins FreqAI has a saved model for (sub-train-<COIN>_<time> folders of the current model), or None when this
    is not a Maj Vaj bot folder (no model name to look for). The strategy enforces the rule itself either way."""
    cfg = _read(bot_paths(user_data)["base_config"]) or {}
    ident = (cfg.get("freqai") or {}).get("identifier")
    if not ident:
        return None
    out = set()
    for d in glob.glob(os.path.join(bot_paths(user_data)["models"], glob.escape(ident), "sub-train-*")):
        name = os.path.basename(d)[len("sub-train-"):]
        if "_" in name and os.path.isdir(d):
            out.add(name.rsplit("_", 1)[0])
    return out


def applied(user_data):
    c = _read(bot_paths(user_data)["config"]) or {}
    pl = (c.get("exchange") or {}).get("pair_whitelist")
    if isinstance(pl, list) and pl:
        return pl
    base = _read(bot_paths(user_data)["base_config"]) or {}
    return list((base.get("exchange") or {}).get("pair_whitelist") or DEFAULT)


# ------------------------------------------------------------------ Kraken's list
def symbol_of(info):
    """The bot's name for a Kraken pair ('XBT/USD' -> 'BTC/USD'), or None if it is not a USD spot pair we can use."""
    ws = info.get("wsname") or ""
    if "/" not in ws:
        return None
    base, quote = ws.split("/", 1)
    if quote != "USD":
        return None
    base = RENAME.get(base, base)
    sym = base + "/USD"
    return sym if SYMBOL_RE.match(sym) else None


def parse_catalog(pairs_result, ticker_result, per_trade):
    """One row per usable coin. per_trade: the smallest trade the bot may place (half size under CAUTION)."""
    rows = []
    for name, info in (pairs_result or {}).items():
        if not isinstance(info, dict) or name.endswith(".d"):
            continue
        if info.get("aclass_base", "currency") != "currency":   # tokenized stocks and the like
            continue
        sym = symbol_of(info)
        if not sym:
            continue
        base = sym.split("/")[0]
        t = (ticker_result or {}).get(name) or (ticker_result or {}).get(info.get("altname")) or {}
        try:
            last = float(t["c"][0])
            volume = float(t["v"][1]) * float(t["p"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            last, volume = None, 0.0
        try:
            min_usd = max(float(info.get("costmin") or 0), float(info.get("ordermin") or 0) * (last or 0))
        except (TypeError, ValueError):
            min_usd = None
        status = info.get("status", "online")
        reason = ""
        if base in EXCLUDE:
            reason = "stablecoin or currency" if base not in ("LUNA", "LUNA2", "REP", "REPV2", "UST") else "renamed coin, not supported"
        elif status != "online":
            reason = "Kraken has it in %s mode" % status.replace("_", "-")
        elif not last:
            reason = "no price from Kraken"
        elif volume < MIN_VOLUME_USD:
            reason = "too thin (under $%s traded in 24 h)" % format(MIN_VOLUME_USD, ",")
        elif min_usd is None or min_usd * MIN_ORDER_BUFFER > per_trade:
            reason = "Kraken's minimum order (%s USD) is above your smallest trade size (%.2f USD)" % (
                "?" if min_usd is None else "%.2f" % min_usd, per_trade)
        rows.append({"pair": sym, "code": info.get("altname") or name, "last": last, "volume_usd": round(volume),
                     "min_usd": None if min_usd is None else round(min_usd, 2), "eligible": not reason, "reason": reason})
    best = {}
    for r in rows:   # Kraken sometimes lists two codes for one coin; keep the busier one
        if r["pair"] not in best or r["volume_usd"] > best[r["pair"]]["volume_usd"]:
            best[r["pair"]] = r
    return sorted(best.values(), key=lambda r: -r["volume_usd"])


def fetch_kraken(http=None):
    http = http or net.http_json
    out = []
    for path in ("AssetPairs", "Ticker"):
        status, data = http("GET", "%s/0/public/%s" % (market.KRAKEN, path), timeout=30)
        if status != 200 or not isinstance(data, dict) or data.get("error") or not isinstance(data.get("result"), dict):
            raise market.MarketError("Kraken %s answered HTTP %s %s" % (path, status, (data or {}).get("error") if isinstance(data, dict) else ""))
        out.append(data["result"])
    return out


# ------------------------------------------------------------------ Desk side
class Pairs:
    def __init__(self, data_dir, user_data, clock=time.time, http=None):
        self.dir, self.ud, self.clock, self.http = data_dir, user_data, clock, http
        self.dp, self.bp = desk_paths(data_dir), bot_paths(user_data)

    def max_open_trades(self):
        return int((_read(self.bp["base_config"]) or {}).get("max_open_trades") or 3)

    def per_trade(self, money):
        """Smallest normal trade: an even share of the bankroll, halved under a CAUTION verdict."""
        return float(money.get("bankroll") or 0) / max(1, self.max_open_trades()) / 2

    def catalog(self, money, refresh=False):
        cached = _read(self.dp["catalog"])
        if refresh or not cached or self.clock() - cached.get("at", 0) > CATALOG_MAX_AGE:
            try:
                ap, tk = fetch_kraken(self.http)
            except (market.MarketError, net.NetError) as e:
                if cached:
                    cached["error"] = "Could not refresh Kraken's list (%s); showing the one from %s." % (e, time.ctime(cached["at"]))
                    return cached
                return {"at": None, "rows": [], "error": "Could not read Kraken's list: %s" % e}
            cached = {"at": self.clock(), "raw": {"pairs": ap, "ticker": tk}}
            _write(self.dp["catalog"], cached, 0o600)
        rows = parse_catalog(cached["raw"]["pairs"], cached["raw"]["ticker"], self.per_trade(money))
        return {"at": cached["at"], "rows": rows, "error": cached.get("error")}

    def job_state(self):
        return _read(self.bp["state"]) or {"status": "idle"}

    def busy(self):
        st = self.job_state().get("status")
        return os.path.exists(self.dp["request"]) or st in ("queued", "running")

    def status(self, open_pairs=()):
        chosen, _ = selection(self.dir)
        live = applied(self.ud)
        trained = trained_coins(self.ud)
        rows = []
        for p in chosen:
            if p not in live:
                state = "waiting to be applied"
            elif trained is None or p.split("/")[0] in trained:
                state = "trading"
            else:
                state = "training (no trades until its model is ready)"
            rows.append({"pair": p, "state": state, "locked": p in LOCKED})
        for p in sorted(set(open_pairs) - set(chosen)):
            rows.append({"pair": p, "state": "closing only (removed; its open trade is managed to the exit)", "locked": False})
        untrained = [p for p in chosen if p in live and trained is not None and p.split("/")[0] not in trained]
        return {"chosen": chosen, "applied": live, "rows": rows, "untrained": untrained, "job": self.job_state(),
                "pending": self.busy() or chosen != live,
                "limits": {"max_pairs": MAX_PAIRS, "min_volume_usd": MIN_VOLUME_USD, "required_days": REQUIRED_DAYS,
                           "locked": list(LOCKED)}}

    def apply(self, wanted, money, research, notify=lambda t: None):
        """Validate the new list, download the new coins' history, then record the request for the watchdog."""
        if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
            return {"ok": False, "message": "Bad list."}
        wanted = list(dict.fromkeys(list(LOCKED) + [p for p in wanted if p not in LOCKED]))
        if len(wanted) > MAX_PAIRS:
            return {"ok": False, "message": "At most %d coins (BTC and ETH included)." % MAX_PAIRS}
        current, codes = selection(self.dir)
        if wanted == current and wanted == applied(self.ud):
            return {"ok": False, "message": "Nothing changed."}
        if self.busy():
            return {"ok": False, "message": "A coin change is already being applied. Wait for it to finish."}
        cat = {r["pair"]: r for r in self.catalog(money)["rows"]}
        new = [p for p in wanted if p not in current]
        for p in new:
            r = cat.get(p)
            if not r:
                return {"ok": False, "message": "%s is not on Kraken's USD list right now." % p}
            if not r["eligible"]:
                return {"ok": False, "message": "%s can't be added: %s." % (p, r["reason"])}
            codes[p] = r["code"]
            market.KNOWN.setdefault(p, r["code"])

        def work(say):
            db = history.connect(research.db_path)
            try:
                refused = {}
                need = [p for p in new if not self._enough(db, p)]
                if need:
                    say("reading the history of %s from Kraken's archive..." % ", ".join(need))
                    for p in need:
                        try:
                            history.import_archive(db, [p], research.remote_factory(), say)
                        except history.HistoryError as e:
                            refused[p] = "no history in Kraken's archive" if "could not find" in str(e) else str(e)[:120]
                say("adding the latest candles...")
                for p in wanted:
                    if p in refused:
                        continue
                    try:
                        history.top_up(db, [p], research.market, say)
                    except history.HistoryError as e:
                        if p in new:
                            refused[p] = str(e)[:120]
                for p in new:
                    if p not in refused and not self._enough(db, p):
                        days = self._days(db, p)
                        refused[p] = "only %d days of history; the model needs %d" % (days, REQUIRED_DAYS)
            finally:
                db.close()
            final = [p for p in wanted if p not in refused]
            note = "; ".join("%s not added (%s)" % kv for kv in refused.items())
            if final == current and final == applied(self.ud):
                raise history.HistoryError("No change applied. " + note)
            _write(self.dp["selection"], {"pairs": final, "codes": {p: codes[p] for p in final if p in codes},
                                          "saved_at": self.clock()}, 0o600)
            _write(self.dp["request"], {"pairs": final, "at": self.clock()}, 0o644)
            notify("Maj Vaj coins changing to %s.%s" % (", ".join(final), (" " + note) if note else ""))
            say("History ready; the bot switches to %s within a few minutes. New coins train before they can trade.%s"
                % (", ".join(final), (" " + note + ".") if note else ""))
        started = research.job.start("pairs", work)
        if not started:
            return {"ok": False, "message": "A history download is already running. Try again when it finishes."}
        return {"ok": True, "message": "Checking and downloading history for the new coins. Progress shows below."}

    @staticmethod
    def _days(db, pair):
        n, a, b = db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM candles WHERE symbol=?", (pair,)).fetchone()
        return int((b - a) / 86400) if n and a and b else 0

    def _enough(self, db, pair):
        row = db.execute("SELECT MAX(ts) FROM candles WHERE symbol=?", (pair,)).fetchone()
        recent = bool(row and row[0] and self.clock() - row[0] < 3 * 86400)
        return recent and self._days(db, pair) >= REQUIRED_DAYS


# ------------------------------------------------------------------ root side (watchdog)
def refresh_data_script(user_data, image, desk_history):
    """Shell steps that copy Desk's history into the bot's data folder (used by pair changes and backtests)."""
    work = os.path.join(os.path.dirname(user_data), "work")
    q = shlex.quote
    return [
        "python3 -c %s" % q("import sqlite3; s=sqlite3.connect('file:%s?mode=ro',uri=True); d=sqlite3.connect('%s/history.db'); "
                             "s.backup(d); d.close()" % (desk_history, work)),
        "chmod 644 %s" % q(os.path.join(work, "history.db")),
        "docker run --rm --entrypoint python -v %s:/freqtrade/user_data -v %s:/work:ro %s "
        "/freqtrade/user_data/tools/convert_history.py /work/history.db /freqtrade/user_data/data/kraken" % (q(user_data), q(work), q(image)),
        "chown -R 1000:1000 %s" % q(os.path.join(user_data, "data")),
    ]


def tick(user_data, image, desk_dir, desk_history, now=None, run_out=None, notify=lambda t: None):
    """Take a recorded coin change, refresh the bot's data in a background job, then switch the bot's list."""
    now = now or time.time()
    run_out = run_out or (lambda cmd: __import__("subprocess").run(cmd, capture_output=True, text=True, timeout=60).stdout)
    bp, req_path = bot_paths(user_data), desk_paths(desk_dir)["request"]
    state = _read(bp["state"]) or {"status": "idle"}
    req = _read(req_path)
    if os.path.exists(req_path):
        os.remove(req_path)          # always consumed, so the instant trigger never loops on it
        if state.get("status") in ("queued", "running"):
            notify("Maj Vaj: a coin change arrived while another was being applied; it was dropped. Apply it again.")
        elif not req or not valid_list(req.get("pairs")):
            state = {"status": "failed", "finished": now, "message": "The coin list was not valid; nothing changed."}
            _write(bp["state"], state)
            return "pairs refused"
        else:
            state = {"status": "queued", "pairs": req["pairs"], "at": now}
            _write(bp["state"], state)
    active = lambda unit: run_out(["systemctl", "is-active", unit]).strip() in ("active", "activating")
    if state.get("status") == "queued":
        if active("majvaj-backtest") or active(UNIT):
            return "pairs waiting for a backtest to finish"
        if not image:
            _write(bp["state"], {"status": "failed", "finished": now, "message": "No bot image known; rerun the installer."})
            return "pairs refused"
        try:
            os.remove(bp["exit"])
        except OSError:
            pass
        script = "(%s); echo $? > %s" % (" && ".join(refresh_data_script(user_data, image, desk_history)), shlex.quote(bp["exit"]))
        run_out(["systemd-run", "--unit", UNIT, "--collect", "--quiet", "/bin/sh", "-c", script])
        state.update(status="running", started=now)
        _write(bp["state"], state)
        return "pairs data refresh started"
    if state.get("status") == "running":
        if active(UNIT):
            return "pairs data refresh running"
        try:
            with open(bp["exit"], "r", encoding="utf-8") as f:
                code = int(f.read().strip() or 1)
        except (OSError, ValueError):
            code = 1
        if code:
            state.update(status="failed", finished=now, message="Copying the history to the bot failed; the bot keeps its "
                                                                 "old coins. Details: journalctl -u %s -n 50" % UNIT)
            _write(bp["state"], state)
            notify("Maj Vaj: coin change FAILED while copying history; the bot keeps its old coins.")
            return "pairs failed"
        _write(bp["config"], {"exchange": {"pair_whitelist": state["pairs"]}}, 0o644,
               (1000, 1000) if os.geteuid() == 0 else None)
        run_out(["systemctl", "restart", "majvaj-freqtrade"])
        state.update(status="done", finished=now)
        _write(bp["state"], state)
        notify("Maj Vaj now watches %s. New coins train first and cannot trade until their model is ready."
               % ", ".join(state["pairs"]))
        return "pairs applied"
    return None
