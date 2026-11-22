import os, ssl, unittest, urllib.error
from unittest import mock
from desk import net


def cert_error(msg="unable to get local issuer certificate"):
    err = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] " + msg)
    err.verify_message = msg
    return urllib.error.URLError(err)


class NetTests(unittest.TestCase):
    def test_bundle_exists_and_loads(self):
        self.assertTrue(os.path.exists(net.BUNDLE))
        ssl.create_default_context(cafile=net.BUNDLE)  # raises if the file is unusable

    def test_normal_success_makes_one_attempt(self):
        with mock.patch.object(net, "_request", return_value=(200, b'{"ok": true}')) as m:
            self.assertEqual(net.http_json("GET", "https://x.example/"), (200, {"ok": True}))
        self.assertEqual(m.call_count, 1)

    def test_cert_failure_retries_with_bundle_and_keeps_verification(self):
        with mock.patch.object(net, "_request", side_effect=[cert_error(), (200, b'{"ok": true}')]) as m:
            status, data = net.http_json("POST", "https://api.telegram.org/botTOKEN/getMe", body=b"{}")
        self.assertEqual((status, data), (200, {"ok": True}))
        ctx = m.call_args_list[1][0][2]
        self.assertIsInstance(ctx, ssl.SSLContext)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_cert_failure_even_with_bundle_gives_help_without_token(self):
        with mock.patch.object(net, "_request", side_effect=[cert_error(), cert_error()]):
            with self.assertRaises(net.NetError) as cm:
                net.http_json("POST", "https://api.telegram.org/botSECRETTOKEN/getMe")
        self.assertIn("certificate", str(cm.exception))
        self.assertIn("unable to get local issuer certificate", str(cm.exception))
        self.assertNotIn("SECRETTOKEN", str(cm.exception))

    def test_other_connection_errors_do_not_retry(self):
        err = urllib.error.URLError(ConnectionRefusedError())
        with mock.patch.object(net, "_request", side_effect=err) as m:
            with self.assertRaises(net.NetError) as cm:
                net.http_json("GET", "https://x.example/")
        self.assertEqual(m.call_count, 1)
        self.assertIn("could not connect", str(cm.exception))

    def test_invalid_url_characters_become_a_clear_error_without_the_token(self):
        import http.client
        boom = http.client.InvalidURL("URL can't contain control characters. '/botSECRETTOKEN/getMe'")
        for exc in (boom, UnicodeEncodeError("ascii", "x", 0, 1, "bad"), http.client.IncompleteRead(b"")):
            with mock.patch.object(net, "_request", side_effect=exc):
                with self.assertRaises(net.NetError) as cm:
                    net.http_json("POST", "https://api.telegram.org/botSECRETTOKEN/getMe")
            self.assertNotIn("SECRETTOKEN", str(cm.exception))

    def test_http_error_status_is_returned_not_raised(self):
        with mock.patch.object(net, "_request", return_value=(401, b'{"ok": false}')):
            self.assertEqual(net.http_json("GET", "https://x.example/")[0], 401)

    def test_non_json_body_is_returned_as_text(self):
        with mock.patch.object(net, "_request", return_value=(502, b"<html>bad gateway</html>")):
            status, data = net.http_json("GET", "https://x.example/")
        self.assertEqual(status, 502); self.assertIn("bad gateway", data)


if __name__ == "__main__":
    unittest.main()
