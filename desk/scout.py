"""The Grok scout: turns a market brief into structured trade proposals.

What it sees: the numbers in the brief plus whatever Grok already knows.
It does NOT fetch live X posts or web pages; that would need xAI's search
tools, which are not enabled here."""
import json
import re
import secrets
from dataclasses import dataclass, field

from . import net
from .gate import Proposal

XAI = "https://api.x.ai/v1"

SYSTEM = """You are the scouting analyst of a small paper-trading desk on Kraken spot markets.
You receive a JSON brief with per-symbol facts and the desk's rules. Find at most 3 long-only
opportunities worth considering over the next hours to days, or none.
Reply with ONE JSON object and nothing else:
{"proposals":[{"symbol":"BTC/USD","direction":"long","entry":<usd>,"stop":<usd>,"target":<usd>,
"horizon_hours":<number>,"confidence":<0 to 1>,"rationale":"<one or two sentences>"}],
"no_trade_reason":"<why none, or empty>"}
Rules: use only symbols that are in the brief. Spot, long only. Entry must be close to the
current ask. Stop must be below entry and target above. The target must clear fees and spread
(see rules) with at least the minimum reward-to-risk. Base the rationale on the supplied
numbers; say so if you rely on general knowledge. Returning no proposals is a good answer
when nothing is clearly worth it. Do not invent prices or news."""


@dataclass
class ScoutResult:
    ok: bool
    proposals: list = field(default_factory=list)
    note: str = ""
    error: str = ""
    skipped: int = 0


def extract_json(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(text[i:j + 1])
        except ValueError:
            return None
    return None


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def parse_proposals(text, now, whitelist, max_hold_hours=48):
    data = extract_json(text)
    if not isinstance(data, dict):
        return ScoutResult(False, error="Grok did not return valid JSON")
    items = data.get("proposals")
    items = items if isinstance(items, list) else []
    out, skipped = [], 0
    for it in items[:10]:
        if not isinstance(it, dict):
            skipped += 1
            continue
        sym = it.get("symbol")
        direction = str(it.get("direction", "long")).lower()
        entry, stop, target = _num(it.get("entry")), _num(it.get("stop")), _num(it.get("target"))
        if sym not in whitelist or direction not in ("long", "buy") or None in (entry, stop, target):
            skipped += 1
            continue
        horizon = min(max(_num(it.get("horizon_hours")) or 24.0, 1.0), float(max_hold_hours))
        conf = _num(it.get("confidence"))
        out.append(Proposal(
            id="p-" + secrets.token_hex(4), symbol=sym, direction="long",
            entry=entry, stop=stop, target=target, expires_at=now + horizon * 3600,
            origin="ai", confidence=None if conf is None else min(max(conf, 0.0), 1.0),
            rationale=str(it.get("rationale", ""))[:500]))
    note = data.get("no_trade_reason")
    return ScoutResult(True, out, str(note)[:300] if note else "", "", skipped)


class GrokScout:
    def __init__(self, get_key, get_model, get_whitelist, get_max_hold, http=None):
        self.get_key, self.get_model = get_key, get_model
        self.get_whitelist, self.get_max_hold = get_whitelist, get_max_hold
        self.http = http or net.http_json

    def propose(self, brief, now):
        key = self.get_key()
        if not key:
            return ScoutResult(False, error="Grok key is not saved")
        body = {"model": self.get_model(), "temperature": 0.2,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": json.dumps(brief)}]}
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        try:
            status, data = self.http("POST", XAI + "/chat/completions", headers,
                                     json.dumps(dict(body, response_format={"type": "json_object"})).encode(), 150)
            if status in (400, 422):  # some models reject response_format: retry plainly
                status, data = self.http("POST", XAI + "/chat/completions", headers, json.dumps(body).encode(), 150)
        except net.NetError as e:
            return ScoutResult(False, error="Grok: %s" % e)
        if status in (401, 403):
            return ScoutResult(False, error="xAI rejected the key (HTTP %s)" % status)
        if status == 404:
            return ScoutResult(False, error="xAI does not know the model '%s'" % self.get_model())
        if status == 429:
            return ScoutResult(False, error="xAI rate limit or quota reached (HTTP 429)")
        if status != 200 or not isinstance(data, dict):
            return ScoutResult(False, error="xAI answered HTTP %s" % status)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ScoutResult(False, error="xAI reply was not in the expected format")
        return parse_proposals(text, now, tuple(self.get_whitelist()), self.get_max_hold())
