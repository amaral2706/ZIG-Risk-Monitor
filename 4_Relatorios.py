from datetime import date
from pathlib import Path
import logging
import sys
from typing import Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ZIG risk palette: green = low, orange = medium, red = high/critical
ZIG_RISK_LOW = "#28a745"
ZIG_RISK_MED = "#F59E0B"
ZIG_RISK_HIGH = "#ef4444"
ZIG_RISK_CRITICAL = "#dc2626"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from modules.auth import ensure_authenticated
from modules.date_session import CALENDAR_MAX, CALENDAR_MIN, sync_global_date_inputs
from modules.data_loader import (
    _parse_timestamp_column,
    date_column_for_period_filter,
    filter_by_brazil_calendar_dates,
    load_processed_data,
)
from modules.month_filter import labels_and_ranges_for_months, year_months_br_from_utc_series
from modules.table_columns import DATA_CRIACAO_COL, add_placeholder_data_criacao_column
from modules.ui import apply_enterprise_theme, aplicar_estilo_global, apply_plotly_readability, format_brl

# Evita loop de automargin Plotly no iframe Streamlit
_PLOTLY_CFG = {"responsive": False, "displaylogo": False}


def _st_plotly(fig, *, key: str | None = None) -> None:
    """Largura do bloco = 100% do container; altura vem do layout Plotly (evita gráficos no canto)."""
    kw: dict = {"use_container_width": True, "config": _PLOTLY_CFG}
    if key:
        kw["key"] = key
    try:
        st.plotly_chart(fig, **kw)
    except TypeError:
        kw.pop("config", None)
        st.plotly_chart(fig, **kw)


def _resolve_period_column(df: pd.DataFrame, date_col: str | None) -> tuple[pd.DataFrame, str]:
    """Garante coluna de período para agregações; evita NameError e falha explícita se impossível."""
    if "periodo" in df.columns:
        return df, "periodo"
    if "mes" in df.columns:
        return df, "mes"
    work = df
    if "PERIODO_MES" not in work.columns and date_col and date_col in work.columns:
        _dc = work[date_col]
        if not pd.api.types.is_datetime64_any_dtype(_dc):
            _dc = _parse_timestamp_column(_dc)
        if _dc.dt.tz is None:
            _dc = _dc.dt.tz_localize("UTC", ambiguous=False, nonexistent="shift_forward")
        _ln = _dc.dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
        work = work.copy()
        work["PERIODO_MES"] = _ln.dt.to_period("M").astype(str)
    if "PERIODO_MES" in work.columns:
        return work, "PERIODO_MES"
    st.error(
        "Coluna de período não encontrada. Inclua `periodo` ou `mes` na base, "
        "ou uma coluna de datas válida para derivar o mês."
    )
    st.stop()
    return work, "PERIODO_MES"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

st.set_page_config(page_title="Relatórios de Risco | ZIG Risk Monitor", page_icon=":page_facing_up:", layout="wide")
if not ensure_authenticated():
    st.stop()

apply_enterprise_theme()
aplicar_estilo_global()
st.markdown(
    """
    <style>
    .rel-title { font-size: 32px; font-weight: 700; color: #0047BB; margin-bottom: 0.75rem; }
    .rel-section-title { color: #0047BB; font-weight: 600; font-size: 18px; margin: 1rem 0 0.5rem 0; }
    .rel-alert-box { background: #fef3c7; border-left: 4px solid #F59E0B; padding: 12px 16px; border-radius: 8px; margin: 8px 0; }
    .rel-alert-title { font-weight: 700; color: #92400e; margin-bottom: 4px; }
    </style>
    """,
    unsafe_allow_html=True,
)

log.info("Relatórios: carregando dados processados...")
df = load_processed_data()
log.info("Relatórios: carregados %d registros", len(df))

if df.empty:
    st.info("Nenhum dado processado disponível. Execute a pipeline no Dashboard ou faça upload na página Admin Upload.")
    st.stop()

date_col = date_column_for_period_filter(df)
if date_col not in df.columns:
    date_col = None
else:
    if not pd.api.types.is_datetime64_any_dtype(df[date_col]):
        df[date_col] = _parse_timestamp_column(df[date_col])
if "PERIODO_MES" not in df.columns and date_col in df.columns and df[date_col].notna().any():
    _dc = df[date_col]
    if not pd.api.types.is_datetime64_any_dtype(_dc):
        _dc = _parse_timestamp_column(_dc)
    if _dc.dt.tz is None:
        _dc = _dc.dt.tz_localize("UTC", ambiguous=False, nonexistent="shift_forward")
    _ln = _dc.dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    df = df.copy()
    df["PERIODO_MES"] = _ln.dt.to_period("M").astype(str)

if date_col and df[date_col].notna().any():
    col_dt = df[date_col].dropna()
    if col_dt.empty:
        data_min, data_max = date.today().replace(day=1), date.today()
    else:
        col_dates = col_dt.dt.tz_convert("UTC").dt.date if col_dt.dt.tz is not None else col_dt.dt.date
        col_dates = col_dates.dropna()
        col_dates = col_dates[col_dates.apply(lambda x: isinstance(x, date))]
        data_min = col_dates.min() if not col_dates.empty else date.today().replace(day=1)
        data_max = col_dates.max() if not col_dates.empty else date.today()
else:
    data_min, data_max = date.today().replace(day=1), date.today()

sync_global_date_inputs(data_min, data_max)

yms_rel = year_months_br_from_utc_series(df[date_col].dropna()) if date_col else []
if not yms_rel:
    y_min, m_min = data_min.year, data_min.month
    y_max, m_max = data_max.year, data_max.month
    yms_rel = []
    cy, cm = y_min, m_min
    while (cy, cm) <= (y_max, m_max):
        yms_rel.append((cy, cm))
        if (cy, cm) == (y_max, m_max):
            break
        cm += 1
        if cm > 12:
            cm, cy = 1, cy + 1
    yms_rel = sorted(set(yms_rel), reverse=True)
mes_labels_rel, mes_ranges_rel = labels_and_ranges_for_months(yms_rel, CALENDAR_MIN, CALENDAR_MAX)

st.markdown('<div class="rel-title">Relatórios de Risco</div>', unsafe_allow_html=True)
c_mes, c_inicio, c_fim = st.columns([1, 1, 1])
with c_mes:
    mes_idx_rel = st.selectbox(
        "Ou selecionar mês",
        range(len(mes_labels_rel)),
        format_func=lambda i: mes_labels_rel[i],
        key="rel_mes_idx_v2",
    )

periodo_mes: Optional[Tuple[date, date]] = None
if mes_idx_rel > 0 and mes_idx_rel - 1 < len(mes_ranges_rel):
    d1, d2 = mes_ranges_rel[mes_idx_rel - 1]
    periodo_mes = (d1, d2)
    st.session_state.data_inicial = d1
    st.session_state.data_final = d2

with c_inicio:
    data_inicial = st.date_input(
        "Data inicial",
        min_value=CALENDAR_MIN,
        max_value=CALENDAR_MAX,
        format="DD/MM/YYYY",
        key="data_inicial",
    )
with c_fim:
    data_final = st.date_input(
        "Data final",
        min_value=CALENDAR_MIN,
        max_value=CALENDAR_MAX,
        format="DD/MM/YYYY",
        key="data_final",
    )

if periodo_mes is not None:
    periodo_inicio, periodo_fim = periodo_mes
else:
    periodo_inicio, periodo_fim = data_inicial, data_final
if periodo_inicio > periodo_fim:
    periodo_inicio, periodo_fim = periodo_fim, periodo_inicio

data_inicial = max(CALENDAR_MIN, min(periodo_inicio, CALENDAR_MAX))
data_final = max(CALENDAR_MIN, min(periodo_fim, CALENDAR_MAX))
if data_inicial > data_final:
    data_inicial, data_final = data_final, data_inicial

if date_col:
    df = filter_by_brazil_calendar_dates(df, date_col, data_inicial, data_final)
else:
    df = df.copy()

if df.empty:
    st.warning("Nenhum registro no período selecionado. Ajuste as datas ou escolha outro intervalo.")
    st.stop()

df, period_col = _resolve_period_column(df, date_col)

# ========== 1) KPI CARDS (incl. fraud rate) ==========
valor_total = float(df["VALOR_RETIRADA"].sum())
total_transacoes = int(df["ID_SAQUE"].nunique()) if "ID_SAQUE" in df.columns else len(df)
if "SUSPEITA" in df.columns and "ID_SAQUE" in df.columns:
    total_suspeitas = int(df.groupby("ID_SAQUE", dropna=False)["SUSPEITA"].max().sum())
elif "SUSPEITA" in df.columns:
    total_suspeitas = int(pd.to_numeric(df["SUSPEITA"], errors="coerce").fillna(0).eq(1).sum())
else:
    total_suspeitas = 0
qtde_saque = total_transacoes
valor_medio_saque = float(df["VALOR_RETIRADA"].mean()) if total_transacoes else 0.0
fraud_rate = (total_suspeitas / total_transacoes * 100.0) if total_transacoes else 0.0

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    st.metric("Volume financeiro total", format_brl(valor_total))
with c2:
    st.metric("Total de transações", f"{total_transacoes:,}".replace(",", "."))
with c3:
    st.metric("Transações suspeitas", f"{total_suspeitas:,}".replace(",", "."))
with c4:
    st.metric("Total de saques", f"{qtde_saque:,}".replace(",", "."))
with c5:
    st.metric("Valor médio de saque", format_brl(valor_medio_saque))
with c6:
    st.metric("Taxa de fraude (%)", f"{fraud_rate:.1f}%")

# Agregação mensal (ordenada cronologicamente); period_col sempre definido após _resolve_period_column
if "ID_SAQUE" in df.columns:
    agg_mensal = df.groupby(period_col, as_index=False).agg(
        VALOR_TOTAL=("VALOR_RETIRADA", "sum"),
        QT_TRANSACOES=("ID_SAQUE", "nunique"),
    )
else:
    agg_mensal = df.groupby(period_col, as_index=False).agg(
        VALOR_TOTAL=("VALOR_RETIRADA", "sum"),
        QT_TRANSACOES=("VALOR_RETIRADA", "count"),
    )
if "SUSPEITA" in df.columns and "ID_SAQUE" in df.columns:
    _sus = df[[period_col, "ID_SAQUE", "SUSPEITA"]].copy()
    _sus["SUSPEITA"] = pd.to_numeric(_sus["SUSPEITA"], errors="coerce").fillna(0)
    suspeitas_agg = (
        _sus.groupby([period_col, "ID_SAQUE"], as_index=False)["SUSPEITA"]
        .max()
        .groupby(period_col, as_index=False)["SUSPEITA"]
        .sum()
        .rename(columns={"SUSPEITA": "SUSPEITAS"})
    )
    agg_mensal = agg_mensal.merge(suspeitas_agg, on=period_col, how="left")
elif "SUSPEITA" in df.columns:
    suspeitas_agg = df.groupby(period_col, as_index=False)["SUSPEITA"].sum().rename(columns={"SUSPEITA": "SUSPEITAS"})
    agg_mensal = agg_mensal.merge(suspeitas_agg, on=period_col, how="left")
else:
    agg_mensal["SUSPEITAS"] = 0
if "RISK_SCORE" in df.columns:
    score_agg = df.groupby(period_col, as_index=False)["RISK_SCORE"].mean().rename(columns={"RISK_SCORE": "SCORE_MEDIO"})
    agg_mensal = agg_mensal.merge(score_agg, on=period_col, how="left")
else:
    agg_mensal["SCORE_MEDIO"] = 0.0
agg_mensal["TRANSACOES"] = agg_mensal["QT_TRANSACOES"]
agg_mensal["TICKET_MEDIO"] = agg_mensal["VALOR_TOTAL"] / agg_mensal["TRANSACOES"].replace(0, 1)
agg_mensal["TAXA_FRAUDE"] = (agg_mensal["SUSPEITAS"] / agg_mensal["TRANSACOES"].replace(0, 1)) * 100
try:
    agg_mensal["_ordem"] = pd.to_datetime(agg_mensal[period_col].astype(str).str.replace(" ", "-"), errors="coerce")
    agg_mensal = agg_mensal.sort_values("_ordem").drop(columns=["_ordem"], errors="ignore")
except Exception:
    if period_col in agg_mensal.columns:
        agg_mensal = agg_mensal.sort_values(period_col)
agg_mensal["PCT_MUDANCA"] = agg_mensal["VALOR_TOTAL"].pct_change() * 100
agg_mensal["MA3"] = agg_mensal["VALOR_TOTAL"].rolling(3, min_periods=1).mean()

# ========== 2) FINANCIAL EVOLUTION (volume + MA + % change) ==========
st.subheader("Volume financeiro por mês e média móvel")
if not agg_mensal.empty:
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(x=agg_mensal[period_col], y=agg_mensal["VALOR_TOTAL"], name="Volume mensal", mode="lines+markers", line=dict(color="#0047BB", width=3), marker=dict(size=10, line=dict(width=1, color="white")), opacity=1))
    fig1.add_trace(go.Scatter(x=agg_mensal[period_col], y=agg_mensal["MA3"], name="Média móvel (3 meses)", mode="lines", line=dict(color="#7A1FA2", width=2, dash="dash"), opacity=1))
    fig1.update_layout(
        title=None,
        autosize=True,
        height=350,
        uirevision="rel_fig1",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#F3F6F9",
        font_color="#0047BB",
        xaxis_title="Mês",
        yaxis_title="Valor (R$)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(automargin=False, gridcolor="#E3E8EE"),
        yaxis=dict(automargin=False, gridcolor="#E3E8EE"),
    )
    apply_plotly_readability(fig1, zig_brand_layout=True, height=350)
    _st_plotly(fig1, key="rel_fig1_vol")
    # Table with % change vs previous month
    tbl_evol = agg_mensal[[period_col, "VALOR_TOTAL", "MA3", "PCT_MUDANCA"]].copy()  # type: ignore[index]
    tbl_evol["VALOR_TOTAL"] = tbl_evol["VALOR_TOTAL"].apply(lambda x: format_brl(x))
    tbl_evol["MA3"] = tbl_evol["MA3"].apply(lambda x: format_brl(x))
    tbl_evol["PCT_MUDANCA"] = tbl_evol["PCT_MUDANCA"].fillna(0).apply(lambda x: f"{x:+.1f}%")
    with st.expander("Ver variação % vs mês anterior"):
        st.dataframe(
            add_placeholder_data_criacao_column(tbl_evol),
            width="stretch",
            hide_index=True,
            column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
        )
else:
    st.info("Sem dados mensais para evolução financeira.")

# ========== 3) FRAUD EVOLUTION (rate + score) ==========
st.markdown('<div class="rel-section-title">Evolução da fraude</div>', unsafe_allow_html=True)
if not agg_mensal.empty:
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=agg_mensal[period_col], y=agg_mensal["TAXA_FRAUDE"], name="Taxa de fraude (%)", mode="lines+markers", line=dict(color="#7A1FA2")))
    fig2.add_trace(go.Scatter(x=agg_mensal[period_col], y=agg_mensal["SCORE_MEDIO"], name="Score médio", mode="lines+markers", yaxis="y2", line=dict(color="#0066CC")))
    fig2.update_layout(
        title="Taxa de fraude e score médio por mês",
        autosize=True,
        height=350,
        uirevision="rel_fig2",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#F3F6F9",
        font_color="#0047BB",
        xaxis=dict(title="Mês", automargin=False, gridcolor="#E3E8EE"),
        yaxis=dict(title="Taxa de fraude (%)", side="left", automargin=False, gridcolor="#E3E8EE"),
        yaxis2=dict(title="Score médio", overlaying="y", side="right", automargin=False, gridcolor="#E3E8EE"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    apply_plotly_readability(fig2, skip_axes=True, height=350)
    _st_plotly(fig2, key="rel_fig2_fraud")
else:
    st.info("Sem dados mensais para evolução da fraude.")

# ========== 4) RISK DISTRIBUTION (stacked bars, ZIG colors) ==========
st.markdown('<div class="rel-section-title">Distribuição de risco</div>', unsafe_allow_html=True)
if "RISK_LEVEL" in df.columns and not df.empty:
    if "ID_SAQUE" in df.columns:
        risk_agg = df.groupby([period_col, "RISK_LEVEL"])["ID_SAQUE"].nunique().reset_index(name="quantidade")
    else:
        risk_agg = df.groupby([period_col, "RISK_LEVEL"]).size().reset_index(name="quantidade")
    level_order = ["baixo risco", "risco medio", "alto risco", "risco critico"]
    risk_agg["RISK_LEVEL"] = pd.Categorical(risk_agg["RISK_LEVEL"], categories=level_order, ordered=True)
    risk_agg = risk_agg.sort_values(["RISK_LEVEL"])
    try:
        risk_agg["_ordem"] = pd.to_datetime(risk_agg[period_col].astype(str).str.replace(" ", "-"), errors="coerce")
        risk_agg = risk_agg.sort_values("_ordem").drop(columns=["_ordem"], errors="ignore")
    except Exception:
        pass
    color_map = {
        "baixo risco": ZIG_RISK_LOW,
        "risco medio": ZIG_RISK_MED,
        "alto risco": ZIG_RISK_HIGH,
        "risco critico": ZIG_RISK_CRITICAL,
    }
    fig3 = px.bar(risk_agg, x=period_col, y="quantidade", color="RISK_LEVEL", barmode="stack", color_discrete_map=color_map, title="Transações por nível de risco")
    fig3.update_layout(
        autosize=True,
        height=350,
        uirevision="rel_fig3",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#F3F6F9",
        font_color="#0047BB",
        xaxis_title="Mês",
        yaxis_title="Quantidade",
        xaxis=dict(automargin=False),
        yaxis=dict(automargin=False),
    )
    apply_plotly_readability(fig3, zig_brand_layout=True, height=350)
    _st_plotly(fig3, key="rel_fig3_risk")
else:
    st.info("Coluna de período mensal (PERIODO_MES) ou RISK_LEVEL não disponível para este recorte.")

# ========== 5) RECEIVING BANKS (top 5 + Others) ==========
st.markdown('<div class="rel-section-title">Bancos recebedores</div>', unsafe_allow_html=True)
if "BANCO_RECEBEDOR" in df.columns and not df.empty:
    bank_vol = df.groupby("BANCO_RECEBEDOR", as_index=False)["VALOR_RETIRADA"].sum().sort_values("VALOR_RETIRADA", ascending=False)
    if len(bank_vol) > 5:
        top5 = bank_vol.head(5)
        others_val = bank_vol.iloc[5:]["VALOR_RETIRADA"].sum()
        others_row = pd.DataFrame([{"BANCO_RECEBEDOR": "Outros", "VALOR_RETIRADA": others_val}])
        bank_display = pd.concat([top5, others_row], ignore_index=True)
    else:
        bank_display = bank_vol
    if not bank_display.empty:
        def _rel_qtd_transacoes(sub: pd.DataFrame) -> int:
            if "ID_SAQUE" in sub.columns and sub["ID_SAQUE"].notna().any():
                return int(sub["ID_SAQUE"].nunique())
            return int(len(sub))

        total_reg_rel = _rel_qtd_transacoes(df)
        top5_nomes = bank_display.loc[bank_display["BANCO_RECEBEDOR"].astype(str) != "Outros", "BANCO_RECEBEDOR"].tolist()
        qtd_rows = []
        for _, br in bank_display.iterrows():
            nome_b = br["BANCO_RECEBEDOR"]
            if str(nome_b) == "Outros":
                mask_o = ~df["BANCO_RECEBEDOR"].isin(top5_nomes)
                sub_o = df.loc[mask_o]
            else:
                sub_o = df.loc[df["BANCO_RECEBEDOR"] == nome_b]
            q = _rel_qtd_transacoes(sub_o)
            qtd_rows.append({"BANCO_RECEBEDOR": nome_b, "qtd_transacoes": q})
        bar_banks = pd.DataFrame(qtd_rows)
        bar_banks["pct_transacoes"] = (
            (bar_banks["qtd_transacoes"] / total_reg_rel * 100.0) if total_reg_rel > 0 else 0.0
        )
        bar_banks["label_bar"] = bar_banks.apply(
            lambda r: f"{int(r['qtd_transacoes']):,}".replace(",", ".") + f" · {r['pct_transacoes']:.1f}%",
            axis=1,
        )
        _ord_b = bar_banks["BANCO_RECEBEDOR"].astype(str).tolist()

        fig4 = px.pie(bank_display, names="BANCO_RECEBEDOR", values="VALOR_RETIRADA", hole=0.35, color_discrete_sequence=["#0047BB", "#0066CC", "#7A1FA2", "#8E44AD", "#5F6A72", "#F59E0B"])
        fig4.update_layout(
            autosize=True,
            height=350,
            uirevision="rel_fig4",
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#0047BB",
            title="Top 5 bancos por volume (demais = Outros)",
        )
        apply_plotly_readability(fig4, skip_axes=True, height=350)
        fig4b = px.bar(
            bar_banks,
            x="BANCO_RECEBEDOR",
            y="qtd_transacoes",
            text="label_bar",
            custom_data=["pct_transacoes"],
            category_orders={"BANCO_RECEBEDOR": _ord_b},
        )
        fig4b.update_layout(
            title=dict(text="<b>Por quantidade de transações</b>", font=dict(color="#0047BB", size=15)),
            autosize=True,
            height=350,
            uirevision="rel_fig4b",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="#F3F6F9",
            font_color="#0047BB",
            xaxis_title="Banco recebedor",
            yaxis_title="Qtd. transações",
            showlegend=False,
            xaxis={"categoryorder": "array", "categoryarray": _ord_b, "automargin": False},
            yaxis={"automargin": False},
            margin=dict(t=48, b=72, l=48, r=24),
        )
        fig4b.update_traces(
            marker_color="#0047BB",
            textposition="outside",
            hovertemplate="Banco: %{x}<br>Qtd: %{y}<br>% no período: %{customdata[0]:.2f}%<extra></extra>",
        )
        # skip_axes: preserva categoryarray / ordem das barras (zig_brand_layout substituiria xaxis inteiro)
        apply_plotly_readability(fig4b, skip_axes=True, height=350)
        c_bank_pie, c_bank_bar = st.columns(2)
        with c_bank_pie:
            _st_plotly(fig4, key="rel_fig4_bank_pie")
        with c_bank_bar:
            _st_plotly(fig4b, key="rel_fig4b_bank_bar")
    else:
        st.info("Sem dados de bancos.")
else:
    st.info("Coluna BANCO_RECEBEDOR não disponível.")

# ========== 6) PAYMENT METHODS (percentage share) ==========
st.markdown('<div class="rel-section-title">Métodos de pagamento</div>', unsafe_allow_html=True)
if "METODO_PAGAMENTO_ESPECIFICO" in df.columns and not df.empty:
    met = df[["METODO_PAGAMENTO_ESPECIFICO", "VALOR_RETIRADA"]].copy()
    if "ID_SAQUE" in df.columns:
        met["ID_SAQUE"] = df["ID_SAQUE"].values
    met["METODO"] = "OUTROS"
    met.loc[met["METODO_PAGAMENTO_ESPECIFICO"].astype(str).str.upper().str.contains("PIX", na=False), "METODO"] = "PIX"
    met.loc[met["METODO_PAGAMENTO_ESPECIFICO"].astype(str).str.upper().str.contains("TED", na=False), "METODO"] = "TED"
    met.loc[met["METODO_PAGAMENTO_ESPECIFICO"].astype(str).str.upper().str.contains("BOLE", na=False), "METODO"] = "BOLETO"
    metodo_share = met.groupby("METODO", as_index=False)["VALOR_RETIRADA"].sum()
    total_met = float(metodo_share["VALOR_RETIRADA"].sum())
    metodo_share["pct"] = (metodo_share["VALOR_RETIRADA"] / total_met * 100) if total_met else 0
    metodo_share = metodo_share.set_index("METODO").reindex(["TED", "PIX", "BOLETO", "OUTROS"]).fillna(0).reset_index()
    metodo_share["label"] = metodo_share["pct"].map(lambda p: f"{p:.1f}%")
    _ord_met = ["TED", "PIX", "BOLETO", "OUTROS"]
    if "ID_SAQUE" in met.columns and met["ID_SAQUE"].notna().any():
        qtd_met = met.groupby("METODO", as_index=True)["ID_SAQUE"].nunique().reindex(_ord_met).fillna(0).astype(int)
    else:
        qtd_met = met.groupby("METODO").size().reindex(_ord_met).fillna(0).astype(int)
    total_met_qtd = int(qtd_met.sum())
    if total_met_qtd <= 0:
        total_met_qtd = 1
    bar_met = pd.DataFrame({"METODO": _ord_met, "qtd_transacoes": qtd_met.values})
    bar_met["pct_transacoes"] = bar_met["qtd_transacoes"] / total_met_qtd * 100.0
    bar_met["label_bar"] = bar_met.apply(
        lambda r: f"{int(r['qtd_transacoes']):,}".replace(",", ".") + f" · {r['pct_transacoes']:.1f}%",
        axis=1,
    )

    fig5 = px.pie(metodo_share, names="METODO", values="pct", hole=0.4, title="Participação por método de pagamento (%)", color_discrete_map={"PIX": "#7A1FA2", "TED": "#0066CC", "BOLETO": "#0047BB", "OUTROS": "#5F6A72"})
    fig5.update_layout(
        autosize=True,
        height=350,
        uirevision="rel_fig5",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#0047BB",
    )
    apply_plotly_readability(fig5, skip_axes=True, height=350)
    fig5b = px.bar(
        bar_met,
        x="METODO",
        y="qtd_transacoes",
        text="label_bar",
        custom_data=["pct_transacoes"],
        category_orders={"METODO": _ord_met},
    )
    fig5b.update_layout(
        title=dict(text="<b>Por quantidade de transações</b>", font=dict(color="#0047BB", size=15)),
        autosize=True,
        height=350,
        uirevision="rel_fig5b",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#F3F6F9",
        font_color="#0047BB",
        xaxis_title="Método de pagamento",
        yaxis_title="Qtd. transações",
        showlegend=False,
        xaxis={"categoryorder": "array", "categoryarray": _ord_met, "automargin": False},
        yaxis={"automargin": False},
        margin=dict(t=48, b=72, l=48, r=24),
    )
    fig5b.update_traces(
        marker_color="#0047BB",
        textposition="outside",
        hovertemplate="Método: %{x}<br>Qtd: %{y}<br>% no período: %{customdata[0]:.2f}%<extra></extra>",
    )
    apply_plotly_readability(fig5b, skip_axes=True, height=350)
    c_met_pie, c_met_bar = st.columns(2)
    with c_met_pie:
        _st_plotly(fig5, key="rel_fig5_met_pie")
    with c_met_bar:
        _st_plotly(fig5b, key="rel_fig5b_met_bar")
else:
    st.info("Coluna METODO_PAGAMENTO_ESPECIFICO não disponível.")

# ========== 7) MONTHLY SUMMARY TABLE (+ average ticket) ==========
st.markdown('<div class="rel-section-title">Resumo mensal</div>', unsafe_allow_html=True)
if not agg_mensal.empty:
    consolidado = agg_mensal[[period_col, "VALOR_TOTAL", "TRANSACOES", "SUSPEITAS", "TAXA_FRAUDE", "SCORE_MEDIO", "TICKET_MEDIO"]].copy()
    consolidado["TAXA_FRAUDE"] = consolidado["TAXA_FRAUDE"].round(2)
    consolidado["SCORE_MEDIO"] = consolidado["SCORE_MEDIO"].round(2)
    consolidado_display = consolidado.copy()
    consolidado_display = consolidado_display.rename(columns={period_col: "periodo", "VALOR_TOTAL": "total_value", "TRANSACOES": "transactions", "SUSPEITAS": "suspicious_transactions", "TAXA_FRAUDE": "fraud_rate", "SCORE_MEDIO": "average_score", "TICKET_MEDIO": "average_ticket"})
    consolidado_display["total_value"] = consolidado_display["total_value"].apply(lambda x: format_brl(x))
    consolidado_display["average_ticket"] = consolidado_display["average_ticket"].apply(lambda x: format_brl(x))
    consolidado_display["fraud_rate"] = consolidado_display["fraud_rate"].astype(str) + "%"
    st.dataframe(
        add_placeholder_data_criacao_column(consolidado_display),
        width="stretch",
        hide_index=True,
        column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
    )
else:
    st.info("Nenhum dado consolidado mensal para exibir.")
