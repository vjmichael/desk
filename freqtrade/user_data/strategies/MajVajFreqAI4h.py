"""Maj Vaj, slower version: FreqAI on 4-hour candles, predicting the average price change over the
next 3 days (18 candles). Fewer, larger trades so Kraken's ~0.52% round-trip cost matters less.
Long only, and only while the daily trend is up (price above its 50-day average)."""
from functools import reduce

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, merge_informative_pair

from MajVajFreqAI import MajVajFreqAI


class MajVajFreqAI4h(MajVajFreqAI):
    timeframe = "4h"
    startup_candle_count = 330           # 50 daily candles = 300 four-hour candles, plus margin
    stoploss = -0.08
    minimal_roi = {"0": 0.12, "4320": -1}  # take 12% at once; after 3 days exit at market
    entry_threshold = 0.03

    def informative_pairs(self):
        return [(pair, "1d") for pair in self.dp.current_whitelist()]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe = self.freqai.start(dataframe, metadata, self)
        daily = self.dp.get_pair_dataframe(metadata["pair"], "1d")
        daily["sma50"] = ta.SMA(daily, timeperiod=50)
        dataframe = merge_informative_pair(dataframe, daily[["date", "close", "sma50"]], self.timeframe, "1d", ffill=True)
        dataframe["uptrend"] = dataframe["close_1d"] > dataframe["sma50_1d"]
        return dataframe

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        conditions = [df["do_predict"] == 1, df["&-s_close"] > self.entry_threshold, df["uptrend"], df["volume"] > 0]
        df.loc[reduce(lambda a, b: a & b, conditions), ["enter_long", "enter_tag"]] = (1, "model_uptrend")
        return df
