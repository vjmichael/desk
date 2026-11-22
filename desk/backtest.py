"""Replay a simple, fixed rules strategy over stored hourly history, using the
same Gate, sizing, fees, slippage and exit rules as the paper desk.

This measures the rules baseline (not Grok: Grok's past answers cannot be
replayed without leaking the future). Settings are picked on the first 70% of
the history and then scored once on the last 30%, which the picking never saw.
"""
import itertools
import math
import time
from dataclasses import replace

from .engine import decide_exit, session_at
from .gate import Account, Market, Policy, Proposal, evaluate

HOUR = 3600
SLIP = 0.0005
HALF_SPREAD = 0.0005
MAX_HOLD_BARS = 48
GRID = [(s, t) for s in (1.5, 2.0, 3.0) for t in (3.0, 4.5, 6.0)]


def indicators(candles):
    """Per-bar ema20, ema50, rsi14, atr14 (None until warmed up). Computed once, forward only."""
    n = len(candles)
    e20, e50, rsi, atr = [None] * n, [None] * n, [None] * n, [None] * n
    k20, k50 = 2 / 21, 2 / 51
    a20 = a50 = None
    ag = al = None
    at = None
    for i, c in enumerate(candles):
        x = c.close
        a20 = x if a20 is None else x * k20 + a20 * (1 - k20)
        a50 = x if a50 is None else x * k50 + a50 * (1 - k50)
        if i >= 50:
            e20[i], e50[i] = a20, a50
        if i >= 1:
            d = x - candles[i - 1].close
            g, l = max(d, 0), max(-d, 0)
            if i <= 14:
                ag = (ag or 0) + g / 14
                al = (al or 0) + l / 14
            else:
                ag, al = (ag * 13 + g) / 14, (al * 13 + l) / 14
            if i >= 14:
                rsi[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
            pc = candles[i - 1].close
            tr = max(c.high - c.low, abs(c.high - pc), abs(c.low - pc))
            if i <= 14:
                at = (at or 0) + tr / 14
            else:
                at = (at * 13 + tr) / 14
            if i >= 14:
                atr[i] = at
    return e20, e50, rsi, atr


def run(series, stop_atr, target_atr, start_equity=1000.0, policy=None, lo=0.0, hi=1.0):
    """series: {symbol: [Candle]}. Evaluates bars with index fraction in [lo, hi)."""
    pol = policy or Policy(whitelist=tuple(series))
    ind = {s: indicators(c) for s, c in series.items()}
    index = {s: {c.ts: i for i, c in enumerate(cs)} for s, cs in series.items()}
    common = sorted(set.intersection(*[set(index[s]) for s in series]))
    a, b = int(len(common) * lo), int(len(common) * hi)
    cash, equity_curve, open_pos, trades = start_equity, [], {}, []
    day, pnl_today = None, 0.0
    peak, max_dd = start_equity, 0.0
    for t in common[a:b]:
        now = t + HOUR  # decisions use the closed bar
        d = now // 86400
        if d != day:
            day, pnl_today = d, 0.0
        marks = {}
        for sym, cs in series.items():
            i = index[sym][t]
            c = cs[i]
            marks[sym] = c.close
            pos = open_pos.get(sym)
            if pos:
                q = Market(bid=c.close * (1 - HALF_SPREAD), ask=c.close * (1 + HALF_SPREAD), ts=now)
                px, why, pos["last_checked"] = decide_exit(pos, [c], q, now)
                if px is not None:
                    proceeds = pos["qty"] * px
                    fee = proceeds * pol.fee_pct_per_side / 100
                    cash += proceeds - fee
                    pnl = proceeds - fee - pos["qty"] * pos["entry"] - pos["entry_fee"]
                    pnl_today += pnl
                    trades.append({"symbol": sym, "entry_ts": pos["opened_at"], "exit_ts": now, "pnl": pnl, "why": why,
                                   "session": session_at(pos["opened_at"])[0], "cost": pos["qty"] * pos["entry"]})
                    del open_pos[sym]
        equity = cash + sum(p["qty"] * marks[s] for s, p in open_pos.items())
        for sym, cs in series.items():
            if sym in open_pos:
                continue
            i = index[sym][t]
            e20, e50, rsi, atr = (x[i] for x in ind[sym])
            if None in (e20, e50, rsi, atr):
                continue
            c = cs[i]
            if not (c.close > e20 > e50 and 50 <= rsi <= 68):
                continue
            p = Proposal(id="bt", symbol=sym, direction="long", entry=c.close, stop=c.close - stop_atr * atr,
                         target=c.close + target_atr * atr, expires_at=now + 3600, origin="rules")
            m = Market(bid=c.close * (1 - HALF_SPREAD), ask=c.close * (1 + HALF_SPREAD), ts=now)
            dec = evaluate(p, m, Account(equity=equity, cash=cash, open_symbols=tuple(open_pos),
                                         realized_pnl_today=pnl_today), pol, now)
            if not dec.approved:
                continue
            entry = dec.entry * (1 + SLIP)
            fee = dec.quantity * entry * pol.fee_pct_per_side / 100
            cost = dec.quantity * entry + fee
            if cost > cash:
                continue
            cash -= cost
            open_pos[sym] = {"qty": dec.quantity, "entry": entry, "entry_fee": fee, "stop": dec.stop, "target": dec.target,
                             "opened_at": now, "expires_at": now + MAX_HOLD_BARS * HOUR, "last_checked": t}
        equity = cash + sum(p["qty"] * marks[s] for s, p in open_pos.items())
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
    return stats(trades, equity_curve, start_equity, max_dd, series, common[a:b])


def stats(trades, curve, start, max_dd, series, times):
    wins = [t for t in trades if t["pnl"] > 0]
    final = curve[-1] if curve else start
    by_session, by_pair = {}, {}
    for t in trades:
        for bucket, key in ((by_session, t["session"]), (by_pair, t["symbol"])):
            r = bucket.setdefault(key, {"trades": 0, "pnl": 0.0, "wins": 0})
            r["trades"] += 1
            r["pnl"] += t["pnl"]
            r["wins"] += t["pnl"] > 0
    hold = {}
    if times:
        rets = []
        for sym, cs in series.items():
            by_ts = {c.ts: c.close for c in cs}
            if times[0] in by_ts and times[-1] in by_ts:
                rets.append(by_ts[times[-1]] / by_ts[times[0]] - 1)
        hold = sum(rets) / len(rets) * 100 if rets else None
    else:
        hold = None
    return {"trades": len(trades), "win_rate": len(wins) / len(trades) * 100 if trades else None,
            "net_pnl": final - start, "return_pct": (final / start - 1) * 100, "max_drawdown_pct": max_dd,
            "buy_hold_pct": hold, "by_session": by_session, "by_pair": by_pair,
            "from": times[0] if times else None, "to": times[-1] if times else None}


def experiment(series, years=4, progress=lambda m: None):
    """Pick the best grid cell on the first 70%, then score it on the last 30%."""
    if any(not cs for cs in series.values()):
        raise ValueError("not enough history; download it first")
    cutoff = max(c[-1].ts for c in series.values()) - int(years * 365 * 86400)
    series = {s: [c for c in cs if c.ts >= cutoff] for s, cs in series.items()}
    if any(len(cs) < 500 for cs in series.values()):
        raise ValueError("not enough history; download it first")
    rows = []
    for n, (stop, target) in enumerate(GRID, 1):
        progress("testing settings %d of %d..." % (n, len(GRID)))
        r = run(series, stop, target, lo=0.0, hi=0.7)
        rows.append({"stop_atr": stop, "target_atr": target, **{k: r[k] for k in ("trades", "return_pct", "win_rate", "max_drawdown_pct")}})
    ranked = [r for r in rows if r["trades"] >= 20] or rows
    best = max(ranked, key=lambda r: r["return_pct"])
    progress("scoring the best settings on data it has not seen...")
    test = run(series, best["stop_atr"], best["target_atr"], lo=0.7, hi=1.0)
    train = run(series, best["stop_atr"], best["target_atr"], lo=0.0, hi=0.7)
    return {"made_at": time.time(), "years": years, "grid": rows, "best": best, "train": train, "test": test}
