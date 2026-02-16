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
    # 1. Recupero dati fondamentali
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
    
    # --- LOGICA SCORE (Allineata al Foglio Excel) ---
    s_inf = 1 if d['delta_inf'] < -0.003 else (-1 if d['delta_inf'] > 0.003 else 0)
    s_move = 1 if d['move_avg'] < 70 else (-1 if d['move_avg'] > 90 else 0)
    s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)
    s_ry = 1 if d['ry'] > 1.8 else (-1 if d['ry'] < 0.5 else 0)
    s_mom = -1 if d['ief_mom'] < -0.015 else (1 if d['ief_mom'] > 0.008 else 0)
    s_tips = 1 if d['tips_var'] < -0.02 else (-1 if d['tips_var'] > 0.02 else 0)
    
    # Total Score calcolato solo sui 6 fattori core
    total_score = s_inf + s_move + s_curve + s_ry + s_mom + s_tips

    # --- HEADER E METRICHE ---
    st.title("🛡️ Bond Monitor Strategico")
    
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        if total_score >= 3: target = "15-20+ anni (Aggressivo)"
        elif total_score >= 1: target = "7-10 anni (Moderato)"
        elif total_score <= -1: target = "1-3 anni (Difensivo)"
        else: target = "4-6 anni (Neutrale)"
        st.subheader(f"🎯 {target}")
        st.caption(f"MOVE Index: {d['move_val']:.2f} | Media 3M: {d['move_avg']:.2f}")

    with c2:
        st.metric("TOTAL SCORE", f"{total_score}")

    with c3:
        # Formula: Total - Move_Score - 1
        stress_val = int(total_score) - int(s_move) - 1
        st.metric("STRESS TEST MOVE", f"{stress_val}")
        resilienza = "✅ ROBUSTO" if stress_val > 0 else "⚠️ VULNERABILE"
        st.write(f"Status: **{resilienza}**")

    # --- TABELLA DI VERIFICA (Sempre visibile per debug) ---
    st.divider()
    st.subheader("📊 Analisi Fattori Score")
    df_debug = pd.DataFrame({
        "Fattore": ["Inflazione", "MOVE (Avg)", "Curva", "Real Yield", "Momentum", "TIPS"],
        "Valore": [f"{d['delta_inf']:.4f}", f"{d['move_avg']:.2f}", f"{d['curve']:.2f}", f"{d['ry']:.2f}%", f"{d['ief_mom']:.2%}", f"{d['tips_var']:.2%}"],
        "Score": [s_inf, s_move, s_curve, s_ry, s_mom, s_tips]
    })
    st.table(df_debug)

    # --- INDICATORI DI CONFIDENCE ---
    st.divider()
    col_a, col_b, col_c = st.columns(3)
    dur_conf = ((total_score + 6) / 12) * (1 + s_ry * 0.15)
    sig_stab = abs(total_score) / 6
    eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)

    col_a.metric("Duration Confidence", f"{dur_conf:.1%}")
    col_b.metric("Signal Stability", f"{sig_stab:.1%}")
    col_c.metric("Eff. Dur. Conf.", f"{eff_dur_conf:.1%}")

except Exception as e:
    st.error(f"Errore: {e}")
