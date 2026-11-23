import json, os, tempfile, unittest, zipfile
from desk import freqailab as F

NOW = 1_800_000_000.0


def fake_result(d, name, trades=4, profit=0.031, market=-0.12):
    os.makedirs(d, exist_ok=True)
    st = {"total_trades": trades, "wins": 3, "losses": 1, "profit_total": profit, "profit_total_abs": 31.0,
          "market_change": market, "max_drawdown_account": 0.021, "winrate": 0.75, "freqai_identifier": "m-backtest",
          "backtest_start": "2026-05-01 00:00:00", "backtest_end": "2026-11-01 00:00:00", "starting_balance": 1000,
          "final_balance": 1031.0}
    with zipfile.ZipFile(os.path.join(d, name), "w") as z:
        z.writestr(name.replace(".zip", ".json"), json.dumps({"strategy": {"MajVajFreqAI4h": st}, "strategy_comparison": []}))
        z.writestr(name.replace(".zip", "_config.json"), "{}")


class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ud = self.tmp.name
        os.makedirs(os.path.join(self.ud, "majvaj"))
        json.dump({"timeframe": "4h", "exchange": {"pair_whitelist": ["BTC/USD"]},
                   "freqai": {"identifier": "majvaj-4h-abc", "live_retrain_hours": 24, "train_period_days": 365,
                              "feature_parameters": {"label_period_candles": 18, "include_timeframes": ["4h", "1d"]}}},
                  open(os.path.join(self.ud, "config-4h.json"), "w"))
        self.lab = F.Lab(self.ud, clock=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_model_and_results_are_read_from_freqtrade_files(self):
        sub = os.path.join(self.ud, "models", "majvaj-4h-abc", "sub-train-BTC_1")
        os.makedirs(sub)
        fake_result(os.path.join(self.ud, "backtest_results"), "backtest-result-2026-11-22_10-00-00.zip")
        st = self.lab.status()
        self.assertEqual(st["model"]["identifier"], "majvaj-4h-abc"); self.assertIsNotNone(st["model"]["last_trained"])
        r = st["results"][0]
        self.assertEqual((r["trades"], r["profit_pct"], r["market_pct"], r["winrate_pct"]), (4, 3.1, -12.0, 75.0))
        self.assertEqual(st["backtest"]["status"], "idle")

    def test_request_once_then_watchdog_starts_and_finishes_it(self):
        self.assertFalse(self.lab.request("forever")["ok"])
        self.assertTrue(self.lab.request("6m")["ok"])
        self.assertFalse(self.lab.request("3m")["ok"])                     # one at a time
        calls, active = [], ["active"]
        def run_out(cmd):
            calls.append(cmd)
            if cmd[:2] == ["systemctl", "is-active"]:
                return active[0] if cmd[2] == F.UNIT else "inactive"
            return ""
        orig = os.chown
        os.chown = lambda *a: None
        try:
            self.assertEqual(F.tick(self.ud, "img@sha256:x", "/h.db", NOW, run_out), "backtest started")
        finally:
            os.chown = orig
        start = [c for c in calls if c[0] == "systemd-run"][0]
        script = start[-1]
        self.assertIn("--timerange 20260717-20270115 ", script)              # 182 days before NOW, ends today
        self.assertIn("--cpus 1", script); self.assertIn("backtest_override.json", script)
        self.assertEqual(json.load(open(os.path.join(self.ud, "majvaj", "backtest_override.json")))["freqai"]["identifier"],
                         "majvaj-4h-abc-backtest")                         # never the live model's folder
        self.assertEqual(self.lab.status()["backtest"]["status"], "running")
        self.assertEqual(F.tick(self.ud, "img", "/h.db", NOW + 120, run_out), "backtest running")
        active[0] = "inactive"
        open(os.path.join(self.ud, "majvaj", "backtest_exit"), "w").write("0\n")
        notes = []
        self.assertEqual(F.tick(self.ud, "img", "/h.db", NOW + 900, run_out, notes.append), "backtest done")
        self.assertEqual(self.lab.status()["backtest"]["status"], "done"); self.assertTrue(notes)
        self.assertIsNone(F.tick(self.ud, "img", "/h.db", NOW + 1000, run_out))

    def test_backtest_waits_for_a_coin_change(self):
        self.lab.request("3m")
        self.assertEqual(F.tick(self.ud, "img", "/h.db", NOW, lambda c: "active" if c[-1] == "majvaj-pairs" else ""),
                         "backtest waiting for a coin change to finish")
        self.assertTrue(os.path.exists(os.path.join(self.ud, "majvaj", "backtest_request.json")))

    def test_failed_run_is_reported(self):
        json.dump({"status": "running", "label": "Last 3 months"}, open(os.path.join(self.ud, "majvaj", "backtest_state.json"), "w"))
        self.assertEqual(F.tick(self.ud, "img", "/h.db", NOW, lambda c: "inactive"), "backtest failed")
        self.assertIn("journalctl", self.lab.status()["backtest"]["message"])


if __name__ == "__main__":
    unittest.main()
