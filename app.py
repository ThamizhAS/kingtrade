                st.session_state.scanner = scanner
                st.session_state.missing_symbols = missing
                st.success("Angel One login successful. WebSocket connecting...")

        except Exception as exc:
            st.error(f"Could not start scanner: {exc}")


if stop:
    current = st.session_state.get("scanner")
    if current:
        current.stop()
    st.session_state.scanner = None
    st.info("Scanner stopped.")


scanner = st.session_state.get("scanner")
if scanner:
    missing = st.session_state.get("missing_symbols", [])
    if missing:
        st.warning("Selected exchange-ல் கிடைக்காத symbols: " + ", ".join(missing))

    @st.fragment(run_every="1s")
    def live_panel():
        current = st.session_state.get("scanner")
        if current is None:
            st.info("Scanner stopped.")
            return

        data = pd.DataFrame(current.snapshot())
        col1, col2, col3 = st.columns(3)
        col1.metric(
            "WebSocket",
            "Connected" if current.connected else "Connecting / Disconnected",
        )
        col2.metric("Stocks Watching", len(current.instruments))
        col3.metric("Threshold", f">= {current.threshold:.2f}%")

        if current.error:
            st.error(current.error)

        if data.empty or data["ltp"].isna().all():
            st.info("Live ticks வர காத்திருக்கிறது... Market open மற்றும் WebSocket connection சரிபார்க்கவும்.")
            return

        hits = data[data["triggered"] == True].copy()
        st.subheader(f"🚀 Threshold Crossed Stocks ({len(hits)})")
        if hits.empty:
            st.info("இதுவரை எந்த stock-மும் threshold-ஐ cross செய்யவில்லை.")
        else:
            hits = hits.sort_values("change_pct", ascending=False)
            hit_view = hits[
                ["symbol", "minute_open", "ltp", "change_pct", "updated"]
            ].rename(columns={
                "symbol": "Stock",
                "minute_open": "1-min Open",
                "ltp": "LTP",
                "change_pct": "Change %",
                "updated": "Last Tick",
            })
            st.dataframe(
                hit_view.style.format({
                    "1-min Open": "{:.2f}",
                    "LTP": "{:.2f}",
                    "Change %": "+{:.2f}%",
                }),
                use_container_width=True,
                hide_index=True,
            )

        with st.expander("All Watched Stocks", expanded=True):
            all_view = data[
                ["symbol", "minute_open", "ltp", "change_pct", "minute", "updated"]
            ].rename(columns={
                "symbol": "Stock",
                "minute_open": "1-min Open",
                "ltp": "LTP",
                "change_pct": "Change %",
                "minute": "Candle Minute",
                "updated": "Last Tick",
            })
            st.dataframe(
                all_view.style.format({
                    "1-min Open": "{:.2f}",
                    "LTP": "{:.2f}",
                    "Change %": "{:+.2f}%",
                }),
                use_container_width=True,
                hide_index=True,
            )

        st.caption(
            "1-minute Open என்பது அந்த minute-ல் கிடைக்கும் முதல் live tick. "
            "Scanner-ஐ minute நடுவில் start செய்தால் முதல் candle partial/approximate. "
            "இது தகவல் நோக்கத்திற்கான scanner மட்டுமே; orders place செய்யாது."
        )

    live_panel()
else:
    st.info(
        "Sidebar-ல் Angel One credentials, stocks, threshold கொடுத்து "
        "Connect & Start Scanner அழுத்தவும்."
    )
    st.markdown(
        "**Example:** `RELIANCE,HDFCBANK,TCS` மற்றும் threshold `0.30%` என்றால், "
        "தற்போதைய 1-minute open-ஐ விட LTP குறைந்தது 0.30% உயர்ந்த stocks மட்டும் காட்டப்படும்."
    )

st.divider()
st.caption("Angel One SmartAPI WebSocket V2 • NSE/BSE EQ • No automatic order placement")
