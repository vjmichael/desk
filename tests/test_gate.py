import os, json, tempfile, time, unittest
from desk.gate import Policy, Proposal, Market, Account, evaluate, log_decision

NOW = 1_800_000_000.0


def prop(**kw):
    d = dict(id="p1", symbol="BTC/USD", direction="long", entry=100.0, stop=97.0,
             target=106.0, expires_at=NOW + 3600)
    d.update(kw)
    return Proposal(**d)


def mkt(**kw):
    d = dict(bid=99.9, ask=100.0, ts=NOW - 5)
    d.update(kw)
    return Market(**d)


def acct(**kw):
    d = dict(equity=1000.0, cash=1000.0)
    d.update(kw)
    return Account(**d)


class GateTests(unittest.TestCase):
    def test_ordinary_ai_idea_is_approved_without_preapproval(self):
        # v0.6 rejected this: no approved strategy family, no prevalidated edge model.
        d = evaluate(prop(origin="ai", confidence=0.2, evidence_ids=[]), mkt(), acct(), now=NOW)
        self.assertTrue(d.approved, d.reason)
        self.assertEqual(d.reason, "approved")

    def test_sizing_matches_risk_budget(self):
        d = evaluate(prop(), mkt(), acct(), now=NOW)
        # risk 0.5% of 1000 = 5.0; loss per unit = 3 + 100*0.62% = 3.62
        self.assertAlmostEqual(d.risk_amount, 5.0)
        self.assertAlmostEqual(d.quantity, 5.0 / 3.62, places=5)
        self.assertLessEqual(d.notional, 200.0)

    def test_missing_evidence_and_rationale_do_not_block(self):
        d = evaluate(prop(rationale="", evidence_ids=[], confidence=None), mkt(), acct(), now=NOW)
        self.assertTrue(d.approved)

    def test_stand_down_shrinks_instead_of_blocking(self):
        full = evaluate(prop(), mkt(), acct(), now=NOW)
        half = evaluate(prop(stand_down=True), mkt(), acct(), now=NOW)
        self.assertTrue(half.approved)
        self.assertAlmostEqual(half.risk_amount, full.risk_amount / 2)
        self.assertTrue(any("stand-down" in n for n in half.notes))

    def test_entry_is_repegged_to_current_ask(self):
        d = evaluate(prop(entry=99.6), mkt(), acct(), now=NOW)
        self.assertTrue(d.approved)
        self.assertEqual(d.entry, 100.0)

    def test_stale_idea_rejected(self):
        d = evaluate(prop(entry=96.0, stop=93.0, target=103.0), mkt(), acct(), now=NOW)
        self.assertFalse(d.approved)
        self.assertTrue(d.reason.startswith("levels:"))

    def test_live_mode_always_rejected(self):
        d = evaluate(prop(), mkt(), acct(), Policy(mode="live"), now=NOW)
        self.assertFalse(d.approved)
        self.assertIn("not enabled", d.reason)

    def test_hard_rejects(self):
        cases = {
            "valid": prop(direction="short"),
            "valid ": prop(entry=-1),
            "whitelist": prop(symbol="DOGE/USD"),
            "valid  ": prop(expires_at=NOW - 1),
        }
        for _, p in cases.items():
            self.assertFalse(evaluate(p, mkt(), acct(), now=NOW).approved)
        self.assertIn("whitelist", evaluate(prop(symbol="DOGE/USD"), mkt(), acct(), now=NOW).reason)

    def test_account_limits(self):
        self.assertIn("paused", evaluate(prop(), mkt(), acct(paused=True), now=NOW).reason)
        self.assertIn("daily loss", evaluate(prop(), mkt(), acct(realized_pnl_today=-30.0), now=NOW).reason)
        self.assertIn("already holding", evaluate(prop(), mkt(), acct(open_symbols=("BTC/USD",)), now=NOW).reason)
        five = ("A", "B", "C", "D", "E")
        self.assertIn("max open", evaluate(prop(), mkt(), acct(open_symbols=five), now=NOW).reason)

    def test_market_checks(self):
        self.assertIn("old", evaluate(prop(), mkt(ts=NOW - 900), acct(), now=NOW).reason)
        self.assertIn("spread", evaluate(prop(), mkt(bid=98.0), acct(), now=NOW).reason)

    def test_costs_can_kill_a_thin_idea(self):
        d = evaluate(prop(target=100.5), mkt(), acct(), now=NOW)  # 0.5% gain < 0.62% costs
        self.assertFalse(d.approved)
        self.assertTrue(d.reason.startswith("worth_it:"))

    def test_bad_stop_distance(self):
        self.assertIn("stop", evaluate(prop(stop=99.9), mkt(), acct(), now=NOW).reason)
        self.assertIn("stop", evaluate(prop(stop=80.0, target=160.0), mkt(), acct(), now=NOW).reason)

    def test_tiny_position_is_rejected_not_enlarged(self):
        d = evaluate(prop(), mkt(), acct(equity=50.0, cash=50.0), now=NOW)
        self.assertFalse(d.approved)
        self.assertIn("below", d.reason)

    def test_cash_caps_size(self):
        d = evaluate(prop(), mkt(), acct(cash=50.0), now=NOW)
        self.assertTrue(d.approved)
        self.assertLessEqual(d.notional, 50.0)

    def test_every_decision_lists_its_checks_and_logs(self):
        d = evaluate(prop(), mkt(), acct(), now=NOW)
        self.assertEqual([c["name"] for c in d.checks],
                         ["mode", "valid", "whitelist", "account", "market", "levels", "worth_it", "sizing"])
        with tempfile.TemporaryDirectory() as t:
            path = os.path.join(t, "sub", "decisions.jsonl")
            log_decision(path, prop(), d)
            log_decision(path, prop(), d)
            with open(path) as fh:
                lines = fh.read().strip().split("\n")
            self.assertEqual(len(lines), 2)
            self.assertTrue(json.loads(lines[0])["decision"]["approved"])


if __name__ == "__main__":
    unittest.main()
