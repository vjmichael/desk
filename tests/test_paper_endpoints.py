import json, tempfile, unittest
from desk.server import DeskServer, serve_in_thread
from desk.store import Store
from desk.engine import build_engine
from tests.test_server import call


class PaperEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.server = DeskServer(self.store, port=0, engine_factory=build_engine)
        serve_in_thread(self.server)

    def tearDown(self):
        eng = self.server._engine
        if eng is not None and eng.running:
            eng.stop()
        self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()

    def test_page_serves_without_token_and_has_no_secret(self):
        code, body = call(self.server, "/paper", token=False)
        self.assertEqual(code, 200)
        self.assertIn(b"Paper trading", body)
        self.assertNotIn(self.server.token.encode(), body)

    def test_status_needs_token(self):
        code, _ = call(self.server, "/api/paper/status", token=False)
        self.assertEqual(code, 403)

    def test_status_shape(self):
        code, body = call(self.server, "/api/paper/status")
        d = json.loads(body)
        self.assertEqual(code, 200)
        for k in ("running", "equity", "cash", "open", "closed", "decisions", "errors", "settings", "version"):
            self.assertIn(k, d)
        self.assertFalse(d["running"])
        self.assertEqual(d["equity"], 1000.0)

    def test_actions_need_token(self):
        code, _ = call(self.server, "/api/paper/start", {}, token=False)
        self.assertEqual(code, 403)

    def test_unknown_action_404(self):
        code, _ = call(self.server, "/api/paper/nope", {})
        self.assertEqual(code, 404)

    def test_scout_now_refused_when_stopped(self):
        _, body = call(self.server, "/api/paper/scout_now", {})
        self.assertFalse(json.loads(body)["ok"])

    def test_reset_validates_equity(self):
        _, body = call(self.server, "/api/paper/reset", {"equity": 5})
        self.assertFalse(json.loads(body)["ok"])
        _, body = call(self.server, "/api/paper/reset", {"equity": "abc"})
        self.assertFalse(json.loads(body)["ok"])
        _, body = call(self.server, "/api/paper/reset", {"equity": 2500})
        self.assertTrue(json.loads(body)["ok"])
        _, body = call(self.server, "/api/paper/status")
        self.assertEqual(json.loads(body)["equity"], 2500.0)

    def test_settings_saved_and_clamped(self):
        call(self.server, "/api/paper/settings", {"scout_minutes": 30, "max_scout_calls_per_day": 9999, "grok_model": "grok-4.3"})
        _, body = call(self.server, "/api/paper/status")
        s = json.loads(body)["settings"]
        self.assertEqual(s["scout_minutes"], 30)
        self.assertLessEqual(s["max_scout_calls_per_day"], 200)

    def test_settings_rejects_bad_model_name(self):
        call(self.server, "/api/paper/settings", {"grok_model": "bad name; rm -rf"})
        _, body = call(self.server, "/api/paper/status")
        self.assertEqual(json.loads(body)["settings"]["grok_model"], "grok-4.3")


if __name__ == "__main__":
    unittest.main()


class SessionTests(unittest.TestCase):
    def test_sessions_and_intervals(self):
        from desk import engine as E
        day = 1_800_000_000 - 1_800_000_000 % 86400
        at = lambda h: day + h * 3600
        self.assertEqual(E.session_at(at(3))[0], "Asia/Tokyo")
        self.assertEqual(E.session_at(at(9))[0], "London")
        self.assertEqual(E.session_at(at(14))[0], "London/New York overlap")
        self.assertEqual(E.session_at(at(18))[0], "New York")
        self.assertEqual(E.session_at(at(23))[0], "Quiet (late US)")
        s = E.Settings()
        self.assertEqual(E.scout_interval_minutes(at(14), s), 30)
        self.assertEqual(E.scout_interval_minutes(at(23), s), 180)
        s.session_scouting = False
        self.assertEqual(E.scout_interval_minutes(at(14), s), 60)
