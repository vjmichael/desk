import json, os, tempfile, time, unittest
from types import SimpleNamespace
from desk import majvaj as MV, verdict as V, watchdog as W
from desk.ftapi import FreqtradeAPI, FtError
from desk.market import Candle
from desk.scout import ResearchResult
from desk.engine import Settings

NOW = 1_800_000_000.0


class Notes:
    def __init__(self):
        self.d, self.r = [], []
    def desk(self, t): self.d.append(t)
    def recovery(self, t): self.r.append(t)


class FakeResearch:
    def __init__(self, ok=True):
        self.ok = ok
    def digest(self, now):
        return ResearchResult(self.ok, "FOMC decision at 18:00 UTC today." if self.ok else "", ["https://e.com"], "" if self.ok else "research: down")


def chat_reply(obj):
    return 200, {"choices": [{"message": {"content": json.dumps(obj) if not isinstance(obj, str) else obj}}]}


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "majvaj", "verdict.json")
        self.notes = Notes()

    def tearDown(self):
        self.tmp.cleanup()

    def svc(self, replies, research=None, key="KEY"):
        calls = []
        def http(method, url, headers=None, body=None, timeout=20):
            calls.append(json.loads(body)); return replies.pop(0)
        s = V.VerdictService(self.tmp.name, self.out, research or FakeResearch(), lambda: key, lambda: Settings(),
                             self.notes, http=http, clock=lambda: NOW)
        s.calls = calls
        return s

    def test_writes_verdict_file_with_code_set_expiry(self):
        s = self.svc([chat_reply({"verdict": "stand_down", "reasons": ["FOMC 18:00 UTC"]})])
        self.assertEqual(s.run_once(), "stand_down")
        v = json.load(open(self.out))
        self.assertEqual(v["verdict"], "stand_down")
        self.assertAlmostEqual(v["expires"], NOW + s.expiry_seconds(NOW))
        self.assertGreater(v["expires"], NOW + 3600)
        self.assertTrue(any("STAND DOWN" in m for m in self.notes.d))
        self.assertIn("untrusted", s.calls[0]["messages"][0]["content"])

    def test_odd_classifier_answers(self):
        self.assertEqual(V.normalise({"verdict": "YOLO", "reasons": ["x"]})[0], "caution")
        self.assertEqual(V.normalise("nope"), (None, []))
        s = self.svc([chat_reply("not json")])
        self.assertIsNone(s.run_once())
        self.assertFalse(os.path.exists(self.out))  # nothing written: the old verdict simply expires

    def test_research_failure_alerts_after_three(self):
        s = self.svc([], research=FakeResearch(ok=False))
        for _ in range(3):
            self.assertIsNone(s.run_once())
        self.assertEqual(len(self.notes.r), 1)
        self.assertEqual(len([h for h in s.history() if not h["ok"]]), 3)

    def test_only_changes_are_announced(self):
        s = self.svc([chat_reply({"verdict": "normal", "reasons": []}), chat_reply({"verdict": "normal", "reasons": []})])
        s.run_once(); s.run_once()
        self.assertEqual(len(self.notes.d), 1)


class ScheduleTests(unittest.TestCase):
    def test_times_and_boundaries(self):
        mins = V.parse_times(["13:30", "0:00", "07:00"])
        self.assertEqual(mins, [0, 420, 810])
        day = NOW - NOW % 86400
        self.assertEqual(V.next_scheduled(day + 3600, mins), day + 420 * 60)          # 01:00 -> 07:00
        self.assertEqual(V.last_scheduled(day + 3600, mins), day)                     # 01:00 -> 00:00
        self.assertEqual(V.next_scheduled(day + 15 * 3600, mins), day + 86400)        # 15:00 -> tomorrow 00:00
        self.assertEqual(V.last_scheduled(day + 60, [810]), day - 86400 + 810 * 60)   # before today's only time
        for bad in ([], ["25:00"], ["7:61"], ["x"], list("123456789")):
            with self.assertRaises(ValueError):
                V.parse_times(bad)

    def test_runs_only_when_a_scheduled_time_has_passed(self):
        tmp = tempfile.TemporaryDirectory()
        day = NOW - NOW % 86400
        clock = [day + 8 * 3600]  # 08:00 UTC
        s = V.VerdictService(tmp.name, os.path.join(tmp.name, "v.json"), FakeResearch(), lambda: "K", lambda: Settings(),
                             Notes(), http=lambda *a, **k: chat_reply({"verdict": "normal", "reasons": []}), clock=lambda: clock[0])
        self.assertTrue(s.due(clock[0]))            # never ran: run now
        s._meta("last_run", clock[0])
        self.assertFalse(s.due(clock[0] + 3 * 3600))  # 11:00, nothing scheduled since 08:00
        self.assertTrue(s.due(day + 13.5 * 3600 + 60))  # 13:31: the New York pre-open check is due
        self.assertEqual(s.run_once(), "normal")
        v = json.load(open(os.path.join(tmp.name, "v.json")))
        self.assertEqual(v["expires"], day + 13.5 * 3600 + V.SLACK)  # valid until the next check + 1 h
        s.set_schedule(["06:00"])
        self.assertEqual(s.schedule_text(), ["06:00"])
        tmp.cleanup()


class MoneyAndBooksTests(unittest.TestCase):
    def test_faq_example(self):
        closed = [(i, 100.0) for i in range(5)] + [(10, -20.0)]
        self.assertEqual(MV.ledger(100.0, closed), (80.0, 500.0))

    def test_money_validation(self):
        m, _ = MV.clean_money({"bankroll": 500, "goal_mode": "opportunity", "goal_reduce_pct": 30, "goal_keep_pct": 60,
                               "goal_sessions": ["Asia/Tokyo"]})
        self.assertEqual((m["goal_mode"], m["goal_keep_pct"], m["goal_sessions"]), ("opportunity", 60, ["Asia/Tokyo"]))
        self.assertEqual(MV.clean_money({}, {"goal_mode": "reduce"})[0]["goal_mode"], "opportunity")
        self.assertEqual(MV.clean_money({})[0]["goal_mode"], "stop")
        for bad in ({"bankroll": 5}, {"goal_mode": "yolo"}, {"goal_reduce_pct": 100}, {"daily_loss_limit_pct": 50},
                    {"timezone": "Mars/Base"}, {"bankroll": "abc"}, {"goal_keep_pct": 101}, {"goal_sessions": ["Mars"]}):
            m, err = MV.clean_money(bad)
            self.assertIsNone(m, bad); self.assertTrue(err)

    def test_verdict_evaluation(self):
        cs = {"BTC/USD": [Candle(int(NOW) + h * 3600, 100, 100, 100, 100 + h, 1) for h in range(10)]}
        vs = [{"ts": NOW, "verdict": "normal", "expires": NOW + 7200}, {"ts": NOW + 3 * 3600, "verdict": "stand_down", "expires": NOW + 6 * 3600}]
        ev = MV.evaluate_verdicts(vs, cs, NOW + 9 * 3600)
        self.assertEqual(ev["normal"]["count"], 1)
        self.assertAlmostEqual(ev["normal"]["hours"], 2.0)
        self.assertAlmostEqual(ev["stand_down"]["avg_move_pct"], (106 / 103 - 1) * 100)


class FtApiTests(unittest.TestCase):
    def test_basic_auth_and_errors(self):
        seen = []
        def http(method, url, headers=None, body=None, timeout=20):
            seen.append((method, url, headers)); return (401, {}) if "profit" in url else (200, {"status": "pong"})
        api = FreqtradeAPI("http://127.0.0.1:8080", "u", "p", http)
        self.assertEqual(api.ping(), {"status": "pong"})
        self.assertTrue(seen[0][2]["Authorization"].startswith("Basic "))
        self.assertEqual(seen[0][1], "http://127.0.0.1:8080/api/v1/ping")
        with self.assertRaises(FtError):
            api.profit()
        self.assertFalse(api.summary()["ok"])


class WatchdogTests(unittest.TestCase):
    OK = {"ft_ok": True, "ft_age_min": 1, "desk_ok": True, "verdict_left_min": 60, "disk_pct": 40}

    def test_healthy_does_nothing(self):
        a, al, healthy, s = W.assess(self.OK, {}, NOW)
        self.assertEqual((a, al, healthy), ([], [], True))

    def test_restart_on_second_failure_with_cap(self):
        bad = dict(self.OK, ft_ok=False)
        a, al, h, s = W.assess(bad, {}, NOW)
        self.assertEqual(a, []); self.assertFalse(h)  # first miss: wait
        restarts = 0
        for i in range(1, 8):
            a, al, h, s = W.assess(bad, s, NOW + i * 120)
            restarts += a.count(("restart", "majvaj-freqtrade"))
        self.assertEqual(restarts, W.MAX_RESTARTS_PER_HOUR)
        a, al, h, s = W.assess(self.OK, s, NOW + 2000)
        self.assertTrue(any("Recovered" in x for x in al))

    def test_stuck_bot_counts_as_failure(self):
        stuck = dict(self.OK, ft_age_min=45)
        _, _, _, s = W.assess(stuck, {}, NOW)
        a, al, h, s = W.assess(stuck, s, NOW + 120)
        self.assertIn(("restart", "majvaj-freqtrade"), a)
        self.assertTrue(any("stuck" in x for x in al))

    def test_expired_verdict_alerts_rarely_and_disk_prunes(self):
        a, al, h, s = W.assess(dict(self.OK, verdict_left_min=-5), {}, NOW)
        self.assertTrue(any("standing down" in x for x in al))
        a, al, h, s = W.assess(dict(self.OK, verdict_left_min=-5), s, NOW + 600)
        self.assertEqual(al, [])
        a, al, h, s = W.assess(dict(self.OK, disk_pct=95), s, NOW + 700)
        self.assertIn(("prune",), a); self.assertFalse(h)

    def test_commands_only_from_owner(self):
        class Store:
            def get(self, k): return {"telegram_recovery_token": "1:x", "telegram_recovery_chat_id": "42"}.get(k, "")
        class Ft:
            paused = False
            def pause(self): Ft.paused = True
            def start(self): pass
        sent = []
        updates = {"ok": True, "result": [
            {"update_id": 5, "message": {"chat": {"id": 99}, "text": "/pause"}},
            {"update_id": 6, "message": {"chat": {"id": 42}, "text": "/pause"}}]}
        orig = W.connectors._tg
        W.connectors._tg = lambda tok, m, p=None: (200, updates) if m == "getUpdates" else (sent.append(p) or (200, {"ok": True}))
        try:
            st = {}
            replies = W.handle_commands(Store(), Ft(), st, dict(self.OK, ft_state="running"), lambda c: None)
        finally:
            W.connectors._tg = orig
        self.assertEqual(len(replies), 1); self.assertTrue(Ft.paused)
        self.assertEqual(st["tg_offset"], 7); self.assertEqual(sent[0]["chat_id"], "42")


if __name__ == "__main__":
    unittest.main()
