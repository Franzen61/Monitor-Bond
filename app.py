import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd
import numpy as np

# Configurazione API FRED
FRED_API_KEY = '938a76ed726e8351f43e1b0c36365784'
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(page_title="Monitor Strategia Bond", layout="wide")

@st.cache_data(ttl=3600)
def fetch_all_data():
    # --- DATI DA FRED ---
    ry = fred.get_series('DFII10').iloc[-1]        # 10Y Real Yield
    be = fred.get_series('T10YIE').iloc[-1]        # B.E. Inflation
    unemp = fred.get_series('UNRATE').iloc[-1]      # Unemployment Rate
    dgs10 = fred.get_series('DGS10').iloc[-1]      # 10Y Yield
    dgs2 = fred.get_series('DGS2').iloc[-1]        # 2Y Yield
    
    # Core PCE per Delta Inflation (B5-B7)
    pce_idx = fred.get_series('PCEPILFE')
    pce_yoy_now = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1) * 100
    pce_yoy_3m = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1) * 100
    delta_inf = pce_yoy_now - pce_yoy_3m
    
    # --- DATI DA YAHOO FINANCE ---
    def get_var_30d(ticker):
        hist = yf.Ticker(ticker).history(period="60d")
        return (hist['Close'].iloc[-1] / hist['Close'].iloc[-30]) - 1

    ief_mom = get_var_30d("IEF")
    spy_var = get_var_30d("SPY")
    tips_var = get_var_30d("TIP")

    return {
        "ry": ry, "be": be, "unemp": unemp, "dgs10": dgs10, "dgs2": dgs2,
        "delta_inf": delta_inf, "pce_now": pce_yoy_now,
        "ief_mom": ief_mom, "spy_var": spy_var, "tips_var": tips_var,
        "curve": dgs10 - dgs2
    }

try:
    d = fetch_all_data()

    # Input manuale MOVE (visto che INDEXNYSEGIS:MOVE non è su API standard)
    st.sidebar.header("⚙️ Parametri Live")
    move_val = st.sidebar.number_input("MOVE Index", value=70.01)

    # --- TAB 3: SCORE LOGIC ---
    # Inflation Score: =SE(B10 < -0,003; 1; SE(B10 > 0,003; -1; 0))
    s_inf = 1 if d['delta_inf'] < -0.003 else (-1 if d['delta_inf'] > 0.003 else 0)
    
    # MOVE Score: =SE(D10 > 90; -1; SE(D10 < 70; 1; 0))
    s_move = -1 if move_val > 90 else (1 if move_val < 70 else 0)
    
    # Curve Score: =SE(C10 < 0,1; 1; SE(C10 > 1; -1; 0))
    s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)
    
    # Real Yield Score: =SE(E10 > 1,8; 1; SE(E10 < 0,5; -1; 0))
    s_ry = 1 if d['ry'] > 1.8 else (-1 if d['ry'] < 0.5 else 0)
    
    # Score TIPS: =SE(F10 < -0,02; 1; SE(F10 > 0,02; -1; 0))
    s_tips = 1 if d['tips_var'] < -0.02 else (-1 if d['tips_var'] > 0.02 else 0)
    
    # Momentum Score: =SE(J10 < -0,015; -1; SE(J10 > 0,008; 1; 0))
    s_mom = -1 if d['ief_mom'] < -0.015 else (1 if d['ief_mom'] > 0.008 else 0)
    
    # Filtro Equity: =SE(K10 < -0,05; 1; 0)
    s_equity = 1 if d['spy_var'] < -0.05 else 0

    # TOTAL SCORE: SOMMA(B13; C13; D13; E13; G10; J11)
    total_score = s_inf + s_move + s_curve + s_ry + s_tips + s_mom + s_equity

    # --- MONITOR RATIOS (LOGICA IMMAGINE 3) ---
    # Duration confidence: (F13+6)/12 * (1 + E13*0.15)
    dur_conf = ((total_score + 6) / 12) * (1 + s_ry * 0.15)
    
    # Signal stability: ABS(F13)/6
    sig_stab = abs(total_score) / 6
    
    # Eff. Dur. Conf: B21 * (0.5 + B22*0.5)
    eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)

    # --- INTERFACCIA ---
    st.title("🛡️ Bond Monitor Strategico")
    
    col1, col2 = st.columns([2,1])
    with col1:
        # TARGET SE: formula F20
        if total_score >= 3: target = "15-20+ anni (Aggressivo - All-in)"
        elif total_score >= 1: target = "7-10 anni (Moderato - Core)"
        elif total_score <= -1: target = "1-3 anni (Difensivo - Cash)"
        else: target = "4-6 anni (Neutrale - Laddering)"
        
        st.subheader(f"🎯 {target}")
        st.write(f"**Driver principale:** {'REAL YIELD' if s_ry >= s_move else 'MOVE'}")
    
    with col2:
        # Calcolo esatto dello Stress Test come da tua formula: F13 - C13 + (-1)
        stress_val = total_score - s_move - 1
        st.metric("STRESS TEST (MOVE 130)", f"{stress_val:.0f}")
        st.markdown(f"**Resilienza:** {'⚠️ VULNERABILE' if stress_val <= 0 else '✅ RESILIENTE'}"))

    st.divider()

    # Sezione Ratios
    r1, r2, r3 = st.columns(3)
    r1.metric("Duration Confidence", f"{dur_conf*100:.1f}%")
    r2.metric("Signal Stability", f"{sig_stab*100:.1f}%")
    r3.metric("Eff. Dur. Conf.", f"{eff_dur_conf*100:.1f}%")

    # Sezione Filtri (Testi esatti dal tuo foglio)
    st.write("### Stato Filtri e Alert")
    f1, f2, f3 = st.columns(3)
    with f1:
        # Breakeven Alert
        if d['be'] < 1.5: st.warning(f"⚠️ DEFLATION RISK: {d['be']:.2f}%")
        elif d['be'] > 3: st.warning(f"⚠️ INFLATION RISK: {d['be']:.2f}%")
        else: st.success(f"✅ BREAKEVEN OK: {d['be']:.2f}%")
    with f2:
        # Unemployment
        if d['unemp'] > 4.5: st.error(f"🚨 UNEMPLOYMENT ALERT: {d['unemp']}%")
        elif d['unemp'] < 3.8: st.success(f"✅ UNEMPLOYMENT: {d['unemp']}%")
        else: st.info(f"➡️ UNEMPLOYMENT: {d['unemp']}% (Range normale)")
    with f3:
        # MOVE Condition
        if move_val > 110: st.error("🔴 Alta Volatilità (MOVE > 110)")
        elif move_val > 80: st.warning("🟡 Media Volatilità (80-110)")
        else: st.success("✅ Bassa Volatilità (MOVE < 80)")

except Exception as e:
    st.error(f"Errore: {e}")
