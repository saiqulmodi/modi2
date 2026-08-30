import json
import os
import sys
import time
import logging
from datetime import datetime, date, time as dtime
import pytz

from option_signal import get_directional_signal, get_trend_signal, GAP_CAUTION_PCT
from option_chain import get_ltp
from send_telegram import send_telegram_message
from intraday_confirm import get_intraday_confirmation, get_option_volume_confirmation
from geopolitical_check import has_breaking_geopolitical_event


def _both_index_trend_line():
    """Both NIFTY and BANKNIFTY trend, appended to every alert regardless of
    which index the alert is actually about -- so a NIFTY alert still shows
    you BANKNIFTY's current trend for context, and vice versa."""
    lines = []
    for sym in ["NIFTY", "BANKNIFTY"]:
        _, note = get_trend_signal(sym)
        lines.append(f"{sym}: {note}")
    return "\n".join(lines)

IST = pytz.timezone('Asia/Kolkata')

# On the contract's own expiry day, don't open a FRESH position past this
# time -- not enough runway left before close for theta to not dominate
# any directional move. Weekly NIFTY/BANKNIFTY options decay fastest in
# their last few hours.
EXPIRY_DAY_ENTRY_CUTOFF = dtime(14, 0)

# On expiry day, force-close anything still open past this time regardless
# of stop-loss/trend, since theta decay accelerates hard into the close
# and liquidity/spreads worsen too.
EXPIRY_DAY_EXIT_CUTOFF = dtime(14, 30)

# Tracks at most one open "we alerted a BUY" position per index (NIFTY/
# BANKNIFTY), so the next run can check it for a protective SELL exit
# (stop-loss hit, or the original bullish thesis no longer holding)
# BEFORE considering a fresh entry. This is alert-only bookkeeping -- it
# does not reflect whether you actually placed the trade, since MODI2's
# real order placement stays DRY_RUN regardless.
OPEN_POSITIONS_FILE = "modi2_open_positions.json"


def load_open_positions():
    if os.path.exists(OPEN_POSITIONS_FILE):
        with open(OPEN_POSITIONS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_open_positions(state):
    with open(OPEN_POSITIONS_FILE, "w") as f:
        json.dump(state, f, indent=2)

# MODI4: automated order placement (still DRY_RUN=True there -- no real
# orders are possible until that's explicitly flipped off). Order execution
# goes through Motilal (real trading account); Angel above is only used for
# intraday candle/volume confirmation, unchanged.
sys.path.insert(0, r"C:\Users\saiqu\Projects\MODI4")
from place_order import place_order

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
    open_positions = load_open_positions()
    now_ist = datetime.now(IST)

    for symbol in ["NIFTY", "BANKNIFTY"]:
        # --- Protective exit: if we alerted a BUY on this index and haven't
        # alerted a SELL yet, check it BEFORE considering any new entry ---
        open_pos = open_positions.get(symbol)
        if open_pos:
            current_ltp = get_ltp(
                open_pos["scripcode"], index_name=symbol,
                strike=open_pos["strike"], option_type=open_pos["option_type"],
            )
            exit_reason = None
            if current_ltp is not None and current_ltp <= open_pos["stop_loss"]:
                exit_reason = f"stop-loss hit (premium Rs.{current_ltp:.2f} <= stop Rs.{open_pos['stop_loss']:.2f})"
            elif open_pos.get("expiry_date"):
                expiry_date = date.fromisoformat(open_pos["expiry_date"])
                days_left = (expiry_date - now_ist.date()).days
                if days_left <= 0 and now_ist.time() >= EXPIRY_DAY_EXIT_CUTOFF:
                    exit_reason = (
                        f"expires today, past {EXPIRY_DAY_EXIT_CUTOFF.strftime('%H:%M')} cutoff -- "
                        f"theta decay accelerates hard into the close"
                    )
            if exit_reason is None:
                intraday = get_intraday_confirmation(symbol)
                if intraday is not None and not intraday["confirms_bullish"]:
                    exit_reason = "intraday trend no longer confirms bullish"

            if exit_reason:
                pnl_pct = (
                    (current_ltp - open_pos["entry_price"]) / open_pos["entry_price"] * 100
                    if current_ltp is not None else None
                )
                message = (
                    f"🔴 <b>MODI2 SELL: {symbol} {open_pos['strike']}{open_pos['option_type']}</b>\n"
                    f"Reason: {exit_reason}\n"
                    f"Entry premium: Rs.{open_pos['entry_price']:.2f}"
                    + (f" -> Now: Rs.{current_ltp:.2f} ({pnl_pct:+.1f}%)" if current_ltp is not None else " -> Now: unavailable")
                    + "\nConsider selling manually (closing this existing position -- not a fresh short)."
                    + f"\n\n{_both_index_trend_line()}"
                )
                send_telegram_message(message)
                logging.info(f"Sent SELL alert for {symbol} {open_pos['strike']}{open_pos['option_type']}: {exit_reason}")
                del open_positions[symbol]
                save_open_positions(open_positions)
            # Whether it exited or is still healthy, don't also evaluate a
            # fresh entry for this symbol in the same run.
            continue

        result = get_directional_signal(symbol)
        direction = result["direction"]

        if direction not in ("CE", "PE"):
            logging.info(f"{symbol}: NEUTRAL, no alert sent")
            continue

        # Geopolitical shock guard: an LLM-classified check (not just
        # keyword matching -- see geopolitical_check.py for why) for a
        # genuinely NEW escalation, not routine ongoing-conflict coverage.
        # Entry-side only, same as the gap/expiry guards below -- a
        # protective SELL on an existing position (above) still fires
        # regardless, since exiting during a crisis shouldn't be suppressed.
        is_escalation, geo_detail = has_breaking_geopolitical_event()
        if is_escalation:
            logging.info(f"{symbol}: {direction} signal held back, breaking geopolitical escalation -- {geo_detail}")
            continue

        # Gap-move caution: a same-direction gap this big already used up
        # much of the move before the alert even fires, and carries real
        # intraday reversal risk. Informational threshold, not backtested
        # like the core trend/intraday/volume gate below -- so it holds
        # the entry back rather than silently ignoring it.
        gap = result["gap_pct"]
        gap_same_direction = (
            (direction == "CE" and gap is not None and gap >= GAP_CAUTION_PCT) or
            (direction == "PE" and gap is not None and gap <= -GAP_CAUTION_PCT)
        )
        if gap_same_direction:
            logging.info(f"{symbol}: {direction} signal held back, already gapped {gap:+.2f}% -- chasing an extended gap")
            continue

        # Theta-decay entry guard: on the contract's own expiry day, don't
        # open a brand new position once there's too little runway left
        # for direction to matter more than decay.
        if result["days_to_expiry"] == 0 and now_ist.time() >= EXPIRY_DAY_ENTRY_CUTOFF:
            logging.info(
                f"{symbol}: {direction} signal held back, expires today and it's past "
                f"{EXPIRY_DAY_ENTRY_CUTOFF.strftime('%H:%M')} -- too little runway left, theta dominates"
            )
            continue

        intraday = get_intraday_confirmation(symbol)
        if intraday is None:
            logging.info(f"{symbol}: {direction} signal held back, no intraday confirmation data available")
            continue
        confirms = intraday["confirms_bullish"] if direction == "CE" else intraday["confirms_bearish"]
        if not confirms:
            logging.info(f"{symbol}: {direction} signal held back, intraday action doesn't confirm ({intraday})")
            continue

        option_volume = get_option_volume_confirmation(symbol, result["strike"], direction)
        if option_volume is None:
            logging.info(f"{symbol}: {direction} signal held back, no option volume data available")
            continue
        if not option_volume["volume_confirms"]:
            logging.info(f"{symbol}: {direction} signal held back, option volume doesn't confirm ({option_volume})")
            continue

        emoji = "🟢" if direction == "CE" else "🟡"
        message = (
            f"{emoji} <b>MODI2 BUY: {symbol} {result['strike']}{direction}</b>\n"
        )
        if direction == "PE":
            # backtest.py: PE historically wins only 32-37% of the time at a
            # 10-day horizon (worse than a coin flip, and gets worse the
            # longer it's held) vs. CE's 52-60%. Always said plainly, not
            # buried, so a PE alert never reads as equally trustworthy.
            message += "⚠️ Historically weaker: PE wins ~32-37% (10-day) per backtest, vs. ~52-60% for CE.\n"
        message += f"Spot: {result['spot']}\nTrend: {result['note']}\n"
        if gap is not None:
            message += f"Gap from yday close: {gap:+.2f}%\n"
        message += (
            f"Intraday: VWAP {intraday['vwap']}, ORB breakout {intraday['orb_breakout']}\n"
            f"Option volume: {option_volume['recent_pace']}/candle vs {option_volume['baseline_pace']}/candle opening "
            f"({option_volume['volume_ratio']}x, needs {option_volume['volume_threshold']}x)"
        )
        if result["days_to_expiry"] is not None:
            message += f"\nExpiry: {result['expiry_date']} ({result['days_to_expiry']}d) "
            if result["days_to_expiry"] == 0:
                message += "-- expires TODAY, theta decay severe, exit well before close"
        if result["entry_price"] is not None:
            message += (
                f"\nEntry (premium): Rs.{result['entry_price']:.2f}"
                f"\nStop-loss: Rs.{result['stop_loss']:.2f} (-30% of premium)"
            )
        message += f"\n\n{_both_index_trend_line()}"
        send_telegram_message(message)
        logging.info(f"Sent BUY alert for {symbol}: {direction} {result['strike']}")

        if result["entry_price"] is not None and result["scripcode"] is not None:
            open_positions[symbol] = {
                "strike": result["strike"],
                "option_type": direction,
                "scripcode": result["scripcode"],
                "scripname": result["scripname"],
                "entry_price": result["entry_price"],
                "stop_loss": result["stop_loss"],
                "expiry_date": result["expiry_date"],
                "date": now_ist.strftime("%Y-%m-%d"),
            }
            save_open_positions(open_positions)

        # MODI4 auto-trading (still DRY_RUN there): always 1 lot for
        # options -- rupee-based position sizing doesn't translate well
        # here, since one full lot of an index option often costs more
        # than the whole target position size (e.g. NIFTY premium x
        # ~75-unit lot size). Motilal's API takes quantity directly in
        # lots (quantityinlot), so no lot-size math needed here.
        if result["entry_price"] is not None and result["scripcode"] is not None:
            place_order(
                symbol=f"{symbol}_{result['strike']}{direction}", scripcode=result["scripcode"],
                exchange="NSEFO", transaction_type="BUY", quantity=1,
                entry_price=result["entry_price"], stop_loss=result["stop_loss"],
                product_type="NORMAL",
            )
        elif result["entry_price"] is not None:
            logging.info(f"{symbol}: MODI4 order skipped, no Motilal scripcode found for strike {result['strike']}")

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