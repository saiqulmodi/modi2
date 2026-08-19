import time
import logging
from datetime import datetime
import pytz

from option_signal import get_directional_signal
from send_telegram import send_telegram_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("market_alerts.log"),
        logging.StreamHandler()
    ]
)

def is_market_open():
    """Checks if the current IST time is between 9:15 AM and 3:45 PM on a weekday."""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)

    if now.weekday() > 4:  # 0-4 are Monday-Friday
        return False

    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=45, second=0, microsecond=0)

    return market_start <= now <= market_end

def check_and_alert():
    for symbol in ["NIFTY", "BANKNIFTY"]:
        result = get_directional_signal(symbol)
        if result["direction"] in ("CE", "PE"):
            message = (
                f"<b>MODI2 Signal: {symbol}</b>\n"
                f"Spot: {result['spot']}\n"
                f"Trend: {result['note']}\n"
                f"Direction: {result['direction']} {result['strike']}"
            )
            send_telegram_message(message)
            logging.info(f"Sent alert for {symbol}: {result['direction']} {result['strike']}")
        else:
            logging.info(f"{symbol}: NEUTRAL, no alert sent")

def run_alert_system():
    logging.info("Starting MODI2 alert monitor... Press Ctrl+C to stop.")

    while True:
        try:
            if is_market_open():
                logging.info("Market is open. Checking signals...")
                check_and_alert()
                time.sleep(300)  # check every 5 minutes
            else:
                logging.info("Market is closed. Sleeping for 60 seconds...")
                time.sleep(60)

        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            logging.info("Retrying in 60 seconds...")
            time.sleep(60)

if __name__ == "__main__":
    try:
        run_alert_system()
    except KeyboardInterrupt:
        logging.info("Alert system stopped manually (Ctrl+C).")