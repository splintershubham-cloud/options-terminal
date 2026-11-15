import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import yfinance as yf
from nsepython import nse_optionchain_scrapper, nse_fiidii, index_history

IST = pytz.timezone("Asia/Kolkata")

INDEX_MAP = {
    "NIFTY":      {"exchange": "NSE", "nse_name": "NIFTY",      "hist_name": "NIFTY 50",            "yf": "^NSEI",     "step": 50},
    "BANKNIFTY":  {"exchange": "NSE", "nse_name": "BANKNIFTY",  "hist_name": "NIFTY BANK",          "yf": "^NSEBANK",  "step": 100},
    "MIDCPNIFTY": {"exchange": "NSE", "nse_name": "MIDCPNIFTY", "hist_name": "NIFTY MIDCAP SELECT", "yf": "^NSEMDCP50", "step": 25},
    "SENSEX":     {"exchange": "BSE", "nse_name": "SENSEX",     "hist_name": "SENSEX",              "yf": "^BSESN",    "step": 100},
}

LIVE_HISTORY = {k: [] for k in INDEX_MAP}

def fetch_live_option_chain(key, cfg):
    step = cfg["step"]
    if cfg["exchange"] == "NSE":
        try:
            payload = nse_optionchain_scrapper(cfg["nse_name"])
            records = payload.get("records", {})
            spot = float(records.get("underlyingValue", 0))
            expiry_list = records.get("expiryDates", [])
            nearest_expiry = expiry_list if expiry_list else ""
            data_rows = records.get("data", [])

            atm_strike = int(round(spot / step) * step)
            ce_ltp, pe_ltp = 0.0, 0.0
            total_ce_oi, total_pe_oi = 0, 0

            for row in data_rows:
                if row.get("expiryDate") != nearest_expiry:
                    continue
                strike = row.get("strikePrice", 0)
                ce = row.get("CE", {})
                pe = row.get("PE", {})
                total_ce_oi += ce.get("openInterest", 0)
                total_pe_oi += pe.get("openInterest", 0)
                if strike == atm_strike:
                    ce_ltp = ce.get("lastPrice", 0.0)
                    pe_ltp = pe.get("lastPrice", 0.0)

            pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 1.0
            if pcr >= 1.15:
                bias = "BULLISH (Put Writers Strong)"
            elif pcr <= 0.85:
                bias = "BEARISH (Call Writers Strong)"
            else:
                bias = "SIDEWAYS / NEUTRAL"

            return {
                "spot": round(spot, 2),
                "expiry": nearest_expiry,
                "atm": atm_strike,
                "ce_txt": f"{atm_strike} CE: Rs.{ce_ltp}",
                "pe_txt": f"{atm_strike} PE: Rs.{pe_ltp}",
                "pcr": pcr,
                "bias": bias,
            }
        except Exception:
            pass

    try:
        ticker = yf.Ticker(cfg["yf"])
        hist = ticker.history(period="2d", interval="5m")
        spot = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc)
        atm_strike = int(round(spot / step) * step)
        chg_pct = (spot - prev_close) / prev_close * 100
        bias = "BULLISH (Spot > Open)" if chg_pct > 0.15 else ("BEARISH (Spot < Open)" if chg_pct < -0.15 else "SIDEWAYS / NEUTRAL")
        return {
            "spot": round(spot, 2),
            "expiry": "BSE Weekly" if cfg["exchange"] == "BSE" else "NSE Feed",
            "atm": atm_strike,
            "ce_txt": f"{atm_strike} CE (ATM Call)",
            "pe_txt": f"{atm_strike} PE (ATM Put)",
            "pcr": "Live Spot",
            "bias": bias,
        }
    except Exception:
        return {
            "spot": 0.0,
            "expiry": "N/A",
            "atm": 0,
            "ce_txt": "Fetching Feed...",
            "pe_txt": "Fetching Feed...",
            "pcr": "N/A",
            "bias": "WAITING FOR FEED",
        }

def fetch_daily_levels_and_trend(cfg):
    try:
        if cfg["exchange"] == "NSE":
            end_dt = datetime.now(IST) + timedelta(days=1)
            start_dt = end_dt - timedelta(days=40)
            df = index_history(cfg["hist_name"], start_dt.strftime("%d-%b-%Y"), end_dt.strftime("%d-%b-%Y"))
            df["CLOSE"] = df["CLOSE"].astype(float)
            df["HIGH"] = df["HIGH"].astype(float)
            df["LOW"] = df["LOW"].astype(float)
            df = df.iloc[::-1].reset_index(drop=True)
            pdh = float(df["HIGH"].iloc[-2]) if len(df) >= 2 else float(df["HIGH"].iloc[-1])
            pdl = float(df["LOW"].iloc[-2]) if len(df) >= 2 else float(df["LOW"].iloc[-1])
            ema20 = df["CLOSE"].ewm(span=20, adjust=False).mean().iloc[-1]
            daily_trend = "BULLISH" if df["CLOSE"].iloc[-1] > ema20 else "BEARISH"
            return {"pdh": pdh, "pdl": pdl, "daily_trend": daily_trend}
    except Exception:
        pass

    try:
        df = yf.Ticker(cfg["yf"]).history(period="2mo", interval="1d")
        pdh = round(float(df["High"].iloc[-2]), 2)
        pdl = round(float(df["Low"].iloc[-2]), 2)
        ema20 = df["Close"].ewm(span=20, adjust=False).mean().iloc[-1]
        daily_trend = "BULLISH" if float(df["Close"].iloc[-1]) > ema20 else "BEARISH"
        return {"pdh": pdh, "pdl": pdl, "daily_trend": daily_trend}
    except Exception:
        return {"pdh": None, "pdl": None, "daily_trend": "N/A"}

def fetch_fii_dii_live():
    try:
        df = nse_fiidii()
        fii_val = df[df["category"].str.contains("FII", case=False, na=False)].iloc["netValue"]
        dii_val = df[df["category"].str.contains("DII", case=False, na=False)].iloc["netValue"]
        return f"FII: Rs.{fii_val} Cr | DII: Rs.{dii_val} Cr"
    except Exception:
        return "FII/DII: Updated by NSE after 6:30 PM IST"

def update_and_build_candles(index_key, live_spot):
    now = datetime.now(IST).replace(second=0, microsecond=0)
    history = LIVE_HISTORY[index_key]

    if live_spot > 0:
        if not history or history[-1]["time"] != now:
            history.append({"time": now, "open": live_spot, "high": live_spot, "low": live_spot, "close": live_spot})
        else:
            bar = history[-1]
            bar["high"] = max(bar["high"], live_spot)
            bar["low"] = min(bar["low"], live_spot)
            bar["close"] = live_spot

    if len(history) > 120:
        LIVE_HISTORY[index_key] = history[-120:]

    if len(history) < 2:
        base = live_spot if live_spot > 0 else 25000.0
        return pd.DataFrame([
            {"time": now - timedelta(minutes=5), "open": base, "high": base, "low": base, "close": base},
            {"time": now, "open": base, "high": base, "low": base, "close": base},
        ])

    df = pd.DataFrame(history)
    df["ema9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df["rsi"] = 100 - (100 / (1 + (gain / loss.replace(0, np.nan))))

    df["buy_ce"] = (df["ema9"] > df["ema21"]) & (df["close"] > df["open"]) & (df["rsi"] >= 55)
    df["buy_pe"] = (df["ema9"] < df["ema21"]) & (df["close"] < df["open"]) & (df["rsi"] <= 45)
    return df

app = dash.Dash(__name__)
server = app.server

app.layout = html.Div(
    style={"backgroundColor": "#0d1117", "color": "#e6edf3", "fontFamily": "Arial, sans-serif", "padding": "14px"},
    children=[
        html.Div(
            style={"display": "flex", "justifyContent": "space-between", "alignItems": "center", "marginBottom": "10px"},
            children=[
                html.H3("Live Indian Index Options Terminal (4 Indices Auto-Refresh)", style={"margin": 0, "color": "#58a6ff"}),
                dcc.Dropdown(
                    id="idx-drop",
                    options=[
                        {"label": "NIFTY 50", "value": "NIFTY"},
                        {"label": "BANKNIFTY", "value": "BANKNIFTY"},
                        {"label": "MIDCPNIFTY", "value": "MIDCPNIFTY"},
                        {"label": "SENSEX (BSE)", "value": "SENSEX"},
                    ],
                    value="NIFTY",
                    clearable=False,
                    style={"width": "220px", "color": "#000000", "fontWeight": "bold"}
                ),
            ]
        ),
        dcc.Graph(id="main-chart", style={"height": "72vh"}),
        html.Div(id="oc-cards", style={"marginTop": "10px"}),
        dcc.Interval(id="timer", interval=10000, n_intervals=0)
    ]
)

@app.callback(
    [Output("main-chart", "figure"), Output("oc-cards", "children")],
    [Input("timer", "n_intervals"), Input("idx-drop", "value")]
)
def refresh_all(_, selected_idx):
    oc_results = {}
    cards = []
    oc_lines = []

    for key, cfg in INDEX_MAP.items():
        oc = fetch_live_option_chain(key, cfg)
        oc_results[key] = oc
        oc_lines.append(f"<b>{key}</b> ({oc['spot']}): ATM {oc['atm']} | PCR {oc['pcr']} -> {oc['bias']}")
        cards.append(
            html.Div(
                style={"backgroundColor": "#161b22", "border": "1px solid #30363d", "borderRadius": "8px", "padding": "10px", "flex": "1", "margin": "0 5px"},
                children=[
                    html.Div(f"{key}: {oc['spot']}", style={"fontWeight": "bold", "fontSize": "14px", "color": "#58a6ff"}),
                    html.Div(f"Call: {oc['ce_txt']}", style={"color": "#3fb950", "fontSize": "13px", "marginTop": "4px"}),
                    html.Div(f"Put:  {oc['pe_txt']}", style={"color": "#f85149", "fontSize": "13px"}),
                    html.Div(f"PCR: {oc['pcr']} | {oc['bias']}", style={"fontSize": "11px", "marginTop": "5px", "color": "#d29922"}),
                ]
            )
        )

    active_oc = oc_results[selected_idx]
    df = update_and_build_candles(selected_idx, active_oc["spot"])
    levels = fetch_daily_levels_and_trend(INDEX_MAP[selected_idx])
    fii_dii = fetch_fii_dii_live()

    last_row = df.iloc[-1]
intraday_trend = "BULLISH" if last_row.get("close", 0) >= last_row.get("ema21", 0) else "BEARISH"
now_ist = datetime.now(IST).strftime("%d %b %Y | %H:%M:%S IST")
fig = go.Figure()
fig.add_trace(go.Candlestick(x=df["time"], open=df["open"], high=df["high"], low=df["low"], close=df["close"], name=selected_idx))
if "ema9" in df.columns:
fig.add_trace(go.Scatter(x=df["time"], y=df["ema9"], line=dict(color="#2962ff", width=1.4), name="9 EMA"))
fig.add_trace(go.Scatter(x=df["time"], y=df["ema21"], line=dict(color="#e91e63", width=1.4), name="21 EMA"))
ce_sig = df[df["buy_ce"]]
pe_sig = df[df["buy_pe"]]
fig.add_trace(go.Scatter(x=ce_sig["time"], y=ce_sig["low"] * 0.9995, mode="markers+text", text=["BUY CE"] * len(ce_sig), textposition="bottom center", marker=dict(symbol="triangle-up", size=12, color="#00c853"), textfont=dict(color="#00c853", size=10), name="BUY CE"))
fig.add_trace(go.Scatter(x=pe_sig["time"], y=pe_sig["high"] * 1.0005, mode="markers+text", text=["BUY PE"] * len(pe_sig), textposition="top center", marker=dict(symbol="triangle-down", size=12, color="#ff1744"), textfont=dict(color="#ff1744", size=10), name="BUY PE"))
if levels["pdh"]:
fig.add_hline(y=levels["pdh"], line_dash="dash", line_color="#ef5350", annotation_text=f"PDH: {levels['pdh']}")
if levels["pdl"]:
fig.add_hline(y=levels["pdl"], line_dash="dash", line_color="#26a69a", annotation_text=f"PDL: {levels['pdl']}")
dashboard_text = (
f"LIVE MARKET DASHBOARD ({now_ist})
"
f"--------------------------------------------------------
"
f"Active Chart ({selected_idx}): {active_oc['spot']}
"
f"Intraday Trend: {intraday_trend} | Daily Trend: {levels['daily_trend']}
"
f"Option Chain Signal: {active_oc['bias']}
"
f"--------------------------------------------------------
"
f"4-INDEX OPTION SCANNER (ATM & PCR):
"
+ "
".join(oc_lines) + "
"
f"--------------------------------------------------------
"
f"Institutional Flow: {fii_dii}"
)
fig.add_annotation(xref="paper", yref="paper", x=0.995, y=0.99, xanchor="right", yanchor="top", align="left", text=dashboard_text, showarrow=False, font=dict(family="Consolas, monospace", size=11, color="white"), bgcolor="rgba(22, 27, 34, 0.92)", bordercolor="#30363d", borderwidth=1.5, borderpad=8)
fig.update_layout(template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=30, r=30, t=30, b=30), legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0))
return fig, html.Div(style={"display": "flex", "justifyContent": "space-between"}, children=cards)
if name == "main":
app.run(debug=False, host="0.0.0.0", port=8050)
