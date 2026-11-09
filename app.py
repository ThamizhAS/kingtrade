
import re
import threading
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pyotp
import requests
import streamlit as st
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

IST = ZoneInfo("Asia/Kolkata")
MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"

st.set_page_config(
    page_title="Angel One 1-Min Scanner",
    page_icon="📈",
    layout="wide"
)

st.title("📈 Angel One — 1 Minute Stock Scanner")
st.caption("Live LTP • 1-minute candle open • Percentage threshold scanner")


@st.cache_data(ttl=3600, show_spinner=False)
def load_master():
    response = requests.get(MASTER_URL, timeout=30)
    response.raise_for_status()
    return response.json()


def normalize_totp_secret(raw_secret):
    """Normalize Base32 key or otpauth URI."""

    secret = (raw_secret or "").strip()

    if not secret:
        raise ValueError("TOTP Secret is empty.")

    if secret.lower().startswith("otpauth://"):
        query = parse_qs(urlparse(secret).query)
        secret = query.get("secret", [""])[0]

    secret = re.sub(r"[\s-]", "", secret).upper()
    secret = secret.rstrip("=")

    if not secret:
        raise ValueError("TOTP Secret is empty.")

    if not re.fullmatch(r"[A-Z2-7]+", secret):
        raise ValueError(
            "Invalid Base32 TOTP Secret. "
            "Enter Secret Key, not the 6-digit OTP."
        )

    try:
        pyotp.TOTP(secret).now()
    except Exception as exc:
        raise ValueError(
            "Invalid TOTP Base32 Secret. Check your key."
        ) from exc

    return secret


def resolve_symbols(symbols, exchange, master):

    wanted = {
        s.strip().upper().removesuffix("-EQ")
        for s in symbols if s.strip()
    }

    found = {}

    for row in master:

        if row.get("exch_seg") != exchange:
            continue

        trading_symbol = str(
            row.get("symbol", "")
        ).upper()

        if trading_symbol.endswith("-EQ"):

            name = trading_symbol[:-3]

            if name in wanted:

                found[name] = {
                    "symbol": trading_symbol,
                    "token": str(row["token"]),
                    "exchange": exchange,
                    "exchange_type": (
                        1 if exchange == "NSE" else 3
                    )
                }

    missing = sorted(wanted - set(found))

    return found, missing


class LiveScanner:

    def __init__(
        self,
        auth_token,
        api_key,
        client_code,
        feed_token,
        instruments,
        threshold
    ):

        self.instruments = instruments
        self.threshold = float(threshold)

        self.lock = threading.Lock()

        self.rows = {
            name: {
                "symbol": item["symbol"],
                "ltp": None,
                "minute_open": None,
                "change_pct": None,
                "minute": None,
                "updated": None,
                "triggered": False
            }
            for name, item in instruments.items()
        }

        self.error = None
        self.connected = False

        self.ws = SmartWebSocketV2(
            auth_token,
            api_key,
            client_code,
            feed_token,
            max_retry_attempt=5,
            retry_strategy=1
        )

        token_list = [{
            "exchangeType": next(
                iter(instruments.values())
            )["exchange_type"],
            "tokens": [
                item["token"]
                for item in instruments.values()
            ]
        }]

        token_to_name = {
            item["token"]: name
            for name, item in instruments.items()
        }

        def on_open(wsapp):

            self.connected = True

            try:
                self.ws.subscribe(
                    "scan1min001",
                    self.ws.LTP_MODE,
                    token_list
                )
            except Exception as exc:
                self.error = f"Subscribe error: {exc}"

        def on_data(wsapp, tick):

            try:

                token = str(tick.get("token", ""))

                name = token_to_name.get(token)

                if name is None:
                    return

                # Angel One LTP is in paise
                ltp = float(
                    tick["last_traded_price"]
                ) / 100.0

                raw_ts = (
                    tick.get("exchange_timestamp")
                    or int(time.time() * 1000)
                )

                tick_time = datetime.fromtimestamp(
                    float(raw_ts) / 1000.0,
                    IST
                )

                minute_key = tick_time.strftime(
                    "%Y-%m-%d %H:%M"
                )

                with self.lock:

                    row = self.rows[name]

                    # New 1-minute candle
                    if row["minute"] != minute_key:

                        row["minute"] = minute_key

                        # First tick becomes minute open
                        row["minute_open"] = ltp

                    row["ltp"] = ltp

                    op = row["minute_open"]

                    if op:

                        change = (
                            (ltp - op) / op
                        ) * 100.0

                    else:
                        change = None

                    row["change_pct"] = change

                    row["updated"] = (
                        tick_time.strftime("%H:%M:%S")
                    )

                    row["triggered"] = (
                        change is not None
                        and change >= self.threshold
                    )

            except Exception as exc:

                self.error = (
                    f"Tick processing error: {exc}"
                )

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

        self.thread = threading.Thread(
            target=self.run,
            daemon=True
        )

        self.thread.start()

    def stop(self):

        try:
            self.ws.close_connection()
        except Exception:
            pass

        self.connected = False

    def snapshot(self):

        with self.lock:
            return [
                dict(row)
                for row in self.rows.values()
            ]


# SIDEBAR

with st.sidebar:

    st.header("Connection & Scanner")

    api_key = st.text_input(
        "Angel One API Key",
        type="password"
    )

    client_code = st.text_input(
        "Client Code"
    )

    pin = st.text_input(
        "Trading PIN",
        type="password"
    )

    totp_secret_input = st.text_input(
        "TOTP Secret Key (Base32)",
        type="password",
        help="Enter Authenticator Secret Key, not 6-digit OTP."
    )

    exchange = st.selectbox(
        "Exchange",
        ["NSE", "BSE"]
    )

    symbols_text = st.text_area(
        "Stocks (comma separated)",
        "RELIANCE,HDFCBANK,TCS,INFY,SBIN"
    )

    threshold = st.number_input(
        "Show stocks when change is ABOVE (%)",
        min_value=0.01,
        max_value=20.0,
        value=0.30,
        step=0.05,
        format="%.2f"
    )

    start = st.button(
        "Connect & Start Scanner",
        type="primary",
        use_container_width=True
    )

    stop = st.button(
        "Stop Scanner",
        use_container_width=True
    )

    st.caption(
        "Never share API key, PIN or TOTP secret."
    )


# START SCANNER

if start:

    if not all([
        api_key.strip(),
        client_code.strip(),
        pin.strip(),
        totp_secret_input.strip(),
        symbols_text.strip()
    ]):

        st.error("Please fill all required fields.")

    else:

        try:

            normalized_secret = normalize_totp_secret(
                totp_secret_input
            )

            current_otp = pyotp.TOTP(
                normalized_secret
            ).now()

            with st.spinner(
                "Connecting to Angel One..."
            ):

                master = load_master()

                symbols = [
                    s.strip()
                    for s in symbols_text.split(",")
                    if s.strip()
                ]

                instruments, missing = resolve_symbols(
                    symbols,
                    exchange,
                    master
                )

                if not instruments:

                    raise ValueError(
                        "No valid EQ symbols found."
                    )

                smart = SmartConnect(
                    api_key=api_key.strip()
                )

                session_data = smart.generateSession(
                    client_code.strip(),
                    pin.strip(),
                    current_otp
                )

                if (
                    not session_data
                    or not session_data.get("status")
                ):

                    raise RuntimeError(
                        f"Angel One login failed: {session_data}"
                    )

                auth_token = (
                    session_data["data"]["jwtToken"]
                )

                feed_token = (
                    session_data["data"]["feedToken"]
                )

                old_scanner = st.session_state.get(
                    "scanner"
                )

                if old_scanner:
                    old_scanner.stop()

                scanner = LiveScanner(
                    auth_token,
                    api_key.strip(),
                    client_code.strip(),
                    feed_token,
                    instruments,
                    threshold
                )

                scanner.start()

                st.session_state.scanner = scanner

                st.session_state.missing_symbols = missing

                st.success(
                    "Angel One login successful. WebSocket connecting..."
                )

        except Exception as exc:

            st.error(
                f"Could not start scanner: {exc}"
            )


# STOP SCANNER

if stop:

    current = st.session_state.get("scanner")

    if current:
        current.stop()

    st.session_state.scanner = None

    st.info("Scanner stopped.")


# LIVE DASHBOARD

scanner = st.session_state.get("scanner")

if scanner:

    missing = st.session_state.get(
        "missing_symbols", []
    )

    if missing:

        st.warning(
            "Symbols not found: " + ", ".join(missing)
        )

    @st.fragment(run_every="1s")
    def live_panel():

        current = st.session_state.get("scanner")

        if current is None:
            st.info("Scanner stopped.")
            return

        data = pd.DataFrame(
            current.snapshot()
        )

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "WebSocket",
            "Connected" if current.connected
            else "Connecting / Disconnected"
        )

        col2.metric(
            "Stocks Watching",
            len(current.instruments)
        )

        col3.metric(
            "Threshold",
            f">= {current.threshold:.2f}%"
        )

        if current.error:
            st.error(current.error)

        if (
            data.empty
            or data["ltp"].isna().all()
        ):

            st.info(
                "Waiting for live ticks..."
            )

            return

        # FILTER TRIGGERED STOCKS

        hits = data[
            data["triggered"] == True
        ].copy()

        st.subheader(
            f"🚀 Threshold Crossed Stocks ({len(hits)})"
        )

        if hits.empty:

            st.info(
                "No stock has crossed the threshold yet."
            )

        else:

            hits = hits.sort_values(
                "change_pct",
                ascending=False
            )

            hit_view = hits[
                [
                    "symbol",
                    "minute_open",
                    "ltp",
                    "change_pct",
                    "updated"
                ]
            ].rename(columns={
                "symbol": "Stock",
                "minute_open": "1-min Open",
                "ltp": "LTP",
                "change_pct": "Change %",
                "updated": "Last Tick"
            })

            st.dataframe(
                hit_view.style.format({
                    "1-min Open": "{:.2f}",
                    "LTP": "{:.2f}",
                    "Change %": "+{:.2f}%"
                }),
                use_container_width=True,
                hide_index=True
            )

        # ALL STOCKS

        with st.expander(
            "All Watched Stocks",
            expanded=True
        ):

            all_view = data[
                [
                    "symbol",
                    "minute_open",
                    "ltp",
                    "change_pct",
                    "minute",
                    "updated"
                ]
            ].rename(columns={
                "symbol": "Stock",
                "minute_open": "1-min Open",
                "ltp": "LTP",
                "change_pct": "Change %",
                "minute": "Candle Minute",
                "updated": "Last Tick"
            })

            st.dataframe(
                all_view.style.format({
                    "1-min Open": "{:.2f}",
                    "LTP": "{:.2f}",
                    "Change %": "{:+.2f}%"
                }),
                use_container_width=True,
                hide_index=True
            )

        st.caption(
            "First live tick in each minute is used as candle open. "
            "First partial candle may be approximate."
        )

    live_panel()

else:

    st.info(
        "Enter Angel One credentials, stocks and threshold "
        "in sidebar, then click Connect & Start Scanner."
    )

    st.markdown(
        "**Example:** RELIANCE,HDFCBANK,TCS and threshold 0.30%"
    )


st.divider()

st.caption(
    "Angel One SmartAPI WebSocket V2 | "
    "NSE/BSE EQ | No automatic order placement"
)
