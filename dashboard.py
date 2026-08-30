import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st
import pandas as pd
from option_chain import get_option_chain, get_spot_price, get_ltp_and_volume, parse_expiry_from_scripname

# How far either side of spot to show strikes -- wider for BANKNIFTY since
# its 100-point strike step and higher spot price mean +-600 would only
# span 6 strikes each side, vs. 12 for NIFTY's 50-point step.
STRIKE_RANGE = {
    "NIFTY": 600,
    "BANKNIFTY": 1200,
}

# The full current-month, wide-range fetch is ~200+ contracts and takes
# ~1-2 minutes (Motilal rate-limits that request volume). Caching the built
# table in memory means only the FIRST load per TTL window pays that cost --
# module-level dict, not st.cache_data, so it persists across Streamlit
# reruns within this one running server process (same pattern as MODI7's
# ai_synthesis.py). 3 min balances staying reasonably fresh intraday against
# not re-paying the full fetch cost on every click.
_CHAIN_CACHE_TTL_SECONDS = 180
_chain_cache = {}

# "centered" (Streamlit's default) instead of "wide" -- wide mode was for
# the old full-width raw-column table; this compact 4-column view looks
# better centered and narrow, especially on a phone screen.
st.set_page_config(page_title="MODI2 Option Chain", layout="centered")

st.title("📈 MODI2 Live Option Chain")

# Create a text box for you to easily change the symbol from your phone
symbol = st.text_input("Enter Symbol:", "NIFTY").upper()

if st.button("Load Option Chain"):
    try:
        if symbol not in ("NIFTY", "BANKNIFTY"):
            st.error(f"'{symbol}' isn't supported -- only NIFTY and BANKNIFTY have live option chains here.")
            st.stop()

        strike_range = STRIKE_RANGE[symbol]
        cached = _chain_cache.get(symbol)
        cache_age = time.time() - cached[0] if cached else None

        if cached and cache_age < _CHAIN_CACHE_TTL_SECONDS:
            simple_df = cached[1]
            st.success(f"Loaded {len(simple_df)} contracts from cache ({cache_age:.0f}s old, spot ±{strike_range}, all current-month expiries).")
        else:
            with st.spinner(f"Fetching live data for {symbol}..."):
                spot_price = get_spot_price(symbol)
                if spot_price is None:
                    st.error(f"Couldn't fetch a live spot price for {symbol} right now (both Motilal and Angel failed) -- try again in a moment.")
                    st.stop()

                chain_df = get_option_chain(symbol, spot_price, strike_range=strike_range, current_month_only=True)
                if chain_df is None or chain_df.empty:
                    st.warning(f"No option contracts found for {symbol}.")
                    st.stop()

                st.success(f"Fetched {len(chain_df)} contracts fresh (spot ±{strike_range}, all current-month expiries).")

                # current_month_only=True can pull 200+ contracts (all
                # weeklies + the monthly) -- fetching LTP/volume one at a
                # time sequentially would take minutes. Concurrent fetch
                # instead, same pattern as MODI7's get_bulk_fundamentals.
                chain_rows = list(chain_df.iterrows())
                ltp_volume_by_index = {}
                progress_bar = st.progress(0)
                status_text = st.empty()
                with ThreadPoolExecutor(max_workers=8) as executor:
                    future_to_index = {
                        executor.submit(get_ltp_and_volume, row["scripcode"]): i
                        for i, (_, row) in enumerate(chain_rows)
                    }
                    completed = 0
                    for future in as_completed(future_to_index):
                        index = future_to_index[future]
                        try:
                            ltp_volume_by_index[index] = future.result()
                        except Exception:
                            ltp_volume_by_index[index] = (None, None)
                        completed += 1
                        progress_bar.progress(completed / len(chain_rows))
                        status_text.text(f"{completed}/{len(chain_rows)} contracts fetched...")
                status_text.empty()
                progress_bar.empty()

                # A burst of ~200+ concurrent requests hits Motilal's rate
                # limit and a handful fail outright (verified: retried slowly
                # one at a time, they succeed every time) -- one sequential
                # retry pass, spaced out, mops up the stragglers instead of
                # showing them as permanently N/A.
                failed_indices = [i for i, v in ltp_volume_by_index.items() if v[0] is None]
                if failed_indices:
                    retry_status = st.empty()
                    for n, i in enumerate(failed_indices, start=1):
                        retry_status.text(f"Retrying {n}/{len(failed_indices)} contracts that hit a rate limit...")
                        time.sleep(0.3)
                        try:
                            ltp_volume_by_index[i] = get_ltp_and_volume(chain_rows[i][1]["scripcode"])
                        except Exception:
                            pass
                    retry_status.empty()

                rows = []
                for i, (_, row) in enumerate(chain_rows):
                    expiry = parse_expiry_from_scripname(row.get("scripname"))
                    ltp, volume = ltp_volume_by_index[i]
                    rows.append({
                        "Strike": row["strikeprice"],
                        "Type": "Call" if row["optiontype"] == "CE" else "Put",
                        "Expiry": expiry.strftime("%d-%b-%Y") if expiry else "Unknown",
                        "Value": ltp,
                        "Volume": volume,
                    })
                simple_df = pd.DataFrame(rows)
                # Multiple expiries are now mixed together (current_month_only=True
                # pulls every weekly + the monthly) -- group by expiry first so
                # same-strike contracts from different expiries don't interleave.
                simple_df["_expiry_sort"] = pd.to_datetime(simple_df["Expiry"], format="%d-%b-%Y", errors="coerce")
                simple_df = simple_df.sort_values(["_expiry_sort", "Strike", "Type"]).drop(columns=["_expiry_sort"])
                _chain_cache[symbol] = (time.time(), simple_df)

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
                "Based on today's traded volume per strike, across all current-month "
                "expiries shown below (Motilal doesn't expose open interest via this "
                "endpoint) -- a read on where activity is concentrated, not a confirmed "
                "level. Informational only."
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

    except Exception as e:
        st.error(f"An error occurred while fetching data: {e}")
