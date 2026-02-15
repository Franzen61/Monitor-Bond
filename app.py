import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd

# API Key FRED
FRED_API_KEY = '938a76ed726e8351f43e1b0c36365784'
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(page_title="Monitor Strategia Bond", layout="wide")

# --- CSS per stile professionale ---
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

st.title("📊 Monitor Strategia Obbligazionaria")
st.info("Analisi quantitativa basata su Volatilità, Inflazione e Rendimenti Reali.")

@st.cache_data(ttl=3600)
def fetch_data():
    # 1. REAL YIELD (10Y)
    ry = fred.get_series('DFII10').iloc[-1]
    
    # 2. INFLATION (Core PCE YoY e Delta 3m)
    pce_idx = fred.get_series('PCEPILFE')
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

try:
    d = fetch_data()

    # MOVE INDEX - Input manuale laterale
    st.sidebar.header("⚙️ Parametri di Mercato")
    move_val = st.sidebar.number_input("MOVE Index attuale", value=70.01, help="Inserisci il valore da GoogleFinance o Investing.com")

    # --- LOGICA SCORE ---
    s_pce = 1 if d['delta_inf'] < 0 else 0
    s_move = 1 if move_val < 70 else (-1 if move_val > 90 else 0)
    s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)
    s_yield = 1 if d['real_yield'] > 1.5 else 0
    s_mom = 1 if d['ief_mom'] > 0.008 else (-1 if d['ief_mom'] < -0.015 else 0)
    s_spy = 1 if d['spy_var'] < -0.05 else 0

    total_score = s_pce + s_move + s_curve + s_yield + s_mom + s_spy

    # --- SEZIONE 1: SINTESI DECISIONALE ---
    st.subheader("🎯 Cerchio Operativo")
    c1, c2, c3 = st.columns(3)
    
    with c1:
        st.metric("TOTAL SCORE", f"{total_score:.0f}")
        if total_score >= 2: st.success("✅ AGGRESSIVO: Target 15-20+ anni")
        elif total_score >= 1: st.info("ℹ️ MODERATO: Target 7-10 anni")
        elif total_score <= -1: st.error("🚨 DIFENSIVO: Target 1-3 anni")
        else: st.warning("⚖️ NEUTRALE: Target 4-6 anni")

    with c2:
        stress_score = total_score - s_move - 1
        st.metric("STRESS SCORE (MOVE 130)", f"{stress_score:.0f}")
        if stress_score >= 0:
            st.write("💎 **RESILIENTE**: La tesi regge allo shock.")
        else:
            st.write("⚠️ **VULNERABILE**: Attenzione alla volatilità!")

    with c3:
        st.write("**Stato Filtri Protezione**")
        st.write(f"Filtro Equity: {'🚨 PANICO' if s_spy == 1 else '✅ OK'}")
        st.write(f"Driver Principale: {'MOVE' if abs(s_move) >= abs(s_yield) else 'Convessità'}")

    st.divider()

    # --- SEZIONE 2: DETTAGLIO MONITOR ---
    st.subheader("🔍 Analisi dei Driver")
    col_a, col_b, col_c = st.columns(3)
    
    with col_a:
        st.write("--- **MACRO** ---")
        st.write(f"Core PCE YoY: **{d['pce_yoy']:.2f}%**")
        st.write(f"Delta Inflation (3m): **{d['delta_inf']:.2f}%**")
        st.write(f"Score Inflazione: **{s_pce}**")

    with col_b:
        st.write("--- **VALUTAZIONE** ---")
        st.write(f"Real Yield (10Y): **{d['real_yield']:.2f}%**")
        st.write(f"Pendenza Curva (10-2): **{d['curve']:.2f}**")
        st.write(f"Convessità: **{'Adeguata' if d['real_yield'] > 1.5 else 'Ridotta'}**")

    with col_c:
        st.write("--- **MARKET** ---")
        st.write(f"Momentum IEF (30g): **{d['ief_mom']*100:.2f}%**")
        st.write(f"MOVE Index: **{move_val}**")
        st.write(f"Score Volatilità: **{s_move}**")

except Exception as e:
    st.error(f"Errore nel recupero dati: {e}")
    st.write("Verifica la tua API Key di FRED o la connessione internet.")
