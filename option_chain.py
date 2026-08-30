import time
import re
import hashlib
import pyotp
import pandas as pd
import requests
from datetime import date, datetime
from motilal_login import (
    auth_token, headers, USER_ID, PASSWORD as MOTILAL_PASSWORD, DOB,
    API_KEY as MOTILAL_API_KEY, TOTP_SECRET as MOTILAL_TOTP_SECRET,
    login_url as MOTILAL_LOGIN_URL,
)
from angel_data import get_angel_index_ltp, get_angel_option_ltp

df = pd.read_csv("nsefo_scrips.csv")

INDEX_SCRIPCODES = {
    "NIFTY": 26000,
    "BANKNIFTY": 26009
}

# motilal_login.py fetches its auth_token once at import time -- fine for
# short-lived scripts (a fresh process each run), but this module has two
# long-running callers (the Streamlit dashboard and live_alerts.py's
# perpetual loop) that only ever import it once and can run for many
# hours/days, by which point that token is dead. Re-logs in fresh, cached
# for 1 hour, instead of running on an increasingly stale token.
_TOKEN_TTL_SECONDS = 3600
_cached_token = None
_cached_token_at = 0


def _get_fresh_motilal_token():
    global _cached_token, _cached_token_at
    now = time.time()
    if _cached_token is not None and (now - _cached_token_at) < _TOKEN_TTL_SECONDS:
        return _cached_token

    hashed_password = hashlib.sha256((MOTILAL_PASSWORD + MOTILAL_API_KEY).encode()).hexdigest()
    totp_code = pyotp.TOTP(MOTILAL_TOTP_SECRET).now()
    body = {"userid": USER_ID, "password": hashed_password, "2FA": DOB, "totp": totp_code}
    try:
        response = requests.post(MOTILAL_LOGIN_URL, json=body, headers=headers, timeout=10)
        data = response.json()
    except Exception:
        return _cached_token or auth_token

    if data.get("status") == "SUCCESS":
        _cached_token = data.get("AuthToken")
        _cached_token_at = now
        return _cached_token
    return _cached_token or auth_token


def get_spot_price(symbol):
    """Fetch live index spot price (NIFTY or BANKNIFTY) via Motilal's LTP endpoint, falling back to Angel One on failure."""
    url = "https://openapi.motilaloswal.com/rest/report/v3/getltpdata"
    ltp_headers = headers.copy()
    ltp_headers["Authorization"] = _get_fresh_motilal_token()
    body = {
        "clientcode": "",
        "exchange": "NSE",
        "scripcode": INDEX_SCRIPCODES[symbol]
    }
    try:
        response = requests.post(url, json=body, headers=ltp_headers, timeout=10)
        result = response.json()
    except Exception:
        result = {"status": "FAILED", "message": "Motilal request/parse error"}

    print(f"  DEBUG spot fetch -> {result}")
    if result.get("status") == "SUCCESS":
        d = result["data"]
        ltp = d["ltp"] / 100
        close = d["close"] / 100
        return ltp if ltp > 0 else close

    angel_result = get_angel_index_ltp(symbol)
    if angel_result and angel_result.get("status"):
        d = angel_result["data"]
        ltp = d["ltp"]
        close = d["close"]
        return ltp if ltp > 0 else close
    return None

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


def get_option_chain(symbol, spot_price, strike_range=500, nearest_expiry_only=True, current_month_only=False):
    """
    current_month_only=True overrides nearest_expiry_only -- instead of just
    the single nearest contract, keeps every weekly AND the monthly expiry
    that falls in the same calendar month as the NEAREST upcoming expiry
    (not necessarily today's month -- near month-end, every expiry left in
    today's month has already lapsed, e.g. today Aug 30 but the nearest
    tradeable NIFTY expiry is already Sep 1; anchoring to today's month
    would then return nothing). Used by the dashboard's chain view;
    option_signal.py's entry logic keeps using the single-nearest-expiry
    default.
    """
    options = df[
        (df["scripshortname"] == symbol) &
        (df["instrumentname"] == "OPTIDX") &
        (df["issuspended"] == "N") &
        (df["strikeprice"] >= spot_price - strike_range) &
        (df["strikeprice"] <= spot_price + strike_range)
    ].copy()

    if not options.empty and (nearest_expiry_only or current_month_only):
        # options["expirydate"] is the same unreliable column (off by 10
        # years) -- picking its min() was actually selecting an ALREADY
        # EXPIRED contract instead of the true nearest future one. Parse
        # the real date from scripname and filter to future expiries only.
        options["_real_expiry"] = options["scripname"].apply(parse_expiry_from_scripname)
        today = date.today()
        future_options = options[options["_real_expiry"] >= today]

        if current_month_only:
            if not future_options.empty:
                nearest = future_options["_real_expiry"].min()
                options = future_options[
                    (future_options["_real_expiry"].apply(lambda d: d.year) == nearest.year)
                    & (future_options["_real_expiry"].apply(lambda d: d.month) == nearest.month)
                ]
            else:
                options = future_options
        elif not future_options.empty:
            nearest = future_options["_real_expiry"].min()
            options = future_options[future_options["_real_expiry"] == nearest]
        options = options.drop(columns=["_real_expiry"])

    return options.sort_values(["strikeprice", "optiontype"])

def get_ltp(scripcode, index_name=None, strike=None, option_type=None):
    """
    Fetches an option contract's premium via Motilal, falling back to Angel
    One if that fails -- but only if index_name/strike/option_type are
    given, since Angel identifies contracts differently than Motilal's
    scripcode and needs those to look up the matching contract itself.
    """
    url = "https://openapi.motilaloswal.com/rest/report/v3/getltpdata"
    ltp_headers = headers.copy()
    ltp_headers["Authorization"] = _get_fresh_motilal_token()
    body = {
        "clientcode": "",
        "exchange": "NSEFO",
        "scripcode": int(scripcode)
    }
    try:
        response = requests.post(url, json=body, headers=ltp_headers, timeout=10)
        result = response.json()
    except Exception:
        result = {"status": "FAILED", "message": "Motilal request/parse error"}

    if result.get("status") == "SUCCESS":
        return result["data"]["ltp"] / 100

    if index_name and strike is not None and option_type:
        angel_result = get_angel_option_ltp(index_name, strike, option_type)
        if angel_result and angel_result.get("status"):
            return angel_result["data"]["ltp"]
    return None


def get_ltp_and_volume(scripcode):
    """
    Same Motilal endpoint as get_ltp(), but also returns the contract's
    traded volume -- used for the dashboard's per-strike volume view
    (highest-volume CALL/PUT strikes as informal resistance/support).
    Angel's LTP fallback doesn't expose volume, so this is Motilal-only:
    returns (None, None) if that request fails, with no fallback attempt.
    """
    url = "https://openapi.motilaloswal.com/rest/report/v3/getltpdata"
    ltp_headers = headers.copy()
    ltp_headers["Authorization"] = _get_fresh_motilal_token()
    body = {
        "clientcode": "",
        "exchange": "NSEFO",
        "scripcode": int(scripcode)
    }
    try:
        response = requests.post(url, json=body, headers=ltp_headers, timeout=10)
        result = response.json()
    except Exception:
        return None, None

    if result.get("status") == "SUCCESS":
        data = result["data"]
        return data["ltp"] / 100, data.get("volume")
    return None, None

if __name__ == "__main__":
    symbol = "NIFTY"
    spot = get_spot_price(symbol)
    print(f"\n{symbol} spot price: {spot}\n")

    if spot:
        chain = get_option_chain(symbol, spot)
        print(f"Found {len(chain)} contracts near spot {spot}\n")
        sample = chain.head(6)
        for _, row in sample.iterrows():
            ltp = get_ltp(row["scripcode"])
            print(f"{row['scripname']:<35} strike={row['strikeprice']:<8} {row['optiontype']}  LTP={ltp}")