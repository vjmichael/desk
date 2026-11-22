"""Historical hourly candles for the whitelist, stored in data/history.db.

Source 1: Kraken's published OHLCVT archive (years of history). Only the files
we need are read, using HTTP byte ranges into the split ZIP, so we download a
few megabytes, not the multi-gigabyte archive.
Source 2: Kraken's public OHLC endpoint tops the history up to today.
"""
import bisect
import csv
import io
import os
import sqlite3
import threading
import zipfile

from . import net
from .market import Candle, KNOWN, KrakenMarket, MarketError

BASE = "https://assets.kraken.com/marketing/institutions/"
PARTS = tuple("Kraken_OHLCVT_Full.zip.part%02d" % i for i in range(5))
CHUNK = 1024 * 1024
BUDGET = 400 * 1024 * 1024       # most we will download in one run
MAX_PART = 4 * 1024 ** 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles(symbol TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL,
  PRIMARY KEY(symbol, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


class HistoryError(Exception):
    pass


def connect(path):
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


class SplitReader(io.RawIOBase):
    """Seekable view over the concatenated ZIP parts."""
    def __init__(self, lengths, read_at):
        self.ends, total = [], 0
        for n in lengths:
            total += n
            self.ends.append(total)
        self.pos, self.read_at = 0, read_at

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.ends[-1] + offset
        if pos < 0:
            raise OSError("invalid seek")
        self.pos = pos
        return pos

    def read(self, size=-1):
        remaining = max(0, self.ends[-1] - self.pos)
        size = remaining if size is None or size < 0 else min(size, remaining)
        out, until = bytearray(), self.pos + size
        while self.pos < until:
            i = bisect.bisect_right(self.ends, self.pos)
            start = 0 if i == 0 else self.ends[i - 1]
            n = min(until - self.pos, self.ends[i] - self.pos, CHUNK)
            data = self.read_at(i, self.pos - start, n)
            if len(data) != n:
                raise HistoryError("short read from the archive")
            out.extend(data)
            self.pos += n
        return bytes(out)

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


class RemoteParts:
    def __init__(self, base=None, parts=None, budget=BUDGET, raw=None):
        self.base, self.parts = base or BASE, parts or PARTS
        self.raw, self.budget, self.received, self.cache = raw or net.http_raw, budget, 0, None
        self.lengths, self.etags = [], []
        for name in self.parts:
            status, hdr, _ = self.raw("HEAD", self.base + name, {"Accept-Encoding": "identity"})
            hdr = {k.lower(): v for k, v in hdr.items()}
            try:
                size = int(hdr.get("content-length", ""))
            except ValueError:
                size = 0
            if status != 200 or not 0 < size <= MAX_PART:
                raise HistoryError("Kraken's archive is not reachable right now (HTTP %s for %s)" % (status, name))
            self.lengths.append(size)
            self.etags.append(hdr.get("etag"))

    def read_at(self, index, offset, n):
        out = bytearray()
        while n:
            begin = offset // CHUNK * CHUNK
            end = min(begin + CHUNK, self.lengths[index]) - 1
            if self.cache is None or self.cache[0] != (index, begin):
                if self.received + (end - begin + 1) > self.budget:
                    raise HistoryError("download limit reached; nothing more was fetched")
                h = {"Range": "bytes=%d-%d" % (begin, end), "Accept-Encoding": "identity"}
                etag = self.etags[index]
                if etag and not etag.startswith("W/"):
                    h["If-Match"] = etag
                status, hdr, data = self.raw("GET", self.base + self.parts[index], h)
                if status != 206 or len(data) != end - begin + 1:
                    raise HistoryError("the server did not honour a byte-range request (HTTP %s)" % status)
                self.received += len(data)
                self.cache = ((index, begin), data)
            data = self.cache[1]
            take = min(n, len(data) - (offset - begin))
            if take <= 0:
                raise HistoryError("bad range offset")
            out.extend(data[offset - begin:offset - begin + take])
            offset += take
            n -= take
        return bytes(out)

    def reader(self):
        return SplitReader(self.lengths, self.read_at)


def _member_names(symbol):
    base, _, quote = symbol.partition("/")
    codes = {("XBT" if base == "BTC" else base) + quote, base + quote, "XBT" + quote if base == "BTC" else base + quote}
    return {c + "_60.csv" for c in codes}


def import_archive(db, symbols, remote, progress=lambda m: None):
    """Copy the hourly file of each symbol out of the archive into the database."""
    results = {}
    with zipfile.ZipFile(remote.reader()) as z:
        infos = z.infolist()
        for sym in symbols:
            wanted = _member_names(sym)
            hit = [i for i in infos if i.filename.rsplit("/", 1)[-1] in wanted and not i.is_dir()]
            if len(hit) != 1:
                sample = sorted({i.filename.rsplit("/", 1)[-1] for i in infos if "XBT" in i.filename or "ETHUSD" in i.filename})[:6]
                raise HistoryError("could not find the hourly file for %s in the archive (looked for %s; saw e.g. %s)"
                                   % (sym, sorted(wanted), sample))
            progress("reading %s (%.1f MB compressed)" % (sym, hit[0].compress_size / 1e6))
            rows = []
            with z.open(hit[0]) as f:
                for line in io.TextIOWrapper(f, encoding="utf-8", newline=""):
                    parts = line.strip().split(",")
                    if len(parts) < 6:
                        continue
                    try:
                        ts, o, h, l, c, v = int(parts[0]), *map(float, parts[1:6])
                    except ValueError:
                        continue
                    if o > 0 and h >= l > 0 and c > 0:
                        rows.append((sym, ts, o, h, l, c, v))
            db.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)", rows)
            db.commit()
            results[sym] = len(rows)
            progress("%s: %d hourly candles stored" % (sym, len(rows)))
    return results


def top_up(db, symbols, market=None, progress=lambda m: None, max_pages=40):
    """Fill from the last stored candle up to now using the public OHLC endpoint."""
    market = market or KrakenMarket()
    for sym in symbols:
        added = 0
        for _ in range(max_pages):
            row = db.execute("SELECT MAX(ts) FROM candles WHERE symbol=?", (sym,)).fetchone()
            last = row[0] or 0
            try:
                got = market.candles(sym, 60, since=last or None)
            except MarketError as e:
                raise HistoryError("top-up for %s failed: %s" % (sym, e)) from None
            fresh = [c for c in got[:-1] if c.ts > last]  # the newest candle is still forming
            if not fresh:
                break
            db.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)",
                           [(sym, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in fresh])
            db.commit()
            added += len(fresh)
        progress("%s: topped up %d recent candles" % (sym, added))


def summary(db, symbols):
    out = {}
    for sym in symbols:
        n, a, b = db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM candles WHERE symbol=?", (sym,)).fetchone()
        out[sym] = {"rows": n, "first": a, "last": b}
    return out


def load(db, symbol, start_ts=0):
    return [Candle(ts, o, h, l, c, v) for ts, o, h, l, c, v in
            db.execute("SELECT ts,o,h,l,c,v FROM candles WHERE symbol=? AND ts>=? ORDER BY ts", (symbol, start_ts))]


class Job:
    """One background job at a time (download or backtest), with progress text."""
    def __init__(self):
        self.lock = threading.Lock()
        self.running, self.message, self.ok, self.kind = False, "", None, ""

    def start(self, kind, fn):
        with self.lock:
            if self.running:
                return False
            self.running, self.kind, self.message, self.ok = True, kind, "starting...", None

        def run():
            try:
                fn(self.say)
                self.ok = True
            except HistoryError as e:
                self.ok, self.message = False, str(e)
            except Exception as e:  # shown on the page, never silent
                self.ok, self.message = False, "unexpected error: %s: %s" % (type(e).__name__, e)
            finally:
                self.running = False
        threading.Thread(target=run, daemon=True).start()
        return True

    def say(self, m):
        self.message = m
