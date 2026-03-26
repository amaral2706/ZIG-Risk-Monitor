"""
Gera workbook Excel com documentação de cálculos do ZIG Risk Monitor.
Executar na pasta zig_risk_monitor: python scripts/gerar_excel_calculos_documentacao.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Calculos_Projeto_ZIG_Risk_Monitor.xlsx"


def _rows() -> list[dict]:
    r: list[dict] = []
    L = lambda **k: r.append(k)  # noqa: E731

    # --- Índice será sheet separado ---

    # ===== modules/risk_engine.py =====
    L(
        categoria="Motor de risco (módulo)",
        menu="Todas as telas que usam dados processados",
        indicador="RISK_SCORE (linha a linha)",
        formula_regra="Inicia em 0; soma pontos: +30 se VALOR_RETIRADA>3000; +20 se HORA_TRANSACAO entre 0 e 5; +10 se METODO_PAGAMENTO_ESPECIFICO contém 'PIX'; +10 se STATUS≠'PAGO'; +25 se nunique(ID_ORGANIZACAO) por CONTA_RECEBEDOR>5 (via transform); +30 se VALOR_RETIRADA > 5× média do ID_ORGANIZACAO (transform mean). Depois clip(0,100).",
        colunas="VALOR_RETIRADA, HORA_TRANSACAO, METODO_PAGAMENTO_ESPECIFICO, STATUS, CONTA_RECEBEDOR, ID_ORGANIZACAO",
        arquivo="modules/risk_engine.py :: calcular_score",
        obs="classificar_risco usa limites ≤30, ≤60, ≤80 para níveis.",
    )
    L(
        categoria="Motor de risco (módulo)",
        menu="—",
        indicador="RISK_LEVEL",
        formula_regra="A partir de RISK_SCORE: ≤30 'baixo risco'; ≤60 'risco medio'; ≤80 'alto risco'; senão 'risco critico'.",
        colunas="RISK_SCORE",
        arquivo="modules/risk_engine.py :: classificar_risco",
        obs="",
    )
    L(
        categoria="Motor de risco (módulo)",
        menu="—",
        indicador="SUSPEITA",
        formula_regra="1 se RISK_SCORE >= 61, senão 0.",
        colunas="RISK_SCORE",
        arquivo="modules/risk_engine.py :: calcular_score",
        obs="",
    )

    # ===== modules/feature_engineering.py =====
    L(
        categoria="Features (módulo)",
        menu="Base enriquecida",
        indicador="HORA_TRANSACAO",
        formula_regra="Hora (0-23) de DATA_SOLICITACAO; fillna(0).",
        colunas="DATA_SOLICITACAO",
        arquivo="modules/feature_engineering.py :: add_features",
        obs="",
    )
    L(
        categoria="Features (módulo)",
        menu="—",
        indicador="SAQUE_NOTURNO",
        formula_regra="1 se HORA_TRANSACAO entre 0 e 5 inclusive, senão 0.",
        colunas="HORA_TRANSACAO",
        arquivo="modules/feature_engineering.py",
        obs="",
    )
    L(
        categoria="Features (módulo)",
        menu="—",
        indicador="VALOR_ALTO",
        formula_regra="1 se VALOR_RETIRADA > 3000.",
        colunas="VALOR_RETIRADA",
        arquivo="modules/feature_engineering.py",
        obs="",
    )
    L(
        categoria="Features (módulo)",
        menu="—",
        indicador="DESTINATARIO_REPETIDO",
        formula_regra="1 se count(ID_SAQUE) por CONTA_RECEBEDOR > 1 (transform).",
        colunas="CONTA_RECEBEDOR, ID_SAQUE",
        arquivo="modules/feature_engineering.py",
        obs="",
    )
    L(
        categoria="Features (módulo)",
        menu="—",
        indicador="PERIODO_MES / SEMANA / DIA",
        formula_regra="Períodos a partir de DATA_SOLICITACAO (to_period).",
        colunas="DATA_SOLICITACAO",
        arquivo="modules/feature_engineering.py",
        obs="",
    )
    L(
        categoria="Features (módulo)",
        menu="—",
        indicador="IS_BOLETO / TERCEIRO / RECEIVER_TIPO",
        formula_regra="Regras em INFO_BOLETO, VALOR_CHAVE_PIX vs CNPJ, dígitos PF/PJ em chave.",
        colunas="INFO_BOLETO, VALOR_CHAVE_PIX, CNPJ",
        arquivo="modules/feature_engineering.py",
        obs="",
    )

    # ===== modules/data_loader.py (VALOR) =====
    L(
        categoria="Carga de dados",
        menu="Pipeline / CSV",
        indicador="VALOR_RETIRADA normalizado",
        formula_regra="Se já numérico: to_numeric. Se texto com vírgula (BR): remove pontos de milhar, troca vírgula por ponto. Se texto sem vírgula: to_numeric direto.",
        colunas="VALOR_RETIRADA",
        arquivo="modules/data_loader.py :: _parse_valor_retirada_series",
        obs="Evita inflar valores ao remover ponto decimal errado.",
    )

    # ===== app.py Dashboard =====
    L(
        categoria="Dashboard",
        menu="Dashboard (app.py)",
        indicador="Volume financeiro total",
        formula_regra="SOMA(VALOR_RETIRADA) com valores coerced para numérico no recorte filtrado.",
        colunas="VALOR_RETIRADA",
        arquivo="app.py",
        obs="Recorte = filtered por datas.",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard",
        indicador="Total transações (cards)",
        formula_regra="Número de linhas do filtered (len).",
        colunas="—",
        arquivo="app.py",
        obs="Contador de registros no recorte.",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard",
        indicador="Ticket médio",
        formula_regra="SOMA(VALOR_RETIRADA) / CONT.VALORES ÚNICOS(ID_SAQUE) se ID_SAQUE existir e não vazio; senão SOMA / número de linhas.",
        colunas="VALOR_RETIRADA, ID_SAQUE",
        arquivo="app.py",
        obs="Não usa MÉDIA simples de VALOR por linha.",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard",
        indicador="% transações suspeitas",
        formula_regra="SOMA(SUSPEITA) / total_transacoes * 100.",
        colunas="SUSPEITA",
        arquivo="app.py",
        obs="total_transacoes = len(filtered).",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard",
        indicador="Transações alto risco (contagem linhas)",
        formula_regra="Contagem de linhas onde RISK_LEVEL ∈ {'alto risco','risco critico'}.",
        colunas="RISK_LEVEL",
        arquivo="app.py",
        obs="",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard",
        indicador="Contas suspeitas",
        formula_regra="CONT.VALORES ÚNICOS(CONTA_RECEBEDOR) nas linhas alto risco acima.",
        colunas="CONTA_RECEBEDOR, RISK_LEVEL",
        arquivo="app.py",
        obs="",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard",
        indicador="Destinatários recorrentes",
        formula_regra="CONT.VALORES ÚNICOS(CONTA_RECEBEDOR) onde DESTINATARIO_REPETIDO=1.",
        colunas="CONTA_RECEBEDOR, DESTINATARIO_REPETIDO",
        arquivo="app.py",
        obs="",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard — Evolução financeira",
        indicador="Série ano/mês",
        formula_regra="Agrupar por ID_SAQUE: VALOR_RETIRADA=max, DATA_SOLICITACAO=first; extrair ANO/MÊS; agrupar por ANO+MÊS somando VALOR_RETIRADA.",
        colunas="ID_SAQUE, VALOR_RETIRADA, DATA_SOLICITACAO",
        arquivo="app.py",
        obs="Uma linha por saque antes de somar por mês.",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard — Método pagamento",
        indicador="qtd_transacoes / valor_total / percentual",
        formula_regra="Se ID_SAQUE: agrupar por ID_SAQUE (soma valor, primeiro método não vazio); mapear bucket TED/PIX/BOLETO/OUTROS; agrupar por bucket: count linhas, sum valor; percentual = valor_total_bucket / SOMA(valor_total) * 100.",
        colunas="ID_SAQUE, METODO_PAGAMENTO_ESPECIFICO, METODO_PAGAMENTO, VALOR_RETIRADA",
        arquivo="app.py :: build_metodo_resumo_dashboard",
        obs="Ordem fixa TED, PIX, BOLETO, OUTROS.",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard — Bancos",
        indicador="Pizza (valor)",
        formula_regra="Top 5 bancos por SOMA(VALOR_RETIRADA); demais agregados em 'Outros'.",
        colunas="BANCO_RECEBEDOR, VALOR_RETIRADA",
        arquivo="app.py",
        obs="Rótulo vazio → 'Não informado'.",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard — Bancos",
        indicador="Barras (qtd e %)",
        formula_regra="Contagem de linhas por banco (após rótulo); top 5 por contagem + Outros; % = qtd_banco / len(filtered) * 100.",
        colunas="BANCO_RECEBEDOR",
        arquivo="app.py",
        obs="",
    )
    L(
        categoria="Dashboard",
        menu="Dashboard — Top contas",
        indicador="BANCO_RECEBEDOR na tabela",
        formula_regra="Por conta: banco com maior SOMA(VALOR_RETIRADA) no recorte.",
        colunas="CONTA_RECEBEDOR, BANCO_RECEBEDOR, VALOR_RETIRADA",
        arquivo="app.py",
        obs="",
    )

    # ===== pages/4_Relatorios.py =====
    L(
        categoria="Relatórios",
        menu="Relatórios",
        indicador="KPI Volume / transações / suspeitas / saques / média / taxa fraude",
        formula_regra="valor_total=SOMA(VALOR_RETIRADA); total_transacoes=CONT.ÚNICOS(ID_SAQUE) ou len(df); total_suspeitas=SOMA(SUSPEITA); qtde_saque=total_transacoes; valor_medio_saque=MÉDIA(VALOR_RETIRADA) por linha; fraud_rate=total_suspeitas/total_transacoes*100.",
        colunas="VALOR_RETIRADA, ID_SAQUE, SUSPEITA",
        arquivo="pages/4_Relatorios.py",
        obs="df já filtrado por período da página.",
    )
    L(
        categoria="Relatórios",
        menu="Relatórios — mensal",
        indicador="QT_TRANSACOES / TICKET_MEDIO / TAXA_FRAUDE",
        formula_regra="Por PERIODO_MES: sum valor; nunique ID_SAQUE ou count; TICKET_MEDIO = VALOR_TOTAL/TRANSACOES; TAXA_FRAUDE = SUSPEITAS/TRANSACOES*100; PCT_MUDANCA = pct_change volume; MA3 média móvel 3 meses.",
        colunas="PERIODO_MES, VALOR_RETIRADA, ID_SAQUE, SUSPEITA, RISK_SCORE",
        arquivo="pages/4_Relatorios.py",
        obs="",
    )
    L(
        categoria="Relatórios",
        menu="Relatórios — risco empilhado",
        indicador="Quantidade por RISK_LEVEL e mês",
        formula_regra="groupby PERIODO_MES + RISK_LEVEL: nunique ID_SAQUE ou size.",
        colunas="PERIODO_MES, RISK_LEVEL, ID_SAQUE",
        arquivo="pages/4_Relatorios.py",
        obs="",
    )
    L(
        categoria="Relatórios",
        menu="Relatórios — bancos pizza + barras",
        indicador="Pizza valor / Barras qtd",
        formula_regra="Igual dashboard: top5 valor + Outros; barras com qtd por mesmo slice (nunique ID_SAQUE por fatia ou len subconjunto); % = qtd/total_reg*100.",
        colunas="BANCO_RECEBEDOR, VALOR_RETIRADA, ID_SAQUE",
        arquivo="pages/4_Relatorios.py",
        obs="total_reg = nunique ID_SAQUE ou len(df).",
    )
    L(
        categoria="Relatórios",
        menu="Relatórios — métodos pizza + barras",
        indicador="Pizza % valor / Barras qtd",
        formula_regra="Classificar linha em TED/PIX/BOLETO/OUTROS por string; pizza: % = valor método / soma valores *100; barras: nunique ID_SAQUE por método; % sobre soma das qtds por método.",
        colunas="METODO_PAGAMENTO_ESPECIFICO, VALOR_RETIRADA, ID_SAQUE",
        arquivo="pages/4_Relatorios.py",
        obs="Ordem TED, PIX, BOLETO, OUTROS.",
    )

    # ===== pages/2_Rede_de_Transacoes.py =====
    L(
        categoria="Rede de Transações",
        menu="Visão geral da rede",
        indicador="Métricas _overview_metrics",
        formula_regra="total_contas=nunique CONTA; contas_suspeitas=nunique CONTA onde SUSPEITA=1; pct_contas_suspeitas=contas_suspeitas/total_contas*100; total_transacoes=len(df); volume_total=sum VALOR; volume_suspeito=sum VALOR onde SUSPEITA=1; pct_trans_susp=sum(SUSPEITA)/n*100; score_medio= média RISK_SCORE; hub=max por conta de nunique RECEIVER.",
        colunas="CONTA_RECEBEDOR, SUSPEITA, VALOR_RETIRADA, RISK_SCORE, RECEIVER",
        arquivo="pages/2_Rede_de_Transacoes.py :: _overview_metrics",
        obs="",
    )
    L(
        categoria="Rede de Transações",
        menu="Grafo",
        indicador="Volume / transações por nó CONTA",
        formula_regra="Soma weight e transactions das arestas ORG→CONTA.",
        colunas="grafo NetworkX",
        arquivo="pages/2_Rede_de_Transacoes.py :: _node_stats",
        obs="",
    )
    L(
        categoria="Rede de Transações",
        menu="Classificação nós",
        indicador="normal / bridge / alert",
        formula_regra="Percentil 90 volumes; alert se remetentes≥6 ou volume≥p90; bridge se remetentes≥3; senão normal.",
        colunas="arestas do grafo",
        arquivo="pages/2_Rede_de_Transacoes.py :: _classify_nodes",
        obs="",
    )
    L(
        categoria="Rede de Transações",
        menu="Tabela detalhamento / 360",
        indicador="Múltiplos",
        formula_regra="Ver analise_conta_360 e _build_detalhe_table (agregações por conta).",
        colunas="—",
        arquivo="pages/2_Rede_de_Transacoes.py",
        obs="",
    )
    L(
        categoria="Rede de Transações",
        menu="Parecer analítico + thresholds",
        indicador="Ticket alto / valor elevado / valor baixo supressão",
        formula_regra="Ticket alto: ticket_conta >= max(P75 tickets_rede*1.25, 25000). Valor elevado: total_conta >= max(P80 totais por conta, 50000). Valor baixo supressão: total < max(P25*0.6, 10000). Faixas score parecer: 0-29 baixo, 30-59 médio, 60-79 alto, 80+ crítico (texto).",
        colunas="VALOR_RETIRADA, CONTA_RECEBEDOR, RISK_SCORE, SUSPEITA",
        arquivo="pages/2_Rede_de_Transacoes.py :: _parecer_analitico_conta_text e helpers",
        obs="Não altera RISK_SCORE na base.",
    )

    # ===== modules/graph_engine.py =====
    L(
        categoria="Grafo (módulo)",
        menu="Rede — métricas",
        indicador="Aresta ORG→CONTA",
        formula_regra="weight += VALOR_RETIRADA; transactions +=1 por linha duplicada mesma aresta.",
        colunas="ID_ORGANIZACAO, CONTA_RECEBEDOR, VALOR_RETIRADA",
        arquivo="modules/graph_engine.py :: build_fraud_graph",
        obs="",
    )
    L(
        categoria="Grafo (módulo)",
        menu="Rede — métricas",
        indicador="Ranking volume / remetentes / alto volume",
        formula_regra="volume por conta sum VALOR; num_remetentes nunique ID_PLACE; top 10; contas alto volume: >= P95 volume.",
        colunas="conta, VALOR_RETIRADA, ID_PLACE",
        arquivo="modules/graph_engine.py :: graph_metrics",
        obs="",
    )
    L(
        categoria="Grafo (módulo)",
        menu="Clusters",
        indicador="Componentes conexos ≥4 nós",
        formula_regra="Grafo não direcionado; score_cluster = SOMA(RISK_SCORE linhas cluster) / num_contas; risk_level: ≥80 crítico, ≥60 alto, ≥30 médio, senão baixo; num_tx=len linhas cluster_df; padrões: concentração max org >20% volume; múltiplos orgs; tx/conta≥5; noturnas/tx>0.5 se HORA 0-5.",
        colunas="RISK_SCORE, VALOR_RETIRADA, ID_ORGANIZACAO/ID_PLACE, HORA_TRANSACAO",
        arquivo="modules/graph_engine.py",
        obs="Bridges: num_remetentes≥3 e volume≥P75.",
    )

    # ===== modules/analise_360.py =====
    L(
        categoria="Investigação 360",
        menu="Métricas conta",
        indicador="ticket_medio, max, min, score_medio",
        formula_regra="ticket = sum VALOR / count linhas; max/min VALOR; score_medio = média RISK_SCORE.",
        colunas="VALOR_RETIRADA, RISK_SCORE",
        arquivo="modules/analise_360.py",
        obs="",
    )
    L(
        categoria="Investigação 360",
        menu="Tipo conta / flags",
        indicador="concentração, alta frequência, noturno, recorrência",
        formula_regra="1 remetente (ID_ORG nunique)→concentrada; mediana intervalo minutos <120 ou ≥5 tx/dia→alta frequência; hora≥22 ou ≤6→noturno; groupby remetente size>1→recorrência.",
        colunas="ID_ORGANIZACAO, DATA_SOLICITACAO, HORA_TRANSACAO",
        arquivo="modules/analise_360.py",
        obs="Classificação texto por score médio 80/60/30.",
    )

    # ===== pages/3_Comportamento_Suspeito.py =====
    L(
        categoria="Comportamento suspeito",
        menu="KPIs segmento",
        indicador="_segment_kpis",
        formula_regra="n=len; vol=sum VALOR; n_tx=nunique ID_SAQUE ou n; susp60=contagem RISK_SCORE>=60; pct_susp60=susp60/n*100; mean_score=média RISK_SCORE; noturno e dest rep: soma flags SAQUE_NOTURNO e DESTINATARIO_REPETIDO com % sobre n; % valores com centavos: (VALOR mod 1 > 0.01)/n*100.",
        colunas="VALOR_RETIRADA, ID_SAQUE, RISK_SCORE, SAQUE_NOTURNO, DESTINATARIO_REPETIDO",
        arquivo="pages/3_Comportamento_Suspeito.py :: _segment_kpis",
        obs="",
    )
    L(
        categoria="Comportamento suspeito",
        menu="Concentração banco",
        indicador="_bank_concentration",
        formula_regra="Maior SOMA por BANCO_RECEBEDOR / volume total *100; flag se >70%.",
        colunas="BANCO_RECEBEDOR, VALOR_RETIRADA",
        arquivo="pages/3_Comportamento_Suspeito.py",
        obs="",
    )
    L(
        categoria="Comportamento suspeito",
        menu="Concentração conta",
        indicador="_account_concentration",
        formula_regra="top1% = maior conta / vol *100; top3% = soma 3 maiores / vol; flag se top1>50 OU top3>75.",
        colunas="CONTA_RECEBEDOR, VALOR_RETIRADA",
        arquivo="pages/3_Comportamento_Suspeito.py",
        obs="",
    )

    L(
        categoria="Referência Excel",
        menu="—",
        indicador="Exemplos genéricos",
        formula_regra="=SOMA(Faixa) para volume; =SOMASES ou tabela dinâmica para agregar por dimensão; =CONT.VALORES ÚNICOS para ID_SAQUE; =MÉDIA para score; =CONT.SE para suspeitas.",
        colunas="—",
        arquivo="—",
        obs="Replicar no Excel filtrando as mesmas linhas que o período do app.",
    )

    return r


def main() -> None:
    df = pd.DataFrame(_rows())
    df.insert(0, "id", range(1, len(df) + 1))
    df = df.rename(
        columns={
            "categoria": "Categoria",
            "menu": "Menu / Tela",
            "indicador": "Indicador ou variável",
            "formula_regra": "Fórmula ou regra (descrição)",
            "colunas": "Colunas / campos principais",
            "arquivo": "Arquivo / função",
            "obs": "Observações",
        }
    )

    indice = pd.DataFrame(
        {
            "Aba": [
                "Todos_Calculos",
                "Resumo_por_Menu",
            ],
            "Conteúdo": [
                "Lista completa ordenada por categoria (este workbook).",
                "Uma linha por menu com contagem de regras (resumo).",
            ],
        }
    )

    resumo_menu = (
        df.groupby("Menu / Tela", dropna=False)
        .agg(qtd_regras=("id", "count"))
        .reset_index()
        .sort_values("qtd_regras", ascending=False)
    )

    with pd.ExcelWriter(OUT, engine="openpyxl") as writer:
        indice.to_excel(writer, sheet_name="00_Indice", index=False)
        df.to_excel(writer, sheet_name="Todos_Calculos", index=False)
        resumo_menu.to_excel(writer, sheet_name="Resumo_por_Menu", index=False)

    print(f"Gerado: {OUT}")


if __name__ == "__main__":
    main()
