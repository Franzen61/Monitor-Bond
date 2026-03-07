import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# ============================================================================
# CONFIGURAZIONE
# ============================================================================

def get_fred_api_key():
    try:
        return st.secrets["fred"]["api_key"]
    except Exception:
        return "938a76ed726e8351f43e1b0c36365784"

FRED_API_KEY = get_fred_api_key()
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(page_title="Bond Monitor Strategico", layout="wide")

# ============================================================================
# CSS
# ============================================================================
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { border: 1px solid #31333F; padding: 10px; border-radius: 10px; }
    div[data-testid="stExpander"] { border: 1px solid #31333F; background-color: #161b22; margin-top: 20px; }
    </style>
    """, unsafe_allow_html=True)

# ============================================================================
# FUNZIONI SCORING ADATTIVE
# ============================================================================

def get_inflation_score(delta_inf, pce_current):
    """
    Soglie adattive basate sul livello assoluto di inflazione.
    
    Logica: Se inflazione è alta (>3.5%), anche accelerazioni piccole sono critiche.
    Se inflazione è bassa (<2%), serve accelerazione maggiore per preoccuparsi.
    """
    if pce_current > 0.035:  # >3.5% - Regime Alta Inflazione
        # Fed in lotta contro inflazione, zero tolleranza
        # Anche +0.05% in 3M è negativo
        return 1 if delta_inf < -0.002 else (-1 if delta_inf > 0.0005 else 0)
    
    elif pce_current > 0.025:  # 2.5-3.5% - Regime Moderata Inflazione
        # Fed vigile, cautela
        return 1 if delta_inf < -0.001 else (-1 if delta_inf > 0.001 else 0)
    
    elif pce_current > 0.015:  # 1.5-2.5% - Regime Inflazione Target
        # Fed neutrale, margine di manovra
        return 1 if delta_inf < -0.003 else (-1 if delta_inf > 0.003 else 0)
    
    else:  # <1.5% - Regime Deflazione Risk
        # Fed preoccupata per deflazione, inflazione benvenuta
        return 1 if delta_inf < -0.005 else (-1 if delta_inf > 0.005 else 0)


def get_real_yield_score(ry, pce_current):
    """
    Soglie ASSOLUTE, adattate al regime inflazionistico.
    
    Logica corretta: RY 1.82% è "alto" o "basso" rispetto al contesto inflazione.
    Non confrontiamo RY vs BE (sono componenti dello stesso yield).
    """
    if pce_current > 0.035:  # >3.5% - Alta Inflazione
        # Serve RY robusto per compensare rischio
        # Soglia alta: 2.0% | Soglia bassa: 0.8%
        return 1 if ry > 0.020 else (-1 if ry < 0.008 else 0)
    
    elif pce_current > 0.025:  # 2.5-3.5% - Moderata Inflazione
        # RY positivo richiesto, ma non estremo
        # Soglia alta: 1.5% | Soglia bassa: 0.3%
        return 1 if ry > 0.015 else (-1 if ry < 0.003 else 0)
    
    elif pce_current > 0.015:  # 1.5-2.5% - Inflazione Target
        # RY anche moderato va bene
        # Soglia alta: 1.0% | Soglia bassa: 0.0%
        return 1 if ry > 0.010 else (-1 if ry < 0.000 else 0)
    
    else:  # <1.5% - Deflazione Risk
        # RY negativo accettabile (Fed espansiva)
        # Soglia alta: 0.5% | Soglia bassa: -0.5%
        return 1 if ry > 0.005 else (-1 if ry < -0.005 else 0)


def get_curve_score(curve, curve_hist=None):
    """
    Adattivo: livello assoluto + trend relativo a storia recente.
    
    Se curve_hist disponibile, usa anche trend.
    Altrimenti, solo livello assoluto con soglie riviste.
    """
    # Livello assoluto (baseline)
    if curve < -0.3:
        level_score = 1   # Inversione forte = recessione risk, bond favoriti
    elif curve > 0.4:
        level_score = -1  # Troppo ripida = rischio inflazione/term premium
    else:
        level_score = 0
    
    # Se abbiamo storia, aggiungi component trend
    if curve_hist is not None and len(curve_hist) > 126:
        curve_6m_avg = curve_hist.tail(126).mean()
        trend = curve - curve_6m_avg
        
        if trend > 0.3:
            # Ripidimento veloce = mercato prezza rischio
            trend_score = -1
        elif trend < -0.3:
            # Appiattimento veloce = recessione risk
            trend_score = 1
        else:
            trend_score = 0
        
        # Combina (media)
        final = (level_score + trend_score) / 2
        return 1 if final > 0.3 else (-1 if final < -0.3 else 0)
    
    else:
        # Solo livello assoluto
        return level_score


def get_move_score(move_current, move_hist=None):
    """
    Adattivo: livello assoluto + percentile storico + spike detection.
    
    MOVE 67 è "basso" o "alto" dipende dal contesto recente.
    """
    # Livello assoluto (baseline)
    if move_current > 110:
        level_score = -1  # Stress estremo
    elif move_current < 60:
        level_score = 1   # Calma estrema
    else:
        level_score = 0
    
    # Se abbiamo storia, aggiungi percentile + spike
    if move_hist is not None and len(move_hist) > 90:
        move_6m = move_hist.tail(126) if len(move_hist) > 126 else move_hist
        
        # Percentile
        percentile = (move_6m < move_current).sum() / len(move_6m)
        
        if percentile > 0.8:
            perc_score = -1  # Top 20% = stress
        elif percentile < 0.2:
            perc_score = 1   # Bottom 20% = calma
        else:
            perc_score = 0
        
        # Spike detection
        move_avg = move_6m.mean()
        move_delta = move_current - move_avg
        
        if move_delta > 20:
            spike_score = -1  # Spike improvviso
        elif move_delta < -20:
            spike_score = 1   # Crollo vol
        else:
            spike_score = 0
        
        # Combina (media pesata)
        final = level_score * 0.4 + perc_score * 0.3 + spike_score * 0.3
        return 1 if final > 0.3 else (-1 if final < -0.3 else 0)
    
    else:
        # Solo livello assoluto
        return level_score


def get_tips_score(tips_var, move_current, spy_var):
    """
    Filtra noise: se stress generale (MOVE alto + SPY giù), ignora TIPS.
    Altrimenti usa soglie più larghe.
    """
    # Se panico equity + MOVE alto, TIPS non affidabile
    if move_current > 100 and spy_var < -0.05:
        # Stress generale, TIPS è noise
        return 0
    
    # Altrimenti, logica normale ma soglie più larghe
    if tips_var < -0.03:
        return 1   # Strong deflation bet
    elif tips_var > 0.03:
        return -1  # Strong inflation bet
    else:
        return 0


# ============================================================================
# FUNZIONE SCORING PRINCIPALE
# ============================================================================

def calculate_scores(data, history=None):
    """
    Calcola score con sistema ADATTIVO.
    
    Args:
        data: dict con dati correnti
        history: dict opzionale con serie storiche (per curve, move)
    """
    # Estrai PCE current per logica adattiva
    pce_current = data.get('pce_current', 0.025)  # Default 2.5% se non presente
    
    # SCORE ADATTIVI
    s_inf = get_inflation_score(data['delta_inf'], pce_current)
    s_ry = get_real_yield_score(data['ry'], pce_current)
    
    # Curve e MOVE con storia se disponibile
    curve_hist = history.get('curve_hist') if history else None
    move_hist = history.get('move_hist') if history else None
    
    s_curve = get_curve_score(data['curve'], curve_hist)
    s_move = get_move_score(data['move_avg'], move_hist)
    
    # TIPS filtrato
    s_tips = get_tips_score(data['tips_var'], data['move_avg'], data.get('spy_var', 0))
    
    # Momentum - invariato (già dinamico per natura)
    s_mom = -1 if data['ief_mom'] < -0.015 else (1 if data['ief_mom'] > 0.008 else 0)
    
    # Equity panic filter
    s_equity = 1 if data.get('spy_var', 0) < -0.05 else 0
    
    # Total Score (6 componenti, equity è filtro separato)
    total_score = s_inf + s_move + s_curve + s_ry + s_tips + s_mom
    
    # Ratios
    dur_conf = ((total_score + 6) / 12) * (1 + s_ry * 0.15)
    sig_stab = abs(total_score) / 6
    eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)
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
        's_inf': s_inf, 's_move': s_move, 's_curve': s_curve,
        's_ry': s_ry, 's_tips': s_tips, 's_mom': s_mom,
        's_equity': s_equity,
        'total_score': total_score,
        'dur_conf': dur_conf, 'sig_stab': sig_stab,
        'eff_dur_conf': eff_dur_conf, 'stress_val': stress_val,
        'target': target, 'regime': regime, 'regime_desc': regime_desc,
        'pce_current': pce_current  # Per debug
    }


# ============================================================================
# FETCH DATA
# ============================================================================

@st.cache_data(ttl=3600)
def fetch_live_data():
    """Fetch dati live da FRED e Yahoo Finance."""
    # FRED
    ry_series = fred.get_series('DFII10')
    be_series = fred.get_series('T10YIE')
    unemp = fred.get_series('UNRATE').iloc[-1]
    
    dgs10_series = fred.get_series('DGS10')
    dgs2_series = fred.get_series('DGS2')
    dgs10 = dgs10_series.iloc[-1]
    dgs2 = dgs2_series.iloc[-1]
    
    # Core PCE
    pce_idx = fred.get_series('PCEPILFE')
    pce_now = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1)
    pce_3m = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1)
    delta_inf = pce_now - pce_3m
    pce_current = pce_now  # Livello YoY
    
    # Curve history (ultimi 12M)
    curve_hist = dgs10_series.tail(252) - dgs2_series.tail(252)
    
    # MOVE
    move_data = yf.Ticker("^MOVE").history(period="400d")  # ~1.5 anni
    if move_data.empty or len(move_data) < 90:
        move_avg = 70.0
        move_hist_series = None
    else:
        move_avg = move_data["Close"].tail(90).mean()
        move_hist_series = move_data["Close"]
    
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
        "pce_current": pce_current,
        "curve": dgs10 - dgs2,
        "ief_mom": get_var("IEF"),
        "spy_var": get_var("SPY"),
        "tips_var": get_var("TIP"),
        "move_avg": move_avg
    }, {
        "curve_hist": curve_hist,
        "move_hist": move_hist_series
    }


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def fetch_backtest_data(backtest_date_str: str):
    """Scarica dati storici per backtest."""
    from datetime import timedelta
    
    target_date = pd.Timestamp(backtest_date_str)
    start_date = target_date - timedelta(days=400)
    
    # FRED
    ry_hist = fred.get_series('DFII10', observation_end=target_date)
    dgs10_h = fred.get_series('DGS10', observation_end=target_date)
    dgs2_h = fred.get_series('DGS2', observation_end=target_date)
    be_hist = fred.get_series('T10YIE', observation_end=target_date)
    unemp_h = fred.get_series('UNRATE', observation_end=target_date)
    pce = fred.get_series('PCEPILFE', observation_end=target_date)
    
    ry = ry_hist.iloc[-1]
    curve = dgs10_h.iloc[-1] - dgs2_h.iloc[-1]
    be = be_hist.iloc[-1]
    unemp = unemp_h.iloc[-1]
    
    pce_now = ((pce.iloc[-1] / pce.iloc[-13]) - 1)
    pce_3m = ((pce.iloc[-4] / pce.iloc[-16]) - 1)
    delta_inf = pce_now - pce_3m
    pce_current = pce_now
    
    # Curve history
    curve_hist = dgs10_h.tail(252) - dgs2_h.tail(252)
    
    # Yahoo Finance
    def get_hist_var(ticker, end_date, lookback=130, var_days=30):
        start = end_date - timedelta(days=lookback)
        h = yf.Ticker(ticker).history(
            start=start.strftime('%Y-%m-%d'),
            end=end_date.strftime('%Y-%m-%d')
        )
        if h.empty or len(h) < var_days:
            return 0
        return (h['Close'].iloc[-1] / h['Close'].iloc[-var_days]) - 1
    
    # MOVE
    move_raw = yf.Ticker("^MOVE").history(
        start=start_date.strftime('%Y-%m-%d'),
        end=target_date.strftime('%Y-%m-%d')
    )
    if move_raw.empty or len(move_raw) < 30:
        move_avg = 70.0
        move_warning = True
        move_hist_series = None
    else:
        move_avg = move_raw['Close'].tail(90).mean()
        move_warning = False
        move_hist_series = move_raw['Close']
    
    ief_mom = get_hist_var("IEF", target_date)
    tips_var = get_hist_var("TIP", target_date)
    spy_var = get_hist_var("SPY", target_date)
    
    return {
        "ry": ry, "curve": curve, "be": be, "unemp": unemp,
        "delta_inf": delta_inf, "pce_current": pce_current,
        "move_avg": move_avg,
        "ief_mom": ief_mom, "tips_var": tips_var, "spy_var": spy_var,
        "move_warning": move_warning
    }, {
        "curve_hist": curve_hist,
        "move_hist": move_hist_series
    }


# ============================================================================
# TABS
# ============================================================================
tab1, tab2 = st.tabs(["📊 Monitor Live", "🔬 Backtest Storico"])

# ============================================================================
# TAB 1: MONITOR LIVE
# ============================================================================
with tab1:
    st.title("🛡️ Bond Monitor Strategico")
    st.caption("🔧 **Sistema Adattivo Attivo** — Soglie dinamiche basate su regime inflazionistico")
    
    try:
        d, history = fetch_live_data()
        scores = calculate_scores(d, history)
        
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
        
        st.divider()
        st.subheader("🔍 Stato Filtri e Analisi")
        
        f1, f2, f3 = st.columns(3)
        
        with f1:
            behr = (
                "🟢 HEDGE ATTIVO"
                if (scores['s_inf'] >= 0 and scores['s_ry'] >= 0 and scores['eff_dur_conf'] >= 0.55)
                else "⚠️ HEDGE DEBOLE"
            )
            st.write(f"**Behr Status:** {behr}")
            st.write(f"**Dec.Bond Eq:** {'🟢 FAVOREVOLE' if d['ry'] > 0 and d['delta_inf'] <= 0 else '🟡 DEBOLE'}")
        
        with f2:
            st.write(f"**Breakeven:** {d['be']:.2f}% ({'✅ OK' if 1.5 < d['be'] < 3 else '⚠️ ALERT'})")
            st.write(f"**Unemployment:** {d['unemp']:.1f}% ({'✅ Normale' if d['unemp'] < 4.5 else '🚨 ALERT'})")
        
        with f3:
            st.write(f"**MOVE 3M Avg:** {d['move_avg']:.2f}")
            st.write(f"**Filtro Equity:** {'🚨 PANICO' if scores['s_equity'] == 1 else '✅ Stabile'}")
            st.write(f"**Convessità:** {'Adeguata' if d['ry'] > 1.8 else 'Ridotta'}")
        
        # DEBUG EXPANDER con soglie adattive
        with st.expander("🔧 Debug — Valori Raw, Score e Soglie Adattive"):
            # Regime inflazione
            pce_pct = scores['pce_current'] * 100
            if scores['pce_current'] > 0.035:
                regime_inf = f"🔴 Alta Inflazione ({pce_pct:.1f}%)"
            elif scores['pce_current'] > 0.025:
                regime_inf = f"🟡 Moderata Inflazione ({pce_pct:.1f}%)"
            elif scores['pce_current'] > 0.015:
                regime_inf = f"🟢 Inflazione Target ({pce_pct:.1f}%)"
            else:
                regime_inf = f"🔵 Rischio Deflazione ({pce_pct:.1f}%)"
            
            st.markdown(f"**Regime Inflazionistico:** {regime_inf}")
            st.caption("Le soglie degli score si adattano automaticamente al regime corrente.")
            
            st.markdown("---")
            
            debug_df = pd.DataFrame({
                'Variabile': [
                    'Delta Inflation (3m)',
                    'MOVE 3M Avg',
                    'Curve 10-2Y',
                    'Real Yield 10Y',
                    'IEF Momentum (30gg)',
                    'TIPS Var (30gg)',
                    'SPY Var (30gg)'
                ],
                'Valore Raw': [
                    f"{d['delta_inf']:.4%}",
                    f"{d['move_avg']:.2f}",
                    f"{d['curve']:.3f}%",
                    f"{d['ry']:.2f}%",
                    f"{d['ief_mom']:.4%}",
                    f"{d['tips_var']:.4%}",
                    f"{d['spy_var']:.4%}"
                ],
                'Score': [
                    scores['s_inf'],
                    scores['s_move'],
                    scores['s_curve'],
                    scores['s_ry'],
                    scores['s_mom'],
                    scores['s_tips'],
                    scores['s_equity']
                ],
                'Soglie (Adattive)': [
                    '⚙️ Dinamiche (regime inflazione)',
                    '⚙️ Dinamiche (percentile storico)',
                    '⚙️ Dinamiche (trend 6M)',
                    '⚙️ Dinamiche (regime inflazione)',
                    'Fisse: > +0.80% / < -1.50%',
                    '⚙️ Filtrate (se stress)',
                    'Filtro: < -5.0%'
                ]
            })
            
            def style_score(val):
                if val > 0:
                    return 'background-color: rgba(0,255,0,0.2); color: #00ff00; font-weight: bold;'
                elif val < 0:
                    return 'background-color: rgba(255,0,0,0.2); color: #ff6b6b; font-weight: bold;'
                return 'background-color: rgba(128,128,128,0.1); color: #888;'
            
            st.dataframe(
                debug_df.style.map(style_score, subset=['Score']),
                use_container_width=True,
                hide_index=True
            )
        
        st.divider()
        
        # Grafici
        g1, g2 = st.columns(2)
        
        with g1:
            fig_ry = go.Figure()
            fig_ry.add_trace(go.Scatter(
                x=d['ry_hist'].index, y=d['ry_hist'].values,
                name="Real Yield", line=dict(color='#00ff00')
            ))
            fig_ry.update_layout(
                title="Andamento Real Yield 10Y",
                template="plotly_dark", height=300,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig_ry, use_container_width=True)
        
        with g2:
            fig_be = go.Figure()
            fig_be.add_trace(go.Scatter(
                x=d['be_hist'].index, y=d['be_hist'].values,
                name="Breakeven", line=dict(color='#00bfff')
            ))
            fig_be.update_layout(
                title="Aspettative Inflazione (Breakeven)",
                template="plotly_dark", height=300,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig_be, use_container_width=True)
        
        st.divider()
        st.info(f"**{scores['regime']}:** {scores['regime_desc']}")
        
        with st.expander("📖 Sistema Adattivo — Come Funziona"):
            st.markdown("""
            ### 🎯 Novità: Soglie Dinamiche
            
            Il monitor ora usa **soglie adattive** che cambiano in base al regime macro corrente.
            
            #### 🔧 Componenti Adattivi:
            
            **1. Inflation Score**
            - Inflazione alta (>3.5%): anche +0.05% in 3M è negativo
            - Inflazione moderata (2.5-3.5%): soglia ±0.10%
            - Inflazione target (1.5-2.5%): soglia ±0.30%
            - Deflazione risk (<1.5%): soglia ±0.50%
            
            **2. Real Yield Score**
            - Inflazione alta: serve RY >2.0% per score +1
            - Inflazione moderata: serve RY >1.5%
            - Inflazione target: serve RY >1.0%
            - RY valutato in ASSOLUTO, non relativo a Breakeven
            
            **3. Curve Score**
            - Livello assoluto + trend 6 mesi
            - Curva >0.4% (ripida) = negativo
            - Ripidimento veloce (>0.3% vs 6M) = negativo
            
            **4. MOVE Score**
            - Livello assoluto + percentile storico + spike detection
            - MOVE 67 è "basso" o "alto" dipende dal contesto recente
            
            **5. TIPS Score**
            - Filtrato se stress generale (MOVE >100 + SPY <-5%)
            - Altrimenti soglie più larghe (±3%)
            
            #### ✅ Vantaggi:
            - Sistema si adatta automaticamente al regime macro
            - Non serve modificare manualmente le soglie
            - Cattura transizioni e cambi di regime
            """)
    
    except Exception as e:
        st.error(f"❌ Errore caricamento dati: {e}")
        st.info("Riprova tra qualche minuto o verifica la connessione.")

# ============================================================================
# TAB 2: BACKTEST
# ============================================================================
with tab2:
    st.title("🔬 Backtest Storico")
    st.markdown("Verifica come si sarebbe comportato il monitor (con soglie adattive) in una data specifica.")
    st.caption("Il sistema adattivo regola automaticamente le soglie in base al regime inflazionistico di quella data.")
    
    st.divider()
    
    col_date, col_btn = st.columns([2, 1])
    
    with col_date:
        backtest_date = st.date_input(
            "📅 Data di Analisi",
            value=datetime(2021, 12, 31),
            min_value=datetime(2010, 1, 1),
            max_value=datetime.now(),
            help="Inserisci la data su cui vuoi testare il monitor"
        )
    
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        run_backtest = st.button("🔍 Calcola Backtest", use_container_width=True)
    
    if run_backtest:
        with st.spinner(f"Caricamento dati storici per {backtest_date.strftime('%d/%m/%Y')}..."):
            try:
                date_key = backtest_date.strftime('%Y-%m-%d')
                bt_data, bt_history = fetch_backtest_data(date_key)
                scores_bt = calculate_scores(bt_data, bt_history)
                
                if bt_data.get("move_warning"):
                    st.warning("⚠️ MOVE storico non disponibile, usando 70 come stima")
                
                st.success(f"✅ Dati caricati per il {backtest_date.strftime('%d/%m/%Y')}")
                st.divider()
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.subheader("📊 Indicazione Monitor")
                    
                    score_color = (
                        "#00ff00" if scores_bt['total_score'] >= 3 else
                        "#ffa500" if scores_bt['total_score'] >= 1 else
                        "#808080" if scores_bt['total_score'] >= -1 else
                        "#ff0000"
                    )
                    
                    st.markdown(f"""
                    <div style="background:{score_color}22;border:2px solid {score_color};
                                padding:15px;border-radius:10px;text-align:center;margin-bottom:15px;">
                        <div style="font-size:13px;color:#888;">TOTAL SCORE</div>
                        <div style="font-size:40px;font-weight:bold;color:white;">
                            {scores_bt['total_score']:+d}
                        </div>
                        <div style="font-size:12px;color:#888;margin-top:4px;">/ 6</div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.metric("🎯 Target Duration", scores_bt['target'])
                    st.metric("⚡ Stress Test MOVE 130", f"{scores_bt['stress_val']:+d}")
                    st.metric("📊 Duration Confidence", f"{scores_bt['dur_conf']:.1%}")
                    st.metric("📈 Signal Stability", f"{scores_bt['sig_stab']:.1%}")
                    
                    st.markdown("---")
                    st.markdown(f"**Regime:** {scores_bt['regime']}")
                    st.caption(scores_bt['regime_desc'])
                
                with col2:
                    st.subheader("📋 Dati alla Data")
                    
                    # Regime inflazione
                    pce_pct_bt = scores_bt['pce_current'] * 100
                    if scores_bt['pce_current'] > 0.035:
                        regime_inf_bt = f"🔴 Alta Inflazione ({pce_pct_bt:.1f}%)"
                    elif scores_bt['pce_current'] > 0.025:
                        regime_inf_bt = f"🟡 Moderata ({pce_pct_bt:.1f}%)"
                    elif scores_bt['pce_current'] > 0.015:
                        regime_inf_bt = f"🟢 Target ({pce_pct_bt:.1f}%)"
                    else:
                        regime_inf_bt = f"🔵 Deflazione Risk ({pce_pct_bt:.1f}%)"
                    
                    st.info(f"**Regime:** {regime_inf_bt}")
                    
                    raw_df = pd.DataFrame({
                        'Indicatore': [
                            'Real Yield', 'Curva 10-2Y', 'Breakeven',
                            'MOVE 3M Avg', 'Delta Inflation',
                            'IEF Momentum', 'TIPS Var', 'Unemployment'
                        ],
                        'Valore': [
                            f"{bt_data['ry']:.2f}%",
                            f"{bt_data['curve']:.2f}%",
                            f"{bt_data['be']:.2f}%",
                            f"{bt_data['move_avg']:.1f}",
                            f"{bt_data['delta_inf']:.2%}",
                            f"{bt_data['ief_mom']:.2%}",
                            f"{bt_data['tips_var']:.2%}",
                            f"{bt_data['unemp']:.1f}%"
                        ]
                    })
                    st.dataframe(raw_df, use_container_width=True, hide_index=True)
                    
                    st.markdown("---")
                    breakdown_df = pd.DataFrame({
                        'Componente': ['Inflation', 'MOVE', 'Curve', 'Real Yield', 'TIPS', 'Momentum'],
                        'Score': [
                            scores_bt['s_inf'], scores_bt['s_move'],
                            scores_bt['s_curve'], scores_bt['s_ry'],
                            scores_bt['s_tips'], scores_bt['s_mom']
                        ]
                    })
                    
                    def style_score(val):
                        if val > 0:
                            return 'background-color: rgba(0,255,0,0.2); color: #00ff00; font-weight: bold;'
                        elif val < 0:
                            return 'background-color: rgba(255,0,0,0.2); color: #ff6b6b; font-weight: bold;'
                        return 'background-color: rgba(128,128,128,0.1); color: #888;'
                    
                    st.dataframe(
                        breakdown_df.style.map(style_score, subset=['Score']),
                        use_container_width=True,
                        hide_index=True
                    )
                
                # ETF riferimento
                st.divider()
                if scores_bt['total_score'] >= 3:
                    etf_ref, etf_desc, etf_color = "TLT", "Bond 20+ anni", "#00ff00"
                elif scores_bt['total_score'] >= 1:
                    etf_ref, etf_desc, etf_color = "IEF", "Bond 7-10 anni", "#ffa500"
                elif scores_bt['total_score'] <= -1:
                    etf_ref, etf_desc, etf_color = "SHY", "Bond 1-3 anni", "#ff6b6b"
                else:
                    etf_ref, etf_desc, etf_color = "IEF", "Bond 7-10 anni (Neutrale)", "#808080"
                
                st.markdown(f"""
                <div style="background:{etf_color}22;border:2px solid {etf_color};
                            padding:15px;border-radius:10px;">
                    <div style="font-size:16px;font-weight:bold;color:{etf_color};">
                        💡 ETF di Riferimento: {etf_ref} ({etf_desc})
                    </div>
                    <div style="font-size:13px;color:#aaa;margin-top:8px;">
                        Verifica su Yahoo Finance la performance di <b>{etf_ref}</b>
                        nei 3-6 mesi successivi al {backtest_date.strftime('%d/%m/%Y')} per validare il segnale.
                    </div>
                    <div style="font-size:12px;color:#888;margin-top:6px;">
                        🔗 yahoo.com/quote/{etf_ref}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            except Exception as e:
                st.error(f"❌ Errore nel caricamento dati storici: {e}")
                st.info("Prova una data diversa o riprova tra qualche minuto.")
    
    else:
        st.info("👆 Seleziona una data e clicca **Calcola Backtest** per iniziare.")

st.markdown("---")
st.caption(
    f"🛡️ Bond Monitor Strategico v5.0 ADAPTIVE | "
    f"Ultimo aggiornamento: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
)
st.caption("⚙️ Sistema con soglie dinamiche attivo | Non costituisce consulenza finanziaria")
