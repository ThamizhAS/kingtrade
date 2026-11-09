# Angel One 1-Minute Stock Scanner

## Features
- Streamlit dashboard with Angel One SmartAPI login and WebSocket V2 live LTP.
- Enter NSE/BSE cash-market equity symbols (e.g. `RELIANCE,INFY,SBIN`).
- Set a percentage threshold; stocks at or above it appear in the Triggered Stocks list.
- Calculation: `(LTP - current 1-minute candle open) / current 1-minute candle open * 100`.
- No order placement functionality.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Enter API Key, Client Code, Trading PIN and TOTP Base32 secret in the sidebar. These are used for login in the current Streamlit session. Do not share them or commit them to source control.

## Important candle-open behavior
The app uses the first live trade tick received during each minute as that minute's open. If the scanner starts part-way through a minute, that first partial minute's open is approximate; the next minute starts a new candle. For an exchange-accurate candle open when starting mid-minute, add a Historical Candle API seed for the current `ONE_MINUTE` candle.

## Notes
- Symbols are resolved from Angel One's public instrument master at runtime; NSE/BSE EQ symbols only.
- Requires an active Angel One SmartAPI account/API access, internet connection and valid TOTP secret.
- Angel One may enforce API/WebSocket limits or change its SDK/API behavior.
