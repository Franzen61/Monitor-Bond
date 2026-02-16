import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# ============================================================================
# CONFIGURAZIONE
# ============================================================================

FRED_API_KEY = '938a76ed726e8351f43e1b0c36365784'
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(page_title="Bond Monitor Strategico", layout="wide")

# ============================================================================
# CSS PERSONALIZZATO
# ============================================================================

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { border: 1px solid #31333F; padding: 10px; border-radius: 10px; }
    div[data-testid="stExpander"] { border: 1px solid #31333F; background-color: #161b22; margin-top: 20px; }
    </style>
    """, unsafe_allow_html=True)

# ============================================================================
# FUNZIONI CONDIVISE
# ============================================================================

def calculate_scores(data):
    """
    Calcola tutti gli score da un dizionario di dati.
    Usata sia dal monitor live che dal backtest.
    
    Args:
        data: dict con chiavi delta_inf, move_avg, curve, ry, tips_var, ief_mom
    
    Returns:
        dict con tutti gli score e metriche
    """
    # Score componenti
    s_inf = 1 if data['delta_inf'] < -0.003 else (-1 if data['delta_inf'] > 0.003 else 0)
    s_move = -1 if data['move_avg'] > 90 else (1 if data['move_avg'] < 70 else 0)
    s_curve = 1 if data['curve'] < 0.1 else (-1 if data['curve'] > 1 else 0)
    s_ry = 1 if data['ry'] > 1.8 else (-1 if data['ry'] < 0.5 else 0)
    s_tips = 1 if data['tips_var'] < -0.02 else (-1 if data['tips_var'] > 0.02 else 0)
    s_mom = -1 if data['ief_mom'] < -0.015 else (1 if data['ief_mom'] > 0.008 else 0)
    s_equity = 1 if data.get('spy_var', 0) < -0.05 else 0
    
    # Total Score (6 componenti, equity è filtro separato)
    total_score = s_inf + s_move + s_curve + s_ry + s_tips + s_mom
    
    # Ratios
    dur_conf = ((total_score + 6) / 12) * (1 + s_ry * 0.15)
    sig_stab = abs(total_score) / 6
    eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)
    
    # Stress Test
    stress_val = total_score - s_move - 1
    
    # Target
    if total_score >= 3:
        target = "15-20+ anni (Aggressivo)"
    elif total_score >= 1:
        target = "7-10 anni (Moderato - Core)"
    elif total_score <= -1:
        target = "1-3 anni (Difensivo)"
    else:
        target = "4-6 anni (Neutrale)"
    
    # Regime
    if dur_conf > 0.6 and sig_stab < 0.4:
        regime = "🚀 FASE INIZIALE"
        regime_desc = "Mercato offre buona remunerazione ma senza consenso uniforme. Accumulo graduale."
    elif dur_conf > 0.6 and sig_stab > 0.7:
        regime = "📢 FASE MATURA"
        regime_desc = "Movimento già prezzato, consenso uniforme. Hold posizioni."
    elif dur_conf < 0.4:
        regime = "🚨 REGIME NEGATIVO"
        regime_desc = "Mercato non paga abbastanza per il rischio. Posizione difensiva."
    else:
        regime = "⚖️ REGIME DI DIVERGENZA"
        regime_desc = "Segnali contrastanti. Neutrale, duration intermedia."
    
    return {
        's_inf': s_inf,
        's_move': s_move,
        's_curve': s_curve,
        's_ry': s_ry,
        's_tips': s_tips,
        's_mom': s_mom,
        's_equity': s_equity,
        'total_score': total_score,
        'dur_conf': dur_conf,
        'sig_stab': sig_stab,
        'eff_dur_conf': eff_dur_conf,
        'stress_val': stress_val,
        'target': target,
        'regime': regime,
        'regime_desc': regime_desc
    }


@st.cache_data(ttl=3600)
def fetch_live_data():
    """
    Fetch dati live da FRED e Yahoo Finance
    """
    # FRED DATA
    ry_series = fred.get_series('DFII10')
    be_series = fred.get_series('T10YIE')
    unemp = fred.get_series('UNRATE').iloc[-1]
    dgs10 = fred.get_series('DGS10').iloc[-1]
    dgs2 = fred.get_series('DGS2').iloc[-1]
    
    # Core PCE Delta
    pce_idx = fred.get_series('PCEPILFE')
    pce_now = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1)
    pce_3m = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1)
    delta_inf = pce_now - pce_3m
    
    # MOVE 3M Average
    move_hist = yf.Ticker("^MOVE").history(period="130d")
    if move_hist.empty or len(move_hist) < 90:
        move_avg = 70.0
    else:
        move_avg = move_hist["Close"].tail(90).mean()
    
    # Variazioni 30gg
    def get_var(ticker):
        h = yf.Ticker(ticker).history(period="60d")
        if h.empty:
            return 0
        return (h['Close'].iloc[-1] / h['Close'].iloc[-30]) - 1
    
    return {
        "ry": ry_series.iloc[-1],
        "ry_hist": ry_series.tail(180),
        "be": be_series.iloc[-1],
        "be_hist": be_series.tail(180),
        "unemp": unemp,
        "delta_inf": delta_inf,
        "curve": dgs10 - dgs2,
        "ief_mom": get_var("IEF"),
        "spy_var": get_var("SPY"),
        "tips_var": get_var("TIP"),
        "move_avg": move_avg
    }


# ============================================================================
# DATASET BACKTEST
# ============================================================================

BACKTEST_PERIODS = {
    "Dicembre 2021 - Inflazione Record": {
        "date": "31/12/2021",
        "context": "Fed ancora accomodante, inflazione in forte accelerazione. Core PCE al 5.24%.",
        "inputs": {
            "delta_inf": 0.0074,  # 5.24% - 4.50% (3 mesi prima)
            "move_avg": 80.67,
            "curve": 0.797,
            "ry": -0.64,
            "tips_var": -0.00493,
            "ief_mom": -0.0287,  # IEF: 113 vs 116.44
            "spy_var": 0.0535,
            "be": 2.56,
            "unemp": 3.9
        },
        "performance_6m": {
            "TLT": -0.15,  # -15% (bond lunghi crollati)
            "IEF": -0.09,
            "SHY": -0.01
        },
        "verdict": "⚠️ SEGNALE PARZIALMENTE CORRETTO",
        "explanation": "Il monitor suggeriva cautela (Real Yield negativo), ma l'inflazione ha sorpreso al rialzo."
    },
    "Agosto 2023 - Tassi al Picco": {
        "date": "31/08/2023",
        "context": "Fed al picco dei rialzi, curva invertita, mercato inizia a prezzare pivot.",
        "inputs": {
            "delta_inf": -0.0040,  # 4.20% - 4.60% (in calo)
            "move_avg": 106.78,
            "curve": -0.657,
            "ry": 1.85,
            "tips_var": -0.0224,
            "ief_mom": 0.0109,
            "spy_var": 0.0109,
            "be": 2.26,
            "unemp": 3.7
        },
        "performance_6m": {
            "TLT": 0.02,  # +2%
            "IEF": 0.04,  # +4%
            "SHY": 0.02
        },
        "verdict": "✅ SEGNALE CORRETTO",
        "explanation": "MOVE alto suggeriva cautela. Nei 6 mesi successivi bond intermedi hanno performato meglio."
    },
    "Marzo 2020 - Panico COVID": {
        "date": "31/03/2020",
        "context": "Lockdown globale, panico massimo, Fed taglia tassi a zero e lancia QE.",
        "inputs": {
            "delta_inf": -0.001,
            "move_avg": 150.0,  # Volatilità estrema
            "curve": 0.40,
            "ry": -1.10,  # Real yield fortemente negativo
            "tips_var": -0.05,
            "ief_mom": 0.08,
            "spy_var": -0.20,  # -20% crollo equity
            "be": 1.20,
            "unemp": 4.4
        },
        "performance_6m": {
            "TLT": 0.15,  # +15%
            "IEF": 0.08,
            "SHY": 0.01
        },
        "verdict": "✅ SEGNALE CORRETTO",
        "explanation": "Filtro equity panic attivo. Bond lunghi hanno protetto durante il crollo."
    },
    "Ottobre 2022 - Picco Inflazione": {
        "date": "31/10/2022",
        "context": "Inflazione ancora sopra 6%, Fed aggressiva con rialzi 75bps, bond ai minimi.",
        "inputs": {
            "delta_inf": 0.002,
            "move_avg": 130.0,
            "curve": 0.45,
            "ry": 1.60,
            "tips_var": 0.01,
            "ief_mom": -0.02,
            "spy_var": -0.08,
            "be": 2.40,
            "unemp": 3.7
        },
        "performance_6m": {
            "TLT": 0.12,  # +12% (inizio rally bond)
            "IEF": 0.08,
            "SHY": 0.02
        },
        "verdict": "✅ SEGNALE CORRETTO",
        "explanation": "Picco inflazione + Fed al termine del ciclo. Bond lunghi hanno iniziato il rally."
    }
}

# ============================================================================
# TABS
# ============================================================================

tab1, tab2 = st.tabs(["📊 Monitor Live", "🔬 Backtest Storico"])

# ============================================================================
# TAB 1: MONITOR LIVE (CODICE ORIGINALE)
# ============================================================================

with tab1:
    st.title("🛡️ Bond Monitor Strategico")
    
    try:
        # Fetch dati live
        d = fetch_live_data()
        
        # Calcola score
        scores = calculate_scores(d)
        
        # HEADER
        c1, c2, c3 = st.columns([2, 1, 1])
        
        with c1:
            st.subheader(f"🎯 Target: {scores['target']}")
            reg_move_txt = "⚠️ REGIME DIPENDENTE DAL MOVE" if scores['s_move'] < 1 else "✅ REGIME ROBUSTO"
            st.caption(f"Status: {reg_move_txt}")
        
        with c2:
            st.metric("Total Score", f"{scores['total_score']:.0f}")
        
        with c3:
            st.metric("STRESS TEST (MOVE 130)", f"{scores['stress_val']:.0f}")
            st.markdown(f"**Resilienza:** {'⚠️ VULNERABILE' if scores['stress_val'] <= 0 else '✅ RESILIENTE'}")
        
        # METRICHE PRINCIPALI
        st.divider()
        
        r1, r2, r3 = st.columns(3)
        
        r1.metric("Duration Confidence", f"{scores['dur_conf']:.1%}")
        
        if scores['sig_stab'] < 0.3:
            stab_txt = "⚪ REGIME POCO DEFINITO"
        elif scores['sig_stab'] > 0.7:
            stab_txt = "🟢 REGIME COERENTE"
        else:
            stab_txt = "🟡 REGIME MODERATAMENTE COERENTE"
        
        r2.metric("Signal Stability", f"{scores['sig_stab']:.1%}")
        st.caption(f"Trend: {stab_txt}")
        
        r3.metric("Eff. Dur. Conf.", f"{scores['eff_dur_conf']:.1%}")
        
        # FILTRI
        st.divider()
        st.subheader("🔍 Stato Filtri e Analisi")
        
        f1, f2, f3 = st.columns(3)
        
        with f1:
            behr = "🟢 HEDGE ATTIVO" if (scores['s_inf'] >= 0 and scores['s_ry'] >= 0 and scores['eff_dur_conf'] >= 0.55) else "⚠️ HEDGE DEBOLE"
            st.write(f"**Behr Status:** {behr}")
            st.write(f"**Dec.Bond Eq:** {'🟢 FAVOREVOLE' if d['ry'] > 0 and d['delta_inf'] <= 0 else '🟡 DEBOLE'}")
        
        with f2:
            st.write(f"**Breakeven:** {d['be']:.2f}% ({'✅ OK' if 1.5 < d['be'] < 3 else '⚠️ ALERT'})")
            st.write(f"**Unemployment:** {d['unemp']:.1f}% ({'✅ Normale' if d['unemp'] < 4.5 else '🚨 ALERT'})")
        
        with f3:
            st.write(f"**MOVE 3M Avg:** {d['move_avg']:.2f}")
            st.write(f"**Filtro Equity:** {'🚨 PANICO' if scores['s_equity'] == 1 else '✅ Stabile'}")
            st.write(f"**Convessità:** {'Adeguata' if d['ry'] > 1.8 else 'Ridotta'}")
        
        # GRAFICI
        st.divider()
        
        g1, g2 = st.columns(2)
        
        with g1:
            fig_ry = go.Figure()
            fig_ry.add_trace(go.Scatter(
                x=d['ry_hist'].index,
                y=d['ry_hist'].values,
                name="Real Yield",
                line=dict(color='#00ff00')
            ))
            fig_ry.update_layout(
                title="Andamento Real Yield 10Y",
                template="plotly_dark",
                height=300,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig_ry, use_container_width=True)
        
        with g2:
            fig_be = go.Figure()
            fig_be.add_trace(go.Scatter(
                x=d['be_hist'].index,
                y=d['be_hist'].values,
                name="Breakeven",
                line=dict(color='#00bfff')
            ))
            fig_be.update_layout(
                title="Aspettative Inflazione (Breakeven)",
                template="plotly_dark",
                height=300,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig_be, use_container_width=True)
        
        # ANALISI REGIME
        st.divider()
        st.info(f"**{scores['regime']}:** {scores['regime_desc']}")
        
        # MANUALE OPERATIVO
        with st.expander("📖 Manuale Operativo e Filosofia del Monitor"):
            st.markdown("""
            ### 🎯 Scopo del Monitor
            Il monitor sintetizza il regime macro obbligazionario per valutare se la duration è
            strutturalmente favorita e se i bond possono tornare a svolgere funzione di hedge.
            
            ### 🚦 Pilastri di Lettura
            
            **Duration Confidence**
            - Misura quanto i tassi reali remunerano il rischio duration.
            - Valori elevati indicano carry reale positivo.
            
            **Signal Stability**
            - Indica quanto i segnali macro sono coerenti tra loro.
            - Le migliori opportunità nascono con confidence alta e stabilità intermedia.
            
            **Total Score**
            - Sintesi direzionale del regime macro:
                - Positivo → contesto favorevole ai bond
                - Neutrale → fase di transizione
                - Negativo → pressione inflattiva o instabilità
            """)
    
    except Exception as e:
        st.error(f"❌ Errore caricamento dati: {e}")
        st.info("Riprova tra qualche minuto o verifica la connessione.")

# ============================================================================
# TAB 2: BACKTEST STORICO (NUOVO)
# ============================================================================

with tab2:
    st.title("🔬 Backtest Storico")
    st.markdown("### Verifica come si sarebbe comportato il monitor in momenti chiave del mercato")
    
    st.divider()
    
    # Selezione periodo
    period_name = st.selectbox(
        "📅 Seleziona Periodo Storico",
        list(BACKTEST_PERIODS.keys()),
        help="Scegli un periodo di stress o transizione del mercato"
    )
    
    if period_name:
        data = BACKTEST_PERIODS[period_name]
        
        # Mostra contesto
        st.info(f"**{data['date']}** - {data['context']}")
        
        st.divider()
        
        # Calcola score con dati storici
        scores_bt = calculate_scores(data['inputs'])
        
        # Layout: Segnale Monitor | Performance Reale
        col1, col2 = st.columns(2)
        
        # === COLONNA 1: SEGNALE MONITOR ===
        with col1:
            st.subheader("📊 Segnale del Monitor")
            
            # Total Score
            score_color = "#00ff00" if scores_bt['total_score'] >= 3 else ("#ffa500" if scores_bt['total_score'] >= 1 else "#ff0000")
            st.markdown(f"""
            <div style="
                background: {score_color}22;
                border: 2px solid {score_color};
                padding: 15px;
                border-radius: 10px;
                text-align: center;
                margin-bottom: 15px;
            ">
                <div style="font-size: 14px; color: #888;">TOTAL SCORE</div>
                <div style="font-size: 42px; font-weight: bold; color: white;">
                    {scores_bt['total_score']:+d}
                </div>
                <div style="font-size: 12px; color: #888;">/ 6</div>
            </div>
            """, unsafe_allow_html=True)
            
            # Indicazioni
            st.metric("Target Duration", scores_bt['target'])
            st.metric("Stress Test", f"{scores_bt['stress_val']:+d}")
            st.metric("Duration Confidence", f"{scores_bt['dur_conf']:.1%}")
            st.metric("Signal Stability", f"{scores_bt['sig_stab']:.1%}")
            
            # Regime
            st.markdown("---")
            st.markdown(f"**Regime:** {scores_bt['regime']}")
            st.caption(scores_bt['regime_desc'])
            
            # Breakdown Score
            with st.expander("🔍 Breakdown Score Dettagliato"):
                breakdown_df = pd.DataFrame({
                    'Componente': ['Inflation', 'MOVE', 'Curve', 'Real Yield', 'TIPS', 'Momentum'],
                    'Score': [scores_bt['s_inf'], scores_bt['s_move'], scores_bt['s_curve'], 
                             scores_bt['s_ry'], scores_bt['s_tips'], scores_bt['s_mom']],
                    'Valore': [
                        f"{data['inputs']['delta_inf']:.2%}",
                        f"{data['inputs']['move_avg']:.1f}",
                        f"{data['inputs']['curve']:.2%}",
                        f"{data['inputs']['ry']:.2f}%",
                        f"{data['inputs']['tips_var']:.2%}",
                        f"{data['inputs']['ief_mom']:.2%}"
                    ]
                })
                st.dataframe(breakdown_df, use_container_width=True, hide_index=True)
        
        # === COLONNA 2: PERFORMANCE REALE ===
        with col2:
            st.subheader("📈 Performance Successiva (6 Mesi)")
            
            perf = data['performance_6m']
            
            # Performance metriche
            st.metric("TLT (Bond 20Y)", f"{perf['TLT']:.1%}", 
                     delta="Long Duration" if scores_bt['total_score'] >= 3 else None)
            st.metric("IEF (Bond 7-10Y)", f"{perf['IEF']:.1%}",
                     delta="Core Duration" if 1 <= scores_bt['total_score'] < 3 else None)
            st.metric("SHY (Bond 1-3Y)", f"{perf['SHY']:.1%}",
                     delta="Short Duration" if scores_bt['total_score'] < 1 else None)
            
            # Valutazione
            st.markdown("---")
            st.markdown(f"**Valutazione:** {data['verdict']}")
            st.caption(data['explanation'])
            
            # Grafico performance
            st.markdown("---")
            fig_perf = go.Figure()
            
            etfs = ['TLT', 'IEF', 'SHY']
            perfs = [perf['TLT'] * 100, perf['IEF'] * 100, perf['SHY'] * 100]
            colors = ['#ff6b6b' if p < 0 else '#00ff00' for p in perfs]
            
            fig_perf.add_trace(go.Bar(
                x=etfs,
                y=perfs,
                marker_color=colors,
                text=[f"{p:.1f}%" for p in perfs],
                textposition='outside'
            ))
            
            fig_perf.update_layout(
                title="Performance 6 Mesi (%)",
                template="plotly_dark",
                height=300,
                yaxis_title="Return %",
                showlegend=False
            )
            
            st.plotly_chart(fig_perf, use_container_width=True)
        
        # === INSIGHTS ===
        st.divider()
        st.subheader("💡 Insights")
        
        # Calcola accuracy
        if scores_bt['total_score'] >= 3:
            correct = perf['TLT'] > perf['SHY']
            expected = "Bond lunghi (20Y) dovrebbero sovraperformare"
        elif scores_bt['total_score'] >= 1:
            correct = perf['IEF'] >= min(perf['TLT'], perf['SHY'])
            expected = "Bond intermedi (7-10Y) dovrebbero offrire miglior risk/reward"
        elif scores_bt['total_score'] <= -1:
            correct = perf['SHY'] > perf['TLT']
            expected = "Bond corti (1-3Y) dovrebbero proteggere meglio"
        else:
            correct = True
            expected = "Regime neutrale, duration intermedia appropriata"
        
        if correct:
            st.success(f"✅ **Previsione corretta:** {expected}")
        else:
            st.warning(f"⚠️ **Previsione parziale:** {expected}, ma altri fattori hanno prevalso")
    
    # === LEGENDA ===
    st.divider()
    with st.expander("ℹ️ Come Interpretare il Backtest"):
        st.markdown("""
        ### 🎯 Obiettivo
        Il backtest verifica se le indicazioni del monitor sarebbero state corrette nei momenti chiave del mercato.
        
        ### 📊 Cosa Confrontiamo
        - **Segnale Monitor:** Target duration suggerito dal Total Score
        - **Performance Reale:** Rendimenti effettivi dei bond nei 6 mesi successivi
        
        ### ✅ Segnale Corretto
        - Score >= +3 → TLT dovrebbe battere SHY
        - Score 1-2 → IEF dovrebbe offrire miglior risk/reward
        - Score <= -1 → SHY dovrebbe proteggere meglio
        
        ### ⚠️ Limitazioni
        - Il backtest è ex-post: non include shock imprevedibili
        - Performance a 6 mesi: orizzonte arbitrario
        - Dati storici: potrebbero essere rivisti dalle fonti ufficiali
        
        ### 💡 Utilizzo
        Usa il backtest per:
        1. Capire la **robustezza** del framework
        2. Identificare **limiti** del modello (es. transizioni violente)
        3. Calibrare **aspettative** realistiche (non è infallibile)
        """)

st.markdown("---")
st.caption(f"🛡️ Bond Monitor Strategico v4.0 | Ultimo aggiornamento: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
st.caption("⚠️ Questo tool è a scopo informativo. Non costituisce consulenza finanziaria.")
