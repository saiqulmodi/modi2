"""
Intraday confirmation layer for NIFTY/BANKNIFTY: VWAP position and Opening
Range Breakout (ORB), computed from today's 5-minute index candles via
Angel One's historical candle API.

Used to gate live_alerts.py's CE signal so it only fires when today's actual
intraday price action agrees with the daily trend/RSI direction, rather than
alerting purely off daily-bar data.

Note: spot index "volume" isn't a meaningful traded quantity (NIFTY/BANKNIFTY
aren't instruments you trade directly), so unlike MODI1's equity version,
volume is not part of the confirmation here -- only VWAP + ORB.

Fails closed: if there isn't enough intraday data yet (e.g. right at market
open) or the candle fetch fails, get_intraday_confirmation() returns None
and the caller should treat that as "no confirmation" rather than guessing.
"""

import requests
import pandas as pd
from datetime import datetime
from angel_login import auth_token, API_KEY

CANDLE_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"
ORB_MINUTES = 15

INDEX_TOKENS = {
    # NOTE: these are the AMXIDX-type tokens, NOT the same ones used for
    # LTP fetches elsewhere (26000/26009). The historical candle endpoint
    # silently returns an empty data array (no error) for the LTP tokens,
    # which caused every single BANKNIFTY signal to get held back all day
    # with "no intraday confirmation data available" despite a real trend.
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009",
}


def _headers():
    return {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "1.2.3.4",
        "X-ClientPublicIP": "1.2.3.4",
        "X-MACAddress": "00:00:00:00:00:00",
        "X-PrivateKey": API_KEY,
    }


def get_today_candles(token, exchange="NSE", interval="FIVE_MINUTE"):
    today = datetime.now().strftime("%Y-%m-%d")
    body = {
        "exchange": exchange,
        "symboltoken": str(token),
        "interval": interval,
        "fromdate": f"{today} 09:15",
        "todate": f"{today} 15:30",
    }
    try:
        response = requests.post(CANDLE_URL, json=body, headers=_headers(), timeout=10)
        result = response.json()
    except Exception as e:
        print(f"Candle fetch error: {e}")
        return None

    if not result.get("status") or not result.get("data"):
        return None

    df = pd.DataFrame(result["data"], columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


def get_intraday_confirmation(symbol):
    """
    Returns a dict describing today's intraday state for NIFTY/BANKNIFTY,
    or None if there isn't enough data yet to judge anything.
    """
    token = INDEX_TOKENS.get(symbol)
    if not token:
        return None

    candles_per_orb = max(1, ORB_MINUTES // 5)
    df = get_today_candles(token)
    if df is None or len(df) < candles_per_orb + 1:
        return None

    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    # Falls back to simple average if index volume is 0/missing (common for
    # spot indices), rather than dividing by zero.
    if df["volume"].sum() > 0:
        vwap = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum()
    else:
        vwap = typical_price.expanding().mean()

    current_price = df["close"].iloc[-1]
    current_vwap = vwap.iloc[-1]

    orb_high = df["high"].iloc[:candles_per_orb].max()
    orb_low = df["low"].iloc[:candles_per_orb].min()
    if current_price > orb_high:
        orb_breakout = "UP"
    elif current_price < orb_low:
        orb_breakout = "DOWN"
    else:
        orb_breakout = None

    above_vwap = current_price > current_vwap

    return {
        "current_price": round(current_price, 2),
        "vwap": round(current_vwap, 2),
        "above_vwap": above_vwap,
        "orb_high": round(orb_high, 2),
        "orb_low": round(orb_low, 2),
        "orb_breakout": orb_breakout,
        "confirms_bullish": above_vwap and orb_breakout == "UP",
    }
