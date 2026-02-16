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
    </style>
    """, unsafe_allow_html=True)

# ============================================================================
# FUNZIONI DI FETCH DATI
# ============================================================================

def get_cleveland_nowcast():
    """
    Scrapa Core PCE Year-over-Year da Cleveland Fed Inflation Nowcasting
    """
    url = "https://www.clevelandfed.org/indicators-and-data/inflation-nowcasting"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        tables = soup.find_all('table')
        
        if len(tables) < 2:
            return None
        
        yoy_table = tables[1]
        rows = yoy_table.find_all('tr')
        
        if len(rows) < 2:
            return None
        
        latest_row = rows[1]
        cells = latest_row.find_all('td')
        
        if len(cells) < 6:
            return None
        
        month = cells[0].text.strip()
        core_pce_yoy_text = cells[4].text.strip()
        
        if not core_pce_yoy_text or core_pce_yoy_text == '':
            latest_row = rows[2]
            cells = latest_row.find_all('td')
            month = cells[0].text.strip()
            core_pce_yoy_text = cells[4].text.strip()
        
        core_pce_yoy = float(core_pce_yoy_text)
        update_date = cells[5].text.strip()
        
        return {
            'value': core_pce_yoy,
            'month': month,
            'update_date': update_date
        }
        
    except Exception as e:
        return None


def get_var(ticker, days=30):
    """
    Calcola variazione percentuale ticker negli ultimi N giorni
    """
    try:
        h = yf.Ticker(ticker).history(period="60d")
        
        if h.empty or len(h) < days:
            return 0
        
        return (h['Close'].iloc[-1] / h['Close'].iloc[-days]) - 1
    
    except Exception as e:
        return 0


def get_move_data():
    """
    Scarica MOVE Index e calcola media 3 mesi
    """
    try:
        move_ticker = yf.Ticker("^MOVE")
        move_history = move_ticker.history(period="6mo")['Close']
        
        if move_history.empty:
            st.warning("⚠️ MOVE Index non disponibile, uso valore stimato")
            return 70.0, pd.Series([70]*90), 70.0
        
        # Valore attuale
        move_current = move_history.iloc[-1]
        
        # Media mobile 3 mesi (circa 63 giorni lavorativi)
        move_3m_avg = move_history.rolling(window=63, min_periods=30).mean().iloc[-1]
        
        return move_current, move_history, move_3m_avg
        
    except Exception as e:
        st.error(f"❌ Errore scaricamento MOVE: {e}")
        return 70.0, pd.Series([70]*90), 70.0


@st.cache_data(ttl=3600)
def fetch_data():
    """
    Raccoglie tutti i dati necessari da FRED, Yahoo Finance, Cleveland Fed
    """
    try:
        # DATI FRED
        ry_series = fred.get_series('DFII10')
        be_series = fred.get_series('T10YIE')
        unemp = fred.get_series('UNRATE').iloc[-1]
        dgs10 = fred.get_series('DGS10').iloc[-1]
        dgs2 = fred.get_series('DGS2').iloc[-1]
        
        # CORE PCE (Cleveland + FRED Fallback)
        cleveland = get_cleveland_nowcast()
        if cleveland:
            pce_current = cleveland['value']
            pce_month = cleveland['month']
            pce_source = 'Cleveland Nowcast'
            try:
                month_num, day_num = cleveland['update_date'].split('/')
                update_datetime = datetime(2026, int(month_num), int(day_num))
                pce_age = (datetime.now() - update_datetime).days
            except:
                pce_age = 3
        else:
            pce_idx = fred.get_series('PCEPILFE')
            pce_current = ((pce_idx.iloc[-1] / pce_idx.iloc[-13]) - 1) * 100
            pce_month = pce_idx.index[-1].strftime('%B %Y')
            pce_source = 'FRED (Ufficiale)'
            pce_age = (pd.Timestamp.now() - pce_idx.index[-1]).days
        
        # Delta Inflation
        pce_idx = fred.get_series('PCEPILFE')
        pce_3m_ago = ((pce_idx.iloc[-4] / pce_idx.iloc[-16]) - 1) * 100
        delta_inf = pce_current - pce_3m_ago
        
        # MOVE INDEX
        move_current, move_history, move_3m_avg = get_move_data()
        
        # YAHOO FINANCE
        ief_mom = get_var("IEF", days=30)
        spy_var = get_var("SPY", days=30)
        tips_var = get_var("TIP", days=30)
        
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
            "tips_var": tips_var,
            "move_current": move_current,
            "move_3m_avg": move_3m_avg,
            "move_history": move_history
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
    st.stop()

# ============================================================================
# CALCOLO SCORE COMPONENTI
# ============================================================================

s_inf = 1 if d['delta_inf'] < -0.003 else (-1 if d['delta_inf'] > 0.003 else 0)

# MOVE Score usa MEDIA 3 MESI (come Excel)
s_move = -1 if d['move_3m_avg'] > 90 else (1 if d['move_3m_avg'] < 70 else 0)

s_curve = 1 if d['curve'] < 0.1 else (-1 if d['curve'] > 1 else 0)
s_ry = 1 if d['ry'] > 1.8 else (-1 if d['ry'] < 0.5 else 0)
s_tips = 1 if d['tips_var'] < -0.02 else (-1 if d['tips_var'] > 0.02 else 0)
s_mom = -1 if d['ief_mom'] < -0.015 else (1 if d['ief_mom'] > 0.008 else 0)
s_equity = 1 if d['spy_var'] < -0.05 else 0

total_score = s_inf + s_move + s_curve + s_ry + s_tips + s_mom + s_equity

# ============================================================================
# CALCOLO RATIOS
# ============================================================================

dur_conf = ((total_score + 6) / 12) * (1 + s_ry * 0.15)
sig_stab = abs(total_score) / 6
eff_dur_conf = dur_conf * (0.5 + sig_stab * 0.5)

# STRESS TEST: Formula Excel = Total Score - MOVE Score - 1
stress_val = total_score - s_move - 1

# ============================================================================
# HEADER DASHBOARD
# ============================================================================

st.title("🛡️ Bond Monitor Strategico")
st.markdown("### Sistema di Regime Detection per Mercato Obbligazionario")
st.markdown("---")

# ============================================================================
# SEZIONE 1: TOTAL SCORE + TARGET + STRESS TEST
# ============================================================================

col_score, col_target, col_stress = st.columns([1, 2, 1])

with col_score:
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
    
    reg_move_txt = "⚠️ REGIME DIPENDENTE DAL MOVE" if s_move < 1 else "✅ REGIME ROBUSTO"
    st.caption(f"Status Volatilità: {reg_move_txt}")
    st.caption(f"MOVE 3M Avg: {d['move_3m_avg']:.1f} | Current: {d['move_current']:.1f}")

with col_stress:
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
        stab_detail = "Segnali contrastanti"
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

breakdown_data = {
    'Fattore': [
        '💹 Inflation (Delta 3M)',
        '💰 Real Yield',
        '📈 MOVE 3M Avg',
        '〰️ Curve 10Y-2Y',
        '🛡️ TIPS Momentum',
        '📊 IEF Momentum',
        '📉 Equity Panic'
    ],
    'Valore Attuale': [
        f"{d['delta_inf']:.2%}",
        f"{d['ry']:.2f}%",
        f"{d['move_3m_avg']:.1f}",
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

def style_score(val):
    if val > 0:
        return 'background-color: rgba(0, 255, 0, 0.2); color: #00ff00; font-weight: bold;'
    elif val < 0:
        return 'background-color: rgba(255, 0, 0, 0.2); color: #ff6b6b; font-weight: bold;'
    else:
        return 'background-color: rgba(128, 128, 128, 0.1); color: #888;'

st.dataframe(
    df_breakdown.style.applymap(style_score, subset=['Score']),
    use_container_width=True,
    hide_index=True
)

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
# SEZIONE 4: GRAFICI TOTAL SCORE + MOVE STORICO
# ============================================================================

st.subheader("📈 Evoluzione Indicatori")

g1, g2 = st.columns(2)

with g1:
    st.markdown("#### Total Score (Simulato - 90 giorni)")
    
    # Simulazione (sostituire con dati reali in produzione)
    date_range = pd.date_range(end=pd.Timestamp.now(), periods=90, freq='D')
    np.random.seed(42)
    score_simulated = np.random.randint(-3, 5, size=90)
    score_simulated = pd.Series(score_simulated).rolling(7, center=True).mean().fillna(method='bfill').fillna(method='ffill').values
    score_simulated = np.round(score_simulated).astype(int)
    score_simulated[-1] = total_score
    
    fig_score = go.Figure()
    
    fig_score.add_trace(go.Scatter(
        x=date_range,
        y=score_simulated,
        mode='lines',
        name='Total Score',
        line=dict(color='#00bfff', width=2)
    ))
    
    fig_score.add_hline(y=3, line_dash="dash", line_color="green", line_width=1)
    fig_score.add_hline(y=1, line_dash="dot", line_color="orange", line_width=1)
    fig_score.add_hline(y=-1, line_dash="dot", line_color="orange", line_width=1)
    fig_score.add_hline(y=0, line_dash="solid", line_color="gray", line_width=1)
    
    fig_score.update_layout(
        template="plotly_dark",
        height=350,
        yaxis=dict(range=[-6, 6], title="Score"),
        xaxis_title="Data",
        margin=dict(l=20, r=20, t=20, b=20),
        showlegend=False
    )
    
    st.plotly_chart(fig_score, use_container_width=True)
    
    score_trend_7d = score_simulated[-1] - score_simulated[-7]
    trend_txt = "📈 salita" if score_trend_7d > 0 else ("📉 discesa" if score_trend_7d < 0 else "➡️ stabile")
    st.caption(f"Trend 7gg: {trend_txt} ({score_trend_7d:+.0f}) | Media 30gg: {np.mean(score_simulated[-30:]):.1f}")

with g2:
    st.markdown("#### MOVE Index Volatility (6 mesi)")
    
    fig_move = go.Figure()
    
    fig_move.add_trace(go.Scatter(
        x=d['move_history'].index,
        y=d['move_history'].values,
        mode='lines',
        name='MOVE Index',
        line=dict(color='#ff6b6b', width=2)
    ))
    
    # Media mobile 3 mesi
    move_ma = d['move_history'].rolling(window=63, min_periods=30).mean()
    fig_move.add_trace(go.Scatter(
        x=move_ma.index,
        y=move_ma.values,
        mode='lines',
        name='3M Average',
        line=dict(color='#ffa500', width=2, dash='dash')
    ))
    
    fig_move.add_hline(y=90, line_dash="dot", line_color="red", line_width=1)
    fig_move.add_hline(y=70, line_dash="dot", line_color="green", line_width=1)
    
    fig_move.update_layout(
        template="plotly_dark",
        height=350,
        yaxis_title="MOVE Index",
        xaxis_title="Data",
        margin=dict(l=20, r=20, t=20, b=20),
        showlegend=True,
        legend=dict(x=0.02, y=0.98)
    )
    
    st.plotly_chart(fig_move, use_container_width=True)
    
    st.caption(f"Current: {d['move_current']:.1f} | 3M Avg: {d['move_3m_avg']:.1f}")

st.markdown("---")

# ============================================================================
# SEZIONE 5: GRAFICI REAL YIELD & BREAKEVEN (PULITI)
# ============================================================================

st.subheader("📊 Analisi Storica - Real Yield & Breakeven")

g3, g4 = st.columns(2)

with g3:
    fig_ry = go.Figure()
    
    # Solo linea, nessun fill
    fig_ry.add_trace(go.Scatter(
        x=d['ry_hist'].index,
        y=d['ry_hist'].values,
        mode='lines',
        name="Real Yield 10Y",
        line=dict(color='#00ff00', width=2)
    ))
    
    fig_ry.add_hline(y=1.8, line_dash="dash", line_color="green", line_width=1)
    fig_ry.add_hline(y=0.5, line_dash="dash", line_color="red", line_width=1)
    
    fig_ry.update_layout(
        title="Andamento Real Yield 10Y (6 mesi)",
        template="plotly_dark",
        height=350,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis_title="Real Yield (%)",
        xaxis_title="Data",
        showlegend=False
    )
    st.plotly_chart(fig_ry, use_container_width=True)

with g4:
    fig_be = go.Figure()
    
    # Solo linea, nessun fill
    fig_be.add_trace(go.Scatter(
        x=d['be_hist'].index,
        y=d['be_hist'].values,
        mode='lines',
        name="Breakeven Inflation",
        line=dict(color='#00bfff', width=2)
    ))
    
    fig_be.add_hline(y=3.0, line_dash="dash", line_color="red", line_width=1)
    fig_be.add_hline(y=1.5, line_dash="dash", line_color="green", line_width=1)
    
    fig_be.update_layout(
        title="Aspettative Inflazione - Breakeven 10Y (6 mesi)",
        template="plotly_dark",
        height=350,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis_title="Breakeven (%)",
        xaxis_title="Data",
        showlegend=False
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
    
    behr = "🟢 HEDGE ATTIVO" if (s_inf >= 0 and s_ry >= 0 and eff_dur_conf >= 0.55) else "⚠️ HEDGE DEBOLE"
    st.write(f"**Behr Status:** {behr}")
    
    if d['ry'] > 0 and d['delta_inf'] <= 0:
        dec_status = "🟢 STRUTTURA FAVOREVOLE"
    elif d['ry'] > 0:
        dec_status = "🟡 STRUTTURA DEBOLE"
    else:
        dec_status = "🔴 STRUTTURA SFAVOREVOLE"
    
    st.write(f"**Dec.Bond Eq:** {dec_status}")

with f2:
    st.markdown("#### 📊 Indicatori Macro")
    
    if 1.5 < d['be'] < 3:
        be_status = "✅ OK"
    elif d['be'] < 1.5:
        be_status = "⚠️ DEFLATION RISK"
    else:
        be_status = "⚠️ INFLATION RISK"
    
    st.write(f"**Breakeven:** {d['be']:.2f}% ({be_status})")
    
    unemp_status = "✅ Normale" if d['unemp'] < 4.5 else "🚨 ALERT"
    st.write(f"**Unemployment:** {d['unemp']:.1f}% ({unemp_status})")

with f3:
    st.markdown("#### ⚡ Filtri Dinamici")
    
    equity_filter = "🚨 PANICO EQUITY" if s_equity == 1 else "✅ Equity Stabile"
    st.write(f"**Equity:** {equity_filter}")
    
    convex = "Adeguata" if d['ry'] > 1.8 else "Ridotta"
    st.write(f"**Convessità:** {convex}")
    
    if d['move_3m_avg'] > 110:
        move_status = "🔴 Alta volatilità"
    elif d['move_3m_avg'] > 80:
        move_status = "🟡 Media volatilità"
    else:
        move_status = "✅ Bassa volatilità"
    
    st.write(f"**MOVE 3M:** {move_status}")

st.markdown("---")

# ============================================================================
# SEZIONE 7: ANALISI DI REGIME
# ============================================================================

st.subheader("🎯 Analisi di Regime Attuale")

if dur_conf > 0.6 and sig_stab < 0.4:
    regime_type = "🚀 FASE INIZIALE"
    regime_color = "#00ff00"
    regime_desc = """
    **Confidence Alta / Stabilità Bassa**
    
    Configurazione ideale per **accumulo graduale**:
    - Il mercato offre buona remunerazione
    - Non c'è ancora consenso uniforme
    - Opportunità: Posizionarsi prima che diventi mainstream
    
    **Azione suggerita:** Iniziare accumulo con 30-40% target allocation
    """
elif dur_conf > 0.6 and sig_stab > 0.7:
    regime_type = "📢 FASE MATURA"
    regime_color = "#ffa500"
    regime_desc = """
    **Tutto Positivo e Allineato**
    
    Il movimento è probabilmente già prezzato:
    - Alta confidence + Alta stabilità = Consenso
    - Tutti i fattori allineati positivamente
    - Upside limitato, già incorporato nei prezzi
    
    **Azione suggerita:** Hold posizioni, evitare di aumentare ora
    """
elif dur_conf < 0.4:
    regime_type = "🚨 REGIME NEGATIVO"
    regime_color = "#ff0000"
    regime_desc = """
    **Duration Non Adeguatamente Compensata**
    
    Il mercato NON paga abbastanza per il rischio:
    - Bassa confidence = Real yield insufficiente
    - Inflazione e/o term premium dominanti
    
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
# SEZIONE 8: MANUALE OPERATIVO
# ============================================================================

with st.expander("📖 Manuale Operativo e Filosofia del Monitor"):
    st.markdown("""
    ## 🎯 Scopo del Monitor
    
    Sistema di **regime detection** per determinare se:
    1. La duration è strutturalmente favorita
    2. I bond possono decorrellaare dall'equity
    3. Il mercato sta cambiando regime o ha già prezzato il movimento
    
    ## 🚦 Pilastri di Lettura
    
    ### Duration Confidence (Remunerazione)
    - **> 70%**: Rischio ben remunerato
    - **40-70%**: Compensazione parziale
    - **< 40%**: Rischio non pagato
    
    ### Signal Stability (Coerenza)
    - **> 70%**: Regime coerente
    - **30-70%**: Segnale in formazione
    - **< 30%**: Segnali contrastanti
    
    **💡 Opportunità:** Confidence alta + Stabilità moderata = fase di transizione
    
    ## 📊 Breakdown Score
    
    - **Inflation**: Delta 3 mesi. Negativo = inflazione rallenta ✅
    - **Real Yield**: Sopra 1.8% = buona remunerazione ✅
    - **MOVE 3M Avg**: Sotto 70 = bassa volatilità ✅
    - **Curve**: Sotto 0.1% = piatta
    - **TIPS/Momentum/Equity**: Performance relative
    
    ## ⚠️ Stress Test MOVE 130
    
    Formula: **Total Score - MOVE Score - 1**
    
    Simula scenario con volatilità elevata:
    - **Positivo**: Regime regge anche con stress
    - **Negativo**: Segnale dipende dalla calma
    
    ## 🎓 Filosofia
    
    Questo NON è un timing tool meccanico, ma un **framework decisionale**.
    
    ✅ Usa per: Capire contesto macro, identificare transizioni, valutare robustezza
    
    ❌ Non usare per: Trading giornaliero, decisioni 100% automatiche
    """)

# ============================================================================
# FOOTER
# ============================================================================

st.markdown("---")
st.caption("🛡️ Bond Monitor Strategico v3.0 - MOVE 3M Avg | Grafici Ottimizzati")
st.caption(f"📅 Ultimo aggiornamento: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
st.caption("⚠️ Questo tool è a scopo informativo. Non costituisce consulenza finanziaria.")
