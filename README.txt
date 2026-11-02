Nifty 500 Pro Breakout Screener

Files:
- app.py: Streamlit application
- requirements.txt: Python dependencies

GitHub / Streamlit Community Cloud deployment:
1. Extract this ZIP.
2. Upload app.py and requirements.txt to the root of your GitHub repository.
3. In Streamlit Community Cloud, select that repository and set Main file path to app.py.
4. Deploy. The app fetches the current Nifty 500 constituent CSV and caches it for 24 hours.
   It also saves a local copy as nifty500_constituents.csv when the host permits writing.

The app scans daily candles and is intended to be run after market close.
The four strategy filters are retained: EMA9 > EMA20 > SMA50, close breakout over
the previous 20 sessions' high, RVOL >= 2.0 using the prior 20-session volume
average (excluding the current session), and CLV >= 0.75.
No Telegram integration or additional indicators are included.
