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
# FUNZIONI SCORING ADATTIVE (STRATEGICO)
# ============================================================================

def get_inflation_score(delta_inf, pce_current):
    """Soglie adattive basate sul livello assoluto di inflazione."""
    if pce_current > 0.035:
        return 1 if delta_inf < -0.002 else (-1 if delta_inf > 0.0005 else 0)
    elif pce_current > 0.025:
        return 1 if delta_inf < -0.001 else (-1 if delta_inf > 0.001 else 0)
    elif pce_current > 0.015:
        return 1 if delta_inf < -0.003 else (-1 if delta_inf > 0.003 else 0)
    else:
        return 1 if delta_inf < -0.005 else (-1 if delta_inf > 0.005 else 0)


def get_real_yield_score(ry, pce_current):
    """Soglie ASSOLUTE, adattate al regime inflazionistico."""
    if pce_current > 0.035:
        return 1 if ry > 0.020 else (-1 if ry < 0.008 else 0)
    elif pce_current > 0.025:
        return 1 if ry > 0.015 else (-1 if ry < 0.003 else 0)
    elif pce_current > 0.015:
        return 1 if ry > 0.010 else (-1 if ry < 0.000 else 0)
    else:
        return 1 if ry > 0.005 else (-1 if ry < -0.005 else 0)


def get_curve_score(curve, curve_hist=None):
    """Adattivo: livello assoluto + trend relativo a storia recente."""
    if curve < -0.3:
        level_score = 1
    elif curve > 0.4:
        level_score = -1
    else:
        level_score = 0
    
    if curve_hist is not None and len(curve_hist) > 126:
        curve_6m_avg = curve_hist.tail(126).mean()
        trend = curve - curve_6m_avg
        
        if trend > 0.3:
            trend_score = -1
        elif trend < -0.3:
            trend_score = 1
        else:
            trend_score = 0
        
        final = (level_score + trend_score) / 2
        return 1 if final > 0.3 else (-1 if final < -0.3 else 0)
    else:
        return level_score


def get_move_score(move_current, move_hist=None):
    """Adattivo: livello assoluto + percentile storico + spike detection."""
    if move_current > 110:
        level_score = -1
    elif move_current < 60:
        level_score = 1
    else:
        level_score = 0
    
    if move_hist is not None and len(move_hist) > 90:
        move_6m = move_hist.tail(126) if len(move_hist) > 126 else move_hist
        
        percentile = (move_6m < move_current).sum() / len(move_6m)
        
        if percentile > 0.8:
            perc_score = -1
        elif percentile < 0.2:
            perc_score = 1
        else:
            perc_score = 0
        
        move_avg = move_6m.mean()
        move_delta = move_current - move_avg
        
        if move_delta > 20:
            spike_score = -1
        elif move_delta < -20:
            spike_score = 1
        else:
            spike_score = 0
        
        final = level_score * 0.4 + perc_score * 0.3 + spike_score * 0.3
        return 1 if final > 0.3 else (-1 if final < -0.3 else 0)
    else:
        return level_score


def get_tips_score(tips_var, move_current, spy_var):
    """Filtra noise: se stress generale, ignora TIPS."""
    if move_current > 100 and spy_var < -0.05:
        return 0
    
    if tips_var < -0.03:
        return 1
    elif tips_var > 0.03:
        return -1
    else:
        return 0


# ============================================================================
# SCORING PRINCIPALE - DUAL SYSTEM
# ============================================================================

def calculate_scores_dual(data, history=None):
    """
    Calcola DUAL SYSTEM: Strategico (6-12M) + Tattico (1-3M)
    
    Returns:
        dict con strategico e tattico scores
    """
    pce_current = data.get('pce_current', 0.025)
    
    # ========== SCORE STRATEGICO (BASE ADATTIVO) ==========
    s_inf = get_inflation_score(data['delta_inf'], pce_current)
    s_ry = get_real_yield_score(data['ry'], pce_current)
    
    curve_hist = history.get('curve_hist') if history else None
    move_hist = history.get('move_hist') if history else None
    
    s_curve = get_curve_score(data['curve'], curve_hist)
    s_move = get_move_score(data['move_avg'], move_hist)
    s_tips = get_tips_score(data['tips_var'], data['move_avg'], data.get('spy_var', 0))
    s_mom = -1 if data['ief_mom'] < -0.015 else (1 if data['ief_mom'] > 0.008 else 0)
    s_equity = 1 if data.get('spy_var', 0) < -0.05 else 0
    
    total_strategico = s_inf + s_move + s_curve + s_ry + s_tips + s_mom
    
    # ========== SCORE TATTICO (CON BOOST) ==========
    
    # Parti dagli score strategici
    s_inf_tatt = s_inf
    s_curve_tatt = s_curve
    s_mom_tatt = s_mom
    
    # BOOST 1: Super-Momentum
    ief_mom_abs = abs(data['ief_mom'])
    if ief_mom_abs > 0.05:  # >5%
        s_mom_tatt = 2 if data['ief_mom'] > 0 else -2
        boost_mom_label = f"Super-Momentum ({data['ief_mom']:.1%})"
    elif ief_mom_abs > 0.03:  # 3-5%
        # Momentum forte ma non estremo
        if data['ief_mom'] > 0:
            s_mom_tatt = min(s_mom + 1, 2)  # Incrementa ma max +2
        else:
            s_mom_tatt = max(s_mom - 1, -2)
        boost_mom_label = f"Momentum Forte ({data['ief_mom']:.1%})"
    else:
        boost_mom_label = None
    
    # BOOST 2: Equity Panic
    spy_var = data.get('spy_var', 0)
    if spy_var < -0.10:  # -10%
        boost_panic = 1
        s_inf_tatt = max(0, s_inf_tatt)  # Inflazione non penalizza
        s_curve_tatt = max(0, s_curve_tatt)  # Curva non penalizza
        boost_panic_label = f"Panic Boost (SPY {spy_var:.1%})"
    elif spy_var < -0.05:  # -5%
        boost_panic = 1
        boost_panic_label = f"Equity Stress (SPY {spy_var:.1%})"
    else:
        boost_panic = 0
        boost_panic_label = None
    
    # BOOST 3: MOVE Context-Aware (Flight to Quality)
    move_boost = 0
    move_boost_label = None
    if data['move_avg'] > 100 and data['ief_mom'] > 0.03 and spy_var < -0.03:
        # MOVE alto + bond salgono + equity giù = Flight to quality
        move_boost = 1
        move_boost_label = f"Flight-to-Quality (MOVE {data['move_avg']:.0f})"
    elif data['move_avg'] > 80 and data['ief_mom'] > 0.05:
        # MOVE moderato ma IEF spike = rally tecnico
        move_boost = 1
        move_boost_label = f"Rally Tecnico (IEF {data['ief_mom']:.1%})"
    
    total_tattico = s_inf_tatt + s_move + s_curve_tatt + s_ry + s_tips + s_mom_tatt + boost_panic + move_boost
    
    # ========== METRICHE COMUNI ==========
    def get_target(score):
        if score >= 3:
            return "15-20+ anni (Aggressivo)"
        elif score >= 1:
            return "7-10 anni (Moderato)"
        elif score <= -1:
            return "1-3 anni (Difensivo)"
        else:
            return "4-6 anni (Neutrale)"
    
    def get_regime(score):
        dur_conf = ((score + 6) / 12) * (1 + s_ry * 0.15)
        sig_stab = abs(score) / 6
        
        if dur_conf > 0.6 and sig_stab < 0.4:
            return "🚀 FASE INIZIALE", "Accumulo graduale"
        elif dur_conf > 0.6 and sig_stab > 0.7:
            return "📢 FASE MATURA", "Hold posizioni"
        elif dur_conf < 0.4:
            return "🚨 REGIME NEGATIVO", "Posizione difensiva"
        else:
            return "⚖️ DIVERGENZA", "Duration intermedia"
    
    dur_conf_strat = ((total_strategico + 6) / 12) * (1 + s_ry * 0.15)
    sig_stab_strat = abs(total_strategico) / 6
    eff_dur_conf_strat = dur_conf_strat * (0.5 + sig_stab_strat * 0.5)
    stress_val_strat = total_strategico - s_move - 1
    
    regime_strat, regime_desc_strat = get_regime(total_strategico)
    
    # Divergenza analysis
    divergenza = abs(total_tattico - total_strategico)
    if divergenza >= 3:
        div_level = "FORTE"
        div_color = "#ff6b6b"
    elif divergenza >= 2:
        div_level = "MODERATA"
        div_color = "#ffa500"
    elif divergenza >= 1:
        div_level = "LIEVE"
        div_color = "#ffeb3b"
    else:
        div_level = "ALLINEATO"
        div_color = "#00ff00"
    
    # Boost labels
    boost_labels = []
    if boost_mom_label:
        boost_labels.append(boost_mom_label)
    if boost_panic_label:
        boost_labels.append(boost_panic_label)
    if move_boost_label:
        boost_labels.append(move_boost_label)
    
    return {
        # Strategico
        'strategico': {
            'total_score': total_strategico,
            'target': get_target(total_strategico),
            'regime': regime_strat,
            'regime_desc': regime_desc_strat,
            'dur_conf': dur_conf_strat,
            'sig_stab': sig_stab_strat,
            'eff_dur_conf': eff_dur_conf_strat,
            'stress_val': stress_val_strat,
            's_inf': s_inf,
            's_move': s_move,
            's_curve': s_curve,
            's_ry': s_ry,
            's_tips': s_tips,
            's_mom': s_mom,
            's_equity': s_equity
        },
        # Tattico
        'tattico': {
            'total_score': total_tattico,
            'target': get_target(total_tattico),
            'boost_labels': boost_labels,
            's_mom': s_mom_tatt,
            'boost_panic': boost_panic,
            'boost_move': move_boost
        },
        # Divergenza
        'divergenza': {
            'delta': divergenza,
            'level': div_level,
            'color': div_color
        },
        # Dati comuni
        'pce_current': pce_current
    }


# ============================================================================
# FETCH DATA
# ============================================================================

@st.cache_data(ttl=3600)
def fetch_live_data():
    """Fetch dati live da FRED e Yahoo Finance."""
    ry_series = fred.get_series('DFII10')
    be_series = fred.get_series('T10YIE')
    unemp = fred.get_series('UNRATE').iloc[-1]
    
    dgs10_series = fred.get_series('DGS10')
    dgs2_series = fred.get_series('DGS2')
    dgs10 = dgs10_series.iloc[-1]
    dgs2 = dgs2_series.iloc[-1]
    
    pce_idx = fred.get_series('PCEPILFE')
    pce_now = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1)
    pce_3m = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1)
    delta_inf = pce_now - pce_3m
    pce_current = pce_now
    
    curve_hist = dgs10_series.tail(252) - dgs2_series.tail(252)
    
    move_data = yf.Ticker("^MOVE").history(period="400d")
    if move_data.empty or len(move_data) < 90:
        move_avg = 70.0
        move_hist_series = None
    else:
        move_avg = move_data["Close"].tail(90).mean()
        move_hist_series = move_data["Close"]
    
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
    
    curve_hist = dgs10_h.tail(252) - dgs2_h.tail(252)
    
    def get_hist_var(ticker, end_date, lookback=130, var_days=30):
        start = end_date - timedelta(days=lookback)
        h = yf.Ticker(ticker).history(
            start=start.strftime('%Y-%m-%d'),
            end=end_date.strftime('%Y-%m-%d')
        )
        if h.empty or len(h) < var_days:
            return 0
        return (h['Close'].iloc[-1] / h['Close'].iloc[-var_days]) - 1
    
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
# UI COMPONENT - DUAL SCORE DISPLAY
# ============================================================================

def display_dual_scores(scores, data):
    """Visualizza score strategico e tattico affiancati."""
    
    st.markdown("### 📊 Dual Monitor: Strategico vs Tattico")
    st.caption("Score Strategico (6-12 mesi) | Score Tattico (1-3 mesi con boost panic/momentum)")
    
    col_strat, col_tatt = st.columns(2)
    
    # ===== COLONNA STRATEGICO =====
    with col_strat:
        st.markdown("#### 🎯 Score Strategico")
        st.caption("Trend 6-12 mesi | Soglie adattive")
        
        strat = scores['strategico']
        
        score_color_strat = (
            "#00ff00" if strat['total_score'] >= 3 else
            "#ffa500" if strat['total_score'] >= 1 else
            "#808080" if strat['total_score'] >= -1 else
            "#ff0000"
        )
        
        st.markdown(f"""
        <div style="background:{score_color_strat}22;border:2px solid {score_color_strat};
                    padding:12px;border-radius:8px;text-align:center;margin-bottom:10px;">
            <div style="font-size:11px;color:#888;">TOTAL SCORE</div>
            <div style="font-size:32px;font-weight:bold;color:white;">
                {strat['total_score']:+d}
            </div>
            <div style="font-size:10px;color:#888;">/ 6</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.metric("Target", strat['target'])
        st.metric("Duration Confidence", f"{strat['dur_conf']:.1%}")
        st.caption(f"**Regime:** {strat['regime']}")
    
    # ===== COLONNA TATTICO =====
    with col_tatt:
        st.markdown("#### ⚡ Score Tattico")
        st.caption("Opportunità 1-3 mesi | Con boost")
        
        tatt = scores['tattico']
        
        score_color_tatt = (
            "#00ff00" if tatt['total_score'] >= 3 else
            "#ffa500" if tatt['total_score'] >= 1 else
            "#808080" if tatt['total_score'] >= -1 else
            "#ff0000"
        )
        
        st.markdown(f"""
        <div style="background:{score_color_tatt}22;border:2px solid {score_color_tatt};
                    padding:12px;border-radius:8px;text-align:center;margin-bottom:10px;">
            <div style="font-size:11px;color:#888;">TOTAL SCORE</div>
            <div style="font-size:32px;font-weight:bold;color:white;">
                {tatt['total_score']:+d}
            </div>
            <div style="font-size:10px;color:#888;">/ {6 + tatt['boost_panic'] + tatt['boost_move']}</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.metric("Target", tatt['target'])
        
        # Boost attivi
        if tatt['boost_labels']:
            st.markdown("**🚀 Boost Attivi:**")
            for label in tatt['boost_labels']:
                st.caption(f"• {label}")
        else:
            st.caption("Nessun boost attivo")
    
    # ===== ANALISI DIVERGENZA =====
    st.divider()
    
    div = scores['divergenza']
    delta_score = scores['tattico']['total_score'] - scores['strategico']['total_score']
    
    st.markdown(f"""
    <div style="background:{div['color']}22;border:1px solid {div['color']};
                padding:12px;border-radius:8px;margin-top:10px;">
        <div style="font-size:13px;font-weight:bold;color:{div['color']};">
            Divergenza: {div['level']} ({delta_score:+d} punti)
        </div>
        <div style="font-size:11px;color:#aaa;margin-top:6px;">
            {get_divergence_explanation(scores, delta_score)}
        </div>
    </div>
    """, unsafe_allow_html=True)


def get_divergence_explanation(scores, delta):
    """Spiega la divergenza tra strategico e tattico."""
    if delta >= 3:
        return ("⚡ **Opportunità tattica forte:** Rally 1-3 mesi probabile (panic spike o momentum estremo), "
                "ma trend 6-12M rimane incerto. Posizione size ridotto con stop.")
    elif delta >= 2:
        return ("⚡ **Segnale tattico positivo:** Possibile rimbalzo 1-2 mesi, "
                "ma prudenza sul medio termine. Duration intermedia con flessibilità.")
    elif delta >= 1:
        return ("⚙️ **Lieve divergenza:** Tattico leggermente più ottimista. "
                "Segui strategico come guida principale.")
    elif delta <= -3:
        return ("🚨 **Alert tattico:** Rischio correzione 1-3 mesi nonostante strategico positivo. "
                "Cautela su entry aggressive.")
    elif delta <= -2:
        return ("⚠️ **Segnale tattico negativo:** Possibile debolezza breve termine. "
                "Attendere conferma prima di aumentare esposizione.")
    elif delta <= -1:
        return ("⚙️ **Lieve divergenza:** Tattico meno ottimista. "
                "Approccio graduale consigliato.")
    else:
        return ("✅ **Allineamento:** Strategico e Tattico concordano. "
                "Segnale coerente su tutti gli orizzonti temporali.")


# ============================================================================
# TABS
# ============================================================================
tab1, tab2 = st.tabs(["📊 Monitor Live", "🔬 Backtest Storico"])

# ============================================================================
# TAB 1: MONITOR LIVE
# ============================================================================
with tab1:
    st.title("🛡️ Bond Monitor Strategico v5.1 DUAL")
    st.caption("🔧 Sistema Dual: Strategico (6-12M) + Tattico (1-3M)")
    
    try:
        d, history = fetch_live_data()
        scores = calculate_scores_dual(d, history)
        
        # Display dual scores
        display_dual_scores(scores, d)
        
        st.divider()
        
        # Metriche aggiuntive
        strat = scores['strategico']
        
        r1, r2, r3 = st.columns(3)
        r1.metric("Signal Stability", f"{strat['sig_stab']:.1%}")
        r2.metric("Eff. Dur. Conf.", f"{strat['eff_dur_conf']:.1%}")
        r3.metric("Stress Test", f"{strat['stress_val']:+d}")
        
        st.divider()
        st.subheader("🔍 Dati di Mercato")
        
        f1, f2, f3 = st.columns(3)
        
        with f1:
            st.write(f"**Real Yield:** {d['ry']:.2f}%")
            st.write(f"**Breakeven:** {d['be']:.2f}%")
        
        with f2:
            st.write(f"**Curva 10-2Y:** {d['curve']:.2f}%")
            st.write(f"**MOVE 3M:** {d['move_avg']:.1f}")
        
        with f3:
            st.write(f"**IEF Mom:** {d['ief_mom']:.2%}")
            st.write(f"**SPY Var:** {d['spy_var']:.2%}")
        
        # Debug expander
        with st.expander("🔧 Debug — Breakdown Completo"):
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
            
            st.markdown("---")
            st.markdown("**Score Strategico:**")
            
            breakdown_df = pd.DataFrame({
                'Componente': ['Inflation', 'MOVE', 'Curve', 'Real Yield', 'TIPS', 'Momentum'],
                'Score': [
                    strat['s_inf'], strat['s_move'], strat['s_curve'],
                    strat['s_ry'], strat['s_tips'], strat['s_mom']
                ],
                'Valore': [
                    f"{d['delta_inf']:.2%}",
                    f"{d['move_avg']:.1f}",
                    f"{d['curve']:.2%}",
                    f"{d['ry']:.2f}%",
                    f"{d['tips_var']:.2%}",
                    f"{d['ief_mom']:.2%}"
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
            
            st.markdown("---")
            st.markdown("**Score Tattico:**")
            tatt = scores['tattico']
            st.write(f"Momentum Score: {tatt['s_mom']} (vs {strat['s_mom']} strategico)")
            st.write(f"Boost Panic: +{tatt['boost_panic']}")
            st.write(f"Boost MOVE: +{tatt['boost_move']}")
        
        # Grafici
        st.divider()
        g1, g2 = st.columns(2)
        
        with g1:
            fig_ry = go.Figure()
            fig_ry.add_trace(go.Scatter(
                x=d['ry_hist'].index, y=d['ry_hist'].values,
                name="Real Yield", line=dict(color='#00ff00')
            ))
            fig_ry.update_layout(
                title="Real Yield 10Y",
                template="plotly_dark", height=250,
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
                title="Breakeven Inflation",
                template="plotly_dark", height=250,
                margin=dict(l=20, r=20, t=40, b=20)
            )
            st.plotly_chart(fig_be, use_container_width=True)
        
        with st.expander("📖 Come Funziona il Dual System"):
            st.markdown("""
            ### 🎯 Sistema Dual
            
            **Score Strategico (6-12 mesi):**
            - Soglie adattive base
            - Cattura trend sostenuti
            - Filtro rumore breve termine
            
            **Score Tattico (1-3 mesi):**
            - Parte dallo strategico
            - Aggiunge boost per:
              - **Super-Momentum**: IEF >5% = +2 (invece di +1)
              - **Equity Panic**: SPY <-10% = +1 bonus
              - **Flight-to-Quality**: MOVE alto + IEF up + SPY down = +1
            
            **Quando usare:**
            - **Divergenza forte** (≥3 punti): Opportunità tattica vs trend strategico
            - **Allineamento**: Segnale confermato su tutti gli orizzonti
            - **Divergenza negativa**: Cautela tattica nonostante strategico positivo
            """)
    
    except Exception as e:
        st.error(f"❌ Errore: {e}")

# ============================================================================
# TAB 2: BACKTEST
# ============================================================================
with tab2:
    st.title("🔬 Backtest Storico - Dual System")
    st.caption("Verifica score Strategico e Tattico su date storiche")
    
    st.divider()
    
    col_date, col_btn = st.columns([2, 1])
    
    with col_date:
        backtest_date = st.date_input(
            "📅 Data Analisi",
            value=datetime(2020, 3, 23),
            min_value=datetime(2010, 1, 1),
            max_value=datetime.now()
        )
    
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        run_bt = st.button("🔍 Calcola", use_container_width=True)
    
    if run_bt:
        with st.spinner("Caricamento..."):
            try:
                date_key = backtest_date.strftime('%Y-%m-%d')
                bt_data, bt_history = fetch_backtest_data(date_key)
                scores_bt = calculate_scores_dual(bt_data, bt_history)
                
                if bt_data.get("move_warning"):
                    st.warning("⚠️ MOVE storico non disponibile")
                
                st.success(f"✅ Dati {backtest_date.strftime('%d/%m/%Y')}")
                st.divider()
                
                # Display dual scores
                display_dual_scores(scores_bt, bt_data)
                
                # Dati raw
                st.divider()
                st.subheader("📋 Dati alla Data")
                
                raw_df = pd.DataFrame({
                    'Indicatore': ['Real Yield', 'Curva', 'MOVE', 'Delta Inf', 'IEF Mom', 'SPY Var'],
                    'Valore': [
                        f"{bt_data['ry']:.2f}%",
                        f"{bt_data['curve']:.2f}%",
                        f"{bt_data['move_avg']:.1f}",
                        f"{bt_data['delta_inf']:.2%}",
                        f"{bt_data['ief_mom']:.2%}",
                        f"{bt_data['spy_var']:.2%}"
                    ]
                })
                st.dataframe(raw_df, use_container_width=True, hide_index=True)
                
                # ETF riferimento
                strat_target = scores_bt['strategico']['target']
                tatt_target = scores_bt['tattico']['target']
                
                st.markdown(f"""
                **📊 Target Strategico:** {strat_target}  
                **⚡ Target Tattico:** {tatt_target}
                
                Verifica performance IEF/TLT/SHY nei mesi successivi per validare.
                """)
            
            except Exception as e:
                st.error(f"❌ Errore: {e}")
    else:
        st.info("👆 Seleziona data e calcola")

st.markdown("---")
st.caption(f"🛡️ Bond Monitor v5.1 DUAL SYSTEM | {datetime.now().strftime('%d/%m/%Y %H:%M')}")
st.caption("⚙️ Strategico (6-12M) + Tattico (1-3M) | Non costituisce consulenza finanziaria")
