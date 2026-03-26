"""Coluna DATA_CRIACAO_PLACE_E_ORG em primeiro lugar nas tabelas e exports."""

from __future__ import annotations

import pandas as pd

DATA_CRIACAO_COL = "DATA_CRIACAO_PLACE_E_ORG"

# Ordem de fallback quando a criação place/org não veio na base (Metabase/planilha).
_FALLBACK_DATAS = ("DATA_SOLICITACAO", "DATA_PAGAMENTO")


def fill_data_criacao_place_e_org(df: pd.DataFrame) -> pd.DataFrame:
    """
    Preenche células vazias/NaT em DATA_CRIACAO_PLACE_E_ORG com datas da própria linha.

    Prioridade: mantém valor existente; depois DATA_SOLICITACAO; por último DATA_PAGAMENTO.
    Cria a coluna se não existir e houver algum fallback. Altera o DataFrame no lugar e retorna o mesmo objeto.
    """
    if df.empty:
        return df
    idx = df.index
    if DATA_CRIACAO_COL in df.columns:
        s = pd.to_datetime(df[DATA_CRIACAO_COL], errors="coerce")
    else:
        s = pd.Series(pd.NaT, index=idx)
    for fb in _FALLBACK_DATAS:
        if fb not in df.columns:
            continue
        s = s.fillna(pd.to_datetime(df[fb], errors="coerce"))
    df[DATA_CRIACAO_COL] = s
    return df


def reorder_data_criacao_first(df: pd.DataFrame) -> pd.DataFrame:
    """Move DATA_CRIACAO_PLACE_E_ORG para a 1ª posição, se existir."""
    if df.empty or DATA_CRIACAO_COL not in df.columns:
        return df
    rest = [c for c in df.columns if c != DATA_CRIACAO_COL]
    return df[[DATA_CRIACAO_COL] + rest].copy()


def format_data_criacao_series(s: pd.Series) -> pd.Series:
    """Formata para exibição em tabela (dd/mm/aaaa hh:mm, fuso Brasil quando houver tz)."""
    ser = pd.to_datetime(s, errors="coerce")
    if pd.api.types.is_datetime64tz_dtype(ser):
        out = ser.dt.tz_convert("America/Sao_Paulo").dt.strftime("%d/%m/%Y %H:%M")
    else:
        out = ser.dt.strftime("%d/%m/%Y %H:%M")
    return out.fillna("—")


def format_row_data_criacao_display(row: pd.Series) -> str:
    """Uma linha: DATA_CRIACAO_PLACE_E_ORG → DATA_SOLICITACAO → DATA_PAGAMENTO (texto formatado)."""
    for c in (DATA_CRIACAO_COL, *_FALLBACK_DATAS):
        if c not in row.index:
            continue
        x = row.get(c)
        if x is not None and not pd.isna(x) and str(x).strip() not in ("", "nan", "None", "NaT", "<NA>"):
            return format_data_criacao_series(pd.Series([x], dtype=object)).iloc[0]
    return "—"


def add_placeholder_data_criacao_column(df: pd.DataFrame, placeholder: str = "—") -> pd.DataFrame:
    """Insere a coluna na 1ª posição com valor fixo (tabelas agregadas sem dado por linha)."""
    out = df.copy()
    if DATA_CRIACAO_COL in out.columns:
        return reorder_data_criacao_first(out)
    out.insert(0, DATA_CRIACAO_COL, placeholder)
    return out
