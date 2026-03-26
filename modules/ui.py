from pathlib import Path
import json
import math

import streamlit as st
import pandas as pd

try:
    from modules.formatting import format_brl, format_metric_float
except ImportError:
    # Fallback: cópias sem modules.formatting (OneDrive/cópia parcial sem formatting.py)
    def format_brl(value: float) -> str:
        try:
            x = float(value)
        except (TypeError, ValueError):
            return "—"
        if not math.isfinite(x):
            return "—"
        return f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    def format_metric_float(value: float, spec: str = ".1f") -> str:
        try:
            x = float(value)
        except (TypeError, ValueError):
            return "—"
        if not math.isfinite(x):
            return "—"
        return format(x, spec)


PIPELINE_STATUS_FILE = Path("logs/pipeline_status.json")

# Log único ao sanearem títulos Plotly (evita spam no console)
_PLOTLY_TITLE_SANITIZE_LOGGED = False


def apply_enterprise_theme() -> None:
    """Aplica o design system ZIG: paleta, tipografia e espaçamento."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        :root {
            /* Paleta ZIG */
            --zig-primary: #0047BB;
            --zig-primary-light: #005FD1;
            --zig-chart: #0066CC;
            --zig-accent: #7A1FA2;
            --zig-accent-alt: #8E44AD;
            --zig-success: #28a745;
            --zig-warn: #ffc107;
            --zig-danger: #ef4444;
            --bg: #F3F6F9;
            --panel: #FFFFFF;
            --panel-2: #eef2f8;
            --border: #E3E8EE;
            --text-primary: #0047BB;
            --text-body: #3d4f5f;
            --muted: #5F6A72;
        }

        .stApp {
            background: var(--bg);
            color: var(--zig-primary);
            font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }
        .stApp p, .stApp span, .stApp li {
            color: var(--zig-primary) !important;
        }
        .block-container label, [data-testid="stWidgetLabel"] label, .stSelectbox label, .stMultiSelect label {
            color: var(--zig-primary) !important;
        }
        .stDataFrame th, .stDataFrame td, .stTable th, .stTable td,
        [data-testid="stDataFrame"] th, [data-testid="stDataFrame"] td {
            color: var(--zig-primary) !important;
        }
        [data-testid="stCaption"] {
            color: var(--zig-primary) !important;
        }
        section[data-testid="stSidebar"] {
            background: var(--zig-primary);
            border-right: 0;
            width: 240px !important;
            min-width: 240px !important;
            max-width: 240px !important;
            box-sizing: border-box !important;
        }
        section[data-testid="stSidebar"] * {
            color: #FFFFFF !important;
        }
        /* Inputs no sidebar: fundo legível (evita “campos brancos” quebrando o tema) */
        section[data-testid="stSidebar"] input,
        section[data-testid="stSidebar"] textarea,
        section[data-testid="stSidebar"] [data-baseweb="select"] > div {
            background-color: rgba(255, 255, 255, 0.12) !important;
            color: #FFFFFF !important;
            border-color: rgba(255, 255, 255, 0.35) !important;
            border-radius: 8px !important;
        }
        /* Menu lateral (multipage): padrão único transparente / hover / ativo */
        div[data-testid="stSidebarNav"] ul {
            gap: 0.35rem;
        }
        div[data-testid="stSidebarNav"] li {
            margin: 0.15rem 0;
        }
        div[data-testid="stSidebarNav"] li a,
        div[data-testid="stSidebarNav"] li a * {
            font-size: 17px !important;
        }
        div[data-testid="stSidebarNav"] li a {
            background: transparent !important;
            color: #FFFFFF !important;
            border-radius: 8px !important;
            padding: 10px 12px !important;
            font-weight: 500;
            letter-spacing: 0.01em;
        }
        div[data-testid="stSidebarNav"] li a:hover {
            background: rgba(255, 255, 255, 0.10) !important;
        }
        div[data-testid="stSidebarNav"] li a[aria-current="page"] {
            background: rgba(255, 255, 255, 0.15) !important;
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.18);
            font-weight: 700;
        }
        div[data-testid="stSidebarNav"] ul li:first-child a {
            position: relative;
        }
        div[data-testid="stSidebarNav"] ul li:first-child a * {
            opacity: 0 !important;
        }
        div[data-testid="stSidebarNav"] ul li:first-child a::after {
            content: "Dashboard";
            font-size: 17px !important;
            font-weight: 700;
            letter-spacing: 0.01em;
            color: #FFFFFF;
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
        }
        /* st.button no sidebar: nunca fundo branco */
        section[data-testid="stSidebar"] [data-testid="stButton"] button {
            background: transparent !important;
            background-color: transparent !important;
            color: #FFFFFF !important;
            border: 1px solid rgba(255, 255, 255, 0.35) !important;
            border-radius: 8px !important;
            padding: 10px 12px !important;
            font-weight: 500 !important;
            font-size: 17px !important;
            box-shadow: none !important;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
            background: rgba(255, 255, 255, 0.10) !important;
            color: #FFFFFF !important;
            border-color: rgba(255, 255, 255, 0.45) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] button:focus-visible {
            outline: 2px solid rgba(255, 255, 255, 0.6) !important;
            outline-offset: 2px !important;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"],
        section[data-testid="stSidebar"] [data-testid="stButton"] button[data-testid="baseButton-primary"] {
            background: rgba(255, 255, 255, 0.15) !important;
            border-color: rgba(255, 255, 255, 0.45) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"]:hover,
        section[data-testid="stSidebar"] [data-testid="stButton"] button[data-testid="baseButton-primary"]:hover {
            background: rgba(255, 255, 255, 0.10) !important;
        }
        section[data-testid="stSidebar"] [data-testid="stButton"] button p,
        section[data-testid="stSidebar"] [data-testid="stButton"] button span,
        section[data-testid="stSidebar"] [data-testid="stButton"] button label {
            color: #FFFFFF !important;
        }
        /* Export / download no sidebar: mesmo destaque do item ativo (nunca branco) */
        section[data-testid="stSidebar"] [data-testid="element-container"]:has([data-testid="stDownloadButton"]) {
            margin-left: 0 !important;
            padding-left: 0 !important;
            padding-right: 0 !important;
        }
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"],
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] a,
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] button,
        section[data-testid="stSidebar"] a[download] {
            width: 100% !important;
            max-width: 100% !important;
            background: rgba(255, 255, 255, 0.15) !important;
            background-color: rgba(255, 255, 255, 0.15) !important;
            border: 1px solid rgba(255, 255, 255, 0.28) !important;
            color: #FFFFFF !important;
            text-align: left !important;
            justify-content: flex-start !important;
            padding: 10px 12px !important;
            font-size: 17px !important;
            font-weight: 500 !important;
            border-radius: 8px !important;
            letter-spacing: 0.01em !important;
            box-shadow: none !important;
            min-height: unset !important;
        }
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] button,
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] a {
            display: flex !important;
            align-items: center !important;
            gap: 0.35rem !important;
        }
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] button svg,
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] a svg {
            display: none !important;
        }
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"]:hover,
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] a:hover,
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] button:hover,
        section[data-testid="stSidebar"] a[download]:hover {
            background: rgba(255, 255, 255, 0.10) !important;
            background-color: rgba(255, 255, 255, 0.10) !important;
            color: #FFFFFF !important;
        }
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] button:disabled,
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] button[disabled],
        section[data-testid="stSidebar"] [data-testid="stDownloadButton"] a[aria-disabled="true"] {
            background: rgba(255, 255, 255, 0.08) !important;
            background-color: rgba(255, 255, 255, 0.08) !important;
            color: rgba(255, 255, 255, 0.6) !important;
            border-color: rgba(255, 255, 255, 0.22) !important;
            opacity: 1 !important;
        }
        /* DataFrames no sidebar: fundo escuro coerente */
        section[data-testid="stSidebar"] [data-testid="stDataFrame"],
        section[data-testid="stSidebar"] [data-testid="stDataFrame"] div {
            background: rgba(0, 0, 0, 0.15) !important;
        }
        /* Conteúdo principal: largura máxima centralizada (layout profissional) */
        .main .block-container {
            max-width: 1400px !important;
            margin-left: auto !important;
            margin-right: auto !important;
            padding-left: 24px !important;
            padding-right: 24px !important;
            padding-top: 1.25rem !important;
            padding-bottom: 24px !important;
            min-width: 0 !important;
            width: 100% !important;
        }
        [data-testid="stHorizontalBlock"] {
            min-width: 0;
            width: 100% !important;
        }
        /* Área principal = .main-container (app: .main .block-container) */
        .main-container {
            box-sizing: border-box;
            width: 100%;
            max-width: 1400px;
            margin-left: auto;
            margin-right: auto;
            padding: 24px;
        }
        /* Filtros: linhas com controles, sem KPIs */
        div[data-testid="stHorizontalBlock"]:not(:has([data-testid="stMetric"])):has(.stSelectbox, .stDateInput, .stTextInput, .stMultiSelect, .stNumberInput) {
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 16px !important;
            margin-bottom: 20px !important;
            align-items: flex-end !important;
            width: 100% !important;
        }
        div[data-testid="stHorizontalBlock"]:not(:has([data-testid="stMetric"])):has(.stSelectbox, .stDateInput, .stTextInput, .stMultiSelect, .stNumberInput) > div[data-testid="column"] {
            flex: 1 1 220px !important;
            min-width: 0 !important;
            width: auto !important;
            max-width: 100% !important;
        }
        /* .cards-container / linha de KPIs (st.metric): alinhamento superior, gap uniforme */
        .zr-cards-container,
        div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 16px !important;
            justify-content: flex-start !important;
            align-items: flex-start !important;
            width: 100% !important;
        }
        .zr-cards-container > div[data-testid="column"],
        div[data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div[data-testid="column"] {
            flex: 0 0 auto !important;
            width: auto !important;
            min-width: 180px !important;
            max-width: none !important;
        }
        /* Plotly: largura do bloco + altura mínima (reduz gráficos “achatados” e redraw estranho) */
        [data-testid="stPlotlyChart"],
        .element-container [data-testid="stPlotlyChart"] {
            width: 100% !important;
            max-width: 100% !important;
            min-height: 350px !important;
        }
        [data-testid="stPlotlyChart"] .js-plotly-plot,
        [data-testid="stPlotlyChart"] .plotly {
            width: 100% !important;
            min-height: 320px !important;
        }
        div[data-testid="column"] [data-testid="stPlotlyChart"] {
            min-height: 320px !important;
        }
        /* .card: st.metric — proporção título/valor; box do card inalterado (min-width/padding) */
        .stApp div[data-testid="stMetric"] {
            flex: 0 0 auto !important;
            min-width: 180px !important;
            background: var(--panel) !important;
            border: 1px solid var(--border) !important;
            border-radius: 10px !important;
            padding: 18px !important;
            box-sizing: border-box !important;
            box-shadow: 0 1px 3px rgba(0, 71, 187, 0.08) !important;
            margin: 0 !important;
            display: flex !important;
            flex-direction: column !important;
            gap: 6px !important;
        }
        .stApp div[data-testid="stMetric"] label,
        .stApp div[data-testid="stMetric"] [data-testid="stMetricLabel"],
        .stApp [data-testid="stMetric"] label {
            color: #6b7280 !important;
            font-size: 24px !important;
            font-weight: 600 !important;
            line-height: 1.25 !important;
            opacity: 0.95 !important;
        }
        .stApp [data-testid="stMetricValue"],
        .stApp [data-testid="stMetricValue"] *,
        .stApp div[data-testid="stMetric"] div[data-testid="stMetricValue"],
        .stApp div[data-testid="stMetric"] div[data-testid="stMetricValue"] * {
            color: #6C2BD9 !important;
            font-weight: 700 !important;
            font-size: 24px !important;
        }
        .stApp div[data-testid="stMetric"] [data-testid="stMetricDelta"],
        .stApp div[data-testid="stMetric"] [data-testid="stMetricDelta"] *,
        .stApp div[data-testid="stMetric"] p,
        .stApp div[data-testid="stMetric"] p * {
            color: #0066CC !important;
            font-size: 14px !important;
            font-weight: 500 !important;
        }
        .stApp [data-testid="stMetric"] > div > div:first-child,
        .stApp [data-testid="stMetric"] > div > div:first-child * {
            color: #6b7280 !important;
            font-weight: 600 !important;
            font-size: 24px !important;
            line-height: 1.25 !important;
            opacity: 0.95 !important;
        }
        .stApp [data-testid="stMetric"] > div > div:nth-child(2),
        .stApp [data-testid="stMetric"] > div > div:nth-child(2) * {
            color: #6C2BD9 !important;
            font-weight: 700 !important;
            font-size: 24px !important;
        }
        .zr-panel {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 18px;
            margin-bottom: 12px;
            box-shadow: 0 1px 3px rgba(0, 71, 187, 0.08);
        }
        .zr-title {
            font-weight: 700;
            color: var(--zig-primary);
            font-size: 32px;
            margin-bottom: 0.1rem;
        }
        .zr-section {
            font-weight: 600;
            color: var(--zig-primary);
            font-size: 20px;
            margin-bottom: 0.5rem;
        }
        .zr-muted {
            color: var(--muted);
            font-size: 15px;
            font-weight: 500;
            margin-bottom: 0.7rem;
        }
        .zr-tag {
            color: var(--zig-primary);
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 0.02em;
            text-transform: uppercase;
            margin-bottom: 0.3rem;
        }
        div[data-baseweb="select"] > div, .stTextInput > div > div > input {
            background-color: var(--panel) !important;
            color: var(--text-body) !important;
            border-color: var(--border) !important;
            border-radius: 10px !important;
            font-size: 14px;
        }
        .stDataFrame, .stTable {
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
            background: var(--panel);
        }
        .stDataFrame th, .stTable th,
        [data-testid="stDataFrame"] th {
            font-size: 14px !important;
            font-weight: 600 !important;
        }
        .stDataFrame td, .stTable td,
        [data-testid="stDataFrame"] td {
            font-size: 14px !important;
        }
        .stMarkdown h1, .stMarkdown h2, .stMarkdown h3,
        [data-testid="stMarkdown"] h1, [data-testid="stMarkdown"] h2, [data-testid="stMarkdown"] h3 {
            color: var(--zig-primary) !important;
        }
        .pipeline-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 16px;
            border-radius: 8px;
            background: #f5f7fb;
            border: 1px solid #e3e8f2;
            flex-wrap: nowrap;
            gap: 24px;
            margin-bottom: 16px;
        }
        .pipeline-info, .pipeline-info-inline {
            display: flex;
            gap: 24px;
            align-items: center;
            flex-wrap: wrap;
            min-width: 0;
            flex: 1;
        }
        .pipeline-info span, .pipeline-info-inline span { white-space: nowrap; font-size: 14px; color: #1F2A37; }
        .pipeline-title { font-weight: 600; color: #0047BB; font-size: 18px; }
        .status-success { color: #1fa971; font-weight: 600; }
        .status-error { color: #e74c3c; font-weight: 600; }
        .run-button {
            background: #8e44ff;
            color: white;
            border: none;
            padding: 6px 14px;
            border-radius: 6px;
            cursor: pointer;
            flex-shrink: 0;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="column"] button[kind="primary"],
        button[kind="primary"] {
            min-width: 140px;
            font-size: 16px !important;
            background-color: #0047BB !important;
            color: #ffffff !important;
            border: 1px solid #003d9e !important;
        }
        button[kind="primary"] p, button[kind="primary"] span,
        button[kind="primary"] label, button[kind="primary"] div {
            color: #ffffff !important;
            font-size: 16px !important;
            font-weight: 600 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def apply_dark_theme() -> None:
    # Compatibilidade com imports antigos.
    apply_enterprise_theme()


def aplicar_estilo_global() -> None:
    """Tipografia e legibilidade globais: fontes maiores, hierarquia visual e padrão consistente em todas as abas.
    Não altera o título principal .zr-title (ZIG Risk Monitor)."""
    st.markdown(
        """
        <style>
        /* ----------------------------------
        BASE GLOBAL (legibilidade operacional)
        ---------------------------------- */
        html, body, [class*="css"] {
            font-size: 17px !important;
        }
        /* ----------------------------------
        SUBTÍTULO (.zr-muted)
        ---------------------------------- */
        .zr-muted {
            font-size: 18px !important;
            font-weight: 500 !important;
            opacity: 0.85 !important;
        }
        /* ----------------------------------
        TÍTULOS DE SEÇÃO
        ---------------------------------- */
        .dash-section-title, .rt-section-title, .cf-section-title, .rel-section-title, .rn-section-title {
            font-size: 22px !important;
            font-weight: 700 !important;
            margin-top: 20px !important;
            margin-bottom: 10px !important;
        }
        /* KPIs: rótulo/valor em apply_enterprise_theme (stMetric) */
        /* ----------------------------------
        SIDEBAR — alinhado ao menu (não forçar 17px nos links; tema principal define)
        ---------------------------------- */
        section[data-testid="stSidebar"] [data-testid="stCaption"],
        section[data-testid="stSidebar"] .stMarkdown p,
        section[data-testid="stSidebar"] .stMarkdown span {
            font-size: 15px !important;
        }
        /* ----------------------------------
        FILTROS (área principal): rótulos maiores — exclui st.metric
        ---------------------------------- */
        .main .block-container [data-testid="stWidgetLabel"] label,
        .main .block-container [data-testid="stWidgetLabel"] p {
            font-size: 19px !important;
            font-weight: 600 !important;
            line-height: 1.35 !important;
        }
        /* st.metric: rótulo no mesmo tamanho do valor (24px); não herdar 19px dos filtros */
        .stApp div[data-testid="stMetric"] [data-testid="stWidgetLabel"] label,
        .stApp div[data-testid="stMetric"] [data-testid="stWidgetLabel"] p {
            font-size: 24px !important;
            font-weight: 600 !important;
            line-height: 1.25 !important;
            color: #6b7280 !important;
            opacity: 0.95 !important;
        }
        .stApp .stSelectbox div,
        .stApp .stDateInput div,
        .stApp .stTextInput div {
            font-size: 18px !important;
        }
        .stApp .stDateInput input,
        .stApp .stTextInput input {
            font-size: 18px !important;
        }
        /* ----------------------------------
        TABELAS
        ---------------------------------- */
        .stDataFrame {
            font-size: 17px !important;
        }
        .stDataFrame th {
            font-size: 17px !important;
            font-weight: 600 !important;
        }
        /* ----------------------------------
        ESPAÇAMENTO GLOBAL (área principal)
        ---------------------------------- */
        .main .block-container {
            padding-top: 2rem !important;
            padding-bottom: 2rem !important;
            max-width: 1400px !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _plotly_layout_title_main_text(fig) -> str | None:
    """Extrai apenas o texto do título (nunca o objeto Title inteiro)."""
    try:
        t = fig.layout.title
        if t is None:
            return None
        if isinstance(t, str):
            s = t.strip()
            return s if s and s.lower() not in ("undefined", "none", "nan") else None
        txt = getattr(t, "text", None)
        if txt is not None:
            s = str(txt).strip()
            if not s or s.lower() in ("undefined", "none", "nan"):
                return None
            return s
        if isinstance(t, dict):
            tx = t.get("text")
            if tx is None:
                return None
            s = str(tx).strip()
            if not s or s.lower() in ("undefined", "none", "nan"):
                return None
            return s
        return None
    except Exception:
        return None


def _plotly_layout_title_subtitle_text(fig) -> str | None:
    try:
        t = fig.layout.title
        if t is None:
            return None
        sub = getattr(t, "subtitle", None)
        if sub is None and isinstance(t, dict):
            sub = t.get("subtitle")
        if sub is None:
            return None
        stx = getattr(sub, "text", None) if not isinstance(sub, dict) else sub.get("text")
        if stx is None:
            return None
        s = str(stx).strip()
        if not s or s.lower() in ("undefined", "none", "nan"):
            return None
        return s
    except Exception:
        return None


def _is_garbage_title_string(s: str) -> bool:
    sl = s.lower()
    return (
        "layout.title" in sl
        or s.startswith("layout.Title")
        or s.startswith("<layout.")
    )


def sanitize_plotly_title_and_legend(fig) -> None:
    """Normaliza título/subtítulo Plotly e evita texto espúrio (ex.: referências a layout.title)."""
    global _PLOTLY_TITLE_SANITIZE_LOGGED
    try:
        main = _plotly_layout_title_main_text(fig)
        if main and _is_garbage_title_string(main):
            main = None
        if main and isinstance(main, float) and math.isnan(main):
            main = None

        sub = _plotly_layout_title_subtitle_text(fig)
        if sub and _is_garbage_title_string(sub):
            sub = None

        if not main:
            fig.update_layout(title=dict(text="", subtitle=dict(text="")))
        elif sub:
            fig.update_layout(title=dict(text=main, subtitle=dict(text=sub)))
        else:
            fig.update_layout(title=dict(text=main, subtitle=dict(text="")))
    except Exception:
        pass

    if not _PLOTLY_TITLE_SANITIZE_LOGGED:
        _PLOTLY_TITLE_SANITIZE_LOGGED = True

    try:
        for trace in fig.data:
            name = getattr(trace, "name", None)
            if name is None or name == "undefined" or (
                isinstance(name, float) and math.isnan(name)
            ):
                trace.name = ""
    except Exception:
        pass


def apply_plotly_readability(
    fig,
    *,
    height: int = 550,
    horizontal_bar: bool = False,
    histogram: bool = False,
    skip_axes: bool = False,
    zig_brand_layout: bool = False,
) -> None:
    """
    Padrão de legibilidade para gráficos Plotly: altura, fontes, legenda e tooltip (hover).
    Chamar após fig.update_layout(...) existente. Valores nos gráficos com textfont_size=16.

    zig_brand_layout=True: fundo de plot, grade e cor de fonte alinhados ao tema ZIG (Dashboard).
    """
    sanitize_plotly_title_and_legend(fig)
    layout = {
        "height": height,
        "title_font": {"size": 22},
        "legend": {"font": {"size": 16, "color": "#0047BB"}},
        "hoverlabel": {
            "font": {"size": 16, "family": "Arial", "color": "black"},
            "bgcolor": "white",
            "bordercolor": "#0047BB",
        },
    }
    if zig_brand_layout:
        from modules.plotly_theme import zig_cartesian_layout

        z = zig_cartesian_layout()
        layout["paper_bgcolor"] = z["paper_bgcolor"]
        layout["plot_bgcolor"] = z["plot_bgcolor"]
        layout["font"] = {**z["font"], "size": 15}
        if not skip_axes:
            layout["xaxis"] = {
                **z["xaxis"],
                "tickfont": {"size": 15 if histogram else 16, "color": "#0047BB"},
                "title_font": {"size": 18, "color": "#0047BB"},
            }
            layout["yaxis"] = {
                **z["yaxis"],
                "tickfont": {"size": 15 if (histogram or horizontal_bar) else 16, "color": "#0047BB"},
                "title_font": {"size": 18, "color": "#0047BB"},
            }
    elif not skip_axes:
        layout["xaxis"] = {"tickfont": {"size": 15 if histogram else 16}, "title_font": {"size": 18}}
        layout["yaxis"] = {"tickfont": {"size": 15 if (histogram or horizontal_bar) else 16}, "title_font": {"size": 18}}

    fig.update_layout(**layout)
    try:
        fig.update_traces(textfont_size=16, hoverlabel={"font": {"size": 16}})
    except Exception:
        pass


def _load_pipeline_status_from_file() -> None:
    """Sincroniza session_state com o arquivo de status (para atualização em tempo real)."""
    if not PIPELINE_STATUS_FILE.exists():
        return
    try:
        data = json.loads(PIPELINE_STATUS_FILE.read_text(encoding="utf-8"))
        for key in ("pipeline_last_run", "pipeline_rows", "pipeline_message", "pipeline_status"):
            if key in data and data[key] is not None:
                st.session_state[key] = data[key]
    except Exception:
        pass


def _save_pipeline_status_to_file() -> None:
    """Persiste o status da pipeline para que outras sessões/atualizações vejam em tempo real."""
    PIPELINE_STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "pipeline_last_run": st.session_state.get("pipeline_last_run"),
        "pipeline_rows": st.session_state.get("pipeline_rows"),
        "pipeline_message": st.session_state.get("pipeline_message"),
        "pipeline_status": st.session_state.get("pipeline_status"),
    }
    PIPELINE_STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@st.fragment(run_every=60)
def _pipeline_status_auto_refresh() -> None:
    """
    Atualiza só o texto de status a partir de logs/pipeline_status.json.
    O botão Executar agora fica fora deste fragmento: assim st.rerun() e
    mudanças nos filtros disparam rerun completo do app (dados e gráficos).
    """
    _load_pipeline_status_from_file()
    last_run = st.session_state.get("pipeline_last_run") or "—"
    rows = st.session_state.get("pipeline_rows") or 0
    try:
        rows_int = int(float(rows))
    except (TypeError, ValueError):
        rows_int = 0
    rows_fmt = f"{rows_int:,}".replace(",", ".")
    message = st.session_state.get("pipeline_message") or "Executado com sucesso"
    status = st.session_state.get("pipeline_status")
    status_class = "status-success" if status == "success" else ("status-error" if status == "error" else "")
    status_text = "Sucesso" if status == "success" else ("Erro" if status == "error" else "—")
    st.markdown(
        f"""
        <div class="pipeline-info-inline">
            <span class="pipeline-title">Pipeline Metabase</span>
            <span>Status: <b class="{status_class}">{status_text}</b></span>
            <span>Última execução: {last_run}</span>
            <span>Linhas inseridas: {rows_fmt}</span>
            <span>Mensagem: {message}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metabase_pipeline_bar() -> None:
    """
    Header da pipeline: Pipeline Metabase | Status | Última execução | Linhas inseridas | Mensagem | Executar agora.
    O texto de status usa fragment run_every=60s; o botão não, para não travar atualização do dashboard.
    """
    from datetime import datetime

    from modules.data_loader import is_metabase_configured, run_metabase_ingestion

    _load_pipeline_status_from_file()

    for key, default in (
        ("pipeline_last_run", None),
        ("pipeline_rows", 0),
        ("pipeline_message", ""),
        ("pipeline_status", None),
    ):
        if key not in st.session_state:
            st.session_state[key] = default

    metabase_ok = is_metabase_configured()

    try:
        _pipe_box = st.container(border=True)
    except TypeError:
        _pipe_box = st.container()
    with _pipe_box:
        col_info, col_btn = st.columns([4, 1])
        with col_info:
            _pipeline_status_auto_refresh()
        with col_btn:
            if st.button("Executar agora", type="primary", width="stretch", key="pipeline_run"):
                if not metabase_ok:
                    st.session_state["pipeline_status"] = "error"
                    st.session_state["pipeline_message"] = (
                        "Metabase não configurado. Defina METABASE_URL e METABASE_CARD_ID (e credenciais) "
                        "em variáveis de ambiente: no Streamlit Community Cloud use **App settings → Secrets**; "
                        "localmente use o arquivo `.env` na pasta do app."
                    )
                    st.session_state["pipeline_rows"] = 0
                    _save_pipeline_status_to_file()
                    st.rerun()
                else:
                    try:
                        with st.spinner(
                            "Atualizando dados do Metabase... O dashboard acima já usa a última base salva; a pipeline tenta export CSV automático para ir além do teto ~2k do JSON."
                        ):
                            ok, rows, msg = run_metabase_ingestion()
                        st.session_state["pipeline_status"] = "success" if ok else "error"
                        st.session_state["pipeline_rows"] = rows
                        st.session_state["pipeline_message"] = msg or "Executado com sucesso"
                        st.session_state["pipeline_last_run"] = datetime.now().strftime("%d/%m/%Y, %H:%M")
                        st.session_state["use_processed_data_next_load"] = True
                    except Exception as e:
                        st.session_state["pipeline_status"] = "error"
                        st.session_state["pipeline_rows"] = 0
                        st.session_state["pipeline_message"] = str(e)[:200] if str(e) else "Erro ao executar a pipeline."
                    _save_pipeline_status_to_file()
                    st.rerun()


def format_currency_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0).apply(format_brl)
    return out


def apply_global_filters(df: pd.DataFrame, key_prefix: str = "global") -> pd.DataFrame:
    if df.empty:
        return df
    st.markdown("### Filtros")
    c1, c2, c3, c4, c5 = st.columns(5)

    month_options = ["Todos"]
    if "PERIODO_MES" in df.columns:
        month_options += sorted(df["PERIODO_MES"].dropna().astype(str).unique().tolist(), reverse=True)
    month = c1.selectbox("Mes", month_options, key=f"{key_prefix}_mes")

    def _opts(column: str) -> list[str]:
        if column not in df.columns:
            return ["Todos"]
        values = [v for v in df[column].astype(str).unique().tolist() if v and v.lower() != "nan"]
        return ["Todos"] + sorted(values)

    nome_place = c2.selectbox("NOME_PLACE", _opts("NOME_PLACE"), key=f"{key_prefix}_nome_place")
    banco = c3.selectbox("BANCO_RECEBEDOR", _opts("BANCO_RECEBEDOR"), key=f"{key_prefix}_banco")
    agencia = c4.selectbox("AGENCIA_RECEBEDOR", _opts("AGENCIA_RECEBEDOR"), key=f"{key_prefix}_agencia")
    conta = c5.selectbox("CONTA_RECEBEDOR", _opts("CONTA_RECEBEDOR"), key=f"{key_prefix}_conta")

    filtered = df.copy()
    if month != "Todos" and "PERIODO_MES" in filtered.columns:
        filtered = filtered[filtered["PERIODO_MES"].astype(str) == month]
    if nome_place != "Todos" and "NOME_PLACE" in filtered.columns:
        filtered = filtered[filtered["NOME_PLACE"].astype(str) == nome_place]
    if banco != "Todos" and "BANCO_RECEBEDOR" in filtered.columns:
        filtered = filtered[filtered["BANCO_RECEBEDOR"].astype(str) == banco]
    if agencia != "Todos" and "AGENCIA_RECEBEDOR" in filtered.columns:
        filtered = filtered[filtered["AGENCIA_RECEBEDOR"].astype(str) == agencia]
    if conta != "Todos" and "CONTA_RECEBEDOR" in filtered.columns:
        filtered = filtered[filtered["CONTA_RECEBEDOR"].astype(str) == conta]
    return filtered
