import os, tempfile, unittest
from desk.__main__ import load_token
from desk.engine import build_engine
from desk.store import Store


class ServerModeTests(unittest.TestCase):
    def test_token_persists_and_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "token")
            a = load_token(p)
            self.assertEqual(a, load_token(p))
            self.assertGreaterEqual(len(a), 20)
            if os.name == "posix":
                self.assertEqual(os.stat(p).st_mode & 0o077, 0)

    def test_resume_only_if_it_was_running(self):
        with tempfile.TemporaryDirectory() as d:
            eng = build_engine(Store(d))
            self.assertFalse(eng.resume())
            eng.account.set_meta("wanted_running", "1")
            self.assertTrue(eng.resume())
            self.assertTrue(eng.running)
            eng.stop()
            self.assertEqual(eng.account.get_meta("wanted_running"), "0")
            self.assertFalse(eng.resume())

    def test_start_remembers_it_should_run(self):
        with tempfile.TemporaryDirectory() as d:
            eng = build_engine(Store(d))
            eng.start()
            self.assertEqual(eng.account.get_meta("wanted_running"), "1")
            eng.stop()


if __name__ == "__main__":
    unittest.main()
