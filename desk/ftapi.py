"""Small client for Freqtrade's REST API (http://127.0.0.1:8080/api/v1).

Desk mostly reads. The control calls (pause, start) exist for the watchdog and the Recovery bot, and both are
reversible: pause stops new entries and leaves open trades managed. force_enter asks the bot to open a trade for an
approved trade plan; the plans module only calls it while the bot is in paper (dry-run) mode, and the bot's own
gates (news verdict, event windows, money rules, trained model) still decide whether the entry happens."""
import base64
import json
import urllib.parse

from . import net


class FtError(Exception):
    pass


class FreqtradeAPI:
    def __init__(self, url, username, password, http=None):
        self.url = url.rstrip("/") + "/api/v1"
        auth = base64.b64encode(("%s:%s" % (username, password)).encode()).decode()
        self.headers = {"Authorization": "Basic " + auth, "Content-Type": "application/json"}
        self.http = http or net.http_json

    @classmethod
    def from_file(cls, path, http=None):
        with open(path, "r", encoding="utf-8") as f:
            c = json.load(f)
        return cls(c.get("ft_url", "http://127.0.0.1:8080"), c["ft_username"], c["ft_password"], http)

    def _call(self, method, path, body=None):
        try:
            status, data = self.http(method, self.url + path, self.headers,
                                     None if body is None else json.dumps(body).encode(), 15)
        except net.NetError as e:
            raise FtError("Freqtrade not reachable: %s" % e) from None
        if status == 401:
            raise FtError("Freqtrade refused the API login")
        if status != 200:
            raise FtError("Freqtrade answered HTTP %s" % status)
        return data

    def ping(self):
        return self._call("GET", "/ping")

    def health(self):
        return self._call("GET", "/health")

    def show_config(self):
        return self._call("GET", "/show_config")

    def status(self):
        return self._call("GET", "/status")

    def profit(self):
        return self._call("GET", "/profit")

    def count(self):
        return self._call("GET", "/count")

    def trades(self, limit=50):
        return self._call("GET", "/trades?limit=%d" % int(limit))

    def pair_candles(self, pair, timeframe, limit=2):
        """The bot's analysed candles (includes the model's prediction columns)."""
        return self._call("GET", "/pair_candles?pair=%s&timeframe=%s&limit=%d" % (
            urllib.parse.quote(pair, safe=""), urllib.parse.quote(timeframe, safe=""), int(limit)))

    def force_enter(self, pair, entry_tag):
        """Ask the bot to open a long trade now. Returns (ok, info); info has trade_id or error."""
        body = json.dumps({"pair": pair, "side": "long", "entry_tag": entry_tag}).encode()
        try:
            status, data = self.http("POST", self.url + "/forceenter", self.headers, body, 30)
        except net.NetError as e:
            return False, {"error": "bot not reachable: %s" % e}
        if status == 200 and isinstance(data, dict) and data.get("trade_id") is not None:
            return True, {"trade_id": data.get("trade_id")}
        msg = ""
        if isinstance(data, dict):
            msg = str(data.get("error") or data.get("detail") or data.get("status") or "")
        return False, {"error": msg or "HTTP %s" % status}

    def balance(self):
        return self._call("GET", "/balance")

    def pause(self):
        return self._call("POST", "/pause", {})

    def start(self):
        return self._call("POST", "/start", {})

    def summary(self):
        """Everything the page and reports need, or an 'error' key."""
        try:
            cfg, prof, opened, health = self.show_config(), self.profit(), self.status(), self.health()
        except FtError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "state": cfg.get("state"), "dry_run": cfg.get("dry_run"),
                "strategy": cfg.get("strategy"), "timeframe": cfg.get("timeframe"),
                "max_open_trades": cfg.get("max_open_trades"),
                "last_process_ts": health.get("last_process_ts"),
                "profit_all": prof.get("profit_all_coin"), "profit_all_pct": prof.get("profit_all_percent"),
                "profit_closed": prof.get("profit_closed_coin"), "trade_count": prof.get("trade_count"),
                "closed_trades": prof.get("closed_trade_count"), "winning": prof.get("winning_trades"),
                "losing": prof.get("losing_trades"),
                "balance": self._balance(cfg.get("stake_currency") or "USD"),
                "open": [{"pair": t.get("pair"), "amount": t.get("amount"), "open_rate": t.get("open_rate"),
                          "current_rate": t.get("current_rate"), "profit_pct": t.get("profit_pct"),
                          "profit_abs": t.get("profit_abs"), "open_date": t.get("open_date"),
                          "stop": t.get("stop_loss_abs"), "stake": t.get("stake_amount")}
                         for t in (opened or []) if isinstance(t, dict)]}

    def _balance(self, stake):
        """Equity (everything the bot holds, valued in USD) and cash (USD not in trades). None if unavailable."""
        try:
            b = self.balance()
        except FtError:
            return None
        cash = next((c.get("free") for c in (b.get("currencies") or []) if c.get("currency") == stake), None)
        return {"equity": b.get("total", b.get("total_bot")), "cash": cash, "currency": stake}
