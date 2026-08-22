"""
Sends an alert via Telegram first; if that fails, falls back to WhatsApp
(via Twilio) so alerts still get through if Telegram is down.

Requires your WhatsApp number to have joined the Twilio sandbox, and that
join to be refreshed within the last 24 hours -- Twilio's sandbox only
allows freeform (non-template) messages within a 24h window of your last
inbound message to it. Outside that window, sends will fail with a
"ContentSid Required" / error 63016 style error.
"""

import re
from send_telegram import send_telegram_message
from twilio.rest import Client
from whatsapp_config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, MY_WHATSAPP_NUMBER


def send_whatsapp_message(text):
    plain_text = re.sub(r"</?[^>]+>", "", text)  # Twilio WhatsApp doesn't support HTML tags
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(from_=TWILIO_WHATSAPP_FROM, body=plain_text, to=MY_WHATSAPP_NUMBER)
        return True
    except Exception as e:
        print(f"WhatsApp send failed: {e}")
        return False


def send_alert(text):
    """Tries Telegram first; falls back to WhatsApp only if Telegram fails."""
    if send_telegram_message(text):
        return True
    print("Telegram send failed, falling back to WhatsApp...")
    return send_whatsapp_message(text)


if __name__ == "__main__":
    send_alert("MODI2 alert fallback test: Telegram -> WhatsApp wiring works!")
