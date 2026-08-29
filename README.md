# MODI2 — NIFTY/BANKNIFTY Options Signals & Live Alerts

Live NIFTY/BANKNIFTY option-chain dashboard (`dashboard.py`) plus a
directional signal engine (`option_signal.py`) that alerts via Telegram/
WhatsApp on entry setups (`live_alerts.py`). Trades NSEFO options — kept in
`DRY_RUN` (simulate only) via `risk_manager`/kill-switch style guards shared
conceptually with MODI4, while MODI1 (NSE equities) is the one allowed live.

## Setup

None of the credential files below are committed — each is gitignored
because it holds live broker/messaging secrets. Recreate them locally with
your own values before running anything.

### `angel_login.py` and `motilal_login.py`

Same templates as MODI1's README — see
[MODI1/README.md](https://github.com/saiqulmodi/modi1#setup) for the full
file content. Both are required here (`angel_data.py`, `option_chain.py`,
`intraday_confirm.py` use them for live quotes/candles).

### `send_telegram.py`

```python
import requests

BOT_TOKEN = "your-bot-token-here"
CHAT_ID = "your-chat-id-here"

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    response = requests.post(url, data=payload)
    if response.status_code != 200:
        print(f"Telegram send failed: {response.text}")
    return response.status_code == 200
```

### `whatsapp_config.py` (used by `send_alert.py`)

```python
TWILIO_ACCOUNT_SID = "your-twilio-account-sid"
TWILIO_AUTH_TOKEN = "your-twilio-auth-token"
TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"   # your Twilio WhatsApp sender number
MY_WHATSAPP_NUMBER = "whatsapp:+91XXXXXXXXXX"    # your number, with country code
```

Get Telegram credentials from [@BotFather](https://t.me/BotFather); Twilio
credentials from the [Twilio Console](https://console.twilio.com/); Angel
One / Motilal API keys from their respective developer portals (see
MODI1's README for the links).

**Status: WhatsApp alerting is currently not active** — Twilio requires a
paid subscription/sender approval to send outside the sandbox's limited
window, which isn't set up right now. `send_alert.py` calls will fail
silently in that path until a paid Twilio plan is in place; Telegram
alerting (`send_telegram.py`) is unaffected and is the working channel.

## Running

- `run_live_alerts.bat` — runs `live_alerts.py` (the alerting loop)
- `python -m streamlit run dashboard.py` — live option-chain dashboard

`nsefo_scrips.csv`, `angel_scrips.json`, and `watchlist.json` are gitignored
generated/reference data — regenerate via `download_angel_scrips.py` or
copy across from MODI1 as needed.
