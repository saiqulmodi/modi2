import re
from datetime import date, datetime
import yfinance as yf
import pandas as pd
from option_chain import get_spot_price, get_option_chain, get_ltp

YF_TICKERS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK"
}

# A same-direction gap this big (in %) has already used up much of a move
# before the alert even fires, and unfilled index gaps this size carry a
# real intraday reversal risk that a pure trend/RSI signal can't see.
# Informational/caution threshold, not backtested like the core CE signal.
GAP_CAUTION_PCT = 1.0


def parse_expiry_from_scripname(scripname):
    """
    scripname looks like 'NIFTY 25-Aug-2026 CE 23750'. Motilal's expirydate
    column in nsefo_scrips.csv turned out to be unreliable (found off by
    exactly 10 years during testing), so the real expiry is parsed straight
    out of the human-readable scripname instead.
    """
    if not scripname:
        return None
    match = re.search(r"(\d{1,2}-[A-Za-z]{3}-\d{4})", scripname)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%d-%b-%Y").date()
    except ValueError:
        return None


def get_gap_pct(symbol):
    """
    Overnight gap: (today's open - yesterday's close) / yesterday's close,
    as a percentage. Returns None if there isn't enough recent history.
    """
    yf_symbol = YF_TICKERS[symbol]
    hist = yf.Ticker(yf_symbol).history(period="5d")
    if len(hist) < 2:
        return None
    prev_close = hist["Close"].iloc[-2]
    today_open = hist["Open"].iloc[-1]
    if not prev_close:
        return None
    return round((today_open - prev_close) / prev_close * 100, 2)

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

# backtest.py showed the PE (bearish) side of this signal is worse than a
# coin flip and gets worse with longer holds (down to 32-37% win rate at a
# 10-day horizon), while CE (bullish) has a real, modest edge (52-60% win
# rate). So PE calls are suppressed at the alert layer until the logic is
# reworked -- see live_alerts.py.

# Stop-loss as a percentage of the option premium paid, the standard
# practical risk control for option buyers (protects against theta decay
# and adverse moves without needing a separate underlying-price stop).
STOPLOSS_PCT = 0.30

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

    entry_price = None
    scripname = None
    scripcode = None
    expiry_date = None
    days_to_expiry = None
    if direction in ("CE", "PE"):
        chain = get_option_chain(symbol, spot)
        match = chain[(chain["strikeprice"] == strike) & (chain["optiontype"] == direction)]
        if not match.empty:
            scripcode = int(match.iloc[0]["scripcode"])
            scripname = match.iloc[0]["scripname"]
            entry_price = get_ltp(scripcode, index_name=symbol, strike=strike, option_type=direction)
            expiry_date = parse_expiry_from_scripname(scripname)
            if expiry_date:
                days_to_expiry = (expiry_date - date.today()).days

    stop_loss = round(entry_price * (1 - STOPLOSS_PCT), 2) if entry_price else None
    gap_pct = get_gap_pct(symbol)

    return {
        "symbol": symbol,
        "spot": spot,
        "trend_score": trend_score,
        "note": note,
        "direction": direction,
        "strike": strike,
        "scripname": scripname,
        "scripcode": scripcode,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "expiry_date": expiry_date.isoformat() if expiry_date else None,
        "days_to_expiry": days_to_expiry,
        "gap_pct": gap_pct,
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