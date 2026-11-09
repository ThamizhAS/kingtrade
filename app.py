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
MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/"
    "OpenAPIScripMaster.json"
)

st.set_page_config(
    page_title="Angel One 1-Min Scanner",
    page_icon="📈",
    layout="wide",
)
st.title("📈 Angel One — 1 Minute Stock Scanner")
st.caption("Live LTP • 1-minute candle open • Percentage threshold scanner")


@st.cache_data(ttl=3600, show_spinner=False)
def load_master():
    response = requests.get(MASTER_URL, timeout=30)
    response.raise_for_status()
    return response.json()


def normalize_totp_secret(raw_secret: str) -> str:
    """Accept a Base32 secret or an otpauth:// URI."""
    secret = (raw_secret or "").strip()
    if not secret:
        raise ValueError("TOTP Secret காலியாக உள்ளது.")

    if secret.lower().startswith("otpauth://"):
        query = parse_qs(urlparse(secret).query)
        secret = query.get("secret", [""])[0]

    secret = re.sub(r"[\s-]", "", secret).upper().rstrip("=")
    if not secret or not re.fullmatch(r"[A-Z2-7]+", secret):
        raise ValueError(
            "TOTP Secret சரியான Base32 format-ல் இல்லை. "
            "Authenticator setup-ல் கிடைத்த Secret Key கொடுக்கவும்; "
            "6-digit OTP கொடுக்க வேண்டாம்."
        )

    try:
        pyotp.TOTP(secret).now()
    except Exception as exc:
        raise ValueError("TOTP Secret decode செய்ய முடியவில்லை. Key-ஐ சரிபார்க்கவும்.") from exc
    return secret


def make_fresh_totp(secret: str) -> str:
    """Create the OTP immediately before login, avoiding expiry during setup work."""
    totp = pyotp.TOTP(secret)
    remaining = totp.interval - (int(time.time()) % totp.interval)
    # If the current code is about to expire, wait for the next code window.
    if remaining <= 4:
        time.sleep(remaining + 0.5)
    return totp.now()


def resolve_symbols(symbols, exchange, master):
    wanted = {
        s.strip().upper().removesuffix("-EQ")
        for s in symbols
        if s.strip()
    }
    found = {}
    for row in master:
        if row.get("exch_seg") != exchange:
            continue
        trading_symbol = str(row.get("symbol", "")).upper()
        if trading_symbol.endswith("-EQ") and trading_symbol[:-3] in wanted:
            found[trading_symbol[:-3]] = {
                "symbol": trading_symbol,
                "token": str(row["token"]),
                "exchange": exchange,
                "exchange_type": 1 if exchange == "NSE" else 3,
            }
    return found, sorted(wanted - set(found))


class LiveScanner:
    def __init__(self, auth_token, api_key, client_code, feed_token, instruments, threshold):
        self.instruments = instruments
        self.threshold = float(threshold)
