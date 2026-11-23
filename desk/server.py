"""Local web server for key entry and connection tests.
Listens on 127.0.0.1 only. API calls need the per-run token printed at start,
and the Host header must be local, which blocks other websites from driving it."""
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, connectors, engine as engine_mod, research as research_mod
from .store import Store, SECRET_FIELDS

APP_PATH = os.path.join(os.path.dirname(__file__), "majvaj.html")  # the whole app: Desk, FreqAI, Scout, Paper, Keys tabs
MAX_BODY = 64 * 1024

TELEGRAM_BOTS = {
    "desk": ("telegram_desk_token", "telegram_desk_chat_id", "Desk"),
    "recovery": ("telegram_recovery_token", "telegram_recovery_chat_id", "Recovery"),
}


class DeskServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, store, port=8765, token=None, engine_factory=None, research_factory=None, majvaj=None):
        self.store = store
        self.majvaj = majvaj
        self.token = token or secrets.token_urlsafe(24)
        self._engine, self._engine_lock = None, threading.Lock()
        self._engine_factory = engine_factory or engine_mod.build_engine
        self._research, self._research_factory = None, research_factory or research_mod.Research
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def research(self):
        with self._engine_lock:
            if self._research is None:
                self._research = self._research_factory(self.store.dir)
            return self._research

    @property
    def engine(self):
        with self._engine_lock:
            if self._engine is None:
                self._engine = self._engine_factory(self.store)
            return self._engine

    @property
    def port(self):
        return self.server_address[1]

    def live_now(self):
        mv = self.majvaj
        return mv is not None and mv.mode.state().get("mode") in ("live", "live_exit_only")

    def key_action(self, action, body):
        """Key list actions from the Keys page. Never returns a key value."""
        s = self.store
        eid = str(body.get("id") or "")
        if action == "add":
            return s.add(str(body.get("service") or ""), body.get("label"), body.get("values") or {})
        svc, active = s.entry(eid)
        if svc is None:
            return {"ok": False, "message": "That key no longer exists. Reload the page."}
        if svc == "kraken_trade" and self.live_now() and (active or action == "use") and action in ("edit", "use", "delete"):
            return {"ok": False, "message": "The bot is trading live with the trade key in use. Switch back to paper "
                                            "first (Maj Vaj page), then change it."}
        if action == "edit":
            return s.edit(eid, body.get("label"), body.get("values") or {})
        if action == "use":
            return s.use(eid)
        if action == "delete":
            return s.delete(eid)
        if action == "test":
            return self.test_values(*s.find(eid))
        if action == "find_chat" and svc.startswith("telegram_"):
            bot = svc[len("telegram_"):]
            tok, chat, label = TELEGRAM_BOTS[bot]
            res, chat_id = connectors.telegram_find_chat(s.find(eid)[1].get(tok, ""), label)
            if chat_id:
                s.edit(eid, values={chat: chat_id})
            return res
        return {"ok": False, "message": "Unknown action."}

    def test_values(self, svc, v):
        """Run the connection test for one saved key (by its values, server-side only)."""
        if svc == "kraken":
            return connectors.kraken_private_test(v.get("kraken_key", ""), v.get("kraken_secret", ""))
        if svc == "kraken_trade":
            return connectors.kraken_private_test(v.get("kraken_trade_key", ""), v.get("kraken_trade_secret", ""))
        if svc == "grok":
            return connectors.grok_test(v.get("grok_key", ""))
        if svc == "fred":
            return connectors.fred_test(v.get("fred_key", ""))
        if svc and svc.startswith("telegram_"):
            tok, chat, label = TELEGRAM_BOTS[svc[len("telegram_"):]]
            return connectors.telegram_test(v.get(tok, ""), v.get(chat, ""), label)
        return {"ok": False, "message": "Unknown service."}

    def run_test(self, name):
        s = self.store
        if name == "kraken_public":
            return connectors.kraken_public_test()
        if name == "kraken_private":
            return connectors.kraken_private_test(s.get("kraken_key"), s.get("kraken_secret"))
        if name == "kraken_trade":
            return connectors.kraken_private_test(s.get("kraken_trade_key"), s.get("kraken_trade_secret"))
        if name == "grok":
            return connectors.grok_test(s.get("grok_key"))
        if name == "fred":
            return connectors.fred_test(s.get("fred_key"))
        for bot, (tok, chat, label) in TELEGRAM_BOTS.items():
            if name == "telegram_" + bot:
                token, chat_id = s.get(tok), s.get(chat)
                if token and not chat_id:  # you must message the bot first; then we can find the chat ourselves
                    _, found = connectors.telegram_find_chat(token, label)
                    if found:
                        s.update({chat: found})
                        chat_id = found
                return connectors.telegram_test(token, chat_id, label)
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "Desk/0.1"

    def log_message(self, *a):  # keep keys and tokens out of logs
        pass

    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        port = self.server.port
        return host in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def _authorized(self):
        t = self.headers.get("X-Desk-Token") or ""
        return secrets.compare_digest(t, self.server.token)

    def _guarded(self, fn):
        try:
            fn()
        except Exception as e:  # never crash silently; never print str(e) (may hold a token)
            where = (" on %s" % e.filename) if isinstance(e, OSError) and isinstance(e.filename, str) else ""  # a path, never a secret
            sys.stderr.write("Desk: internal error (%s%s) while handling %s\n" % (type(e).__name__, where, self.path.split('?')[0]))
            try:
                self._send(500, {"ok": False, "message": "The app hit an internal error (%s%s)." % (type(e).__name__, where)})
            except Exception:
                pass

    def do_GET(self):
        self._guarded(self._get)

    def do_POST(self):
        self._guarded(self._post)

    def _get(self):
        if not self._host_ok():
            return self._send(403, {"ok": False, "error": "forbidden host"})
        path = self.path.split("?")[0]
        if path in ("/", "/majvaj"):
            with open(APP_PATH, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/majvaj/status":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            mv = self.server.majvaj
            if mv is None:
                return self._send(200, {"ok": False, "error": "Maj Vaj (the Freqtrade bot) is not set up on this machine."})
            return self._send(200, dict(mv.status(), ok=True, version=__version__))
        if path == "/api/majvaj/pairs":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            mv = self.server.majvaj
            if mv is None:
                return self._send(200, {"ok": False, "error": "Maj Vaj is not set up on this machine."})
            refresh = "refresh=1" in (self.path.split("?", 1) + [""])[1]
            return self._send(200, dict(mv.pairs_view(refresh), ok=True))
        if path == "/api/history/status":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            return self._send(200, dict(self.server.research.status(), ok=True))
        if path == "/api/keys":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            return self._send(200, {"ok": True, "services": self.server.store.listing()})
        if path == "/api/status":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            st = self.server.store
            return self._send(200, {"ok": True, "saved": st.saved(), "saved_at": st.saved_at(), "version": __version__})
        self._send(404, {"ok": False, "error": "not found"})

    def _post(self):
        if not self._host_ok():
            return self._send(403, {"ok": False, "error": "forbidden host"})
        if not self._authorized():
            return self._send(403, {"ok": False, "error": "bad token"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"ok": False, "error": "too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"ok": False, "error": "bad request"})
        path = self.path.split("?")[0]
        store = self.server.store
        if path.startswith("/api/keys/"):
            return self._send(200, self.server.key_action(path[len("/api/keys/"):], body))
        if path == "/api/save":
            vals = body.get("values")
            if not isinstance(vals, dict) or not set(vals) <= set(SECRET_FIELDS):
                return self._send(400, {"ok": False, "error": "unknown field"})
            clear = [c for c in (body.get("clear") or []) if c in SECRET_FIELDS]
            mv = self.server.majvaj
            if mv is not None and any(c.startswith("kraken_trade") for c in clear) and \
                    mv.mode.state().get("mode") in ("live", "live_exit_only"):
                return self._send(200, {"ok": False, "message": "The bot is trading live with this key. Switch back to "
                                                                "paper first (Maj Vaj page), then delete it."})
            store.update(vals, clear=clear)
            return self._send(200, {"ok": True, "message": "Deleted." if clear and not vals else "Saved."})
        if path == "/api/history/download":
            started = self.server.research.download()
            return self._send(200, {"ok": started, "message": "Updating the candle history from Kraken..." if started
                                    else "An update is already running."})
        if path.startswith("/api/majvaj/"):
            mv = self.server.majvaj
            if mv is None:
                return self._send(200, {"ok": False, "message": "Maj Vaj is not set up on this machine."})
            action = path[len("/api/majvaj/"):]
            if action == "money":
                return self._send(200, mv.save_money(body))
            if action == "mode":
                return self._send(200, mv.mode_request(body))
            if action == "data_refresh":
                threading.Thread(target=mv.econ.refresh, daemon=True).start()
                return self._send(200, {"ok": True, "message": "Refreshing outside data; this takes up to a minute."})
            if action == "trading":
                return self._send(200, mv.set_trading(bool(body.get("on")), body.get("confirm")))
            if action == "pairs":
                return self._send(200, mv.pairs.apply(body.get("pairs"), mv.money(), self.server.research, mv.notifier.recovery))
            if action == "strategy":
                return self._send(200, mv.save_strategy(body))
            if action == "restart":
                return self._send(200, mv.restart_request(str(body.get("what") or "")))
            if action == "backtest":
                return self._send(200, mv.lab.request(str(body.get("range") or "")))
            if action == "grok":
                return self._send(200, mv.save_grok_settings(body))
            if action == "schedule":
                return self._send(200, mv.save_schedule(body.get("times")))
            if action == "verdict_now":
                mv.verdicts._meta("last_run", 0)
                return self._send(200, {"ok": True, "message": "A fundamentals check starts within a minute."})
            if action == "report_now":
                kind = body.get("kind")
                if kind not in ("evening", "weekly"):
                    return self._send(400, {"ok": False, "error": "unknown report"})
                text = mv.evening_report() if kind == "evening" else mv.weekly_review()
                mv.notifier.desk(text)
                return self._send(200, {"ok": True, "message": "Sent to the Desk Telegram bot.", "text": text})
            return self._send(404, {"ok": False, "error": "unknown action"})
        if path.startswith("/api/test/"):
            res = self.server.run_test(path[len("/api/test/"):])
            if res is None:
                return self._send(404, {"ok": False, "error": "unknown test"})
            return self._send(200, res)
        if path.startswith("/api/find_chat/"):
            bot = TELEGRAM_BOTS.get(path[len("/api/find_chat/"):])
            if not bot:
                return self._send(404, {"ok": False, "error": "unknown bot"})
            tok, chat, label = bot
            res, chat_id = connectors.telegram_find_chat(store.get(tok), label)
            if chat_id:
                store.update({chat: chat_id})
            return self._send(200, res)
        self._send(404, {"ok": False, "error": "not found"})


def serve_in_thread(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t
