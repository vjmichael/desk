"""Kraken public market data. No key needed."""
import time
from dataclasses import dataclass

from . import net
from .gate import Market

KRAKEN = "https://api.kraken.com"
KNOWN = {"BTC/USD": "XBTUSD", "ETH/USD": "ETHUSD", "SOL/USD": "SOLUSD"}


class MarketError(Exception):
    pass


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _get(url):
    return net.http_json("GET", url)


def pair_code(symbol):
    if symbol in KNOWN:
        return KNOWN[symbol]
    base, _, quote = symbol.partition("/")
    return ("XBT" if base == "BTC" else base) + quote


def _result(status, data):
    if status != 200 or not isinstance(data, dict):
        raise MarketError("Kraken answered HTTP %s" % status)
    if data.get("error"):
        raise MarketError("Kraken error: %s" % "; ".join(str(e) for e in data["error"][:2]))
    res = data.get("result")
    if not isinstance(res, dict) or not res:
        raise MarketError("Kraken returned no data")
    return res


class KrakenMarket:
    def quote(self, symbol):
        try:
            status, data = _get("%s/0/public/Ticker?pair=%s" % (KRAKEN, pair_code(symbol)))
        except net.NetError as e:
            raise MarketError(str(e)) from None
        t = next(iter(_result(status, data).values()))
        try:
            bid, ask = float(t["b"][0]), float(t["a"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            raise MarketError("unexpected ticker format for %s" % symbol) from None
        if not (bid > 0 and ask > 0 and ask >= bid):
            raise MarketError("nonsense prices for %s" % symbol)
        return Market(bid=bid, ask=ask, ts=time.time())

    def candles(self, symbol, interval, since=None):
        url = "%s/0/public/OHLC?pair=%s&interval=%d" % (KRAKEN, pair_code(symbol), int(interval))
        if since:
            url += "&since=%d" % int(since)
        try:
            status, data = _get(url)
        except net.NetError as e:
            raise MarketError(str(e)) from None
        rows = next((v for k, v in _result(status, data).items() if k != "last" and isinstance(v, list)), None)
        if rows is None:
            raise MarketError("no candle data for %s" % symbol)
        out = []
        for r in rows:
            try:
                out.append(Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])))
            except (IndexError, TypeError, ValueError):
                continue
        out.sort(key=lambda c: c.ts)
        return out
