"""The paper-trading engine: one cycle = prices, exits, (sometimes) scout, Gate, fills, alerts."""
import collections
import dataclasses
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import connectors, gate, indicators
from .gate import Account, Policy
from .market import KrakenMarket, MarketError
from .paper import PaperAccount
from .scout import GrokResearch, GrokScout


@dataclass
class Settings:
    paper_equity: float = 1000.0
    cycle_minutes: float = 5
    scout_minutes: float = 60
    session_scouting: bool = True
    research: bool = True
    max_scout_calls_per_day: int = 24
    grok_model: str = "grok-4.3"
    whitelist: tuple = ("BTC/USD", "ETH/USD", "SOL/USD")
    max_hold_hours: float = 48
    slippage_pct: float = 0.05
    target_buffer_pct: float = 0.05


EDITABLE = {"cycle_minutes": (1, 60, float), "scout_minutes": (5, 1440, float),
            "max_scout_calls_per_day": (1, 200, int)}
MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


# UTC windows. Interval = scout_minutes * factor (overlap is the busiest, so scout twice as often).
SESSIONS = ((0, 7, "Asia/Tokyo", 1.0), (7, 13, "London", 1.0), (13, 16, "London/New York overlap", 0.5),
            (16, 21, "New York", 1.0), (21, 24, "Quiet (late US)", 3.0))


def session_at(now):
    hour = int(now % 86400 // 3600)
    for lo, hi, name, factor in SESSIONS:
        if lo <= hour < hi:
            return name, factor
    return "Quiet", 1.0


def scout_interval_minutes(now, s):
    if not s.session_scouting:
        return s.scout_minutes
    return s.scout_minutes * session_at(now)[1]


def _settings_path(data_dir):
    return os.path.join(data_dir, "settings.json")


def load_settings(data_dir):
    s = Settings()
    try:
        with open(_settings_path(data_dir), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, ValueError):
        return s
    return _apply(s, raw if isinstance(raw, dict) else {})


def _apply(s, raw):
    for k, (lo, hi, typ) in EDITABLE.items():
        if k in raw:
            try:
                v = typ(raw[k])
                if lo <= v <= hi:
                    setattr(s, k, v)
            except (TypeError, ValueError):
                pass
    if isinstance(raw.get("session_scouting"), bool):
        s.session_scouting = raw["session_scouting"]
    if isinstance(raw.get("research"), bool):
        s.research = raw["research"]
    if isinstance(raw.get("grok_model"), str) and MODEL_RE.match(raw["grok_model"]):
        s.grok_model = raw["grok_model"]
    return s


def save_settings(data_dir, changes):
    current = dataclasses.asdict(load_settings(data_dir))
    s = _apply(Settings(**{**current, "whitelist": tuple(current["whitelist"])}), changes)
    keep = {k: getattr(s, k) for k in list(EDITABLE) + ["grok_model", "session_scouting", "research"]}
    with open(_settings_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(keep, f)
    return s


def decide_exit(pos, candles, quote, now, slip_pct=0.05, target_buffer_pct=0.05):
    """Return (exit_price, reason, new_last_checked). Conservative: if a single
    candle touches both stop and target, the stop is assumed to have hit first."""
    slip = slip_pct / 100
    fresh = [c for c in candles if c.ts > pos["last_checked"]]
    for c in fresh:
        if c.low <= pos["stop"]:
            return min(pos["stop"], c.open) * (1 - slip), "stop", pos["last_checked"]
        if c.high >= pos["target"] * (1 + target_buffer_pct / 100):
            return pos["target"], "target", pos["last_checked"]
    checked = pos["last_checked"]
    if len(candles) >= 2:  # the newest candle is still forming: look at it again next time
        checked = max(checked, candles[-2].ts)
    if quote is not None and quote.bid <= pos["stop"]:
        return quote.bid * (1 - slip), "stop", checked
    if quote is not None and now >= pos["expires_at"]:
        return quote.bid * (1 - slip), "time", checked
    return None, None, checked


class TelegramNotifier:
    def __init__(self, store):
        self.store = store

    def _send(self, bot, text):
        try:
            tok, chat = self.store.get("telegram_%s_token" % bot), self.store.get("telegram_%s_chat_id" % bot)
            if not tok or not chat:
                return False
            status, data = connectors._tg(tok, "sendMessage", {"chat_id": chat, "text": text[:3500]})
            return status == 200 and isinstance(data, dict) and bool(data.get("ok"))
        except Exception:
            return False

    def desk(self, text):
        return self._send("desk", text)

    def recovery(self, text):
        return self._send("recovery", text)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Engine:
    def __init__(self, account, market, scout, notifier, settings_fn, clock=time.time, research=None):
        self.account, self.market, self.scout, self.notifier = account, market, scout, notifier
        self.research = research
        self.settings, self.clock = settings_fn, clock
        self.cycle_lock = threading.Lock()
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread, self.running, self._force = None, False, False
        self.last, self.last_bids = {}, {}
        self.errors = collections.deque(maxlen=12)
        self.market_failures = self.scout_failures = self.crash_count = 0
        self.last_alert = {}

    def policy(self, s=None):
        return Policy(whitelist=tuple((s or self.settings()).whitelist))

    # ------------------------------------------------------------ control
    def start(self, resumed=False):
        if self.running:
            return False
        self.account.set_meta("wanted_running", "1")
        self.stop_event.clear()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.notifier.desk("Desk %s (paper trading). Equity %.2f USD." % (
            "restarted and resumed on its own" if resumed else "started", self.account.equity(self.last_bids)))
        return True

    def resume(self):
        """After a server restart or reboot: carry on if it was running when it went down."""
        if self.account.get_meta("wanted_running") == "1":
            return self.start(resumed=True)
        return False

    def stop(self):
        if not self.running:
            return False
        self.account.set_meta("wanted_running", "0")
        self.running = False
        self.stop_event.set()
        self.wake.set()
        self.notifier.desk("Desk stopped. Open paper positions are NOT being watched until you start it again.")
        return True

    def request_scout(self):
        if not self.running:
            return False
        self._force = True
        self.wake.set()
        return True

    def _loop(self):
        while not self.stop_event.is_set():
            force, self._force = self._force, False
            try:
                self.cycle(force_scout=force)
                self.crash_count = 0
            except Exception as e:  # keep running; never print details (could hold secrets)
                self.crash_count += 1
                self._error("cycle crashed (%s)" % type(e).__name__)
                if self.crash_count >= 3:
                    self._alert("crash", "The cycle has crashed %d times in a row (%s). "
                                         "Check the terminal window." % (self.crash_count, type(e).__name__))
            self.wake.wait(timeout=self.settings().cycle_minutes * 60)
            self.wake.clear()

    # ------------------------------------------------------------ helpers
    def _error(self, msg):
        self.errors.appendleft({"ts": self.clock(), "message": msg})

    def _alert(self, key, text):
        now = self.clock()
        if now - self.last_alert.get(key, 0) >= 1800:
            self.last_alert[key] = now
            self.notifier.recovery(text)

    def _account_state(self, snap):
        now = self.clock()
        bids = {k: v.bid for k, v in snap.items()}
        return Account(equity=self.account.equity(bids), cash=self.account.cash,
                       open_symbols=tuple(p["symbol"] for p in self.account.open_positions()),
                       realized_pnl_today=self.account.realized_today(now), paused=False)

    # ------------------------------------------------------------ one cycle
    def cycle(self, force_scout=False):
        with self.cycle_lock:
            now, s = self.clock(), self.settings()
            symbols = list(dict.fromkeys(list(s.whitelist) + [p["symbol"] for p in self.account.open_positions()]))
            snap, errs = {}, []
            for sym in symbols:
                try:
                    snap[sym] = self.market.quote(sym)
                except MarketError as e:
                    errs.append("%s: %s" % (sym, e))
            if not snap:
                self.market_failures += 1
                self._error("no market data: %s" % "; ".join(errs[:2]))
                if self.market_failures >= 3:
                    self._alert("market", "Kraken price data has failed %d cycles in a row: %s"
                                % (self.market_failures, "; ".join(errs[:2])))
                self.last = {"ts": now, "ok": False, "note": "no market data"}
                return self.last
            self.market_failures = 0
            self.last_bids = {k: v.bid for k, v in snap.items()}
            closed = self._manage(now, snap, s)
            self.account.snapshot(now, self.account.equity(self.last_bids))
            opened, rejected, note = [], [], ""
            if self._scout_due(now, s, force_scout):
                opened, rejected, note = self._scout_round(now, snap, s)
            self.last = {"ts": now, "ok": True, "closed": closed, "opened": opened,
                         "rejected": rejected, "note": note, "price_errors": errs}
            return self.last

    def _manage(self, now, snap, s):
        closed = []
        fee_rate = self.policy(s).fee_pct_per_side / 100
        for pos in self.account.open_positions():
            q = snap.get(pos["symbol"])
            try:
                candles = self.market.candles(pos["symbol"], 1, since=pos["last_checked"] - 60)
            except MarketError as e:
                candles = []
                self._error("candles for %s: %s" % (pos["symbol"], e))
            price, reason, checked = decide_exit(pos, candles, q, now, s.slippage_pct, s.target_buffer_pct)
            if reason is None:
                if checked != pos["last_checked"]:
                    self.account.set_last_checked(pos["id"], checked)
                continue
            done = self.account.close_position(pos["id"], price, reason, pos["qty"] * price * fee_rate, now)
            closed.append({"symbol": done["symbol"], "reason": reason, "pnl": done["pnl"]})
            cost = done["qty"] * done["entry"] + done["entry_fee"]
            self.notifier.desk("SELL (paper) %s: %s hit\nP&L %+.2f USD (%+.2f%%)\nEquity %.2f USD" % (
                done["symbol"], {"stop": "stop", "target": "target", "time": "time limit"}[reason],
                done["pnl"], done["pnl"] / cost * 100 if cost else 0.0, self.account.equity(self.last_bids)))
        return closed

    def _scout_due(self, now, s, force):
        if not self.running and not force:
            return False
        if force:
            return True
        last = self.account.get_meta("last_scout_ts")
        return last is None or now - float(last) >= scout_interval_minutes(now, s) * 60

    def _brief(self, snap, now, s):
        symbols = {}
        for sym in s.whitelist:
            q = snap.get(sym)
            if q is None:
                continue
            try:
                candles = self.market.candles(sym, 60)
            except MarketError:
                continue
            if len(candles) >= 30:
                symbols[sym] = indicators.summarize(q, candles[-72:])
        if not symbols:
            return None
        pol, st = self.policy(s), self._account_state(snap)
        return {"time_utc": _iso(now), "symbols": symbols,
                "account": {"equity_usd": round(st.equity, 2), "cash_usd": round(st.cash, 2),
                            "open_positions": [{"symbol": p["symbol"], "entry": p["entry"], "stop": p["stop"],
                                                "target": p["target"]} for p in self.account.open_positions()]},
                "rules": {"direction": "long only (spot)", "fee_pct_per_side": pol.fee_pct_per_side,
                          "min_net_reward_to_risk": pol.min_reward_risk,
                          "stop_distance_pct_range": [pol.min_stop_distance_pct, pol.max_stop_distance_pct],
                          "max_hold_hours": s.max_hold_hours, "max_entry_drift_pct": pol.max_entry_drift_pct}}

    def _scout_round(self, now, snap, s):
        if self.account.scout_calls_today(now) >= s.max_scout_calls_per_day:
            note = "daily scout limit (%d) reached" % s.max_scout_calls_per_day
            self.account.log_scout(now, False, False, 0, note)
            self.account.set_meta("last_scout_ts", now)
            return [], [], note
        brief = self._brief(snap, now, s)
        if brief is None:
            self._error("scout skipped: not enough candle data")
            self.account.log_scout(now, False, False, 0, "not enough candle data")
            return [], [], "not enough candle data"
        if s.research and self.research is not None:
            try:
                r = self.research.digest(now)
            except Exception as e:  # research must never stop scouting
                r = None
                self._error("research crashed (%s)" % type(e).__name__)
            if r is not None and r.ok and r.text:
                brief["fundamentals"] = {"retrieved_utc": _iso(now), "untrusted_digest": r.text}
                self.account.set_meta("last_digest", json.dumps({"ts": now, "text": r.text, "sources": r.sources}))
            elif r is not None:
                self._error(r.error or "research returned nothing")
        res = self.scout.propose(brief, now)
        self.account.set_meta("last_scout_ts", now)
        self.account.log_scout(now, True, res.ok, len(res.proposals), res.error or res.note)
        if not res.ok:
            self.scout_failures += 1
            self._error("scout failed: %s" % res.error)
            if self.scout_failures >= 3:
                self._alert("scout", "The Grok scout has failed %d times in a row: %s" % (self.scout_failures, res.error))
            return [], [], res.error
        self.scout_failures = 0
        opened, rejected = [], []
        pol = self.policy(s)
        for p in sorted(res.proposals, key=lambda x: -(x.confidence or 0)):
            q = snap.get(p.symbol)
            if q is None:
                rejected.append("%s: no current price" % p.symbol)
                continue
            d = gate.evaluate(p, q, self._account_state(snap), pol, now)
            self.account.log_decision(now, p, d)
            if not d.approved:
                rejected.append("%s: %s" % (p.symbol, d.reason))
                continue
            msg = self._open(now, p, d, q, s, pol)
            (opened if msg is None else rejected).append(p.symbol if msg is None else "%s: %s" % (p.symbol, msg))
        if res.proposals:
            lines = ["Scout round: %d proposal(s), %d opened, %d rejected" % (len(res.proposals), len(opened), len(rejected))]
            lines += rejected[:5]
            self.notifier.desk("\n".join(lines))
        return opened, rejected, res.note

    def _open(self, now, p, d, q, s, pol):
        slip, fee_rate = s.slippage_pct / 100, pol.fee_pct_per_side / 100
        fill, qty, scale = q.ask * (1 + slip), d.quantity, 10 ** pol.qty_decimals
        cash = self.account.cash
        if qty * fill * (1 + fee_rate) > cash:
            qty = math.floor(cash / (fill * (1 + fee_rate)) * scale) / scale
        if qty * fill < pol.min_notional:
            return "not enough cash for the minimum order"
        fee = qty * fill * fee_rate
        self.account.open_position(p.id, p.symbol, qty, fill, d.stop, d.target, now, p.expires_at, fee, p.rationale)
        self.notifier.desk("BUY (paper) %s\n%.6g at %.6g, cost %.2f USD\nstop %.6g | target %.6g\nWhy: %s" % (
            p.symbol, qty, fill, qty * fill + fee, d.stop, d.target, p.rationale or "(no reason given)"))
        return None

    # ------------------------------------------------------------ for the page
    def _digest(self):
        try:
            return json.loads(self.account.get_meta("last_digest") or "null")
        except ValueError:
            return None

    def status(self):
        now, s = self.clock(), self.settings()
        bids, rows = self.last_bids, []
        for p in self.account.open_positions():
            mark = bids.get(p["symbol"])
            rows.append({**p, "mark": mark, "unrealized": None if mark is None else (mark - p["entry"]) * p["qty"] - p["entry_fee"]})
        equity = self.account.equity(bids)
        return {"now": now, "running": self.running, "cash": self.account.cash, "equity": equity,
                "start_equity": self.account.start_equity, "pnl": equity - self.account.start_equity,
                "open": rows, "closed": self.account.closed_positions(15),
                "decisions": self.account.recent_decisions(25), "last_scout": self.account.last_scout(),
                "scout_calls_today": self.account.scout_calls_today(now), "last_cycle": self.last,
                "session": session_at(now)[0], "scout_interval": scout_interval_minutes(now, s),
                "errors": list(self.errors), "settings": dataclasses.asdict(s), "digest": self._digest()}


def build_engine(store):
    settings_fn = lambda: load_settings(store.dir)
    account = PaperAccount(os.path.join(store.dir, "ledger.db"), settings_fn().paper_equity)
    scout = GrokScout(lambda: store.get("grok_key"), lambda: settings_fn().grok_model,
                      lambda: settings_fn().whitelist, lambda: settings_fn().max_hold_hours)
    research = GrokResearch(lambda: store.get("grok_key"), lambda: settings_fn().grok_model)
    return Engine(account, KrakenMarket(), scout, TelegramNotifier(store), settings_fn, research=research)
