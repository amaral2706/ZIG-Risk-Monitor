"""Formatação numérica para painéis — sem dependência de Streamlit (import estável nas páginas)."""

from __future__ import annotations

import math


def format_brl(value: float) -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    return f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_metric_float(value: float, spec: str = ".1f") -> str:
    """Número para `st.metric` / texto; não-finito vira traço (evita 'inf' / 'nan' no painel)."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    return format(x, spec)
