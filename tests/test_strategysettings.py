import json, os, tempfile, unittest
from unittest import mock

from desk import freqailab as F, strategysettings as S, majvaj as MV
from tests.test_freqailab import fake_result, NOW


class SettingsTests(unittest.TestCase):
    def test_defaults_match_the_built_in_strategy(self):
        s, err = S.clean({})
        self.assertEqual(err, ""); self.assertEqual(s, S.DEFAULTS)
        lay = S.layer(s, "majvaj-4h-abc")
        self.assertEqual(lay["stoploss"], -0.08)
        self.assertEqual(lay["minimal_roi"], {"0": 0.12, "4320": -1})        # 12% at once, exit after 72 h
        self.assertEqual(lay["majvaj_entry_threshold"], 0.03)
        self.assertNotIn("identifier", lay["freqai"])                         # unchanged look-ahead: same model, no retrain
        self.assertEqual(lay["freqai"]["feature_parameters"]["label_period_candles"], 18)

    def test_a_different_look_ahead_gets_its_own_model_name(self):
        s, _ = S.clean({"look_ahead_candles": 6})
        self.assertEqual(S.layer(s, "majvaj-4h-abc")["freqai"]["identifier"], "majvaj-4h-abc-L6")

    def test_limits_and_bad_input_are_refused(self):
        for bad in ({"entry_threshold_pct": 0.1}, {"entry_threshold_pct": 11}, {"stop_loss_pct": 0}, {"stop_loss_pct": 40},
                    {"take_profit_pct": "abc"}, {"time_exit_hours": 1}, {"look_ahead_candles": 1.5}, {"look_ahead_candles": 100},
                    {"entry_threshold_pct": True}, {"entry_threshold_pct": float("nan")}, {"trend_filter": "maybe"},
                    {"made_up": 1}):
            s, err = S.clean(bad)
            self.assertIsNone(s, bad); self.assertTrue(err, bad)
        self.assertIsNone(S.clean("not a dict")[0])
        s, err = S.clean({"trend_filter": "false", "entry_threshold_pct": "1.5", "look_ahead_candles": "12"})
        self.assertEqual((s["trend_filter"], s["entry_threshold_pct"], s["look_ahead_candles"]), (False, 1.5, 12))

    def test_warnings_explain_the_risk(self):
        w = " ".join(S.warnings_for(dict(S.DEFAULTS, entry_threshold_pct=0.5, take_profit_pct=0.8, stop_loss_pct=1.5,
                                         trend_filter=False, time_exit_hours=8)))
        for word in ("fees", "take-profit", "stop loss", "falling", "time exit"):
            self.assertIn(word, w)
        self.assertEqual(S.warnings_for(S.DEFAULTS), [])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ud = self.tmp.name
        self.mv = os.path.join(self.ud, "majvaj"); os.makedirs(self.mv)
        json.dump({"timeframe": "4h", "freqai": {"identifier": "majvaj-4h-abc"}}, open(os.path.join(self.ud, "config-4h.json"), "w"))
        self.ss = S.StrategySettings(self.mv, os.path.join(self.ud, "config-4h.json"), clock=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_writes_settings_and_the_bots_layer(self):
        r = self.ss.save({"entry_threshold_pct": 1.5, "look_ahead_candles": 6, "trend_filter": False})
        self.assertTrue(r["ok"]); self.assertTrue(r["retrain"])
        self.assertEqual(sorted(r["changed"]), ["entry_threshold_pct", "look_ahead_candles", "trend_filter"])
        lay = json.load(open(os.path.join(self.mv, "config.strategy.json")))
        self.assertEqual((lay["majvaj_entry_threshold"], lay["majvaj_trend_filter"]), (0.015, False))
        self.assertEqual(lay["freqai"]["identifier"], "majvaj-4h-abc-L6")
        self.assertEqual(S.effective_identifier(self.ud), "majvaj-4h-abc-L6")
        st = self.ss.status()
        self.assertEqual(st["settings"]["entry_threshold_pct"], 1.5); self.assertEqual(st["model_name"], "majvaj-4h-abc-L6")
        self.assertIn("any market", st["summary"])
        r2 = self.ss.save({"stop_loss_pct": 5})                              # stop and take-profit never retrain
        self.assertTrue(r2["ok"]); self.assertFalse(r2["retrain"])

    def test_a_refused_save_changes_nothing(self):
        self.ss.save({"stop_loss_pct": 5})
        before = open(os.path.join(self.mv, "config.strategy.json")).read()
        r = self.ss.save({"stop_loss_pct": 99})
        self.assertFalse(r["ok"]); self.assertIn("Stop loss", r["message"])
        self.assertEqual(open(os.path.join(self.mv, "config.strategy.json")).read(), before)
        self.assertEqual(self.ss.load()["stop_loss_pct"], 5.0)

    def test_an_unreadable_settings_file_falls_back_to_the_built_in_numbers(self):
        open(os.path.join(self.mv, "strategy_settings.json"), "w").write("garbage")
        self.assertEqual(self.ss.load(), S.DEFAULTS)
        json.dump({"settings": {"stop_loss_pct": 500}}, open(os.path.join(self.mv, "strategy_settings.json"), "w"))
        self.assertEqual(self.ss.load(), S.DEFAULTS)

    def test_unwritable_folder_is_reported_and_changes_nothing(self):
        with mock.patch.object(S, "_write", side_effect=PermissionError(13, "Permission denied", "/x/majvaj/s.json")):
            r = self.ss.save({"stop_loss_pct": 5})
        self.assertFalse(r["ok"]); self.assertIn("/x/majvaj/s.json", r["message"])


class DeskIntegrationTests(unittest.TestCase):
    def setUp(self):
        from desk.store import Store
        from tests.test_majvaj import FakeFt, Notes
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        os.makedirs(os.path.join(d, "mv"))
        cfg = {"ft_username": "u", "ft_password": "p", "verdict_file": os.path.join(d, "mv", "verdict.json"),
               "money_file": os.path.join(d, "mv", "money.json")}
        json.dump(cfg, open(os.path.join(d, "c.json"), "w"))
        self.notes = Notes()
        self.mv = MV.MajVaj(Store(os.path.join(d, "data")), os.path.join(d, "c.json"), notifier=self.notes, ft=FakeFt())

    def tearDown(self):
        self.tmp.cleanup()

    def test_saving_restarts_the_bot_but_a_refusal_does_not(self):
        req = os.path.join(self.mv.store.dir, "restart_request.json")
        r = self.mv.save_strategy({"settings": {"stop_loss_pct": 99}})
        self.assertFalse(r["ok"]); self.assertFalse(os.path.exists(req))
        r = self.mv.save_strategy({"settings": {"look_ahead_candles": 8}})
        self.assertTrue(r["ok"]); self.assertIn("trains a new model", r["message"])
        self.assertEqual(json.load(open(req))["what"], "bot")
        self.assertTrue(any("strategy settings changed" in m for m in self.notes.d))
        self.assertEqual(self.mv.status()["strategy"]["settings"]["look_ahead_candles"], 8)


class BacktestUsesTheSettingsTests(unittest.TestCase):
    def test_results_show_coins_and_settings_and_the_backtest_uses_the_layer(self):
        with tempfile.TemporaryDirectory() as ud:
            os.makedirs(os.path.join(ud, "majvaj"))
            json.dump({"timeframe": "4h", "freqai": {"identifier": "majvaj-4h-abc"}}, open(os.path.join(ud, "config-4h.json"), "w"))
            ss = S.StrategySettings(os.path.join(ud, "majvaj"), os.path.join(ud, "config-4h.json"))
            ss.save({"look_ahead_candles": 6})
            lab = F.Lab(ud, clock=lambda: NOW)
            self.assertEqual(lab.model()["identifier"], "majvaj-4h-abc-L6"); self.assertEqual(lab.model()["label_candles"], 6)
            self.assertTrue(lab.request("3m")["ok"])
            calls = []
            run = lambda c: (calls.append(c) or ("inactive" if c[:2] == ["systemctl", "is-active"] else ""))
            with mock.patch.object(os, "chown", lambda *a: None):
                self.assertEqual(F.tick(ud, "img", "/h.db", NOW, run), "backtest started")
            script = [c for c in calls if c[0] == "systemd-run"][0][-1]
            self.assertIn("majvaj/config.strategy.json", script)
            self.assertEqual(json.load(open(os.path.join(ud, "majvaj", "backtest_override.json")))["freqai"]["identifier"],
                             "majvaj-4h-abc-L6-backtest")
            # a result file that carries its config shows the coins and the numbers it ran with
            fake_result(os.path.join(ud, "backtest_results"), "backtest-result-2026-11-23_10-00-00.zip", config={
                "exchange": {"pair_whitelist": ["BTC/USD", "ETH/USD"]}, "stoploss": -0.05,
                "minimal_roi": {"0": 0.04, "1440": -1}, "majvaj_entry_threshold": 0.015, "majvaj_trend_filter": False,
                "freqai": {"feature_parameters": {"label_period_candles": 6}}})
            r = lab.results()[0]
            self.assertEqual(r["pairs"], ["BTC/USD", "ETH/USD"])
            for part in ("buy >= 1.5%", "look-ahead 6", "trend filter off", "stop 5%", "take-profit 4%", "exit after 24 h"):
                self.assertIn(part, r["settings"])


if __name__ == "__main__":
    unittest.main()
