"""Copy Desk's Kraken history (history.db) into Freqtrade's data folder, 1h and 4h candles.
Run inside the Freqtrade environment: python convert_history.py <history.db> <user_data/data/kraken>"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from freqtrade.data.history.datahandlers import get_datahandler
from freqtrade.enums import CandleType

PAIRS = ("BTC/USD", "ETH/USD", "SOL/USD")
COLS = ["date", "open", "high", "low", "close", "volume"]


def main(db_path, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dh = get_datahandler(out, "feather")
    db = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    for pair in PAIRS:
        df = pd.read_sql_query("SELECT ts, o AS open, h AS high, l AS low, c AS close, v AS volume "
                               "FROM candles WHERE symbol=? ORDER BY ts", db, params=(pair,))
        if df.empty:
            print("%s: no rows, skipped" % pair)
            continue
        df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        h1 = df[COLS].drop_duplicates("date").reset_index(drop=True)
        dh.ohlcv_store(pair, "1h", h1, CandleType.SPOT)
        h4 = (h1.set_index("date").resample("4h", label="left", closed="left")
              .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
              .dropna().reset_index())
        if len(h4) and h1["date"].iloc[-1] < h4["date"].iloc[-1] + pd.Timedelta(hours=3):
            h4 = h4.iloc[:-1]  # last 4h candle not complete yet
        dh.ohlcv_store(pair, "4h", h4[COLS], CandleType.SPOT)
        d1 = (h1.set_index("date").resample("1D", label="left", closed="left")
              .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
              .dropna().reset_index())
        if len(d1) and h1["date"].iloc[-1] < d1["date"].iloc[-1] + pd.Timedelta(hours=23):
            d1 = d1.iloc[:-1]  # today's daily candle not complete yet
        dh.ohlcv_store(pair, "1d", d1[COLS], CandleType.SPOT)
        print("%s: %d hourly, %d four-hour, %d daily candles, %s to %s" % (
            pair, len(h1), len(h4), len(d1), h1["date"].iloc[0], h1["date"].iloc[-1]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
