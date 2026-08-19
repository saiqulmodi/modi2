import yfinance as yf
import pandas as pd
from option_chain import get_spot_price, get_option_chain, get_ltp

YF_TICKERS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK"
}

def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def get_trend_signal(symbol):
    """Same logic as MODI1's get_trend_signal, adapted for index tickers (no volume check - indices don't report volume the same way)."""
    yf_symbol = YF_TICKERS[symbol]
    hist = yf.Ticker(yf_symbol).history(period="1y")
    if hist.empty or len(hist) <= 200:
        return 0, "not enough history"

    hist = hist.copy()
    hist["MA50"] = hist["Close"].rolling(window=50).mean()
    hist["MA200"] = hist["Close"].rolling(window=200).mean()
    hist["RSI"] = calculate_rsi(hist["Close"])

    latest_ma50 = hist["MA50"].iloc[-1]
    latest_ma200 = hist["MA200"].iloc[-1]
    latest_rsi = hist["RSI"].iloc[-1]

    trend_score = 0
    if latest_ma50 > latest_ma200:
        trend_score += 1
        trend = "uptrend"
    else:
        trend_score -= 1
        trend = "downtrend"

    if latest_rsi > 70:
        rsi_note = "overbought"
        trend_score -= 1
    elif latest_rsi < 30:
        rsi_note = "oversold"
        trend_score += 1
    else:
        rsi_note = "neutral"

    return trend_score, f"{trend}, RSI {latest_rsi:.0f} ({rsi_note})"

STRIKE_STEPS = {
    "NIFTY": 50,
    "BANKNIFTY": 100
}

def get_directional_signal(symbol, strike_step=None):
    if strike_step is None:
        strike_step = STRIKE_STEPS[symbol]
    """
    Combines trend/RSI signal with live option chain to recommend CE or PE
    and pick a strike close to ATM (slightly OTM in the trend's direction).
    """
    trend_score, note = get_trend_signal(symbol)
    spot = get_spot_price(symbol)

    if trend_score > 0:
        direction = "CE"
        # slightly OTM call: round up to next strike above spot
        strike = (int(spot // strike_step) + 1) * strike_step
    elif trend_score < 0:
        direction = "PE"
        # slightly OTM put: round down to next strike below spot
        strike = int(spot // strike_step) * strike_step
    else:
        direction = "NEUTRAL"
        strike = round(spot / strike_step) * strike_step

    return {
        "symbol": symbol,
        "spot": spot,
        "trend_score": trend_score,
        "note": note,
        "direction": direction,
        "strike": strike
    }

if __name__ == "__main__":
    for sym in ["NIFTY", "BANKNIFTY"]:
        result = get_directional_signal(sym)
        print(f"\n{result['symbol']}: spot={result['spot']}, trend_score={result['trend_score']} ({result['note']})")
        print(f"  -> Signal: {result['direction']} {result['strike']}")

        if result["direction"] in ("CE", "PE"):
            chain = get_option_chain(result["symbol"], result["spot"])
            match = chain[(chain["strikeprice"] == result["strike"]) & (chain["optiontype"] == result["direction"])]
            if not match.empty:
                scripcode = match.iloc[0]["scripcode"]
                ltp = get_ltp(scripcode)
                print(f"  -> {match.iloc[0]['scripname']}  LTP={ltp}")