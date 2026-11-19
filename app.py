import io
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Nifty 500 Scanner", page_icon="📈", layout="wide")
st.title("📈 Nifty 500 – 52 Week Low Recovery Scanner")

NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_nifty500():
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(NIFTY500_URL, headers=headers, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    return df[["Company Name", "Industry", "Symbol"]]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_prices(symbols):
    tickers = [s + ".NS" for s in symbols]
    frames = []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        data = yf.download(
            chunk, period="1y", interval="1d",
            group_by="ticker", auto_adjust=False,
            threads=True, progress=False,
        )
        frames.append((chunk, data))
    return frames


def analyse(frames, meta):
    rows = []
    info_map = meta.set_index("Symbol").to_dict("index")
    for chunk, data in frames:
        for t in chunk:
            try:
                d = data[t].dropna(subset=["Close", "Low"])
                if len(d) < 50:
                    continue
                low = d["Low"].min()
                low_date = d["Low"].idxmin()
                ltp = d["Close"].iloc[-1]
                high = d["High"].max()
                gain = (ltp - low) / low * 100
                avg_vol = d["Volume"].iloc[-21:-1].mean()
                today_vol = d["Volume"].iloc[-1]
                vol_ratio = today_vol / avg_vol if avg_vol else 0
                sym = t.replace(".NS", "")
                info = info_map[sym]
                rows.append({
                    "Symbol": sym,
                    "Company": info["Company Name"],
                    "Industry": info["Industry"],
                    "LTP": round(ltp, 2),
                    "52W Low": round(low, 2),
                    "Low Date": low_date.date(),
                    "52W High": round(high, 2),
                    "% Up from 52W Low": round(gain, 2),
                    "Avg Vol (20D)": int(avg_vol) if avg_vol == avg_vol else 0,
                    "Today Vol": int(today_vol),
                    "Vol Ratio": round(vol_ratio, 2),
                })
            except Exception:
                continue
    return pd.DataFrame(rows)


def send_telegram(text):
    try:
        token = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
    except Exception:
        return False, "Telegram secrets set nahi hain."
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text}, timeout=15,
    )
    return r.ok, r.text


try:
    meta = load_nifty500()
except Exception:
    st.warning("NSE se list nahi aayi. Nifty 500 CSV manually upload karo.")
    up = st.file_uploader("ind_nifty500list.csv", type="csv")
    if up is None:
        st.stop()
    meta = pd.read_csv(up)[["Company Name", "Industry", "Symbol"]]

with st.sidebar:
    st.header("Settings")
    threshold = st.number_input("Minimum % up from 52W low", 1.0, 500.0, 30.0, 1.0)
    max_gain = st.number_input("Maximum % up (0 = no limit)", 0.0, 1000.0, 0.0, 5.0)

    st.subheader("Sector filter")
    sectors = sorted(meta["Industry"].dropna().unique().tolist())
    sel_sectors = st.multiselect("Sector chuno (khali = sab)", sectors)

    st.subheader("Volume filter")
    min_avg_vol = st.number_input("Min avg volume (20D)", 0, 100000000, 0, 50000)
    min_vol_ratio = st.number_input("Min Today/Avg volume ratio", 0.0, 50.0, 0.0, 0.5)

    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

with st.spinner("Data fetch ho raha hai (pehli baar 1-2 min lag sakte hain)..."):
    frames = fetch_prices(tuple(meta["Symbol"].tolist()))
    df = analyse(frames, meta)

if df.empty:
    st.error("Data nahi mila. Thodi der baad Refresh karo.")
    st.stop()

res = df[df["% Up from 52W Low"] >= threshold]
if max_gain > 0:
    res = res[res["% Up from 52W Low"] <= max_gain]
if sel_sectors:
    res = res[res["Industry"].isin(sel_sectors)]
if min_avg_vol > 0:
    res = res[res["Avg Vol (20D)"] >= min_avg_vol]
if min_vol_ratio > 0:
    res = res[res["Vol Ratio"] >= min_vol_ratio]

# Rank 1 = jo threshold (30%) ke sabse nazdeek ho
res = res.sort_values("% Up from 52W Low", ascending=True).reset_index(drop=True)
res.insert(0, "Rank", res.index + 1)
res["Distance from Threshold"] = (res["% Up from 52W Low"] - threshold).round(2)

c1, c2 = st.columns(2)
c1.metric("Stocks scanned", len(df))
c2.metric(f"Stocks ≥ {threshold:.0f}% up", len(res))

st.dataframe(res, use_container_width=True, hide_index=True)

if not res.empty:
    st.subheader("Sector-wise count")
    st.bar_chart(res["Industry"].value_counts())

col_a, col_b = st.columns(2)
col_a.download_button(
    "⬇️ Download CSV", res.to_csv(index=False).encode(),
    "nifty500_scan.csv", "text/csv",
)

if col_b.button("📲 Top 10 Telegram par bhejo"):
    if res.empty:
        st.info("Bhejne ke liye koi stock nahi hai.")
    else:
        lines = [f"Nifty500 Scan (>= {threshold:.0f}% from 52W low)"]
        for _, r in res.head(10).iterrows():
            lines.append(f"{r['Rank']}. {r['Symbol']} | LTP {r['LTP']} | +{r['% Up from 52W Low']}%")
        ok, msg = send_telegram("\n".join(lines))
        st.success("Telegram par bhej diya ✅") if ok else st.error(msg)

st.caption("Data: Yahoo Finance (EOD, thoda delayed). Ye sirf screening tool hai, investment advice nahi.")
