"""
Carrega XLSX de checkout (schema Metabase/saques), aplica o mesmo pipeline da ingestão
e grava data/uploaded/saques.csv + data/processed/saques_processado.csv para o painel Streamlit.

Uso (a partir da pasta zig_risk_monitor):
  python scripts/import_planilha_checkout.py
  python scripts/import_planilha_checkout.py caminho/para/arquivo.xlsx
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    import pandas as pd

    from modules.data_loader import (
        UPLOAD_PATH,
        _apply_historico_filter,
        _coerce_types,
        _dedupe_by_id_saque,
        _enrich_place_columns,
        _normalize_columns,
        date_column_for_period_filter,
        save_processed_data,
        validate_schema,
    )
    from modules.feature_engineering import add_features
    from modules.risk_engine import calcular_score

    xlsx = ROOT / "planilhas_check_out" / "check_out_2025_2026.xlsx"
    if len(sys.argv) > 1:
        xlsx = Path(sys.argv[1]).expanduser().resolve()
    if not xlsx.is_file():
        print("Arquivo não encontrado:", xlsx)
        sys.exit(1)

    print("Lendo Excel (pode demorar em arquivos grandes)...")
    df = pd.read_excel(xlsx)
    df = _normalize_columns(df)
    ok, missing = validate_schema(df)
    if not ok:
        print("Colunas ausentes:", ", ".join(missing))
        sys.exit(1)

    df = _coerce_types(df)
    n0 = len(df)
    df = _apply_historico_filter(df)
    print(f"Filtro histórico (.env / padrão): {n0} -> {len(df)} linhas")

    df = _enrich_place_columns(df)
    if "ID_SAQUE" in df.columns:
        sort_col = "DATA_SOLICITACAO" if "DATA_SOLICITACAO" in df.columns else date_column_for_period_filter(df)
        if sort_col in df.columns:
            df = df.sort_values(sort_col, ascending=False, na_position="last")
        df = _dedupe_by_id_saque(df, "[IMPORT]")

    UPLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(UPLOAD_PATH, index=False, encoding="utf-8")
    print("CSV bruto:", UPLOAD_PATH.resolve(), f"({len(df)} linhas)")

    print("Feature engineering + score de risco...")
    df = add_features(df)
    df = calcular_score(df)
    save_processed_data(df)
    print("Base processada gravada. Linhas:", len(df))
    print("Arquivo:", (ROOT / "data" / "processed" / "saques_processado.csv").resolve())


if __name__ == "__main__":
    main()
