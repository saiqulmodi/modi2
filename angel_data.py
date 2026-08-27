"""
Angel One fallback for index spot price and option premiums, used when
Motilal Oswal's price endpoints fail. Mirrors the pattern already used in
MODI1's angel_data.py.
"""

import json
import time
import pyotp
import requests
from datetime import datetime
from angel_login import (
    auth_token, API_KEY, CLIENT_ID, PASSWORD as ANGEL_PASSWORD,
    TOTP_SECRET as ANGEL_TOTP_SECRET, login_url as ANGEL_LOGIN_URL,
    headers as ANGEL_LOGIN_HEADERS,
)

with open("angel_scrips.json", "r") as f:
    angel_scrips = json.load(f)

INDEX_TOKENS = {
    "NIFTY": "26000",
    "BANKNIFTY": "26009",
}

# angel_login.py fetches its auth_token once at import time -- same
# staleness problem as motilal_login.py, and this module has the same two
# long-running callers (dashboard + live_alerts.py's perpetual loop) via
# option_chain.py's Angel fallback. Re-logs in fresh, cached for 1 hour.
_TOKEN_TTL_SECONDS = 3600
_cached_token = None
_cached_token_at = 0


def _get_fresh_angel_token():
    global _cached_token, _cached_token_at
    now = time.time()
    if _cached_token is not None and (now - _cached_token_at) < _TOKEN_TTL_SECONDS:
        return _cached_token

    totp_code = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
    body = {"clientcode": CLIENT_ID, "password": ANGEL_PASSWORD, "totp": totp_code}
    try:
        response = requests.post(ANGEL_LOGIN_URL, json=body, headers=ANGEL_LOGIN_HEADERS, timeout=10)
        data = response.json()
    except Exception:
        return _cached_token or auth_token

    if data.get("status"):
        _cached_token = data["data"]["jwtToken"]
        _cached_token_at = now
        return _cached_token
    return _cached_token or auth_token


def _headers():
    return {
        "Authorization": f"Bearer {_get_fresh_angel_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
        "X-ClientLocalIP": "1.2.3.4",
        "X-ClientPublicIP": "1.2.3.4",
        "X-MACAddress": "00:00:00:00:00:00",
        "X-PrivateKey": API_KEY,
    }


def _get_ltp_by_token(token, exchange):
    url = "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/getLtpData"
    body = {"exchange": exchange, "tradingsymbol": "-", "symboltoken": str(token)}
    try:
        response = requests.post(url, json=body, headers=_headers(), timeout=10)
        return response.json()
    except (requests.exceptions.RequestException, ValueError):
        return None


def get_angel_index_ltp(symbol):
    """symbol: 'NIFTY' or 'BANKNIFTY'. Returns the raw Angel LTP response, or None."""
    token = INDEX_TOKENS.get(symbol)
    if not token:
        return None
    return _get_ltp_by_token(token, "NSE")


def find_angel_option_token(index_name, strike, option_type):
    """
    Finds the nearest-expiry OPTIDX contract token matching index_name
    ('NIFTY'/'BANKNIFTY'), strike (plain rupee value, e.g. 22600), and
    option_type ('CE'/'PE'). Returns None if no match found.
    """
    suffix = option_type.upper()
    candidates = []
    for entry in angel_scrips:
        if (
            entry.get("instrumenttype") == "OPTIDX"
            and entry.get("name") == index_name
            and entry.get("symbol", "").endswith(suffix)
        ):
            try:
                entry_strike = float(entry["strike"]) / 100
            except (ValueError, TypeError):
                continue
            if abs(entry_strike - strike) < 0.01:
                try:
                    expiry_date = datetime.strptime(entry["expiry"], "%d%b%Y")
                except ValueError:
                    continue
                candidates.append((expiry_date, entry["token"]))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def find_angel_option_contract(index_name, strike, option_type):
    """
    Same matching as find_angel_option_token(), but also returns Angel's
    exact tradingsymbol string (e.g. 'NIFTY25AUG2624500CE') and lot size --
    needed for order placement, which requires symboltoken, tradingsymbol,
    AND a quantity that's a multiple of the contract's lot size.
    Returns (token, tradingsymbol, lot_size) or (None, None, None) if no match.
    """
    suffix = option_type.upper()
    candidates = []
    for entry in angel_scrips:
        if (
            entry.get("instrumenttype") == "OPTIDX"
            and entry.get("name") == index_name
            and entry.get("symbol", "").endswith(suffix)
        ):
            try:
                entry_strike = float(entry["strike"]) / 100
            except (ValueError, TypeError):
                continue
            if abs(entry_strike - strike) < 0.01:
                try:
                    expiry_date = datetime.strptime(entry["expiry"], "%d%b%Y")
                except ValueError:
                    continue
                lot_size = int(entry.get("lotsize", 1))
                candidates.append((expiry_date, entry["token"], entry["symbol"], lot_size))

    if not candidates:
        return None, None, None
    candidates.sort(key=lambda c: c[0])
    _, token, tradingsymbol, lot_size = candidates[0]
    return token, tradingsymbol, lot_size


def get_angel_option_ltp(index_name, strike, option_type):
    """Returns the raw Angel LTP response for the nearest-expiry matching option contract, or None."""
    token = find_angel_option_token(index_name, strike, option_type)
    if not token:
        return None
    return _get_ltp_by_token(token, "NFO")
