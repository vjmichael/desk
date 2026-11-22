import http.server, io, json, os, random, tempfile, threading, unittest, zipfile
from desk import history, backtest
from desk.market import Candle
from desk.research import Research
from desk.server import DeskServer, serve_in_thread
from desk.store import Store
from tests.test_server import call


def make_zip(rows_by_name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, rows in rows_by_name.items():
            z.writestr("Kraken_OHLCVT/" + name, "\n".join(",".join(str(x) for x in r) for r in rows))
    return buf.getvalue()


def candles(n, seed=1, start=1_600_000_000):
    random.seed(seed)
    p, rows = 100.0, []
    for i in range(n):
        o = p
        p *= 1 + random.gauss(0.0001, 0.006)
        rows.append((start + i * 3600, o, max(o, p) * 1.002, min(o, p) * 0.998, p, 10.0, 5))
    return rows


class RangeServer(http.server.BaseHTTPRequestHandler):
    parts = {}

    def log_message(self, *a):
        pass

    def _do(self, head):
        data = self.parts.get(self.path.rsplit("/", 1)[-1])
        if data is None:
            self.send_response(404); self.end_headers(); return
        rng = self.headers.get("Range")
        if rng:
            a, b = rng[6:].split("-"); a, b = int(a), int(b)
            body = data[a:b + 1]
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (a, a + len(body) - 1, len(data)))
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) if head else len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self):
        self._do(False)

    def do_HEAD(self):
        self._do(True)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        raw = make_zip({"XBTUSD_60.csv": candles(900, 1), "ETHUSD_60.csv": candles(900, 2), "SOLUSD_60.csv": candles(900, 3),
                        "XBTUSD_1.csv": candles(10, 9)})
        third = len(raw) // 3
        RangeServer.parts = {"p0": raw[:third], "p1": raw[third:2 * third], "p2": raw[2 * third:]}
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeServer)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d/" % self.srv.server_address[1]
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); self.tmp.cleanup()

    def remote(self, **kw):
        return history.RemoteParts(base=self.base, parts=("p0", "p1", "p2"), **kw)

    def test_import_reads_only_the_hourly_files(self):
        db = history.connect(os.path.join(self.tmp.name, "h.db"))
        out = history.import_archive(db, ("BTC/USD", "ETH/USD", "SOL/USD"), self.remote())
        self.assertEqual(out, {"BTC/USD": 900, "ETH/USD": 900, "SOL/USD": 900})
        self.assertEqual(len(history.load(db, "BTC/USD")), 900)

    def test_budget_stops_the_download(self):
        db = history.connect(os.path.join(self.tmp.name, "h.db"))
        with self.assertRaises(history.HistoryError):
            history.import_archive(db, ("BTC/USD",), self.remote(budget=1))

    def test_missing_member_gives_clear_error(self):
        db = history.connect(os.path.join(self.tmp.name, "h.db"))
        with self.assertRaises(history.HistoryError) as cm:
            history.import_archive(db, ("DOGE/USD",), self.remote())
        self.assertIn("could not find", str(cm.exception))

    def test_top_up_skips_forming_candle(self):
        db = history.connect(os.path.join(self.tmp.name, "h.db"))
        db.execute("INSERT INTO candles VALUES('BTC/USD',1000,1,1,1,1,1)"); db.commit()

        class M:
            def candles(self, sym, interval, since=None):
                return [Candle(t, 1, 1, 1, 1, 1) for t in (1000, 4600, 8200)]
        history.top_up(db, ("BTC/USD",), M())
        self.assertEqual([c.ts for c in history.load(db, "BTC/USD")], [1000, 4600])


class BacktestTests(unittest.TestCase):
    def series(self, n=4000):
        return {s: [Candle(r[0], r[1], r[2], r[3], r[4], r[5]) for r in candles(n, i)] for i, s in
                enumerate(("BTC/USD", "ETH/USD", "SOL/USD"), 1)}

    def test_experiment_runs_and_is_repeatable(self):
        a = backtest.experiment(self.series(), years=10)
        b = backtest.experiment(self.series(), years=10)
        self.assertEqual(a["best"], b["best"])
        self.assertEqual(a["test"]["return_pct"], b["test"]["return_pct"])
        self.assertEqual(len(a["grid"]), len(backtest.GRID))
        self.assertGreater(a["test"]["from"], a["train"]["to"] - 1)

    def test_needs_history(self):
        with self.assertRaises(ValueError):
            backtest.experiment({"BTC/USD": [], "ETH/USD": [], "SOL/USD": []})

    def test_never_spends_more_than_cash(self):
        r = backtest.run(self.series(1500), 2.0, 4.5)
        self.assertGreaterEqual(r["return_pct"], -100)


class ResearchEndpointTests(unittest.TestCase):
    def test_download_without_network_reports_problem_and_status_works(self):
        tmp = tempfile.TemporaryDirectory()
        store = Store(tmp.name)

        def factory(d):
            def bad():
                raise history.HistoryError("Kraken's archive is not reachable right now (HTTP 0)")
            return Research(d, remote_factory=bad)
        server = DeskServer(store, port=0, research_factory=factory)
        serve_in_thread(server)
        try:
            code, body = call(server, "/api/history/download", {})
            self.assertEqual(code, 200)
            for _ in range(50):
                _, b = call(server, "/api/history/status")
                st = json.loads(b)
                if not st["job"]["running"]:
                    break
                threading.Event().wait(0.1)
            self.assertIs(st["job"]["ok"], False)
            self.assertIn("not reachable", st["job"]["message"])
            code, _ = call(server, "/api/history/status", token=False)
            self.assertEqual(code, 403)
            _, b = call(server, "/api/history/backtest", {})
            for _ in range(50):
                _, b = call(server, "/api/history/status")
                st = json.loads(b)
                if not st["job"]["running"]:
                    break
                threading.Event().wait(0.1)
            self.assertIn("No history yet", st["job"]["message"])
        finally:
            server.shutdown(); server.server_close(); tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
