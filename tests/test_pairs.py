import json, os, tempfile, time, unittest
from unittest import mock

from desk import history, livemode, market, pairs as P, watchdog as W

NOW = 1_800_000_000.0


def kraken():
    ap = {
        "XXBTZUSD": {"altname": "XBTUSD", "wsname": "XBT/USD", "status": "online", "ordermin": "0.00005", "costmin": "0.5"},
        "XETHZUSD": {"altname": "ETHUSD", "wsname": "ETH/USD", "status": "online", "ordermin": "0.002", "costmin": "0.5"},
        "XDGUSD": {"altname": "XDGUSD", "wsname": "XDG/USD", "status": "online", "ordermin": "40", "costmin": "0.5"},
        "SOLUSD": {"altname": "SOLUSD", "wsname": "SOL/USD", "status": "online", "ordermin": "0.02", "costmin": "0.5"},
        "USDTZUSD": {"altname": "USDTUSD", "wsname": "USDT/USD", "status": "online", "ordermin": "5", "costmin": "0.5"},
        "TINYUSD": {"altname": "TINYUSD", "wsname": "TINY/USD", "status": "online", "ordermin": "1", "costmin": "0.5"},
        "BIGUSD": {"altname": "BIGUSD", "wsname": "BIG/USD", "status": "online", "ordermin": "1", "costmin": "0.5"},
        "HALTUSD": {"altname": "HALTUSD", "wsname": "HALT/USD", "status": "reduce_only", "ordermin": "1"},
        "AAPLXUSD": {"altname": "AAPLxUSD", "wsname": "AAPLx/USD", "status": "online", "aclass_base": "tokenized_asset"},
        "SOLEUR": {"altname": "SOLEUR", "wsname": "SOL/EUR", "status": "online"},
        "XBTUSD.d": {"altname": "XBTUSD.d", "wsname": "XBT/USD", "status": "online"},
    }
    t = lambda price, vol: {"c": [str(price), "1"], "v": ["1", str(vol)], "p": ["1", str(price)]}
    tk = {"XXBTZUSD": t(90000, 2000), "XETHZUSD": t(3000, 30000), "XDGUSD": t(0.2, 5e7), "SOLUSD": t(150, 1e5),
          "USDTZUSD": t(1, 1e9), "TINYUSD": t(1, 1000), "BIGUSD": t(500, 1e5), "HALTUSD": t(1, 1e7)}
    return ap, tk


class CatalogTests(unittest.TestCase):
    def test_names_filters_and_reasons(self):
        rows = {r["pair"]: r for r in P.parse_catalog(*kraken(), per_trade=166.0)}
        self.assertEqual(rows["BTC/USD"]["code"], "XBTUSD")
        self.assertEqual(rows["DOGE/USD"]["code"], "XDGUSD")                 # Kraken's XDG is DOGE
        self.assertTrue(rows["DOGE/USD"]["eligible"] and rows["SOL/USD"]["eligible"])
        self.assertIn("stablecoin", rows["USDT/USD"]["reason"])
        self.assertIn("too thin", rows["TINY/USD"]["reason"])
        self.assertIn("minimum order", rows["BIG/USD"]["reason"])             # 500 USD minimum > 166 USD trade
        self.assertIn("reduce-only", rows["HALT/USD"]["reason"])
        self.assertNotIn("AAPLX/USD", rows); self.assertNotIn("SOL/EUR", rows)
        self.assertEqual(list(rows)[0], "USDT/USD")                           # busiest first


class FakeResearch:
    def __init__(self, d):
        self.db_path, self.market, self.job = os.path.join(d, "history.db"), None, history.Job()
        self.remote_factory = lambda: None


def fill(db, sym, days, end=NOW):
    db.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?)",
                   [(sym, int(end - h * 3600), 1, 1, 1, 1, 1) for h in range(0, days * 24, 24)])
    db.commit()


class DeskSideTests(unittest.TestCase):
    def setUp(self):
        self.known = dict(market.KNOWN)
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "data"); os.makedirs(self.data)
        self.ud = os.path.join(self.tmp.name, "ud"); os.makedirs(os.path.join(self.ud, "majvaj"))
        json.dump({"max_open_trades": 3, "exchange": {"pair_whitelist": list(P.DEFAULT)},
                   "freqai": {"identifier": "m1"}}, open(os.path.join(self.ud, "config-4h.json"), "w"))
        ap, tk = kraken()
        http = lambda m, url, *a, **k: (200, {"error": [], "result": ap if "AssetPairs" in url else tk})
        self.p = P.Pairs(self.data, self.ud, clock=lambda: NOW, http=http)
        self.money = {"bankroll": 1000}
        self.r = FakeResearch(self.data)

    def tearDown(self):
        market.KNOWN.clear(); market.KNOWN.update(self.known)
        self.tmp.cleanup()

    def wait(self):
        for _ in range(200):
            if not self.r.job.running:
                return
            time.sleep(0.01)

    def test_defaults_and_training_state(self):
        st = self.p.status()
        self.assertEqual(st["chosen"], list(P.DEFAULT)); self.assertFalse(st["pending"])
        self.assertEqual(st["untrained"], list(P.DEFAULT))                    # no model folders yet
        for c in ("BTC", "ETH", "SOL"):
            os.makedirs(os.path.join(self.ud, "models", "m1", "sub-train-%s_1700000000" % c))
        st = self.p.status(open_pairs=["ADA/USD"])
        self.assertEqual(st["untrained"], [])
        self.assertIn("closing only", st["rows"][-1]["state"])

    def test_limits(self):
        self.assertFalse(self.p.apply(["X%d/USD" % i for i in range(9)], self.money, self.r)["ok"])
        self.assertIn("can't be added", self.p.apply(["BIG/USD"], self.money, self.r)["message"])
        self.assertIn("not on Kraken", self.p.apply(["NOPE/USD"], self.money, self.r)["message"])
        self.assertEqual(self.p.apply(["SOL/USD"], self.money, self.r)["message"], "Nothing changed.")

    def test_apply_downloads_checks_history_and_records_request(self):
        db = history.connect(self.r.db_path)
        for s in ("BTC/USD", "ETH/USD", "SOL/USD"):
            fill(db, s, 500)
        db.close()
        def archive(db, syms, remote, say):
            for s in syms:
                fill(db, s, 500 if s == "DOGE/USD" else 30)
        with mock.patch.object(history, "import_archive", archive), mock.patch.object(history, "top_up", lambda *a, **k: None):
            r = self.p.apply(["DOGE/USD"], self.money, self.r)
            self.assertTrue(r["ok"], r); self.wait()
        self.assertTrue(self.r.job.ok, self.r.job.message)
        req = json.load(open(os.path.join(self.data, "pairs_request.json")))
        self.assertEqual(req["pairs"], ["BTC/USD", "ETH/USD", "DOGE/USD"])   # SOL removed, BTC/ETH kept
        self.assertEqual(P.selection(self.data)[1]["DOGE/USD"], "XDGUSD")
        self.assertTrue(self.p.status()["pending"])
        self.assertFalse(self.p.apply(["SOL/USD"], self.money, self.r)["ok"])  # one change at a time

    def test_coin_with_short_history_is_refused(self):
        db = history.connect(self.r.db_path)
        for s in ("BTC/USD", "ETH/USD", "SOL/USD"):
            fill(db, s, 500)
        db.close()
        with mock.patch.object(history, "import_archive", lambda db, syms, *a: [fill(db, s, 30) for s in syms]), \
                mock.patch.object(history, "top_up", lambda *a, **k: None):
            self.assertTrue(self.p.apply(["SOL/USD", "DOGE/USD"], self.money, self.r)["ok"]); self.wait()
        self.assertFalse(self.r.job.ok)
        self.assertIn("only 29 days", self.r.job.message)
        self.assertFalse(os.path.exists(os.path.join(self.data, "pairs_request.json")))

    def test_root_side_applies_in_background_then_switches(self):
        desk = self.data
        json.dump({"pairs": ["BTC/USD", "ETH/USD", "DOGE/USD"], "at": NOW}, open(os.path.join(desk, "pairs_request.json"), "w"))
        calls, units = [], {"majvaj-backtest": "active"}
        def run_out(cmd):
            calls.append(cmd)
            return units.get(cmd[2], "inactive") if cmd[:2] == ["systemctl", "is-active"] else ""
        self.assertEqual(P.tick(self.ud, "img", desk, "/h.db", NOW, run_out), "pairs waiting for a backtest to finish")
        self.assertFalse(os.path.exists(os.path.join(desk, "pairs_request.json")))   # consumed: no trigger loop
        units["majvaj-backtest"] = "inactive"
        self.assertEqual(P.tick(self.ud, "img", desk, "/h.db", NOW, run_out), "pairs data refresh started")
        self.assertTrue(any(c[0] == "systemd-run" and "convert_history.py" in c[-1] for c in calls))
        units[P.UNIT] = "active"
        self.assertEqual(P.tick(self.ud, "img", desk, "/h.db", NOW, run_out), "pairs data refresh running")
        units[P.UNIT] = "inactive"
        open(os.path.join(self.ud, "majvaj", "pairs_exit"), "w").write("0\n")
        with mock.patch.object(os, "chown", lambda *a: None):
            self.assertEqual(P.tick(self.ud, "img", desk, "/h.db", NOW, run_out), "pairs applied")
        self.assertEqual(P.applied(self.ud), ["BTC/USD", "ETH/USD", "DOGE/USD"])
        self.assertIn(["systemctl", "restart", "majvaj-freqtrade"], calls)

    def test_root_side_refuses_bad_lists_and_failed_copies(self):
        desk = self.data
        json.dump({"pairs": ["SOL/USD; rm -rf /"]}, open(os.path.join(desk, "pairs_request.json"), "w"))
        self.assertEqual(P.tick(self.ud, "img", desk, "/h.db", NOW, lambda c: ""), "pairs refused")
        json.dump({"pairs": ["SOL/USD", "DOGE/USD"]}, open(os.path.join(desk, "pairs_request.json"), "w"))
        self.assertEqual(P.tick(self.ud, "img", desk, "/h.db", NOW, lambda c: ""), "pairs refused")  # BTC/ETH missing
        json.dump({"status": "running", "pairs": list(P.DEFAULT)}, open(os.path.join(self.ud, "majvaj", "pairs_state.json"), "w"))
        self.assertEqual(P.tick(self.ud, "img", desk, "/h.db", NOW, lambda c: "inactive"), "pairs failed")
        self.assertFalse(os.path.exists(os.path.join(self.ud, "config.pairs.json")))      # old coins kept

    def test_live_switch_waits_for_training(self):
        self.assertTrue(any("still training: DOGE/USD" in b for b in livemode.blockers(True, {"bankroll": 5}, ["DOGE/USD"])))
        self.assertTrue(livemode.blockers(True, {"bankroll": 5}, [], True))


class WatchdogTests(unittest.TestCase):
    def test_restart_button_is_limited(self):
        with tempfile.TemporaryDirectory() as d:
            log, ran, notes = os.path.join(d, "log.json"), [], []
            for i in range(W.MANUAL_RESTARTS_PER_HOUR + 1):
                json.dump({"what": "bot", "at": NOW}, open(os.path.join(d, "restart_request.json"), "w"))
                res = W.manual_restart(d, NOW + i, ran.append, notes.append, log)
            self.assertEqual(len(ran), W.MANUAL_RESTARTS_PER_HOUR)
            self.assertEqual(res, "restart refused (limit)")
            json.dump({"what": "everything", "at": NOW}, open(os.path.join(d, "restart_request.json"), "w"))
            self.assertEqual(W.manual_restart(d, NOW, ran.append, notes.append, log), "restart request ignored")

    def test_folder_owner_is_put_back(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            fake = mock.Mock(pw_uid=999)
            with mock.patch.object(W.pwd, "getpwnam", return_value=fake), \
                    mock.patch.object(W.os, "chown", lambda *a: calls.append(a)), mock.patch.object(W.os, "chmod", lambda *a: None):
                self.assertTrue(W.heal_folder(d))
            self.assertEqual(calls, [(d, 999, 1000)])


if __name__ == "__main__":
    unittest.main()
