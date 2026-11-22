"""The trade Gate, paper mode.

Design change from v0.6: the Gate used to demand pre-approved setups, a
pre-validated edge model and complete data before ANY paper trade. That made
it impossible to collect the evidence it demanded. In paper mode the Gate now
rejects only what makes an order impossible or unprofitable after costs, and
shrinks size instead of blocking where it can. Every decision, approved or
rejected, lists each check with its reason, so you can always see why.

Live trading is not enabled in this build: mode "live" is always rejected.
"""
import json
import math
import os
import time
from dataclasses import dataclass, field, asdict


@dataclass
class Policy:
    mode: str = "paper"
    whitelist: tuple = ("BTC/USD", "ETH/USD", "SOL/USD")
    max_open_positions: int = 5
    risk_per_trade_pct: float = 0.5      # of equity, measured at the stop
    max_position_pct: float = 20.0       # one position's cost, % of equity
    daily_loss_limit_pct: float = 3.0    # new entries stop for the day
    min_reward_risk: float = 1.2         # after fees and spread
    min_stop_distance_pct: float = 0.3
    max_stop_distance_pct: float = 10.0
    max_entry_drift_pct: float = 1.0     # proposal entry vs current ask
    max_spread_pct: float = 0.5
    max_data_age_s: int = 300
    fee_pct_per_side: float = 0.26       # conservative Kraken taker fee
    min_notional: float = 10.0           # smallest order we will place, USD
    qty_decimals: int = 6
    stand_down_size_factor: float = 0.5  # AI doubts: trade smaller, not zero


@dataclass
class Proposal:
    id: str
    symbol: str
    direction: str
    entry: float
    stop: float
    target: float
    expires_at: float                    # unix seconds
    origin: str = "ai"                   # "ai" or "rules"
    confidence: float | None = None      # advisory only, never sizes a trade
    rationale: str = ""
    evidence_ids: list = field(default_factory=list)
    stand_down: bool = False             # reviewer wants smaller size


@dataclass
class Market:
    bid: float
    ask: float
    ts: float


@dataclass
class Account:
    equity: float
    cash: float
    open_symbols: tuple = ()
    realized_pnl_today: float = 0.0
    paused: bool = False


@dataclass
class Decision:
    approved: bool
    reason: str                          # first failure, or "approved"
    checks: list = field(default_factory=list)   # [{name, passed, detail}]
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    quantity: float = 0.0
    notional: float = 0.0
    risk_amount: float = 0.0
    notes: list = field(default_factory=list)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def evaluate(p, m, a, pol=None, now=None):
    pol = pol or Policy()
    now = time.time() if now is None else now
    d = Decision(False, "")

    def check(name, ok, detail):
        d.checks.append({"name": name, "passed": bool(ok), "detail": detail})
        if not ok and not d.reason:
            d.reason = "%s: %s" % (name, detail)
        return bool(ok)

    def done():
        d.approved = not d.reason
        if d.approved:
            d.reason = "approved"
        return d

    if not check("mode", pol.mode == "paper",
                 "paper mode" if pol.mode == "paper" else "live trading is not enabled in this build"):
        return done()

    valid = (_num(p.entry) and _num(p.stop) and _num(p.target)
             and p.direction == "long" and _num(p.expires_at) and p.expires_at > now)
    if not check("valid", valid,
                 "ok" if valid else "needs a long (spot) proposal with positive prices that has not expired"):
        return done()

    if not check("whitelist", p.symbol in pol.whitelist,
                 "ok" if p.symbol in pol.whitelist else "%s is not in the allowed list" % p.symbol):
        return done()

    if a.paused:
        check("account", False, "desk is paused")
        return done()
    daily_floor = -a.equity * pol.daily_loss_limit_pct / 100
    state = ("daily loss limit reached" if a.realized_pnl_today <= daily_floor
             else "already holding %s" % p.symbol if p.symbol in a.open_symbols
             else "max open positions reached" if len(a.open_symbols) >= pol.max_open_positions
             else "ok")
    if not check("account", state == "ok", state):
        return done()

    age = now - m.ts
    spread = (m.ask - m.bid) / m.ask * 100 if _num(m.ask) and _num(m.bid) else float("inf")
    ok = age <= pol.max_data_age_s and spread <= pol.max_spread_pct
    msg = ("ok (spread %.3f%%)" % spread if ok
           else "price data is %ds old" % age if age > pol.max_data_age_s
           else "spread %.2f%% is wider than %.2f%%" % (spread, pol.max_spread_pct))
    if not check("market", ok, msg):
        return done()

    # Levels. The AI's entry is a hint: if it is near the current price we
    # re-peg to the current ask. Stop and target must still make sense.
    drift = abs(p.entry - m.ask) / m.ask * 100
    entry = m.ask
    stop_pct = (entry - p.stop) / entry * 100
    ok = (drift <= pol.max_entry_drift_pct and p.stop < entry < p.target
          and pol.min_stop_distance_pct <= stop_pct <= pol.max_stop_distance_pct)
    msg = ("ok (entry re-pegged to ask %.6g, stop %.2f%% away)" % (entry, stop_pct) if ok
           else "entry is %.2f%% from the market; the idea is stale" % drift if drift > pol.max_entry_drift_pct
           else "stop and target must sit below and above the entry" if not (p.stop < entry < p.target)
           else "stop distance %.2f%% is outside %.1f%%-%.1f%%" % (
               stop_pct, pol.min_stop_distance_pct, pol.max_stop_distance_pct))
    if not check("levels", ok, msg):
        return done()

    # Worth it, after costs. No prevalidated edge model is required in paper
    # mode: measuring the edge is what paper trading is for.
    cost_pct = 2 * pol.fee_pct_per_side + spread
    gross = (p.target - entry) / entry * 100
    net_reward = gross - cost_pct
    net_risk = stop_pct + cost_pct
    rr = net_reward / net_risk if net_risk > 0 else 0
    ok = net_reward > 0 and rr >= pol.min_reward_risk
    if not check("worth_it", ok,
                 "net reward/risk %.2f after %.2f%% costs" % (rr, cost_pct) if ok
                 else "net reward/risk %.2f after %.2f%% costs is below %.2f" % (rr, cost_pct, pol.min_reward_risk)):
        return done()

    # Size from the stop distance. Round down; never enlarge to reach a minimum.
    factor = pol.stand_down_size_factor if p.stand_down else 1.0
    if p.stand_down:
        d.notes.append("reviewer stand-down: size cut to %d%%" % round(factor * 100))
    risk_amount = a.equity * pol.risk_per_trade_pct / 100 * factor
    loss_per_unit = (entry - p.stop) + entry * cost_pct / 100
    qty = risk_amount / loss_per_unit
    qty = min(qty, a.equity * pol.max_position_pct / 100 / entry, a.cash / entry)
    scale = 10 ** pol.qty_decimals
    qty = math.floor(qty * scale) / scale
    notional = qty * entry
    ok = qty > 0 and notional >= pol.min_notional
    if not check("sizing", ok,
                 "%.6g units, cost %.2f, risk %.2f" % (qty, notional, risk_amount) if ok
                 else "position would be %.2f, below the %.2f minimum (not enlarged)" % (notional, pol.min_notional)):
        return done()

    d.entry, d.stop, d.target = entry, p.stop, p.target
    d.quantity, d.notional, d.risk_amount = qty, notional, risk_amount
    return done()


def log_decision(path, proposal, decision):
    """Append one line per decision so you can see why nothing (or something) traded."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.time(), "proposal": asdict(proposal),
                            "decision": asdict(decision)}) + "\n")
