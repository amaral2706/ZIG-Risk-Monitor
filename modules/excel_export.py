"""Utilitários para exportação Excel (Download Base) no Streamlit."""

from __future__ import annotations

import pandas as pd

from modules.table_columns import reorder_data_criacao_first

# Colunas do motor antifraude que devem constar no export (base já processada / filtrada).
MOTOR_EXPORT_COLUMNS = (
    "HORA_TRANSACAO",
    "VALOR_ALTO",
    "SAQUE_NOTURNO",
    "DESTINATARIO_REPETIDO",
    "RISK_SCORE",
    "SUSPEITA",
)


def prepare_download_base_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Prepara o DataFrame para download (CSV):
    - Remove timezone de colunas datetime (compatível com Excel).
    - **DATA_CRIACAO_PLACE_E_ORG** na primeira coluna, quando existir.
    - Garante colunas do motor (HORA_TRANSACAO, flags, RISK_SCORE, SUSPEITA) quando ausentes
      (export legado); o fluxo normal já envia a base processada pós add_features/calcular_score.
    """
    out = df.copy()
    for col in list(out.columns):
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            ser = out[col]
            if hasattr(ser.dt, "tz") and ser.dt.tz is not None:
                out[col] = ser.dt.tz_convert("UTC").dt.tz_localize(None)

    for col in MOTOR_EXPORT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    return reorder_data_criacao_first(out)
