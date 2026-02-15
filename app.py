import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd
import plotly.graph_objects as go

# Configurazione API FRED
FRED_API_KEY = '938a76ed726e8351f43e1b0c36365784'
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(page_title="Professional Bond Monitor", layout="wide")

# --- CSS Personalizzato ---
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { border: 1px solid #31333F; padding: 10px; border-radius: 10px; }
    div[data-testid="stExpander"] { border: 1px solid #31333F; background-color: #161b22; margin-top: 20px; }
    </style>
    """, unsafe_allow_html=True)

@st.cache_data(ttl=3600)
def fetch_data():
    # Dati da FRED
    ry_series = fred.get_series('DFII10')
    be_series = fred.get_series('T10YIE')
    unemp = fred.get_series('UNRATE').iloc[-1]
    dgs10 = fred.get_series('DGS10').iloc[-1]
    dgs2 = fred.get_series('DGS2').iloc[-1]
    
    # Core PCE Delta
    pce_idx = fred.get_series('PCEPILFE')
    pce_now = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1) * 100
    pce_3m = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1) * 100
    delta_inf = pce_now - pce_3m
    
    def get_var(t):
        h = yf.Ticker(t).history(period="60d")
        if h.empty: return 0
        return (h['Close'].iloc[-1] / h['Close'].iloc[-30]) - 1

    return {
        "ry": ry_series.iloc[-1], "ry_hist": ry_series.tail(180),
        "be": be_series.iloc[-1], "be_hist": be_series.tail(180),
        "unemp": unemp, "delta_inf": delta_inf, "curve": dgs10 - dgs2,
        "ief_mom": get_var("IEF"), "spy_var": get_var("SPY"), "tips_var": get_var("TIP")
    }

try:
    d = fetch_data()
    
    # --- SIDEBAR ---
    st.sidebar.header("⚙️ Parametri Live")
    move_val = st.sidebar.number_input("MOVE Index", value=70.01)

    # --- LOGICA SCORE ---
    s_inf = 1 if d['delta_inf'] < -0.003 else (-1 if d['delta_inf'] > 0.003 else 0)
    s_move = -1 if move_val > 90 else (1 if move_val < 70 else 0)
    s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)
    s_ry = 1 if d['ry'] > 1.8 else (-1 if d['ry'] < 0.5 else 0)
    s_tips = 1 if d['tips_var'] < -0.02 else (-1 if d['tips_var'] > 0.02 else 0)
    s_mom = -1 if d['ief_mom'] < -0.015 else (1 if d['ief_mom'] > 0.008 else 0)
    s_equity = 1 if d['spy_var'] < -0.05 else 0
    total_score = s_inf + s_move + s_curve + s_ry + s_tips + s_mom + s_equity

    # --- RATIOS ---
    dur_conf = ((total_score + 6) / 12) * (1 + s_ry * 0.15)
    sig_stab = abs(total_score) / 6
    eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)

    # --- HEADER ---
    st.title("🛡️ Bond Monitor Strategico")
    
    c1, c2 = st.columns([2,1])
    with c1:
        if total_score >= 3: target = "15-20+ anni (Aggressivo)"
        elif total_score >= 1: target = "7-10 anni (Moderato - Core)"
        elif total_score <= -1: target = "1-3 anni (Difensivo)"
        else: target = "4-6 anni (Neutrale)"
        st.subheader(f"🎯 Target: {target}")
        
        # --- DRIVER RENDIMENTO (Nuova Sezione) ---
        if s_inf < 0 and s_ry <= 0:
            driver_txt = "🔴 PREMIO INFLAZIONE (rischio duration)"
        elif s_inf >= 0 and s_ry > 0:
            driver_txt = "🟠 PREMIO TERM / DEBITO (duration penalizzata)"
        else:
            driver_txt = "🟢 REAL YIELD SANO (regime equilibrato)"
        
        st.markdown(f"**Driver Rendimento:** {driver_txt}")
        
        reg_move_txt = "⚠️ REGIME DIPENDENTE DAL MOVE" if s_move < 1 else "✅ REGIME ROBUSTO"
        st.caption(f"Status Volatilità: {reg_move_txt}")
    
    with c2:
        # Calcolo allineato al Foglio Google: (Total Score - Move Score) - 1
        stress_val = (total_score - s_move) - 1
        st.metric("STRESS TEST (MOVE 130)", f"{stress_val:.0f}")
        resilienza = "✅ RESILIENTE" if stress_val > 0 else "⚠️ VULNERABILE"
        st.markdown(f"**Status:** {resilienza}")

    # --- METRICHE PRINCIPALI ---
    st.divider()
    r1, r2, r3 = st.columns(3)
    r1.metric("Duration Confidence", f"{dur_conf:.1%}")
    
    if sig_stab < 0.3: stab_txt = "⚪ REGIME POCO DEFINITO"
    elif sig_stab > 0.7: stab_txt = "🟢 REGIME COERENTE"
    else: stab_txt = "🟡 REGIME MODERATAMENTE COERENTE"
    r2.metric("Signal Stability", f"{sig_stab:.1%}")
    st.caption(f"Trend: {stab_txt} ({sig_stab:.0%})")
    
    r3.metric("Eff. Dur. Conf.", f"{eff_dur_conf:.1%}")

    # --- FILTRI ---
    st.divider()
    st.subheader("🔍 Stato Filtri e Analisi")
    f1, f2, f3 = st.columns(3)
    with f1:
        behr = "🟢 HEDGE ATTIVO" if (s_inf >= 0 and s_ry >= 0 and eff_dur_conf >= 0.55) else "⚠️ HEDGE DEBOLE"
        st.write(f"**Behr Status:** {behr}")
        st.write(f"**Dec.Bond Eq:** {'🟢 FAVOREVOLE' if d['ry']>0 and d['delta_inf']<=0 else '🟡 DEBOLE'}")
    with f2:
        st.write(f"**Breakeven:** {d['be']:.2f}% ({'✅ OK' if 1.5<d['be']<3 else '⚠️ ALERT'})")
        st.write(f"**Unemployment:** {d['unemp']}% ({'✅ Normale' if d['unemp']<4.5 else '🚨 ALERT'})")
    with f3:
        st.write(f"**Filtro Equity:** {'🚨 PANICO' if s_equity == 1 else '✅ Stabile'}")
        st.write(f"**Convessità:** {'Adeguata' if d['ry'] > 1.8 else 'Ridotta'}")

    # --- GRAFICI ---
    st.divider()
    g1, g2 = st.columns(2)
    with g1:
        fig_ry = go.Figure()
        fig_ry.add_trace(go.Scatter(x=d['ry_hist'].index, y=d['ry_hist'].values, name="Real Yield", line=dict(color='#00ff00')))
        fig_ry.update_layout(title="Andamento Real Yield 10Y", template="plotly_dark", height=300, margin=dict(l=20,r=20,t=40,b=20))
        st.plotly_chart(fig_ry, width='stretch')
    with g2:
        fig_be = go.Figure()
        fig_be.add_trace(go.Scatter(x=d['be_hist'].index, y=d['be_hist'].values, name="Breakeven", line=dict(color='#00bfff')))
        fig_be.update_layout(title="Aspettative Inflazione (Breakeven)", template="plotly_dark", height=300, margin=dict(l=20,r=20,t=40,b=20))
        st.plotly_chart(fig_be, width='stretch')

    # --- GUIDA STRATEGICA ---
    st.divider()
    
    if dur_conf > 0.6 and sig_stab < 0.4:
        reg_txt = "🚀 FASE INIZIALE: Confidence Alta / Stabilità Bassa. Opportunità di accumulo graduale."
    elif dur_conf > 0.6 and sig_stab > 0.7:
        reg_txt = "📢 FASE MATURA: Tutto Positivo e Allineato. Movimento probabilmente già prezzato."
    elif dur_conf < 0.4:
        reg_txt = "🚨 REGIME NEGATIVO: Duration non adeguatamente compensata."
    else:
        reg_txt = "⚖️ REGIME DI DIVERGENZA: Segnale incerto o cambiamento di aspettative in corso."
    
    st.info(f"**Analisi di Regime Attuale:** {reg_txt}")

    with st.expander("📖 Manuale Operativo e Filosofia del Monitor"):
        st.markdown("""
        ### 🎯 Scopo del Monitor
        Fornire una lettura del regime macro per capire se la duration è strutturalmente favorita e se i bond decorrelano dall'equity.
        
        #### 🚦 Pilastri di Lettura
        - **Duration Confidence**: Remunerazione dei tassi reali. Se bassa, il rischio non è pagato.
        - **Signal Stability**: Coerenza dei dati. Le migliori opportunità nascono con Confidence alta e Stabilità moderata (fase di transizione).
        - **Hedge Status**: Capacità di protezione. Se debole, i bond non decorrelano (rischio 2022).
        
        #### 🧩 Configurazioni di Regime
        - **Iniziale**: Mercato diffidente, ottimo per accumulo graduale.
        - **Matura**: Consenso uniforme, movimento già incorporato nei prezzi.
        - **Negativa**: Dominata dall'inflazione, sfavorevole alla duration lunga.
        - **Divergenza**: Rischio macro non ancora risolto o cambio aspettative.
        """)

except Exception as e:
    st.error(f"Errore: {e}")
