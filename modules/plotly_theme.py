"""
Paleta e layout Plotly alinhados ao design system ZIG (mesmas cores do CSS em modules/ui.py).

Uso: importar constantes no Dashboard (e outras páginas) para evitar sequências padrão do Plotly
(tons pastéis / baixa saturação) e manter contraste consistente.
"""
from __future__ import annotations

# Espelha :root do apply_enterprise_theme (ui.py)
ZIG_PRIMARY = "#0047BB"
ZIG_PRIMARY_LIGHT = "#005FD1"
ZIG_CHART = "#0066CC"
ZIG_ACCENT = "#7A1FA2"
ZIG_ACCENT_ALT = "#8E44AD"
ZIG_TEXT_BODY = "#3d4f5f"
ZIG_BORDER = "#E3E8EE"
ZIG_BG_PANEL = "#F3F6F9"

# Múltiplas séries (linhas por ano, fatias de pizza, etc.): saturadas, sem pastel padrão Plotly
ZIG_SERIES_COLORS: tuple[str, ...] = (
    "#0047BB",
    "#005FD1",
    "#0066CC",
    "#153E75",
    "#7A1FA2",
    "#5B21B6",
    "#0D47A1",
    "#1E3A5F",
    "#2563EB",
    "#4338CA",
)

# Distribuição por método (ordem fixa do dashboard)
METODO_COLOR_MAP: dict[str, str] = {
    "TED": "#0047BB",
    "PIX": "#005FD1",
    "BOLETO": "#153E75",
    "OUTROS": "#475569",
}


def zig_cartesian_layout() -> dict:
    """Layout comum: fundo de plot leve, grade visível, fonte primária (sem transparência nas traces)."""
    return {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": ZIG_BG_PANEL,
        "font": {"color": ZIG_PRIMARY, "family": "Arial, sans-serif"},
        "xaxis": {
            "gridcolor": ZIG_BORDER,
            "zerolinecolor": ZIG_BORDER,
            "showgrid": True,
            "zeroline": False,
        },
        "yaxis": {
            "gridcolor": ZIG_BORDER,
            "zerolinecolor": ZIG_BORDER,
            "showgrid": True,
            "zeroline": False,
        },
    }
