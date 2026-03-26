from pathlib import Path
import json
import math
import random
import re
import sys
from datetime import date, timedelta

import numpy as np
import networkx as nx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from pyvis.network import Network

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from modules.analise_360 import analise_conta_360
from modules.auth import ensure_authenticated
from modules.data_loader import (
    date_column_for_period_filter,
    filter_by_brazil_calendar_dates,
    load_processed_data,
    load_raw_data,
)
from modules.date_session import CALENDAR_MAX, CALENDAR_MIN, sync_global_date_inputs
from modules.feature_engineering import add_features
from modules.graph_engine import build_fraud_graph, graph_metrics
from modules.risk_engine import calcular_score
from modules.table_columns import (
    DATA_CRIACAO_COL,
    add_placeholder_data_criacao_column,
    fill_data_criacao_place_e_org,
    format_data_criacao_series,
    format_row_data_criacao_display,
    reorder_data_criacao_first,
)
# Um único import de ui (tema + formatação): ui carrega mesmo sem modules/formatting.py
from modules.ui import (
    apply_enterprise_theme,
    aplicar_estilo_global,
    format_brl,
    format_currency_columns,
    format_metric_float,
    sanitize_plotly_title_and_legend,
)

# Dimensões fixas do grafo Plotly — evita loop "Too many auto-margin redraws" com use_container_width + autosize.
RT_NETWORK_PLOT_WIDTH = 920
RT_NETWORK_PLOT_HEIGHT = 720


def _parse_receiver_nome_cnpj(val):
    """Extrai nome e CPF/CNPJ do campo RECEIVER (string ou JSON como {"nome":"...","cnpj":"..."})."""
    if pd.isna(val) or val == "":
        return "Não informado", "Não informado"
    s = str(val).strip()
    if not s or s == "nan":
        return "Não informado", "Não informado"
    if s.startswith("{"):
        try:
            d = json.loads(s) if isinstance(s, str) else s
            nome = (d.get("nome") or d.get("name") or "").strip() or "Não informado"
            doc = (d.get("cnpj") or d.get("cpf") or d.get("doc") or "").strip() or "Não informado"
            return nome, doc
        except (json.JSONDecodeError, TypeError, AttributeError):
            return s, "Não informado"
    return s, "Não informado"


def _node_stats(graph: nx.DiGraph) -> tuple[dict, dict, dict]:
    """Returns (account_volume, account_senders, account_transactions) for CONTA nodes."""
    account_volume = {}
    account_senders = {}
    account_transactions = {}
    for source, target, attrs in graph.edges(data=True):
        if str(target).startswith("CONTA::"):
            try:
                wf = float(attrs.get("weight", 0.0))
            except (TypeError, ValueError):
                wf = 0.0
            if not math.isfinite(wf):
                wf = 0.0
            account_volume[target] = account_volume.get(target, 0.0) + wf
            account_senders.setdefault(target, set()).add(source)
            account_transactions[target] = account_transactions.get(target, 0) + int(attrs.get("transactions", 1))
    return account_volume, account_senders, account_transactions


def _classify_nodes(graph: nx.DiGraph) -> dict[str, str]:
    account_volume, account_senders, _ = _node_stats(graph)
    if account_volume:
        sorted_volumes = sorted(account_volume.values())
        p90 = sorted_volumes[min(int(0.9 * len(sorted_volumes)), len(sorted_volumes) - 1)]
    else:
        p90 = 0.0

    category = {}
    for node in graph.nodes():
        if str(node).startswith("ORG::"):
            category[node] = "vitima"
            continue
        senders = len(account_senders.get(node, set()))
        volume = account_volume.get(node, 0.0)
        if senders >= 6 or volume >= p90:
            category[node] = "alert"
        elif senders >= 3:
            category[node] = "bridge"
        else:
            category[node] = "normal"
    return category


def _graph_positions(graph: nx.DiGraph, categories: dict[str, str]) -> dict[str, tuple[float, float]]:
    rng = random.Random(42)
    pos = {}
    org_nodes = [n for n in graph.nodes() if str(n).startswith("ORG::")]
    bridge_nodes = [n for n in graph.nodes() if categories.get(n) == "bridge"]
    normal_nodes = [n for n in graph.nodes() if categories.get(n) == "normal"]
    alert_nodes = [n for n in graph.nodes() if categories.get(n) == "alert"]

    for node in org_nodes:
        pos[node] = (rng.uniform(-1.0, -0.15), rng.uniform(-0.85, 0.85))
    for node in bridge_nodes:
        pos[node] = (rng.uniform(-0.15, 0.65), rng.uniform(-0.75, 0.75))
    for node in normal_nodes:
        pos[node] = (rng.uniform(-0.75, -0.05), rng.uniform(-0.8, 0.8))
    for node in alert_nodes:
        pos[node] = (rng.uniform(0.2, 1.0), rng.uniform(-0.5, 0.5))
    return pos


def _top_weight_subgraph(graph: nx.DiGraph, max_edges: int) -> nx.DiGraph:
    if graph.number_of_edges() <= max_edges:
        return graph.copy()
    ranked_edges = sorted(graph.edges(data=True), key=lambda e: e[2].get("weight", 0.0), reverse=True)[:max_edges]
    sub = nx.DiGraph()
    for source, target, attrs in ranked_edges:
        sub.add_edge(source, target, **attrs)
    return sub


def _plot_graph(graph: nx.DiGraph, selected_node: str | None = None) -> go.Figure:
    if graph.number_of_nodes() == 0:
        return go.Figure()

    categories = _classify_nodes(graph)
    pos = _graph_positions(graph, categories)
    account_volume, account_senders, account_transactions = _node_stats(graph)

    # Arestas: destacar as que tocam o nó selecionado
    edge_x, edge_y = [], []
    edge_x_sel, edge_y_sel = [], []
    for source, target in graph.edges():
        x0, y0 = pos[source]
        x1, y1 = pos[target]
        if selected_node and (source == selected_node or target == selected_node):
            edge_x_sel += [x0, x1, None]
            edge_y_sel += [y0, y1, None]
        else:
            edge_x += [x0, x1, None]
            edge_y += [y0, y1, None]

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y,
        line=dict(width=1.0, color="rgba(71,85,105,0.35)"),
        hoverinfo="none", mode="lines", showlegend=False,
    )
    traces = [edge_trace]
    if edge_x_sel:
        traces.append(
            go.Scatter(
                x=edge_x_sel, y=edge_y_sel,
                line=dict(width=2.5, color="rgba(239,68,68,0.85)"),
                hoverinfo="none", mode="lines", showlegend=False,
            )
        )

    style_map = {
        "vitima": {"label": "Place (origem)", "color": "#94A3B8"},
        "normal": {"label": "Conta normal", "color": "#3B82F6"},
        "bridge": {"label": "Conta intermediária", "color": "#F59E0B"},
        "alert": {"label": "Conta suspeita", "color": "#EF4444"},
    }

    for cat_key in ["vitima", "normal", "bridge", "alert"]:
        nodes = [n for n in graph.nodes() if categories.get(n) == cat_key]
        if not nodes:
            continue
        labels = []
        for n in nodes:
            acc = str(n).replace("ORG::", "").replace("CONTA::", "")
            vol = account_volume.get(n, 0.0)
            tx_count = account_transactions.get(n, 0)
            senders = len(account_senders.get(n, set()))
            if cat_key == "vitima":
                labels.append(f"Place: {acc}")
            else:
                labels.append(
                    f"Conta: {acc}<br>"
                    f"Valor recebido: {format_brl(vol)}<br>"
                    f"Transações: {tx_count}<br>"
                    f"Remetentes: {senders}"
                )
        is_selected = [n == selected_node for n in nodes]
        node_ids = [str(n) for n in nodes]
        size = [18 if sel else (14 if cat_key == "alert" else 13) for sel in is_selected]
        line_w = [3.0 if sel else 1.2 for sel in is_selected]
        line_c = ["#0F172A" if sel else "rgba(255,255,255,0.7)" for sel in is_selected]
        traces.append(
            go.Scatter(
                x=[pos[n][0] for n in nodes],
                y=[pos[n][1] for n in nodes],
                mode="markers",
                name=style_map[cat_key]["label"],
                hovertemplate="%{text}<extra></extra>",
                text=labels,
                customdata=[[nid] for nid in node_ids],
                marker=dict(
                    size=size,
                    color=style_map[cat_key]["color"],
                    line=dict(width=line_w, color=line_c),
                ),
            )
        )

    fig = go.Figure(data=traces)
    sanitize_plotly_title_and_legend(fig)
    fig.update_layout(
        title=dict(
            text="Rede de transações (Place → Conta recebedora)",
            font=dict(size=18, color="#0047BB"),
            y=0.98,
            x=0.01,
            xanchor="left",
            yanchor="top",
        ),
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(color="#0047BB", size=14),
        showlegend=True,
        hovermode="closest",
        # Trava layout: sem autosize responsivo + sem automargin nos eixos (causa típica do warning Plotly.js).
        autosize=False,
        width=RT_NETWORK_PLOT_WIDTH,
        height=RT_NETWORK_PLOT_HEIGHT,
        uirevision="zig_rt_network",
        margin=dict(t=56, l=12, r=12, b=100),
        legend=dict(
            orientation="h",
            yanchor="top",
            y=-0.22,
            xanchor="center",
            x=0.5,
            bgcolor="rgba(255,255,255,0.97)",
            bordercolor="#E3E8EE",
            borderwidth=1,
            font=dict(size=14, color="#0047BB"),
        ),
        hoverlabel=dict(
            font=dict(size=14, family="Arial, sans-serif", color="black"),
            bgcolor="white",
            bordercolor="#0047BB",
        ),
        xaxis=dict(
            visible=False,
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            automargin=False,
            fixedrange=True,
            range=[-1.2, 1.2],
        ),
        yaxis=dict(
            visible=False,
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            automargin=False,
            fixedrange=True,
            range=[-1.05, 1.05],
            scaleanchor="x",
            scaleratio=1,
        ),
    )
    return fig


def _build_pyvis_html(graph: nx.DiGraph) -> str:
    categories = _classify_nodes(graph)
    account_volume, account_senders, account_transactions = _node_stats(graph)
    net = Network(height="720px", width="100%", directed=True, bgcolor="#ffffff", font_color="#0047BB")
    for node in graph.nodes():
        cat = categories.get(node, "normal")
        color = {"vitima": "#94A3B8", "normal": "#3B82F6", "bridge": "#F59E0B", "alert": "#EF4444"}.get(cat, "#3B82F6")
        label = str(node).replace("CONTA::", "").replace("ORG::", "")
        vol = account_volume.get(node, 0.0)
        tx_count = account_transactions.get(node, 0)
        senders = len(account_senders.get(node, set()))
        title = f"Conta: {label}<br>Valor recebido: {format_brl(vol)}<br>Transações: {tx_count}<br>Remetentes: {senders}"
        net.add_node(node, label=label, color=color, title=title)
    for source, target, attrs in graph.edges(data=True):
        try:
            wt = float(attrs.get("weight", 0.0))
        except (TypeError, ValueError):
            wt = 0.0
        if not math.isfinite(wt):
            wt = 0.0
        net.add_edge(source, target, value=wt, title=f"Peso: {wt:,.2f}")
    net.set_options(
        """
        const options = {
          "nodes": {"shape": "dot", "size": 11, "font": {"size": 12, "color": "#0047BB"}},
          "edges": {"arrows": {"to": {"enabled": true}}},
          "physics": {"stabilization": true}
        }
        """
    )
    html = net.generate_html()
    # Injetar script para capturar clique no nó e redirecionar para aba de detalhamento
    # Pyvis gera "var X = new vis.Network(container, data, options);" - nome da variável pode variar
    pattern = re.compile(r"var\s+(\w+)\s*=\s*new\s+vis\.Network\s*\([^)]+\)\s*;")
    match = pattern.search(html)
    if match:
        var_name = match.group(1)
        click_handler = (
            "window.network = " + var_name + ";\n"
            + var_name + ".on('click', function(params) {\n"
            "  if (params.nodes.length > 0) {\n"
            "    var id = params.nodes[0];\n"
            "    if (String(id).indexOf('CONTA::') === 0) {\n"
            "      var c = String(id).replace(/^CONTA::/, '');\n"
            "      var loc = (window.parent && window.parent.location) ? window.parent.location : window.location;\n"
            "      var base = loc.pathname || '/';\n"
            "      var sep = base.indexOf('?') >= 0 ? '&' : '?';\n"
            "      loc.assign(base + sep + 'conta=' + encodeURIComponent(c));\n"
            "    }\n"
            "  }\n"
            "});\n"
        )
        full_stmt = match.group(0)
        html = html.replace(full_stmt, full_stmt + "\n" + click_handler, 1)
    return html


def _overview_metrics(df: pd.DataFrame):
    """Métricas da visão geral a partir do DataFrame (vetorizado, respeitando filtros de data)."""
    n = len(df)
    if n == 0:
        return {
            "total_contas": 0,
            "contas_suspeitas": 0,
            "pct_contas_suspeitas": 0.0,
            "total_transacoes": 0,
            "volume_total": 0.0,
            "volume_suspeito": 0.0,
            "pct_transacoes_suspeitas": 0.0,
            "score_medio": 0.0,
            "hub_conexoes": 0,
        }
    total_contas = int(df["CONTA_RECEBEDOR"].nunique()) if "CONTA_RECEBEDOR" in df.columns else 0
    col_suspeita = df.get("SUSPEITA")
    if col_suspeita is not None and not col_suspeita.empty:
        suspeita_num = pd.to_numeric(col_suspeita, errors="coerce").fillna(0)
        contas_suspeitas = int(df.loc[suspeita_num == 1, "CONTA_RECEBEDOR"].nunique()) if "CONTA_RECEBEDOR" in df.columns else 0
        if "VALOR_RETIRADA" in df.columns:
            vs = pd.to_numeric(df.loc[suspeita_num == 1, "VALOR_RETIRADA"], errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            ).fillna(0.0)
            volume_suspeito = float(vs.sum())
        else:
            volume_suspeito = 0.0
        pct_transacoes_suspeitas = (float(suspeita_num.sum()) / n) * 100.0 if n else 0.0
    else:
        contas_suspeitas = 0
        volume_suspeito = 0.0
        pct_transacoes_suspeitas = 0.0
    pct_contas_suspeitas = (contas_suspeitas / total_contas * 100.0) if total_contas else 0.0
    if "VALOR_RETIRADA" in df.columns:
        vall = pd.to_numeric(df["VALOR_RETIRADA"], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        volume_total = float(vall.sum())
    else:
        volume_total = 0.0
    if "RISK_SCORE" in df.columns:
        rs = pd.to_numeric(df["RISK_SCORE"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        score_medio = float(rs.mean()) if len(rs) else 0.0
    else:
        score_medio = 0.0
    if not math.isfinite(volume_suspeito):
        volume_suspeito = 0.0
    if not math.isfinite(volume_total):
        volume_total = 0.0
    if not math.isfinite(pct_transacoes_suspeitas):
        pct_transacoes_suspeitas = 0.0
    if not math.isfinite(pct_contas_suspeitas):
        pct_contas_suspeitas = 0.0
    if not math.isfinite(score_medio):
        score_medio = 0.0
    hub_conexoes = 0
    if "CONTA_RECEBEDOR" in df.columns and "RECEIVER" in df.columns:
        hub_conexoes = int(df.groupby("CONTA_RECEBEDOR")["RECEIVER"].nunique().max()) if not df.empty else 0
    return {
        "total_contas": total_contas,
        "contas_suspeitas": contas_suspeitas,
        "pct_contas_suspeitas": pct_contas_suspeitas,
        "total_transacoes": n,
        "volume_total": volume_total,
        "volume_suspeito": volume_suspeito,
        "pct_transacoes_suspeitas": pct_transacoes_suspeitas,
        "score_medio": score_medio,
        "hub_conexoes": hub_conexoes,
    }


def _num_remetentes_conta(conta_df: pd.DataFrame) -> int:
    """Quantidade de remetentes distintos na conta (mesma lógica da análise 360)."""
    if conta_df.empty:
        return 0
    if "ID_ORGANIZACAO" in conta_df.columns:
        return int(conta_df["ID_ORGANIZACAO"].nunique())
    if "ID_PLACE" in conta_df.columns:
        return int(conta_df["ID_PLACE"].nunique())
    return 0


def _ticket_medio_alto_relativo(ticket_medio: float, filtered: pd.DataFrame) -> bool:
    """Ticket médio da conta acima do P75 da rede (período filtrado), com piso em R$ 25 mil."""
    if ticket_medio <= 0 or filtered.empty:
        return False
    if "CONTA_RECEBEDOR" not in filtered.columns or "VALOR_RETIRADA" not in filtered.columns:
        return ticket_medio >= 50000
    try:
        g = filtered.groupby("CONTA_RECEBEDOR", as_index=False).agg(
            soma=("VALOR_RETIRADA", "sum"),
            n=("VALOR_RETIRADA", "count"),
        )
        g = g[g["n"] > 0]
        if g.empty:
            return ticket_medio >= 50000
        tickets_rede = g["soma"] / g["n"]
        p75 = float(tickets_rede.quantile(0.75))
        limite = max(p75 * 1.25, 25000.0)
        return ticket_medio >= limite
    except Exception:
        return ticket_medio >= 50000


def _valor_total_elevado_conta(valor_total: float, filtered: pd.DataFrame) -> bool:
    """Volume total da conta acima do P80 dos totais por conta no período (valor elevado)."""
    if valor_total <= 0 or filtered.empty:
        return False
    if "CONTA_RECEBEDOR" not in filtered.columns or "VALOR_RETIRADA" not in filtered.columns:
        return valor_total >= 100_000
    try:
        por_conta = filtered.groupby("CONTA_RECEBEDOR")["VALOR_RETIRADA"].sum()
        if por_conta.empty:
            return valor_total >= 100_000
        p80 = float(por_conta.quantile(0.80))
        return valor_total >= max(p80, 50_000)
    except Exception:
        return valor_total >= 100_000


def _valor_baixo_supressao(valor_total: float, filtered: pd.DataFrame) -> bool:
    """Valor total considerado 'baixo' para suprimir falso positivo (1 tx + concentração)."""
    if valor_total <= 0:
        return True
    if filtered.empty or "CONTA_RECEBEDOR" not in filtered.columns or "VALOR_RETIRADA" not in filtered.columns:
        return valor_total < 15_000
    try:
        por_conta = filtered.groupby("CONTA_RECEBEDOR")["VALOR_RETIRADA"].sum()
        p25 = float(por_conta.quantile(0.25))
        return valor_total < max(10_000, p25 * 0.6)
    except Exception:
        return valor_total < 15_000


def _faixa_risco_score(score: float) -> tuple[str, str, str]:
    """
    Alinhado à tela Regra de Negócio: faixa, rótulo, ação sugerida.
    Retorna (chave, descrição classificação, ação).
    """
    if score >= 80:
        return "critico", "Risco crítico", "Bloqueio automático (regra de negócio)"
    if score >= 60:
        return "alto", "Alto risco", "Revisão manual"
    if score >= 30:
        return "medio", "Médio risco", "Monitorar"
    return "baixo", "Baixo risco", "Sem ação"


def _parecer_analitico_conta_text(
    filtered: pd.DataFrame,
    conta_id: str | None,
    graph: nx.DiGraph,
    metrics: dict,
    node_categories: dict,
) -> str:
    """
    Parecer alinhado às regras de negócio (tela Regra de Negócio).
    Não altera scores nem flags na base — apenas interpreta dados e analise_conta_360.
    """
    if not conta_id or not str(conta_id).strip():
        return (
            "**Selecione uma conta** na investigação 360 ou no grafo para ver o "
            "**resumo analítico** com base no período filtrado."
        )
    cid = str(conta_id).strip()
    conta_df = filtered[filtered["CONTA_RECEBEDOR"].astype(str) == cid].copy()
    if conta_df.empty:
        return f"**Conta {cid}:** sem transações no período filtrado — não há parecer analítico."

    analise = analise_conta_360(conta_df, graph, metrics, node_categories, cid, log_fn=lambda _x: None)

    n_tx = len(conta_df)
    valor_total = float(conta_df["VALOR_RETIRADA"].sum()) if "VALOR_RETIRADA" in conta_df.columns else 0.0
    num_rem = _num_remetentes_conta(conta_df)
    score_m = float(analise.get("score_medio", 0.0))
    ticket_m = float(analise.get("ticket_medio", 0.0))

    valor_baixo = _valor_baixo_supressao(valor_total, filtered)
    suprimir_falso_positivo = n_tx == 1 and valor_baixo

    valor_elevado = _valor_total_elevado_conta(valor_total, filtered)
    ticket_alto = _ticket_medio_alto_relativo(ticket_m, filtered)
    alta_freq = bool(analise.get("alta_frequencia"))
    noturno = bool(analise.get("comportamento_noturno"))
    recorrencia = bool(analise.get("recorrencia"))
    concentracao_remetente = num_rem == 1

    # Gatilho para mensagens de alerta (pelo menos uma condição — Regra de Negócio)
    concentrada_volume_relevante = concentracao_remetente and not valor_baixo and n_tx >= 2
    pode_gerar_alerta = (
        valor_elevado
        or n_tx > 2
        or alta_freq
        or score_m >= 30
        or concentrada_volume_relevante
    )

    # Comportamentos no texto só quando faz sentido
    mostrar_recorrencia = recorrencia and n_tx > 2
    mostrar_alta_freq = alta_freq and n_tx >= 2
    mostrar_noturno = noturno and n_tx >= 1

    mostrar_concentracao = concentracao_remetente and not suprimir_falso_positivo and (n_tx > 1 or valor_elevado)

    faixa, risco_label, acao_label = _faixa_risco_score(score_m)

    # Perfil na rede (Regra de Negócio §5)
    tipo = str(analise.get("tipo_conta", ""))
    if "concentrada" in tipo.lower():
        if suprimir_falso_positivo:
            perfil_rede = "**Perfil na rede:** Normal — movimentação pontual e baixa exposição."
        elif valor_elevado or n_tx >= 3:
            perfil_rede = "**Perfil na rede:** Concentradora — recebimentos com volume relevante; priorizar análise se outros indicadores confirmarem."
        else:
            perfil_rede = "**Perfil na rede:** Concentração em remetente único; avaliar evolução do volume."
    elif "intermediária" in tipo.lower():
        perfil_rede = "**Perfil na rede:** Intermediária — múltiplos remetentes."
    else:
        perfil_rede = "**Perfil na rede:** Normal — padrão mais distribuído / baixa concentração."

    partes: list[str] = []
    partes.append(
        f"Conta recebeu **{format_brl(valor_total)}** em **{n_tx}** transações, com **{num_rem}** remetente(s).\n\n"
        f"Score médio de risco: **{format_metric_float(score_m)}**.\n\n"
        f"**Classificação (faixas de score):** {risco_label} — *{acao_label}*."
    )
    partes.append(perfil_rede)

    # Indicadores comportamentais (condicionais)
    inds: list[str] = []
    if mostrar_concentracao:
        inds.append("*Concentração com único remetente (possível centralização).*")
    if ticket_alto and (n_tx > 1 or valor_elevado):
        inds.append("*Ticket médio elevado em relação à rede.*")
    if mostrar_recorrencia:
        inds.append("*Padrão recorrente (mesmo remetente em múltiplas transações).*")
    if mostrar_alta_freq:
        inds.append("*Alta frequência ou transações próximas no tempo.*")
    if mostrar_noturno:
        inds.append("*Atividade em horário noturno (22h–6h), fora do padrão usual.*")
    if inds:
        partes.append("\n".join(inds))

    suspeita_alguma = False
    if "SUSPEITA" in conta_df.columns:
        sus = pd.to_numeric(conta_df["SUSPEITA"], errors="coerce").fillna(0)
        suspeita_alguma = bool((sus == 1).any())

    # Narrativa de suspeita: alinhada à ideia de valor elevado + padrão (Regra de Negócio §3)
    noturno_e_relevante = noturno and n_tx > 1 and not valor_baixo
    criterios_suspeita_narrativa = (
        valor_elevado
        or ticket_alto
        or (mostrar_recorrencia and (noturno or alta_freq))
        or (alta_freq and n_tx > 2)
        or noturno_e_relevante
    )

    if suspeita_alguma and suprimir_falso_positivo:
        partes.append(
            "O modelo marcou **suspeita** em ao menos uma transação; com **apenas uma operação de baixo volume**, "
            "isso costuma ser **falso positivo contextual**. Recomenda-se validação pontual, sem prioridade de fraude."
        )
    elif suspeita_alguma and (criterios_suspeita_narrativa or n_tx > 2):
        partes.append("👉 **Foram identificadas transações suspeitas nesta conta** (conforme flags do modelo e padrão de valor/frequência/comportamento).")
    elif suspeita_alguma:
        partes.append(
            "Há **flag de suspeita** no modelo; os demais indicadores são **limitados** — recomenda-se **conferência** sem conclusão automática de fraude."
        )
    else:
        partes.append("*Não há transações marcadas como suspeitas pelo modelo neste recorte.*")

    # Fechamento e alertas por faixa (sem isolar uma única variável frágil)
    fatores_risco = sum(
        1
        for x in (
            valor_elevado,
            ticket_alto,
            mostrar_concentracao,
            mostrar_recorrencia,
            mostrar_alta_freq,
            mostrar_noturno,
        )
        if x
    )

    if faixa == "baixo":
        if suprimir_falso_positivo or (n_tx <= 2 and valor_baixo and score_m < 30):
            partes.append(
                "**Baixo volume e baixa recorrência**, sem indícios relevantes de risco no conjunto analisado."
            )
        elif not suspeita_alguma:
            partes.append("**Cenário compatível com baixo risco**, sem alertas prioritários pelas regras atuais.")

    elif faixa == "medio":
        if not suprimir_falso_positivo and pode_gerar_alerta and (fatores_risco >= 1 or score_m >= 30):
            partes.append("👉 **Atenção:** recomenda-se **monitoramento** conforme faixa de médio risco.")
        elif suprimir_falso_positivo:
            partes.append(
                "**Médio risco** pela faixa de score, porém **volume e frequência muito limitados** — tratar com **cautela** e evitar alarme desproporcional."
            )

    elif faixa == "alto":
        if pode_gerar_alerta and not suprimir_falso_positivo and (fatores_risco >= 2 or suspeita_alguma):
            partes.append("👉 **Risco elevado.** Recomenda-se **análise imediata** (revisão manual).")
        elif pode_gerar_alerta:
            partes.append("👉 **Risco elevado** pelo score; consolidar com **volume, remetentes e histórico** antes da decisão.")
        else:
            partes.append("Score na faixa **alta**; **contexto de volume/frequência** é limitado — recomenda-se **revisão** focada no motivo do score.")

    else:  # critico
        partes.append(
            "👉 **Risco crítico** pela faixa de score. Pelas regras de negócio, indica **bloqueio automático** / prioridade máxima até validação."
        )

    return "\n\n".join(partes)


def _safe_str(val, default: str = "Não informado"):
    if pd.isna(val) or val is None or str(val).strip() in ("", "nan"):
        return default
    return str(val).strip()


def _get_account_detail(conta_id: str, filtered: pd.DataFrame, graph: nx.DiGraph, node_categories: dict) -> dict | None:
    """Monta dicionário com dados da conta para o painel lateral. conta_id pode ser '123' ou 'CONTA::123'."""
    conta = str(conta_id).replace("CONTA::", "").strip()
    if not conta:
        return None
    node_key = f"CONTA::{conta}"
    if node_key not in graph.nodes():
        return None
    account_volume, account_senders, _ = _node_stats(graph)
    rows = filtered[filtered["CONTA_RECEBEDOR"].astype(str) == conta].copy()
    if rows.empty:
        valor_total = float(account_volume.get(node_key, 0.0))
        if not math.isfinite(valor_total):
            valor_total = 0.0
        nome_rec, cpf_cnpj_rec = "Não informado", "Não informado"
        qtd_transacoes = 0
        data_inicial_str = data_final_str = "Não informado"
    else:
        valor_total = float(
            pd.to_numeric(rows["VALOR_RETIRADA"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
            .sum()
        )
        qtd_transacoes = len(rows)
        if "RECEIVER" in rows.columns:
            first_rec = rows["RECEIVER"].iloc[0]
            nome_rec, cpf_cnpj_rec = _parse_receiver_nome_cnpj(first_rec)
        else:
            nome_rec, cpf_cnpj_rec = "Não informado", "Não informado"
        date_col = "DATA_SOLICITACAO" if "DATA_SOLICITACAO" in rows.columns else None
        if date_col and rows[date_col].notna().any():
            dt = pd.to_datetime(rows[date_col].dropna())
            data_inicial_str = dt.min().strftime("%d/%m/%Y") if len(dt) else "Não informado"
            data_final_str = dt.max().strftime("%d/%m/%Y") if len(dt) else "Não informado"
        else:
            data_inicial_str = data_final_str = "Não informado"

    cat = node_categories.get(node_key, "normal")
    if not rows.empty and "SUSPEITA" in rows.columns:
        stv = pd.to_numeric(rows["SUSPEITA"], errors="coerce").fillna(0)
        flag_suspeita = "Sim" if (stv == 1).any() else "Não"
    else:
        flag_suspeita = "Sim" if cat == "alert" else ("Intermediária" if cat == "bridge" else "Não")
    score_risco = "—"
    if not rows.empty and "RISK_SCORE" in filtered.columns:
        s = pd.to_numeric(rows["RISK_SCORE"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if len(s):
            mx = float(s.max())
            score_risco = f"{mx:.2f}" if math.isfinite(mx) else "—"
    qtd_remetentes = len(account_senders.get(node_key, set()))

    remetente_nome_col = "NOME_ORGANIZACAO" if "NOME_ORGANIZACAO" in filtered.columns else "NOME_PLACE"
    id_rem_col = "ID_ORGANIZACAO" if "ID_ORGANIZACAO" in filtered.columns else "ID_PLACE"
    remetentes = []
    principal_remetente_nome = "Não informado"
    principal_remetente_pct = 0.0
    if remetente_nome_col in filtered.columns and id_rem_col in filtered.columns and not rows.empty:
        _agg_kw: dict = {
            "nome_remetente": (remetente_nome_col, "first"),
            "valor_total_enviado": ("VALOR_RETIRADA", "sum"),
            "quantidade_transacoes": ("VALOR_RETIRADA", "count"),
        }
        if DATA_CRIACAO_COL in rows.columns:
            _agg_kw[DATA_CRIACAO_COL] = (DATA_CRIACAO_COL, "first")
        rem_agg = (
            rows.groupby(id_rem_col, as_index=False)
            .agg(**_agg_kw)
            .sort_values("valor_total_enviado", ascending=False)
            .head(20)
        )
        rem_agg["valor_total_enviado"] = (
            pd.to_numeric(rem_agg["valor_total_enviado"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )
        rem_agg["ticket_medio"] = rem_agg["valor_total_enviado"] / rem_agg["quantidade_transacoes"].replace(0, 1)
        rem_agg["ticket_medio"] = rem_agg["ticket_medio"].where(np.isfinite(rem_agg["ticket_medio"]), 0.0)
        if "CNPJ" in filtered.columns:
            cnpj_map = rows.groupby(id_rem_col)["CNPJ"].first().to_dict()
            rem_agg["cpf_cnpj_remetente"] = rem_agg[id_rem_col].map(lambda x: _safe_str(cnpj_map.get(x)))
        else:
            rem_agg["cpf_cnpj_remetente"] = "Não informado"
        if DATA_CRIACAO_COL in rem_agg.columns:
            rem_agg[DATA_CRIACAO_COL] = format_data_criacao_series(rem_agg[DATA_CRIACAO_COL])
        else:
            rem_agg[DATA_CRIACAO_COL] = "—"
        rem_agg = reorder_data_criacao_first(rem_agg)
        rem_cols = [c for c in [DATA_CRIACAO_COL, "nome_remetente", "cpf_cnpj_remetente", "valor_total_enviado", "quantidade_transacoes", "ticket_medio"] if c in rem_agg.columns]
        remetentes = rem_agg[rem_cols].to_dict("records")
        if remetentes and valor_total > 0 and math.isfinite(valor_total):
            principal_remetente_nome = remetentes[0].get("nome_remetente", "Não informado") or "Não informado"
            v0 = float(remetentes[0].get("valor_total_enviado", 0.0))
            principal_remetente_pct = (100.0 * v0 / valor_total) if math.isfinite(v0) else 0.0

    tipo_col = "RECEIVER_TIPO" if "RECEIVER_TIPO" in filtered.columns else None
    status_col = "STATUS" if "STATUS" in filtered.columns else None
    transacoes = []
    if not rows.empty:
        for _, r in rows.sort_values("VALOR_RETIRADA", ascending=False).iterrows():
            dc_tx = format_row_data_criacao_display(r)
            data_hora = "Não informado"
            if "DATA_SOLICITACAO" in rows.columns and pd.notna(r.get("DATA_SOLICITACAO")):
                try:
                    data_hora = pd.to_datetime(r["DATA_SOLICITACAO"]).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    data_hora = _safe_str(r.get("DATA_SOLICITACAO"))
            nome_rem = _safe_str(r.get(remetente_nome_col)) if remetente_nome_col in rows.columns else "Não informado"
            cnpj_rem = _safe_str(r.get("CNPJ")) if "CNPJ" in rows.columns else "Não informado"
            def _i01(col: str) -> int:
                if col not in rows.columns:
                    return 0
                v = r.get(col)
                try:
                    return int(float(v)) if pd.notna(v) else 0
                except (TypeError, ValueError):
                    return 0

            def _hora_tx() -> int | None:
                if "HORA_TRANSACAO" not in rows.columns:
                    return None
                v = r.get("HORA_TRANSACAO")
                try:
                    return int(float(v)) if pd.notna(v) else None
                except (TypeError, ValueError):
                    return None

            rs_tx = r.get("RISK_SCORE")
            try:
                risk_sc = float(rs_tx) if pd.notna(rs_tx) else None
            except (TypeError, ValueError):
                risk_sc = None
            transacoes.append(
                {
                    DATA_CRIACAO_COL: dc_tx,
                    "data_hora": data_hora,
                    "valor": float(r["VALOR_RETIRADA"]) if pd.notna(r["VALOR_RETIRADA"]) else 0.0,
                    "nome_remetente": nome_rem,
                    "cpf_cnpj_remetente": cnpj_rem,
                    "tipo_transacao": _safe_str(r.get(tipo_col)) if tipo_col else "Não informado",
                    "status": _safe_str(r.get(status_col)) if status_col else "Não informado",
                    "HORA_TRANSACAO": _hora_tx(),
                    "VALOR_ALTO": _i01("VALOR_ALTO"),
                    "SAQUE_NOTURNO": _i01("SAQUE_NOTURNO"),
                    "DESTINATARIO_REPETIDO": _i01("DESTINATARIO_REPETIDO"),
                    "RISK_SCORE": risk_sc,
                    "SUSPEITA": _i01("SUSPEITA"),
                }
            )

    if not math.isfinite(valor_total):
        valor_total = 0.0
    if not math.isfinite(principal_remetente_pct):
        principal_remetente_pct = 0.0

    return {
        "conta": conta,
        "nome_recebedor": nome_rec or "Não informado",
        "cpf_cnpj_recebedor": cpf_cnpj_rec or "Não informado",
        "valor_total_recebido": valor_total,
        "flag_suspeita": flag_suspeita,
        "score_risco": score_risco,
        "quantidade_remetentes": qtd_remetentes,
        "quantidade_transacoes": qtd_transacoes,
        "data_inicial": data_inicial_str,
        "data_final": data_final_str,
        "principal_remetente_nome": principal_remetente_nome,
        "principal_remetente_pct": principal_remetente_pct,
        "remetentes": remetentes,
        "transacoes": transacoes,
    }


def _historia_conta_text(df_acc: pd.DataFrame) -> str:
    """Gera texto automático da coluna 'História da conta' para um DataFrame já filtrado por conta."""
    if df_acc.empty:
        return "Sem dados."
    n = len(df_acc)
    if "VALOR_RETIRADA" in df_acc.columns:
        valor_total = float(
            pd.to_numeric(df_acc["VALOR_RETIRADA"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
            .sum()
        )
    else:
        valor_total = 0.0
    qtd_remetentes = int(df_acc["RECEIVER"].nunique()) if "RECEIVER" in df_acc.columns else 0
    if "RISK_SCORE" in df_acc.columns:
        rs = pd.to_numeric(df_acc["RISK_SCORE"], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        score_medio = float(rs.mean()) if len(rs) else 0.0
    else:
        score_medio = 0.0
    if not math.isfinite(score_medio):
        score_medio = 0.0
    suspeita = int(df_acc["SUSPEITA"].max()) if "SUSPEITA" in df_acc.columns else 0
    partes = [
        f"Conta recebeu {format_brl(valor_total)} em {n} transação(ões), com {qtd_remetentes} remetente(s).",
        f"Score médio de risco: {format_metric_float(score_medio)}.",
    ]
    if qtd_remetentes == 1:
        partes.append("Alta concentração em único remetente (possível centralização).")
    if score_medio > 70:
        partes.append("Alto risco identificado.")
    if valor_total > 1_000_000:  # exemplo de “alto volume”
        partes.append("Alto volume financeiro movimentado.")
    if suspeita == 1:
        partes.append("Conta classificada como suspeita.")
    return " ".join(partes)


def _build_detalhe_table(df: pd.DataFrame, conta_selecionada: str | None) -> pd.DataFrame:
    """Monta a tabela de detalhamento (transações) com colunas padronizadas e História da conta."""
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    if conta_selecionada is not None:
        work = work[work["CONTA_RECEBEDOR"].astype(str) == str(conta_selecionada)]
    if work.empty:
        return pd.DataFrame()

    fill_data_criacao_place_e_org(work)

    remetente_nome = "NOME_ORGANIZACAO" if "NOME_ORGANIZACAO" in work.columns else "NOME_PLACE"
    nome_rec_map = {}
    cnpj_rec_map = {}
    for conta in work["CONTA_RECEBEDOR"].unique():
        sub = work[work["CONTA_RECEBEDOR"] == conta]
        if "RECEIVER" in sub.columns and not sub["RECEIVER"].empty:
            nome_r, cnpj_r = _parse_receiver_nome_cnpj(sub["RECEIVER"].iloc[0])
            nome_rec_map[conta] = nome_r
            cnpj_rec_map[conta] = cnpj_r
        else:
            nome_rec_map[conta] = "Não informado"
            cnpj_rec_map[conta] = "Não informado"

    work["Conta recebedora"] = work["CONTA_RECEBEDOR"].astype(str)
    work["Nome recebedor"] = work["CONTA_RECEBEDOR"].map(lambda c: nome_rec_map.get(c, "Não informado"))
    work["CPF/CNPJ recebedor"] = work["CONTA_RECEBEDOR"].map(lambda c: cnpj_rec_map.get(c, "Não informado"))
    work["Valor recebido"] = work["VALOR_RETIRADA"]
    if "RECEIVER" in work.columns:
        work["Nome remetente"] = work["RECEIVER"].fillna("Não informado").astype(str).str.strip().replace("", "Não informado")
    else:
        work["Nome remetente"] = work[remetente_nome].fillna("Não informado").astype(str) if remetente_nome in work.columns else "Não informado"
    work["CPF/CNPJ remetente"] = work["CNPJ"].fillna("Não informado").astype(str) if "CNPJ" in work.columns else "Não informado"

    if "DATA_SOLICITACAO" in work.columns:
        work["Data/Hora"] = pd.to_datetime(work["DATA_SOLICITACAO"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
    elif "DATA_PAGAMENTO" in work.columns:
        work["Data/Hora"] = pd.to_datetime(work["DATA_PAGAMENTO"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
    else:
        work["Data/Hora"] = "—"
    work["Data/Hora"] = work["Data/Hora"].fillna("—")
    work["Score de risco"] = work["RISK_SCORE"].round(2) if "RISK_SCORE" in work.columns else 0
    work["Suspeita"] = work["SUSPEITA"].fillna(0).astype(int) if "SUSPEITA" in work.columns else 0
    work["Hora"] = (
        pd.to_numeric(work["HORA_TRANSACAO"], errors="coerce").fillna(0).astype(int)
        if "HORA_TRANSACAO" in work.columns
        else 0
    )
    work["Valor alto"] = work["VALOR_ALTO"].fillna(0).astype(int) if "VALOR_ALTO" in work.columns else 0
    work["Saque noturno"] = work["SAQUE_NOTURNO"].fillna(0).astype(int) if "SAQUE_NOTURNO" in work.columns else 0
    work["Dest. repetido"] = (
        work["DESTINATARIO_REPETIDO"].fillna(0).astype(int) if "DESTINATARIO_REPETIDO" in work.columns else 0
    )

    work[DATA_CRIACAO_COL] = format_data_criacao_series(work[DATA_CRIACAO_COL])

    historia_por_conta = work.groupby("CONTA_RECEBEDOR").apply(_historia_conta_text)
    work["História da conta"] = work["CONTA_RECEBEDOR"].map(historia_por_conta)

    cols = [
        DATA_CRIACAO_COL,
        "Conta recebedora",
        "Nome recebedor",
        "CPF/CNPJ recebedor",
        "Valor recebido",
        "Nome remetente",
        "CPF/CNPJ remetente",
        "Data/Hora",
        "Hora",
        "Valor alto",
        "Saque noturno",
        "Dest. repetido",
        "Score de risco",
        "Suspeita",
        "História da conta",
    ]
    out = work[cols].copy()
    out = out.sort_values("Valor recebido", ascending=False)
    return out


def _html_escape(s: str) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    s = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return s


def _risk_class_historia(suspeita: int, score: float) -> str:
    """Retorna classe CSS para colorir o link 'Ver análise': vermelho (suspeita), amarelo (médio), verde (baixo)."""
    if suspeita == 1:
        return "rt-historia-alerta"
    if score > 70:
        return "rt-historia-medio"
    return "rt-historia-ok"


def _td_class_for_detail_col(col_name: str) -> str:
    """Classes de célula: alinhadas ao padrão visual dos cards (valor e score em destaque roxo)."""
    if col_name == "Valor recebido":
        return "rt-td rt-td-valor"
    if col_name == "Score de risco":
        return "rt-td rt-td-score"
    return "rt-td"


def _render_tabela_detalhe_com_popover(tabela_df: pd.DataFrame) -> str:
    """Gera HTML da tabela de detalhamento com coluna 'Detalhes' e popover para História da conta."""
    df = tabela_df.copy()
    if "Valor recebido" in df.columns:
        df["Valor recebido"] = df["Valor recebido"].apply(lambda x: format_brl(x) if pd.notna(x) and not isinstance(x, str) else (x if pd.notna(x) else "—"))
    cols_display = [
        DATA_CRIACAO_COL,
        "Conta recebedora",
        "Nome recebedor",
        "CPF/CNPJ recebedor",
        "Valor recebido",
        "Nome remetente",
        "CPF/CNPJ remetente",
        "Data/Hora",
        "Hora",
        "Valor alto",
        "Saque noturno",
        "Dest. repetido",
        "Score de risco",
        "Suspeita",
    ]
    if "História da conta" not in df.columns:
        cols_display = [c for c in cols_display if c in df.columns]
        has_historia = False
    else:
        has_historia = True
    headers = cols_display + (["Detalhes"] if has_historia else [])
    html_rows = []
    for _, row in df.iterrows():
        cells = []
        for c in cols_display:
            if c not in df.columns:
                continue
            val = row.get(c, "")
            if pd.isna(val):
                val = "—"
            td_cls = _td_class_for_detail_col(c)
            cells.append(f'<td class="{td_cls}">{_html_escape(val)}</td>')
        if has_historia:
            historia = row.get("História da conta", "")
            suspeita = int(row.get("Suspeita", 0)) if pd.notna(row.get("Suspeita")) else 0
            score = float(row.get("Score de risco", 0)) if pd.notna(row.get("Score de risco")) else 0
            cls = _risk_class_historia(suspeita, score)
            historia_esc = _html_escape(historia).replace("\n", "&#10;")
            cells.append(
                f'<td class="rt-td rt-td-detalhes">'
                f'<span class="rt-ver-analise {cls}" data-historia="{historia_esc}" role="button" tabindex="0">Ver análise</span>'
                f'</td>'
            )
        html_rows.append("<tr>" + "".join(cells) + "</tr>")
    thead = "<thead><tr>" + "".join(f'<th class="rt-th">{_html_escape(h)}</th>' for h in headers) + "</tr></thead>"
    tbody = "<tbody>" + "".join(html_rows) + "</tbody>"
    table_html = f'<table class="rt-tabela-detalhe">{thead}{tbody}</table>'
    css = """
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    .rt-tabela-detalhe-wrap {
      overflow: auto;
      max-height: 560px;
      border: 1px solid #E5E7EB;
      border-radius: 12px;
      background: #ffffff;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.06);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
    .rt-tabela-detalhe {
      width: 100%;
      border-collapse: collapse;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 13px;
      line-height: 1.5;
      color: #374151;
    }
    .rt-tabela-detalhe thead th.rt-th {
      background: #ffffff;
      color: #1F4ED8;
      font-weight: 500;
      font-size: 14px;
      padding: 10px 12px;
      text-align: left;
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid #E5E7EB;
      box-shadow: 0 1px 0 #E5E7EB;
      letter-spacing: 0.01em;
    }
    .rt-tabela-detalhe tbody td.rt-td {
      padding: 10px 12px;
      border-bottom: 1px solid #E5E7EB;
      color: #374151;
      font-size: 13px;
      line-height: 1.5;
      vertical-align: middle;
      font-weight: 400;
    }
    .rt-tabela-detalhe tbody td.rt-td-valor,
    .rt-tabela-detalhe tbody td.rt-td-score {
      color: #6D28D9;
      font-weight: 600;
      font-size: 13px;
    }
    .rt-tabela-detalhe tbody tr:hover td.rt-td {
      background: rgba(31, 78, 216, 0.05);
    }
    .rt-tabela-detalhe tbody td.rt-td-detalhes {
      width: 96px;
      max-width: 96px;
      white-space: nowrap;
    }
    .rt-ver-analise { cursor: pointer; text-decoration: underline; font-weight: 500; font-size: 12px; }
    .rt-ver-analise.rt-historia-alerta { color: #DC2626; }
    .rt-ver-analise.rt-historia-medio { color: #D97706; }
    .rt-ver-analise.rt-historia-ok { color: #059669; }
    .rt-popover {
      display: none;
      position: fixed;
      max-width: 320px;
      max-height: 260px;
      overflow: auto;
      background: #fff;
      box-shadow: 0 8px 24px rgba(16, 24, 40, 0.12);
      border-radius: 12px;
      padding: 12px 14px;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      font-size: 13px;
      line-height: 1.5;
      color: #374151;
      z-index: 9999;
      border: 1px solid #E5E7EB;
      white-space: pre-wrap;
      word-wrap: break-word;
    }
    .rt-popover.rt-show { display: block; }
    """
    js = """
    (function(){
      function showPopover(el, text) {
        text = (text || '').replace(/&#10;/g, '\\n').replace(/\\. /g, '.\\n');
        var pop = document.getElementById('rt-popover-global');
        if (!pop) { pop = document.createElement('div'); pop.id = 'rt-popover-global'; pop.className = 'rt-popover'; document.body.appendChild(pop); }
        pop.textContent = text;
        pop.classList.add('rt-show');
        var rect = el.getBoundingClientRect();
        var doc = document.documentElement;
        pop.style.left = Math.min(rect.left, doc.clientWidth - 320) + 'px';
        pop.style.top = (rect.bottom + 4) + 'px';
      }
      function hidePopover() {
        var pop = document.getElementById('rt-popover-global');
        if (pop) pop.classList.remove('rt-show');
      }
      document.addEventListener('click', function(e) {
        if (e.target && e.target.classList && e.target.classList.contains('rt-ver-analise')) {
          e.preventDefault();
          var pop = document.getElementById('rt-popover-global');
          if (pop && pop.classList.contains('rt-show')) hidePopover();
          else showPopover(e.target, e.target.getAttribute('data-historia'));
        } else if (e.target && e.target.id !== 'rt-popover-global' && !e.target.closest('.rt-popover')) {
          hidePopover();
        }
      });
    })();
    """
    return f"<style>{css}</style><div class='rt-tabela-detalhe-wrap'>{table_html}</div><script>{js}</script>"


def _render_account_sidebar(detail: dict) -> None:
    """Renderiza o painel lateral com detalhes da conta selecionada (visão investigação)."""
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Detalhes da conta (clique no grafo)")

    st.sidebar.markdown("**DADOS DA CONTA**")
    st.sidebar.text(f"Conta: {detail['conta']}")
    st.sidebar.text(f"Nome recebedor: {detail['nome_recebedor']}")
    st.sidebar.text(f"CPF/CNPJ recebedor: {detail['cpf_cnpj_recebedor']}")
    st.sidebar.text(f"Valor total recebido: {format_brl(detail['valor_total_recebido'])}")

    st.sidebar.markdown("**DADOS DE RISCO**")
    st.sidebar.text(f"Flag suspeita: {detail['flag_suspeita']}")
    st.sidebar.text(f"Score risco: {detail['score_risco']}")
    st.sidebar.text(f"Quantidade de remetentes: {detail['quantidade_remetentes']}")

    st.sidebar.markdown("**HISTÓRIA DA CONTA**")
    _pp_hist = format_metric_float(detail["principal_remetente_pct"])
    _pp_hist_disp = f"{_pp_hist}%" if _pp_hist != "—" else "—"
    historia = (
        f"Valor total recebido: {format_brl(detail['valor_total_recebido'])}. "
        f"Quantidade de transações: {detail['quantidade_transacoes']}. "
        f"Quantidade de remetentes: {detail['quantidade_remetentes']}. "
        f"Principal remetente: {detail['principal_remetente_nome']} "
        f"({_pp_hist_disp} de participação). "
        f"Período das transações: de {detail['data_inicial']} a {detail['data_final']}."
    )
    st.sidebar.caption(historia)

    st.sidebar.markdown("**DADOS DOS REMETENTES**")
    if detail["remetentes"]:
        rem_df = pd.DataFrame(detail["remetentes"])
        rem_df = add_placeholder_data_criacao_column(reorder_data_criacao_first(rem_df))
        rem_df["valor_total_enviado"] = rem_df["valor_total_enviado"].apply(lambda x: format_brl(x) if pd.notna(x) else "Não informado")
        rem_df["ticket_medio"] = rem_df["ticket_medio"].apply(lambda x: format_brl(x) if pd.notna(x) else "Não informado")
        st.sidebar.dataframe(
            rem_df,
            hide_index=True,
            height=min(320, 80 + len(rem_df) * 36),
            column_config={
                DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium"),
                "valor_total_enviado": "valor total enviado",
                "quantidade_transacoes": "qtd transações",
                "ticket_medio": "ticket médio",
            },
        )
    else:
        st.sidebar.caption("Nenhum remetente encontrado.")

    st.sidebar.markdown("**DETALHE DAS TRANSAÇÕES**")
    if detail["transacoes"]:
        tx_df = add_placeholder_data_criacao_column(reorder_data_criacao_first(pd.DataFrame(detail["transacoes"])))
        tx_df["valor"] = tx_df["valor"].apply(lambda x: format_brl(x) if pd.notna(x) else "Não informado")
        st.sidebar.dataframe(
            tx_df,
            hide_index=True,
            height=min(400, 80 + len(tx_df) * 36),
            column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
        )
    else:
        st.sidebar.caption("Nenhuma transação encontrada.")

    if st.sidebar.button("Limpar seleção", key="rt_clear_selection"):
        if "rt_selected_node" in st.session_state:
            del st.session_state["rt_selected_node"]
        st.rerun()


def _render_detalhamento_page(detail: dict | None, filtered: pd.DataFrame, graph: nx.DiGraph, node_categories: dict) -> None:
    """Renderiza a aba 'Detalhamento Conta Suspeita' no conteúdo principal."""
    st.markdown('<p class="rt-section-title">Detalhamento Conta Suspeita</p>', unsafe_allow_html=True)
    if detail is None:
        st.info("Selecione uma conta no grafo (clique em um nó na visualização Pyvis ou Plotly) para ver os detalhes.")
        if st.button("Voltar para o grafo", key="rt_voltar_grafo"):
            if "aba_ativa" in st.session_state:
                del st.session_state["aba_ativa"]
            if "conta_selecionada" in st.session_state:
                del st.session_state["conta_selecionada"]
            st.query_params.clear()
            st.rerun()
        return

    st.markdown("**RESUMO**")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Conta", detail["conta"])
        st.metric("Nome", detail["nome_recebedor"])
        st.metric("CPF/CNPJ", detail["cpf_cnpj_recebedor"])
    with c2:
        st.metric("Valor total recebido", format_brl(detail["valor_total_recebido"]))
        st.metric("Quantidade de transações", detail["quantidade_transacoes"])
        st.metric("Quantidade de remetentes", detail["quantidade_remetentes"])
    with c3:
        pass

    st.markdown("**HISTÓRIA DA CONTA**")
    _pp_hist2 = format_metric_float(detail["principal_remetente_pct"])
    _pp_hist2_disp = f"{_pp_hist2}%" if _pp_hist2 != "—" else "—"
    historia = (
        f"Valor total recebido: {format_brl(detail['valor_total_recebido'])}. "
        f"Quantidade de transações: {detail['quantidade_transacoes']}. "
        f"Quantidade de remetentes: {detail['quantidade_remetentes']}. "
        f"Principal remetente: {detail['principal_remetente_nome']} "
        f"({_pp_hist2_disp} de participação). "
        f"Período: de {detail['data_inicial']} a {detail['data_final']}."
    )
    st.write(historia)

    st.markdown("**DADOS DE RISCO**")
    st.write(f"**Flag suspeita:** {detail['flag_suspeita']}  |  **Score risco:** {detail['score_risco']}")

    st.markdown("**REMETENTES**")
    if detail["remetentes"]:
        rem_df = add_placeholder_data_criacao_column(reorder_data_criacao_first(pd.DataFrame(detail["remetentes"])))
        rem_df["valor_total_enviado"] = rem_df["valor_total_enviado"].apply(lambda x: format_brl(x) if pd.notna(x) else "Não informado")
        rem_df["ticket_medio"] = rem_df["ticket_medio"].apply(lambda x: format_brl(x) if pd.notna(x) else "Não informado")
        st.dataframe(
            rem_df,
            hide_index=True,
            height=min(320, 80 + len(rem_df) * 36),
            column_config={
                DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium"),
                "valor_total_enviado": "valor total",
                "quantidade_transacoes": "qtd trans.",
                "ticket_medio": "ticket médio",
            },
        )
    else:
        st.caption("Nenhum remetente encontrado.")

    st.markdown("**TRANSAÇÕES**")
    if detail["transacoes"]:
        tx_df = add_placeholder_data_criacao_column(reorder_data_criacao_first(pd.DataFrame(detail["transacoes"])))
        tx_df["valor"] = tx_df["valor"].apply(lambda x: format_brl(x) if pd.notna(x) else "Não informado")
        st.dataframe(
            tx_df,
            hide_index=True,
            height=min(400, 80 + len(tx_df) * 36),
            column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
        )
    else:
        st.caption("Nenhuma transação encontrada.")

    if st.button("Voltar para o grafo", key="rt_voltar_grafo"):
        if "aba_ativa" in st.session_state:
            del st.session_state["aba_ativa"]
        if "conta_selecionada" in st.session_state:
            del st.session_state["conta_selecionada"]
        st.query_params.clear()
        st.rerun()


st.set_page_config(page_title="Rede de Transações | ZIG Risk Monitor", page_icon=":spider_web:", layout="wide")
if not ensure_authenticated():
    st.stop()

apply_enterprise_theme()
aplicar_estilo_global()
st.markdown(
    """
    <style>
    .rt-filter-bar {
        background: #ffffff;
        border: 1px solid #dbe3f0;
        border-radius: 12px;
        padding: 16px 20px;
        margin-bottom: 16px;
        box-shadow: 0 2px 4px rgba(16, 24, 40, 0.06);
        max-width: 100%;
    }
    @media (max-width: 640px) {
        .rt-filter-bar { padding: 12px 14px; }
    }
    .rt-date-filters { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
    .rt-metric-card { border-radius: 8px; padding: 12px 14px; margin-bottom: 8px; }
    .rt-metric-alto { border-left: 4px solid #EF4444; background: rgba(239,68,68,0.06); }
    .rt-metric-medio { border-left: 4px solid #F59E0B; background: rgba(245,158,11,0.08); }
    .rt-metric-baixo { border-left: 4px solid #22C55E; background: rgba(34,197,94,0.06); }
    .rt-section-title {
        font-size: 1.15rem;
        font-weight: 700;
        color: #0047BB;
        margin: 20px 0 12px 0;
        padding-bottom: 6px;
        border-bottom: 2px solid #E3E8EE;
    }
    [data-testid="stDataFrame"] { border-radius: 8px; border: 1px solid #E3E8EE; }
    [data-testid="stDataFrame"] tbody tr:hover { background: rgba(0, 71, 187, 0.06); }
    .rt-periodo-invalido {
        background: #FEF3C7;
        border: 1px solid #F59E0B;
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 16px;
        color: #92400E;
        font-weight: 500;
    }
    /* Parecer analítico fixo (Investigação 360) — mesmo conteúdo do antigo popover */
    .rt-parecer-fixo-titulo {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        font-size: 15px;
        font-weight: 600;
        color: #1F4ED8;
        margin: 0 0 4px 0;
        padding: 0;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:has(.rt-parecer-fixo-titulo) p,
    [data-testid="stVerticalBlockBorderWrapper"]:has(.rt-parecer-fixo-titulo) li {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        font-size: 13px;
        line-height: 1.55;
        color: #374151;
    }
    [data-testid="stVerticalBlockBorderWrapper"]:has(.rt-parecer-fixo-titulo) strong {
        color: #6D28D9;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
# Prioriza base processada (já com features/score) — evita recalcular 100k+ linhas a cada abertura da aba.
df = load_processed_data()
if df.empty:
    raw = load_raw_data()
    if raw.empty:
        st.info("Sem dados para montar o grafo. Envie uma planilha em `Admin Upload` ou configure o Metabase.")
        st.stop()
    df = calcular_score(add_features(raw))
elif "RISK_SCORE" not in df.columns or "SUSPEITA" not in df.columns:
    df = calcular_score(add_features(df))

# --- Date range e valores padrão a partir da base (alinhado a date_column_for_period_filter) ---
date_col = date_column_for_period_filter(df)
source_col = date_col
if source_col not in df.columns or df[source_col].isna().all():
    data_min, data_max = date.today() - timedelta(days=30), date.today()
else:
    col_dt = pd.to_datetime(df[source_col].dropna(), errors="coerce")
    col_dt = col_dt[col_dt.notna()]
    if col_dt.empty:
        data_min, data_max = date.today() - timedelta(days=30), date.today()
    else:
        data_min = col_dt.min().date() if hasattr(col_dt.min(), "date") else col_dt.min()
        data_max = col_dt.max().date() if hasattr(col_dt.max(), "date") else col_dt.max()

sync_global_date_inputs(data_min, data_max)

# ========== 1) TÍTULO + FILTROS DE DATA (mesma linha, título à esquerda, filtros à direita) ==========
col_title, col_dates = st.columns([2, 1])
with col_title:
    st.title("Rede de Transações")
with col_dates:
    d1, d2 = st.columns(2, gap="medium")
    with d1:
        data_inicial = st.date_input(
            "Data inicial",
            min_value=CALENDAR_MIN,
            max_value=CALENDAR_MAX,
            format="DD/MM/YYYY",
            key="data_inicial",
        )
    with d2:
        data_final = st.date_input(
            "Data final",
            min_value=CALENDAR_MIN,
            max_value=CALENDAR_MAX,
            format="DD/MM/YYYY",
            key="data_final",
        )
data_inicial = max(CALENDAR_MIN, min(data_inicial, CALENDAR_MAX))
data_final = max(CALENDAR_MIN, min(data_final, CALENDAR_MAX))

erro_periodo = None
if data_inicial > data_final:
    erro_periodo = "Data inicial não pode ser maior que a data final."

# Aplicar filtro por período (só para checar Regra 3 se ainda não há erro)
filtered = filter_by_brazil_calendar_dates(df, date_col, data_inicial, data_final)

if erro_periodo is None and filtered.empty:
    erro_periodo = "Nenhum dado encontrado para o período selecionado."

if erro_periodo:
    st.markdown(
        f'<div class="rt-periodo-invalido">⚠️ Período inválido. Verifique as datas selecionadas ou escolha um intervalo válido dentro da base.</div>',
        unsafe_allow_html=True,
    )
    st.caption(erro_periodo)
    st.stop()

graph = build_fraud_graph(filtered)
metrics = graph_metrics(graph, filtered)
node_categories = _classify_nodes(graph)

# Conta selecionada (sessão / URL) — disponível antes dos cards para o resumo analítico ao lado do score
_rt_qp = st.query_params
if _rt_qp.get("conta"):
    st.session_state["rt_selected_node"] = f"CONTA::{_rt_qp.get('conta')}"
selected_node = st.session_state.get("rt_selected_node")
conta_para_tabela = None
if selected_node and str(selected_node).startswith("CONTA::"):
    conta_para_tabela = str(selected_node).replace("CONTA::", "").strip()

# ========== 2) NETWORK OVERVIEW CARDS ==========
st.markdown('<p class="rt-section-title">Visão geral da rede</p>', unsafe_allow_html=True)
m = _overview_metrics(filtered)

# Linha 1: risco imediato
st.markdown("**Risco imediato**")
row1_1, row1_2, row1_3 = st.columns(3, gap="medium")
with row1_1:
    _p = format_metric_float(m["pct_transacoes_suspeitas"])
    st.metric("% transações suspeitas", f"{_p}%" if _p != "—" else "—")
with row1_2:
    st.metric("Valor total suspeito", format_brl(m["volume_suspeito"]))
with row1_3:
    st.metric("Score médio de risco", format_metric_float(m["score_medio"]))

# Linha 2: contexto + rede
st.markdown("**Contexto e rede**")
row2_1, row2_2, row2_3, row2_4 = st.columns(4, gap="medium")
with row2_1:
    st.metric("Total de transações", f"{m['total_transacoes']:,}".replace(",", "."))
with row2_2:
    st.metric(
        "Volume financeiro total",
        format_brl(m["volume_total"]),
        f"Suspeitos: {format_brl(m['volume_suspeito'])}",
    )
with row2_3:
    _pc = format_metric_float(m["pct_contas_suspeitas"])
    _pct_s = f"{_pc}%" if _pc != "—" else "—"
    st.metric(
        "Contas alerta (suspeitas)",
        f"{m['contas_suspeitas']:,} ({_pct_s})".replace(",", "."),
    )
with row2_4:
    st.metric("Conta mais conectada (hub)", f"{m['hub_conexoes']:,}".replace(",", "."))

# ========== 3) NETWORK GRAPH ==========
st.markdown('<p class="rt-section-title">Grafo da rede</p>', unsafe_allow_html=True)
cluster_id_to_nodes = metrics.get("cluster_id_to_nodes") or {}
cluster_filter_options = ["Todos"]
if not metrics["clusters_detalhes"].empty:
    for _, r in metrics["clusters_detalhes"].iterrows():
        cid = r.get("cluster_id")
        rl = r.get("risk_level", "")
        cluster_filter_options.append(f"Cluster {int(cid)} ({rl})")
cluster_sel = st.selectbox(
    "Filtrar grafo por cluster",
    range(len(cluster_filter_options)),
    format_func=lambda i: cluster_filter_options[i],
    key="rt_cluster_filter",
)
if cluster_sel and cluster_sel > 0 and cluster_id_to_nodes:
    keys_ordered = sorted(cluster_id_to_nodes.keys())
    if cluster_sel <= len(keys_ordered):
        cid_key = keys_ordered[cluster_sel - 1]
        subset = set(cluster_id_to_nodes[cid_key])
        focus_graph = graph.subgraph(subset)
    else:
        focus_graph = _top_weight_subgraph(graph, max_edges=180)
else:
    focus_graph = _top_weight_subgraph(graph, max_edges=180)
focus_node_categories = _classify_nodes(focus_graph)

# Layout: [GRAFO] | [TABELA DE DETALHAMENTO] (conta_para_tabela e selected_node já resolvidos acima)
col_grafo, col_tabela = st.columns([1, 1], gap="large")

with col_grafo:
    tab_plotly, tab_pyvis = st.tabs(["Plotly", "Pyvis"])
    with tab_plotly:
        st.caption("Clique em um nó (conta recebedora) para atualizar a tabela ao lado.")
        try:
            fig = _plot_graph(focus_graph, selected_node=selected_node)
            _plotly_cfg = {"responsive": False, "displaylogo": False}
            try:
                event = st.plotly_chart(
                    fig,
                    key="rt_network_plot",
                    on_select="rerun",
                    selection_mode="points",
                    use_container_width=False,
                    config=_plotly_cfg,
                )
            except TypeError:
                event = st.plotly_chart(
                    fig,
                    key="rt_network_plot",
                    on_select="rerun",
                    selection_mode="points",
                    use_container_width=False,
                )
            selection = getattr(event, "selection", None) or (event.get("selection") if isinstance(event, dict) else None)
            if selection:
                points = getattr(selection, "points", None) or (selection.get("points") if isinstance(selection, dict) else [])
                if points:
                    pt = points[0]
                    cdata = getattr(pt, "customdata", None) or (pt.get("customdata") if isinstance(pt, dict) else None)
                    if cdata and len(cdata) > 0:
                        node_id = cdata[0] if isinstance(cdata[0], str) else str(cdata[0])
                        if node_id.startswith("CONTA::"):
                            st.session_state["rt_selected_node"] = node_id
        except Exception:
            st.error("Não foi possível renderizar a visualização Plotly da rede.")
    with tab_pyvis:
        try:
            st.components.v1.html(_build_pyvis_html(focus_graph), height=720, scrolling=False)
        except Exception:
            st.error("Não foi possível renderizar a visualização Pyvis da rede.")

with col_tabela:
    st.markdown("**Tabela de detalhamento**")
    if conta_para_tabela:
        st.caption(f"Conta selecionada: **{conta_para_tabela}** — transações e história da conta.")
        if st.button("Limpar seleção", key="rt_clear_selection"):
            if "rt_selected_node" in st.session_state:
                del st.session_state["rt_selected_node"]
            st.query_params.clear()
            st.rerun()
    else:
        st.caption("Clique em um nó no grafo para ver as transações da conta.")

    if not conta_para_tabela:
        st.info("Selecione uma conta no grafo (clique em um nó) para exibir as transações e a história da conta.")
    else:
        tabela_df = _build_detalhe_table(filtered, conta_para_tabela)
        if tabela_df.empty:
            st.warning("Nenhuma transação encontrada para esta conta no período.")
        else:
            tabela_df = tabela_df.copy()
            html_tabela = _render_tabela_detalhe_com_popover(tabela_df)
            try:
                st.components.v1.html(html_tabela, height=580, scrolling=False)
            except Exception:
                st.warning("Não foi possível renderizar a tabela interativa. Exibindo dados em formato tabular.")
                st.dataframe(
                    add_placeholder_data_criacao_column(tabela_df),
                    width="stretch",
                    hide_index=True,
                    column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
                )

# ========== 4) CRITICAL ACCOUNTS RANKING ==========
def _enrich_ranking_contas(rank_df: pd.DataFrame, filtered: pd.DataFrame) -> pd.DataFrame:
    """Enriquece ranking com Nome recebedor, CPF/CNPJ recebedor, Nome remetente, CPF/CNPJ remetente."""
    if rank_df.empty or "conta" not in rank_df.columns:
        return rank_df
    recebedor = []
    for conta in rank_df["conta"].astype(str):
        rows = filtered[filtered["CONTA_RECEBEDOR"].astype(str) == conta]
        if rows.empty or "RECEIVER" not in rows.columns:
            recebedor.append({"nome_recebedor": "Não informado", "cpf_cnpj_recebedor": "Não informado"})
        else:
            n, c = _parse_receiver_nome_cnpj(rows["RECEIVER"].iloc[0])
            recebedor.append({"nome_recebedor": n, "cpf_cnpj_recebedor": c})
    rec_df = pd.DataFrame(recebedor)
    # Nome remetente: metrics trazem NOME_PLACE do graph_engine
    nome_rem = rank_df["NOME_PLACE"].fillna("Não informado").astype(str) if "NOME_PLACE" in rank_df.columns else pd.Series(["Não informado"] * len(rank_df))
    id_place_col = "ID_PLACE"
    cnpj_rem = []
    dc_rank = []
    for _, r in rank_df.iterrows():
        conta = str(r["conta"])
        pid = r.get(id_place_col)
        if pid is None or pd.isna(pid) or "CNPJ" not in filtered.columns:
            cnpj_rem.append("Não informado")
        else:
            sub = filtered[(filtered["CONTA_RECEBEDOR"].astype(str) == conta) & (filtered[id_place_col] == pid)]
            cnpj_rem.append(str(sub["CNPJ"].iloc[0]).strip() if not sub.empty and pd.notna(sub["CNPJ"].iloc[0]) else "Não informado")
        sub_c = filtered[filtered["CONTA_RECEBEDOR"].astype(str) == conta]
        if sub_c.empty or DATA_CRIACAO_COL not in sub_c.columns or sub_c[DATA_CRIACAO_COL].isna().all():
            dc_rank.append("—")
        else:
            dc_rank.append(format_data_criacao_series(sub_c[DATA_CRIACAO_COL].dropna().head(1)).iloc[0])
    out = rank_df.copy()
    out[DATA_CRIACAO_COL] = dc_rank
    out["Conta"] = out["conta"].astype(str)
    out["Nome recebedor"] = rec_df["nome_recebedor"].values
    out["CPF/CNPJ recebedor"] = rec_df["cpf_cnpj_recebedor"].values
    out["Valor recebido"] = out["volume_recebido"]
    out["Nome remetente"] = nome_rem.values
    out["CPF/CNPJ remetente"] = cnpj_rem
    return reorder_data_criacao_first(out)


def _risk_level_is_critico(risk_level) -> bool:
    """Compatível com graph_engine: risk_level == 'crítico'."""
    if risk_level is None or (isinstance(risk_level, float) and pd.isna(risk_level)):
        return False
    s = str(risk_level).strip().lower()
    return s in ("crítico", "critico")


def _doc_recebedor_para_exibicao(cpf_cnpj_raw: str, nome_raw: str) -> tuple[str, str]:
    """Nome e coluna CPF/CNPJ; ausência gera mensagem padrão obrigatória."""
    nome = (nome_raw or "").strip() or "Não informado"
    c = (cpf_cnpj_raw or "").strip()
    if not c or c.lower() in ("não informado", "nao informado", "nan", "none", "<na>", "nat"):
        return nome, "Recebedor sem identificação (CPF/CNPJ não informado)"
    return nome, c


def _build_cluster_recebedores_table(
    cluster_id: int,
    filtered: pd.DataFrame,
    cluster_id_to_nodes: dict,
) -> pd.DataFrame:
    """
    Agrega transações do período filtrado por conta recebedora pertencente ao cluster.
    Relaciona cluster_id ↔ CONTA_RECEBEDOR via cluster_id_to_nodes (nós CONTA::).
    """
    nodes = cluster_id_to_nodes.get(cluster_id)
    if nodes is None:
        nodes = cluster_id_to_nodes.get(int(cluster_id))
    if not nodes:
        return pd.DataFrame()
    contas = sorted({str(n).replace("CONTA::", "").strip() for n in nodes if str(n).startswith("CONTA::")})
    if not contas:
        return pd.DataFrame()
    sub = filtered[filtered["CONTA_RECEBEDOR"].astype(str).isin(contas)].copy()
    cols_out = [
        DATA_CRIACAO_COL,
        "Nome recebedor",
        "CPF/CNPJ recebedor",
        "Conta recebedora",
        "Banco recebedor",
        "Valor total recebido",
        "Nº transações",
    ]
    if sub.empty:
        return pd.DataFrame(columns=cols_out)
    agg = sub.groupby("CONTA_RECEBEDOR", as_index=False).agg(
        valor_total=("VALOR_RETIRADA", "sum"),
        n_tx=("VALOR_RETIRADA", "count"),
    )
    rows_out = []
    for _, r in agg.iterrows():
        conta = str(r["CONTA_RECEBEDOR"])
        rows_c = sub[sub["CONTA_RECEBEDOR"].astype(str) == conta]
        if "RECEIVER" in rows_c.columns and rows_c["RECEIVER"].notna().any():
            ix = rows_c["RECEIVER"].first_valid_index()
            nr, dr = _parse_receiver_nome_cnpj(rows_c.loc[ix, "RECEIVER"])
        else:
            nr, dr = "Não informado", "Não informado"
        nome_ex, doc_ex = _doc_recebedor_para_exibicao(dr, nr)
        if "BANCO_RECEBEDOR" in rows_c.columns:
            bc = rows_c["BANCO_RECEBEDOR"].dropna().astype(str).str.strip()
            bc = bc[(bc.ne("")) & (~bc.str.lower().isin(["nan", "none", "<na>"]))]
            banco = str(bc.iloc[0]) if len(bc) else "Não informado"
        else:
            banco = "Não informado"
        dc_cl = "—"
        if DATA_CRIACAO_COL in rows_c.columns:
            _dc_s = rows_c[DATA_CRIACAO_COL].dropna()
            if len(_dc_s):
                dc_cl = format_data_criacao_series(_dc_s.head(1)).iloc[0]
        rows_out.append(
            {
                DATA_CRIACAO_COL: dc_cl,
                "Nome recebedor": nome_ex,
                "CPF/CNPJ recebedor": doc_ex,
                "Conta recebedora": conta,
                "Banco recebedor": banco if banco else "Não informado",
                "Valor total recebido": format_brl(float(r["valor_total"])),
                "Nº transações": int(r["n_tx"]),
            }
        )
    return pd.DataFrame(rows_out, columns=cols_out)


st.markdown('<p class="rt-section-title">Ranking de contas críticas</p>', unsafe_allow_html=True)

st.markdown("**Maior volume recebido**")
top_vol = metrics["top_volume_recebido"].copy()
if not top_vol.empty:
    rank_vol = _enrich_ranking_contas(top_vol, filtered)
    rank_vol = rank_vol.sort_values("Valor recebido", ascending=False)
    display_cols = [DATA_CRIACAO_COL, "Conta", "Nome recebedor", "CPF/CNPJ recebedor", "Valor recebido", "Nome remetente", "CPF/CNPJ remetente"]
    rank_vol = rank_vol[[c for c in display_cols if c in rank_vol.columns]]
    rank_vol["Valor recebido"] = rank_vol["Valor recebido"].apply(lambda x: format_brl(x) if pd.notna(x) else "—")
    st.dataframe(
        add_placeholder_data_criacao_column(rank_vol),
        width="stretch",
        hide_index=True,
        height=320,
        column_config={
            DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium"),
            "Valor recebido": st.column_config.TextColumn("Valor recebido"),
        },
    )
else:
    st.dataframe(
        add_placeholder_data_criacao_column(
            pd.DataFrame(
                columns=[
                    DATA_CRIACAO_COL,
                    "Conta",
                    "Nome recebedor",
                    "CPF/CNPJ recebedor",
                    "Valor recebido",
                    "Nome remetente",
                    "CPF/CNPJ remetente",
                ]
            )
        ),
        width="stretch",
        hide_index=True,
        height=200,
        column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
    )

# ========== 5) SUSPICIOUS CLUSTERS ==========
st.markdown('<p class="rt-section-title">Clusters suspeitos</p>', unsafe_allow_html=True)
clusters_df = metrics["clusters_detalhes"].copy()
_cluster_nodes_map = metrics.get("cluster_id_to_nodes") or {}
if not clusters_df.empty:
    st.caption(
        "**Drill-down:** abaixo, cada cluster pode ser expandido. "
        "**Recebedores (nome, documento, conta, banco, totais)** só são listados integralmente quando "
        "**nível de risco = crítico**; nos demais casos é exibido o aviso de dados insuficientes para análise detalhada."
    )
    for _, _crow in clusters_df.iterrows():
        _cid = int(_crow["cluster_id"])
        _rl = _crow.get("risk_level", "—")
        _nc = int(_crow["num_contas"]) if pd.notna(_crow.get("num_contas")) else 0
        _label = f"Cluster {_cid} — risco: {_rl} — {_nc} conta(s) recebedora(s) no grafo"
        with st.expander(_label, expanded=False):
            if _risk_level_is_critico(_rl):
                _rec_df = _build_cluster_recebedores_table(_cid, filtered, _cluster_nodes_map)
                if _rec_df.empty:
                    st.info(
                        "Nenhum registro no **período filtrado** para as contas deste cluster — "
                        "verifique o recorte de datas ou se as arestas do cluster têm transações na base."
                    )
                else:
                    st.dataframe(
                        add_placeholder_data_criacao_column(_rec_df),
                        width="stretch",
                        hide_index=True,
                        height=min(420, 80 + len(_rec_df) * 36),
                        column_config={
                            DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium"),
                            "Nome recebedor": st.column_config.TextColumn("Nome recebedor", width="large"),
                            "CPF/CNPJ recebedor": st.column_config.TextColumn("CPF/CNPJ recebedor", width="medium"),
                            "Conta recebedora": st.column_config.TextColumn("Conta recebedora", width="small"),
                            "Banco recebedor": st.column_config.TextColumn("Banco recebedor", width="medium"),
                            "Valor total recebido": st.column_config.TextColumn("Valor total recebido", width="small"),
                            "Nº transações": st.column_config.NumberColumn("Nº transações", width="small"),
                        },
                    )
            else:
                st.warning("Dados insuficientes para análise do cluster")
else:
    st.info("Nenhum cluster com 4+ nós encontrado.")

# ========== 6) ACCOUNT INVESTIGATION (360) ==========
st.markdown('<p class="rt-section-title">Investigação de conta (visão 360)</p>', unsafe_allow_html=True)
all_accounts_360 = sorted(filtered["CONTA_RECEBEDOR"].dropna().astype(str).unique().tolist())
conta_df = pd.DataFrame()
conta_sel = None

if not all_accounts_360:
    st.info("Nenhuma conta para investigar no período filtrado.")
else:
    account_input = st.text_input(
        "Digite ou cole a conta para investigação (busca parcial)",
        key="rt_conta_360_input",
        placeholder="Ex.: 301250731 ou parte do número — deixe vazio para ver a primeira conta da lista",
    )

    if account_input is not None and str(account_input).strip():
        account_input = str(account_input).strip()
        conta_df = filtered[
            filtered["CONTA_RECEBEDOR"].astype(str).str.contains(account_input, na=False, regex=False)
        ].copy()
        n_registros = len(conta_df)
        print(f"[360] Conta digitada: '{account_input}' — Registros encontrados: {n_registros}")
        if conta_df.empty:
            st.warning("Conta não encontrada na base.")
        else:
            conta_sel = str(conta_df["CONTA_RECEBEDOR"].iloc[0])
            if conta_df["CONTA_RECEBEDOR"].nunique() > 1:
                st.caption(
                    f"Busca parcial: {conta_df['CONTA_RECEBEDOR'].nunique()} contas encontradas. "
                    f"Exibindo dados agregados dos registros que contêm \"{account_input}\"."
                )
    else:
        # Campo vazio: mantém todos os cards e a análise (primeira conta, ordem alfabética)
        conta_sel = all_accounts_360[0]
        conta_df = filtered[filtered["CONTA_RECEBEDOR"].astype(str) == str(conta_sel)].copy()
        st.caption(
            f"**Conta exibida (padrão):** `{conta_sel}` — Digite ou cole outra conta para buscar. "
            f"Total de contas no período: **{len(all_accounts_360)}**."
        )
        print(f"[360] Modo padrão — conta={conta_sel} — registros={len(conta_df)}")

if all_accounts_360 and not conta_df.empty and conta_sel is not None:
    analise = analise_conta_360(conta_df, graph, metrics, node_categories, conta_sel)

    # KPIs: totais + ticket médio, maior, menor, score médio
    st.markdown("**Métricas da conta**")
    k1, k2, k3, k4, k5 = st.columns(5, gap="medium")
    with k1:
        st.metric("Total de transações", f"{len(conta_df):,}".replace(",", "."))
    with k2:
        rem_col = "ID_ORGANIZACAO" if "ID_ORGANIZACAO" in conta_df.columns else "ID_PLACE"
        num_rem = conta_df[rem_col].nunique() if rem_col in conta_df.columns else 0
        st.metric("Total de remetentes", f"{num_rem:,}".replace(",", "."))
    with k3:
        _vfin360 = float(
            pd.to_numeric(conta_df["VALOR_RETIRADA"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
            .sum()
        )
        st.metric("Valor financeiro total", format_brl(_vfin360))
    with k4:
        st.metric("Ticket médio", format_brl(analise["ticket_medio"]))
    with k5:
        st.metric("Score médio", format_metric_float(analise["score_medio"]))

    if "SUSPEITA" in conta_df.columns:
        _ns = int(pd.to_numeric(conta_df["SUSPEITA"], errors="coerce").fillna(0).eq(1).sum())
        st.caption(f"Registros com **SUSPEITA=1** (regra de negócio) nesta conta: **{_ns}**.")

    # Mesmo texto do antigo tooltip/popover: sempre visível, largura total, abaixo da 1ª linha de métricas
    _txt_parecer_360 = _parecer_analitico_conta_text(filtered, conta_sel, graph, metrics, node_categories)
    with st.container(border=True):
        st.markdown('<p class="rt-parecer-fixo-titulo">Parecer analítico</p>', unsafe_allow_html=True)
        st.caption("Resumo automático desta conta no período filtrado.")
        st.markdown(_txt_parecer_360)

    k6, k7, k8, _k, _ = st.columns(5, gap="medium")
    with k6:
        st.metric("Maior transação", format_brl(analise["max_valor"]))
    with k7:
        st.metric("Menor transação", format_brl(analise["min_valor"]))

    # Perfil da conta
    st.markdown("**Perfil da conta**")
    tipo = analise["tipo_conta"]
    comportamentos = analise["comportamento"] if analise["comportamento"] else ["Nenhum padrão destacado"]
    st.caption(f"**Tipo:** {tipo} — **Comportamento:** {', '.join(comportamentos)}")

    # Indicadores de risco (flags)
    st.markdown("**Indicadores de risco**")
    f1, f2, f3, f4 = st.columns(4, gap="medium")
    with f1:
        st.metric("Concentração remetente", "Sim" if analise["concentracao_remetente"] else "Não")
    with f2:
        st.metric("Alta frequência", "Sim" if analise["alta_frequencia"] else "Não")
    with f3:
        st.metric("Comportamento noturno", "Sim" if analise["comportamento_noturno"] else "Não")
    with f4:
        st.metric("Recorrência", "Sim" if analise["recorrencia"] else "Não")

    # Classificação e recomendação
    st.markdown("**Classificação e recomendação**")
    col_class, col_rec = st.columns(2, gap="medium")
    with col_class:
        st.metric("Classificação da conta", analise["classificacao"].capitalize())
    with col_rec:
        st.metric("Recomendação", analise["recomendacao"])

    # Contexto de rede (se disponível)
    st.markdown("**Contexto de rede**")
    net_c1, net_c2, net_c3 = st.columns(3, gap="medium")
    with net_c1:
        st.metric("Cluster", analise["cluster_id"] if analise["cluster_id"] is not None else "—")
    with net_c2:
        st.metric("Número de conexões", f"{analise['num_conexoes']:,}".replace(",", "."))
    with net_c3:
        st.metric("Tipo na rede", analise["tipo_rede"].capitalize())

    # Tabela de transações
    st.markdown("**Transações da conta**")
    fill_data_criacao_place_e_org(conta_df)
    detail_cols = [
        DATA_CRIACAO_COL,
        "DATA_SOLICITACAO",
        "NOME_PLACE",
        "BANCO_RECEBEDOR",
        "CONTA_RECEBEDOR",
        "VALOR_RETIRADA",
        "HORA_TRANSACAO",
        "VALOR_ALTO",
        "SAQUE_NOTURNO",
        "DESTINATARIO_REPETIDO",
        "RISK_SCORE",
        "SUSPEITA",
    ]
    detail = conta_df[[c for c in detail_cols if c in conta_df.columns]].copy()
    if DATA_CRIACAO_COL not in detail.columns:
        detail.insert(0, DATA_CRIACAO_COL, pd.NaT)
        fill_data_criacao_place_e_org(detail)
    detail[DATA_CRIACAO_COL] = format_data_criacao_series(detail[DATA_CRIACAO_COL])
    detail = detail.rename(columns={
        "DATA_SOLICITACAO": "date",
        "NOME_PLACE": "place",
        "BANCO_RECEBEDOR": "receiving_bank",
        "CONTA_RECEBEDOR": "receiving_account",
        "VALOR_RETIRADA": "received_value",
        "HORA_TRANSACAO": "hora_transacao",
        "VALOR_ALTO": "valor_alto",
        "SAQUE_NOTURNO": "saque_noturno",
        "DESTINATARIO_REPETIDO": "destinatario_repetido",
        "RISK_SCORE": "risk_score",
        "SUSPEITA": "suspeita",
    })
    detail = reorder_data_criacao_first(detail)
    if "date" in detail.columns:
        detail["date"] = pd.to_datetime(detail["date"], errors="coerce").dt.strftime("%d/%m/%Y %H:%M")
    if "received_value" in detail.columns:
        detail["received_value"] = detail["received_value"].apply(lambda x: format_brl(x) if pd.notna(x) else "")
    st.caption(f"Transações da conta selecionada: {len(detail)} registros")
    st.dataframe(
        add_placeholder_data_criacao_column(detail),
        width="stretch",
        hide_index=True,
        height=400,
        column_config={DATA_CRIACAO_COL: st.column_config.TextColumn(DATA_CRIACAO_COL, width="medium")},
    )
