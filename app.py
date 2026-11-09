import json
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
import requests
import streamlit as st
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

IST = ZoneInfo("Asia/Kolkata")
MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

st.set_page_config(page_title="Angel One 1-Min Stock Scanner", page_icon="📈", layout="wide")
st.title("📈 Angel One — 1 Minute Stock Scanner")
st.caption("Live WebSocket LTP • 1-minute candle open • Threshold breakout watchlist")

@st.cache_data(ttl=3600, show_spinner=False)
def load_master():
    response = requests.get(MASTER_URL, timeout=30)
    response.raise_for_status()
    return response.json()


def resolve_symbols(symbols, exchange, master):
    wanted = {s.strip().upper().replace("-EQ", "") for s in symbols if s.strip()}
    found = {}
    for row in master:
        if row.get("exch_seg") != exchange:
            continue
        ts = row.get("symbol", "").upper()
        if ts.endswith("-EQ") and ts[:-3] in wanted:
            found[ts[:-3]] = {
                "symbol": ts,
                "token": str(row["token"]),
                "exchange": exchange,
                "exchange_type": 1 if exchange == "NSE" else 3,
            }
    missing = sorted(wanted - set(found))
    return found, missing


class LiveScanner:
    def __init__(self, auth_token, api_key, client_code, feed_token, instruments, threshold):
        self.instruments = instruments
        self.threshold = float(threshold)
        self.lock = threading.Lock()
        self.rows = {
            name: {"symbol": item["symbol"], "ltp": None, "minute_open": None,
                   "change_pct": None, "minute": None, "updated": None, "triggered": False}
            for name, item in instruments.items()
        }
        self.error = None
        self.connected = False
        self.stop_event = threading.Event()
        self.ws = SmartWebSocketV2(auth_token, api_key, client_code, feed_token,
                                   max_retry_attempt=5, retry_strategy=1)

        def on_open(wsapp):
            self.connected = True
            token_list = [{"exchangeType": instruments[next(iter(instruments))]["exchange_type"],
                           "tokens": [x["token"] for x in instruments.values()]}]
            self.ws.subscribe("scan1min001", self.ws.LTP_MODE, token_list)

        def on_data(wsapp, tick):
            try:
                token = str(tick.get("token", ""))
                item = next((n for n, x in instruments.items() if x["token"] == token), None)
                if item is None:
                    return
                ltp = float(tick["last_traded_price"]) / 100.0
                tick_time = datetime.fromtimestamp(tick.get("exchange_timestamp", int(time.time()*1000))/1000, IST)
                minute_key = tick_time.strftime("%Y-%m-%d %H:%M")
                with self.lock:
                    row = self.rows[item]
                    if row["minute"] != minute_key:
                        # First received trade tick in each minute is used as that minute's open.
                        row["minute"] = minute_key
                        row["minute_open"] = ltp
                    row["ltp"] = ltp
                    op = row["minute_open"]
                    row["change_pct"] = ((ltp - op) / op * 100.0) if op else None
                    row["updated"] = tick_time.strftime("%H:%M:%S")
                    row["triggered"] = row["change_pct"] is not None and row["change_pct"] >= self.threshold
            except Exception as exc:
                self.error = f"Tick parse error: {exc}"

        def on_error(wsapp, error):
            self.error = str(error)
            self.connected = False

        def on_close(wsapp):
            self.connected = False

        self.ws.on_open = on_open
        self.ws.on_data = on_data
        self.ws.on_error = on_error
        self.ws.on_close = on_close

    def run(self):
        try:
            self.ws.connect()
        except Exception as exc:
            self.error = str(exc)
            self.connected = False

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        try:
            self.ws.close_connection()
        except Exception:
            pass

    def snapshot(self):
        with self.lock:
            return [dict(v) for v in self.rows.values()]


with st.sidebar:
    st.header("Connection & Scanner")
    api_key = st.text_input("Angel One API Key", type="password")
    client_code = st.text_input("Client Code")
    pin = st.text_input("Trading PIN", type="password")
    totp_secret = st.text_input("TOTP Secret (Base32)", type="password", help="Authenticator secret key; not your current 6-digit OTP.")
    exchange = st.selectbox("Exchange", ["NSE", "BSE"])
    symbols_text = st.text_area("Stocks (comma separated)", "RELIANCE,HDFCBANK,TCS,INFY,SBIN")
    threshold = st.number_input("Trigger when change is ABOVE (%)", min_value=0.01, max_value=20.0, value=0.30, step=0.05, format="%.2f")
    start = st.button("Connect & Start Scanner", type="primary", use_container_width=True)
    stop = st.button("Stop Scanner", use_container_width=True)
    st.caption("Credentials are used in this Streamlit session only. Never share your API key, PIN or TOTP secret.")

if start:
    if not all([api_key, client_code, pin, totp_secret, symbols_text.strip()]):
        st.error("Please fill in all credentials and at least one stock symbol.")
    else:
        try:
            with st.spinner("Logging in to Angel One and resolving symbols..."):
                master = load_master()
                symbols = [s.strip() for s in symbols_text.split(",") if s.strip()]
                instruments, missing = resolve_symbols(symbols, exchange, master)
                if not instruments:
                    raise ValueError("No valid EQ symbols found. Use symbols such as RELIANCE, INFY, SBIN.")
                smart = SmartConnect(api_key=api_key)
                session = smart.generateSession(client_code, pin, pyotp.TOTP(totp_secret).now())
                if not session or not session.get("status"):
                    raise RuntimeError(str(session))
                auth_token = session["data"]["jwtToken"]
                feed_token = session["data"]["feedToken"]
                scanner = LiveScanner(auth_token, api_key, client_code, feed_token, instruments, threshold)
                scanner.start()
                st.session_state.scanner = scanner
                st.session_state.missing_symbols = missing
                st.session_state.active_symbols = list(instruments)
                st.success("Login successful. WebSocket connection is starting.")
        except Exception as exc:
            st.error(f"Could not start scanner: {exc}")

if stop:
    scanner = st.session_state.get("scanner")
    if scanner:
        scanner.stop()
    st.session_state.scanner = None
    st.info("Scanner stopped.")

scanner = st.session_state.get("scanner")

if scanner:
    if st.session_state.get("missing_symbols"):
        st.warning("Not found in selected exchange: " + ", ".join(st.session_state.missing_symbols))

    @st.fragment(run_every="1s")
    def live_panel():
        current = st.session_state.get("scanner")
        if not current:
            st.info("Scanner is stopped.")
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("WebSocket", "Connected" if current.connected else "Connecting / Disconnected")
        c2.metric("Watching", len(current.instruments))
        c3.metric("Threshold", f"> {current.threshold:.2f}%")
        if current.error:
            st.error(current.error)
        data = pd.DataFrame(current.snapshot())
        if data.empty:
            st.info("Waiting for live ticks...")
            return
        hits = data[data["triggered"] == True].copy()
        st.subheader(f"🚀 Triggered Stocks ({len(hits)})")
        if not hits.empty:
            hits = hits.sort_values("change_pct", ascending=False)
            show = hits[["symbol", "minute_open", "ltp", "change_pct", "updated"]].rename(columns={
                "symbol":"Stock", "minute_open":"1-min Open", "ltp":"LTP", "change_pct":"Change %", "updated":"Tick Time"})
            st.dataframe(show.style.format({"1-min Open":"{:.2f}", "LTP":"{:.2f}", "Change %":"+{:.2f}%"}), use_container_width=True, hide_index=True)
        else:
            st.info("No stock has crossed the threshold yet.")
        st.subheader("All Watched Stocks")
        all_show = data[["symbol", "minute_open", "ltp", "change_pct", "minute", "updated"]].rename(columns={
            "symbol":"Stock", "minute_open":"1-min Open", "ltp":"LTP", "change_pct":"Change %", "minute":"Candle Minute", "updated":"Tick Time"})
        st.dataframe(all_show.style.format({"1-min Open":"{:.2f}", "LTP":"{:.2f}", "Change %":"{:+.2f}%"}), use_container_width=True, hide_index=True)
        st.caption("The 1-minute open is the first live tick received in that minute. On startup, the first partial minute is approximate; the next minute boundary starts a fresh candle. Scanner is informational only and does not place orders.")
    live_panel()
else:
    st.info("Enter your Angel One API credentials, stocks and percentage threshold in the sidebar, then click **Connect & Start Scanner**.")
    st.markdown("**Example:** Stocks `RELIANCE,HDFCBANK,TCS` and threshold `0.30%` → any stock whose live LTP is at least 0.30% above its current 1-minute open appears in Triggered Stocks.")

st.divider()
st.caption("Built with Angel One SmartAPI WebSocket V2. Market data availability, API permissions and subscription limits are controlled by Angel One.")
