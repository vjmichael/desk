"""Maj Vaj outside data, stored point-in-time.

Every value is kept with the moment it became public ("available_at"), and revisions are
added as new rows, never overwriting old ones. The bot and its backtests only use a value
after its available_at, so the model never learns from information that did not exist yet.

Sources
- Crypto Fear & Greed index (alternative.me), daily since 2018. No key.
- FOMC meeting calendar (federalreserve.gov). Decision statements at 14:00 New York time. No key.
- FRED / ALFRED (St. Louis Fed), needs a free API key:
    release calendar for CPI, the jobs report and PPI (08:30 New York time), past and scheduled;
    first-release values of CPI, unemployment rate and the fed funds rate, dated by release day.

Output for the bot (in its majvaj/ folder):
- events.json: high-impact events (FOMC, CPI, jobs, PPI) with times, for the calendar gate.
- series.csv: series,available_at,value for the model's features."""
import csv
import io
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import net

NY = ZoneInfo("America/New_York")
FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FRED = "https://api.stlouisfed.org/fred"
RELEASES = {10: ("CPI", "Consumer Price Index"), 50: ("JOBS", "Employment Situation (jobs report)"),
            46: ("PPI", "Producer Price Index")}
SERIES = {"CPIAUCNS": "cpi_index", "UNRATE": "unemployment_rate", "FEDFUNDS": "fed_funds_rate"}
MONTHS = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August",
                                       "September", "October", "November", "December"), 1)}
UA = {"User-Agent": "Mozilla/5.0 (compatible; MajVaj/0.8; personal research)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations(source TEXT, series TEXT, period TEXT, value REAL, available_at REAL,
  retrieved_at REAL, PRIMARY KEY(source, series, period, available_at));
CREATE TABLE IF NOT EXISTS events(kind TEXT, name TEXT, at REAL, source TEXT, retrieved_at REAL,
  PRIMARY KEY(kind, at));
CREATE TABLE IF NOT EXISTS fetches(source TEXT PRIMARY KEY, ok INTEGER, at REAL, rows INTEGER, message TEXT);
"""


class DataError(Exception):
    pass


# ------------------------------------------------------------------ parsers (pure, tested)
def parse_fng(data):
    """alternative.me JSON -> [(period 'YYYY-MM-DD', value, available_at)]. A day's value is used
    only one hour after that UTC day starts, to stay on the safe side of its publication."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise DataError("Fear & Greed answer not understood")
    out = []
    for r in data["data"]:
        try:
            ts, v = int(r["timestamp"]), float(r["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= v <= 100:
            out.append((datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"), v, ts + 3600))
    if len(out) < 10:
        raise DataError("Fear & Greed returned too little data")
    return out


def parse_fomc(html):
    """Fed calendar page -> [(name, at)] for each meeting's decision, 14:00 New York on its last day.
    Notation votes and minutes are skipped."""
    out = []
    for ym in re.finditer(r"(\d{4}) FOMC Meetings", html):
        year = int(ym.group(1))
        end = html.find("FOMC Meetings", ym.end())
        block = html[ym.end(): end if end > 0 else len(html)]
        for m in re.finditer(r'fomc-meeting__month[^>]*>\s*<strong>([A-Za-z/]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<',
                             block, re.S):
            month_txt, date_txt = m.group(1), m.group(2).strip()
            if "notation" in date_txt.lower() or "unscheduled" in date_txt.lower():
                continue
            last_month = month_txt.split("/")[-1]
            days = re.findall(r"\d+", date_txt)
            if last_month not in MONTHS or not days:
                continue
            day = int(days[-1])
            try:
                at = datetime(year, MONTHS[last_month], day, 14, 0, tzinfo=NY).timestamp()
            except ValueError:
                continue
            out.append(("FOMC decision %s %d" % (last_month, year), at))
    if not out:
        raise DataError("no FOMC meetings found on the Fed calendar page")
    return sorted(set(out), key=lambda x: x[1])


def parse_release_dates(data, kind):
    """FRED release/dates JSON -> [(name, at)] at 08:30 New York on each date."""
    out = []
    for r in (data or {}).get("release_dates") or []:
        try:
            y, mo, d = (int(x) for x in r["date"].split("-"))
        except (KeyError, ValueError):
            continue
        out.append(("%s release" % kind, datetime(y, mo, d, 8, 30, tzinfo=NY).timestamp()))
    return out


def parse_first_releases(data):
    """FRED observations with output_type=4 (initial release only) -> [(period, value, available_at)].
    available_at = 09:00 New York on the release day (data comes out at 08:30)."""
    out = []
    for r in (data or {}).get("observations") or []:
        if r.get("value") in (None, ".", ""):
            continue
        try:
            y, mo, d = (int(x) for x in r["realtime_start"].split("-"))
            out.append((r["date"], float(r["value"]), datetime(y, mo, d, 9, 0, tzinfo=NY).timestamp()))
        except (KeyError, ValueError):
            continue
    return out


# ------------------------------------------------------------------ store
class EconData:
    def __init__(self, data_dir, out_dir, get_fred_key=lambda: "", http=None, raw=None, clock=time.time):
        self.db = sqlite3.connect(os.path.join(data_dir, "econ.db"), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.RLock()
        self.out_dir, self.get_fred_key, self.clock = out_dir, get_fred_key, clock
        self.http, self.raw = http or net.http_json, raw or net.http_raw
        self.stop_event, self.thread = threading.Event(), None

    def _log(self, source, ok, rows, message):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO fetches VALUES(?,?,?,?,?)", (source, int(ok), self.clock(), rows, message[:300]))
            self.db.commit()

    def _obs(self, source, series, rows):
        now = self.clock()
        with self.lock:
            before = self.db.total_changes
            self.db.executemany("INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?)",
                                [(source, series, p, v, a, now) for p, v, a in rows])
            self.db.commit()
            return self.db.total_changes - before

    def _events(self, kind, source, rows):
        now = self.clock()
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)", [(kind, n, a, source, now) for n, a in rows])
            self.db.commit()

    def fetch_fng(self):
        status, data = self.http("GET", FNG_URL, UA, None, 60)
        if status != 200:
            raise DataError("Fear & Greed answered HTTP %s" % status)
        return self._obs("alternative.me", "fear_greed", parse_fng(data))

    def fetch_fomc(self):
        status, hdr, body = self.raw("GET", FOMC_URL, UA, 60)
        if status != 200:
            raise DataError("Fed calendar answered HTTP %s" % status)
        rows = parse_fomc(body.decode("utf-8", "replace"))
        self._events("FOMC", "federalreserve.gov", rows)
        return len(rows)

    def fetch_fred(self):
        key = self.get_fred_key()
        if not key:
            raise DataError("no FRED API key saved (free at fredaccount.stlouisfed.org)")
        n = 0
        for rid, (kind, _) in RELEASES.items():
            status, data = self.http("GET", "%s/release/dates?release_id=%d&api_key=%s&file_type=json&realtime_start=2015-01-01"
                                     "&include_release_dates_with_no_data=true&limit=1000" % (FRED, rid, key), UA, None, 60)
            if status != 200:
                raise DataError("FRED release calendar answered HTTP %s (check the key)" % status)
            rows = parse_release_dates(data, kind)
            self._events(kind, "fred", rows)
            n += len(rows)
        for sid, name in SERIES.items():
            status, data = self.http("GET", "%s/series/observations?series_id=%s&api_key=%s&file_type=json&output_type=4"
                                     "&realtime_start=2014-01-01&realtime_end=9999-12-31&observation_start=2013-01-01"
                                     % (FRED, sid, key), UA, None, 60)
            if status != 200:
                raise DataError("FRED %s answered HTTP %s" % (sid, status))
            n += self._obs("fred", name, parse_first_releases(data))
        return n

    def refresh(self):
        """Fetch every source; one failing never stops the others. Returns {source: message}."""
        report = {}
        for source, fn in (("fear_greed", self.fetch_fng), ("fomc", self.fetch_fomc), ("fred", self.fetch_fred)):
            try:
                n = fn()
                self._log(source, True, n, "ok")
                report[source] = "ok (%d new)" % n
            except (DataError, net.NetError) as e:
                self._log(source, False, 0, str(e))
                report[source] = "failed: %s" % e
            except Exception as e:  # unexpected shapes must not kill the loop
                self._log(source, False, 0, "unexpected %s" % type(e).__name__)
                report[source] = "failed: unexpected %s" % type(e).__name__
        self.export()
        return report

    # ------------------------------------------------------------------ outputs for the bot
    def export(self):
        os.makedirs(self.out_dir, exist_ok=True)
        with self.lock:
            ev = self.db.execute("SELECT kind, name, at FROM events ORDER BY at").fetchall()
            obs = self.db.execute("SELECT series, available_at, value FROM observations ORDER BY series, available_at").fetchall()
        _atomic(os.path.join(self.out_dir, "events.json"),
                json.dumps({"made": self.clock(), "events": [{"kind": k, "name": n, "at": a} for k, n, a in ev]}))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["series", "available_at", "value"])
        w.writerows(obs)
        w.writerows(cpi_yoy(self._first_releases("cpi_index")))
        _atomic(os.path.join(self.out_dir, "series.csv"), buf.getvalue())

    def _first_releases(self, series):
        with self.lock:
            return self.db.execute("SELECT period, value, MIN(available_at) FROM observations WHERE series=? "
                                   "GROUP BY period ORDER BY period", (series,)).fetchall()

    def status(self):
        with self.lock:
            f = [dict(zip(("source", "ok", "at", "rows", "message"), r)) for r in self.db.execute("SELECT * FROM fetches")]
            counts = dict(self.db.execute("SELECT series, COUNT(*) FROM observations GROUP BY series").fetchall())
            nxt = self.db.execute("SELECT kind, name, at FROM events WHERE at>? ORDER BY at LIMIT 5", (self.clock(),)).fetchall()
        return {"fetches": f, "counts": counts, "upcoming": [{"kind": k, "name": n, "at": a} for k, n, a in nxt]}

    # ------------------------------------------------------------------ daily loop
    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True, name="econdata")
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            with self.lock:
                last = self.db.execute("SELECT MAX(at) FROM fetches").fetchone()[0] or 0
            if self.clock() - last >= 12 * 3600:
                try:
                    self.refresh()
                except Exception:
                    pass
            self.stop_event.wait(600)


def cpi_yoy(first):
    """[(period, value, available_at)] of CPI first releases -> rows (cpi_yoy, available_at, % change on a
    year earlier). Uses only numbers that were public when the newer one came out."""
    by_period = {p: v for p, v, _ in first}
    out = []
    for p, v, a in first:
        y, m, d = p.split("-")
        prev = by_period.get("%04d-%s-%s" % (int(y) - 1, m, d))
        if prev:
            out.append(("cpi_yoy", a, round((v / prev - 1) * 100, 3)))
    return out


def _atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
