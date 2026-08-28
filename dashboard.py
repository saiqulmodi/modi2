import streamlit as st
import pandas as pd
from option_chain import get_option_chain, get_spot_price, get_ltp, parse_expiry_from_scripname

# Set the page to be wide so the tables fit nicely on mobile
st.set_page_config(page_title="MODI2 Option Chain", layout="wide")

st.title("📈 MODI2 Live Option Chain")

# Create a text box for you to easily change the symbol from your phone
symbol = st.text_input("Enter Symbol:", "NIFTY").upper()

if st.button("Load Option Chain"):
    try:
        with st.spinner(f"Fetching live data for {symbol}..."):
            if symbol not in ("NIFTY", "BANKNIFTY"):
                st.error(f"'{symbol}' isn't supported -- only NIFTY and BANKNIFTY have live option chains here.")
                st.stop()

            # Call your existing function
            spot_price = get_spot_price(symbol)
            if spot_price is None:
                st.error(f"Couldn't fetch a live spot price for {symbol} right now (both Motilal and Angel failed) -- try again in a moment.")
                st.stop()

            chain_df = get_option_chain(symbol, spot_price)

            if chain_df is not None and not chain_df.empty:
                st.success(f"Successfully loaded {len(chain_df)} contracts.")

                rows = []
                for _, row in chain_df.iterrows():
                    expiry = parse_expiry_from_scripname(row.get("scripname"))
                    rows.append({
                        "Strike": row["strikeprice"],
                        "Type": "Call" if row["optiontype"] == "CE" else "Put",
                        "Expiry": expiry.strftime("%d-%b-%Y") if expiry else "Unknown",
                        "Value": get_ltp(row["scripcode"]),
                    })
                simple_df = pd.DataFrame(rows)

                st.dataframe(
                    simple_df.style.format({"Value": lambda v: f"Rs.{v:.2f}" if v is not None else "N/A"}),
                    use_container_width=True, hide_index=True,
                )
            else:
                st.warning(f"No option contracts found for {symbol}.")
                
    except Exception as e:
        st.error(f"An error occurred while fetching data: {e}")