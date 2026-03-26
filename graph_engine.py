from typing import Any

import numpy as np
import networkx as nx
import pandas as pd


def build_fraud_graph(df: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    if df.empty:
        return graph

    edges = df[["ID_ORGANIZACAO", "CONTA_RECEBEDOR", "VALOR_RETIRADA"]].copy()
    edges = edges.dropna(subset=["ID_ORGANIZACAO", "CONTA_RECEBEDOR"])
    if edges.empty:
        return graph

    vr = pd.to_numeric(edges["VALOR_RETIRADA"], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
    edges["VALOR_RETIRADA"] = vr

    # Uma aresta por par (org, conta): evita iterrows em 100k+ linhas (muito mais rápido).
    agg = edges.groupby(["ID_ORGANIZACAO", "CONTA_RECEBEDOR"], as_index=False).agg(
        weight=("VALOR_RETIRADA", "sum"),
        transactions=("VALOR_RETIRADA", "count"),
    )
    for org, conta, w, ntx in zip(
        agg["ID_ORGANIZACAO"],
        agg["CONTA_RECEBEDOR"],
        agg["weight"],
        agg["transactions"],
    ):
        wf = float(w)
        if not np.isfinite(wf):
            wf = 0.0
        graph.add_edge(f"ORG::{org}", f"CONTA::{conta}", weight=wf, transactions=int(ntx))

    return graph


def graph_metrics(graph: nx.DiGraph, df: pd.DataFrame | None = None) -> dict[str, Any]:
    if graph.number_of_nodes() == 0:
        return {
            "num_contas": 0,
            "num_conexoes": 0,
            "top_volume_recebido": pd.DataFrame(),
            "top_remetentes_por_conta": pd.DataFrame(),
            "contas_alto_volume": pd.DataFrame(),
            "clusters_suspeitos": 0,
            "clusters_detalhes": pd.DataFrame(),
            "cluster_id_to_nodes": {},
            "contas_passagem": pd.DataFrame(),
        }

    base = pd.DataFrame()
    if df is not None and not df.empty:
        base = df.copy()
        if "ID_PLACE" not in base.columns:
            base["ID_PLACE"] = base.get("ID_ORGANIZACAO")
        if "NOME_PLACE" not in base.columns:
            base["NOME_PLACE"] = base.get("NOME_ORGANIZACAO")
        base["conta"] = base["CONTA_RECEBEDOR"].astype(str)

    if not base.empty:
        place_by_conta = (
            base.groupby(["conta", "ID_PLACE", "NOME_PLACE"], as_index=False)["VALOR_RETIRADA"]
            .sum()
            .sort_values("VALOR_RETIRADA", ascending=False)
            .drop_duplicates(subset=["conta"])
            .rename(columns={"VALOR_RETIRADA": "valor_place"})
        )
        volume_df = (
            base.groupby("conta", as_index=False)["VALOR_RETIRADA"]
            .sum()
            .rename(columns={"VALOR_RETIRADA": "volume_recebido"})
        )
        sender_df = (
            base.groupby("conta", as_index=False)["ID_PLACE"]
            .nunique()
            .rename(columns={"ID_PLACE": "num_remetentes"})
        )
        top_volume = (
            volume_df.merge(place_by_conta[["conta", "ID_PLACE", "NOME_PLACE"]], on="conta", how="left")
            .sort_values("volume_recebido", ascending=False)
            .head(10)
        )
        top_senders = (
            sender_df.merge(place_by_conta[["conta", "ID_PLACE", "NOME_PLACE"]], on="conta", how="left")
            .merge(volume_df, on="conta", how="left")
            .sort_values(["num_remetentes", "volume_recebido"], ascending=False)
            .head(10)
        )
    else:
        incoming = {}
        sender_counts = {}
        for _, target, attrs in graph.edges(data=True):
            incoming[target] = incoming.get(target, 0.0) + attrs.get("weight", 0.0)
            sender_counts[target] = sender_counts.get(target, 0) + 1
        volume_df = pd.DataFrame(
            [{"conta": k.replace("CONTA::", ""), "volume_recebido": v} for k, v in incoming.items()]
        )
        sender_df = pd.DataFrame(
            [{"conta": k.replace("CONTA::", ""), "num_remetentes": v} for k, v in sender_counts.items()]
        )
        top_volume = volume_df.sort_values("volume_recebido", ascending=False).head(10)
        top_senders = sender_df.sort_values("num_remetentes", ascending=False).head(10)

    if not volume_df.empty:
        pct95 = volume_df["volume_recebido"].quantile(0.95)
        high_volume = volume_df[volume_df["volume_recebido"] >= pct95].sort_values(
            "volume_recebido", ascending=False
        )
    else:
        high_volume = pd.DataFrame(columns=["conta", "volume_recebido"])

    undirected = graph.to_undirected()
    clusters = [c for c in nx.connected_components(undirected) if len(c) >= 4]
    cluster_rows = []
    cluster_id_to_nodes = {}
    id_to_name = {}
    if not base.empty:
        id_to_name = (
            base[["ID_PLACE", "NOME_PLACE"]]
            .dropna()
            .drop_duplicates()
            .astype(str)
            .set_index("ID_PLACE")["NOME_PLACE"]
            .to_dict()
        )
    # Chave (org, conta) para mapear transações do base ao cluster (usa score existente)
    id_org_col = "ID_ORGANIZACAO" if "ID_ORGANIZACAO" in base.columns else "ID_PLACE"
    score_col = "RISK_SCORE" if "RISK_SCORE" in base.columns else None
    susp_col = "SUSPEITA" if "SUSPEITA" in base.columns else None
    hora_col = "HORA_TRANSACAO" if "HORA_TRANSACAO" in base.columns else None

    for idx, cluster_nodes in enumerate(clusters, start=1):
        nodes = set(cluster_nodes)
        sub = graph.subgraph(nodes)
        contas = [n for n in nodes if str(n).startswith("CONTA::")]
        orgs = [n for n in nodes if str(n).startswith("ORG::")]
        org_ids = [str(n).replace("ORG::", "") for n in orgs]
        org_names = [id_to_name.get(pid, "") for pid in org_ids if id_to_name.get(pid, "")]
        total_volume = sum(attrs.get("weight", 0.0) for _, _, attrs in sub.edges(data=True))
        num_transactions = sum(int(attrs.get("transactions", 1)) for _, _, attrs in sub.edges(data=True))

        num_contas = len(contas)
        num_tx = num_transactions
        score_cluster = None
        risk_level = "baixo"
        padrao_list = []
        prioridade = 4

        if not base.empty and id_org_col in base.columns:
            cluster_edges_str = {
                f"{s.replace('ORG::', '')}_{t.replace('CONTA::', '')}"
                for s, t in sub.edges()
            }
            base["_ek"] = base[id_org_col].astype(str) + "_" + base["CONTA_RECEBEDOR"].astype(str)
            cluster_df = base[base["_ek"].isin(cluster_edges_str)].copy()

            if not cluster_df.empty:
                # Prioridade / risco do cluster pela taxa de SUSPEITA=1 (regra de negócio), não por média de score.
                if susp_col and susp_col in cluster_df.columns:
                    stv = pd.to_numeric(cluster_df[susp_col], errors="coerce").fillna(0)
                    taxa_susp = float((stv == 1).mean()) if len(cluster_df) else 0.0
                    score_cluster = taxa_susp * 100.0
                    if taxa_susp >= 0.30:
                        risk_level = "crítico"
                        prioridade = 1
                    elif taxa_susp >= 0.15:
                        risk_level = "alto"
                        prioridade = 2
                    elif taxa_susp >= 0.05:
                        risk_level = "médio"
                        prioridade = 3
                    else:
                        risk_level = "baixo"
                        prioridade = 4
                elif score_col and score_col in cluster_df.columns:
                    soma_score = float(cluster_df[score_col].sum())
                    score_cluster = soma_score / num_contas if num_contas else 0.0
                    if score_cluster >= 80:
                        risk_level = "crítico"
                        prioridade = 1
                    elif score_cluster >= 60:
                        risk_level = "alto"
                        prioridade = 2
                    elif score_cluster >= 30:
                        risk_level = "médio"
                        prioridade = 3
                    else:
                        risk_level = "baixo"
                        prioridade = 4
                else:
                    score_cluster = 0.0

                total_val = float(cluster_df["VALOR_RETIRADA"].sum()) if "VALOR_RETIRADA" in cluster_df.columns else total_volume
                num_tx = len(cluster_df)  # sobrescreve para uso em padrao e na linha final

                # Padrão: concentração financeira > 20% → Concentrador
                if "VALOR_RETIRADA" in cluster_df.columns and id_org_col in cluster_df.columns and total_val > 0:
                    vol_por_org = cluster_df.groupby(id_org_col)["VALOR_RETIRADA"].sum()
                    pct_max = float(vol_por_org.max()) / total_val if vol_por_org.size else 0
                    if pct_max > 0.20:
                        padrao_list.append("Concentrador")
                # Múltiplos remetentes → Distribuição
                if len(orgs) > 1:
                    padrao_list.append("Distribuição")
                # Alta frequência (transações por conta)
                if num_contas > 0 and num_tx / num_contas >= 5:
                    padrao_list.append("Alta atividade")
                # Predominância noturna (HORA 0–5)
                if hora_col and hora_col in cluster_df.columns:
                    noturnas = (cluster_df[hora_col].between(0, 5, inclusive="both")).sum()
                    if num_tx > 0 and (noturnas / num_tx) > 0.5:
                        padrao_list.append("Noturno")

        padrao_cluster = ", ".join(padrao_list) if padrao_list else "—"
        if score_cluster is None:
            score_cluster = 0.0

        # Log opcional: etapas do cálculo por cluster (descomente para debug)
        # print(f"[Cluster {idx}] contas={num_contas} tx={num_tx} soma_score→score_cluster={score_cluster:.1f} risk={risk_level} prioridade={prioridade} padrao={padrao_cluster}")

        cluster_id_to_nodes[idx] = list(nodes)
        cluster_rows.append(
            {
                "cluster_id": idx,
                "ID_PLACE": ", ".join(org_ids[:5]),
                "NOME_PLACE": ", ".join(org_names[:5]),
                "num_nos": len(nodes),
                "num_contas": num_contas,
                "num_organizacoes": len(orgs),
                "num_conexoes": sub.number_of_edges(),
                "num_transactions": num_tx,
                "volume_total": float(total_volume),
                "score_cluster": float(score_cluster),
                "risk_level": risk_level,
                "padrao_cluster": padrao_cluster,
                "prioridade": prioridade,
            }
        )

    clusters_df = pd.DataFrame(cluster_rows) if cluster_rows else pd.DataFrame()
    if not clusters_df.empty:
        clusters_df = clusters_df.sort_values(["prioridade", "volume_total"], ascending=[True, False])

    merged = sender_df.merge(volume_df, on="conta", how="inner") if (not sender_df.empty and not volume_df.empty) else pd.DataFrame()
    if not merged.empty:
        volume_cut = merged["volume_recebido"].quantile(0.75)
        bridges_df = merged[
            (merged["num_remetentes"] >= 3) & (merged["volume_recebido"] >= volume_cut)
        ].sort_values(["num_remetentes", "volume_recebido"], ascending=False)
    else:
        bridges_df = pd.DataFrame(columns=["conta", "num_remetentes", "volume_recebido"])

    return {
        "num_contas": sum(1 for n in graph.nodes if str(n).startswith("CONTA::")),
        "num_conexoes": graph.number_of_edges(),
        "top_volume_recebido": top_volume,
        "top_remetentes_por_conta": top_senders,
        "contas_alto_volume": high_volume.head(20),
        "clusters_suspeitos": len(clusters),
        "clusters_detalhes": clusters_df.head(20),
        "cluster_id_to_nodes": cluster_id_to_nodes,
        "contas_passagem": bridges_df.head(20),
    }
