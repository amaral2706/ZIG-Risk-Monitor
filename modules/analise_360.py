"""
Análise 360 de conta para detecção de fraude.

Usa exclusivamente o RISK_SCORE já calculado pelas regras existentes.
Não altera o cálculo de score; apenas agrega métricas, classificações e recomendações.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import networkx as nx


def _finite_num(x: object, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _log(msg: str, log_fn: Callable[[str], None] | None = None) -> None:
    """Log/print para acompanhamento das etapas (cálculo, classificação, recomendação)."""
    line = f"[360] {msg}"
    if log_fn is not None:
        log_fn(line)
    else:
        print(line)


def analise_conta_360(
    conta_df: pd.DataFrame,
    graph: "nx.DiGraph",
    metrics: dict,
    node_categories: dict,
    conta_id: str,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> dict:
    """
    Análise 360 da conta: métricas, classificações e recomendações.

    Utiliza apenas regras e score já existentes (RISK_SCORE). Não altera lógica de risco.

    Retorna dict com:
    - ticket_medio, max_valor, min_valor, score_medio
    - tipo_conta, comportamento (lista)
    - concentracao_remetente, alta_frequencia, comportamento_noturno, recorrencia (flags)
    - classificacao (baixo|médio|alto|crítico), recomendacao
    - cluster_id, num_conexoes, tipo_rede (contexto de rede)
    """
    out = {
        "ticket_medio": 0.0,
        "max_valor": 0.0,
        "min_valor": 0.0,
        "score_medio": 0.0,
        "tipo_conta": "—",
        "comportamento": [],
        "concentracao_remetente": False,
        "alta_frequencia": False,
        "comportamento_noturno": False,
        "recorrencia": False,
        "classificacao": "baixo",
        "recomendacao": "Sem ação",
        "cluster_id": None,
        "num_conexoes": 0,
        "tipo_rede": "isolado",
    }
    if conta_df.empty:
        return out

    vfin = (
        pd.to_numeric(conta_df["VALOR_RETIRADA"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        if "VALOR_RETIRADA" in conta_df.columns
        else pd.Series(dtype=float)
    )
    total_valor = float(vfin.sum()) if len(vfin) else 0.0
    total_tx = len(conta_df)
    num_remetentes = conta_df["ID_PLACE"].nunique() if "ID_PLACE" in conta_df.columns else 0
    if "ID_ORGANIZACAO" in conta_df.columns:
        num_remetentes = conta_df["ID_ORGANIZACAO"].nunique()

    # ---------- Métricas obrigatórias ----------
    out["ticket_medio"] = total_valor / total_tx if total_tx else 0.0
    if "VALOR_RETIRADA" in conta_df.columns and len(vfin):
        out["max_valor"] = _finite_num(float(vfin.max()))
        out["min_valor"] = _finite_num(float(vfin.min()))
    else:
        out["max_valor"] = 0.0
        out["min_valor"] = 0.0
    if "RISK_SCORE" in conta_df.columns:
        rs = pd.to_numeric(conta_df["RISK_SCORE"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        out["score_medio"] = float(rs.mean()) if len(rs) else 0.0
    out["ticket_medio"] = _finite_num(out["ticket_medio"])
    out["max_valor"] = _finite_num(out["max_valor"])
    out["min_valor"] = _finite_num(out["min_valor"])
    out["score_medio"] = _finite_num(out["score_medio"])
    _log(
        f"Métricas: ticket_medio={out['ticket_medio']:.2f} max_valor={out['max_valor']:.2f} "
        f"min_valor={out['min_valor']:.2f} score_medio={out['score_medio']:.1f}",
        log_fn=log_fn,
    )

    # ---------- Análise comportamental: tipo de conta ----------
    if num_remetentes == 1:
        out["tipo_conta"] = "Conta concentrada"
        out["concentracao_remetente"] = True
    elif 2 <= num_remetentes <= 5:
        out["tipo_conta"] = "Conta intermediária"
    else:
        out["tipo_conta"] = "Conta distribuída"
    _log(f"Classificação tipo: {out['tipo_conta']} (remetentes={num_remetentes})", log_fn=log_fn)

    # ---------- Frequência: muitas transações em curto período ----------
    if "DATA_SOLICITACAO" in conta_df.columns and total_tx >= 2:
        dt = pd.to_datetime(conta_df["DATA_SOLICITACAO"].dropna(), errors="coerce")
        dt = dt.sort_values()
        if len(dt) >= 2:
            diff_min = (dt.diff().dropna().dt.total_seconds() / 60).abs()
            mediana_min = diff_min.median()
            if mediana_min < 120:  # menos de 2h entre transações
                out["alta_frequencia"] = True
                out["comportamento"].append("Alta frequência")
        tx_por_dia = conta_df.copy()
        tx_por_dia["_dia"] = pd.to_datetime(tx_por_dia["DATA_SOLICITACAO"], errors="coerce").dt.date
        por_dia = tx_por_dia.groupby("_dia", as_index=False).size()
        if (por_dia["size"] >= 5).any():
            out["alta_frequencia"] = True
            if "Alta frequência" not in out["comportamento"]:
                out["comportamento"].append("Alta frequência")
    _log(f"Alta frequência: {out['alta_frequencia']}", log_fn=log_fn)

    # ---------- Comportamento noturno (22h–06h) ----------
    if "HORA_TRANSACAO" in conta_df.columns:
        hora = conta_df["HORA_TRANSACAO"].dropna()
        noturno = ((hora >= 22) | (hora <= 6)).any()
        out["comportamento_noturno"] = bool(noturno)
        if noturno:
            out["comportamento"].append("Noturno")
    _log(f"Comportamento noturno: {out['comportamento_noturno']}", log_fn=log_fn)

    # ---------- Destinatário recorrente (mesmo remetente várias vezes) ----------
    id_rem = "ID_ORGANIZACAO" if "ID_ORGANIZACAO" in conta_df.columns else "ID_PLACE"
    if id_rem in conta_df.columns:
        rec = conta_df.groupby(id_rem).size()
        out["recorrencia"] = (rec > 1).any()
        if out["recorrencia"] and "Recorrente" not in out["comportamento"]:
            out["comportamento"].append("Recorrente")
    _log(f"Recorrência: {out['recorrencia']}", log_fn=log_fn)

    # ---------- Classificação final (score médio: 0–29, 30–59, 60–79, 80+) ----------
    s = out["score_medio"]
    if s >= 80:
        out["classificacao"] = "crítico"
        out["recomendacao"] = "Bloqueio preventivo"
    elif s >= 60:
        out["classificacao"] = "alto"
        out["recomendacao"] = "Investigar"
    elif s >= 30:
        out["classificacao"] = "médio"
        out["recomendacao"] = "Monitorar"
    else:
        out["classificacao"] = "baixo"
        out["recomendacao"] = "Sem ação"
    _log(f"Classificação final: {out['classificacao']} → Recomendação: {out['recomendacao']}", log_fn=log_fn)

    # ---------- Contexto de rede (cluster_id, número de conexões, tipo) ----------
    node_key = f"CONTA::{conta_id}"
    if graph is not None and hasattr(graph, "edges"):
        out["num_conexoes"] = sum(1 for _u, v in graph.edges() if v == node_key)
    cat = node_categories.get(node_key, "normal") if node_categories else "normal"
    if cat == "alert":
        out["tipo_rede"] = "hub"
    elif cat == "bridge":
        out["tipo_rede"] = "intermediário"
    else:
        out["tipo_rede"] = "isolado"
    cluster_id_to_nodes = metrics.get("cluster_id_to_nodes") or {}
    for cid, nodes in cluster_id_to_nodes.items():
        if node_key in nodes:
            out["cluster_id"] = cid
            break
    _log(f"Rede: cluster_id={out['cluster_id']} conexoes={out['num_conexoes']} tipo={out['tipo_rede']}", log_fn=log_fn)

    return out
