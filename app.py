import streamlit as st
import yfinance as yf
from fredapi import Fred
import pandas as pd
import plotly.graph_objects as go
import requests
from bs4 import BeautifulSoup
import numpy as np
from datetime import datetime, timedelta

# ============================================================================
# CONFIGURAZIONE
# ============================================================================

FRED_API_KEY = '938a76ed726e8351f43e1b0c36365784'
fred = Fred(api_key=FRED_API_KEY)

st.set_page_config(
    page_title="Bond Monitor Strategico",
    page_icon="🛡️",
    layout="wide"
)

# ============================================================================
# CSS PERSONALIZZATO
# ============================================================================

st.markdown("""
    <style>
    .main { 
        background-color: #0e1117; 
    }
    .stMetric { 
        border: 1px solid #31333F; 
        padding: 15px; 
        border-radius: 10px; 
        background-color: #1a1d24;
    }
    div[data-testid="stExpander"] { 
        border: 1px solid #31333F; 
        background-color: #161b22; 
        margin-top: 20px; 
    }
    .score-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px;
        border-radius: 15px;
        text-align: center;
        color: white;
        font-size: 24px;
        font-weight: bold;
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

# ============================================================================
# FUNZIONI DI FETCH DATI
# ============================================================================

def get_cleveland_nowcast():
    """
    Scrapa Core PCE Year-over-Year da Cleveland Fed Inflation Nowcasting
    
    Returns:
        dict: Dizionario con valore, mese, data aggiornamento, fonte
        None: Se scraping fallisce
    """
    url = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Trova tutte le tabelle nella pagina
        tables = soup.find_all('table')
        
        if len(tables) < 2:
            raise Exception("Struttura HTML cambiata: tabelle non trovate")
        
        # La seconda tabella contiene Year-over-Year data
        yoy_table = tables[1]
        rows = yoy_table.find_all('tr')
        
        if len(rows) < 2:
            raise Exception("Dati tabella insufficienti")
        
        # Prima riga con dati (mese più recente)
        latest_row = rows[1]
        cells = latest_row.find_all('td')
        
        if len(cells) < 6:
            raise Exception("Formato cella inaspettato")
        
        # Estrai valori
        month = cells[0].text.strip()
        core_pce_yoy_text = cells[4].text.strip()  # Core PCE è colonna 4
        
        # Gestisci celle vuote (dati ufficiali già rilasciati)
        if not core_pce_yoy_text or core_pce_yoy_text == '':
            # Prova riga successiva
            latest_row = rows[2]
            cells = latest_row.find_all('td')
            month = cells[0].text.strip()
            core_pce_yoy_text = cells[4].text.strip()
        
        core_pce_yoy = float(core_pce_yoy_text)
        update_date = cells[5].text.strip()
        
        return {
            'value': core_pce_yoy,
            'month': month,
            'update_date': update_date,
            'source': 'Cleveland Fed Nowcast',
            'url': url
        }
        
    except requests.exceptions.RequestException as e:
        st.warning(f"⚠️ Errore connessione Cleveland Fed: {e}")
        return None
    except Exception as e:
        st.warning(f"⚠️ Errore parsing Cleveland Fed: {e}")
        return None


def get_pce_with_fallback():
    """
    Ottiene Core PCE YoY con strategia ibrida:
    1. Prova Cleveland Fed Nowcast (aggiornato settimanalmente)
    2. Fallback su FRED (ufficiale ma ritardato)
    
    Returns:
        tuple: (valore_pce, mese_riferimento, fonte, giorni_vecchiaia)
    """
    # Layer 1: Cleveland Nowcast
    cleveland = get_cleveland_nowcast()
    
    if cleveland:
        # Calcola età dato
        try:
            # Parse update date (formato MM/DD)
            month_num, day_num = cleveland['update_date'].split('/')
            update_datetime = datetime(2026, int(month_num), int(day_num))
            data_age = (datetime.now() - update_datetime).days
        except:
            data_age = 0
        
        return cleveland['value'], cleveland['month'], 'Cleveland Nowcast', data_age
    
    # Layer 2: Fallback FRED
    try:
        pce_idx = fred.get_series('PCEPILFE')
        pce_yoy = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1) * 100
        pce_date = pce_idx.index[-1]
        data_age = (pd.Timestamp.now() - pce_date).days
        
        return pce_yoy, pce_date.strftime('%B %Y'), 'FRED (Ufficiale)', data_age
    
    except Exception as e:
        st.error(f"❌ Errore FRED: {e}")
        return 2.8, "Sconosciuto", "Fallback statico", 999


def get_var(ticker, days=30):
    """
    Calcola variazione percentuale ticker negli ultimi N giorni
    
    Args:
        ticker: Symbol ticker (es. "SPY", "TIP")
        days: Numero giorni lookback (default 30)
    
    Returns:
        float: Variazione percentuale (es. -0.05 = -5%)
        None: Se dati non disponibili
    """
    try:
        h = yf.Ticker(ticker).history(period="60d")
        
        if h.empty or len(h) < days:
            st.warning(f"⚠️ Dati insufficienti per {ticker}")
            return None
        
        return (h['Close'].iloc[-1] / h['Close'].iloc[-days]) - 1
    
    except Exception as e:
        st.error(f"❌ Errore fetching {ticker}: {e}")
        return None


@st.cache_data(ttl=3600)
def fetch_data():
    """
    Raccoglie tutti i dati necessari da FRED, Yahoo Finance, Cleveland Fed
    Cache: 1 ora
    
    Returns:
        dict: Dizionario con tutti i dati necessari per il monitor
    """
    try:
        # ---- DATI FRED ----
        ry_series = fred.get_series('DFII10')
        be_series = fred.get_series('T10YIE')
        unemp = fred.get_series('UNRATE').iloc[-1]
        dgs10 = fred.get_series('DGS10').iloc[-1]
        dgs2 = fred.get_series('DGS2').iloc[-1]
        
        # ---- CORE PCE (Cleveland + FRED Fallback) ----
        pce_current, pce_month, pce_source, pce_age = get_pce_with_fallback()
        
        # Delta Inflation (confronto con 3 mesi fa)
        # Usa FRED per calcolo storico anche se current viene da Cleveland
        pce_idx = fred.get_series('PCEPILFE')
        pce_3m_ago = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1) * 100
        delta_inf = pce_current - pce_3m_ago
        
        # ---- YAHOO FINANCE ----
        ief_mom = get_var("IEF", days=30)
        spy_var = get_var("SPY", days=30)
        tips_var = get_var("TIP", days=30)
        
        # Gestione None (se fetch fallisce, usa 0)
        ief_mom = ief_mom if ief_mom is not None else 0
        spy_var = spy_var if spy_var is not None else 0
        tips_var = tips_var if tips_var is not None else 0
        
        return {
            "ry": ry_series.iloc[-1],
            "ry_hist": ry_series.tail(180),
            "be": be_series.iloc[-1],
            "be_hist": be_series.tail(180),
            "unemp": unemp,
            "delta_inf": delta_inf,
            "pce_current": pce_current,
            "pce_month": pce_month,
            "pce_source": pce_source,
            "pce_age": pce_age,
            "curve": dgs10 - dgs2,
            "ief_mom": ief_mom,
            "spy_var": spy_var,
            "tips_var": tips_var
        }
    
    except Exception as e:
        st.error(f"❌ Errore critico nel fetch dati: {e}")
        st.stop()


# ============================================================================
# CARICAMENTO DATI
# ============================================================================

try:
    d = fetch_data()
except Exception as e:
    st.error(f"❌ Impossibile caricare i dati. Riprova tra qualche minuto.")
    st.error(f"Dettaglio errore: {e}")
    st.stop()

# ============================================================================
# SIDEBAR - PARAMETRI MANUALI
# ============================================================================

st.sidebar.header("⚙️ Parametri Live")
st.sidebar.markdown("---")

move_val = st.sidebar.number_input(
    "MOVE Index", 
    value=70.01,
    min_value=0.0,
    max_value=200.0,
    step=0.1,
    help="Indice di volatilità bond market. Aggiorna manualmente."
)

st.sidebar.markdown("---")
st.sidebar.caption(f"💾 Cache aggiornata ogni ora")
st.sidebar.caption(f"🕐 Ultimo refresh: {datetime.now().strftime('%H:%M:%S')}")

# ============================================================================
# CALCOLO SCORE COMPONENTI
# ============================================================================

# Inflation Score
s_inf = 1 if d['delta_inf'] < -0.003 else (-1 if d['delta_inf'] > 0.003 else 0)

# MOVE Score
s_move = -1 if move_val > 90 else (1 if move_val < 70 else 0)

# Curve Score
s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)

# Real Yield Score
s_ry = 1 if d['ry'] > 1.8 else (-1 if d['ry'] < 0.5 else 0)

# TIPS Score
s_tips = 1 if d['tips_var'] < -0.02 else (-1 if d['tips_var'] > 0.02 else 0)

# Momentum Score
s_mom = -1 if d['ief_mom'] < -0.015 else (1 if d['ief_mom'] > 0.008 else 0)

# Equity Panic Score
s_equity = 1 if d['spy_var'] < -0.05 else 0

# Total Score
total_score = s_inf + s_move + s_curve + s_ry + s_tips + s_mom + s_equity

# ============================================================================
# CALCOLO RATIOS
# ============================================================================

# Duration Confidence
dur_conf = ((total_score + 6) / 12) * (1 + s_ry * 0.15)

# Signal Stability
sig_stab = abs(total_score) / 6

# Effective Duration Confidence
eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)

# Stress Test MOVE 130
# Formula: (Total Score - MOVE Score) - 1
stress_val = (total_score - s_move) - 1

# ============================================================================
# HEADER DASHBOARD
# ============================================================================

st.title("🛡️ Bond Monitor Strategico")
st.markdown("### Sistema di Regime Detection per Mercato Obbligazionario")
st.markdown("---")

# ============================================================================
# SEZIONE 1: TOTAL SCORE + TARGET
# ============================================================================

col_score, col_target, col_stress = st.columns([1, 2, 1])

with col_score:
    # Determina colore e label score
    if total_score >= 3:
        score_emoji = "🟢"
        score_label = "FORTE"
        score_color = "#00ff00"
    elif total_score >= 1:
        score_emoji = "🟡"
        score_label = "MODERATO"
        score_color = "#ffa500"
    elif total_score >= -1:
        score_emoji = "⚪"
        score_label = "NEUTRALE"
        score_color = "#808080"
    else:
        score_emoji = "🔴"
        score_label = "NEGATIVO"
        score_color = "#ff0000"
    
    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, {score_color}22 0%, {score_color}44 100%);
        border: 2px solid {score_color};
        padding: 20px;
        border-radius: 15px;
        text-align: center;
    ">
        <div style="font-size: 14px; color: #888;">TOTAL SCORE</div>
        <div style="font-size: 48px; font-weight: bold; color: white;">
            {total_score:+d}
        </div>
        <div style="font-size: 12px; color: #888;">/ 6</div>
        <div style="font-size: 18px; margin-top: 10px; color: {score_color};">
            {score_emoji} {score_label}
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_target:
    # Determina target duration
    if total_score >= 3:
        target_duration = "15-20+ anni"
        target_style = "Aggressivo - All-in Duration"
        target_color = "#00ff00"
    elif total_score >= 1:
        target_duration = "7-10 anni"
        target_style = "Moderato - Posizionamento Core"
        target_color = "#ffa500"
    elif total_score >= -1:
        target_duration = "4-6 anni"
        target_style = "Neutrale - Laddering"
        target_color = "#808080"
    else:
        target_duration = "1-3 anni"
        target_style = "Difensivo - Cash/Short Term"
        target_color = "#ff0000"
    
    st.markdown(f"""
    <div style="padding: 20px;">
        <div style="font-size: 18px; color: #888; margin-bottom: 10px;">
            🎯 TARGET DURATION
        </div>
        <div style="font-size: 32px; font-weight: bold; color: {target_color};">
            {target_duration}
        </div>
        <div style="font-size: 14px; color: #aaa; margin-top: 5px;">
            {target_style}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Driver Rendimento
    if s_inf < 0 and s_ry <= 0:
        driver_txt = "🔴 PREMIO INFLAZIONE"
        driver_detail = "Rischio duration elevato"
    elif s_inf >= 0 and s_ry > 0:
        driver_txt = "🟠 PREMIO TERM/DEBITO"
        driver_detail = "Duration penalizzata dal mercato"
    else:
        driver_txt = "🟢 REAL YIELD SANO"
        driver_detail = "Regime equilibrato"
    
    st.markdown(f"**Driver Rendimento:** {driver_txt}")
    st.caption(driver_detail)
    
    # Status Volatilità
    reg_move_txt = "⚠️ REGIME DIPENDENTE DAL MOVE" if s_move < 1 else "✅ REGIME ROBUSTO"
    st.caption(f"Status Volatilità: {reg_move_txt}")

with col_stress:
    # Stress Test
    resilienza = "✅ RESILIENTE" if stress_val > 0 else "⚠️ VULNERABILE"
    stress_color = "#00ff00" if stress_val > 0 else "#ff6b6b"
    
    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, {stress_color}22 0%, {stress_color}44 100%);
        border: 2px solid {stress_color};
        padding: 20px;
        border-radius: 15px;
        text-align: center;
    ">
        <div style="font-size: 12px; color: #888;">STRESS TEST</div>
        <div style="font-size: 14px; color: #aaa; margin-bottom: 5px;">MOVE → 130</div>
        <div style="font-size: 42px; font-weight: bold; color: white;">
            {stress_val:+d}
        </div>
        <div style="font-size: 16px; margin-top: 10px; color: {stress_color};">
            {resilienza}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    st.caption("Score atteso se MOVE sale a 130")

st.markdown("---")

# ============================================================================
# SEZIONE 2: METRICHE PRINCIPALI
# ============================================================================

st.subheader("📊 Metriche di Affidabilità")

met1, met2, met3 = st.columns(3)

with met1:
    st.metric(
        label="Duration Confidence",
        value=f"{dur_conf:.1%}",
        help="Quanto il mercato remunera il rischio duration"
    )
    if dur_conf > 0.7:
        st.caption("🟢 Alta fiducia nella duration")
    elif dur_conf > 0.5:
        st.caption("🟡 Fiducia moderata")
    else:
        st.caption("🔴 Duration non adeguatamente compensata")

with met2:
    st.metric(
        label="Signal Stability",
        value=f"{sig_stab:.1%}",
        help="Coerenza dei segnali di mercato"
    )
    
    if sig_stab < 0.3:
        stab_txt = "⚪ REGIME POCO DEFINITO"
        stab_detail = "Segnali contrastanti - cautela"
    elif sig_stab > 0.7:
        stab_txt = "🟢 REGIME COERENTE"
        stab_detail = "Pricing del rischio uniforme"
    else:
        stab_txt = "🟡 REGIME MODERATAMENTE COERENTE"
        stab_detail = "Segnali in formazione"
    
    st.caption(stab_txt)
    st.caption(stab_detail)

with met3:
    st.metric(
        label="Effective Duration Confidence",
        value=f"{eff_dur_conf:.1%}",
        help="Combinazione di confidence × stability"
    )
    st.caption(f"Affidabilità complessiva del segnale")

st.markdown("---")

# ============================================================================
# SEZIONE 3: BREAKDOWN SCORE DETTAGLIATO
# ============================================================================

st.subheader("🔬 Breakdown Score per Componente")

# Crea DataFrame
breakdown_data = {
    'Fattore': [
        '💹 Inflation (Delta 3M)',
        '💰 Real Yield',
        '📈 MOVE Volatility',
        '〰️ Curve 10Y-2Y',
        '🛡️ TIPS Momentum',
        '📊 IEF Momentum',
        '📉 Equity Panic'
    ],
    'Valore Attuale': [
        f"{d['delta_inf']:.2%}",
        f"{d['ry']:.2f}%",
        f"{move_val:.1f}",
        f"{d['curve']:.2f}%",
        f"{d['tips_var']:.2%}",
        f"{d['ief_mom']:.2%}",
        f"{d['spy_var']:.2%}"
    ],
    'Score': [s_inf, s_ry, s_move, s_curve, s_tips, s_mom, s_equity],
    'Soglie (+1 / -1)': [
        '< -0.3% / > +0.3%',
        '> 1.8% / < 0.5%',
        '< 70 / > 90',
        '< 0.1% / > 1.0%',
        '< -2% / > +2%',
        '> +0.8% / < -1.5%',
        '< -5% / --'
    ]
}

df_breakdown = pd.DataFrame(breakdown_data)

# Funzione per colorare score
def style_score(val):
    if val > 0:
        return 'background-color: rgba(0, 255, 0, 0.2); color: #00ff00; font-weight: bold;'
    elif val < 0:
        return 'background-color: rgba(255, 0, 0, 0.2); color: #ff6b6b; font-weight: bold;'
    else:
        return 'background-color: rgba(128, 128, 128, 0.1); color: #888;'

# Mostra tabella
st.dataframe(
    df_breakdown.style.applymap(style_score, subset=['Score']),
    use_container_width=True,
    hide_index=True
)

# Info fonte PCE
if d['pce_age'] <= 7:
    pce_status = "✅"
elif d['pce_age'] <= 30:
    pce_status = "🟡"
else:
    pce_status = "🔴"

st.caption(f"{pce_status} **Fonte Inflazione:** {d['pce_source']} - Core PCE YoY: {d['pce_current']:.2f}% ({d['pce_month']}) - Aggiornato {d['pce_age']} giorni fa")

if d['pce_age'] > 45:
    st.warning(f"⚠️ Dati PCE vecchi di {d['pce_age']} giorni. Considera di verificare CPI più recente.")

st.markdown("---")

# ============================================================================
# SEZIONE 4: GRAFICO TOTAL SCORE STORICO
# ============================================================================

st.subheader("📈 Evoluzione Total Score (Simulato)")

# NOTA: Questa è una simulazione
# In produzione, dovresti salvare lo score storico in un database/file
date_range = pd.date_range(end=pd.Timestamp.now(), periods=90, freq='D')

# Simula andamento (sostituire con dati reali salvati)
np.random.seed(42)
score_simulated = np.random.randint(-3, 5, size=90)
# Smooth per realismo
score_simulated = pd.Series(score_simulated).rolling(7, center=True).mean().fillna(method='bfill').fillna(method='ffill').values
score_simulated = np.round(score_simulated).astype(int)
score_simulated[-1] = total_score  # Ultimo valore = score attuale

fig_score = go.Figure()

# Area colorata
fig_score.add_trace(go.Scatter(
    x=date_range,
    y=score_simulated,
    mode='lines+markers',
    name='Total Score',
    line=dict(color='#00bfff', width=3),
    fill='tozeroy',
    fillcolor='rgba(0,191,255,0.2)',
    marker=dict(size=4)
))

# Linee soglia
fig_score.add_hline(
    y=3, 
    line_dash="dash", 
    line_color="green",
    annotation_text="Aggressivo (+3)",
    annotation_position="right"
)
fig_score.add_hline(
    y=1, 
    line_dash="dot", 
    line_color="orange",
    annotation_text="Moderato (+1)",
    annotation_position="right"
)
fig_score.add_hline(
    y=-1, 
    line_dash="dot", 
    line_color="orange",
    annotation_text="Difensivo (-1)",
    annotation_position="right"
)
fig_score.add_hline(
    y=0, 
    line_dash="solid", 
    line_color="gray", 
    line_width=1
)

fig_score.update_layout(
    title={
        'text': "Total Score - Identificazione Fase di Regime",
        'x': 0.5,
        'xanchor': 'center'
    },
    template="plotly_dark",
    height=450,
    yaxis=dict(
        range=[-6, 6], 
        title="Score",
        tickmode='linear',
        tick0=-6,
        dtick=1
    ),
    xaxis_title="Data",
    margin=dict(l=20, r=20, t=60, b=20),
    hovermode='x unified',
    showlegend=False
)

st.plotly_chart(fig_score, use_container_width=True)

# Interpretazione trend
score_trend_7d = score_simulated[-1] - score_simulated[-7]
if score_trend_7d > 0:
    trend_emoji = "📈"
    trend_txt = "in salita"
elif score_trend_7d < 0:
    trend_emoji = "📉"
    trend_txt = "in discesa"
else:
    trend_emoji = "➡️"
    trend_txt = "stabile"

st.caption(f"**Trend 7 giorni:** {trend_emoji} {trend_txt} ({score_trend_7d:+.0f} punti) | "
           f"**Media 30gg:** {np.mean(score_simulated[-30:]):.1f} | "
           f"**Volatilità:** {np.std(score_simulated[-30:]):.1f}")

st.info("""
**💡 Come interpretare il grafico:**
- **Score crescente** = Regime sta diventando favorevole alla duration (accumulo graduale)
- **Score alto e stabile** = Regime maturo, movimento probabilmente prezzato
- **Score in discesa** = Deterioramento condizioni, ridurre esposizione
- **Score vicino a soglie** = Attenzione a possibili cambi di regime
""")

st.markdown("---")

# ============================================================================
# SEZIONE 5: GRAFICI REAL YIELD & BREAKEVEN
# ============================================================================

st.subheader("📊 Analisi Storica - Real Yield & Breakeven")

g1, g2 = st.columns(2)

with g1:
    fig_ry = go.Figure()
    fig_ry.add_trace(go.Scatter(
        x=d['ry_hist'].index,
        y=d['ry_hist'].values,
        name="Real Yield 10Y",
        line=dict(color='#00ff00', width=2),
        fill='tozeroy',
        fillcolor='rgba(0,255,0,0.1)'
    ))
    
    # Soglie Real Yield
    fig_ry.add_hline(y=1.8, line_dash="dash", line_color="green", annotation_text="Soglia Alta (1.8%)")
    fig_ry.add_hline(y=0.5, line_dash="dash", line_color="red", annotation_text="Soglia Bassa (0.5%)")
    
    fig_ry.update_layout(
        title="Andamento Real Yield 10Y (6 mesi)",
        template="plotly_dark",
        height=350,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis_title="Real Yield (%)",
        xaxis_title="Data"
    )
    st.plotly_chart(fig_ry, use_container_width=True)

with g2:
    fig_be = go.Figure()
    fig_be.add_trace(go.Scatter(
        x=d['be_hist'].index,
        y=d['be_hist'].values,
        name="Breakeven Inflation",
        line=dict(color='#00bfff', width=2),
        fill='tozeroy',
        fillcolor='rgba(0,191,255,0.1)'
    ))
    
    # Zone Breakeven
    fig_be.add_hrect(y0=1.5, y1=3.0, fillcolor="green", opacity=0.1, annotation_text="Range Normale", annotation_position="left")
    
    fig_be.update_layout(
        title="Aspettative Inflazione - Breakeven 10Y (6 mesi)",
        template="plotly_dark",
        height=350,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis_title="Breakeven (%)",
        xaxis_title="Data"
    )
    st.plotly_chart(fig_be, use_container_width=True)

st.markdown("---")

# ============================================================================
# SEZIONE 6: FILTRI E ANALISI
# ============================================================================

st.subheader("🔍 Stato Filtri e Analisi Avanzata")

f1, f2, f3 = st.columns(3)

with f1:
    st.markdown("#### 🛡️ Capacità Hedge")
    
    # Behr Status
    behr = "🟢 HEDGE ATTIVO" if (s_inf >= 0 and s_ry >= 0 and eff_dur_conf >= 0.55) else "⚠️ HEDGE DEBOLE"
    st.write(f"**Behr Status:** {behr}")
    
    # Decorrelazione Bond/Equity
    if d['ry'] > 0 and d['delta_inf'] <= 0:
        dec_status = "🟢 STRUTTURA FAVOREVOLE"
    elif d['ry'] > 0:
        dec_status = "🟡 STRUTTURA DEBOLE"
    else:
        dec_status = "🔴 STRUTTURA SFAVOREVOLE"
    
    st.write(f"**Dec.Bond Eq:** {dec_status}")

with f2:
    st.markdown("#### 📊 Indicatori Macro")
    
    # Breakeven
    if 1.5 < d['be'] < 3:
        be_status = "✅ OK"
        be_detail = "Range normale"
    elif d['be'] < 1.5:
        be_status = "⚠️ DEFLATION RISK"
        be_detail = "Aspettative basse"
    else:
        be_status = "⚠️ INFLATION RISK"
        be_detail = "Aspettative alte"
    
    st.write(f"**Breakeven:** {d['be']:.2f}% ({be_status})")
    st.caption(be_detail)
    
    # Unemployment
    if d['unemp'] < 4.5:
        unemp_status = "✅ Normale"
    else:
        unemp_status = "🚨 ALERT"
    
    st.write(f"**Unemployment:** {d['unemp']:.1f}% ({unemp_status})")

with f3:
    st.markdown("#### ⚡ Filtri Dinamici")
    
    # Filtro Equity
    equity_filter = "🚨 PANICO EQUITY" if s_equity == 1 else "✅ Equity Stabile"
    st.write(f"**Equity:** {equity_filter}")
    if s_equity == 1:
        st.caption("Favorevole per rifugio in bond")
    
    # Convessità
    convex = "Adeguata" if d['ry'] > 1.8 else "Ridotta"
    st.write(f"**Convessità:** {convex}")
    
    # Volatilità MOVE
    if move_val > 110:
        move_status = "🔴 Alta volatilità"
    elif move_val > 80:
        move_status = "🟡 Media volatilità"
    else:
        move_status = "✅ Bassa volatilità"
    
    st.write(f"**MOVE:** {move_status}")

st.markdown("---")

# ============================================================================
# SEZIONE 7: ANALISI DI REGIME
# ============================================================================

st.subheader("🎯 Analisi di Regime Attuale")

# Determina regime
if dur_conf > 0.6 and sig_stab < 0.4:
    regime_type = "🚀 FASE INIZIALE"
    regime_color = "#00ff00"
    regime_desc = """
    **Confidence Alta / Stabilità Bassa**
    
    Questa è la configurazione ideale per **accumulo graduale**:
    - Il mercato offre buona remunerazione (alta confidence)
    - Ma non c'è ancora consenso uniforme (bassa stabilità)
    - Opportunità: Posizionarsi prima che diventi mainstream
    - Rischio: Segnale potrebbe ancora invertirsi
    
    **Azione suggerita:** Iniziare accumulo con ~30-40% target allocation
    """
elif dur_conf > 0.6 and sig_stab > 0.7:
    regime_type = "📢 FASE MATURA"
    regime_color = "#ffa500"
    regime_desc = """
    **Tutto Positivo e Allineato**
    
    Il movimento è probabilmente già prezzato:
    - Alta confidence + Alta stabilità = Consenso market
    - Tutti i fattori allineati positivamente
    - Opportunità: Mantenere posizioni esistenti
    - Rischio: Upside limitato, già incorporato nei prezzi
    
    **Azione suggerita:** Hold posizioni, evitare di aumentare ora
    """
elif dur_conf < 0.4:
    regime_type = "🚨 REGIME NEGATIVO"
    regime_color = "#ff0000"
    regime_desc = """
    **Duration Non Adeguatamente Compensata**
    
    Il mercato NON paga abbastanza per il rischio duration:
    - Bassa confidence = Real yield insufficiente
    - Inflazione e/o term premium dominanti
    - Opportunità: Minimali, meglio attendere
    - Rischio: Perdite se tassi salgono ulteriormente
    
    **Azione suggerita:** Posizione difensiva (1-3 anni), liquidità
    """
else:
    regime_type = "⚖️ REGIME DI DIVERGENZA"
    regime_color = "#808080"
    regime_desc = """
    **Segnale Incerto o Cambio Aspettative**
    
    Situazione mista con fattori contrastanti:
    - Confidence e Stabilità in range moderato
    - Alcuni fattori positivi, altri negativi
    - Opportunità: Attendere maggiore chiarezza
    - Rischio: Difficile prevedere direzione
    
    **Azione suggerita:** Neutrale, duration intermedia (4-6 anni)
    """

st.markdown(f"""
<div style="
    background: linear-gradient(135deg, {regime_color}22 0%, {regime_color}44 100%);
    border-left: 4px solid {regime_color};
    padding: 20px;
    border-radius: 10px;
">
    <h3 style="color: {regime_color}; margin-top: 0;">{regime_type}</h3>
    <div style="color: #ccc; line-height: 1.8;">
        {regime_desc}
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("---")

# ============================================================================
# SEZIONE 8: MANUALE OPERATIVO (EXPANDER)
# ============================================================================

with st.expander("📖 Manuale Operativo e Filosofia del Monitor"):
    st.markdown("""
    ## 🎯 Scopo del Monitor
    
    Questo sistema fornisce una lettura del **regime macroeconomico** per determinare se:
    1. La duration è **strutturalmente favorita** dal contesto
    2. I bond possono **decorrellaare dall'equity** (capacità di hedge)
    3. Il mercato sta **cambiando regime** o ha già **prezzato** il movimento
    
    ---
    
    ## 🚦 Pilastri di Lettura
    
    ### 1️⃣ Duration Confidence (Remunerazione)
    Misura se i tassi reali compensano adeguatamente il rischio duration:
    - **> 70%**: Il mercato paga bene, rischio ben remunerato
    - **40-70%**: Compensazione parziale, prudenza
    - **< 40%**: Rischio non pagato, evitare duration lunga
    
    ### 2️⃣ Signal Stability (Coerenza)
    Indica quanto i vari fattori sono allineati:
    - **> 70%**: Regime coerente, segnale forte
    - **30-70%**: Segnale in formazione o misto
    - **< 30%**: Segnali contrastanti, incertezza alta
    
    **💡 Opportunità:** Le migliori configurazioni hanno **Confidence alta + Stabilità moderata**
    (fase di transizione, mercato non ancora allineato completamente)
    
    ### 3️⃣ Effective Duration Confidence (Sintesi)
    Combina i due precedenti: è la metrica più importante per decisioni tattiche.
    
    ---
    
    ## 🧩 Configurazioni di Regime
    
    | Regime | Confidence | Stability | Interpretazione | Azione |
    |--------|-----------|-----------|-----------------|--------|
    | **Iniziale** | Alta | Bassa | Mercato diffidente, opportunità | Accumulo graduale |
    | **Matura** | Alta | Alta | Consenso uniforme, prezzato | Mantenere, non aumentare |
    | **Negativa** | Bassa | Alta/Bassa | Inflazione dominante | Difensivo (cash/short) |
    | **Divergenza** | Media | Media | Incertezza, cambio aspettative | Neutrale, attendere |
    
    ---
    
    ## 📊 Come Usare il Breakdown Score
    
    Ogni fattore può dare **+1** (favorevole), **0** (neutrale), **-1** (sfavorevole):
    
    - **Inflation**: Delta 3 mesi. Negativo = inflazione rallenta ✅
    - **Real Yield**: Sopra 1.8% = buona remunerazione ✅
    - **MOVE**: Sotto 70 = bassa volatilità ✅
    - **Curve**: Sotto 0.1% = piatta, sopra 1% = ripida ❌
    - **TIPS**: Performance TIPS vs Treasury. Positivo = mercato cerca protezione
    - **Momentum**: IEF performance. Positivo = bond stanno salendo
    - **Equity**: Sotto -5% = panico, favorevole per bond rifugio
    
    **Total Score Range:**
    - **+6 a +3**: Fortemente pro-duration
    - **+2 a +1**: Moderatamente favorevole
    - **0 a -1**: Neutrale/Cauto
    - **-2 a -6**: Sfavorevole, difensivo
    
    ---
    
    ## ⚠️ Stress Test MOVE 130
    
    Simula cosa succederebbe se la volatilità bond (MOVE Index) esplodesse a 130:
    - **Positivo**: Il regime reggerebbe anche con stress elevato
    - **Negativo**: Il segnale positivo dipende dalla calma, vulnerabile a shock
    
    **Uso:** Se Stress Test < 0 ma Total Score > 0, sei in "regime dipendente dal MOVE"
    = La tua posizione è buona solo finché la volatilità resta bassa
    
    ---
    
    ## 🎓 Filosofia di Approccio
    
    Questo monitor **non è un timing tool meccanico**, ma un **framework decisionale**:
    
    ✅ **Usa per:**
    - Capire se il contesto macro favorisce duration
    - Identificare fasi di transizione (opportunità)
    - Valutare robustezza del posizionamento attuale
    
    ❌ **Non usare per:**
    - Trading giornaliero
    - Decisioni 100% automatiche (usa giudizio)
    - Ignorare altri fattori (geopolitica, Fed guidance, etc.)
    
    ---
    
    ## 🔧 Limitazioni e Avvertenze
    
    1. **Dati ritardati**: PCE può avere 30-45 giorni delay (usiamo Cleveland Nowcast per mitigare)
    2. **Thresholds statici**: Le soglie (1.8% Real Yield, etc.) potrebbero cambiare se il regime macro si trasforma strutturalmente
    3. **Black swans**: Eventi imprevedibili (crisi bancarie, guerre, pandemie) invalidano qualsiasi modello
    4. **Correlazioni instabili**: La decorrelazione bond/equity non è garantita (vedi 2022)
    
    **Conclusione:** Usa il monitor come **uno** degli strumenti decisionali, non l'unico.
    """)

# ============================================================================
# FOOTER
# ============================================================================

st.markdown("---")
st.caption("🛡️ Bond Monitor Strategico v2.0 - Sistema di Regime Detection")
st.caption(f"📅 Ultimo aggiornamento dati: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
st.caption("⚠️ Questo tool è a scopo informativo. Non costituisce consulenza finanziaria.")
