import pandas as pd
import requests
from motilal_login import auth_token, headers
from angel_data import get_angel_index_ltp, get_angel_option_ltp

df = pd.read_csv("nsefo_scrips.csv")

INDEX_SCRIPCODES = {
    "NIFTY": 26000,
    "BANKNIFTY": 26009
}

def get_spot_price(symbol):
    """Fetch live index spot price (NIFTY or BANKNIFTY) via Motilal's LTP endpoint, falling back to Angel One on failure."""
    url = "https://openapi.motilaloswal.com/rest/report/v3/getltpdata"
    ltp_headers = headers.copy()
    ltp_headers["Authorization"] = auth_token
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

def get_option_chain(symbol, spot_price, strike_range=500, nearest_expiry_only=True):
    options = df[
        (df["scripshortname"] == symbol) &
        (df["instrumentname"] == "OPTIDX") &
        (df["issuspended"] == "N") &
        (df["strikeprice"] >= spot_price - strike_range) &
        (df["strikeprice"] <= spot_price + strike_range)
    ].copy()

    if nearest_expiry_only and not options.empty:
        nearest = options["expirydate"].min()
        options = options[options["expirydate"] == nearest]

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
    ltp_headers["Authorization"] = auth_token
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