import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd

# Configurazione API
FRED_API_KEY = '938a76ed726e8351f43e1b0c36365784'
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(page_title="Bond Strategy Monitor", layout="wide")

st.title("📊 Bond Strategy Monitor")
st.caption("Dati in tempo reale via FRED e Yahoo Finance")

@st.cache_data(ttl=3600)
def fetch_data():
    # 1. REAL YIELD (10Y)
    ry = fred.get_series('DFII10').iloc[-1]
    
    # 2. INFLATION (Core PCE YoY e Delta 3m)
    pce_idx = fred.get_series('PCEPILFE')
    # Calcolo YoY attuale e di 3 mesi fa
    current_pce_yoy = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1) * 100
    pce_3m_ago = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1) * 100
    delta_inf = current_pce_yoy - pce_3m_ago
    
    # 3. CURVE (10Y - 2Y)
    curve = fred.get_series('DGS10').iloc[-1] - fred.get_series('DGS2').iloc[-1]
    
    # 4. MOMENTUM (IEF 30gg)
    ief = yf.Ticker("IEF").history(period="60d")
    ief_mom = (ief['Close'].iloc[-1] / ief['Close'].iloc[-30]) - 1
    
    # 5. SPY (Filtro Equity 30gg)
    spy = yf.Ticker("SPY").history(period="60d")
    spy_var = (spy['Close'].iloc[-1] / spy['Close'].iloc[-30]) - 1

    return {
        "pce_yoy": current_pce_yoy,
        "delta_inf": delta_inf,
        "real_yield": ry,
        "curve": curve,
        "ief_mom": ief_mom,
        "spy_var": spy_var
    }

d = fetch_data()

# MOVE INDEX - Input manuale (dato che non c'è API gratuita affidabile)
st.sidebar.header("Parametri Manuali")
move_val = st.sidebar.number_input("MOVE Index attuale", value=70.01)

# --- LOGICA SCORE ---
s_pce = 1 if d['delta_inf'] < 0 else 0
s_move = 1 if move_val < 70 else (-1 if move_val > 90 else 0)
s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)
s_yield = 1 if d['real_yield'] > 1.5 else 0
s_mom = 1 if d['ief_mom'] > 0.008 else (-1 if d['ief_mom'] < -0.015 else 0)
s_spy = 1 if d['spy_var'] < -0.05 else 0

total_score = s_pce + s_move + s_curve + s_yield + s_mom + s_spy

# --- DISPLAY ---
c1, c2, c3 = st.columns(3)
with c1:
    st.metric("TOTAL SCORE", round(total_score, 2))
    if total_score >= 2: st.success("TARGET: 15-20+ anni")
    elif total_score >= 1: st.info("TARGET: 7-10 anni")
    elif total_score <= -1: st.error("TARGET: 1-3 anni")
    else: st.warning("TARGET: 4-6 anni")

with c2:
    stress_score = total_score - s_move - 1
    st.metric("STRESS SCORE (MOVE 130)", round(stress_score, 2))
    st.write(f"Resilienza: {'✅ RESILIENTE' if stress_score >= 0 else '⚠️ VULNERABILE'}")

with c3:
    st.write("**Dettaglio Indicatori**")
    st.write(f"Delta Inflation: {d['delta_inf']:.2f}%")
    st.write(f"Real Yield: {d['real_yield']:.2f}%")
    st.write(f"Filtro Equity: {'⚠️ ALERT' if s_spy == 1 else '✅ OK'}")

st.divider()
st.dataframe(pd.DataFrame([d]))
