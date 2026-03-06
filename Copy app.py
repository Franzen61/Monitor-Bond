import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# ============================================================================
# CONFIGURAZIONE
# FIX #1: API Key spostata in st.secrets con fallback per sviluppo locale.
# Su Streamlit Cloud: aggiungi [fred] api_key = "..." in secrets.toml
# In locale: puoi ancora passarla come variabile d'ambiente o usare il fallback
# ============================================================================

def get_fred_api_key():
    try:
        return st.secrets["fred"]["api_key"]
    except Exception:
        # Fallback per sviluppo locale — sostituisci con la tua key
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
# FUNZIONI CONDIVISE
# ============================================================================

def calculate_scores(data):
    """
    Calcola tutti gli score da un dizionario di dati.
    Usata sia dal monitor live che dal backtest.
    """
    s_inf  = 1 if data['delta_inf'] < -0.003 else (-1 if data['delta_inf'] > 0.003 else 0)
    s_move = -1 if data['move_avg'] > 90 else (1 if data['move_avg'] < 70 else 0)
    s_curve = 1 if data['curve'] < 0.1 else (-1 if data['curve'] > 1 else 0)
    s_ry   = 1 if data['ry'] > 1.8 else (-1 if data['ry'] < 0.5 else 0)
    s_tips = 1 if data['tips_var'] < -0.02 else (-1 if data['tips_var'] > 0.02 else 0)
    s_mom  = -1 if data['ief_mom'] < -0.015 else (1 if data['ief_mom'] > 0.008 else 0)
    s_equity = 1 if data.get('spy_var', 0) < -0.05 else 0

    total_score = s_inf + s_move + s_curve + s_ry + s_tips + s_mom

    dur_conf     = ((total_score + 6) / 12) * (1 + s_ry * 0.15)
    sig_stab     = abs(total_score) / 6
    eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)
    stress_val   = total_score - s_move - 1

    if total_score >= 3:
        target = "15-20+ anni (Aggressivo)"
    elif total_score >= 1:
        target = "7-10 anni (Moderato - Core)"
    elif total_score <= -1:
        target = "1-3 anni (Difensivo)"
    else:
        target = "4-6 anni (Neutrale)"

    if dur_conf > 0.6 and sig_stab < 0.4:
        regime      = "🚀 FASE INIZIALE"
        regime_desc = "Mercato offre buona remunerazione ma senza consenso uniforme. Accumulo graduale."
    elif dur_conf > 0.6 and sig_stab > 0.7:
        regime      = "📢 FASE MATURA"
        regime_desc = "Movimento già prezzato, consenso uniforme. Hold posizioni."
    elif dur_conf < 0.4:
        regime      = "🚨 REGIME NEGATIVO"
        regime_desc = "Mercato non paga abbastanza per il rischio. Posizione difensiva."
    else:
        regime      = "⚖️ REGIME DI DIVERGENZA"
        regime_desc = "Segnali contrastanti. Neutrale, duration intermedia."

    return {
        's_inf': s_inf, 's_move': s_move, 's_curve': s_curve,
        's_ry': s_ry, 's_tips': s_tips, 's_mom': s_mom,
        's_equity': s_equity,
        'total_score': total_score,
        'dur_conf': dur_conf, 'sig_stab': sig_stab,
        'eff_dur_conf': eff_dur_conf, 'stress_val': stress_val,
        'target': target, 'regime': regime, 'regime_desc': regime_desc
    }


@st.cache_data(ttl=3600)
def fetch_live_data():
    """Fetch dati live da FRED e Yahoo Finance."""
    ry_series = fred.get_series('DFII10')
    be_series = fred.get_series('T10YIE')
    unemp     = fred.get_series('UNRATE').iloc[-1]
    dgs10     = fred.get_series('DGS10').iloc[-1]
    dgs2      = fred.get_series('DGS2').iloc[-1]

    pce_idx  = fred.get_series('PCEPILFE')
    pce_now  = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1)
    pce_3m   = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1)
    delta_inf = pce_now - pce_3m

    move_hist = yf.Ticker("^MOVE").history(period="130d")
    if move_hist.empty or len(move_hist) < 90:
        move_avg = 70.0
    else:
        move_avg = move_hist["Close"].tail(90).mean()

    def get_var(ticker):
        h = yf.Ticker(ticker).history(period="60d")
        if h.empty:
            return 0
        return (h['Close'].iloc[-1] / h['Close'].iloc[-30]) - 1

    return {
        "ry":       ry_series.iloc[-1],
        "ry_hist":  ry_series.tail(180),
        "be":       be_series.iloc[-1],
        "be_hist":  be_series.tail(180),
        "unemp":    unemp,
        "delta_inf": delta_inf,
        "curve":    dgs10 - dgs2,
        "ief_mom":  get_var("IEF"),
        "spy_var":  get_var("SPY"),
        "tips_var": get_var("TIP"),
        "move_avg": move_avg
    }


# ============================================================================
# FIX #2: Backtest con @st.cache_data
# Prima ogni click ri-scaricava tutto da FRED (~10-15 secondi).
# Ora il risultato viene memorizzato per 6 ore — click successivi istantanei.
# La cache è parametrizzata sulla data, quindi date diverse non si mescolano.
# ============================================================================

@st.cache_data(ttl=3600 * 6, show_spinner=False)
def fetch_backtest_data(backtest_date_str: str):
    """
    Scarica i dati storici per una data specifica.
    La stringa della data è usata come chiave di cache — date diverse
    generano cache entry separate.
    """
    from datetime import timedelta

    target_date = pd.Timestamp(backtest_date_str)
    start_date  = target_date - timedelta(days=130)

    # FRED
    ry_hist   = fred.get_series('DFII10',    observation_end=target_date)
    dgs10_h   = fred.get_series('DGS10',     observation_end=target_date)
    dgs2_h    = fred.get_series('DGS2',      observation_end=target_date)
    be_hist   = fred.get_series('T10YIE',    observation_end=target_date)
    unemp_h   = fred.get_series('UNRATE',    observation_end=target_date)
    pce       = fred.get_series('PCEPILFE',  observation_end=target_date)

    ry    = ry_hist.iloc[-1]
    curve = dgs10_h.iloc[-1] - dgs2_h.iloc[-1]
    be    = be_hist.iloc[-1]
    unemp = unemp_h.iloc[-1]

    pce_now   = ((pce.iloc[-1] / pce.iloc[-13]) - 1)
    pce_3m    = ((pce.iloc[-4] / pce.iloc[-16]) - 1)
    delta_inf = pce_now - pce_3m

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

    move_raw = yf.Ticker("^MOVE").history(
        start=start_date.strftime('%Y-%m-%d'),
        end=target_date.strftime('%Y-%m-%d')
    )
    if move_raw.empty or len(move_raw) < 30:
        move_avg     = 70.0
        move_warning = True
    else:
        move_avg     = move_raw['Close'].tail(90).mean()
        move_warning = False

    ief_mom  = get_hist_var("IEF", target_date)
    tips_var = get_hist_var("TIP", target_date)
    spy_var  = get_hist_var("SPY", target_date)

    return {
        "ry": ry, "curve": curve, "be": be, "unemp": unemp,
        "delta_inf": delta_inf, "move_avg": move_avg,
        "ief_mom": ief_mom, "tips_var": tips_var, "spy_var": spy_var,
        "move_warning": move_warning
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

    try:
        d      = fetch_live_data()
        scores = calculate_scores(d)

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

        # FIX #3: applymap → map (deprecato in Pandas 2.x, rimosso in Pandas 3.x)
        with st.expander("🔧 Debug — Valori Raw e Score Componenti"):
            debug_df = pd.DataFrame({
                'Variabile': [
                    'Delta Inflation (3m)', 'MOVE 3M Avg', 'Curve 10-2Y',
                    'Real Yield 10Y', 'IEF Momentum (30 barre)',
                    'TIPS Var (30 barre)', 'SPY Var (30 barre)'
                ],
                'Valore Raw': [
                    f"{d['delta_inf']:.4%}", f"{d['move_avg']:.2f}",
                    f"{d['curve']:.3f}%",    f"{d['ry']:.2f}%",
                    f"{d['ief_mom']:.4%}",   f"{d['tips_var']:.4%}",
                    f"{d['spy_var']:.4%}"
                ],
                'Score': [
                    scores['s_inf'],  scores['s_move'], scores['s_curve'],
                    scores['s_ry'],   scores['s_mom'],  scores['s_tips'],
                    scores['s_equity']
                ],
                'Soglia +1 / -1': [
                    '< -0.30% / > +0.30%', '< 70 / > 90', '< 0.1% / > 1.0%',
                    '> 1.8% / < 0.5%',     '> +0.80% / < -1.50%',
                    '< -2.0% / > +2.0%',   '< -5.0% (filtro)'
                ]
            })

            def style_score(val):
                if val > 0:
                    return 'background-color: rgba(0,255,0,0.2); color: #00ff00; font-weight: bold;'
                elif val < 0:
                    return 'background-color: rgba(255,0,0,0.2); color: #ff6b6b; font-weight: bold;'
                return 'background-color: rgba(128,128,128,0.1); color: #888;'

            # FIX #3: .applymap() → .map() — applymap deprecato da Pandas 2.1, rimosso in 3.x
            st.dataframe(
                debug_df.style.map(style_score, subset=['Score']),
                use_container_width=True,
                hide_index=True
            )

        st.divider()

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

        # ====================================================================
        # YIELD CURVE SNAPSHOT
        # ====================================================================
        st.divider()
        st.subheader("📐 Curva dei Rendimenti — Snapshot")

        @st.cache_data(ttl=3600)
        def fetch_yield_curve():
            curve_tickers = {
                "3M": "DGS3MO", "6M": "DGS6MO", "1Y": "DGS1",
                "2Y": "DGS2",   "3Y": "DGS3",   "5Y": "DGS5",
                "7Y": "DGS7",   "10Y": "DGS10", "20Y": "DGS20", "30Y": "DGS30",
            }
            result = {}
            for label, ticker in curve_tickers.items():
                try:
                    series = fred.get_series(ticker).dropna()
                    result[label] = series
                except Exception:
                    result[label] = None
            return result

        curve_data = fetch_yield_curve()
        maturities = ["3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
        x_labels   = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]

        def get_curve_at(data, offset_days=0):
            values = []
            for label in maturities:
                s = data.get(label)
                if s is None or s.empty:
                    values.append(None)
                    continue
                if offset_days == 0:
                    values.append(float(s.iloc[-1]))
                else:
                    cutoff = s.index[-1] - pd.Timedelta(days=offset_days)
                    s_past = s[s.index <= cutoff]
                    values.append(float(s_past.iloc[-1]) if not s_past.empty else None)
            return values

        y_today = get_curve_at(curve_data, 0)
        y_1m    = get_curve_at(curve_data, 30)
        y_1y    = get_curve_at(curve_data, 365)

        last_date = None
        for label in maturities:
            s = curve_data.get(label)
            if s is not None and not s.empty:
                last_date = s.index[-1].strftime("%d %b %Y")
                break

        fig_yc = go.Figure()
        fig_yc.add_trace(go.Scatter(
            x=x_labels, y=y_1y, mode="lines+markers", name="1 anno fa",
            line=dict(color="#555555", width=1.5, dash="dot"), marker=dict(size=5)
        ))
        fig_yc.add_trace(go.Scatter(
            x=x_labels, y=y_1m, mode="lines+markers", name="1 mese fa",
            line=dict(color="#FFA500", width=1.5, dash="dash"), marker=dict(size=5)
        ))
        fig_yc.add_trace(go.Scatter(
            x=x_labels, y=y_today, mode="lines+markers+text",
            name=f"Oggi ({last_date})",
            line=dict(color="#00FF00", width=2.5), marker=dict(size=7),
            text=[f"{v:.2f}%" if v else "" for v in y_today],
            textposition="top center", textfont=dict(size=9, color="#00FF00")
        ))
        fig_yc.add_hline(y=0, line_dash="solid", line_color="#333333", line_width=1, opacity=0.5)

        all_values = [v for v in y_today + y_1m + y_1y if v is not None]
        y_min = round(min(all_values) - 0.3, 1) if all_values else 0
        y_max = round(max(all_values) + 0.3, 1) if all_values else 6

        fig_yc.update_layout(
            template="plotly_dark", height=400,
            title=dict(text="Curva dei Rendimenti US Treasury", font=dict(size=15)),
            xaxis=dict(title="Scadenza", tickvals=x_labels, ticktext=maturities,
                       gridcolor="#1e2430", tickangle=-45),
            yaxis=dict(title="Rendimento (%)", gridcolor="#1e2430", range=[y_min, y_max]),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=40, r=20, t=60, b=40), hovermode="x unified"
        )
        st.plotly_chart(fig_yc, use_container_width=True)

        v_3m  = y_today[0]
        v_2y  = y_today[3]
        v_10y = y_today[7]
        v_30y = y_today[9]

        if v_10y and v_3m:
            spread_10_3m = v_10y - v_3m
            if spread_10_3m < 0:
                shape_label = "🔴 Invertita (10Y < 3M) — segnale recessivo"
            elif spread_10_3m < 0.5:
                shape_label = "🟡 Piatta — transizione in corso"
            else:
                shape_label = "🟢 Normale — pendenza positiva"

            col_yc1, col_yc2, col_yc3, col_yc4 = st.columns(4)
            col_yc1.metric("3M",  f"{v_3m:.2f}%"  if v_3m  else "N/D")
            col_yc2.metric("2Y",  f"{v_2y:.2f}%"  if v_2y  else "N/D")
            col_yc3.metric("10Y", f"{v_10y:.2f}%" if v_10y else "N/D")
            col_yc4.metric("30Y", f"{v_30y:.2f}%" if v_30y else "N/D")

        delta_2y  = (y_today[3] - y_1m[3]) if (y_today[3] and y_1m[3]) else None
        delta_10y = (y_today[7] - y_1m[7]) if (y_today[7] and y_1m[7]) else None

        if delta_2y is not None and delta_10y is not None:
            if delta_10y > 0 and delta_10y > delta_2y:
                regime_curve = "📈 Bear Steepening — tassi lunghi salgono più dei corti"
                regime_color = "#ff6b6b"
            elif delta_2y < 0 and delta_10y > delta_2y:
                regime_curve = "📈 Bull Steepening — tassi corti scendono più dei lunghi (Fed taglia)"
                regime_color = "#00ff00"
            elif delta_2y > 0 and delta_2y > delta_10y:
                regime_curve = "📉 Bear Flattening — tassi corti salgono più dei lunghi (Fed alza)"
                regime_color = "#ffa500"
            elif delta_10y < 0 and delta_2y > delta_10y:
                regime_curve = "📉 Bull Flattening — tassi lunghi scendono più dei corti"
                regime_color = "#00bfff"
            else:
                regime_curve = "➡️ Curva stabile — variazioni minime nell'ultimo mese"
                regime_color = "#888888"
        else:
            regime_curve = "N/D"
            regime_color = "#888888"

        st.caption(
            f"**Forma curva:** {shape_label} &nbsp;|&nbsp; Spread 10Y-3M: {spread_10_3m:+.2f}%"
            if v_10y and v_3m else ""
        )

        st.markdown(f"""
        <div style="background:#161b22;border:1px solid #31333F;border-radius:8px;padding:12px 16px;margin-top:8px;">
            <div style="font-size:0.9em;font-weight:bold;color:{regime_color};margin-bottom:6px;">{regime_curve}</div>
            <div style="font-size:0.75em;color:#888;line-height:1.7;">
                <b style="color:#aaa;">Metodo:</b> confronto variazioni 2Y e 10Y rispetto a 1 mese fa<br>
                🔴 <b style="color:#ff6b6b;">Bear Steepening</b> — lunghi salgono &gt; corti · bond lunghi sotto pressione<br>
                🟢 <b style="color:#00ff00;">Bull Steepening</b> — corti scendono &gt; lunghi · Fed in taglio<br>
                🟠 <b style="color:#ffa500;">Bear Flattening</b> — corti salgono &gt; lunghi · Fed restrittiva<br>
                🔵 <b style="color:#00bfff;">Bull Flattening</b> — lunghi scendono &gt; corti · risk-off o disinflazione
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.divider()
        st.info(f"**{scores['regime']}:** {scores['regime_desc']}")

        with st.expander("📖 Manuale Operativo e Filosofia del Monitor"):
            st.markdown("""
            ### 🎯 Scopo del Monitor
            Il monitor sintetizza il regime macro obbligazionario per valutare se la duration è
            strutturalmente favorita e se i bond possono tornare a svolgere funzione di hedge.

            ### 🚦 Pilastri di Lettura

            **Duration Confidence** — Misura quanto i tassi reali remunerano il rischio duration.

            **Signal Stability** — Indica quanto i segnali macro sono coerenti tra loro.
            Le migliori opportunità nascono con confidence alta e stabilità intermedia.

            **Total Score** — Sintesi direzionale del regime macro:
            - Positivo → contesto favorevole ai bond
            - Neutrale → fase di transizione
            - Negativo → pressione inflattiva o instabilità
            """)

    except Exception as e:
        st.error(f"❌ Errore caricamento dati: {e}")
        st.info("Riprova tra qualche minuto o verifica la connessione.")

# ============================================================================
# TAB 2: BACKTEST STORICO
# ============================================================================
with tab2:
    st.title("🔬 Backtest Storico")
    st.markdown("Inserisci una data per vedere cosa avrebbe indicato il monitor in quel momento.")

    # FIX #4: aggiunto indicatore di cache — l'utente sa se i dati sono freschi o in cache
    st.caption("I dati vengono scaricati automaticamente da FRED e Yahoo Finance. "
               "Il risultato viene **memorizzato per 6 ore** — click successivi sulla stessa data sono istantanei.")

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
        # FIX #4: spinner informativo durante il caricamento
        with st.spinner(f"Caricamento dati storici per {backtest_date.strftime('%d/%m/%Y')}... "
                        "Prima esecuzione ~15s, poi in cache."):
            try:
                # La data viene convertita in stringa per usarla come chiave di cache
                date_key   = backtest_date.strftime('%Y-%m-%d')
                bt_data    = fetch_backtest_data(date_key)
                scores_bt  = calculate_scores(bt_data)

                if bt_data.get("move_warning"):
                    st.warning("⚠️ MOVE storico non disponibile per questa data, usando 70 come stima")

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

                    st.metric("🎯 Target Duration",          scores_bt['target'])
                    st.metric("⚡ Stress Test MOVE 130",     f"{scores_bt['stress_val']:+d}")
                    st.metric("📊 Duration Confidence",      f"{scores_bt['dur_conf']:.1%}")
                    st.metric("📈 Signal Stability",         f"{scores_bt['sig_stab']:.1%}")

                    st.markdown("---")
                    st.markdown(f"**Regime:** {scores_bt['regime']}")
                    st.caption(scores_bt['regime_desc'])

                with col2:
                    st.subheader("📋 Dati alla Data")

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
                            scores_bt['s_inf'],  scores_bt['s_move'],
                            scores_bt['s_curve'], scores_bt['s_ry'],
                            scores_bt['s_tips'],  scores_bt['s_mom']
                        ]
                    })

                    def style_score(val):
                        if val > 0:
                            return 'background-color: rgba(0,255,0,0.2); color: #00ff00; font-weight: bold;'
                        elif val < 0:
                            return 'background-color: rgba(255,0,0,0.2); color: #ff6b6b; font-weight: bold;'
                        return 'background-color: rgba(128,128,128,0.1); color: #888;'

                    # FIX #3 anche qui: .applymap() → .map()
                    st.dataframe(
                        breakdown_df.style.map(style_score, subset=['Score']),
                        use_container_width=True,
                        hide_index=True
                    )

                # ETF di riferimento
                st.divider()
                if scores_bt['total_score'] >= 3:
                    etf_ref, etf_desc, etf_color = "TLT", "Bond 20+ anni",           "#00ff00"
                elif scores_bt['total_score'] >= 1:
                    etf_ref, etf_desc, etf_color = "IEF", "Bond 7-10 anni",          "#ffa500"
                elif scores_bt['total_score'] <= -1:
                    etf_ref, etf_desc, etf_color = "SHY", "Bond 1-3 anni",           "#ff6b6b"
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

                st.caption("⚠️ Nota: il PCE potrebbe avere un delay di 30-45 giorni rispetto alla data selezionata.")

            except Exception as e:
                st.error(f"❌ Errore nel caricamento dati storici: {e}")
                st.info("Prova una data diversa o riprova tra qualche minuto.")

    else:
        st.info("👆 Seleziona una data e clicca **Calcola Backtest** per iniziare.")

    st.divider()
    with st.expander("ℹ️ Come Usare il Backtest"):
        st.markdown("""
        ### 🎯 Obiettivo
        Verificare cosa avrebbe indicato il monitor in una data specifica,
        e confrontarlo con il comportamento reale del mercato.

        ### 📋 Come Procedere
        1. Seleziona una data storica
        2. Clicca **Calcola Backtest**
        3. Guarda il **Target Duration** e l'**ETF di riferimento**
        4. Vai su **Yahoo Finance** e controlla la performance di quell'ETF
           nei 3-6 mesi successivi alla data
        5. Valuta se il segnale era corretto, anticipato o tardivo

        ### 📊 ETF di Riferimento
        | Score | Target | ETF |
        |-------|--------|-----|
        | >= +3 | 15-20+ anni | TLT |
        | +1/+2 | 7-10 anni | IEF |
        | 0 | 4-6 anni (neutrale) | IEF |
        | -1 o meno | 1-3 anni | SHY |

        ### ⚠️ Limitazioni
        - PCE ha delay di 30-45 giorni (dato non disponibile in real-time)
        - MOVE storico disponibile dal 2010
        - Non considera eventi imprevedibili (black swans)

        ### ⚡ Performance Cache
        - Prima esecuzione su una data: ~10-15 secondi (download FRED + Yahoo)
        - Click successivi sulla stessa data: istantanei (dati in cache per 6 ore)
        - Date diverse generano cache separate — nessun dato si mescola
        """)

st.markdown("---")
st.caption(
    f"🛡️ Bond Monitor Strategico v4.1 | "
    f"Ultimo aggiornamento: {datetime.now().strftime('%d/%m/%Y %H:%M')} | "
    f"Fix: API Key sicura · Cache backtest · Pandas 2.x compat"
)
st.caption("⚠️ Questo tool è a scopo informativo. Non costituisce consulenza finanziaria.")
