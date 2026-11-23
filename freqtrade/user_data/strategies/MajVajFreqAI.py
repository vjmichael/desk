"""Maj Vaj: FreqAI spot strategy for Kraken BTC/ETH/SOL (long only).

The model predicts the average price change over the next 24 hourly candles.
The strategy buys only when that prediction clears trading costs by a margin and
the model trusts the current market (do_predict == 1). Exits: the prediction turns
negative, the stop is hit, or the trade is 24 hours old.

Fundamentals gate: Maj Vaj Desk asks Grok (web + X search) for news, scheduled events and
sentiment, and writes a verdict file: normal, caution or stand_down, with an expiry time.
In dry-run and live, every new entry checks it: stand_down blocks, caution halves the stake,
and a missing, unreadable or expired verdict counts as stand_down (fail closed). Exits and
open trades are never affected. Backtests ignore the verdict unless majvaj_gate_force is set.

Money rules (the Maj Vaj financial design, TB-FIN-01), applied in every mode including backtests:
- Only the authorized bankroll is ever at risk; the rest of the account is never used.
- Every closed winning trade's net profit (after fees) becomes protected and is never traded again.
  Losses come out of the active bankroll; protected profit never refills it.
- Optional daily goal. Once reached: stop new entries (default), keep trading, or opportunity mode:
  extra trades only in chosen sessions, one at a time, smaller, with a stronger signal and a NORMAL
  verdict, sized so a loss can never take back more than a set share of the day's profit.
- Daily loss limit: no new entries after losing a set share of the day's starting bankroll.
Settings live in majvaj/money.json (written from the Desk page). Open trades are always managed.

Economic calendar gate (all modes, backtests included, because these dates are published in advance):
no new entries from 60 minutes before to 90 minutes after an FOMC decision or a CPI, jobs or PPI release.
Outside data features (point-in-time): Crypto Fear & Greed level and 7-day change, hours to the next and
since the last high-impact event, and (when a FRED key is set) CPI year-on-year, unemployment and the
fed funds rate as first published. A value is used only after the moment it became public."""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from functools import reduce

import talib.abstract as ta
import numpy as np
import pandas as pd
from pandas import DataFrame
from technical import qtpylib

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy

logger = logging.getLogger(__name__)
VERDICT_FILE = "/freqtrade/user_data/majvaj/verdict.json"
VERDICTS = ("normal", "caution", "stand_down")
MONEY_FILE = "/freqtrade/user_data/majvaj/money.json"
MONEY_DEFAULTS = {"bankroll": None, "daily_goal_usd": 0.0, "goal_mode": "stop", "goal_reduce_pct": 50,
                  "goal_keep_pct": 50, "goal_sessions": ["Asia/Tokyo", "London/New York overlap"],
                  "daily_loss_limit_pct": 3.0, "timezone": "America/Denver"}
GOAL_MODES = ("continue", "stop", "opportunity")
# UTC session windows, same as Desk's scout schedule
SESSIONS = ((0, 7, "Asia/Tokyo"), (7, 13, "London"), (13, 16, "London/New York overlap"),
            (16, 21, "New York"), (21, 24, "Quiet (late US)"))
OPPORTUNITY_SIGNAL_FACTOR = 1.5   # after the goal, the model must be 50% more convinced
ROUND_TRIP_COST = 0.0052
EVENTS_FILE = "/freqtrade/user_data/majvaj/events.json"
SERIES_FILE = "/freqtrade/user_data/majvaj/series.csv"
EVENT_BEFORE_MIN, EVENT_AFTER_MIN = 60, 90
DECISIONS_FILE = "/freqtrade/user_data/majvaj/decisions.jsonl"
DECISIONS_MAX_BYTES = 1_000_000
_cache = {}


def log_decision(path, pair, result, reason, now=None):
    """Append one entry decision (opened / rejected and why) for the Desk page. Never raises."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > DECISIONS_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now or time.time(), "pair": pair, "result": result, "reason": str(reason)[:300]}) + "\n")
    except Exception:
        pass


def _cached(path, loader):
    """Re-read a data file only when it changes on disk."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        value = loader(path)
    except Exception:
        logger.warning("Maj Vaj: could not read %s", path)
        return None
    _cache[path] = (mtime, value)
    return value


def load_events(path):
    """Sorted numpy array of event times (unix seconds), or None if there is no calendar."""
    def read(p):
        with open(p, "r", encoding="utf-8") as f:
            ev = json.load(f).get("events") or []
        return np.array(sorted(float(e["at"]) for e in ev if isinstance(e, dict) and "at" in e))
    return _cached(path, read)


def in_event_window(events, now_ts, before_min=EVENT_BEFORE_MIN, after_min=EVENT_AFTER_MIN):
    if events is None or not len(events):
        return False
    i = np.searchsorted(events, now_ts - after_min * 60)
    return i < len(events) and events[i] <= now_ts + before_min * 60


def load_series(path):
    def read(p):
        df = pd.read_csv(p)
        return {k: g.sort_values("available_at")[["available_at", "value"]].reset_index(drop=True)
                for k, g in df.groupby("series")}
    return _cached(path, read)


def point_in_time(close_ts, table):
    """For each candle close time, the latest value already public at that time (NaN before the first)."""
    if table is None or table.empty:
        return np.full(len(close_ts), np.nan)
    idx = np.searchsorted(table["available_at"].to_numpy(), close_ts, side="right") - 1
    vals = table["value"].to_numpy()
    out = np.where(idx >= 0, vals[np.clip(idx, 0, None)], np.nan)
    return out


def event_distances(close_ts, events, cap_hours=168.0):
    if events is None or not len(events):
        return np.full(len(close_ts), cap_hours), np.full(len(close_ts), cap_hours)
    j = np.searchsorted(events, close_ts, side="right")
    nxt = np.where(j < len(events), (events[np.clip(j, 0, len(events) - 1)] - close_ts) / 3600, cap_hours)
    prv = np.where(j > 0, (close_ts - events[np.clip(j - 1, 0, None)]) / 3600, cap_hours)
    return np.minimum(nxt, cap_hours), np.minimum(prv, cap_hours)


def session_name(dt_utc):
    h = dt_utc.hour
    return next((n for lo, hi, n in SESSIONS if lo <= h < hi), "Quiet (late US)")


def load_money(path, fallback_bankroll):
    """Money settings with safe defaults. A bad file means no new entries (bankroll 0)."""
    m = dict(MONEY_DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            m.update({k: v for k, v in json.load(f).items() if k in MONEY_DEFAULTS})
    except FileNotFoundError:
        m["bankroll"] = fallback_bankroll
    except (OSError, ValueError, AttributeError):
        m["bankroll"] = 0.0
    try:
        m["bankroll"] = max(0.0, float(m["bankroll"] if m["bankroll"] is not None else fallback_bankroll))
        m["daily_goal_usd"] = max(0.0, float(m["daily_goal_usd"] or 0))
        m["goal_reduce_pct"] = min(99, max(1, int(m["goal_reduce_pct"])))
        m["goal_keep_pct"] = min(100, max(0, int(m["goal_keep_pct"])))
        m["goal_sessions"] = [x for x in (m["goal_sessions"] or []) if isinstance(x, str)]
        m["daily_loss_limit_pct"] = min(100.0, max(0.0, float(m["daily_loss_limit_pct"])))
        ZoneInfo(m["timezone"])
    except (TypeError, ValueError, KeyError, Exception):
        m.update(bankroll=0.0)
    if m["goal_mode"] == "reduce":
        m["goal_mode"] = "opportunity"  # older name
    if m["goal_mode"] not in GOAL_MODES:
        m["goal_mode"] = "stop"
    return m


def goal_state(money, today):
    """today: today's closed trade P&L in close order. Returns (reached, peak, net).
    Once the goal is reached it stays reached for the rest of the day, even if a later loss
    pulls the day back below it."""
    goal, net, peak, reached = money["daily_goal_usd"], 0.0, 0.0, False
    for p in today:
        net += p
        peak = max(peak, net)
        if goal > 0 and net >= goal:
            reached = True
    return reached, peak, net


def ledger(bankroll, closed):
    """closed: list of (close_datetime_utc, net_profit). Returns (active, protected) and the
    active capital at each day start is computed by the caller from the same rule."""
    active, protected = bankroll, 0.0
    for _, pnl in sorted(closed, key=lambda x: x[0]):
        if pnl > 0:
            protected += pnl
        else:
            active += pnl
    return max(0.0, active), protected


def allowance(money, closed, open_stakes, now_utc, slots_left, proposed, stop_frac=0.04):
    """Return (stake, reason, mode). closed: [(close_dt_utc, net_pnl)], open_stakes: stakes of open trades.
    mode is "normal" or "opportunity" (after the daily goal: careful extra trades only)."""
    if money["bankroll"] <= 0:
        return 0.0, "no authorized bankroll", "normal"
    tz = ZoneInfo(money["timezone"])
    day_start = now_utc.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    before = [(t, p) for t, p in closed if t < day_start]
    today = [p for t, p in sorted(closed, key=lambda x: x[0]) if t >= day_start]
    active, protected = ledger(money["bankroll"], closed)
    day_start_active, _ = ledger(money["bankroll"], before)
    reached, peak, today_net = goal_state(money, today)
    if money["daily_loss_limit_pct"] > 0 and today_net <= -day_start_active * money["daily_loss_limit_pct"] / 100:
        return 0.0, "daily loss limit reached (%.2f today)" % today_net, "normal"
    free = active - sum(open_stakes)
    if free <= 0:
        return 0.0, "bankroll fully in use", "normal"
    stake = min(proposed, free / max(1, slots_left))
    if not reached or money["goal_mode"] == "continue":
        return stake, "ok (active %.2f, protected %.2f, free %.2f)" % (active, protected, free), "normal"
    if money["goal_mode"] == "stop":
        return 0.0, "daily goal reached (%.2f), no new entries today" % peak, "normal"
    # opportunity mode: careful extra trades that can never give back more than the day allows
    if open_stakes:
        return 0.0, "goal reached: one careful trade at a time", "opportunity"
    session = session_name(now_utc)
    if session not in money["goal_sessions"]:
        return 0.0, "goal reached: extra trades only in %s (now %s)" % (", ".join(money["goal_sessions"]), session), "opportunity"
    floor = peak * money["goal_keep_pct"] / 100
    budget = today_net - floor
    if budget <= 0:
        return 0.0, "goal reached: the day's kept profit (%.2f) is protected, done for today" % floor, "opportunity"
    stake = min(stake * money["goal_reduce_pct"] / 100, budget / (stop_frac + ROUND_TRIP_COST))
    return stake, "goal reached: careful trade, can risk at most %.2f of today's %.2f" % (budget, today_net), "opportunity"


def read_verdict(path, now):
    """Return (verdict, reason). Anything doubtful is stand_down."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            v = json.load(f)
        verdict, expires = v.get("verdict"), float(v.get("expires", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return "stand_down", "no readable verdict file"
    if verdict not in VERDICTS:
        return "stand_down", "unknown verdict %r" % (verdict,)
    if now >= expires:
        return "stand_down", "verdict expired %d minutes ago" % ((now - expires) // 60)
    return verdict, "; ".join(str(r) for r in (v.get("reasons") or [])[:3])[:300]


class MajVajFreqAI(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    use_exit_signal = True
    startup_candle_count = 240           # longest indicator: 50 periods on 4h candles = 200 hours

    stoploss = -0.04
    minimal_roi = {"0": 0.06, "1440": -1}  # take 6% at once; after 24 hours exit at market

    # costs are about 0.52% round trip on Kraken taker fees, so demand clearly more than that
    entry_threshold = 0.012

    def feature_engineering_expand_all(self, dataframe: DataFrame, period: int, metadata: dict, **kwargs) -> DataFrame:
        dataframe["%-rsi-period"] = ta.RSI(dataframe, timeperiod=period)
        dataframe["%-mfi-period"] = ta.MFI(dataframe, timeperiod=period)
        dataframe["%-adx-period"] = ta.ADX(dataframe, timeperiod=period)
        dataframe["%-close_vs_ema-period"] = dataframe["close"] / ta.EMA(dataframe, timeperiod=period) - 1
        dataframe["%-roc-period"] = ta.ROC(dataframe, timeperiod=period)
        bb = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=period, stds=2.2)
        dataframe["%-bb_width-period"] = (bb["upper"] - bb["lower"]) / bb["mid"]
        dataframe["%-close_vs_bb_lower-period"] = dataframe["close"] / bb["lower"] - 1
        dataframe["%-relative_volume-period"] = dataframe["volume"] / dataframe["volume"].rolling(period).mean()
        dataframe["%-atr_pct-period"] = ta.ATR(dataframe, timeperiod=period) / dataframe["close"]
        return dataframe

    def feature_engineering_expand_basic(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe["%-pct-change"] = dataframe["close"].pct_change()
        dataframe["%-range_pct"] = (dataframe["high"] - dataframe["low"]) / dataframe["close"]
        return dataframe

    def feature_engineering_standard(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        dataframe["%-day_of_week"] = dataframe["date"].dt.dayofweek
        dataframe["%-hour_of_day"] = dataframe["date"].dt.hour
        if not self.config.get("majvaj_external_features", True):
            return dataframe
        # a candle's information is complete at its close, so outside data is matched to the close time
        close_ts = ((dataframe["date"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
                    + self._tf_seconds()).to_numpy().astype(float)
        series = load_series(self.config.get("majvaj_series_file", SERIES_FILE)) or {}
        fng = point_in_time(close_ts, series.get("fear_greed"))
        dataframe["%-fear_greed"] = pd.Series(fng).ffill().fillna(50.0).to_numpy()
        dataframe["%-fear_greed_chg7d"] = pd.Series(dataframe["%-fear_greed"].to_numpy()).diff(
            int(7 * 86400 / self._tf_seconds())).fillna(0.0).to_numpy()
        nxt, prv = event_distances(close_ts, load_events(self.config.get("majvaj_events_file", EVENTS_FILE)))
        dataframe["%-hours_to_event"] = nxt
        dataframe["%-hours_since_event"] = prv
        # Macro features change the model's inputs, so they are an explicit setting, never switched on by data
        # appearing mid-run (a trained model fed different columns fails until it retrains). To turn them on, set
        # "majvaj_macro_features": true AND change freqai.identifier so a fresh model is trained.
        if self.config.get("majvaj_macro_features"):
            for name in ("unemployment_rate", "fed_funds_rate", "cpi_yoy"):
                v = point_in_time(close_ts, series.get(name))
                dataframe["%-" + name] = pd.Series(v).ffill().fillna(0.0).to_numpy()
        return dataframe

    def _tf_seconds(self):
        unit = {"m": 60, "h": 3600, "d": 86400}[self.timeframe[-1]]
        return int(self.timeframe[:-1]) * unit

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        n = self.freqai_info["feature_parameters"]["label_period_candles"]
        dataframe["&-s_close"] = dataframe["close"].shift(-n).rolling(n).mean() / dataframe["close"] - 1
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return self.freqai.start(dataframe, metadata, self)

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        conditions = [df["do_predict"] == 1, df["&-s_close"] > self.entry_threshold, df["volume"] > 0]
        df.loc[reduce(lambda a, b: a & b, conditions), ["enter_long", "enter_tag"]] = (1, "model")
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        df.loc[(df["do_predict"] == 1) & (df["&-s_close"] < 0), "exit_long"] = 1
        return df

    # ---------------------------------------------------------------- fundamentals gate
    def _gate_active(self):
        return self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE) or bool(self.config.get("majvaj_gate_force"))

    def _verdict(self, current_time: datetime):
        path = self.config.get("majvaj_verdict_file", VERDICT_FILE)
        now = current_time.timestamp() if self.config.get("majvaj_gate_force") else time.time()
        return read_verdict(path, now)

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float, time_in_force: str,
                            current_time: datetime, entry_tag, side: str, **kwargs) -> bool:
        try:
            return self._confirm(pair, current_time)
        except Exception:  # any fault in the safety checks means no trade, never a default yes
            logger.exception("Maj Vaj: entry check failed on %s, entry refused", pair)
            return False

    def _decided(self, pair, result, reason):
        if self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE):  # backtests would flood the page
            log_decision(self.config.get("majvaj_decisions_file", DECISIONS_FILE), pair, result, reason)

    def _confirm(self, pair, current_time):
        ok, reason = self._confirm_reason(pair, current_time)
        self._decided(pair, "approved" if ok else "rejected", reason)
        if not ok:
            logger.info("Maj Vaj: no entry on %s (%s)", pair, reason)
        return ok

    def _confirm_reason(self, pair, current_time):
        events = load_events(self.config.get("majvaj_events_file", EVENTS_FILE))
        if in_event_window(events, current_time.timestamp()):
            return False, "calendar: inside a high-impact event window (FOMC / CPI / jobs / PPI)"
        if self._opportunity_mode(current_time):
            if not self._strong_signal(pair):
                return False, "goal reached: signal not strong enough for an extra trade"
            if self._gate_active() and self._verdict(current_time)[0] != "normal":
                return False, "goal reached: extra trades need a NORMAL fundamentals verdict"
        if not self._gate_active():
            return True, "approved"
        verdict, reason = self._verdict(current_time)
        if verdict == "stand_down":
            return False, "fundamentals: stand down (%s)" % reason
        return True, "approved (fundamentals %s)" % verdict

    def _money_decision(self, current_time, proposed_stake=1e12, max_stake=1e12):
        money = load_money(self.config.get("majvaj_money_file", MONEY_FILE),
                           float(self.config.get("dry_run_wallet", 0)) if self.config.get("dry_run") else 0.0)
        closed = [(t.close_date_utc, float(t.close_profit_abs or 0)) for t in Trade.get_trades_proxy(is_open=False)]
        opened = Trade.get_trades_proxy(is_open=True)
        slots = max(1, int(self.config.get("max_open_trades", 1)) - len(opened))
        return allowance(money, closed, [float(t.stake_amount) for t in opened], current_time.astimezone(timezone.utc),
                         slots, min(proposed_stake, max_stake), abs(self.stoploss))

    def _opportunity_mode(self, current_time):
        return self._money_decision(current_time)[2] == "opportunity"

    def _strong_signal(self, pair):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty or "&-s_close" not in df:
            return False
        return float(df["&-s_close"].iloc[-1]) >= self.entry_threshold * OPPORTUNITY_SIGNAL_FACTOR

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float, proposed_stake: float,
                            min_stake, max_stake: float, leverage: float, entry_tag, side: str, **kwargs) -> float:
        try:
            return self._stake(pair, current_time, proposed_stake, min_stake, max_stake)
        except Exception:  # any fault in the money rules means no trade, never the default stake
            logger.exception("Maj Vaj: money rules failed on %s, entry refused", pair)
            return 0.0

    def _stake(self, pair, current_time, proposed_stake, min_stake, max_stake):
        stake, reason, mode = self._money_decision(current_time, proposed_stake, max_stake)
        if self._gate_active() and stake > 0:
            verdict, _ = self._verdict(current_time)
            if verdict == "caution":
                stake *= 0.5
        if stake <= 0:
            logger.info("Maj Vaj money rules: no entry on %s (%s)", pair, reason)
            self._decided(pair, "rejected", "money rules: " + reason)
            return 0.0
        if min_stake and stake < min_stake:
            logger.info("Maj Vaj money rules: %s stake %.2f below exchange minimum %.2f, skipped", pair, stake, min_stake)
            self._decided(pair, "rejected", "stake %.2f below Kraken's minimum %.2f" % (stake, min_stake))
            return 0.0
        return stake

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 1},
            {"method": "StoplossGuard", "lookback_period_candles": 12, "trade_limit": 3,
             "stop_duration_candles": 6, "only_per_pair": False},
            {"method": "MaxDrawdown", "lookback_period_candles": 48, "trade_limit": 5,
             "stop_duration_candles": 12, "max_allowed_drawdown": 0.10},
        ]
