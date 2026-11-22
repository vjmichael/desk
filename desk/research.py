"""Glue between the web page and the history/backtest modules."""
import json
import os

from . import backtest, history

SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD")


class Research:
    def __init__(self, data_dir, remote_factory=None, market=None):
        self.dir = data_dir
        self.db_path = os.path.join(data_dir, "history.db")
        self.result_path = os.path.join(data_dir, "backtest.json")
        self.job = history.Job()
        self.remote_factory = remote_factory or history.RemoteParts
        self.market = market

    def status(self):
        db = history.connect(self.db_path)
        try:
            pairs = history.summary(db, SYMBOLS)
        finally:
            db.close()
        result = None
        try:
            with open(self.result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
        except (OSError, ValueError):
            pass
        j = self.job
        return {"job": {"running": j.running, "message": j.message, "ok": j.ok, "kind": j.kind},
                "pairs": pairs, "result": result}

    def download(self):
        def work(say):
            db = history.connect(self.db_path)
            try:
                say("connecting to Kraken's history archive...")
                history.import_archive(db, SYMBOLS, self.remote_factory(), say)
                say("adding the latest candles...")
                history.top_up(db, SYMBOLS, self.market, say)
                say("done")
            finally:
                db.close()
        return self.job.start("download", work)

    def backtest(self, years=4):
        def work(say):
            db = history.connect(self.db_path)
            try:
                series = {s: history.load(db, s) for s in SYMBOLS}
            finally:
                db.close()
            if any(len(c) < 500 for c in series.values()):
                raise history.HistoryError("No history yet. Press Download history first.")
            res = backtest.experiment(series, years, say)
            with open(self.result_path, "w", encoding="utf-8") as f:
                json.dump(res, f)
            say("done")
        return self.job.start("backtest", work)
