"""
Backtests the directional trend/RSI signal used by option_signal.py's
get_trend_signal() / get_directional_signal(), which drives MODI2's
CE/PE alerts (live_alerts.py).

This validates the DIRECTIONAL call on the underlying index (NIFTY,
BANKNIFTY) only -- it does not simulate actual option P&L (leverage, theta
decay, IV changes, and strike selection all affect real option returns
differently from the index move itself). Treat this as: "does the trend+RSI
signal correctly predict which way the index moves next," which is the
premise the CE/PE choice depends on.

For each day with enough history, recomputes the same trend_score used live
(MA50 vs MA200, RSI overbought/oversold), then checks the index's forward
return over several holding periods (3/5/10 trading days, roughly proxying
short-dated weekly-option holds). A "win" for a CE call is a positive forward
return; for a PE call it's a negative forward return.
"""

import numpy as np
import pandas as pd
import yfinance as yf

YF_TICKERS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}

HORIZONS = [3, 5, 10]


def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def bucket_stats(sub_df, group_col, return_col):
    stats = sub_df.groupby(group_col).agg(
        n=(return_col, "size"),
        win_rate=(return_col, lambda s: (s > 0).mean()),
        avg_return=(return_col, "mean"),
        median_return=(return_col, "median"),
    )
    stats["win_rate"] = (stats["win_rate"] * 100).round(1)
    stats["avg_return"] = (stats["avg_return"] * 100).round(2)
    stats["median_return"] = (stats["median_return"] * 100).round(2)
    return stats


for symbol, yf_symbol in YF_TICKERS.items():
    print(f"\n{'=' * 70}\n{symbol} ({yf_symbol})\n{'=' * 70}")

    hist = yf.Ticker(yf_symbol).history(period="5y")
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)

    hist["MA50"] = hist["Close"].rolling(window=50).mean()
    hist["MA200"] = hist["Close"].rolling(window=200).mean()
    hist["RSI"] = calculate_rsi(hist["Close"])

    trend_component = np.where(hist["MA50"] > hist["MA200"], 1, -1)
    rsi_component = np.where(hist["RSI"] > 70, -1, np.where(hist["RSI"] < 30, 1, 0))
    hist["trend_score"] = trend_component + rsi_component

    hist["direction"] = np.select(
        [hist["trend_score"] > 0, hist["trend_score"] < 0],
        ["CE", "PE"],
        default="NEUTRAL",
    )

    hist = hist.dropna(subset=["MA200", "RSI"])
    print(f"Usable history: {hist.index.min().date()} to {hist.index.max().date()}  (n={len(hist)})")
    print("Direction counts:", hist["direction"].value_counts().to_dict())

    for horizon in HORIZONS:
        df = hist.copy()
        future_close = df["Close"].shift(-horizon)
        future_return = (future_close - df["Close"]) / df["Close"]
        df["signed_return"] = np.where(
            df["direction"] == "CE", future_return,
            np.where(df["direction"] == "PE", -future_return, np.nan)
        )
        df = df.dropna(subset=["signed_return"])

        print(f"\n--- Forward horizon: {horizon} trading days ---")
        print(bucket_stats(df, "direction", "signed_return"))

print(f"\n{'=' * 70}")
print("Reminder: this measures index-direction accuracy, not real option P&L.")
print("Real CE/PE returns will differ due to leverage, theta decay, and IV changes.")
