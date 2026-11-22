import json, unittest
from desk import engine as E
from desk.scout import GrokResearch, responses_text
from tests.test_stage2 import Base, prop, T


def reply(text="BTC ETF inflows rose.", urls=("https://example.com/a",)):
    return 200, {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": text, "annotations": [{"type": "url_citation", "url": u} for u in urls]}]}]}


class ResearchClientTests(unittest.TestCase):
    def client(self, replies):
        self.calls = []

        def http(method, url, headers=None, body=None, timeout=20):
            self.calls.append((url, headers, json.loads(body)))
            return replies.pop(0)
        return GrokResearch(lambda: "KEY", lambda: "grok-4.3", http)

    def test_success_uses_both_search_tools(self):
        r = self.client([reply()]).digest(T)
        self.assertTrue(r.ok); self.assertIn("ETF", r.text); self.assertEqual(r.sources, ["https://example.com/a"])
        url, headers, body = self.calls[0]
        self.assertTrue(url.endswith("/responses")); self.assertEqual(headers["Authorization"], "Bearer KEY")
        self.assertEqual([t["type"] for t in body["tools"]], ["web_search", "x_search"])

    def test_errors_are_plain_and_hide_the_key(self):
        for status, word in ((401, "rejected"), (400, "refused"), (429, "rate limit"), (500, "HTTP 500")):
            r = self.client([(status, {})]).digest(T)
            self.assertFalse(r.ok); self.assertIn(word, r.error); self.assertNotIn("KEY", r.error)
        self.assertFalse(self.client([(200, {"output": []})]).digest(T).ok)
        self.assertFalse(GrokResearch(lambda: "", lambda: "m").digest(T).ok)

    def test_text_extraction_is_tolerant(self):
        self.assertEqual(responses_text("junk"), ("", []))
        t, u = responses_text({"output": [{"content": [{"type": "output_text", "text": "a"}]}], "citations": ["https://x.y", {"url": "https://z.w"}]})
        self.assertEqual((t, u), ("a", ["https://x.y", "https://z.w"]))


class FakeResearch:
    def __init__(self, ok=True, boom=False):
        self.ok, self.boom, self.calls = ok, boom, 0

    def digest(self, now):
        from desk.scout import ResearchResult
        self.calls += 1
        if self.boom:
            raise RuntimeError("x")
        return ResearchResult(self.ok, "Fed speaks at 14:00 UTC." if self.ok else "", ["https://e.com"], "" if self.ok else "research: down")


class EngineResearchTests(Base):
    def run_round(self, research):
        self.make([])
        self.eng.research = research
        return self.eng.cycle(force_scout=True)

    def test_digest_goes_into_the_brief_and_is_stored(self):
        self.run_round(FakeResearch())
        brief = self.scout.briefs[0]
        self.assertIn("Fed speaks", brief["fundamentals"]["untrusted_digest"])
        self.assertIn("Fed speaks", self.eng.status()["digest"]["text"])

    def test_research_failure_does_not_stop_scouting(self):
        for fr in (FakeResearch(ok=False), FakeResearch(boom=True)):
            self.scout_calls_before = 0
            self.run_round(fr)
            self.assertNotIn("fundamentals", self.scout.briefs[0])
            self.assertEqual(self.scout.calls, 1)
            self.assertTrue(self.eng.errors)

    def test_switch_off_skips_research(self):
        self.settings.research = False
        fr = FakeResearch()
        self.run_round(fr)
        self.assertEqual(fr.calls, 0)

    def test_setting_persists_and_ignores_junk(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(E.load_settings(d).research)
            self.assertFalse(E.save_settings(d, {"research": False}).research)
            E.save_settings(d, {"research": "yes"})
            self.assertFalse(E.load_settings(d).research)


if __name__ == "__main__":
    unittest.main()
