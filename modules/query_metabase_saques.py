"""
Baixa a card de saques do Metabase (METABASE_CARD_ID no .env) e grava CSV.

Por que o painel pode parecer "desatualizado"?
- O Streamlit, com Metabase configurado, lê os dados pela API do Metabase (não lê
  saques_metabase_export.csv). A API costuma limitar o resultado (~2000 linhas).
- Para o dashboard usar um CSV completo que você gerou: grave em saques.csv com
  --write-upload e defina no .env: ZIG_LOAD_FROM_CSV_ONLY=1

Uso (PowerShell), pasta zig_risk_monitor:
  .\\.venv\\Scripts\\python.exe docs\\query_metabase_saques.py
  .\\.venv\\Scripts\\python.exe docs\\query_metabase_saques.py --write-upload
  .\\.venv\\Scripts\\python.exe docs\\query_metabase_saques.py --print-sql 2026-03-16 2026-03-31

SQL Metabase (filtros variáveis): backend/sql/metabase_withdraw_query.sql
SQL Snowflake (período BETWEEN): backend/sql/snowflake_saques_por_periodo.sql
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    load_dotenv()
except ImportError:
    pass

from modules.data_loader import UPLOAD_PATH
from modules.metabase_client import load_from_metabase


def montar_query_sql_snowflake(data_inicio: str, data_fim: str) -> str:
    """
    Monta o SQL para rodar direto no Snowflake (referência; período em PAYMENTDATE).
    Não executa conexão — só retorna o texto (igual à ideia do seu montar_query).
    """
    return f"""
    SELECT
       RFD.RFD_WITHDRAW_REQUESTS.ID AS ID_Saque,
       RFD.RFD_PLACES.ID AS ID_PLACE,
        DATEADD(HOUR, -3, RFD.RFD_WITHDRAW_REQUESTS.REQUESTDATE) AS Data_Solicitacao,
        DATEADD(HOUR, -3, RFD.RFD_WITHDRAW_REQUESTS.PAYMENTDATE) AS Data_Pagamento,
        RFD.RFD_PLACES.NAME AS NOME_PLACE,
        metabase.Clientes.doc_contrato AS CNPJ,
        RFD.RFD_ORGANIZATIONS.NAME AS NOME_ORGANIZACAO,
        RFD.RFD_ORGANIZATIONS.USERNAME AS ID_ORGANIZACAO,
        RFD.RFD_ZIG_LOCATIONS.NAME AS CIDADE,
        CASE
            WHEN TRIM(RFD.RFD_PAY_TRANSACTIONS.PAYMENTMETHOD) IS NULL
                 OR TRIM(RFD.RFD_PAY_TRANSACTIONS.PAYMENTMETHOD) = ''
            THEN 'BOLETO'
            ELSE RFD.RFD_PAY_TRANSACTIONS.PAYMENTMETHOD
        END AS Metodo_Pagamento_Especifico,
        (RFD.RFD_WITHDRAW_REQUESTS.WITHDRAWVALUE / 100.0) AS Valor_Retirada,
        RFD.RFD_WITHDRAW_REQUESTS.STATUS,
        RFD.RFD_WITHDRAW_REQUESTS_PIX.RECEIVER,
        RFD.RFD_WITHDRAW_REQUESTS.CLIENTOBSERVATION AS Motivo,
        RFD.RFD_WITHDRAW_REQUESTS.BANKPAYMENTSLIPINFO AS Info_Boleto,
        RFD.RFD_BANK_ACCOUNTS.pixkey_type AS Tipo_Chave_PIX,
        RFD.RFD_BANK_ACCOUNTS.pixkey_value AS Valor_Chave_PIX,
        RFD.RFD_BANK_ACCOUNTS.RECEIVER_BANK AS Banco_Recebedor,
        RFD.RFD_BANK_ACCOUNTS.RECEIVER_AGENCY AS Agencia_Recebedor,
        CASE
            WHEN RFD.RFD_BANK_ACCOUNTS.RECEIVER_ACCOUNT IS NULL
            THEN PARSE_JSON(RFD.RFD_WITHDRAW_REQUESTS.BANKPAYMENTSLIPINFO):beneficiary::string
            ELSE RFD.RFD_BANK_ACCOUNTS.RECEIVER_ACCOUNT
        END AS Conta_Recebedor,
        COALESCE(
            RFD.RFD_ORGANIZATIONS.createdAt,
            RFD.RFD_PLACES.CREATEDAT
        ) AS data_criacao_place_e_org
    FROM RFD.RFD_WITHDRAW_REQUESTS
    LEFT JOIN RFD.RFD_WITHDRAW_REQUESTS_PIX
        ON RFD.RFD_WITHDRAW_REQUESTS.ID = RFD.RFD_WITHDRAW_REQUESTS_PIX.WITHDRAWREQUESTID
    LEFT JOIN RFD.RFD_PAY_TRANSACTIONS
        ON RFD.RFD_WITHDRAW_REQUESTS_PIX.ID = RFD.RFD_PAY_TRANSACTIONS.SYSTEMREFERENCE
    LEFT JOIN RFD.RFD_BANK_ACCOUNTS
        ON RFD.RFD_WITHDRAW_REQUESTS.BANKACCOUNTID = RFD.RFD_BANK_ACCOUNTS.ID
    LEFT JOIN RFD.RFD_PLACES
        ON RFD.RFD_WITHDRAW_REQUESTS.PLACEID = RFD.RFD_PLACES.ID
    LEFT JOIN RFD.RFD_ORGANIZATIONS
        ON RFD.RFD_PLACES.ORGANIZATIONID = RFD.RFD_ORGANIZATIONS.ID
    LEFT JOIN RFD.RFD_ZIG_LOCATIONS
        ON RFD.RFD_PLACES.ZIGLOCATIONID = RFD.RFD_ZIG_LOCATIONS.ID
    LEFT JOIN metabase.Clientes
        ON RFD.RFD_WITHDRAW_REQUESTS.PLACEID = metabase.Clientes.fin_facts_place_id
    WHERE 1=1
        AND RFD.RFD_WITHDRAW_REQUESTS.STATUS = 'Paid'
        AND RFD.RFD_WITHDRAW_REQUESTS.TYPE = 'Withdraw'
        AND DATE(RFD.RFD_WITHDRAW_REQUESTS.PAYMENTDATE)
            BETWEEN '{data_inicio}' AND '{data_fim}'
    ORDER BY
        RFD.RFD_ORGANIZATIONS.NAME ASC,
        RFD.RFD_PLACES.NAME ASC
    """.strip()


def main() -> None:
    p = argparse.ArgumentParser(description="Export Metabase card de saques para CSV.")
    p.add_argument(
        "--write-upload",
        action="store_true",
        help=f"Também grava em {UPLOAD_PATH} (arquivo que o app usa quando força CSV).",
    )
    p.add_argument(
        "--print-sql",
        nargs=2,
        metavar=("DATA_INI", "DATA_FIM"),
        help="Só imprime SQL Snowflake (YYYY-MM-DD) e sai; não chama Metabase.",
    )
    args = p.parse_args()

    if args.print_sql:
        d1, d2 = args.print_sql
        print(montar_query_sql_snowflake(d1, d2))
        return

    df, err = load_from_metabase()
    if err:
        print(f"Erro: {err}")
        sys.exit(1)

    out_dir = ROOT / "data" / "uploaded"
    out_dir.mkdir(parents=True, exist_ok=True)
    export_path = out_dir / "saques_metabase_export.csv"
    df.to_csv(export_path, index=False, encoding="utf-8")
    print(f"OK: {len(df)} linhas gravadas em {export_path}")

    if len(df) >= 2000:
        print(
            "Aviso: 2000+ linhas costumam ser o limite da API/consulta do Metabase. "
            "Para base completa: exporte no Snowflake (veja montar_query / snowflake_saques_por_periodo.sql) "
            "ou aumente o limite no Metabase."
        )

    if args.write_upload:
        upload_full = ROOT / UPLOAD_PATH if not UPLOAD_PATH.is_absolute() else UPLOAD_PATH
        upload_full.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(upload_full, index=False, encoding="utf-8")
        print(f"OK: também gravado em {upload_full} (use ZIG_LOAD_FROM_CSV_ONLY=1 no .env para o app priorizar este arquivo).")


if __name__ == "__main__":
    main()
