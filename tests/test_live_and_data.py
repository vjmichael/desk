import json, os, tempfile, time, unittest
from datetime import datetime, timezone
from desk import econdata as E, livemode as L

NOW = 1_800_000_000.0


class DataTests(unittest.TestCase):
    def test_parsers(self):
        f = E.parse_fng({"data": [{"value": str(i), "timestamp": str(1_700_000_000 + i * 86400)} for i in range(20)]})
        self.assertEqual(len(f), 20); self.assertEqual(f[0][2], 1_700_000_000 + 3600)
        with self.assertRaises(E.DataError):
            E.parse_fng({"data": []})
        html = ('<h4><a id="1">2026 FOMC Meetings</a></h4><div class="fomc-meeting__month x"><strong>January</strong></div>'
                '<div class="fomc-meeting__date x">27-28</div><div class="fomc-meeting__month x"><strong>Apr/May</strong></div>'
                '<div class="fomc-meeting__date x">30-1</div><div class="fomc-meeting__month x"><strong>March</strong></div>'
                '<div class="fomc-meeting__date x">5 (notation vote)</div>')
        ev = E.parse_fomc(html)
        self.assertEqual([datetime.fromtimestamp(a, timezone.utc).strftime("%m-%d %H") for _, a in ev], ["01-28 19", "05-01 18"])
        rd = E.parse_release_dates({"release_dates": [{"date": "2026-12-10"}]}, "CPI")
        self.assertEqual(datetime.fromtimestamp(rd[0][1], timezone.utc).strftime("%H:%M"), "13:30")
        fr = E.parse_first_releases({"observations": [{"date": "2026-10-01", "value": "4.1", "realtime_start": "2026-11-06"},
                                                      {"date": "2026-09-01", "value": ".", "realtime_start": "2026-10-03"}]})
        self.assertEqual(len(fr), 1); self.assertEqual(fr[0][:2], ("2026-10-01", 4.1))

    def test_cpi_yoy_uses_first_releases(self):
        rows = E.cpi_yoy([("2025-10-01", 100.0, 1.0), ("2026-10-01", 103.0, 2.0)])
        self.assertEqual(rows, [("cpi_yoy", 2.0, 3.0)])

    def test_refresh_isolates_failures_and_is_point_in_time(self):
        with tempfile.TemporaryDirectory() as d:
            fng = {"data": [{"value": "40", "timestamp": str(1_700_000_000 + i * 86400)} for i in range(15)]}
            def http(m, u, h=None, b=None, t=20):
                return (200, fng) if "alternative" in u else (500, {})
            def raw(m, u, h=None, t=60):
                return 503, {}, b""
            e = E.EconData(d, os.path.join(d, "out"), http=http, raw=raw, clock=lambda: NOW)
            rep = e.refresh()
            self.assertTrue(rep["fear_greed"].startswith("ok")); self.assertTrue(rep["fomc"].startswith("failed"))
            self.assertIn("no FRED API key", rep["fred"])
            self.assertTrue(os.path.exists(os.path.join(d, "out", "series.csv")))
            fng["data"][0]["value"] = "99"   # a revision arrives later with a later available_at: both kept
            e._obs("alternative.me", "fear_greed", [(E.parse_fng(fng)[0][0], 99.0, NOW)])
            n = e.db.execute("SELECT COUNT(*) FROM observations WHERE series='fear_greed'").fetchone()[0]
            self.assertEqual(n, 16)


class LiveSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.meta = {}
        self.f = {k: os.path.join(d, k + ".json") for k in ("state", "wd", "secret", "modecfg", "desk")}
        self.desk = L.ModeDesk(d, self.m, clock=lambda: NOW, state_file=self.f["state"], watchdog_file=self.f["wd"])

    def tearDown(self):
        self.tmp.cleanup()

    def m(self, k, v=None):
        if v is None:
            return self.meta.get(k)
        self.meta[k] = v

    def test_advice_never_blocks_only_key_and_bankroll_do(self):
        c = self.desk.checks(0, [], {"bankroll": 500})
        self.assertFalse(all(ok for _, ok, _ in c))                      # day one: advice not met
        self.assertEqual(L.blockers(True, {"bankroll": 500}), [])          # ...but nothing blocks
        self.assertEqual(len(L.blockers(False, {"bankroll": 0})), 2)
        r = self.desk.request("live", L.PHRASE_LIVE, True, L.blockers(False, {"bankroll": 500}), {})
        self.assertFalse(r["ok"]); self.assertIn("trade-enabled", r["message"])
        self.assertFalse(os.path.exists(self.desk.request_file))

    def test_live_needs_exact_phrase_and_ack(self):
        for phrase, ack in (("enable live", True), (L.PHRASE_LIVE, False), ("", True), (None, True)):
            self.assertFalse(self.desk.request("live", phrase, ack, [], {})["ok"])
        self.assertFalse(os.path.exists(self.desk.request_file))
        r = self.desk.request("live", "enable live trading", True, [], {"bankroll": 500}, ["Paper trading for at least 7 days"])
        self.assertTrue(r["ok"]); self.assertEqual(json.load(open(self.desk.request_file))["to"], "live")
        audit = [json.loads(x) for x in open(self.desk.audit_file)]
        self.assertEqual(audit[-1]["decision"], "live_confirmed")
        self.assertEqual(audit[-1]["advice_not_met"], ["Paper trading for at least 7 days"])

    def test_trade_key_from_keys_page_or_console_file(self):
        self.assertIsNone(L.trade_key(self.f["desk"], self.f["secret"]))
        json.dump({"key": "K" * 30, "secret": "S" * 60}, open(self.f["secret"], "w"))
        self.assertEqual(L.trade_key(self.f["desk"], self.f["secret"])["key"], "K" * 30)
        json.dump({"kraken_key": "query-only", "kraken_trade_key": "T" * 30, "kraken_trade_secret": "U" * 60}, open(self.f["desk"], "w"))
        self.assertEqual(L.trade_key(self.f["desk"], self.f["secret"])["key"], "T" * 30)   # never the query key

    def test_applier_live_then_exit_only_then_paper(self):
        class Ft:
            def __init__(self): self.paused, self.open = 0, [{"pair": "BTC/USD"}]
            def pause(self): self.paused += 1
            def status(self): return self.open
        ft, runs, notes = Ft(), [], []
        req = os.path.join(self.tmp.name, "req.json")
        kw = dict(run=runs.append, notify=notes.append, state_file=self.f["state"], mode_config=self.f["modecfg"],
                  live_secret=self.f["secret"], desk_secrets=self.f["desk"])
        orig = L._write
        L._write = lambda path, obj, mode=0o640, owner=None: orig(path, obj, mode, None)  # no chown in tests
        try:
            json.dump({"to": "live", "at": NOW - 10}, open(req, "w"))
            self.assertEqual(L.apply_pending(ft, req, NOW, **kw), "live request refused")   # no trade key
            json.dump({"kraken_trade_key": "T" * 30, "kraken_trade_secret": "U" * 60}, open(self.f["desk"], "w"))
            json.dump({"to": "live", "at": NOW - 5000}, open(req, "w"))
            self.assertEqual(L.apply_pending(ft, req, NOW, **kw), "live request refused")   # too old
            json.dump({"to": "live", "at": NOW - 10, "scope": {"bankroll": 500}}, open(req, "w"))
            self.assertEqual(L.apply_pending(ft, req, NOW, **kw), "switched to live")
            cfg = json.load(open(self.f["modecfg"]))
            self.assertFalse(cfg["dry_run"]); self.assertEqual(cfg["exchange"]["key"], "T" * 30)
            json.dump({"to": "paper", "at": NOW}, open(req, "w"))
            self.assertIn("still open", L.apply_pending(ft, req, NOW, **kw))
            self.assertEqual(json.load(open(self.f["state"]))["mode"], "live_exit_only")
            self.assertGreaterEqual(ft.paused, 1)
            ft.open = []
            self.assertEqual(L.apply_pending(ft, req, NOW + 600, **kw), "returned to paper")
            self.assertTrue(json.load(open(self.f["modecfg"]))["dry_run"])
            self.assertNotIn("exchange", json.load(open(self.f["modecfg"])))   # trade key removed from the bot
        finally:
            L._write = orig
        self.assertEqual(runs.count(["systemctl", "restart", "majvaj-freqtrade"]), 2)


if __name__ == "__main__":
    unittest.main()
