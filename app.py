"""
Live NSE F&O screener (Yahoo Finance data) - Streamlit web app.
Replicates the ACTIVE filters of the Chartink scan:
  1. Today's daily High >= max(High 1 day ago, High 2 days ago)
  2. Latest 5-min Volume > 2 x SMA(5-min volume, 20)
  3. ANY of: latest 5-min High > prev-day High | latest close > Daily VWAP | previous close > Daily VWAP
  4. Today's daily High > 200
"""
import io
import urllib.request
from datetime import datetime

import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="F&O Live Screener", layout="wide")

FALLBACK = """
RELIANCE TCS HDFCBANK ICICIBANK INFY SBIN BHARTIARTL ITC LT KOTAKBANK AXISBANK
HINDUNILVR BAJFINANCE MARUTI SUNPHARMA TATAMOTORS TATASTEEL NTPC POWERGRID
ONGC ADANIENT ADANIPORTS ASIANPAINT HCLTECH WIPRO ULTRACEMCO TITAN NESTLEIND
M&M BAJAJ-AUTO BAJAJFINSV JSWSTEEL COALINDIA HINDALCO GRASIM TECHM CIPLA
DRREDDY EICHERMOT HEROMOTOCO BPCL IOC TATACONSUM APOLLOHOSP DIVISLAB BRITANNIA
INDUSINDBK SBILIFE HDFCLIFE PNB BANKBARODA CANBK VEDL SAIL NMDC GAIL
TATAPOWER DLF JINDALSTEL LUPIN AUROPHARMA BIOCON ZOMATO HAL BEL IRCTC
PFC RECLTD IDFCFIRSTBANK FEDERALBNK AUBANK BANDHANBNK CHOLAFIN MUTHOOTFIN
TRENT PIDILITIND SIEMENS ABB HAVELLS VOLTAS GODREJCP DABUR MARICO COLPAL
PERSISTENT COFORGE MPHASIS LTIM OFSS TVSMOTOR ASHOKLEY BHARATFORG MOTHERSON
INDIGO UPL SRF PIIND DEEPAKNTR NAVINFLUOR TATACHEM CONCOR NATIONALUM
""".split()
DROP = {"SYMBOL", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}


@st.cache_data(ttl=86400)
def load_fo_symbols():
    try:
        url = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore")
        df = pd.read_csv(io.StringIO(raw))
        df.columns = [c.strip() for c in df.columns]
        syms = [str(s).strip() for s in df["SYMBOL"]]
        syms = [s for s in syms if s and s.upper() not in DROP]
        if len(syms) > 50:
            return syms
    except Exception:
        pass
    return FALLBACK


def get_frame(data, ticker):
    try:
        df = data[ticker].dropna(subset=["Close"])
        return df if len(df) else None
    except Exception:
        return None


def evaluate(sym, intra, daily, vol_mult, min_high):
    if intra is None or daily is None or len(intra) < 22:
        return None
    intra = intra.copy()
    if intra.index.tz is None:
        intra.index = intra.index.tz_localize("UTC")
    intra.index = intra.index.tz_convert("Asia/Kolkata")

    last_day = intra.index[-1].date()
    today = intra[intra.index.date == last_day]
    prev = daily[[d.date() < last_day for d in daily.index]]
    if len(today) < 2 or len(prev) < 2:
        return None
    d1, d2 = prev.iloc[-1], prev.iloc[-2]

    day_high = today["High"].max()
    tp = (today["High"] + today["Low"] + today["Close"]) / 3
    cumvol = today["Volume"].cumsum().replace(0, float("nan"))
    vwap_now = ((tp * today["Volume"]).cumsum() / cumvol).iloc[-1]
    vol_sma20 = intra["Volume"].rolling(20).mean().iloc[-1]
    last, before = intra.iloc[-1], intra.iloc[-2]

    c1 = day_high >= max(d1["High"], d2["High"])
    c2 = last["Volume"] > vol_mult * vol_sma20
    c3a = last["High"] > d1["High"]
    c3b = last["Close"] > vwap_now
    c3c = before["Close"] > vwap_now
    c4 = day_high > min_high

    if c1 and c2 and (c3a or c3b or c3c) and c4:
        chg = (last["Close"] / d1["Close"] - 1) * 100
        return {
            "Symbol": sym,
            "Bar time": intra.index[-1].strftime("%d-%b %H:%M"),
            "Close": round(last["Close"], 2),
            "Chg %": round(chg, 2),
            "Day High": round(day_high, 2),
            "Prev High": round(d1["High"], 2),
            "VWAP": round(vwap_now, 2),
            "Vol / SMA20": round(last["Volume"] / vol_sma20, 2),
            "> Prev High": c3a,
            "> VWAP": c3b,
        }
    return None


@st.cache_data(ttl=55, show_spinner=False)
def run_scan(vol_mult, min_high):
    symbols = load_fo_symbols()
    tickers = [s + ".NS" for s in symbols]
    kw = dict(group_by="ticker", threads=True, progress=False, auto_adjust=False)
    intra_all = yf.download(tickers, period="5d", interval="5m", **kw)
    daily_all = yf.download(tickers, period="15d", interval="1d", **kw)
    rows = []
    for s, t in zip(symbols, tickers):
        r = evaluate(s, get_frame(intra_all, t), get_frame(daily_all, t), vol_mult, min_high)
        if r:
            rows.append(r)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Vol / SMA20", ascending=False)
    return df, len(symbols), datetime.now().strftime("%H:%M:%S")


st.title("NSE F&O Live Screener")
with st.sidebar:
    st.header("Settings")
    refresh = st.selectbox("Auto-refresh", [60, 120, 300, 0], format_func=lambda s: "Off" if s == 0 else f"{s} sec")
    vol_mult = st.number_input("Volume multiple (x SMA20)", 1.0, 10.0, 2.0, 0.5)
    min_high = st.number_input("Min daily high", 0, 100000, 200)
    st.caption("Yahoo's NSE intraday data is ~15 min delayed. Outside market hours (9:15-15:30 IST) the scan runs on the last session's candles.")


@st.fragment(run_every=refresh if refresh else None)
def live_view():
    with st.spinner("Scanning F&O stocks..."):
        df, total, ts = run_scan(vol_mult, min_high)
    st.caption(f"Scanned {total} stocks · last run {ts}")
    if df.empty:
        st.info("No stocks match right now.")
    else:
        st.success(f"{len(df)} match(es)")
        st.dataframe(df, use_container_width=True, hide_index=True)
    if st.button("Scan now"):
        run_scan.clear()
        st.rerun()


live_view()
