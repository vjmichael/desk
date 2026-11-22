import unittest
from unittest import mock
from desk import connectors as c
from desk.net import NetError


TOK = "123456789:AAExampleTokenExampleToken0123"


class SignatureTest(unittest.TestCase):
    def test_kraken_documented_vector(self):
        # Example published in Kraken's REST authentication documentation.
        sig = c.kraken_sign(
            "/0/private/AddOrder", 1616492376594,
            "nonce=1616492376594&ordertype=limit&pair=XBTUSD&price=37500&type=buy&volume=1.25",
            "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==")
        self.assertEqual(sig, "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==")


def patch(returns):
    return mock.patch.object(c, "http_json", side_effect=returns if isinstance(returns, list) else None,
                             return_value=None if isinstance(returns, list) else returns)


class KrakenTests(unittest.TestCase):
    def test_public_ok(self):
        data = {"error": [], "result": {"XXBTZUSD": {"b": ["64000.1", "1", "1"], "a": ["64000.5", "1", "1"]}}}
        with patch((200, data)):
            r = c.kraken_public_test()
        self.assertTrue(r["ok"]); self.assertIn("64000.10", r["message"])

    def test_public_network_error(self):
        with mock.patch.object(c, "http_json", side_effect=NetError("timed out")):
            self.assertFalse(c.kraken_public_test()["ok"])

    def test_private_needs_both(self):
        self.assertFalse(c.kraken_private_test("k", "")["ok"])

    def test_private_bad_secret_not_base64(self):
        r = c.kraken_private_test("k", "not base64 !!")
        self.assertFalse(r["ok"]); self.assertIn("base64", r["message"])

    def test_private_ok_signs_request(self):
        with patch((200, {"error": [], "result": {"ZUSD": "10.0", "XXBT": "0.1"}})) as m:
            r = c.kraken_private_test("KEY", "c2VjcmV0")
        self.assertTrue(r["ok"]); self.assertIn("2 asset", r["message"])
        method, url, headers, body = m.call_args[0]
        self.assertEqual(method, "POST"); self.assertTrue(url.endswith("/0/private/Balance"))
        self.assertEqual(headers["API-Key"], "KEY"); self.assertTrue(headers["API-Sign"])
        self.assertTrue(body.startswith(b"nonce="))

    def test_private_rejected_key(self):
        with patch((200, {"error": ["EAPI:Invalid key"]})):
            r = c.kraken_private_test("K", "c2VjcmV0")
        self.assertFalse(r["ok"]); self.assertIn("Invalid key", r["message"])
        self.assertNotIn("c2VjcmV0", r["message"])


class GrokTests(unittest.TestCase):
    def test_ok(self):
        with patch((200, {"data": [{"id": "grok-a"}, {"id": "grok-b"}]})) as m:
            r = c.grok_test("XKEY")
        self.assertTrue(r["ok"]); self.assertIn("grok-a", r["message"])
        self.assertEqual(m.call_args[0][2]["Authorization"], "Bearer XKEY")

    def test_rejected(self):
        with patch((401, {"error": "bad"})):
            self.assertFalse(c.grok_test("x")["ok"])

    def test_missing_key(self):
        self.assertFalse(c.grok_test("")["ok"])


class TelegramTests(unittest.TestCase):
    def test_token_ok_no_chat(self):
        with patch((200, {"ok": True, "result": {"username": "deskbot"}})):
            r = c.telegram_test(TOK, "", "Desk")
        self.assertTrue(r["ok"]); self.assertIn("Find chat ID", r["message"])

    def test_token_ok_sends_message(self):
        with patch([(200, {"ok": True, "result": {"username": "deskbot"}}), (200, {"ok": True})]) as m:
            r = c.telegram_test(TOK, "123456", "Desk")
        self.assertTrue(r["ok"]); self.assertEqual(m.call_count, 2)
        self.assertNotIn(TOK, r["message"])

    def test_malformed_token_is_rejected_before_any_network_call(self):
        for bad in ("abc", "123456789:short", "123456789:has space inside it_______", "tok\\u2026en:1234567890123456789012"):
            with mock.patch.object(c, "http_json") as m:
                r = c.telegram_test(bad, "", "Recovery")
                r2, chat = c.telegram_find_chat(bad, "Recovery")
            self.assertFalse(r["ok"]); self.assertIn("does not look right", r["message"])
            self.assertFalse(r2["ok"]); self.assertIsNone(chat)
            m.assert_not_called()

    def test_chat_id_must_be_a_number(self):
        with mock.patch.object(c, "http_json") as m:
            r = c.telegram_test(TOK, "not-a-number", "Desk")
        self.assertFalse(r["ok"]); self.assertIn("number", r["message"]); m.assert_not_called()

    def test_bad_token(self):
        with patch((401, {"ok": False})):
            self.assertFalse(c.telegram_test(TOK, "111", "Recovery")["ok"])

    def test_send_fails(self):
        with patch([(200, {"ok": True, "result": {"username": "b"}}), (400, {"ok": False, "description": "chat not found"})]):
            r = c.telegram_test(TOK, "999", "Desk")
        self.assertFalse(r["ok"]); self.assertIn("chat not found", r["message"])

    def test_find_chat(self):
        upd = {"ok": True, "result": [
            {"message": {"chat": {"type": "group", "id": -5}}},
            {"message": {"chat": {"type": "private", "id": 42}}}]}
        with patch((200, upd)):
            r, chat = c.telegram_find_chat(TOK, "Desk")
        self.assertTrue(r["ok"]); self.assertEqual(chat, "42")

    def test_find_chat_none(self):
        with patch((200, {"ok": True, "result": []})):
            r, chat = c.telegram_find_chat(TOK, "Desk")
        self.assertFalse(r["ok"]); self.assertIsNone(chat)


if __name__ == "__main__":
    unittest.main()
