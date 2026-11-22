"""Run inside the Freqtrade environment:  python -m unittest discover -s tests -t ."""
import json, os, sys, tempfile, time, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "user_data", "strategies"))
from MajVajFreqAI import allowance, goal_state, ledger, load_money, read_verdict  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
