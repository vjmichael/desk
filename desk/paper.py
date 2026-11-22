"""The paper account and ledger, stored in a local SQLite file."""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS positions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, qty REAL, entry REAL, stop REAL, target REAL,
  opened_at REAL, expires_at REAL, entry_fee REAL, last_checked REAL, status TEXT,
  exit_price REAL, exit_reason TEXT, closed_at REAL, exit_fee REAL, pnl REAL,
  proposal_id TEXT, rationale TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, proposal_id TEXT, symbol TEXT,
  approved INTEGER, reason TEXT, rationale TEXT);
CREATE TABLE IF NOT EXISTS equity(ts REAL, cash REAL, equity REAL);
CREATE TABLE IF NOT EXISTS scout_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, called INTEGER, ok INTEGER, n_proposals INTEGER, note TEXT);
"""


class PaperAccount:
    def __init__(self, path, start_equity=1000.0):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            if self.get_meta("start_equity") is None:
                self._init_meta(start_equity)

    def _init_meta(self, equity):
        self.set_meta("start_equity", repr(float(equity)))
        self.set_meta("cash", repr(float(equity)))

    def get_meta(self, k, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return default if r is None else r["v"]

    def set_meta(self, k, v):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, str(v)))
            self.db.commit()

    @property
    def cash(self):
        return float(self.get_meta("cash", 0))

    @property
    def start_equity(self):
        return float(self.get_meta("start_equity", 0))

    def reset(self, equity):
        with self.lock:
            for t in ("positions", "decisions", "equity", "scout_runs", "meta"):
                self.db.execute("DELETE FROM " + t)
            self.db.commit()
            self._init_meta(equity)

    # ---- positions
    def open_position(self, proposal_id, symbol, qty, fill, stop, target, opened_at, expires_at, fee, rationale=""):
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO positions(symbol,qty,entry,stop,target,opened_at,expires_at,entry_fee,"
                "last_checked,status,proposal_id,rationale) VALUES(?,?,?,?,?,?,?,?,?,'open',?,?)",
                (symbol, qty, fill, stop, target, opened_at, expires_at, fee, opened_at, proposal_id, rationale))
            self.set_meta("cash", repr(self.cash - qty * fill - fee))
            return cur.lastrowid

    def open_positions(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM positions WHERE status='open' ORDER BY id")]

    def closed_positions(self, limit=20):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM positions WHERE status='closed' ORDER BY id DESC LIMIT ?", (limit,))]

    def set_last_checked(self, pos_id, ts):
        with self.lock:
            self.db.execute("UPDATE positions SET last_checked=? WHERE id=?", (ts, pos_id))
            self.db.commit()

    def close_position(self, pos_id, price, reason, fee, now):
        with self.lock:
            p = dict(self.db.execute("SELECT * FROM positions WHERE id=? AND status='open'", (pos_id,)).fetchone())
            pnl = (price - p["entry"]) * p["qty"] - p["entry_fee"] - fee
            self.db.execute(
                "UPDATE positions SET status='closed',exit_price=?,exit_reason=?,closed_at=?,exit_fee=?,pnl=? WHERE id=?",
                (price, reason, now, fee, pnl, pos_id))
            self.set_meta("cash", repr(self.cash + p["qty"] * price - fee))
            p.update(exit_price=price, exit_reason=reason, closed_at=now, exit_fee=fee, pnl=pnl)
            return p

    def realized_today(self, now):
        with self.lock:
            r = self.db.execute("SELECT COALESCE(SUM(pnl),0) s FROM positions WHERE status='closed' AND closed_at>=?",
                                (now - now % 86400,)).fetchone()
        return float(r["s"])

    def equity(self, bids):
        return self.cash + sum(p["qty"] * bids.get(p["symbol"], p["entry"]) for p in self.open_positions())

    # ---- logs
    def log_decision(self, ts, proposal, decision):
        with self.lock:
            self.db.execute("INSERT INTO decisions(ts,proposal_id,symbol,approved,reason,rationale) VALUES(?,?,?,?,?,?)",
                            (ts, proposal.id, proposal.symbol, int(decision.approved), decision.reason, proposal.rationale))
            self.db.commit()

    def recent_decisions(self, limit=30):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))]

    def snapshot(self, ts, equity):
        with self.lock:
            self.db.execute("INSERT INTO equity(ts,cash,equity) VALUES(?,?,?)", (ts, self.cash, equity))
            self.db.commit()

    def log_scout(self, ts, called, ok, n, note):
        with self.lock:
            self.db.execute("INSERT INTO scout_runs(ts,called,ok,n_proposals,note) VALUES(?,?,?,?,?)",
                            (ts, int(called), int(ok), n, (note or "")[:300]))
            self.db.commit()

    def last_scout(self):
        with self.lock:
            r = self.db.execute("SELECT * FROM scout_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None

    def scout_calls_today(self, now):
        with self.lock:
            r = self.db.execute("SELECT COUNT(*) c FROM scout_runs WHERE called=1 AND ts>=?", (now - now % 86400,)).fetchone()
        return int(r["c"])
