from pathlib import Path
import sys

# =========================
# FIX DEFINITIVO DE IMPORT
# =========================
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# =========================
# ENV
# =========================
try:
    from dotenv import load_dotenv
    _env = ROOT / ".env"
    if _env.exists():
        load_dotenv(_env)
    load_dotenv()
except ImportError:
    pass

import json
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from modules.auth import ensure_authenticated
from modules.data_loader import (
    _parse_timestamp_column,
    date_column_for_period_filter,
    filter_by_brazil_calendar_dates,
    load_processed_data,
    load_raw_data,
    save_processed_data,
)
from modules.date_session import CALENDAR_MAX, CALENDAR_MIN, sync_global_date_inputs
from modules.feature_engineering import add_features
from modules.risk_engine import calcular_score
from modules.table_columns import DATA_CRIACAO_COL, add_placeholder_data_criacao_column, format_data_criacao_series
from modules.month_filter import labels_and_ranges_for_months, year_months_br_from_utc_series
from modules.plotly_theme import METODO_COLOR_MAP, ZIG_PRIMARY, ZIG_SERIES_COLORS
from modules.ui import (
    apply_enterprise_theme,
    aplicar_estilo_global,
    apply_plotly_readability,
    format_brl,
    format_currency_columns,
    render_metabase_pipeline_bar,
)


def _metodo_pagamento_bucket(raw: object) -> str:
    """
    Classifica bucket de método no mesmo espírito do risk_engine (PIX = texto contém 'PIX').
    Ordem: BOLETO antes de substrings genéricas; depois PIX, TED.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return "OUTROS"
    t = str(raw).strip().upper()
    if not t or t in ("NAN", "NONE", "NAT"):
        return "OUTROS"
    if "BOLET" in t:
        return "BOLETO"
    if "PIX" in t:
        return "PIX"
    if "TED" in t:
        return "TED"
    return "OUTROS"


def _row_metodo_bucket_from_raw(espec_raw: str, gen_raw: str) -> str:
    """Une específico + genérico: BOLETO em qualquer coluna prevalece sobre PIX/TED."""
    buckets: list[str] = []
    for raw in (espec_raw, gen_raw):
        if raw is None:
            continue
        s = str(raw).strip()
        if not s or s.upper() in ("NAN", "NONE", "NAT", "<NA>"):
            continue
        buckets.append(_metodo_pagamento_bucket(s))
    if not buckets:
        return "OUTROS"
    if "BOLETO" in buckets:
        return "BOLETO"
    if "PIX" in buckets:
        return "PIX"
    if "TED" in buckets:
        return "TED"
    return buckets[-1]


def _aggregate_saque_metodo_bucket(series: pd.Series) -> str:
    """Prioridade BOLETO > PIX > TED no mesmo ID_SAQUE (várias linhas na base)."""
    seen: list[str] = []
    for b in series:
        sb = str(b)
        if sb not in seen:
            seen.append(sb)
    if not seen:
        return "OUTROS"
    if "BOLETO" in seen:
        return "BOLETO"
    if "PIX" in seen:
        return "PIX"
    if "TED" in seen:
        return "TED"
    return seen[0]


def build_metodo_resumo_dashboard(filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Distribuição por método **somente no recorte filtrado** (datas / mês do dashboard).

    - Não usa df global: recebe apenas `filtered`.
    - Se existir `ID_SAQUE`, agrega **uma linha por saque** (soma valor, um método),
      corrigindo PIX/qtd duplicadas quando a base traz múltiplas linhas por transação.
    - Usa **METODO_PAGAMENTO_ESPECIFICO** e **METODO_PAGAMENTO** juntos (BOLETO em qualquer um conta).
    - **INFO_BOLETO** preenchido força bucket BOLETO na linha, alinhado ao score.
    """
    cols_out = ["METODO_PAGAMENTO", "qtd_transacoes", "valor_total", "percentual", "label_pct"]
    if filtered.empty or "VALOR_RETIRADA" not in filtered.columns:
        return pd.DataFrame(columns=cols_out)

    work = filtered.copy()
    if "METODO_PAGAMENTO_ESPECIFICO" in work.columns:
        espec = work["METODO_PAGAMENTO_ESPECIFICO"].astype(str).str.strip()
    else:
        espec = pd.Series("", index=work.index, dtype=object)
    espec = espec.replace({"nan": "", "None": "", "NaT": "", "<NA>": ""})
    if "METODO_PAGAMENTO" in work.columns:
        gen = work["METODO_PAGAMENTO"].astype(str).str.strip().replace({"nan": "", "None": "", "NaT": "", "<NA>": ""})
    else:
        gen = pd.Series("", index=work.index, dtype=object)

    row_bucket = pd.Series(
        [_row_metodo_bucket_from_raw(e, g) for e, g in zip(espec, gen)],
        index=work.index,
        dtype=object,
    )
    if "INFO_BOLETO" in work.columns:
        info = work["INFO_BOLETO"].astype(str).str.strip().replace({"nan": "", "None": "", "NaT": "", "<NA>": ""})
        row_bucket = row_bucket.mask(info.str.len() > 0, "BOLETO")
    work["_metodo_bucket_row"] = row_bucket

    if "ID_SAQUE" in work.columns:
        metodo = work.groupby("ID_SAQUE", as_index=False).agg(
            METODO_PAGAMENTO=("_metodo_bucket_row", _aggregate_saque_metodo_bucket),
            VALOR_RETIRADA=("VALOR_RETIRADA", "sum"),
        )
    else:
        metodo = work[["_metodo_bucket_row", "VALOR_RETIRADA"]].rename(columns={"_metodo_bucket_row": "METODO_PAGAMENTO"})
    metodo_resumo = metodo.groupby("METODO_PAGAMENTO", as_index=False).agg(
        qtd_transacoes=("METODO_PAGAMENTO", "count"),
        valor_total=("VALOR_RETIRADA", "sum"),
    )
    for metodo in ("PIX", "TED", "BOLETO"):
        if metodo not in metodo_resumo["METODO_PAGAMENTO"].values:
            metodo_resumo = pd.concat(
                [metodo_resumo, pd.DataFrame({"METODO_PAGAMENTO": [metodo], "qtd_transacoes": [0], "valor_total": [0.0]})],
                ignore_index=True,
            )
    metodo_resumo = (
        metodo_resumo.set_index("METODO_PAGAMENTO")
        .reindex(["TED", "PIX", "BOLETO", "OUTROS"])
        .fillna(0)
        .reset_index()
    )
    total_metodo = float(metodo_resumo["valor_total"].sum())
    metodo_resumo["percentual"] = (metodo_resumo["valor_total"] / total_metodo * 100.0) if total_metodo > 0 else 0.0
    metodo_resumo["label_pct"] = metodo_resumo["percentual"].map(lambda p: f"{p:.1f}%")

    return metodo_resumo


def _qtd_saques_unicos(d: pd.DataFrame) -> int:
    """Contagem alinhada a uma linha por saque no dashboard."""
    if "ID_SAQUE" in d.columns:
        return int(d["ID_SAQUE"].nunique())
    return len(d)


def _fmt_int_br(n: int) -> str:
    return f"{n:,}".replace(",", ".")


@st.cache_data(show_spinner=False)
def _cached_mirror_export_from_planilhas(
    path_resolved: str,
    mtime: float,
    filt_start_iso: str,
    filt_end_iso: str,
) -> tuple[bytes | None, str | None, str | None]:
    """
    Lê planilhas_check_out (dtype=str), filtra por DATA_SOLICITACAO no intervalo do painel,
    exporta CSV UTF-8-SIG. Cache por arquivo + mtime + período. Sem dedupe nem score.
    """
    latest_file = Path(path_resolved)
    try:
        fs = date.fromisoformat(filt_start_iso)
        fe = date.fromisoformat(filt_end_iso)
    except ValueError:
        return None, None, "Datas de filtro inválidas."

    try:
        suf = latest_file.suffix.lower()
        if suf == ".csv":
            df_download = pd.read_csv(latest_file, dtype=str, low_memory=False)
        elif suf in (".xlsx", ".xls"):
            try:
                xsz = latest_file.stat().st_size
            except OSError:
                xsz = 0
            # Excel grande trava o painel por vários minutos; exige CSV leve ou saques.csv gerado na importação.
            if xsz > 15 * 1024 * 1024:
                return (
                    None,
                    None,
                    "Arquivo Excel em planilhas_check_out é grande demais para o download automático no painel. "
                    "Coloque uma cópia em CSV na pasta ou use a base já exportada em data/uploaded/saques.csv (importação).",
                )
            df_download = pd.read_excel(latest_file, dtype=str)
        else:
            return None, None, f"Formato não suportado para download: {suf} (use .csv ou .xlsx/.xls)."
    except Exception as e:
        return None, None, f"Erro ao ler arquivo: {e}"

    df_download.columns = [str(c).strip().upper() for c in df_download.columns]
    if "DATA_SOLICITACAO" not in df_download.columns:
        return None, None, "Coluna DATA_SOLICITACAO não encontrada no arquivo."

    _dt = pd.to_datetime(df_download["DATA_SOLICITACAO"], errors="coerce")
    d_part = _dt.dt.date
    mask = (d_part >= fs) & (d_part <= fe)
    df_out = df_download.loc[mask].copy()

    print(f"Arquivo usado no download: {latest_file}")
    print(f"Filtro aplicado: {fs} até {fe}")
    print(f"Linhas após filtro: {len(df_out)}")

    csv_bytes = df_out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8")
    return csv_bytes, latest_file.stem, None


def _resolve_planilhas_check_out_download(filt_start: date, filt_end: date) -> tuple[bytes | None, Path | None, str | None, str | None]:
    """
    Arquivo mais recente em planilhas_check_out + export filtrado pelo mesmo período do dashboard.
    """
    folder = Path(__file__).resolve().parent / "planilhas_check_out"
    if not folder.is_dir():
        return None, None, None, f"Pasta não encontrada: {folder}"
    candidates = [p for p in folder.glob("*.*") if p.is_file() and not p.name.startswith(".")]
    if not candidates:
        return None, None, None, f"Nenhum arquivo em {folder}"

    latest_file = max(candidates, key=lambda p: p.stat().st_mtime)
    export_file = latest_file
    try:
        mtime_src = latest_file.stat().st_mtime
        sz = latest_file.stat().st_size
    except OSError:
        return None, latest_file, latest_file.stem, "Não foi possível ler metadados do arquivo."

    # Excel grande: usar saques.csv (mesma base, leitura muito mais rápida) para não travar o dashboard.
    if latest_file.suffix.lower() in (".xlsx", ".xls") and sz > 15 * 1024 * 1024:
        fb = Path(__file__).resolve().parent / "data" / "uploaded" / "saques.csv"
        if fb.is_file():
            export_file = fb
            try:
                mtime_src = fb.stat().st_mtime
            except OSError:
                mtime_src = 0.0
        else:
            return (
                None,
                latest_file,
                latest_file.stem,
                "O Excel em planilhas_check_out é grande demais para gerar o download aqui e não há "
                "data/uploaded/saques.csv. Rode a importação da planilha ou coloque um CSV na pasta.",
            )

    resolved = str(export_file.resolve())
    csv_bytes, stem, err = _cached_mirror_export_from_planilhas(
        resolved,
        mtime_src,
        filt_start.isoformat(),
        filt_end.isoformat(),
    )
    return csv_bytes, latest_file, stem, err


@st.cache_data(show_spinner=False, max_entries=6)
def _dashboard_cached_processed(resolved: str, mtime: float) -> pd.DataFrame:
    """Cache do CSV processado por caminho + mtime (invalida ao salvar nova base)."""
    from pathlib import Path

    from modules.data_loader import read_processed_from_disk

    return read_processed_from_disk(Path(resolved))


def _load_processed_for_dashboard() -> pd.DataFrame:
    """Lê base processada com st.cache_data (reruns e navegação mais rápidos)."""
    from modules.data_loader import PROCESSED_PATH

    if not PROCESSED_PATH.exists():
        return pd.DataFrame()
    try:
        mt = PROCESSED_PATH.stat().st_mtime
    except OSError:
        return pd.DataFrame()
    return _dashboard_cached_processed(str(PROCESSED_PATH.resolve()), mt)


def _load_data():
    """
    Prioriza `data/processed/saques_processado.csv` (rápido, sem rede).
    Metabase/CSV upload só roda se não houver processado ou com ZIG_ALWAYS_REFRESH_METABASE=1.
    Imediatamente após 'Executar agora', usa o processado recém-gravado.
    """
    import os

    if st.session_state.get("use_processed_data_next_load"):
        st.session_state["use_processed_data_next_load"] = False
        df = _load_processed_for_dashboard()
        if not df.empty:
            return df
        df = load_processed_data()
        if not df.empty:
            return df
    live = (os.getenv("ZIG_ALWAYS_REFRESH_METABASE") or "").strip().lower() in ("1", "true", "yes", "on")
    if not live:
        df = _load_processed_for_dashboard()
        if not df.empty:
            return df
        df = load_processed_data()
        if not df.empty:
            return df
    raw = load_raw_data()
    if raw.empty:
        return raw
    scored = calcular_score(add_features(raw))
    save_processed_data(scored)
    return scored


st.set_page_config(
    page_title="ZIG Risk Monitor",
    page_icon=":bar_chart:",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
<style>
.main .block-container {
    max-width: 1400px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-left: 24px !important;
    padding-right: 24px !important;
}
</style>
""",
    unsafe_allow_html=True,
)

apply_enterprise_theme()
aplicar_estilo_global()
st.markdown(
    """
    <style>
    .dash-filter-row { background: #ffffff; border: 1px solid #dbe3f0; border-radius: 12px; padding: 10px 12px 2px 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.06); }
    .dash-section-title { color: #0047BB; font-weight: 600; font-size: 18px; margin: 0 0 8px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Topo fixo: aparece logo (antes de ler a base), para a página não parecer “travada”.
top_banner = st.empty()


def _render_top_banner() -> None:
    with top_banner.container():
        col_title, col_pipeline = st.columns([0.4, 0.6])
        with col_title:
            st.markdown('<div class="zr-title">ZIG Risk Monitor</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="zr-muted">Sistema inteligente de monitoramento de saques e pagamentos suspeitos.</div>',
                unsafe_allow_html=True,
            )
        with col_pipeline:
            render_metabase_pipeline_bar()


_render_top_banner()

# --- Dados: spinner explícito + cache na leitura do processado ---
with st.spinner("Carregando dados…"):
    df = _load_data()
if df.empty:
    st.info("Nenhuma planilha carregada. Use a página `Admin Upload` para enviar dados.")
    st.stop()

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

sync_global_date_inputs(data_min, data_max, default_last_n_days=30)

yms = year_months_br_from_utc_series(df[date_col].dropna()) if date_col in df.columns else []
if not yms:
    y_min, m_min = data_min.year, data_min.month
    y_max, m_max = data_max.year, data_max.month
    yms = []
    cy, cm = y_min, m_min
    while (cy, cm) <= (y_max, m_max):
        yms.append((cy, cm))
        if (cy, cm) == (y_max, m_max):
            break
        cm += 1
        if cm > 12:
            cm, cy = 1, cy + 1
    yms = sorted(set(yms), reverse=True)

mes_labels, mes_ranges = labels_and_ranges_for_months(yms, CALENDAR_MIN, CALENDAR_MAX)

# Container nativo: evita <div> via markdown (Streamlit pode sanitizar </div> e “sumir” o restante da página).
with st.container():
    st.markdown("**Filtros**")
    col_mes, col_a, col_b = st.columns([1, 1, 1])
    with col_mes:
        mes_idx = st.selectbox(
            "Ou selecionar mês",
            range(len(mes_labels)),
            format_func=lambda i: mes_labels[i],
            key="dash_mes_idx_v2",
        )

    if mes_idx > 0 and mes_idx - 1 < len(mes_ranges):
        d1, d2 = mes_ranges[mes_idx - 1]
        st.session_state.data_inicial = d1
        st.session_state.data_final = d2

    with col_a:
        data_inicial = st.date_input(
            "Data inicial",
            min_value=CALENDAR_MIN,
            max_value=CALENDAR_MAX,
            format="DD/MM/YYYY",
            key="data_inicial",
        )
    with col_b:
        data_final = st.date_input(
            "Data final",
            min_value=CALENDAR_MIN,
            max_value=CALENDAR_MAX,
            format="DD/MM/YYYY",
            key="data_final",
        )

data_inicial = max(CALENDAR_MIN, min(data_inicial, CALENDAR_MAX))
data_final = max(CALENDAR_MIN, min(data_final, CALENDAR_MAX))

if not st.session_state.get("_dash_place_transacao_filters_removed_logged"):
    st.session_state._dash_place_transacao_filters_removed_logged = True
    print("Filtros NOME_PLACE e ID_TRANSACAO removidos do Dashboard")

# Prioridade: mês selecionado (índice) > datas manuais
if mes_idx > 0 and mes_idx - 1 < len(mes_ranges):
    filt_start, filt_end = mes_ranges[mes_idx - 1]
else:
    filt_start, filt_end = data_inicial, data_final

if filt_start > filt_end:
    filt_start, filt_end = filt_end, filt_start

filtered = filter_by_brazil_calendar_dates(df, date_col, filt_start, filt_end)

_qtd_dash = _qtd_saques_unicos(filtered)
_qtd_base = _qtd_saques_unicos(df)
st.markdown(
    f"<h2 style='color:#1f77b4;margin:0.35rem 0 0.75rem 0;'>Qtd registros (período): {_fmt_int_br(_qtd_dash)}</h2>",
    unsafe_allow_html=True,
)
st.caption(
    f"Saques únicos na base carregada: {_fmt_int_br(_qtd_base)} · "
    f"Recorte por **{date_col}**: {filt_start.strftime('%d/%m/%Y')} a {filt_end.strftime('%d/%m/%Y')} (calendário Brasil). "
    "A ingestão tenta export CSV automático após o JSON (teto típico ~2k no JSON). Desligar: METABASE_SKIP_CSV_EXPORT=1."
)
if not st.session_state.get("_dash_qtd_registros_destaque_logged"):
    st.session_state._dash_qtd_registros_destaque_logged = True
    print("Datas removidas e Qtd registros destacada")

if filtered.empty:
    st.warning("Nenhum registro encontrado para os filtros selecionados.")
    st.stop()

with st.expander("Debug — validação pós-filtro (colunas e métodos)", expanded=False):
    st.write("**Colunas do recorte:**")
    st.write(list(filtered.columns))
    st.write(f"**Coluna de data usada no filtro:** `{date_col}`")
    st.write("**Dados após filtro — METODO_PAGAMENTO:**")
    if "METODO_PAGAMENTO" in filtered.columns:
        _vc_m = filtered["METODO_PAGAMENTO"].value_counts(dropna=False).reset_index()
        _vc_m.columns = ["METODO_PAGAMENTO", "qtd"]
        st.dataframe(
            add_placeholder_data_criacao_column(_vc_m),
            width="stretch",
            hide_index=True,
            column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
        )
    else:
        st.caption("Coluna METODO_PAGAMENTO ausente.")
    st.write("**Dados após filtro — METODO_PAGAMENTO_ESPECIFICO:**")
    if "METODO_PAGAMENTO_ESPECIFICO" in filtered.columns:
        _vc_e = filtered["METODO_PAGAMENTO_ESPECIFICO"].value_counts(dropna=False).reset_index()
        _vc_e.columns = ["METODO_PAGAMENTO_ESPECIFICO", "qtd"]
        st.dataframe(
            add_placeholder_data_criacao_column(_vc_e),
            width="stretch",
            hide_index=True,
            column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
        )
    else:
        st.caption("Coluna METODO_PAGAMENTO_ESPECIFICO ausente.")
    if "METODO_PAGAMENTO" in filtered.columns and "VALOR_RETIRADA" in filtered.columns:
        st.write("**Soma VALOR_RETIRADA por METODO_PAGAMENTO (linhas do recorte, sem agregar ID_SAQUE):**")
        _dbg_met = (
            filtered.groupby("METODO_PAGAMENTO", dropna=False)["VALOR_RETIRADA"]
            .sum()
            .reset_index()
            .rename(columns={"VALOR_RETIRADA": "valor_total"})
        )
        st.dataframe(
            add_placeholder_data_criacao_column(_dbg_met),
            width="stretch",
            hide_index=True,
            column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
        )

if "HORA_TRANSACAO" not in filtered.columns and "DATA_SOLICITACAO" in filtered.columns:
    filtered = filtered.copy()
    filtered["HORA_TRANSACAO"] = pd.to_datetime(filtered["DATA_SOLICITACAO"], errors="coerce").dt.hour.fillna(0).astype(int)

# Indicadores de risco e comportamento (df_filtrado = filtered)
df_filtrado = filtered
total_transacoes = _qtd_saques_unicos(df_filtrado)
# Valores numéricos explícitos (evita .mean() em object / escala errada)
_valores = (
    pd.to_numeric(df_filtrado["VALOR_RETIRADA"], errors="coerce")
    if "VALOR_RETIRADA" in df_filtrado.columns
    else pd.Series(dtype=float)
)
volume_total = float(_valores.sum()) if len(_valores) else 0.0
# Ticket médio = SOMA(VALOR) / QTD saques únicos (ID_SAQUE)
if total_transacoes and "VALOR_RETIRADA" in df_filtrado.columns:
    ticket_medio = round(volume_total / total_transacoes, 2)
else:
    ticket_medio = 0.0
# Suspeitas pela regra oficial (SUSPEITA==1), não por faixa de RISK_SCORE / RISK_LEVEL.
if "SUSPEITA" in df_filtrado.columns and "ID_SAQUE" in df_filtrado.columns:
    total_suspeitas = int(df_filtrado.groupby("ID_SAQUE", dropna=False)["SUSPEITA"].max().sum())
elif "SUSPEITA" in df_filtrado.columns:
    total_suspeitas = int(pd.to_numeric(df_filtrado["SUSPEITA"], errors="coerce").fillna(0).eq(1).sum())
else:
    total_suspeitas = 0
perc_suspeitas = (total_suspeitas / total_transacoes * 100) if total_transacoes > 0 else 0.0
sus_mask = pd.to_numeric(df_filtrado["SUSPEITA"], errors="coerce").fillna(0).eq(1) if "SUSPEITA" in df_filtrado.columns else pd.Series(False, index=df_filtrado.index)
contas_suspeitas = (
    int(df_filtrado.loc[sus_mask, "CONTA_RECEBEDOR"].nunique())
    if "CONTA_RECEBEDOR" in df_filtrado.columns and sus_mask.any()
    else 0
)
dest_repetidos = df_filtrado[df_filtrado["DESTINATARIO_REPETIDO"] == 1]["CONTA_RECEBEDOR"].nunique() if "DESTINATARIO_REPETIDO" in df_filtrado.columns and "CONTA_RECEBEDOR" in df_filtrado.columns else 0

c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    st.metric("Volume financeiro total", format_brl(volume_total))
with c2:
    st.metric("Percentual de transações suspeitas", f"{perc_suspeitas:.2f}%")
with c3:
    st.metric("Transações suspeitas (SUSPEITA)", f"{total_suspeitas:,}".replace(",", "."))
with c4:
    st.metric("Contas suspeitas", f"{contas_suspeitas:,}".replace(",", "."))
with c5:
    st.metric("Destinatários recorrentes", f"{dest_repetidos:,}".replace(",", "."))
with c6:
    st.metric("Ticket médio", format_brl(ticket_medio))

st.subheader("Evolução financeira por ano")
# Base sem duplicidade por ID_SAQUE (filtro de data já aplicado em filtered).
_evol_cols = [date_col, "VALOR_RETIRADA"]
if "ID_SAQUE" in filtered.columns:
    df_evol = (
        filtered.groupby("ID_SAQUE", as_index=False)
        .agg(VALOR_RETIRADA=("VALOR_RETIRADA", "max"), **{date_col: (date_col, "first")})
        .reset_index(drop=True)
    )
elif all(c in filtered.columns for c in _evol_cols):
    df_evol = filtered[_evol_cols].copy()
else:
    df_evol = pd.DataFrame(columns=[date_col, "VALOR_RETIRADA"])

if not df_evol.empty and "VALOR_RETIRADA" in df_evol.columns:
    df_evol[date_col] = _parse_timestamp_column(df_evol[date_col])
    df_evol = df_evol.dropna(subset=[date_col])
    sdt = df_evol[date_col].dt.tz_convert("America/Sao_Paulo")
    df_evol["ANO"] = sdt.dt.year
    df_evol["MES"] = sdt.dt.month
    df_group = df_evol.groupby(["ANO", "MES"], as_index=False)["VALOR_RETIRADA"].sum()
else:
    df_group = pd.DataFrame(columns=["ANO", "MES", "VALOR_RETIRADA"])

meses_short = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

if not df_group.empty:
    anos = sorted({int(a) for a in df_group["ANO"].dropna().unique().tolist()})
    if not anos:
        st.info(
            f"Sem datas válidas para montar ano/mês na evolução (eixo temporal: **{date_col}** no recorte)."
        )
    else:
        meses = pd.DataFrame({"MES": list(range(1, 13))})
        grid = pd.merge(pd.DataFrame({"ANO": anos}), meses, how="cross")
        df_g = df_group.copy()
        df_g["ANO"] = df_g["ANO"].astype(int)
        df_g["MES"] = df_g["MES"].astype(int)
        df_full = grid.merge(df_g, on=["ANO", "MES"], how="left")
        df_full["VALOR_RETIRADA"] = df_full["VALOR_RETIRADA"].fillna(0.0)
        df_full = df_full.sort_values(["ANO", "MES"])
        df_full["MES_NOME"] = df_full["MES"].map(lambda m: meses_short[int(m) - 1] if 1 <= m <= 12 else str(m))

        fig_trend = px.line(
            df_full,
            x="MES_NOME",
            y="VALOR_RETIRADA",
            color="ANO",
            markers=True,
            labels={"VALOR_RETIRADA": "Valor (R$)", "MES_NOME": "Mês", "ANO": "Ano"},
            category_orders={"MES_NOME": meses_short},
            color_discrete_sequence=list(ZIG_SERIES_COLORS),
        )
        fig_trend.update_layout(
            title=None,
            margin=dict(t=24, b=60),
            xaxis_tickangle=-45,
            legend_title="Ano",
            legend=dict(font=dict(color=ZIG_PRIMARY, size=16)),
        )
        fig_trend.update_traces(
            line=dict(width=3),
            opacity=1,
            marker=dict(size=11, opacity=1, line=dict(width=2, color="#FFFFFF")),
        )
        apply_plotly_readability(fig_trend, zig_brand_layout=True)
        st.plotly_chart(fig_trend, width="stretch")
        if not st.session_state.get("_dash_evol_ano_title_dedup_logged"):
            st.session_state._dash_evol_ano_title_dedup_logged = True
            print("Título duplicado removido")
else:
    st.info("Sem dados para exibir a evolução financeira por ano.")

st.markdown('<div class="dash-section-title">Distribuição por método de pagamento</div>', unsafe_allow_html=True)
st.caption(
    "Valores e quantidades no **período filtrado**; cada transação conta uma vez (por **ID_SAQUE**, quando existir). "
    "BOLETO se **qualquer** coluna (específico ou genérico) indicar boleto, ou se **INFO_BOLETO** estiver preenchido; "
    "PIX/TED seguem texto (como no score)."
)
metodo_resumo = build_metodo_resumo_dashboard(filtered)
_ordem_metodo = ["TED", "PIX", "BOLETO", "OUTROS"]
if not metodo_resumo.empty and float(metodo_resumo["valor_total"].sum()) > 0:
    # Ordem fixa no eixo X: sem isso, Plotly ordena as categorias (ex.: alfabético) e o
    # customdata linha-a-linha fica desalinhado — tooltip de PIX mostrava qtd/% do TED.
    fig_metodo = px.bar(
        metodo_resumo,
        x="METODO_PAGAMENTO",
        y="valor_total",
        color="METODO_PAGAMENTO",
        color_discrete_map=METODO_COLOR_MAP,
        text="label_pct",
        custom_data=["qtd_transacoes", "percentual"],
        category_orders={"METODO_PAGAMENTO": _ordem_metodo},
    )
    fig_metodo.update_layout(
        yaxis_title="Valor (R$)",
        xaxis_title=None,
        showlegend=False,
        xaxis={"categoryorder": "array", "categoryarray": _ordem_metodo},
    )
    fig_metodo.update_traces(
        opacity=1,
        marker_line_width=0,
        textfont=dict(color="#FFFFFF", size=16),
        hovertemplate=(
            "Método: %{x}<br>Valor: R$ %{y:,.2f}<br>"
            "Qtd: %{customdata[0]}<br>Participação: %{customdata[1]:.2f}%<extra></extra>"
        ),
    )
    apply_plotly_readability(fig_metodo, zig_brand_layout=True)
    st.plotly_chart(fig_metodo, width="stretch")
else:
    st.info("Sem dados para distribuição por método de pagamento no período.")

st.markdown('<div class="dash-section-title">Top bancos recebedores</div>', unsafe_allow_html=True)

if "BANCO_RECEBEDOR" not in filtered.columns or "VALOR_RETIRADA" not in filtered.columns:
    st.info("Colunas BANCO_RECEBEDOR e/ou VALOR_RETIRADA indisponíveis para estes gráficos.")
else:
    _bank_base = filtered.copy()
    _br_lbl = _bank_base["BANCO_RECEBEDOR"].fillna("").astype(str).str.strip()
    _br_lbl = _br_lbl.replace({"nan": "", "None": "", "NaT": "", "<NA>": ""})
    _br_lbl = _br_lbl.mask(_br_lbl.eq(""), "Não informado")
    _bank_base["BANCO_RECEBEDOR_lbl"] = _br_lbl

    total_registros_recorte = len(_bank_base)
    top_bank_all = (
        _bank_base.groupby("BANCO_RECEBEDOR_lbl", as_index=False)["VALOR_RETIRADA"]
        .sum()
        .sort_values("VALOR_RETIRADA", ascending=False)
        .rename(columns={"BANCO_RECEBEDOR_lbl": "BANCO_RECEBEDOR"})
    )
    cnt_por_banco = (
        _bank_base.groupby("BANCO_RECEBEDOR_lbl", as_index=False)
        .agg(qtd_transacoes=("BANCO_RECEBEDOR_lbl", "count"))
        .sort_values("qtd_transacoes", ascending=False)
        .rename(columns={"BANCO_RECEBEDOR_lbl": "BANCO_RECEBEDOR"})
    )

    if top_bank_all.empty:
        st.info("Sem dados de bancos recebedores.")
    else:
        top5_val = top_bank_all.head(5)
        rest_val = top_bank_all.iloc[5:]
        top_bank = (
            pd.concat(
                [
                    top5_val,
                    pd.DataFrame([{"BANCO_RECEBEDOR": "Outros", "VALOR_RETIRADA": rest_val["VALOR_RETIRADA"].sum()}]),
                ],
                ignore_index=True,
            )
            if not rest_val.empty
            else top5_val
        )

        top5_qtd = cnt_por_banco.head(5)
        rest_qtd = cnt_por_banco.iloc[5:]
        bar_bancos = (
            pd.concat(
                [
                    top5_qtd,
                    pd.DataFrame(
                        [{"BANCO_RECEBEDOR": "Outros", "qtd_transacoes": int(rest_qtd["qtd_transacoes"].sum())}]
                    ),
                ],
                ignore_index=True,
            )
            if not rest_qtd.empty
            else top5_qtd
        )
        # % transações = DIVIDE(COUNT por banco, COUNT total no recorte filtrado)
        bar_bancos = bar_bancos.copy()
        bar_bancos["pct_transacoes"] = (
            (bar_bancos["qtd_transacoes"] / total_registros_recorte * 100.0) if total_registros_recorte > 0 else 0.0
        )
        bar_bancos["label_bar"] = bar_bancos.apply(
            lambda r: f"{int(r['qtd_transacoes']):,}".replace(",", ".") + f" · {r['pct_transacoes']:.1f}%",
            axis=1,
        )
        _ordem_bar = bar_bancos["BANCO_RECEBEDOR"].tolist()

        col_pie_banco, col_bar_banco = st.columns(2)
        with col_pie_banco:
            fig_bank = px.pie(
                top_bank,
                names="BANCO_RECEBEDOR",
                values="VALOR_RETIRADA",
                hole=0.35,
                color_discrete_sequence=list(ZIG_SERIES_COLORS),
            )
            fig_bank.update_layout(margin=dict(l=0, r=0, t=0, b=0))
            fig_bank.update_traces(
                opacity=1,
                marker=dict(line=dict(color="#FFFFFF", width=2)),
                textfont=dict(size=14, color="#FFFFFF"),
                textposition="inside",
            )
            apply_plotly_readability(fig_bank, zig_brand_layout=True, skip_axes=True)
            st.plotly_chart(fig_bank, width="stretch")
        with col_bar_banco:
            fig_bar_banco = px.bar(
                bar_bancos,
                x="BANCO_RECEBEDOR",
                y="qtd_transacoes",
                text="label_bar",
                custom_data=["pct_transacoes"],
                category_orders={"BANCO_RECEBEDOR": _ordem_bar},
            )
            fig_bar_banco.update_layout(
                title=dict(
                    text="<b>Top bancos recebedores por quantidade de transações</b>",
                    font=dict(color=ZIG_PRIMARY, size=16),
                ),
                xaxis_title="Banco recebedor",
                yaxis_title="Qtd. transações",
                showlegend=False,
                xaxis={"categoryorder": "array", "categoryarray": _ordem_bar},
                margin=dict(t=56, b=88, l=48, r=24),
            )
            fig_bar_banco.update_traces(
                marker_color=ZIG_PRIMARY,
                opacity=1,
                marker_line_width=0,
                textfont=dict(color=ZIG_PRIMARY, size=14),
                textposition="outside",
                hovertemplate="Banco: %{x}<br>Qtd. transações: %{y}<br>% participação (qtd): %{customdata[0]:.2f}%<extra></extra>",
            )
            apply_plotly_readability(fig_bar_banco, zig_brand_layout=True)
            st.plotly_chart(fig_bar_banco, width="stretch")

st.markdown('<div class="dash-section-title">Top places por volume financeiro</div>', unsafe_allow_html=True)
places_vol = filtered.groupby("NOME_PLACE", as_index=False)["VALOR_RETIRADA"].sum().sort_values("VALOR_RETIRADA", ascending=True).tail(15)
if not places_vol.empty:
    fig_places = px.bar(
        places_vol,
        x="VALOR_RETIRADA",
        y="NOME_PLACE",
        orientation="h",
        labels={"VALOR_RETIRADA": "Valor (R$)", "NOME_PLACE": "Place"},
    )
    fig_places.update_layout(
        margin=dict(l=120, b=80, r=40),
        uniformtext_minsize=10,
        uniformtext_mode="hide",
    )
    apply_plotly_readability(fig_places, horizontal_bar=True, zig_brand_layout=True)
    fig_places.update_traces(
        marker_color=ZIG_PRIMARY,
        opacity=1,
        marker_line_width=0,
        texttemplate="R$ %{x:,.0f}",
        textposition="inside",
        textfont=dict(size=14, color="#FFFFFF"),
    )
    st.plotly_chart(fig_places, width="stretch")
else:
    st.info("Sem dados para ranking de places.")

def _parse_receiver_nome_cnpj(val):
    """Extrai nome e CPF/CNPJ do campo RECEIVER (string ou JSON como {"nome":"...","cnpj":"..."})."""
    if pd.isna(val) or val == "":
        return "Não informado", "Não informado"
    s = str(val).strip()
    if not s or s == "nan":
        return "Não informado", "Não informado"
    if s.startswith("{"):
        try:
            d = json.loads(s) if isinstance(s, str) else s
            nome = (d.get("nome") or d.get("name") or "").strip() or "Não informado"
            doc = (d.get("cnpj") or d.get("cpf") or d.get("doc") or "").strip() or "Não informado"
            return nome, doc
        except (json.JSONDecodeError, TypeError, AttributeError):
            return s, "Não informado"
    return s, "Não informado"

st.markdown('<div class="dash-section-title">Top contas recebedoras</div>', unsafe_allow_html=True)
top_contas = filtered.groupby("CONTA_RECEBEDOR", as_index=False)["VALOR_RETIRADA"].sum().sort_values("VALOR_RETIRADA", ascending=False).head(15)
if not top_contas.empty:
    # Dados do recebedor: parse de RECEIVER (string ou JSON) para nome e cpf_cnpj
    if "RECEIVER" in filtered.columns:
        rec_first = filtered.groupby("CONTA_RECEBEDOR", as_index=False)["RECEIVER"].first()
        parsed = rec_first["RECEIVER"].map(_parse_receiver_nome_cnpj)
        rec_first["nome_recebedor"] = [p[0] for p in parsed]
        rec_first["cpf_cnpj_recebedor"] = [p[1] for p in parsed]
        agg_recebedor = rec_first[["CONTA_RECEBEDOR", "nome_recebedor", "cpf_cnpj_recebedor"]].copy()
    else:
        agg_recebedor = top_contas[["CONTA_RECEBEDOR"]].copy()
        agg_recebedor["nome_recebedor"] = "Não informado"
        agg_recebedor["cpf_cnpj_recebedor"] = "Não informado"

    # Remetente principal por conta (maior valor agregado por CONTA_RECEBEDOR + remetente)
    remetente_nome_col = "NOME_ORGANIZACAO" if "NOME_ORGANIZACAO" in filtered.columns else ("NOME_PLACE" if "NOME_PLACE" in filtered.columns else None)
    if remetente_nome_col:
        remetente_val = filtered.groupby(["CONTA_RECEBEDOR", remetente_nome_col], as_index=False)["VALOR_RETIRADA"].sum()
        idx_max = remetente_val.groupby("CONTA_RECEBEDOR")["VALOR_RETIRADA"].idxmax()
        main_remetente = remetente_val.loc[idx_max, ["CONTA_RECEBEDOR", remetente_nome_col]].copy()
        if "CNPJ" in filtered.columns:
            cnpj_rem = filtered[["CONTA_RECEBEDOR", remetente_nome_col, "CNPJ"]].drop_duplicates(subset=["CONTA_RECEBEDOR", remetente_nome_col])
            main_remetente = main_remetente.merge(cnpj_rem, on=["CONTA_RECEBEDOR", remetente_nome_col], how="left")
            main_remetente["cpf_cnpj_remetente"] = main_remetente["CNPJ"].fillna("Não informado").astype(str).str.strip().replace("", "Não informado").replace("nan", "Não informado")
        else:
            main_remetente["cpf_cnpj_remetente"] = "Não informado"
        main_remetente = main_remetente.rename(columns={remetente_nome_col: "nome_remetente"})[["CONTA_RECEBEDOR", "nome_remetente", "cpf_cnpj_remetente"]]
    else:
        main_remetente = top_contas[["CONTA_RECEBEDOR"]].copy()
        main_remetente["nome_remetente"] = "Não informado"
        main_remetente["cpf_cnpj_remetente"] = "Não informado"

    # Banco recebedor por conta: no recorte filtrado, o banco com maior soma de VALOR_RETIRADA (empate → primeira linha do idxmax)
    if "BANCO_RECEBEDOR" in filtered.columns:
        _bk = filtered[["CONTA_RECEBEDOR", "BANCO_RECEBEDOR", "VALOR_RETIRADA"]].copy()
        _bk["BANCO_RECEBEDOR"] = _bk["BANCO_RECEBEDOR"].fillna("").astype(str).str.strip()
        _bk["BANCO_RECEBEDOR"] = _bk["BANCO_RECEBEDOR"].replace({"nan": "", "None": "", "NaT": "", "<NA>": ""})
        _bk.loc[_bk["BANCO_RECEBEDOR"].eq(""), "BANCO_RECEBEDOR"] = "Não informado"
        bank_por_conta = (
            _bk.groupby(["CONTA_RECEBEDOR", "BANCO_RECEBEDOR"], as_index=False)["VALOR_RETIRADA"].sum()
        )
        idx_b = bank_por_conta.groupby("CONTA_RECEBEDOR")["VALOR_RETIRADA"].idxmax()
        bank_por_conta = bank_por_conta.loc[idx_b, ["CONTA_RECEBEDOR", "BANCO_RECEBEDOR"]]
    else:
        bank_por_conta = top_contas[["CONTA_RECEBEDOR"]].copy()
        bank_por_conta["BANCO_RECEBEDOR"] = "Não informado"

    top_contas_display = (
        top_contas.rename(columns={"CONTA_RECEBEDOR": "conta", "VALOR_RETIRADA": "valor_recebido"})
        .merge(agg_recebedor.rename(columns={"CONTA_RECEBEDOR": "conta"})[["conta", "nome_recebedor", "cpf_cnpj_recebedor"]], on="conta", how="left")
        .merge(bank_por_conta.rename(columns={"CONTA_RECEBEDOR": "conta"}), on="conta", how="left")
        .merge(main_remetente.rename(columns={"CONTA_RECEBEDOR": "conta"})[["conta", "nome_remetente", "cpf_cnpj_remetente"]], on="conta", how="left")
    )
    top_contas_display["nome_recebedor"] = top_contas_display["nome_recebedor"].fillna("Não informado").astype(str).str.strip()
    top_contas_display["cpf_cnpj_recebedor"] = top_contas_display["cpf_cnpj_recebedor"].fillna("Não informado").astype(str).str.strip()
    top_contas_display["BANCO_RECEBEDOR"] = top_contas_display["BANCO_RECEBEDOR"].fillna("Não informado").astype(str).str.strip()
    top_contas_display["nome_remetente"] = top_contas_display["nome_remetente"].fillna("Não informado").astype(str).str.strip()
    top_contas_display["cpf_cnpj_remetente"] = top_contas_display["cpf_cnpj_remetente"].fillna("Não informado").astype(str).str.strip()
    if DATA_CRIACAO_COL in filtered.columns:
        dc_agg = filtered.groupby("CONTA_RECEBEDOR", as_index=False)[DATA_CRIACAO_COL].first()
        dc_agg = dc_agg.rename(columns={"CONTA_RECEBEDOR": "conta"})
        top_contas_display = top_contas_display.merge(dc_agg, on="conta", how="left")
        top_contas_display[DATA_CRIACAO_COL] = format_data_criacao_series(top_contas_display[DATA_CRIACAO_COL])
    else:
        top_contas_display[DATA_CRIACAO_COL] = "—"
    top_contas_display = top_contas_display[
        [
            DATA_CRIACAO_COL,
            "conta",
            "nome_recebedor",
            "cpf_cnpj_recebedor",
            "BANCO_RECEBEDOR",
            "valor_recebido",
            "nome_remetente",
            "cpf_cnpj_remetente",
        ]
    ]
    top_contas_display = format_currency_columns(top_contas_display, ["valor_recebido"])
    st.dataframe(
        add_placeholder_data_criacao_column(top_contas_display),
        width="stretch",
        hide_index=True,
        height=360,
        column_config={
            DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium"),
        },
    )
else:
    st.info("Sem dados de contas recebedoras.")

st.markdown('<div class="dash-section-title">Distribuição de transações por hora</div>', unsafe_allow_html=True)
hora_col = "HORA_TRANSACAO" if "HORA_TRANSACAO" in filtered.columns else None
if hora_col:
    by_hour = filtered.groupby(hora_col, as_index=False).size()
    by_hour = by_hour.rename(columns={hora_col: "hora", "size": "quantidade"})
    fig_hour = px.bar(by_hour, x="hora", y="quantidade", labels={"hora": "Hora do dia", "quantidade": "Nº de transações"})
    fig_hour.update_layout(margin=dict(t=24, b=40))
    fig_hour.update_traces(
        marker_color=ZIG_PRIMARY,
        opacity=1,
        marker_line_width=0,
    )
    apply_plotly_readability(fig_hour, histogram=True, zig_brand_layout=True)
    st.plotly_chart(fig_hour, width="stretch")
else:
    st.info("Coluna de hora não disponível.")

st.markdown('<div class="dash-section-title">Resumo por método de pagamento</div>', unsafe_allow_html=True)
metodo_view = metodo_resumo.copy()
metodo_view = metodo_view.rename(columns={"METODO_PAGAMENTO": "PAGAMENTO"})
metodo_view = format_currency_columns(metodo_view, ["valor_total"])
metodo_view["percentual"] = metodo_resumo["percentual"].map(lambda p: f"{p:.2f}%")
st.dataframe(
    add_placeholder_data_criacao_column(metodo_view[["PAGAMENTO", "qtd_transacoes", "valor_total", "percentual"]].copy()),
    width="stretch",
    hide_index=True,
    column_config={
        DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium"),
        "PAGAMENTO": st.column_config.TextColumn("PAGAMENTO", width="medium"),
        "qtd_transacoes": st.column_config.NumberColumn("Qtd. transações", width="small"),
        "valor_total": st.column_config.TextColumn("Valor total", width="medium"),
        "percentual": st.column_config.TextColumn("%", width="small"),
    },
)

# Download no sidebar por último: leitura/filtragem de CSV pode ser pesada; não bloqueia gráficos e KPIs acima.
with st.sidebar:
    csv_dl, _path_dl, stem_dl, download_err = _resolve_planilhas_check_out_download(filt_start, filt_end)
    if download_err:
        st.download_button(
            label="Download Base",
            data=b"",
            file_name=f"base_{date.today().isoformat()}.csv",
            mime="text/csv; charset=utf-8",
            key="sidebar_download_base",
            width="stretch",
            disabled=True,
        )
    else:
        out_stem = stem_dl or (_path_dl.stem if _path_dl else "base")
        out_name = f"{out_stem}_{date.today().isoformat()}.csv"
        st.download_button(
            label="Download Base",
            data=csv_dl or b"",
            file_name=out_name,
            mime="text/csv; charset=utf-8",
            key="sidebar_download_base",
            width="stretch",
        )
