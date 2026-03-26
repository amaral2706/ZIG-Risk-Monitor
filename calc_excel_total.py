import pandas as pd


EXCEL_PATH = r"C:\Users\BrunaAmaral\Downloads\base_motor_pix___alerta_2026-03-17T13_50_04.729232039Z.xlsx"
START = pd.Timestamp("2026-01-01")
END = pd.Timestamp("2026-03-16")


def pick_amount_column(df: pd.DataFrame) -> str | None:
    cols_map = {str(c).strip().upper(): c for c in df.columns}
    for cand in [
        "VALOR_RETIRADA",
        "VALOR DA RETIRADA",
        "VALOR_TOTAL",
        "VALOR",
        "VLR_RETIRADA",
        "VALOR_PIX",
        "VALOR DO PIX",
        "VLR_PIX",
    ]:
        if cand in cols_map:
            return cols_map[cand]
    return None


def pick_date_columns(df: pd.DataFrame) -> list[str]:
    cols_map = {str(c).strip().upper(): c for c in df.columns}
    candidates = ["DATA_SOLICITACAO", "DATA PAGAMENTO", "DATA_PAGAMENTO", "DATARETIRADA"]
    out: list[str] = []
    for cand in candidates:
        if cand in cols_map:
            out.append(cols_map[cand])
    if out:
        return out
    # Fallback: any column containing DATA
    return [c for c in df.columns if "DATA" in str(c).upper()]


def main() -> None:
    xls = pd.ExcelFile(EXCEL_PATH)
    sheet = xls.sheet_names[0]
    df = pd.read_excel(EXCEL_PATH, sheet_name=sheet)

    amount_col = pick_amount_column(df)
    date_cols = pick_date_columns(df)

    print("Sheet:", sheet)
    print("Shape:", df.shape)
    print("Columns:", list(df.columns))
    print("Amount column:", amount_col)
    print("Date columns:", date_cols)

    if not amount_col:
        raise SystemExit("Could not find amount column in Excel.")
    if not date_cols:
        raise SystemExit("Could not find date columns in Excel.")

    # Normalize amount (handles BR numbers like 1.234,56)
    amt = df[amount_col]
    if amt.dtype == object:
        amt_num = pd.to_numeric(
            amt.astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
            errors="coerce",
        )
    else:
        amt_num = pd.to_numeric(amt, errors="coerce")

    for dc in date_cols:
        d = pd.to_datetime(df[dc], errors="coerce")
        dates_only = d.dt.date
        mask = (pd.to_datetime(dates_only) >= START) & (pd.to_datetime(dates_only) <= END)
        s = float(pd.Series(amt_num[mask]).fillna(0).sum())
        n = int(mask.sum())
        print(f"Sum using {dc}: {s:,.2f} ({n} rows)")


if __name__ == "__main__":
    main()

