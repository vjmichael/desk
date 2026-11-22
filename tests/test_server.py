import json, os, stat, tempfile, unittest, urllib.request, urllib.error
from unittest import mock
from desk.server import DeskServer, serve_in_thread
from desk.store import Store


def call(server, path, body=None, token=True, host=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Desk-Token"] = server.token
    if host:
        headers["Host"] = host
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (server.port, path),
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.server = DeskServer(self.store, port=0)
        serve_in_thread(self.server)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()

    def test_page_loads_without_token_and_holds_no_secrets(self):
        code, body = call(self.server, "/", token=False)
        self.assertEqual(code, 200); self.assertIn(b"Desk: connections", body)
        self.assertNotIn(self.server.token.encode(), body)

    def test_api_requires_token(self):
        self.assertEqual(call(self.server, "/api/status", token=False)[0], 403)
        self.assertEqual(call(self.server, "/api/save", {"values": {}}, token=False)[0], 403)

    def test_foreign_host_header_rejected(self):
        self.assertEqual(call(self.server, "/", token=False, host="evil.example")[0], 403)

    def test_save_never_returns_or_leaks_secret_and_file_is_private(self):
        code, _ = call(self.server, "/api/save", {"values": {"grok_key": "SUPERSECRET123"}})
        self.assertEqual(code, 200)
        code, body = call(self.server, "/api/status")
        self.assertNotIn(b"SUPERSECRET123", body)
        self.assertTrue(json.loads(body)["saved"]["grok_key"]); self.assertTrue(json.loads(body)["version"])
        mode = stat.S_IMODE(os.stat(self.store.path).st_mode)
        if os.name == "posix":
            self.assertEqual(mode, 0o600)

    def test_empty_values_do_not_wipe_saved_keys(self):
        call(self.server, "/api/save", {"values": {"grok_key": "KEEPME"}})
        call(self.server, "/api/save", {"values": {"grok_key": ""}})
        self.assertEqual(self.store.get("grok_key"), "KEEPME")

    def test_unknown_field_rejected(self):
        self.assertEqual(call(self.server, "/api/save", {"values": {"evil": "x"}})[0], 400)

    def test_tests_use_stored_secrets(self):
        call(self.server, "/api/save", {"values": {"grok_key": "GK"}})
        with mock.patch("desk.connectors.grok_test", return_value={"ok": True, "message": "fine"}) as m:
            code, body = call(self.server, "/api/test/grok", {})
        self.assertEqual(code, 200); m.assert_called_once_with("GK")
        self.assertEqual(call(self.server, "/api/test/nonsense", {})[0], 404)

    def test_find_chat_saves_id(self):
        call(self.server, "/api/save", {"values": {"telegram_desk_token": "TT"}})
        with mock.patch("desk.connectors.telegram_find_chat", return_value=({"ok": True, "message": "found"}, "777")):
            call(self.server, "/api/find_chat/desk", {})
        self.assertEqual(self.store.get("telegram_desk_chat_id"), "777")

    def test_a_crashing_test_returns_json_and_the_server_keeps_working(self):
        with mock.patch("desk.connectors.grok_test", side_effect=RuntimeError("boom SECRETTOKEN")):
            code, body = call(self.server, "/api/test/grok", {})
        self.assertEqual(code, 500)
        self.assertNotIn(b"SECRETTOKEN", body)
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(call(self.server, "/api/status")[0], 200)

    def test_oversize_body_rejected(self):
        self.assertEqual(call(self.server, "/api/save", {"values": {"grok_key": "x" * 70000}})[0], 413)


if __name__ == "__main__":
    unittest.main()
