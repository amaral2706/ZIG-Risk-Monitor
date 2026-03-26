"""
Filtro por mês no calendário Brasil — sem depender de split()/nome do mês no retorno do selectbox.

Evita: MESES_PT.index(nome) falhar (Unicode) e cair no default mês 1 → recorte errado / vazio.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date

import pandas as pd

MESES_PT = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)


def year_months_br_from_utc_series(s: pd.Series) -> list[tuple[int, int]]:
    """(ano, mês) civil em America/Sao_Paulo a partir de datetime64 com timezone (ex.: UTC)."""
    if s is None or len(s) == 0:
        return []
    s = s.dropna()
    if s.empty:
        return []
    if not pd.api.types.is_datetime64_any_dtype(s):
        return []
    if s.dt.tz is None:
        s = s.dt.tz_localize("UTC", ambiguous=False, nonexistent="shift_forward")
    br = s.dt.tz_convert("America/Sao_Paulo")
    pairs = zip(br.dt.year.astype(int), br.dt.month.astype(int))
    return sorted(set(pairs), reverse=True)


def labels_and_ranges_for_months(
    year_months: list[tuple[int, int]],
    cal_min: date,
    cal_max: date,
) -> tuple[list[str], list[tuple[date, date]]]:
    """
    Retorna:
    - labels: primeiro item = legenda “nenhum mês”; demais = \"Março 2026\", …
    - ranges: paralelo a labels[1:], cada item (início, fim) inclusivo no dia civil BR.
    """
    labels: list[str] = ["— Selecionar mês"]
    ranges: list[tuple[date, date]] = []
    seen: set[tuple[int, int]] = set()
    for y, m in year_months:
        ym = (int(y), int(m))
        if ym in seen:
            continue
        seen.add(ym)
        m = max(1, min(12, ym[1]))
        y = ym[0]
        _, ult = monthrange(y, m)
        d1 = date(y, m, 1)
        d2 = date(y, m, ult)
        d1 = max(cal_min, min(d1, cal_max))
        d2 = max(cal_min, min(d2, cal_max))
        if d1 > d2:
            d1, d2 = d2, d1
        labels.append(f"{MESES_PT[m - 1]} {y}")
        ranges.append((d1, d2))
    return labels, ranges
