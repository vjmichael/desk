"""Connection tests for Kraken, Grok (xAI) and the two Telegram bots.
Each returns {"ok": bool, "message": str}. Secrets never appear in messages."""
import base64
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlencode

from . import net

KRAKEN = "https://api.kraken.com"
XAI = "https://api.x.ai/v1"
TELEGRAM = "https://api.telegram.org"


_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{20,}$")
_CHAT_RE = re.compile(r"^-?\d{3,20}$")


def _bad_token(label):
    return _res(False, "The %s bot token does not look right. It should look like "
                       "123456789:ABCdef... (digits, a colon, then letters and digits) with no "
                       "spaces. Copy it again from @BotFather." % label)


def _res(ok, message):
    return {"ok": bool(ok), "message": message}


def http_json(*a, **kw):  # indirection so tests can patch one name
    return net.http_json(*a, **kw)


# ---------------------------------------------------------------- Kraken
def kraken_sign(path, nonce, postdata, secret_b64):
    """Kraken's documented signature: HMAC-SHA512 of path + SHA256(nonce +
    postdata), keyed with the base64-decoded API secret."""
    digest = hashlib.sha256((str(nonce) + postdata).encode()).digest()
    mac = hmac.new(base64.b64decode(secret_b64), path.encode() + digest, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def kraken_public_test():
    try:
        status, data = http_json("GET", KRAKEN + "/0/public/Ticker?pair=XBTUSD")
    except net.NetError as e:
        return _res(False, "Kraken public data: %s" % e)
    if status != 200 or not isinstance(data, dict) or data.get("error"):
        return _res(False, "Kraken public data failed (HTTP %s): %s" % (status, _errs(data)))
    try:
        t = next(iter(data["result"].values()))
        bid, ask = float(t["b"][0]), float(t["a"][0])
    except (KeyError, StopIteration, ValueError, TypeError, IndexError):
        return _res(False, "Kraken answered, but the price data was not in the expected format.")
    return _res(True, "Kraken public data works. BTC/USD bid %.2f, ask %.2f." % (bid, ask))


def kraken_private_test(key, secret):
    if not key or not secret:
        return _res(False, "Enter and save both the Kraken API key and secret first.")
    try:
        nonce = int(time.time() * 1000)
        body = urlencode({"nonce": nonce})
        path = "/0/private/Balance"
        headers = {
            "API-Key": key,
            "API-Sign": kraken_sign(path, nonce, body, secret),
            "Content-Type": "application/x-www-form-urlencoded",
        }
    except (ValueError, TypeError):
        return _res(False, "The Kraken secret is not valid base64. Re-copy it from Kraken.")
    try:
        status, data = http_json("POST", KRAKEN + path, headers, body.encode())
    except net.NetError as e:
        return _res(False, "Kraken private test: %s" % e)
    if status != 200 or not isinstance(data, dict):
        return _res(False, "Kraken private test failed (HTTP %s)." % status)
    if data.get("error"):
        return _res(False, "Kraken rejected the key: %s. Check the key, secret and that "
                           "'Query funds' is enabled." % _errs(data))
    n = len(data.get("result") or {})
    return _res(True, "Kraken key works (read-only balance query succeeded; %d asset balance(s)). "
                      "Make sure the key has NO withdrawal permission; I cannot check that for you." % n)


# ------------------------------------------------------------------ Grok
def grok_test(key):
    if not key:
        return _res(False, "Enter and save the Grok (xAI) API key first.")
    try:
        status, data = http_json("GET", XAI + "/models", {"Authorization": "Bearer " + key})
    except net.NetError as e:
        return _res(False, "Grok test: %s" % e)
    if status in (401, 403):
        return _res(False, "xAI rejected the key (HTTP %s). Check you copied the whole key." % status)
    if status != 200 or not isinstance(data, dict):
        return _res(False, "xAI answered with HTTP %s; the key could not be confirmed." % status)
    ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    if not ids:
        return _res(True, "xAI accepted the key, but listed no models.")
    return _res(True, "Grok key works. %d model(s) available, e.g. %s." % (len(ids), ", ".join(ids[:4])))


# -------------------------------------------------------------- Telegram
def _tg(token, method, params=None):
    body = json.dumps(params or {}).encode()
    return http_json("POST", "%s/bot%s/%s" % (TELEGRAM, token, method),
                     {"Content-Type": "application/json"}, body)


def telegram_test(token, chat_id, label):
    if not token:
        return _res(False, "Enter and save the %s bot token first." % label)
    if not _TOKEN_RE.match(token):
        return _bad_token(label)
    if chat_id and not _CHAT_RE.match(chat_id):
        return _res(False, "The %s chat ID should be a number such as 123456789. Clear it and "
                           "press 'Find chat ID' instead." % label)
    try:
        status, data = _tg(token, "getMe")
    except net.NetError as e:
        return _res(False, "Telegram test: %s" % e)
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        return _res(False, "Telegram rejected the %s bot token. Re-copy it from @BotFather." % label)
    name = (data.get("result") or {}).get("username", "?")
    if not chat_id:
        return _res(True, "%s bot token works (@%s). Chat ID not set yet: send the bot any "
                          "message in Telegram, then press 'Find chat ID'." % (label, name))
    try:
        status, data = _tg(token, "sendMessage",
                           {"chat_id": chat_id, "text": "Desk test: the %s bot can reach you." % label})
    except net.NetError as e:
        return _res(False, "Telegram send: %s" % e)
    if status == 200 and isinstance(data, dict) and data.get("ok"):
        return _res(True, "%s bot works (@%s). A test message was sent to you." % (label, name))
    return _res(False, "Token is valid (@%s) but sending failed: %s. Message the bot first, "
                       "then use 'Find chat ID'." % (name, _tg_desc(data)))


def telegram_find_chat(token, label):
    """Return (result, chat_id_or_None) after the user has messaged the bot."""
    if not token:
        return _res(False, "Enter and save the %s bot token first." % label), None
    if not _TOKEN_RE.match(token):
        return _bad_token(label), None
    try:
        status, data = _tg(token, "getUpdates", {"timeout": 0, "limit": 20})
    except net.NetError as e:
        return _res(False, "Telegram: %s" % e), None
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        return _res(False, "Telegram rejected the %s bot token." % label), None
    chat = None
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or upd.get("edited_message") or {}
        c = msg.get("chat") or {}
        if c.get("type") == "private" and c.get("id") is not None:
            chat = str(c["id"])
            break
    if chat is None:
        return _res(False, "No message found. Open the %s bot in Telegram, press Start / send "
                           "'hello', then try again." % label), None
    return _res(True, "Found your chat ID for the %s bot and saved it." % label), chat


def _errs(data):
    if isinstance(data, dict) and data.get("error"):
        return "; ".join(str(e) for e in data["error"][:3])
    return "no details"


def _tg_desc(data):
    if isinstance(data, dict) and data.get("description"):
        return str(data["description"])[:120]
    return "unknown reason"
