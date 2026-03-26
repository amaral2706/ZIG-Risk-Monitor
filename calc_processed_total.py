import pandas as pd


CSV_PATH = r"data/processed/saques_processado.csv"
DATE_COL = "DATA_SOLICITACAO"
AMOUNT_COL = "VALOR_RETIRADA"

START = pd.Timestamp("2026-01-01").date()
END = pd.Timestamp("2026-03-16").date()


def main() -> None:
    df = pd.read_csv(CSV_PATH)
    if DATE_COL not in df.columns:
        raise SystemExit(f"Missing date column {DATE_COL}. Columns: {list(df.columns)}")
    if AMOUNT_COL not in df.columns:
        raise SystemExit(f"Missing amount column {AMOUNT_COL}. Columns: {list(df.columns)}")

    dt = pd.to_datetime(df[DATE_COL], errors="coerce", utc=True)
    dates = dt.dt.tz_convert("UTC").dt.date

    amt = pd.to_numeric(df[AMOUNT_COL], errors="coerce").fillna(0.0)
    mask = (pd.to_datetime(dates) >= pd.Timestamp(START)) & (pd.to_datetime(dates) <= pd.Timestamp(END))

    total = float(amt[mask].sum())
    count = int(mask.sum())
    print("Processed CSV totals")
    print("Rows in range:", count)
    print("Sum", total)


if __name__ == "__main__":
    main()

