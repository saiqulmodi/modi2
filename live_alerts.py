import time
from datetime import datetime
import pytz # Make sure to 'pip install pytz' if you haven't already

# Import your functions from the other files. 
# (You may need to wrap the main logic in option_signal.py and send_telegram.py into functions)
# from option_signal import generate_signal
# from send_telegram import send_message

def is_market_open():
    """Checks if the current IST time is between 9:15 AM and 3:30 PM on a weekday."""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    
    # Check if it's the weekend (Monday is 0, Sunday is 6)
    if now.weekday() > 4:
        return False
        
    # Define market start and end times
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_start <= now <= market_end

def run_alert_system():
    print("Starting market alert monitor... Press Ctrl+C to stop.")
    
    while True:
        if is_market_open():
            current_time = datetime.now().strftime('%H:%M:%S')
            print(f"[{current_time}] Market is open. Checking signals...")
            
            # --- PUT YOUR LOGIC HERE ---
            # 1. Fetch your signal: 
            # signal_message = generate_signal() 
            
            # 2. If you got a signal, send it:
            # if signal_message:
            #     send_message(signal_message)
            # ---------------------------
            
            # Wait for X seconds before checking again (e.g., 5 minutes = 300 seconds)
            time.sleep(300) 
        else:
            current_time = datetime.now().strftime('%H:%M:%S')
            print(f"[{current_time}] Market is closed. Sleeping for 60 seconds...")
            # Check more frequently when closed so it triggers right at 9:15 AM
            time.sleep(60) 

if __name__ == "__main__":
    try:
        run_alert_system()
    except KeyboardInterrupt:
        print("\nAlert system stopped manually.")