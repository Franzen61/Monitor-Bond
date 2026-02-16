import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd
import plotly.graph_objects as go

# Configurazione API FRED
FRED_API_KEY = '938a76ed726e8351f43e1b0c36365784'
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(page_title="Professional Bond Monitor", layout="wide")

@st.cache_data(ttl=3600)
def fetch_data():
    # 1. Dati da FRED
    ry_series = fred.get_series('DFII10')
    be_series = fred.get_series('T10YIE')
    unemp = fred.get_series('UNRATE').iloc[-1]
    dgs10 = fred.get_series('DGS10').iloc[-1]
    dgs2 = fred.get_series('DGS2').iloc[-1]
    
    # 2. Core PCE Delta
    pce_idx = fred.get_series('PCEPILFE')
    pce_now = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1) * 100
    pce_3m = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1) * 100
    delta_inf = pce_now - pce_3m
    
    # 3. Yahoo Finance
    move_data = yf.Ticker("^MOVE").history(period="120d")
    move_curr = move_data['Close'].iloc[-1]
    move_3m_avg = move_data['Close'].tail(90).mean()

    def get_var(t):
        h = yf.Ticker(t).history(period="60d")
        return (h['Close'].iloc[-1] / h['Close'].iloc[-30]) - 1 if not h.empty else 0

    return {
        "ry": ry_series.iloc[-1], "ry_hist": ry_series.tail(180),
        "be": be_series.iloc[-1], "be_hist": be_series.tail(180),
        "unemp": unemp, "delta_inf": delta_inf, "curve": dgs10 - dgs2,
        "ief_mom": get_var("IEF"), "spy_var": get_var("SPY"), "tips_var": get_var("TIP"),
        "move_val": move_curr, "move_avg": move_3m_avg
    }

try:
    d = fetch_data()
    
    # --- LOGICA SCORE CORRETTA ---
    s_inf = 1 if d['delta_inf'] < -0.003 else (-1 if d['delta_inf'] > 0.003 else 0)
    
    # CORREZIONE: Lo score del MOVE deve basarsi sulla media 3M (d['move_avg'])
    s_move = 1 if d['move_avg'] < 70 else (-1 if d['move_avg'] > 90 else 0)
    
    s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)
    s_ry = 1 if d['ry'] > 1.8 else (-1 if d['ry'] < 0.5 else 0)
    s_tips = 1 if d['tips_var'] < -0.02 else (-1 if d['tips_var'] > 0.02 else 0)
    s_mom = -1 if d['ief_mom'] < -0.015 else (1 if d['ief_mom'] > 0.008 else 0)
    s_equity = 1 if d['spy_var'] < -0.05 else 0
    
    total_score = s_inf + s_move + s_curve + s_ry + s_mom + s_tips + s_equity

    # --- HEADER ---
    st.title("🛡️ Bond Monitor Strategico")
    
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        if total_score >= 3: target = "15-20+ anni (Aggressivo)"
        elif total_score >= 1: target = "7-10 anni (Moderato - Core)"
        elif total_score <= -1: target = "1-3 anni (Difensivo)"
        else: target = "4-6 anni (Neutrale)"
        st.subheader(f"🎯 Target: {target}")
        
        # Driver Rendimento
        if s_inf < 0 and s_ry <= 0:
            driver_txt = "🔴 PREMIO INFLAZIONE"
        elif s_inf >= 0 and s_ry > 0:
            driver_txt = "🟠 PREMIO TERM / DEBITO"
        else:
            driver_txt = "🟢 REAL YIELD SANO"
        st.markdown(f"**Driver:** {driver_txt}")
        st.caption(f"MOVE Index: {d['move_val']:.2f} | Media 3M: {d['move_avg']:.2f}")

    with c2:
        st.metric("TOTAL SCORE", f"{total_score}")
        st.caption(f"MOVE Score (on Avg): {s_move}")

    with c3:
        # Calcolo GS: Total - Move_Score - 1
        stress_val = int(total_score) - int(s_move) - 1
        st.metric("STRESS TEST MOVE", f"{stress_val}")
        resilienza = "✅ ROBUSTO" if stress_val > 0 else "⚠️ VULNERABILE"
        st.markdown(f"**Status:** {resilienza}")

    # --- TABELLA DETTAGLIO SCORE ---
    with st.expander("📊 Dettaglio Fattori Score"):
        scores = {
            "Fattore": ["Inflazione (PCE)", "Volatilità (MOVE)", "Curva (10-2)", "Real Yield", "Momentum (IEF)", "TIPS Var", "Equity (SPY)"],
            "Valore": [f"{d['delta_inf']:.1%}", f"{d['move_avg']:.1f}", f"{d['curve']:.2f}", f"{d['ry']:.2f}%", f"{d['ief_mom']:.1%}", f"{d['tips_var']:.1%}", f"{d['spy_var']:.1%}"],
            "Score": [s_inf, s_move, s_curve, s_ry, s_mom, s_tips, s_equity]
        }
        st.table(pd.DataFrame(scores))

    # ... (resto del codice per metriche e grafici) ...

except Exception as e:
    st.error(f"Errore: {e}")
