import pandas as pd

from modules.datetime_brt import parse_timestamp_series


def _detect_receiver_type(value: object) -> str:
    if pd.isna(value):
        return "INDEFINIDO"
    raw = str(value).strip()
    only_digits = "".join(ch for ch in raw if ch.isdigit())
    if len(only_digits) == 11:
        return "PF"
    if len(only_digits) == 14:
        return "PJ"
    return "INDEFINIDO"


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    # Referência temporal única para features: DATA_SOLICITACAO (alinhado ao dashboard / filtros).
    if "DATA_SOLICITACAO" in out.columns:
        out["DATA_SOLICITACAO"] = parse_timestamp_series(out["DATA_SOLICITACAO"])
        dt_feat = out["DATA_SOLICITACAO"]
    elif "DATA_PAGAMENTO" in out.columns:
        dt_feat = parse_timestamp_series(out["DATA_PAGAMENTO"])
    else:
        dt_feat = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")

    out["HORA_TRANSACAO"] = dt_feat.dt.hour.fillna(0).astype(int)
    out["SAQUE_NOTURNO"] = out["HORA_TRANSACAO"].between(0, 5, inclusive="both").astype(int)
    out["VALOR_ALTO"] = (out["VALOR_RETIRADA"] > 3000).astype(int)

    # Contas recebedoras com mais de um saque no recorte: marca o conjunto de contas "recorrentes"
    # (via groupby + isin), não um contador por linha com transform.
    if "CONTA_RECEBEDOR" in out.columns and "ID_SAQUE" in out.columns:
        contas_recorrentes = (
            out.groupby("CONTA_RECEBEDOR", dropna=False)["ID_SAQUE"]
            .count()
            .loc[lambda x: x > 1]
            .index
        )
        out["DESTINATARIO_REPETIDO"] = out["CONTA_RECEBEDOR"].isin(contas_recorrentes).astype(int)
    else:
        out["DESTINATARIO_REPETIDO"] = pd.Series(0, index=out.index, dtype=int)

    out["RECEIVER_TIPO"] = out["VALOR_CHAVE_PIX"].apply(_detect_receiver_type)
    # Períodos no calendário Brasil (alinha dropdown “mês” e coluna PERIODO_MES ao filtro por data BR)
    dt_local_naive = dt_feat.dt.tz_convert("America/Sao_Paulo").dt.tz_localize(None)
    out["PERIODO_DIA"] = dt_local_naive.dt.to_period("D").astype(str)
    out["PERIODO_SEMANA"] = dt_local_naive.dt.to_period("W").astype(str)
    out["PERIODO_MES"] = dt_local_naive.dt.to_period("M").astype(str)
    out["IS_BOLETO"] = out["INFO_BOLETO"].astype(str).str.strip().ne("").astype(int)
    out["TERCEIRO"] = (
        out["VALOR_CHAVE_PIX"].astype(str).str.strip().ne("")
        & out["VALOR_CHAVE_PIX"].astype(str).str.strip().ne(out["CNPJ"].astype(str).str.strip())
    ).astype(int)
    return out
