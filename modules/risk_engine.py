import pandas as pd


def _flag_series_to_int(s: pd.Series, index: pd.Index) -> pd.Series:
    """
    Normaliza flags 0/1 vindas de CSV (object, float, bool, string) para 0/1 int.
    Usa (valor numérico >= 1) para aceitar 1.0 e strings "1" sem falhar em comparações estritas.
    """
    if s is None or len(s) == 0:
        return pd.Series(0, index=index, dtype=int)
    s = s.reindex(index)
    if s.dtype == bool:
        return s.fillna(False).astype(int)
    num = pd.to_numeric(s, errors="coerce").fillna(0)
    return (num >= 1).astype(int)


def sync_suspeita_from_flags(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reaplica apenas a regra oficial de SUSPEITA sobre as flags atuais do DataFrame.
    Use após carregar CSV processado ou após add_features sem recalcular score inteiro,
    para evitar SUSPEITA stale ou tipos que falham em (col == 1).
    """
    if df.empty:
        return df
    out = df.copy()
    _ensure_suspeita_input_columns(out)
    idx = out.index
    va = _flag_series_to_int(out["VALOR_ALTO"], idx)
    sn = _flag_series_to_int(out["SAQUE_NOTURNO"], idx)
    dr = _flag_series_to_int(out["DESTINATARIO_REPETIDO"], idx)
    out["SUSPEITA"] = ((va == 1) & ((sn == 1) | (dr == 1))).astype(int)
    return out


def _ensure_suspeita_input_columns(out: pd.DataFrame) -> None:
    """
    Garante as flags da regra oficial de SUSPEITA antes de calcular a coluna.
    Só preenche colunas ausentes (não sobrescreve o que veio de add_features).
    """
    if "VALOR_ALTO" not in out.columns:
        if "VALOR_RETIRADA" in out.columns:
            out["VALOR_ALTO"] = (
                pd.to_numeric(out["VALOR_RETIRADA"], errors="coerce").fillna(0) > 3000
            ).astype(int)
        else:
            out["VALOR_ALTO"] = pd.Series(0, index=out.index, dtype=int)

    if "SAQUE_NOTURNO" not in out.columns:
        if "HORA_TRANSACAO" in out.columns:
            h = pd.to_numeric(out["HORA_TRANSACAO"], errors="coerce").fillna(0).astype(int)
            out["SAQUE_NOTURNO"] = h.between(0, 5, inclusive="both").astype(int)
        else:
            out["SAQUE_NOTURNO"] = pd.Series(0, index=out.index, dtype=int)

    if "DESTINATARIO_REPETIDO" not in out.columns:
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


def classificar_risco(score: float) -> str:
    if score <= 30:
        return "baixo risco"
    if score <= 60:
        return "risco medio"
    if score <= 80:
        return "alto risco"
    return "risco critico"


def calcular_score(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    out["RISK_SCORE"] = 0

    out.loc[out["VALOR_RETIRADA"] > 3000, "RISK_SCORE"] += 30
    out.loc[out["HORA_TRANSACAO"].between(0, 5, inclusive="both"), "RISK_SCORE"] += 20
    out.loc[
        out["METODO_PAGAMENTO_ESPECIFICO"].astype(str).str.upper().str.contains("PIX", na=False),
        "RISK_SCORE",
    ] += 10
    out.loc[out["STATUS"].astype(str).str.upper().ne("PAGO"), "RISK_SCORE"] += 10

    org_per_receiver = out.groupby("CONTA_RECEBEDOR")["ID_ORGANIZACAO"].transform("nunique")
    out.loc[org_per_receiver > 5, "RISK_SCORE"] += 25

    org_avg = out.groupby("ID_ORGANIZACAO")["VALOR_RETIRADA"].transform("mean").fillna(0)
    out.loc[out["VALOR_RETIRADA"] > (5 * org_avg), "RISK_SCORE"] += 30

    out["RISK_SCORE"] = out["RISK_SCORE"].clip(0, 100)
    out["RISK_LEVEL"] = out["RISK_SCORE"].apply(classificar_risco)

    # SUSPEITA: regra de negócio oficial — não usar RISK_SCORE nem RISK_LEVEL.
    # 1 = VALOR_ALTO e (SAQUE_NOTURNO ou DESTINATARIO_REPETIDO).
    _ensure_suspeita_input_columns(out)
    idx = out.index
    va = _flag_series_to_int(out["VALOR_ALTO"], idx)
    sn = _flag_series_to_int(out["SAQUE_NOTURNO"], idx)
    dr = _flag_series_to_int(out["DESTINATARIO_REPETIDO"], idx)
    out["SUSPEITA"] = ((va == 1) & ((sn == 1) | (dr == 1))).astype(int)
    return out
