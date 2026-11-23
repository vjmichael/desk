"""Run inside the Freqtrade environment:  python -m unittest discover -s tests -t ."""
import json, os, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "user_data", "strategies"))
from MajVajFreqAI import (allowance, event_distances, goal_state, in_event_window, ledger, load_money,  # noqa: E402
                          log_decision, model_trained, point_in_time, read_verdict)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

DAY = datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc)  # London/New York overlap


def money(**kw):
    m = load_money("/nonexistent", 1000.0)
    m.update(kw)
    return m


class LedgerTests(unittest.TestCase):
    def test_faq_example(self):
        closed = [(DAY - timedelta(days=6 - i), 100.0) for i in range(5)] + [(DAY, -20.0)]
        self.assertEqual(ledger(100.0, closed), (80.0, 500.0))

    def test_bankroll_caps_size_and_profit_never_traded(self):
        m = money(bankroll=300)
        closed = [(DAY - timedelta(days=2), 500.0)]
        stake, why, _ = allowance(m, closed, [], DAY, 3, 10_000)
        self.assertAlmostEqual(stake, 100.0)
        self.assertIn("protected 500", why)

    def test_bad_money_file_blocks(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("garbage")
        self.assertEqual(load_money(f.name, 1000)["bankroll"], 0.0)
        self.assertEqual(allowance(load_money(f.name, 1000), [], [], DAY, 3, 100)[0], 0.0)

    def test_daily_loss_limit(self):
        self.assertEqual(allowance(money(), [(DAY - timedelta(hours=1), -31.0)], [], DAY, 3, 100)[0], 0.0)


class GoalTests(unittest.TestCase):
    WON = [(DAY - timedelta(hours=1), 10.0)]

    def test_stop_mode(self):
        self.assertEqual(allowance(money(daily_goal_usd=10, goal_mode="stop"), self.WON, [], DAY, 3, 300)[0], 0.0)

    def test_opportunity_mode_risks_at_most_the_allowed_giveback(self):
        m = money(daily_goal_usd=10, goal_mode="opportunity", goal_keep_pct=50)
        stake, why, mode = allowance(m, self.WON, [], DAY, 3, 300, stop_frac=0.08)
        self.assertEqual(mode, "opportunity")
        self.assertLessEqual(stake * (0.08 + 0.0052), 5.0 + 1e-9)
        self.assertEqual(allowance(m, self.WON, [], DAY.replace(hour=19), 3, 300)[0], 0.0)        # New York
        self.assertEqual(allowance(m, self.WON, [50.0], DAY, 2, 300)[0], 0.0)                       # one at a time
        self.assertEqual(allowance(m, self.WON + [(DAY, -5.0)], [], DAY + timedelta(minutes=5), 3, 300)[0], 0.0)

    def test_goal_stays_reached_after_a_loss(self):
        reached, peak, net = goal_state(money(daily_goal_usd=10), [12.0, -6.0])
        self.assertEqual((reached, peak, net), (True, 12.0, 6.0))


class VerdictTests(unittest.TestCase):
    def test_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "v.json")
            now = time.time()
            json.dump({"verdict": "normal", "expires": now + 60}, open(p, "w"))
            self.assertEqual(read_verdict(p, now)[0], "normal")
            json.dump({"verdict": "normal", "expires": now - 1}, open(p, "w"))
            self.assertEqual(read_verdict(p, now)[0], "stand_down")
            self.assertEqual(read_verdict(os.path.join(d, "missing"), now)[0], "stand_down")


class CalendarAndDataTests(unittest.TestCase):
    def test_event_window(self):
        ev = np.array([10_000.0])
        self.assertTrue(in_event_window(ev, 10_000 - 59 * 60))     # 59 min before
        self.assertFalse(in_event_window(ev, 10_000 - 61 * 60))    # 61 min before
        self.assertTrue(in_event_window(ev, 10_000 + 89 * 60))     # 89 min after
        self.assertFalse(in_event_window(ev, 10_000 + 91 * 60))
        self.assertFalse(in_event_window(None, 10_000))            # no calendar: no block (Grok still guards)

    def test_point_in_time_never_uses_future_values(self):
        table = pd.DataFrame({"available_at": [100.0, 200.0], "value": [1.0, 2.0]})
        out = point_in_time(np.array([50.0, 100.0, 150.0, 250.0]), table)
        self.assertTrue(np.isnan(out[0])); self.assertEqual(list(out[1:]), [1.0, 1.0, 2.0])

    def test_event_distances(self):
        nxt, prv = event_distances(np.array([3600.0 * 10]), np.array([0.0, 3600.0 * 30]))
        self.assertEqual((nxt[0], prv[0]), (20.0, 10.0))


class DecisionLogTests(unittest.TestCase):
    def test_log_appends_rotates_and_never_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "decisions.jsonl")
            log_decision(p, "BTC/USD", "rejected", "money rules: daily loss limit", now=1.0)
            log_decision(p, "ETH/USD", "approved", "approved (fundamentals normal)", now=2.0)
            rows = [json.loads(x) for x in open(p)]
            self.assertEqual([r["result"] for r in rows], ["rejected", "approved"])
            with open(p, "a") as f:
                f.write("x" * 1_000_100)
            log_decision(p, "SOL/USD", "rejected", "calendar", now=3.0)
            self.assertTrue(os.path.exists(p + ".1")); self.assertEqual(len(open(p).readlines()), 1)
            log_decision(os.path.join(d, "missing-dir", "x.jsonl"), "BTC/USD", "approved", "ok")   # no exception


class ModelGateTests(unittest.TestCase):
    def test_a_coin_trades_only_after_its_model_is_saved(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(model_trained(d, "m1", "DOGE/USD"))
            self.assertFalse(model_trained(d, None, "BTC/USD"))
            os.makedirs(os.path.join(d, "m1", "sub-train-DOGE_1790000000"))
            os.makedirs(os.path.join(d, "m1", "sub-train-BTCX_1790000000"))
            self.assertTrue(model_trained(d, "m1", "DOGE/USD"))
            self.assertFalse(model_trained(d, "m1", "BTC/USD"))          # BTCX is another coin
            self.assertFalse(model_trained(d, "m2", "DOGE/USD"))         # another model version


if __name__ == "__main__":
    unittest.main()


class SettingsFromConfigTests(unittest.TestCase):
    """The Desk page's strategy settings reach the strategy through config keys; bad values fall back safely."""
    def stub(self, cfg, default=0.03):
        from types import SimpleNamespace
        return SimpleNamespace(config=cfg, entry_threshold=default)

    def test_threshold(self):
        from MajVajFreqAI import MajVajFreqAI as S
        self.assertEqual(S._threshold(self.stub({})), 0.03)                                   # nothing set: built-in
        self.assertEqual(S._threshold(self.stub({"majvaj_entry_threshold": 0.015})), 0.015)
        for bad in ("abc", None, -1, 0, 0.0001, 0.9, float("nan")):
            self.assertEqual(S._threshold(self.stub({"majvaj_entry_threshold": bad})), 0.03, bad)

    def test_trend_filter_only_accepts_a_real_boolean(self):
        from MajVajFreqAI import MajVajFreqAI as S
        self.assertTrue(S._trend_filter(self.stub({})))
        self.assertFalse(S._trend_filter(self.stub({"majvaj_trend_filter": False})))
        self.assertTrue(S._trend_filter(self.stub({"majvaj_trend_filter": "no"})))           # unclear: keep the safe filter on


class PlanTradeTests(unittest.TestCase):
    """Trades the Desk opens for approved plans use the plan's stop, target and expiry."""
    def setUp(self):
        from types import SimpleNamespace
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "plans_active.json")
        json.dump({"plans": {"plan-5": {"pair": "BTC/USD", "stop": 97.0, "target": 106.0, "expires": 2_000_000_000}}},
                  open(self.path, "w"))
        self.stub = SimpleNamespace(config={"majvaj_plans_file": self.path})
        from MajVajFreqAI import MajVajFreqAI as S
        self.S = S
        self.stub._plan = lambda trade: S._plan(self.stub, trade)

    def tearDown(self):
        self.tmp.cleanup()

    def trade(self, tag, pair="BTC/USD"):
        from types import SimpleNamespace
        return SimpleNamespace(enter_tag=tag, pair=pair)

    def test_exit_reason(self):
        from MajVajFreqAI import plan_exit_reason
        p = {"target": 106.0, "expires": 1000}
        self.assertEqual(plan_exit_reason(p, 106.0, 10), "plan_target")
        self.assertEqual(plan_exit_reason(p, 100.0, 1000), "plan_expired")
        self.assertIsNone(plan_exit_reason(p, 100.0, 10))
        self.assertIsNone(plan_exit_reason({"target": "x"}, 100.0, 10))

    def test_stop_target_and_model_exit_for_plan_trades_only(self):
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        plan_trade, other = self.trade("plan-5"), self.trade("model")
        sl = self.S.custom_stoploss(self.stub, "BTC/USD", plan_trade, now, 100.0, 0.0, False)
        self.assertAlmostEqual(sl, 0.03, places=4)                                   # 3% below 100 = the plan's 97
        self.assertIsNone(self.S.custom_stoploss(self.stub, "BTC/USD", other, now, 100.0, 0.0, False))
        self.assertIsNone(self.S.custom_stoploss(self.stub, "ETH/USD", self.trade("plan-5", "ETH/USD"), now, 100.0, 0, False))
        self.assertEqual(self.S.custom_exit(self.stub, "BTC/USD", plan_trade, now, 106.5, 0.06), "plan_target")
        self.assertIsNone(self.S.custom_exit(self.stub, "BTC/USD", plan_trade, now, 101, 0.01))
        args = dict(order_type="limit", amount=1, time_in_force="GTC", current_time=now)
        self.assertFalse(self.S.confirm_trade_exit(self.stub, "BTC/USD", plan_trade, rate=101, exit_reason="exit_signal", **args))
        self.assertTrue(self.S.confirm_trade_exit(self.stub, "BTC/USD", plan_trade, rate=101, exit_reason="stop_loss", **args))
        self.assertTrue(self.S.confirm_trade_exit(self.stub, "BTC/USD", other, rate=101, exit_reason="exit_signal", **args))

    def test_missing_plan_file_falls_back_to_the_normal_stop(self):
        from types import SimpleNamespace
        stub = SimpleNamespace(config={"majvaj_plans_file": "/nonexistent/plans.json"})
        stub._plan = lambda trade: self.S._plan(stub, trade)
        now = datetime(2026, 11, 23, tzinfo=timezone.utc)
        self.assertIsNone(self.S.custom_stoploss(stub, "BTC/USD", self.trade("plan-5"), now, 100.0, 0.0, False))
