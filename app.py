import io
import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ---------------- Page configuration ----------------
st.set_page_config(page_title="Pro Breakout Screener", page_icon="📈", layout="wide")
st.title("🎯 Institutional-Grade Volume Breakout Scanner")
st.markdown(
    "A systematic screening tool combining **EMA Trend Stack**, **20-Day "
    "Breakouts**, **RVOL ≥ 2.0x**, and **CLV Quality Filters**."
)

NIFTY500_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
CACHE_FILE = Path("nifty500_constituents.csv")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Referer": "https://www.niftyindices.com/",
}


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def load_nifty500_tickers():
    """Load the complete Nifty 500 list; cache it for 24 hours and save a local copy."""
    errors = []
    for url in NIFTY500_URLS:
        try:
            response = requests.get(url, headers=HEADERS, timeout=25)
            response.raise_for_status()
            csv_text = response.content.decode("utf-8-sig", errors="replace")
            constituents = pd.read_csv(io.StringIO(csv_text))
            constituents.columns = [str(c).strip() for c in constituents.columns]

            symbol_col = next(
                (c for c in constituents.columns if c.lower() in {"symbol", "ticker"}),
                None,
            )
            if symbol_col is None:
                raise ValueError("CSV did not contain a Symbol column")

            symbols = (
                constituents[symbol_col]
                .dropna()
                .astype(str)
                .str.strip()
                .loc[lambda s: s.ne("")]
                .drop_duplicates()
                .tolist()
            )
            if len(symbols) < 450:
                raise ValueError(f"Only {len(symbols)} symbols received; expected Nifty 500")

            tickers = [s if s.endswith(".NS") else f"{s}.NS" for s in symbols]
            # Save the downloaded official constituent file for reuse during this deployment.
            try:
                CACHE_FILE.write_bytes(response.content)
            except OSError:
                pass
            return tickers, f"Downloaded current constituent list ({len(tickers)} symbols)."
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    # Use the previously downloaded constituent file if the live source is temporarily down.
    if CACHE_FILE.exists():
        try:
            cached = pd.read_csv(CACHE_FILE)
            cached.columns = [str(c).strip() for c in cached.columns]
            symbol_col = next(
                (c for c in cached.columns if c.lower() in {"symbol", "ticker"}),
                None,
            )
            if symbol_col:
                symbols = (
                    cached[symbol_col].dropna().astype(str).str.strip()
                    .loc[lambda s: s.ne("")].drop_duplicates().tolist()
                )
                if len(symbols) >= 450:
                    tickers = [s if s.endswith(".NS") else f"{s}.NS" for s in symbols]
                    return tickers, f"Using saved constituent file ({len(tickers)} symbols); live refresh failed."
        except Exception as exc:
            errors.append(f"Saved CSV: {exc}")

    raise RuntimeError(
        "Could not obtain the Nifty 500 constituent list. "
        + " | ".join(errors)
        + " Upload/restore nifty500_constituents.csv or retry later."
    )


def normalize_yfinance_columns(df):
    """Flatten yfinance's MultiIndex columns without changing the data."""
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance can return either (Price, Ticker) or (Ticker, Price).
        known = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
        for level in range(df.columns.nlevels):
            values = set(map(str, df.columns.get_level_values(level)))
            if known.intersection(values):
                df.columns = df.columns.get_level_values(level)
                break
    return df


def run_master_scanner(stock_list):
    shortlisted_stocks = []
    failed_downloads = []
    skipped_data = []
    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(stock_list)

    for i, ticker in enumerate(stock_list):
        status_text.text(f"Scanning ({i + 1}/{total}): {ticker}...")
        try:
            # Daily candles; run after market close for completed-session signals.
            df = yf.download(
                ticker,
                period="6mo",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=20,
            )

            if df is None or df.empty:
                failed_downloads.append(ticker)
                continue

            df = normalize_yfinance_columns(df.copy())
            required = {"High", "Low", "Close", "Volume"}
            if not required.issubset(set(df.columns)):
                skipped_data.append(ticker)
                continue

            df = df.dropna(subset=["High", "Low", "Close", "Volume"])
            if len(df) < 51:
                skipped_data.append(ticker)
                continue

            # Original indicators and thresholds retained.
            df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
            df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
            df["SMA50"] = df["Close"].rolling(window=50).mean()
            df["Vol_SMA20"] = df["Volume"].rolling(window=20).mean()
            df["20Day_High"] = df["High"].rolling(window=20).max()

            latest = df.iloc[-1]
            prev = df.iloc[-2]

            # Tier 1: Trend Stack
            if not (
                latest["EMA9"] > latest["EMA20"]
                and latest["EMA20"] > latest["SMA50"]
            ):
                continue

            # Tier 2: Breakout above the prior 20 completed sessions.
            if not (latest["Close"] >= prev["20Day_High"]):
                continue

            # Tier 3: RVOL uses the previous 20-session average, excluding today.
            prior_volume_average = prev["Vol_SMA20"]
            if pd.isna(prior_volume_average) or prior_volume_average <= 0:
                continue
            rvol = latest["Volume"] / prior_volume_average
            if pd.isna(rvol) or rvol < 2.0:
                continue

            # Tier 4: CLV
            high_low_range = latest["High"] - latest["Low"]
            if pd.isna(high_low_range) or high_low_range <= 0:
                continue
            clv = (latest["Close"] - latest["Low"]) / high_low_range
            if pd.isna(clv) or clv < 0.75:
                continue

            shortlisted_stocks.append(
                {
                    "Ticker": ticker.removesuffix(".NS"),
                    "Close Price": round(float(latest["Close"]), 2),
                    "RVOL": round(float(rvol), 2),
                    "CLV": round(float(clv), 2),
                }
            )
        except Exception:
            failed_downloads.append(ticker)
        finally:
            progress_bar.progress((i + 1) / total)

    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(shortlisted_stocks), failed_downloads, skipped_data


# ---------------- Universe selection ----------------
st.subheader("Nifty 500 Universe")
try:
    nifty500_tickers, universe_status = load_nifty500_tickers()
    st.success(f"{universe_status} The scanner universe is the full Nifty 500.")
    use_nifty500 = st.checkbox("Scan all Nifty 500 stocks", value=True)
except Exception as exc:
    nifty500_tickers = []
    st.error(str(exc))
    use_nifty500 = False

with st.expander("Optional: scan a custom ticker list instead"):
    default_tickers = (
        "PERSISTENT.NS, MPHASIS.NS, COFORGE.NS, LTTS.NS, KPITTECH.NS, "
        "OBEROIRLTY.NS, GODREJPROP.NS, PHOENIXLTD.NS, SUPREMEIND.NS, ASTRAL.NS"
    )
    ticker_input = st.text_area(
        "Enter NSE tickers (comma separated):", default_tickers, height=90
    )

if st.button("🚀 Run Master Scanner", type="primary"):
    if use_nifty500:
        tickers_to_scan = nifty500_tickers
    else:
        tickers_to_scan = [
            t.strip().upper()
            for t in ticker_input.split(",")
            if t.strip()
        ]
        tickers_to_scan = [
            t if t.endswith(".NS") else f"{t}.NS" for t in tickers_to_scan
        ]

    if not tickers_to_scan:
        st.warning("No tickers available. Check the Nifty 500 list or enter custom tickers.")
    else:
        st.info(f"Starting scan of {len(tickers_to_scan)} stocks using daily candles.")
        with st.spinner("Fetching daily data and applying the four original filters..."):
            result_df, failed_downloads, skipped_data = run_master_scanner(tickers_to_scan)

        if not result_df.empty:
            result_df = result_df.sort_values(
                ["RVOL", "CLV"], ascending=[False, False]
            ).reset_index(drop=True)
            st.success(f"Found {len(result_df)} breakout candidate(s).")
            st.dataframe(result_df, use_container_width=True, hide_index=True)
        else:
            st.info("No stocks matched all four strict filters today.")

        if failed_downloads or skipped_data:
            st.warning(
                f"Data note: {len(failed_downloads)} download failures; "
                f"{len(skipped_data)} symbols had insufficient/unusable data. "
                "A failed download is not treated as a non-breakout."
            )
            with st.expander("View symbols with data issues"):
                if failed_downloads:
                    st.write("Download failures:", ", ".join(failed_downloads))
                if skipped_data:
                    st.write("Insufficient/unusable data:", ", ".join(skipped_data))

st.caption(
    "Signal quality depends on data availability and market conditions. "
    "This is a screening tool, not a recommendation to buy or sell."
)
