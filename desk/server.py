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

UI_PATH = os.path.join(os.path.dirname(__file__), "ui.html")
PAPER_PATH = os.path.join(os.path.dirname(__file__), "paper.html")
MAX_BODY = 64 * 1024

TELEGRAM_BOTS = {
    "desk": ("telegram_desk_token", "telegram_desk_chat_id", "Desk"),
    "recovery": ("telegram_recovery_token", "telegram_recovery_chat_id", "Recovery"),
}


class DeskServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, store, port=8765, token=None, engine_factory=None, research_factory=None):
        self.store = store
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

    def run_test(self, name):
        s = self.store
        if name == "kraken_public":
            return connectors.kraken_public_test()
        if name == "kraken_private":
            return connectors.kraken_private_test(s.get("kraken_key"), s.get("kraken_secret"))
        if name == "grok":
            return connectors.grok_test(s.get("grok_key"))
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
            sys.stderr.write("Desk: internal error (%s) while handling %s\n" % (type(e).__name__, self.path.split('?')[0]))
            try:
                self._send(500, {"ok": False, "message": "The app hit an internal error (%s). See the terminal window." % type(e).__name__})
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
        if path == "/":
            with open(UI_PATH, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/paper":
            with open(PAPER_PATH, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/paper/status":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            return self._send(200, dict(self.server.engine.status(), ok=True, version=__version__))
        if path == "/api/history/status":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            return self._send(200, dict(self.server.research.status(), ok=True))
        if path == "/api/status":
            if not self._authorized():
                return self._send(403, {"ok": False, "error": "bad token"})
            return self._send(200, {"ok": True, "saved": self.server.store.saved(), "version": __version__})
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
        if path == "/api/save":
            vals = body.get("values")
            if not isinstance(vals, dict) or not set(vals) <= set(SECRET_FIELDS):
                return self._send(400, {"ok": False, "error": "unknown field"})
            store.update(vals, clear=[c for c in body.get("clear", []) if c in SECRET_FIELDS])
            return self._send(200, {"ok": True})
        if path in ("/api/history/download", "/api/history/backtest"):
            r = self.server.research
            started = r.download() if path.endswith("download") else r.backtest()
            return self._send(200, {"ok": started, "message": "Started." if started else "Something is already running."})
        if path.startswith("/api/paper/"):
            return self._paper(path[len("/api/paper/"):], body)
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


def _paper_action(self, action, body):
    eng = self.server.engine
    if action == "start":
        ok = eng.start()
        return {"ok": True, "message": "Paper trading started." if ok else "It was already running."}
    if action == "stop":
        ok = eng.stop()
        return {"ok": True, "message": "Stopped." if ok else "It was not running."}
    if action == "scout_now":
        if not eng.request_scout():
            return {"ok": False, "message": "Press Start first; the scout only runs while the desk is running."}
        return {"ok": True, "message": "Scout requested. It can take a minute; the page refreshes by itself."}
    if action == "reset":
        if eng.running:
            return {"ok": False, "message": "Stop the desk before resetting the paper account."}
        try:
            equity = float(body.get("equity", 1000))
        except (TypeError, ValueError):
            equity = 0
        if not 100 <= equity <= 10_000_000:
            return {"ok": False, "message": "Starting equity must be between 100 and 10,000,000."}
        eng.account.reset(equity)
        eng.last, eng.last_bids = {}, {}
        eng.errors.clear()
        engine_mod.save_settings(self.server.store.dir, {})
        return {"ok": True, "message": "Paper account reset to %.2f USD." % equity}
    if action == "settings":
        engine_mod.save_settings(self.server.store.dir, {k: body[k] for k in body if k in
                                 ("scout_minutes", "cycle_minutes", "max_scout_calls_per_day", "grok_model", "session_scouting")})
        return {"ok": True, "message": "Settings saved."}
    return None


Handler._paper = lambda self, action, body: (
    lambda res: self._send(404, {"ok": False, "error": "unknown action"}) if res is None else self._send(200, res)
)(_paper_action(self, action, body))


def serve_in_thread(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t
