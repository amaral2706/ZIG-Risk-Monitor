"""
Comportamento suspeito — painel investigativo (PF, PJ, boletos, indicadores de risco).
Filtros: período (datas), mês (PERIODO_MES), place (opcional).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import io
import sys
from typing import Optional, Tuple

import pandas as pd
import plotly.express as px
import streamlit as st

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
from modules.excel_export import prepare_download_base_df
from modules.feature_engineering import add_features
from modules.risk_engine import calcular_score
from modules.table_columns import (
    DATA_CRIACAO_COL,
    add_placeholder_data_criacao_column,
    fill_data_criacao_place_e_org,
    format_data_criacao_series,
)
from modules.ui import apply_enterprise_theme, aplicar_estilo_global, apply_plotly_readability, format_brl

def _row_pct(mask: pd.Series, n: int) -> float:
    return float(mask.sum() / n * 100) if n else 0.0


def _hora_serie(df: pd.DataFrame, date_col: str) -> pd.Series:
    if "HORA_TRANSACAO" in df.columns and df["HORA_TRANSACAO"].notna().any():
        return pd.to_numeric(df["HORA_TRANSACAO"], errors="coerce").fillna(-1).astype(int)
    if date_col in df.columns:
        s = pd.to_datetime(df[date_col], errors="coerce")
        if s.dt.tz is not None:
            s = s.dt.tz_convert("UTC")
        return s.dt.hour.fillna(-1).astype(int)
    return pd.Series([-1] * len(df), index=df.index)


def _bank_concentration(filtered: pd.DataFrame, vol: float) -> tuple[bool, float, str | None]:
    if vol <= 0 or "BANCO_RECEBEDOR" not in filtered.columns:
        return False, 0.0, None
    by_bank = (
        filtered.assign(_banco=filtered["BANCO_RECEBEDOR"].astype(str))
        .groupby("_banco", as_index=False)["VALOR_RETIRADA"]
        .sum()
        .rename(columns={"_banco": "BANCO_RECEBEDOR"})
    )
    if by_bank.empty:
        return False, 0.0, None
    top = by_bank.sort_values("VALOR_RETIRADA", ascending=False).iloc[0]
    pct = float(top["VALOR_RETIRADA"] / vol * 100) if vol else 0.0
    nome = str(top["BANCO_RECEBEDOR"])
    return pct > 70.0, pct, nome


def _account_concentration(filtered: pd.DataFrame, vol: float) -> tuple[bool, float, int]:
    if vol <= 0 or "CONTA_RECEBEDOR" not in filtered.columns:
        return False, 0.0, 0
    by_c = (
        filtered.assign(_conta=filtered["CONTA_RECEBEDOR"].astype(str))
        .groupby("_conta", as_index=False)["VALOR_RETIRADA"]
        .sum()
        .rename(columns={"_conta": "CONTA_RECEBEDOR"})
    )
    by_c = by_c.sort_values("VALOR_RETIRADA", ascending=False)
    if by_c.empty:
        return False, 0.0, 0
    top1 = float(by_c.iloc[0]["VALOR_RETIRADA"] / vol * 100)
    top3 = float(by_c.head(3)["VALOR_RETIRADA"].sum() / vol * 100) if len(by_c) >= 1 else top1
    flag = top1 > 50.0 or top3 > 75.0
    n_contas = int(by_c.shape[0])
    return flag, top3, n_contas


def _hourly_distribution(filtered: pd.DataFrame, date_col: str) -> pd.DataFrame:
    h = _hora_serie(filtered, date_col)
    valid = h[h >= 0]
    if valid.empty:
        return pd.DataFrame(columns=["hora", "qtd"])
    vc = valid.value_counts().reindex(range(24), fill_value=0).rename_axis("hora").reset_index(name="qtd")
    return vc


_VIZ_PRIMARY = "#0047BB"
_VIZ_SECONDARY = "#7A1FA2"


def _segment_kpis(d: pd.DataFrame, date_col: str) -> dict[str, float | int]:
    """KPIs por recorte; % suspeitas = transações (ID_SAQUE) com SUSPEITA=1, não score ≥ 60."""
    if d.empty:
        return {
            "n": 0,
            "vol": 0.0,
            "n_tx": 0,
            "pct_suspeita": 0.0,
            "mean_score": 0.0,
            "qtd_noturno": 0,
            "pct_noturno": 0.0,
            "qtd_dest_rep": 0,
            "pct_dest_rep": 0.0,
            "pct_valor_centavos": 0.0,
        }
    n = len(d)
    vol = float(d["VALOR_RETIRADA"].sum()) if "VALOR_RETIRADA" in d.columns else 0.0
    n_tx = int(d["ID_SAQUE"].nunique()) if "ID_SAQUE" in d.columns else n
    if "SUSPEITA" in d.columns and "ID_SAQUE" in d.columns:
        n_susp = int(d.groupby("ID_SAQUE", dropna=False)["SUSPEITA"].max().sum())
    elif "SUSPEITA" in d.columns:
        n_susp = int(pd.to_numeric(d["SUSPEITA"], errors="coerce").fillna(0).eq(1).sum())
    else:
        n_susp = 0
    mean_score = float(pd.to_numeric(d["RISK_SCORE"], errors="coerce").mean()) if "RISK_SCORE" in d.columns else 0.0
    pct_suspeita = (n_susp / n_tx * 100) if n_tx else 0.0
    qtd_noturno = int(d["SAQUE_NOTURNO"].eq(1).sum()) if "SAQUE_NOTURNO" in d.columns else 0
    pct_noturno = (qtd_noturno / n * 100) if n else 0.0
    qtd_dest_rep = int(d["DESTINATARIO_REPETIDO"].eq(1).sum()) if "DESTINATARIO_REPETIDO" in d.columns else 0
    pct_dest_rep = (qtd_dest_rep / n * 100) if n else 0.0
    pct_valor_centavos = 0.0
    if "VALOR_RETIRADA" in d.columns and n:
        vals = pd.to_numeric(d["VALOR_RETIRADA"], errors="coerce").fillna(0)
        pct_valor_centavos = float((vals % 1 > 0.01).sum() / n * 100)
    return {
        "n": n,
        "vol": vol,
        "n_tx": n_tx,
        "pct_suspeita": pct_suspeita,
        "mean_score": mean_score,
        "qtd_noturno": qtd_noturno,
        "pct_noturno": pct_noturno,
        "qtd_dest_rep": qtd_dest_rep,
        "pct_dest_rep": pct_dest_rep,
        "pct_valor_centavos": pct_valor_centavos,
    }


def _df_hour_count(d: pd.DataFrame, date_col: str) -> pd.DataFrame:
    h = _hourly_distribution(d, date_col)
    if h.empty:
        return pd.DataFrame(columns=["hora", "count"])
    return h.rename(columns={"qtd": "count"})


def _col_recebedor_nome(d: pd.DataFrame) -> str | None:
    for c in ("NOME_RECEBEDOR", "NOME_PLACE", "NOME_ORGANIZACAO"):
        if c in d.columns:
            return c
    return None


def _col_recebedor_doc(d: pd.DataFrame) -> str | None:
    for c in ("CPF_CNPJ_RECEBEDOR", "CPF_CNPJ", "CNPJ", "DOCUMENTO_RECEBEDOR"):
        if c in d.columns:
            return c
    return None


def _contas_suspeitas_tabela(d: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    """Contas com ao menos uma linha SUSPEITA=1 (regra de negócio); até `limit` contas distintas."""
    cols_out = [DATA_CRIACAO_COL, "conta", "nome do recebedor", "CPF/CNPJ do recebedor", "SUSPEITA"]
    if d.empty or "SUSPEITA" not in d.columns or "CONTA_RECEBEDOR" not in d.columns:
        return pd.DataFrame(columns=cols_out)
    s = d.loc[pd.to_numeric(d["SUSPEITA"], errors="coerce").fillna(0).eq(1)].copy()
    if s.empty:
        return pd.DataFrame(columns=cols_out)
    fill_data_criacao_place_e_org(s)
    if "VALOR_RETIRADA" in s.columns:
        s = s.sort_values("VALOR_RETIRADA", ascending=False)
    nk = _col_recebedor_nome(s)
    dk = _col_recebedor_doc(s)
    if DATA_CRIACAO_COL in s.columns:
        dc_col = format_data_criacao_series(s[DATA_CRIACAO_COL]).tolist()
    else:
        dc_col = ["—"] * len(s)
    out = pd.DataFrame(
        {
            DATA_CRIACAO_COL: dc_col,
            "conta": s["CONTA_RECEBEDOR"].astype(str),
            "nome do recebedor": s[nk].astype(str) if nk else "—",
            "CPF/CNPJ do recebedor": s[dk].astype(str) if dk else "—",
            "SUSPEITA": pd.to_numeric(s["SUSPEITA"], errors="coerce").fillna(0).astype(int),
        }
    )
    out = out.drop_duplicates(subset=["conta"], keep="first").head(limit)
    return out


def _render_segmento_visual(titulo: str, d: pd.DataFrame, date_col: str) -> str:
    """4 KPIs, histograma, hora, tabela de contas suspeitas, resumo e alerta."""
    st.subheader(titulo)
    if d.empty:
        st.caption("Sem dados no período.")
        return "Sem dados"

    m = _segment_kpis(d, date_col)
    vol_seg = float(m["vol"])
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Volume", format_brl(vol_seg))
    with c2:
        st.metric("Score médio", f"{float(m['mean_score']):.1f}")
    with c3:
        st.metric("% suspeitas (SUSPEITA)", f"{float(m['pct_suspeita']):.1f}%")
    with c4:
        st.metric("Qtd. transações", f"{int(m['n_tx']):,}".replace(",", "."))

    conc_flag, _, _ = _account_concentration(d, vol_seg)

    col_a, col_b = st.columns(2)
    with col_a:
        if "VALOR_RETIRADA" in d.columns:
            nb = min(40, max(10, len(d) // 5))
            fig_h = px.histogram(d, x="VALOR_RETIRADA", nbins=nb)
            fig_h.update_traces(marker_color=_VIZ_SECONDARY, marker_line_width=0, opacity=0.9)
            fig_h.update_layout(
                showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color=_VIZ_PRIMARY,
                margin=dict(t=40, b=48, l=52, r=20),
                title=dict(text="Valores", font=dict(size=14, color=_VIZ_PRIMARY)),
            )
            apply_plotly_readability(fig_h, height=300, histogram=True)
            st.plotly_chart(fig_h, width="stretch")
        else:
            st.caption("—")

    with col_b:
        hc = _df_hour_count(d, date_col)
        if not hc.empty and int(hc["count"].sum()) > 0:
            fig_b = px.bar(hc, x="hora", y="count", labels={"hora": "Hora", "count": "Qtd."})
            fig_b.update_traces(marker_color=_VIZ_PRIMARY)
            fig_b.update_layout(
                showlegend=False,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color=_VIZ_PRIMARY,
                margin=dict(t=40, b=48, l=52, r=20),
                title=dict(text="Por hora", font=dict(size=14, color=_VIZ_PRIMARY)),
            )
            apply_plotly_readability(fig_b, height=300, histogram=True)
            st.plotly_chart(fig_b, width="stretch")
        else:
            st.caption("—")

    st.subheader("Contas suspeitas")
    df_tab = _contas_suspeitas_tabela(d, limit=10)
    if df_tab.empty:
        st.caption("Nenhuma conta com transação suspeita (SUSPEITA=1) neste segmento.")
    else:
        st.dataframe(
            add_placeholder_data_criacao_column(df_tab),
            width="stretch",
            hide_index=True,
            column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
        )

    st.caption(
        f"{int(m['n'])} linhas · {format_brl(vol_seg)} · {float(m['pct_suspeita']):.1f}% suspeitas (SUSPEITA) · "
        f"noturno {float(m['pct_noturno']):.1f}%."
    )
    if conc_flag:
        st.warning("Alta concentração detectada")
    else:
        st.info("Comportamento distribuído")

    tags: list[str] = ["Concentrado" if conc_flag else "Distribuído"]
    if float(m["pct_suspeita"]) > 8:
        tags.append("Suspeito")
    return " · ".join(dict.fromkeys(tags))


st.set_page_config(
    page_title="Comportamento suspeito | ZIG Risk Monitor",
    page_icon=":warning:",
    layout="wide",
    initial_sidebar_state="expanded",
)
if not ensure_authenticated():
    st.stop()

apply_enterprise_theme()
aplicar_estilo_global()
st.markdown(
    """
    <style>
    .cs-title { font-size: 32px; font-weight: 700; color: #0047BB; margin-bottom: 0.35rem; }
    .cs-muted { color: #64748b; margin-bottom: 1rem; font-size: 15px; }
    .cs-section { color: #0047BB; font-weight: 600; font-size: 18px; margin: 1rem 0 0.5rem 0; }
    .cs-tag { display: inline-block; background: #0047BB; color: #fff; padding: 4px 12px;
      border-radius: 999px; font-weight: 600; font-size: 14px; margin: 4px 6px 0 0; }
    .dash-filter-row { background: #ffffff; border: 1px solid #dbe3f0; border-radius: 12px;
      padding: 10px 12px 12px 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.06); }
    </style>
    """,
    unsafe_allow_html=True,
)

df = load_processed_data()
if df.empty:
    st.info("Nenhum dado processado disponível. Execute a pipeline no Dashboard ou faça upload na página Admin Upload.")
    st.stop()

refez_features = False
if "RECEIVER_TIPO" not in df.columns or "IS_BOLETO" not in df.columns:
    df = add_features(df)
    refez_features = True
# add_features altera VALOR_ALTO / DESTINATARIO_REPETIDO / SAQUE_NOTURNO: é obrigatório recalcular score e SUSPEITA.
if refez_features or "RISK_SCORE" not in df.columns or "SUSPEITA" not in df.columns:
    df = calcular_score(df)

date_col = date_column_for_period_filter(df)
if date_col in df.columns and not pd.api.types.is_datetime64_any_dtype(df[date_col]):
    df[date_col] = _parse_timestamp_column(df[date_col])

if date_col not in df.columns or df[date_col].isna().all():
    data_min, data_max = date.today() - timedelta(days=30), date.today()
else:
    col_dt = df[date_col].dropna()
    if col_dt.empty:
        data_min, data_max = date.today() - timedelta(days=30), date.today()
    else:
        _min_ts = pd.Timestamp(col_dt.min())
        _max_ts = pd.Timestamp(col_dt.max())
        data_min = _min_ts.date() if hasattr(_min_ts, "date") else _min_ts
        data_max = _max_ts.date() if hasattr(_max_ts, "date") else _max_ts

if "PERIODO_MES" not in df.columns and date_col in df.columns and df[date_col].notna().any():
    _dc = df[date_col]
    if not pd.api.types.is_datetime64_any_dtype(_dc):
        _dc = _parse_timestamp_column(_dc)
    if _dc.dt.tz is None:
        _dc = _dc.dt.tz_localize("UTC", ambiguous=False, nonexistent="shift_forward")
    _ln = _dc.dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    df = df.copy()
    df["PERIODO_MES"] = _ln.dt.to_period("M").astype(str)

sync_global_date_inputs(data_min, data_max)

st.markdown('<div class="cs-title">Comportamento suspeito</div>', unsafe_allow_html=True)
st.caption("Análise de padrões e risco · filtros por período, mês e place")

yms_cs = year_months_br_from_utc_series(df[date_col].dropna()) if date_col in df.columns else []
if not yms_cs:
    y_min, m_min = data_min.year, data_min.month
    y_max, m_max = data_max.year, data_max.month
    yms_cs = []
    cy, cm = y_min, m_min
    while (cy, cm) <= (y_max, m_max):
        yms_cs.append((cy, cm))
        if (cy, cm) == (y_max, m_max):
            break
        cm += 1
        if cm > 12:
            cm, cy = 1, cy + 1
    yms_cs = sorted(set(yms_cs), reverse=True)
mes_labels_cs, mes_ranges_cs = labels_and_ranges_for_months(yms_cs, CALENDAR_MIN, CALENDAR_MAX)

st.markdown('<div class="dash-filter-row">', unsafe_allow_html=True)
col_mes, col_f1, col_f2 = st.columns([2, 2, 2])
with col_mes:
    mes_idx_cs = st.selectbox(
        "Filtrar por mês",
        range(len(mes_labels_cs)),
        format_func=lambda i: mes_labels_cs[i],
        key="cs_mes_idx_v2",
    )
with col_f1:
    data_inicial = st.date_input(
        "Data inicial",
        min_value=CALENDAR_MIN,
        max_value=CALENDAR_MAX,
        format="DD/MM/YYYY",
        key="data_inicial",
    )
with col_f2:
    data_final = st.date_input(
        "Data final",
        min_value=CALENDAR_MIN,
        max_value=CALENDAR_MAX,
        format="DD/MM/YYYY",
        key="data_final",
    )

st.markdown("</div>", unsafe_allow_html=True)

data_inicial = max(CALENDAR_MIN, min(data_inicial, CALENDAR_MAX))
data_final = max(CALENDAR_MIN, min(data_final, CALENDAR_MAX))

periodo_mes: Optional[Tuple[date, date]] = None
if mes_idx_cs > 0 and mes_idx_cs - 1 < len(mes_ranges_cs):
    periodo_mes = mes_ranges_cs[mes_idx_cs - 1]

if periodo_mes is not None:
    filt_start, filt_end = periodo_mes
else:
    filt_start, filt_end = data_inicial, data_final
    if filt_start > filt_end:
        filt_start, filt_end = filt_end, filt_start

filtered = filter_by_brazil_calendar_dates(df, date_col, filt_start, filt_end)

place_col = "NOME_PLACE" if "NOME_PLACE" in filtered.columns else ("NOME_ORGANIZACAO" if "NOME_ORGANIZACAO" in filtered.columns else None)
if not filtered.empty:
    st.markdown('<div class="dash-filter-row">', unsafe_allow_html=True)
    if place_col:
        opts = sorted(filtered[place_col].dropna().astype(str).unique().tolist())
        sel_place = st.multiselect(
            "Place (opcional) — refine por estabelecimento",
            options=opts,
            default=[],
            key="cs_filter_place",
        )
        if sel_place:
            filtered = filtered[filtered[place_col].astype(str).isin(sel_place)]
    st.markdown("</div>", unsafe_allow_html=True)

col_k1, col_k2, col_k3, col_k4 = st.columns(4)

if filtered.empty:
    with col_k1:
        st.warning("Sem dados no período (ajuste datas, mês ou place).")
    st.stop()

_place_tuple = tuple(st.session_state.get("cs_filter_place") or ())
_cs_export_sig = (int(mes_idx_cs), str(data_inicial), str(data_final), _place_tuple)
if st.session_state.get("_cs_export_sig_cache") != _cs_export_sig:
    st.session_state.pop("cs_xlsx_bytes", None)
st.session_state["_cs_export_sig_cache"] = _cs_export_sig

n = len(filtered)
n_unique = filtered["ID_SAQUE"].nunique() if "ID_SAQUE" in filtered.columns else n
vol = float(filtered["VALOR_RETIRADA"].sum()) if "VALOR_RETIRADA" in filtered.columns else 0.0
if "SUSPEITA" in filtered.columns and "ID_SAQUE" in filtered.columns:
    susp = int(filtered.groupby("ID_SAQUE", dropna=False)["SUSPEITA"].max().sum())
elif "SUSPEITA" in filtered.columns:
    susp = int(pd.to_numeric(filtered["SUSPEITA"], errors="coerce").fillna(0).eq(1).sum())
else:
    susp = 0
pct_susp = (susp / n_unique * 100) if n_unique else 0.0

concentração_banco, _, _ = _bank_concentration(filtered, vol)
alta_concentracao_conta, _, _ = _account_concentration(filtered, vol)

mean_score = float(filtered["RISK_SCORE"].mean()) if "RISK_SCORE" in filtered.columns else 0.0

boleto_n = int(filtered["IS_BOLETO"].eq(1).sum()) if "IS_BOLETO" in filtered.columns else 0
terc_n = int(filtered["TERCEIRO"].eq(1).sum()) if "TERCEIRO" in filtered.columns else 0
noturno_n = int(filtered["SAQUE_NOTURNO"].eq(1).sum()) if "SAQUE_NOTURNO" in filtered.columns else 0
rep_n = int(filtered["DESTINATARIO_REPETIDO"].eq(1).sum()) if "DESTINATARIO_REPETIDO" in filtered.columns else 0
val_alto_n = int(filtered["VALOR_ALTO"].eq(1).sum()) if "VALOR_ALTO" in filtered.columns else 0

with col_k1:
    st.metric("Volume", format_brl(vol))
with col_k2:
    st.metric("Transações", f"{n_unique:,}".replace(",", "."))
with col_k3:
    st.metric("% suspeitas", f"{pct_susp:.1f}%")
with col_k4:
    st.metric("Score médio", f"{mean_score:.1f}" if "RISK_SCORE" in filtered.columns else "—")

with st.sidebar:
    if st.button("Gerar download", key="cs_gen_xlsx", width="stretch"):
        with st.spinner("Gerando…"):
            buf = io.BytesIO()
            prepare_download_base_df(filtered).to_excel(buf, index=False, engine="openpyxl")
            st.session_state["cs_xlsx_bytes"] = buf.getvalue()
    if st.session_state.get("cs_xlsx_bytes"):
        st.download_button(
            label="Download Base",
            data=st.session_state["cs_xlsx_bytes"],
            file_name=f"comportamento_suspeito_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="cs_download",
            width="stretch",
        )

_per_ini = filt_start.strftime("%d/%m/%Y") if hasattr(filt_start, "strftime") else str(filt_start)
_per_fim = filt_end.strftime("%d/%m/%Y") if hasattr(filt_end, "strftime") else str(filt_end)
st.caption(
    f"Período: {_per_ini} → {_per_fim} · {n:,} linhas · {n_unique:,} transações únicas".replace(",", ".")
)

st.subheader("Visão geral")
gv_l, gv_r = st.columns(2)
with gv_l:
    if "BANCO_RECEBEDOR" in filtered.columns and "VALOR_RETIRADA" in filtered.columns and vol > 0:
        bank_df = (
            filtered.assign(_banco=filtered["BANCO_RECEBEDOR"].astype(str))
            .groupby("_banco", as_index=False)["VALOR_RETIRADA"]
            .sum()
            .rename(columns={"_banco": "BANCO_RECEBEDOR"})
        )
        bank_df = bank_df.sort_values("VALOR_RETIRADA", ascending=False).head(20)
        fig_bank = px.bar(
            bank_df,
            x="BANCO_RECEBEDOR",
            y="VALOR_RETIRADA",
            labels={"BANCO_RECEBEDOR": "Banco", "VALOR_RETIRADA": "Valor (R$)"},
        )
        fig_bank.update_traces(marker_color=_VIZ_PRIMARY)
        fig_bank.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color=_VIZ_PRIMARY,
            xaxis_tickangle=-35,
            showlegend=False,
            margin=dict(t=44, b=64, l=48, r=24),
            title=dict(text="Volume por banco", font=dict(size=14, color=_VIZ_PRIMARY)),
        )
        apply_plotly_readability(fig_bank, height=400)
        st.plotly_chart(fig_bank, width="stretch")
    else:
        st.caption("Sem dados de banco.")

with gv_r:
    hc_g = _df_hour_count(filtered, date_col)
    if not hc_g.empty and int(hc_g["count"].sum()) > 0:
        fig_hg = px.bar(hc_g, x="hora", y="count", labels={"hora": "Hora", "count": "Qtd."})
        fig_hg.update_traces(marker_color=_VIZ_SECONDARY)
        fig_hg.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color=_VIZ_PRIMARY,
            showlegend=False,
            margin=dict(t=44, b=48, l=48, r=24),
            title=dict(text="Transações por hora", font=dict(size=14, color=_VIZ_PRIMARY)),
        )
        apply_plotly_readability(fig_hg, height=400, histogram=True)
        st.plotly_chart(fig_hg, width="stretch")
    else:
        st.caption("Sem distribuição horária.")

if concentração_banco or alta_concentracao_conta:
    st.warning("Alta concentração detectada")
else:
    st.info("Comportamento distribuído")

if "RECEIVER_TIPO" in filtered.columns:
    _rt = (
        filtered["RECEIVER_TIPO"]
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({"NAN": "INDEFINIDO", "NONE": "INDEFINIDO", "": "INDEFINIDO"})
    )
else:
    _rt = pd.Series(["INDEFINIDO"] * len(filtered), index=filtered.index)

df_pf = filtered[_rt == "PF"].copy()
df_pj = filtered[_rt == "PJ"].copy()
df_boleto = filtered[filtered["IS_BOLETO"].eq(1)].copy() if "IS_BOLETO" in filtered.columns else pd.DataFrame()
df_demais = filtered[~_rt.isin(["PF", "PJ"])].copy()

tipo_comportamento_pf = _render_segmento_visual("Pessoa Física", df_pf, date_col)
tipo_comportamento_pj = _render_segmento_visual("Pessoa Jurídica", df_pj, date_col)
tipo_comportamento_boleto = _render_segmento_visual("Boletos", df_boleto, date_col)
if not df_demais.empty:
    _render_segmento_visual(
        "Demais recortes (tipo indefinido ou chave PIX sem 11/14 dígitos)",
        df_demais,
        date_col,
    )

st.subheader("Indicadores de risco")
susp_linhas = int(pd.to_numeric(filtered["SUSPEITA"], errors="coerce").fillna(0).eq(1).sum()) if "SUSPEITA" in filtered.columns else 0
flags = pd.DataFrame(
    {
        "Indicador": [
            "Transação suspeita (regra: valor alto e (noturno ou conta recorrente))",
            "Boleto preenchido",
            "Chave PIX de terceiro",
            "Saque noturno (0h–5h)",
            "Destinatário em conta recorrente",
            "Valor acima de R$ 3.000",
        ],
        "Qtd. linhas": [susp_linhas, boleto_n, terc_n, noturno_n, rep_n, val_alto_n],
        "% do período": [
            _row_pct(pd.to_numeric(filtered["SUSPEITA"], errors="coerce").fillna(0).eq(1), n)
            if "SUSPEITA" in filtered.columns
            else 0,
            _row_pct(filtered["IS_BOLETO"].eq(1), n) if "IS_BOLETO" in filtered.columns else 0,
            _row_pct(filtered["TERCEIRO"].eq(1), n) if "TERCEIRO" in filtered.columns else 0,
            _row_pct(filtered["SAQUE_NOTURNO"].eq(1), n) if "SAQUE_NOTURNO" in filtered.columns else 0,
            _row_pct(filtered["DESTINATARIO_REPETIDO"].eq(1), n) if "DESTINATARIO_REPETIDO" in filtered.columns else 0,
            _row_pct(filtered["VALOR_ALTO"].eq(1), n) if "VALOR_ALTO" in filtered.columns else 0,
        ],
    }
)
flags["% do período"] = flags["% do período"].map(lambda x: f"{x:.1f}%")
st.dataframe(
    add_placeholder_data_criacao_column(flags),
    width="stretch",
    hide_index=True,
    column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
)
