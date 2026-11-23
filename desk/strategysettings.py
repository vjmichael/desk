"""Strategy settings the owner can change by hand from the FreqAI tab.

These are the numbers that decide when the bot buys and sells, so they used to be fixed in the strategy code:
  - how much rise the model must predict before the bot buys (entry threshold),
  - how far ahead the model looks (look-ahead, in candles),
  - whether the bot buys only while the daily price is above its 50-day average (trend filter),
  - the stop loss, the take-profit and a time exit.

Desk validates and saves them, writes them as a config layer the bot reads at start (majvaj/config.strategy.json,
the same mechanism as the coin list), and asks the watchdog to restart the bot. Only the look-ahead changes what the
model learns, so only a different look-ahead gets its own model name and a retrain. Everything else takes effect on the
restart with the model the bot already has. A value outside its limits is refused, and a bad file makes the bot fall
back to the built-in numbers, never to something unsafe."""
import json
import os
import time

DEFAULT_LOOKAHEAD = 18             # the model name carries a suffix only when the look-ahead differs from this
FEE_ROUND_TRIP_PCT = 0.52          # Kraken taker fees, buy plus sell, as used in the backtests

DEFAULTS = {"entry_threshold_pct": 3.0, "look_ahead_candles": DEFAULT_LOOKAHEAD, "trend_filter": True,
            "stop_loss_pct": 8.0, "take_profit_pct": 12.0, "time_exit_hours": 72}
LIMITS = {"entry_threshold_pct": (0.3, 10.0), "look_ahead_candles": (2, 60), "stop_loss_pct": (1.0, 25.0),
          "take_profit_pct": (0.5, 50.0), "time_exit_hours": (4, 336)}
NAMES = {"entry_threshold_pct": "Entry threshold", "look_ahead_candles": "Look-ahead",
         "stop_loss_pct": "Stop loss", "take_profit_pct": "Take-profit", "time_exit_hours": "Time exit"}
UNITS = {"entry_threshold_pct": "%", "look_ahead_candles": " candles", "stop_loss_pct": "%",
         "take_profit_pct": "%", "time_exit_hours": " hours"}


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def clean(raw, current=None):
    """Validate settings from the page. Returns (settings, error); error is '' when fine."""
    s = dict(DEFAULTS)
    s.update(current or {})
    if not isinstance(raw, dict):
        return None, "Bad request."
    for key in raw:
        if key not in DEFAULTS:
            return None, "Unknown setting: %s." % key
    for key, value in raw.items():
        if key == "trend_filter":
            if isinstance(value, bool):
                s[key] = value
            elif isinstance(value, str) and value.lower() in ("true", "false"):
                s[key] = value.lower() == "true"
            else:
                return None, "Trend filter must be on or off."
            continue
        lo, hi = LIMITS[key]
        try:
            if isinstance(value, bool):
                raise ValueError
            v = float(value)
        except (TypeError, ValueError):
            return None, "%s must be a number." % NAMES[key]
        if v != v or v in (float("inf"), float("-inf")):
            return None, "%s must be a number." % NAMES[key]
        if key == "look_ahead_candles" and v != int(v):
            return None, "Look-ahead must be a whole number of candles."
        if not lo <= v <= hi:
            return None, "%s must be between %s and %s%s." % (NAMES[key], lo, hi, UNITS[key])
        s[key] = int(v) if key == "look_ahead_candles" else round(v, 4)
    return s, ""


def layer(s, base_identifier=None):
    """The config layer the bot reads. Look-ahead is the only setting that changes what the model learns."""
    n = int(s["look_ahead_candles"])
    freqai = {"feature_parameters": {"label_period_candles": n}}
    if base_identifier and n != DEFAULT_LOOKAHEAD:
        freqai["identifier"] = "%s-L%d" % (base_identifier, n)
    minutes = int(round(float(s["time_exit_hours"]) * 60))
    return {"stoploss": -round(float(s["stop_loss_pct"]) / 100, 6),
            "minimal_roi": {"0": round(float(s["take_profit_pct"]) / 100, 6), str(minutes): -1},
            "majvaj_entry_threshold": round(float(s["entry_threshold_pct"]) / 100, 6),
            "majvaj_trend_filter": bool(s["trend_filter"]),
            "freqai": freqai}


def effective_identifier(user_data):
    """The model name the bot is using: the strategy layer's if it set one, else the base config's."""
    layer_cfg = _read(os.path.join(user_data, "majvaj", "config.strategy.json")) or {}
    ident = (layer_cfg.get("freqai") or {}).get("identifier")
    if ident:
        return ident
    return ((_read(os.path.join(user_data, "config-4h.json")) or {}).get("freqai") or {}).get("identifier")


def timeframe_hours(base_config):
    tf = str((base_config or {}).get("timeframe") or "4h")
    try:
        return int(tf[:-1]) * {"m": 1 / 60, "h": 1, "d": 24}[tf[-1]]
    except (ValueError, KeyError):
        return 4


def warnings_for(s, tf_hours=4):
    out = []
    if s["entry_threshold_pct"] < 1.0:
        out.append("An entry threshold under about 1%% means the bot buys on predictions smaller than Kraken's fees "
                   "(about %.2f%% to buy and sell), so most trades will lose to fees." % FEE_ROUND_TRIP_PCT)
    if s["take_profit_pct"] < 1.0:
        out.append("A take-profit under 1%% barely covers Kraken's fees (about %.2f%% to buy and sell)." % FEE_ROUND_TRIP_PCT)
    if s["stop_loss_pct"] < 2.0:
        out.append("A stop loss under 2% will often be hit by ordinary price swings.")
    if not s["trend_filter"]:
        out.append("With the trend filter off, the bot also buys while the market is falling.")
    ahead_hours = s["look_ahead_candles"] * tf_hours
    if s["time_exit_hours"] < ahead_hours:
        out.append("The time exit (%g hours) is shorter than the look-ahead (%g hours), so the bot sells before the "
                   "move it predicted can happen." % (s["time_exit_hours"], ahead_hours))
    return out


def describe(s, tf_hours=4):
    return ("Buys when the model expects the price to rise by at least %g%% on average over the next %g hours (%d candles)%s. "
            "Sells at +%g%% take-profit, at -%g%% stop loss, or after %g hours." % (
                s["entry_threshold_pct"], s["look_ahead_candles"] * tf_hours, s["look_ahead_candles"],
                ", only while the daily price is above its 50-day average" if s["trend_filter"] else ", in any market",
                s["take_profit_pct"], s["stop_loss_pct"], s["time_exit_hours"]))


class StrategySettings:
    def __init__(self, mv_dir, base_config_path, clock=time.time):
        self.mv, self.base_path, self.clock = mv_dir, base_config_path, clock
        self.settings_path = os.path.join(mv_dir, "strategy_settings.json")
        self.layer_path = os.path.join(mv_dir, "config.strategy.json")

    def base_config(self):
        return _read(self.base_path) or {}

    def load(self):
        saved = _read(self.settings_path)
        if isinstance(saved, dict):
            s, err = clean(saved.get("settings") if "settings" in saved else saved)
            if s is not None:
                return s
        return dict(DEFAULTS)

    def status(self):
        s = self.load()
        tf = timeframe_hours(self.base_config())
        saved = _read(self.settings_path) or {}
        return {"settings": s, "defaults": dict(DEFAULTS), "limits": {k: list(v) for k, v in LIMITS.items()},
                "summary": describe(s, tf), "warnings": warnings_for(s, tf), "timeframe_hours": tf,
                "saved_at": saved.get("saved_at"), "fee_round_trip_pct": FEE_ROUND_TRIP_PCT,
                "model_name": effective_identifier(os.path.dirname(self.mv))}

    def save(self, raw):
        """Validate, then write the settings and the bot's config layer. Returns a result for the page."""
        before = self.load()
        s, err = clean(raw, before)
        if s is None:
            return {"ok": False, "message": err}
        base = (self.base_config().get("freqai") or {}).get("identifier")
        retrain = s["look_ahead_candles"] != before["look_ahead_candles"]
        try:
            _write(self.settings_path, {"settings": s, "saved_at": self.clock()})
            _write(self.layer_path, layer(s, base))
        except OSError as e:
            return {"ok": False, "message": "Nothing changed: Desk could not save the settings to %s (%s)." % (
                e.filename or self.mv, e.strerror or type(e).__name__)}
        tf = timeframe_hours(self.base_config())
        return {"ok": True, "retrain": retrain, "settings": s, "warnings": warnings_for(s, tf), "summary": describe(s, tf),
                "changed": [k for k in DEFAULTS if s[k] != before[k]]}
