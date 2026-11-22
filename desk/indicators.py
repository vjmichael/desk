"""A few plain indicators, used to give the scout a compact picture."""
import math


def _r(x):
    return None if x is None else float("%.6g" % x)


def ema(values, n):
    if len(values) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(values[:n]) / n
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def hourly_volatility_pct(closes):
    rets = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    m = sum(rets) / len(rets)
    return math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def summarize(quote, candles_1h):
    """candles_1h: oldest first. Returns a small dict of facts, never advice."""
    closes = [c.close for c in candles_1h]
    last24 = candles_1h[-24:]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    price = (quote.bid + quote.ask) / 2
    trend = "unknown"
    if e20 and e50:
        trend = "up" if price > e20 > e50 else "down" if price < e20 < e50 else "mixed"
    vols = [c.volume for c in candles_1h]
    recent, before = vols[-6:], vols[-30:-6]
    ratio = (sum(recent) / len(recent)) / (sum(before) / len(before)) if recent and before and sum(before) > 0 else None
    change = (price / closes[-25] - 1) * 100 if len(closes) >= 25 and closes[-25] > 0 else None
    return {
        "bid": _r(quote.bid), "ask": _r(quote.ask),
        "change_24h_pct": _r(change),
        "high_24h": _r(max(c.high for c in last24)), "low_24h": _r(min(c.low for c in last24)),
        "rsi14": _r(rsi(closes)), "ema20": _r(e20), "ema50": _r(e50), "trend": trend,
        "volume_vs_prior_ratio": _r(ratio),
        "hourly_volatility_pct": _r(hourly_volatility_pct(closes[-48:])),
        "last_6_hourly_closes": [_r(c) for c in closes[-6:]],
    }
