"""
Parse de timestamps vindos do Metabase/Snowflake (muitas vezes sem TZ, já em horário de Brasília).

Evita import circular entre data_loader e feature_engineering.
Parsing elemento a elemento evita FutureWarning do pandas com fusos mistos na mesma Series.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

_DEFAULT_BR = "America/Sao_Paulo"
# ISO com Z ou offset no fim (ex.: +00:00, -03:00)
_RE_HAS_UTC_OFFSET = re.compile(r"(Z\s*$)|([+-]\d{2}:?\d{2}\s*$)", re.IGNORECASE)


def _parse_scalar(x: Any) -> pd.Timestamp:
    if x is None:
        return pd.NaT
    try:
        if pd.isna(x):
            return pd.NaT
    except (ValueError, TypeError):
        pass

    if isinstance(x, pd.Timestamp):
        ts = x
        if ts.tz is None:
            try:
                return ts.tz_localize(
                    _DEFAULT_BR, ambiguous=False, nonexistent="shift_forward"
                ).tz_convert("UTC")
            except Exception as e:
                log.warning("datetime_brt: tz_localize(Sao_Paulo) falhou (%s); utc=True.", e)
                return pd.to_datetime(x, errors="coerce", utc=True)
        return ts.tz_convert("UTC")

    if isinstance(x, datetime):
        ts = pd.Timestamp(x)
        if ts.tz is None:
            try:
                return ts.tz_localize(
                    _DEFAULT_BR, ambiguous=False, nonexistent="shift_forward"
                ).tz_convert("UTC")
            except Exception as e:
                log.warning("datetime_brt: tz_localize(Sao_Paulo) falhou (%s); utc=True.", e)
                return pd.to_datetime(x, errors="coerce", utc=True)
        return ts.tz_convert("UTC")

    s = str(x).strip()
    if not s or s.lower() in ("nan", "nat", "none", "<na>"):
        return pd.NaT

    if _RE_HAS_UTC_OFFSET.search(s):
        t = pd.to_datetime(s, errors="coerce", utc=True)
        return pd.NaT if pd.isna(t) else t

    t = pd.to_datetime(s, errors="coerce", utc=False)
    if pd.isna(t):
        return pd.NaT
    if t.tz is None:
        try:
            return t.tz_localize(
                _DEFAULT_BR, ambiguous=False, nonexistent="shift_forward"
            ).tz_convert("UTC")
        except Exception as e:
            log.warning("datetime_brt: tz_localize(Sao_Paulo) falhou (%s); utc=True.", e)
            return pd.to_datetime(s, errors="coerce", utc=True)
    return t.tz_convert("UTC")


def parse_timestamp_series(series: pd.Series) -> pd.Series:
    """
    Sem timezone na string/coluna: trata como America/Sao_Paulo e normaliza para UTC.
    Com timezone (ex. ...Z ou +00:00): converte para UTC mantendo o instante.

    Caminho vetorizado (pandas) para colunas grandes; fallback elemento a elemento só se
    o vetorizado falhar (tipos mistos incomuns).
    """
    if series is None:
        return pd.Series(dtype="datetime64[ns, UTC]")
    if len(series) == 0:
        return pd.Series(dtype="datetime64[ns, UTC]", index=series.index)

    # --- Fast path: evita ~N chamadas Python a _parse_scalar (muito lento em ~100k+ linhas)
    ser_in = series
    if pd.api.types.is_datetime64_any_dtype(ser_in):
        conv = ser_in
    else:
        to_dt_kw: dict = {"errors": "coerce", "utc": False}
        try:
            major = int(pd.__version__.split(".", 1)[0])
            if major >= 2:
                to_dt_kw["format"] = "mixed"
        except (ValueError, TypeError, AttributeError):
            pass
        try:
            conv = pd.to_datetime(ser_in, **to_dt_kw)
        except (ValueError, TypeError):
            conv = pd.to_datetime(ser_in, errors="coerce", utc=False)

    if pd.api.types.is_datetime64_any_dtype(conv):
        try:
            tz = getattr(conv.dt, "tz", None)
            if tz is None:
                loc = conv.dt.tz_localize(
                    _DEFAULT_BR,
                    ambiguous=False,
                    nonexistent="shift_forward",
                )
                return loc.dt.tz_convert("UTC")
            return conv.dt.tz_convert("UTC")
        except (TypeError, ValueError, AttributeError):
            pass

    parsed = [_parse_scalar(v) for v in series]
    return pd.to_datetime(pd.Series(parsed, index=series.index), errors="coerce", utc=True)
