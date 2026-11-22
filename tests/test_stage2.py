import json, os, tempfile, threading, time, unittest
from desk import engine as E, indicators
from desk.gate import Market, Proposal
from desk.market import Candle, MarketError, KrakenMarket, pair_code
from desk.paper import PaperAccount
from desk.scout import GrokScout, ScoutResult, parse_proposals, extract_json

T = 1_800_000_000.0
SYMS = ("BTC/USD", "ETH/USD", "SOL/USD")


def hourly(n=80, start=100.0):
    return [Candle(int(T) - (n - i) * 3600, start + i * 0.05, start + i * 0.05 + 0.3, start + i * 0.05 - 0.3,
                   start + i * 0.05 + 0.1, 10.0 + (i % 5)) for i in range(n)]


class FakeMarket:
    def __init__(self):
        self.q = {s: Market(bid=99.9, ask=100.0, ts=T) for s in SYMS}
        self.m1, self.fail, self.candle_fail = [], False, False

    def quote(self, sym):
        if self.fail:
            raise MarketError("down")
        return self.q[sym]

    def candles(self, sym, interval, since=None):
        if self.candle_fail:
            raise MarketError("down")
        return hourly() if interval == 60 else list(self.m1)


class FakeScout:
    def __init__(self, props=None, ok=True, error=""):
        self.props, self.ok, self.error, self.calls, self.briefs = props or [], ok, error, 0, []

    def propose(self, brief, now):
        self.calls += 1
        self.briefs.append(brief)
        return ScoutResult(self.ok, list(self.props), "nothing clear" if not self.props else "", self.error)


class FakeNotifier:
    def __init__(self):
        self.desk_msgs, self.rec_msgs = [], []

    def desk(self, t): self.desk_msgs.append(t)
    def recovery(self, t): self.rec_msgs.append(t)


def prop(**kw):
    d = dict(id="p1", symbol="BTC/USD", direction="long", entry=100.0, stop=97.0, target=106.0,
             expires_at=T + 6 * 3600, confidence=0.6, rationale="test thesis")
    d.update(kw)
    return Proposal(**d)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "ledger.db")
        self.clock = [T]
        self.mk, self.notes = FakeMarket(), FakeNotifier()
        self.settings = E.Settings()
        self.make()

    def make(self, props=None, **kw):
        self.account = PaperAccount(self.path, 1000.0)
        self.scout = FakeScout(props, **kw)
        self.eng = E.Engine(self.account, self.mk, self.scout, self.notes, lambda: self.settings, lambda: self.clock[0])

    def tearDown(self):
        self.eng.stop_event.set(); self.account.db.close(); self.tmp.cleanup()

    def open_one(self):
        self.make([prop()])
        r = self.eng.cycle(force_scout=True)
        self.assertEqual(r["opened"], ["BTC/USD"], r)
        return self.account.open_positions()[0]


class OpeningTests(Base):
    def test_approved_idea_opens_and_cash_adds_up(self):
        pos = self.open_one()
        self.assertAlmostEqual(pos["entry"], 100.0 * 1.0005)
        spent = pos["qty"] * pos["entry"] + pos["entry_fee"]
        self.assertAlmostEqual(self.account.cash, 1000.0 - spent, places=6)
        self.assertAlmostEqual(pos["entry_fee"], pos["qty"] * pos["entry"] * 0.0026, places=6)
        self.assertTrue(any(m.startswith("BUY (paper) BTC/USD") for m in self.notes.desk_msgs))
        self.assertIn("test thesis", " ".join(self.notes.desk_msgs))

    def test_brief_sent_to_scout_has_facts_and_rules(self):
        self.open_one()
        brief = self.scout.briefs[0]
        self.assertEqual(set(brief["symbols"]), set(SYMS))
        self.assertIn("rsi14", brief["symbols"]["BTC/USD"])
        self.assertIn("min_net_reward_to_risk", brief["rules"])

    def test_rejected_idea_is_logged_with_reason_and_reported(self):
        self.make([prop(target=100.4)])  # too small after costs
        r = self.eng.cycle(force_scout=True)
        self.assertEqual(r["opened"], [])
        d = self.account.recent_decisions()[0]
        self.assertEqual(d["approved"], 0); self.assertIn("worth_it", d["reason"])
        self.assertTrue(any("rejected" in m for m in self.notes.desk_msgs))
        self.assertEqual(self.account.open_positions(), [])

    def test_no_proposals_is_a_valid_quiet_round(self):
        self.make([])
        r = self.eng.cycle(force_scout=True)
        self.assertEqual((r["opened"], r["rejected"]), ([], []))
        self.assertEqual(self.account.last_scout()["ok"], 1)
        self.assertEqual(self.notes.desk_msgs, [])

    def test_second_idea_same_symbol_is_rejected(self):
        self.make([prop(id="a"), prop(id="b", confidence=0.1)])
        r = self.eng.cycle(force_scout=True)
        self.assertEqual(r["opened"], ["BTC/USD"])
        self.assertTrue(any("already holding" in x for x in r["rejected"]))

    def test_scout_only_runs_when_due(self):
        self.make([]); self.eng.running = True
        self.eng.cycle(); self.eng.cycle()
        self.assertEqual(self.scout.calls, 1)
        self.clock[0] += 61 * 60
        self.eng.cycle()
        self.assertEqual(self.scout.calls, 2)

    def test_stopped_engine_does_not_scout(self):
        self.make([])
        self.eng.cycle()
        self.assertEqual(self.scout.calls, 0)

    def test_daily_scout_cap(self):
        self.settings.max_scout_calls_per_day = 2
        self.make([])
        for _ in range(4):
            self.eng.cycle(force_scout=True)
        self.assertEqual(self.scout.calls, 2)
        self.assertIn("daily scout limit", self.account.last_scout()["note"])


class ExitTests(Base):
    def candle(self, o, h, l, c, dt=60):
        return Candle(int(self.clock[0]) + dt, o, h, l, c, 1.0)

    def close_state(self):
        pos = self.account.closed_positions()[0]
        self.assertAlmostEqual(self.account.equity({}), self.account.cash)  # nothing left open
        return pos

    def test_target_hit_books_profit_after_fees(self):
        pos = self.open_one()
        self.clock[0] += 300
        self.mk.m1 = [self.candle(100, 101, 99.5, 100.5), self.candle(101, 107, 100.8, 106, 120), self.candle(106, 106, 106, 106, 180)]
        r = self.eng.cycle()
        self.assertEqual(r["closed"][0]["reason"], "target")
        done = self.close_state()
        self.assertEqual(done["exit_price"], 106.0)
        expected = (106.0 - pos["entry"]) * pos["qty"] - pos["entry_fee"] - pos["qty"] * 106.0 * 0.0026
        self.assertAlmostEqual(done["pnl"], expected, places=6)
        self.assertAlmostEqual(self.account.cash, 1000.0 + done["pnl"], places=6)
        self.assertTrue(any("SELL (paper) BTC/USD: target" in m for m in self.notes.desk_msgs))

    def test_stop_hit_loses_about_the_risk_budget(self):
        pos = self.open_one()
        self.mk.m1 = [self.candle(100, 100.2, 96.5, 97, 120), self.candle(97, 97, 97, 97, 180)]
        self.clock[0] += 300
        self.eng.cycle()
        done = self.close_state()
        self.assertEqual(done["exit_reason"], "stop")
        self.assertAlmostEqual(done["exit_price"], 97.0 * 0.9995)
        self.assertLess(done["pnl"], 0)
        self.assertLess(abs(done["pnl"]), 6.0)  # budget is 0.5% of 1000 = 5, plus slippage

    def test_gap_below_stop_fills_at_the_open_not_the_stop(self):
        self.open_one()
        self.mk.m1 = [self.candle(94, 95, 93, 94, 120), self.candle(94, 94, 94, 94, 180)]
        self.clock[0] += 300
        self.eng.cycle()
        self.assertAlmostEqual(self.close_state()["exit_price"], 94.0 * 0.9995)

    def test_candle_touching_both_levels_counts_as_stop(self):
        self.open_one()
        self.mk.m1 = [self.candle(100, 110, 90, 100, 120), self.candle(100, 100, 100, 100, 180)]
        self.clock[0] += 300
        self.eng.cycle()
        self.assertEqual(self.close_state()["exit_reason"], "stop")

    def test_the_entry_minute_candle_is_ignored(self):
        self.open_one()
        self.mk.m1 = [Candle(int(T) - 30, 100, 100, 50, 100, 1.0), self.candle(100, 100, 100, 100, 120)]
        self.clock[0] += 300
        self.eng.cycle()
        self.assertEqual(len(self.account.open_positions()), 1)

    def test_forming_candle_is_rechecked_next_cycle(self):
        pos = self.open_one()
        self.mk.m1 = [self.candle(100, 101, 99.5, 100, 60), self.candle(100, 101, 99.5, 100, 120)]
        self.clock[0] += 300
        self.eng.cycle()
        self.assertEqual(self.account.open_positions()[0]["last_checked"], int(T) + 60)
        self.mk.m1[-1] = self.candle(100, 108, 99.5, 107, 120)  # same candle now shows the target
        self.mk.m1.append(self.candle(107, 107, 107, 107, 180))
        self.eng.cycle()
        self.assertEqual(self.close_state()["exit_reason"], "target")

    def test_time_limit_exit(self):
        self.open_one()
        self.mk.m1 = []
        self.clock[0] = T + 7 * 3600
        self.mk.q["BTC/USD"] = Market(bid=100.2, ask=100.3, ts=self.clock[0])
        self.eng.cycle()
        done = self.close_state()
        self.assertEqual(done["exit_reason"], "time")

    def test_price_below_stop_with_no_candles_still_stops(self):
        self.open_one()
        self.mk.m1 = []
        self.mk.q["BTC/USD"] = Market(bid=96.0, ask=96.1, ts=T)
        self.eng.cycle()
        self.assertEqual(self.close_state()["exit_reason"], "stop")

    def test_position_survives_restart(self):
        self.open_one()
        cash = self.account.cash
        self.account.db.close()
        self.make([])
        self.assertEqual(len(self.account.open_positions()), 1)
        self.assertAlmostEqual(self.account.cash, cash)


class FailureTests(Base):
    def test_market_outage_alerts_recovery_bot_once(self):
        self.mk.fail = True
        for _ in range(5):
            r = self.eng.cycle()
            self.assertFalse(r["ok"])
        self.assertEqual(len(self.notes.rec_msgs), 1)
        self.clock[0] += 1900
        self.eng.cycle()
        self.assertEqual(len(self.notes.rec_msgs), 2)
        self.mk.fail = False
        self.assertTrue(self.eng.cycle()["ok"])
        self.assertEqual(self.eng.market_failures, 0)

    def test_scout_failure_does_not_crash_and_alerts_after_three(self):
        self.make([], ok=False, error="xAI rejected the key (HTTP 401)")
        for _ in range(3):
            r = self.eng.cycle(force_scout=True)
            self.assertTrue(r["ok"])
        self.assertEqual(len(self.notes.rec_msgs), 1)
        self.assertIn("rejected", self.notes.rec_msgs[0])
        self.assertEqual(self.account.last_scout()["ok"], 0)

    def test_candle_outage_skips_scout_but_keeps_running(self):
        self.make([prop()]); self.mk.candle_fail = True
        r = self.eng.cycle(force_scout=True)
        self.assertEqual(r["opened"], [])
        self.assertEqual(self.scout.calls, 0)

    def test_status_reports_everything_the_page_needs(self):
        self.open_one()
        st = self.eng.status()
        for k in ("running", "cash", "equity", "pnl", "open", "closed", "decisions", "last_scout", "errors", "settings"):
            self.assertIn(k, st)
        self.assertEqual(len(st["open"]), 1)
        self.assertIsNotNone(st["open"][0]["mark"])
        json.dumps(st)  # must be serialisable


class ThreadTests(Base):
    def test_start_scout_now_and_stop(self):
        self.settings.cycle_minutes = 0.01
        self.make([])
        self.assertTrue(self.eng.start()); self.assertFalse(self.eng.start())
        deadline = time.time() + 5
        while self.scout.calls < 1 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.scout.calls, 1)  # first round runs at once, then waits for the interval
        self.assertTrue(self.eng.request_scout())
        while self.scout.calls < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.scout.calls, 2)
        self.assertTrue(self.eng.stop())
        self.assertFalse(self.eng.request_scout())
        self.assertTrue(any("started" in m for m in self.notes.desk_msgs))
        self.assertTrue(any("stopped" in m for m in self.notes.desk_msgs))


class SettingsTests(unittest.TestCase):
    def test_defaults_clamps_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(E.load_settings(d).scout_minutes, 60)
            s = E.save_settings(d, {"scout_minutes": 30, "grok_model": "grok-4.20-0309-reasoning", "cycle_minutes": 9999,
                                    "paper_equity": 1, "whitelist": ["EVIL/USD"]})
            self.assertEqual((s.scout_minutes, s.cycle_minutes, s.grok_model), (30, 5, "grok-4.20-0309-reasoning"))
            self.assertEqual(s.paper_equity, 1000.0); self.assertEqual(s.whitelist, SYMS)
            self.assertEqual(E.load_settings(d).scout_minutes, 30)
            E.save_settings(d, {"grok_model": "bad model; drop"})
            self.assertEqual(E.load_settings(d).grok_model, "grok-4.20-0309-reasoning")


class ScoutParsingTests(unittest.TestCase):
    GOOD = {"proposals": [{"symbol": "BTC/USD", "direction": "long", "entry": 100, "stop": 97, "target": 106,
                           "horizon_hours": 12, "confidence": 0.7, "rationale": "r"}], "no_trade_reason": ""}

    def parse(self, text):
        return parse_proposals(text, T, SYMS, 48)

    def test_plain_fenced_and_chatty_json(self):
        body = json.dumps(self.GOOD)
        for text in (body, "```json\n%s\n```" % body, "Sure! Here you go:\n%s\nGood luck." % body):
            r = self.parse(text)
            self.assertTrue(r.ok); self.assertEqual(len(r.proposals), 1)
        p = self.parse(body).proposals[0]
        self.assertEqual((p.symbol, p.entry, p.origin), ("BTC/USD", 100.0, "ai"))
        self.assertAlmostEqual(p.expires_at, T + 12 * 3600)

    def test_garbage_and_hostile_items(self):
        self.assertFalse(self.parse("not json at all").ok)
        self.assertFalse(self.parse("[1,2]").ok)
        bad = {"proposals": [{"symbol": "DOGE/USD", "entry": 1, "stop": 0.9, "target": 1.2},
                             {"symbol": "BTC/USD", "direction": "short", "entry": 1, "stop": 2, "target": 0},
                             {"symbol": "BTC/USD", "entry": "abc", "stop": 1, "target": 2},
                             "junk", {"symbol": "ETH/USD", "entry": float("nan"), "stop": 1, "target": 2}]}
        r = self.parse(json.dumps(bad).replace("NaN", "NaN"))
        self.assertTrue(r.ok); self.assertEqual(r.proposals, []); self.assertEqual(r.skipped, 5)

    def test_horizon_and_confidence_are_clamped_and_text_is_capped(self):
        d = json.loads(json.dumps(self.GOOD))
        d["proposals"][0].update(horizon_hours=9999, confidence=5, rationale="x" * 5000)
        p = self.parse(json.dumps(d)).proposals[0]
        self.assertAlmostEqual(p.expires_at, T + 48 * 3600); self.assertEqual(p.confidence, 1.0); self.assertEqual(len(p.rationale), 500)

    def test_empty_with_reason(self):
        r = self.parse(json.dumps({"proposals": [], "no_trade_reason": "choppy"}))
        self.assertTrue(r.ok); self.assertEqual((r.proposals, r.note), ([], "choppy"))


class GrokScoutTests(unittest.TestCase):
    def scout(self, replies):
        self.calls = []
        def http(method, url, headers=None, body=None, timeout=20):
            self.calls.append((url, headers, json.loads(body)))
            return replies.pop(0)
        return GrokScout(lambda: "KEY", lambda: "grok-4.3", lambda: SYMS, lambda: 48, http)

    def reply(self, content):
        return 200, {"choices": [{"message": {"content": content}}]}

    def test_success_sends_model_brief_and_key(self):
        s = self.scout([self.reply(json.dumps(ScoutParsingTests.GOOD))])
        r = s.propose({"hello": 1}, T)
        self.assertTrue(r.ok); self.assertEqual(len(r.proposals), 1)
        url, headers, body = self.calls[0]
        self.assertTrue(url.endswith("/chat/completions")); self.assertEqual(headers["Authorization"], "Bearer KEY")
        self.assertEqual(body["model"], "grok-4.3"); self.assertEqual(json.loads(body["messages"][1]["content"]), {"hello": 1})
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_retries_without_response_format_when_rejected(self):
        s = self.scout([(400, {"error": "unsupported"}), self.reply(json.dumps(ScoutParsingTests.GOOD))])
        self.assertTrue(s.propose({}, T).ok)
        self.assertNotIn("response_format", self.calls[1][2])

    def test_error_statuses_have_plain_messages_without_the_key(self):
        for status, word in ((401, "rejected"), (404, "model"), (429, "rate limit"), (500, "HTTP 500")):
            r = self.scout([(status, {})]).propose({}, T)
            self.assertFalse(r.ok); self.assertIn(word, r.error); self.assertNotIn("KEY", r.error)

    def test_missing_key_and_odd_shapes(self):
        self.assertFalse(GrokScout(lambda: "", lambda: "m", lambda: SYMS, lambda: 48).propose({}, T).ok)
        self.assertFalse(self.scout([(200, {"choices": []})]).propose({}, T).ok)
        self.assertFalse(self.scout([self.reply("I cannot help")]).propose({}, T).ok)


class PaperAndMarketTests(unittest.TestCase):
    def test_reset_wipes_and_restores(self):
        with tempfile.TemporaryDirectory() as d:
            a = PaperAccount(os.path.join(d, "l.db"), 500.0)
            a.open_position("p", "BTC/USD", 1.0, 100.0, 90.0, 120.0, T, T + 99, 0.26)
            self.assertAlmostEqual(a.cash, 399.74)
            a.reset(2000.0)
            self.assertEqual((a.cash, a.start_equity, a.open_positions()), (2000.0, 2000.0, []))
            a.db.close()

    def test_indicators_known_values(self):
        self.assertAlmostEqual(indicators.ema([1, 2, 3, 4, 5], 3), 4.0)
        self.assertEqual(indicators.rsi([float(i) for i in range(30)]), 100.0)
        self.assertAlmostEqual(indicators.rsi([1.0, 2.0] * 20), 50.0, delta=3)
        self.assertIsNone(indicators.rsi([1.0, 2.0]))

    def test_kraken_parsing_and_pair_codes(self):
        from unittest import mock
        self.assertEqual((pair_code("BTC/USD"), pair_code("DOGE/USD")), ("XBTUSD", "DOGEUSD"))
        tick = {"error": [], "result": {"XXBTZUSD": {"b": ["64000.1", "1", "1"], "a": ["64000.5", "1", "1"]}}}
        ohlc = {"error": [], "result": {"XXBTZUSD": [[1, "1", "2", "0.5", "1.5", "1.2", "9", 3], ["bad"], [2, "1", "2", "0.5", "1.5", "1.2", "8", 3]], "last": 2}}
        with mock.patch("desk.market._get", side_effect=[(200, tick), (200, ohlc), (200, {"error": ["EGeneral:Invalid"]}), (500, "x")]):
            m = KrakenMarket()
            q = m.quote("BTC/USD"); self.assertEqual((q.bid, q.ask), (64000.1, 64000.5))
            c = m.candles("BTC/USD", 60); self.assertEqual([x.ts for x in c], [1, 2])
            with self.assertRaises(MarketError): m.quote("BTC/USD")
            with self.assertRaises(MarketError): m.candles("BTC/USD", 1, since=5)


if __name__ == "__main__":
    unittest.main()
