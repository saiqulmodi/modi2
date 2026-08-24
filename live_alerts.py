import time
import logging
from datetime import datetime
import pytz

from option_signal import get_directional_signal
from send_telegram import send_telegram_message
from intraday_confirm import get_intraday_confirmation, get_option_volume_confirmation

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

        if result["direction"] == "PE":
            # Suppressed: backtest.py showed PE calls are worse than a coin
            # flip (32-37% win rate at 10-day horizon) and get worse the
            # longer they're held, so they're not alerted on for now.
            logging.info(f"{symbol}: PE signal suppressed (unreliable per backtest), no alert sent")
            continue

        if result["direction"] == "CE":
            intraday = get_intraday_confirmation(symbol)
            if intraday is None:
                logging.info(f"{symbol}: CE signal held back, no intraday confirmation data available")
                continue
            if not intraday["confirms_bullish"]:
                logging.info(f"{symbol}: CE signal held back, intraday action doesn't confirm ({intraday})")
                continue

            option_volume = get_option_volume_confirmation(symbol, result["strike"], "CE")
            if option_volume is None:
                logging.info(f"{symbol}: CE signal held back, no option volume data available")
                continue
            if not option_volume["volume_confirms"]:
                logging.info(f"{symbol}: CE signal held back, option volume doesn't confirm ({option_volume})")
                continue

            message = (
                f"<b>MODI2 Signal: {symbol}</b>\n"
                f"Spot: {result['spot']}\n"
                f"Trend: {result['note']}\n"
                f"Intraday: VWAP {intraday['vwap']}, ORB breakout {intraday['orb_breakout']}\n"
                f"Option volume: {option_volume['recent_pace']}/candle vs {option_volume['baseline_pace']}/candle opening "
                f"({option_volume['volume_ratio']}x, needs {option_volume['volume_threshold']}x)\n"
                f"Direction: {result['direction']} {result['strike']}"
            )
            if result["entry_price"] is not None:
                message += (
                    f"\nEntry (premium): Rs.{result['entry_price']:.2f}"
                    f"\nStop-loss: Rs.{result['stop_loss']:.2f} (-30% of premium)"
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