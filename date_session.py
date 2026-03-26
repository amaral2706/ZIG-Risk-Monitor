"""Sincronização global de intervalo de datas entre páginas Streamlit."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import streamlit as st

# Limites do calendário: independentes do min/max das datas na base carregada.
CALENDAR_MIN = date(2000, 1, 1)
CALENDAR_MAX = date(2100, 12, 31)


def _to_date(v: object, fallback: date) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "date") and callable(getattr(v, "date", None)):
        try:
            d = v.date()
            if isinstance(d, date):
                return d
        except Exception:
            pass
    return fallback


def sync_global_date_inputs(
    data_min: date,
    data_max: date,
    *,
    default_last_n_days: int | None = None,
) -> None:
    """
    Garante ``st.session_state.data_inicial`` e ``data_final`` com chaves fixas.

    ``data_min`` / ``data_max`` vêm da base e orientam o primeiro acesso (defaults).
    O intervalo permitido no calendário é [CALENDAR_MIN, CALENDAR_MAX].

    ``default_last_n_days``: se ambas as chaves ainda não existirem, usa os últimos N dias
    (fim = hoje na base) em vez do mês corrente — útil para dashboard mais leve na primeira abertura.
    """
    if data_min > data_max:
        data_min, data_max = data_max, data_min

    today = date.today()
    first_month = today.replace(day=1)

    cal_min, cal_max = CALENDAR_MIN, CALENDAR_MAX

    if "data_inicial" not in st.session_state and "data_final" not in st.session_state:
        end_d = max(data_min, min(today, data_max))
        if default_last_n_days is not None and default_last_n_days > 0:
            start_d = max(data_min, end_d - timedelta(days=default_last_n_days - 1))
            st.session_state.data_inicial = start_d
            st.session_state.data_final = end_d
        else:
            st.session_state.data_inicial = max(data_min, min(first_month, data_max))
            st.session_state.data_final = max(data_min, min(today, data_max))
    elif "data_inicial" not in st.session_state:
        st.session_state.data_inicial = max(data_min, min(first_month, data_max))
    elif "data_final" not in st.session_state:
        st.session_state.data_final = max(data_min, min(today, data_max))

    di = _to_date(st.session_state.data_inicial, data_min)
    df = _to_date(st.session_state.data_final, data_max)

    di = max(cal_min, min(di, cal_max))
    df = max(cal_min, min(df, cal_max))
    if di > df:
        di, df = df, di
    st.session_state.data_inicial = di
    st.session_state.data_final = df

    if not st.session_state.get("_zig_date_calendar_logged"):
        st.session_state._zig_date_calendar_logged = True
        print("Calendários sincronizados via session_state")
