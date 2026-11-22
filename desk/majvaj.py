"""Maj Vaj desk layer: ties Desk to the Freqtrade bot.

- Runs the fundamentals verdict service (Grok research -> verdict file the bot reads).
- Reads the bot over its local API (read-only) for the page and reports.
- Keeps the money settings file (bankroll, daily goal, loss limit) the bot's sizing obeys.
- Sends the evening report and the Sunday weekly review to the Desk Telegram bot."""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .engine import TelegramNotifier, load_settings
from .ftapi import FreqtradeAPI, FtError
from .market import KrakenMarket, MarketError
from .scout import GrokResearch
from .econdata import EconData
from .livemode import ModeDesk, WARNING_OFF, WARNING_ON, WARNING_VERSION, PHRASE_LIVE, PHRASE_PAPER, blockers
from .verdict import VerdictService, write_atomic

GOAL_MODES = ("continue", "stop", "opportunity")
SESSION_NAMES = ("Asia/Tokyo", "London", "London/New York overlap", "New York", "Quiet (late US)")
MONEY_DEFAULTS = {"bankroll": 1000.0, "daily_goal_usd": 0.0, "goal_mode": "stop", "goal_reduce_pct": 50,
                  "goal_keep_pct": 50, "goal_sessions": ["Asia/Tokyo", "London/New York overlap"],
                  "daily_loss_limit_pct": 3.0, "timezone": "America/Denver"}
PAIRS = ("BTC/USD", "ETH/USD", "SOL/USD")
PAUSE_PHRASE = "PAUSE TRADING"
RESUME_PHRASE = "RESUME TRADING"


def clean_money(raw, current=None):
    """Validate money settings from the page. Returns (settings, error)."""
    m = dict(MONEY_DEFAULTS)
    m.update(current or {})
    if m.get("goal_mode") == "reduce":
        m["goal_mode"] = "opportunity"
    try:
        if "bankroll" in raw:
            b = float(raw["bankroll"])
            if not 10 <= b <= 10_000_000:
                return None, "Bankroll must be between 10 and 10,000,000 USD."
            m["bankroll"] = b
        if "daily_goal_usd" in raw:
            g = float(raw["daily_goal_usd"] or 0)
            if not 0 <= g <= 1_000_000:
                return None, "Daily goal must be 0 (off) or a positive amount."
            m["daily_goal_usd"] = g
        if "goal_mode" in raw:
            if raw["goal_mode"] not in GOAL_MODES:
                return None, "Unknown goal mode."
            m["goal_mode"] = raw["goal_mode"]
        if "goal_reduce_pct" in raw:
            r = int(raw["goal_reduce_pct"])
            if not 1 <= r <= 99:
                return None, "Extra-trade size must be 1 to 99 percent."
            m["goal_reduce_pct"] = r
        if "goal_keep_pct" in raw:
            k = int(raw["goal_keep_pct"])
            if not 0 <= k <= 100:
                return None, "Share of the day's profit to keep must be 0 to 100 percent."
            m["goal_keep_pct"] = k
        if "goal_sessions" in raw:
            ss = raw["goal_sessions"]
            if not isinstance(ss, list) or any(x not in SESSION_NAMES for x in ss):
                return None, "Unknown session."
            m["goal_sessions"] = list(dict.fromkeys(ss))
        if "daily_loss_limit_pct" in raw:
            l = float(raw["daily_loss_limit_pct"])
            if not 0.5 <= l <= 20:
                return None, "Daily loss limit must be 0.5 to 20 percent."
            m["daily_loss_limit_pct"] = l
        if "timezone" in raw:
            ZoneInfo(str(raw["timezone"]))
            m["timezone"] = str(raw["timezone"])
    except (TypeError, ValueError, KeyError, Exception) as e:
        return None, "Invalid value (%s)." % type(e).__name__
    return m, ""


def ledger(bankroll, closed):
    """Same rule as the bot: wins are protected, losses reduce the active bankroll."""
    active, protected = bankroll, 0.0
    for _, pnl in sorted(closed, key=lambda x: x[0]):
        if pnl > 0:
            protected += pnl
        else:
            active += pnl
    return max(0.0, active), protected


def market_move(candles_by_pair, start, end):
    """Average % change across pairs between two unix times, from hourly candles."""
    moves = []
    for cs in candles_by_pair.values():
        inside = [c for c in cs if start <= c.ts <= end]
        if len(inside) >= 1:
            first = max((c for c in cs if c.ts <= start), key=lambda c: c.ts, default=inside[0])
            moves.append((inside[-1].close / first.close - 1) * 100)
    return sum(moves) / len(moves) if moves else None


def evaluate_verdicts(verdicts, candles_by_pair, now):
    """verdicts: ok rows oldest first. Each verdict counts until the next one or its expiry.
    Returns {verdict: {"count", "hours", "avg_move_pct", "worst_move_pct"}}."""
    out = {}
    for i, v in enumerate(verdicts):
        end = min(v["expires"], verdicts[i + 1]["ts"] if i + 1 < len(verdicts) else now, now)
        mv = market_move(candles_by_pair, v["ts"], end)
        r = out.setdefault(v["verdict"], {"count": 0, "hours": 0.0, "moves": []})
        r["count"] += 1
        r["hours"] += max(0.0, end - v["ts"]) / 3600
        if mv is not None:
            r["moves"].append(mv)
    for r in out.values():
        mv = r.pop("moves")
        r["avg_move_pct"] = sum(mv) / len(mv) if mv else None
        r["worst_move_pct"] = min(mv) if mv else None
    return out


class MajVaj:
    def __init__(self, store, config_path, notifier=None, ft=None, market=None, clock=time.time):
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.store, self.clock = store, clock
        self.notifier = notifier or TelegramNotifier(store)
        self.ft = ft or FreqtradeAPI(self.cfg.get("ft_url", "http://127.0.0.1:8080"), self.cfg["ft_username"], self.cfg["ft_password"])
        self.market = market or KrakenMarket()
        self.money_file = self.cfg["money_file"]
        settings_fn = lambda: load_settings(store.dir)
        research = GrokResearch(lambda: store.get("grok_key"), lambda: settings_fn().grok_model)
        self.verdicts = VerdictService(store.dir, self.cfg["verdict_file"], research, lambda: store.get("grok_key"),
                                       settings_fn, self.notifier, clock=clock)
        self.econ = EconData(store.dir, os.path.dirname(self.cfg["verdict_file"]), lambda: store.get("fred_key"))
        self.mode = ModeDesk(store.dir, self.verdicts._meta, clock=clock)
        self.stop_event, self.thread = threading.Event(), None

    # ------------------------------------------------------------ mode (paper / live)
    def _checks_7d(self):
        return [v for v in self.verdicts.history(since=self.clock() - 7 * 86400, limit=500)]

    def _trade_key_saved(self):
        return bool(self.store.get("kraken_trade_key") and self.store.get("kraken_trade_secret"))

    def mode_info(self, bot=None):
        bot = bot or self.ft.summary()
        if bot.get("ok") and bot.get("dry_run") and not self.verdicts._meta("paper_since"):
            self.verdicts._meta("paper_since", self.clock())
        closed = bot.get("closed_trades") or 0 if bot.get("ok") and bot.get("dry_run") else 0
        money = self.money()
        checks = self.mode.checks(closed, self._checks_7d(), money)
        block = blockers(self._trade_key_saved(), money)
        return {"state": self.mode.state(), "dry_run": bot.get("dry_run") if bot.get("ok") else None,
                "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
                "blockers": block, "locked": bool(block),
                "scope": {"bankroll": money["bankroll"], "daily_loss_limit_pct": money["daily_loss_limit_pct"]},
                "warning_off": WARNING_OFF, "warning_on": WARNING_ON, "warning_version": WARNING_VERSION,
                "phrase_live": PHRASE_LIVE, "phrase_paper": PHRASE_PAPER}

    def mode_request(self, body):
        bot = self.ft.summary()
        info = self.mode_info(bot)
        to = body.get("to")
        m = self.money()
        scope = {"bankroll": m["bankroll"], "daily_loss_limit_pct": m["daily_loss_limit_pct"],
                 "max_open_trades": bot.get("max_open_trades"), "strategy": bot.get("strategy"),
                 "pairs": ["BTC/USD", "ETH/USD", "SOL/USD"], "venue": "Kraken spot"}
        res = self.mode.request(to, body.get("phrase"), bool(body.get("ack")), info["blockers"], scope,
                                [c["name"] for c in info["checks"] if not c["ok"]])
        if res["ok"] and to == "paper":
            try:
                self.ft.pause()  # fence new entries right away; the applier finishes the job
            except FtError:
                pass
        if res["ok"]:
            self.notifier.recovery("Maj Vaj mode request: %s (%s)" % (to, res["message"]))
        return res

    # ------------------------------------------------------------ money settings
    def money(self):
        try:
            with open(self.money_file, "r", encoding="utf-8") as f:
                m, err = clean_money(json.load(f))
            return m if m else dict(MONEY_DEFAULTS)
        except (OSError, ValueError):
            return dict(MONEY_DEFAULTS)

    def save_money(self, raw):
        m, err = clean_money(raw, self.money())
        if m is None:
            return {"ok": False, "message": err}
        write_atomic(self.money_file, m)
        self.notifier.desk("Maj Vaj money settings changed: bankroll %.2f USD, daily goal %s, loss limit %.1f%%." % (
            m["bankroll"], ("%.2f USD (%s)" % (m["daily_goal_usd"], m["goal_mode"])) if m["daily_goal_usd"] else "off",
            m["daily_loss_limit_pct"]))
        return {"ok": True, "message": "Saved. The bot uses it from its next entry decision."}

    # ------------------------------------------------------------ trading switches
    def control_file(self):
        return os.path.join(os.path.dirname(self.money_file), "control.json")

    def control(self):
        try:
            with open(self.control_file(), "r", encoding="utf-8") as f:
                c = json.load(f)
            return c if isinstance(c, dict) else {}
        except (OSError, ValueError):
            return {}

    def wanted_trading(self):
        return self.control().get("trading", "running") != "paused"

    def set_trading(self, on, phrase):
        """Pause or resume new entries. Needs the exact phrase, so a stray click can never flip it."""
        want = RESUME_PHRASE if on else PAUSE_PHRASE
        if (phrase or "").strip().upper() != want:
            return {"ok": False, "message": "Nothing changed. To confirm, type exactly: %s" % want}
        c = self.control()
        c.update(trading="running" if on else "paused", changed_at=self.clock(), changed_by="Maj Vaj page")
        write_atomic(self.control_file(), c)
        try:
            self.ft.start() if on else self.ft.pause()
            applied = True
        except FtError:
            applied = False  # the enforcement loop applies it as soon as the bot answers
        self.notifier.desk("Maj Vaj trading %s from the Maj Vaj page.%s" % (
            "RESUMED" if on else "PAUSED (no new entries; open trades still managed)",
            "" if applied else " The bot is not answering yet; it will be applied when it does."))
        return {"ok": True, "message": "Trading %s." % ("resumed" if on else "paused: no new entries, open trades still managed")}

    def enforce_control(self):
        """Keep the bot in the state you chose, even after it restarts (Freqtrade always starts running)."""
        bot = self.ft.summary()
        if not bot.get("ok"):
            return None
        want = self.wanted_trading()
        if not want and bot.get("state") == "running":
            self.ft.pause()
            return "paused"
        if want and bot.get("state") == "paused":
            # Never undo the paper/live switch's own fence: no resuming while live trades are being wound
            # down (live_exit_only) or while a mode change is waiting to be applied.
            if self.mode.state().get("mode") not in ("paper", "live") or os.path.exists(self.mode.request_file):
                return None
            self.ft.start()
            return "running"
        return None

    # ------------------------------------------------------------ bot data
    def closed_trades(self):
        rows, offset = [], 0
        while True:
            page = self.ft.trades(limit=500) if offset == 0 else self.ft._call("GET", "/trades?limit=500&offset=%d" % offset)
            batch = [t for t in (page.get("trades") or []) if not t.get("is_open")]
            rows += batch
            if len(page.get("trades") or []) < 500:
                return rows
            offset += 500

    def books(self, now=None):
        """Bankroll, active, protected, today's net, from closed bot trades."""
        now = now or self.clock()
        m = self.money()
        closed = [(t["close_timestamp"] / 1000, float(t.get("close_profit_abs") or 0)) for t in self.closed_trades()
                  if t.get("close_timestamp")]
        tz = ZoneInfo(m["timezone"])
        day_start = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        active, protected = ledger(m["bankroll"], closed)
        today = [p for t, p in closed if t >= day_start]
        return {"bankroll": m["bankroll"], "active": active, "protected": protected, "today_net": sum(today),
                "today_trades": len(today), "closed": closed, "money": m}

    def status(self):
        bot = self.ft.summary()
        books = None
        if bot.get("ok"):
            try:
                b = self.books()
                b.pop("closed")
                books = b
            except FtError as e:
                bot = {"ok": False, "error": str(e)}
        latest = self.verdicts.latest()
        try:
            with open(self.cfg["verdict_file"], "r", encoding="utf-8") as f:
                current = json.load(f)
        except (OSError, ValueError):
            current = None
        return {"bot": bot, "books": books, "money": self.money(), "verdict_file": current,
                "latest_verdict": latest, "verdicts": self.verdicts.history(limit=25), "now": self.clock(),
                "schedule": self.verdicts.schedule_text(), "next_check": self.verdicts.next_check(),
                "mode": self.mode_info(bot), "data": self.econ.status(),
                "control": {"trading_wanted": self.wanted_trading(),
                            "phrases": {"pause": PAUSE_PHRASE, "resume": RESUME_PHRASE}}}

    def save_schedule(self, items):
        if isinstance(items, str):
            items = [x for x in items.replace(" ", "").split(",") if x]
        try:
            self.verdicts.set_schedule(items)
        except ValueError as e:
            return {"ok": False, "message": "Times must be like 00:00, 07:00, 13:30 (UTC, 1 to 8 of them): %s." % e}
        return {"ok": True, "message": "Grok will check at %s UTC." % ", ".join(self.verdicts.schedule_text())}

    # ------------------------------------------------------------ reports
    def evening_report(self, now=None):
        now = now or self.clock()
        bot = self.ft.summary()
        if not bot.get("ok"):
            return "Maj Vaj evening report\nThe trading bot is not answering: %s" % bot.get("error")
        b = self.books(now)
        hist = [v for v in self.verdicts.history(since=now - 86400) if v["ok"]]
        counts = {}
        for v in hist:
            counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
        fails = len([v for v in self.verdicts.history(since=now - 86400) if not v["ok"]])
        lines = ["Maj Vaj evening report (%s)" % ("paper" if bot.get("dry_run") else "LIVE"),
                 "Bot: %s, strategy %s" % (bot.get("state"), bot.get("strategy")),
                 "Today: %d closed trade(s), net %+.2f USD" % (b["today_trades"], b["today_net"]),
                 "Bankroll %.2f | active %.2f | protected profit %.2f" % (b["bankroll"], b["active"], b["protected"]),
                 "Open: %s" % (", ".join("%s %+.2f%%" % (t["pair"], (t.get("profit_pct") or 0)) for t in bot["open"]) or "none"),
                 "Fundamentals today: %s%s" % (", ".join("%s x%d" % kv for kv in counts.items()) or "no verdicts",
                                                (" | %d failed checks" % fails) if fails else "")]
        return "\n".join(lines)

    def weekly_review(self, now=None):
        now = now or self.clock()
        week = now - 7 * 86400
        lines = ["Maj Vaj weekly review"]
        try:
            closed = [t for t in self.closed_trades() if (t.get("close_timestamp") or 0) / 1000 >= week]
            net = sum(float(t.get("close_profit_abs") or 0) for t in closed)
            wins = len([t for t in closed if (t.get("close_profit_abs") or 0) > 0])
            lines.append("Trades closed: %d, net %+.2f USD, wins %d" % (len(closed), net, wins))
        except FtError as e:
            lines.append("Bot not answering: %s" % e)
        vs = sorted([v for v in self.verdicts.history(since=week, limit=1000) if v["ok"]], key=lambda v: v["ts"])
        candles = {}
        for p in PAIRS:
            try:
                candles[p] = self.market.candles(p, 60, since=week - 3600)
            except MarketError:
                pass
        ev = evaluate_verdicts(vs, candles, now) if vs and candles else {}
        if ev:
            lines.append("What the market did while each verdict was in force (avg of BTC, ETH, SOL):")
            for k in ("normal", "caution", "stand_down"):
                if k in ev:
                    r = ev[k]
                    lines.append("- %s: %d verdicts, %.0f h, avg move %s, worst %s" % (
                        k, r["count"], r["hours"],
                        "-" if r["avg_move_pct"] is None else "%+.2f%%" % r["avg_move_pct"],
                        "-" if r["worst_move_pct"] is None else "%+.2f%%" % r["worst_move_pct"]))
            lines.append("If stand-down periods were not worse than normal ones, Grok's vetoes are not yet earning their keep.")
        else:
            lines.append("No verdict data to evaluate this week.")
        return "\n".join(lines)

    def _report_tick(self):
        tz = ZoneInfo(self.money()["timezone"])
        local = datetime.fromtimestamp(self.clock(), tz)
        hour = int(self.cfg.get("evening_hour", 21))
        if local.hour != hour:
            return
        key = local.strftime("%Y-%m-%d")
        if self.verdicts._meta("evening_sent") != key:
            self.verdicts._meta("evening_sent", key)
            self.notifier.desk(self.evening_report())
        if local.weekday() == 6 and self.verdicts._meta("weekly_sent") != key:
            self.verdicts._meta("weekly_sent", key)
            self.notifier.desk(self.weekly_review())

    # ------------------------------------------------------------ run
    def start(self):
        if not os.path.exists(self.money_file):
            write_atomic(self.money_file, dict(MONEY_DEFAULTS))
        self.verdicts.start()
        self.econ.start()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="majvaj-reports")
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            for job in (self.enforce_control, self._report_tick):
                try:
                    job()
                except Exception:
                    pass
            self.stop_event.wait(60)
