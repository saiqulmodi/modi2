import streamlit as st
import pandas as pd
from option_chain import get_option_chain, get_spot_price, get_ltp_and_volume, parse_expiry_from_scripname

# How far either side of spot to show strikes -- narrower than the 500-point
# default get_option_chain() uses elsewhere, focused on the range actually
# relevant for reading near-the-money support/resistance.
STRIKE_RANGE = 300

# "centered" (Streamlit's default) instead of "wide" -- wide mode was for
# the old full-width raw-column table; this compact 4-column view looks
# better centered and narrow, especially on a phone screen.
st.set_page_config(page_title="MODI2 Option Chain", layout="centered")

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

            chain_df = get_option_chain(symbol, spot_price, strike_range=STRIKE_RANGE)

            if chain_df is not None and not chain_df.empty:
                st.success(f"Successfully loaded {len(chain_df)} contracts (spot ±{STRIKE_RANGE}).")

                rows = []
                for _, row in chain_df.iterrows():
                    expiry = parse_expiry_from_scripname(row.get("scripname"))
                    ltp, volume = get_ltp_and_volume(row["scripcode"])
                    rows.append({
                        "Strike": row["strikeprice"],
                        "Type": "Call" if row["optiontype"] == "CE" else "Put",
                        "Expiry": expiry.strftime("%d-%b-%Y") if expiry else "Unknown",
                        "Value": ltp,
                        "Volume": volume,
                    })
                simple_df = pd.DataFrame(rows)

                # Highest-volume CALL strike = where the most call activity is
                # concentrated (informal resistance -- a lot of call writing/
                # buying tends to cluster near a level the market expects to
                # cap upside). Highest-volume PUT strike = informal support,
                # same logic on the downside. This is informational only --
                # it never generates a buy/sell signal on its own, and MODI2
                # never sells to open a fresh position either way (see
                # live_alerts.py: SELL alerts only ever close an existing
                # tracked BUY, never a new short).
                calls = simple_df[simple_df["Type"] == "Call"].dropna(subset=["Volume"])
                puts = simple_df[simple_df["Type"] == "Put"].dropna(subset=["Volume"])
                if not calls.empty or not puts.empty:
                    st.subheader("Volume-based support / resistance")
                    sr1, sr2 = st.columns(2)
                    if not calls.empty:
                        top_call = calls.loc[calls["Volume"].idxmax()]
                        sr1.metric(
                            "Resistance (max Call volume)",
                            f"{top_call['Strike']:.0f}",
                            help=f"Volume {top_call['Volume']:,.0f}",
                        )
                    if not puts.empty:
                        top_put = puts.loc[puts["Volume"].idxmax()]
                        sr2.metric(
                            "Support (max Put volume)",
                            f"{top_put['Strike']:.0f}",
                            help=f"Volume {top_put['Volume']:,.0f}",
                        )
                    st.caption(
                        "Based on today's traded volume per strike (Motilal doesn't expose open "
                        "interest via this endpoint) -- a read on where activity is concentrated, "
                        "not a confirmed level. Informational only."
                    )

                st.dataframe(
                    simple_df.style.format({
                        "Value": lambda v: f"Rs.{v:.2f}" if v is not None else "N/A",
                        "Volume": lambda v: f"{v:,.0f}" if v is not None else "N/A",
                    }),
                    hide_index=True,
                    use_container_width=False,
                    column_config={
                        "Strike": st.column_config.NumberColumn(width="small"),
                        "Type": st.column_config.TextColumn(width="small"),
                        "Expiry": st.column_config.TextColumn(width="small"),
                        "Value": st.column_config.TextColumn(width="small"),
                        "Volume": st.column_config.TextColumn(width="small"),
                    },
                )
            else:
                st.warning(f"No option contracts found for {symbol}.")
                
    except Exception as e:
        st.error(f"An error occurred while fetching data: {e}")