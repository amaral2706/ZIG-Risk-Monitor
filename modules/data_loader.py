import os
from datetime import date
from pathlib import Path
from typing import BinaryIO, Optional, Union

import numpy as np
import pandas as pd

from modules.datetime_brt import parse_timestamp_series as _parse_timestamp_column
from modules.metabase_client import load_from_metabase
from modules.table_columns import fill_data_criacao_place_e_org

UPLOAD_PATH = Path("data/uploaded/saques.csv")

# Evita DtypeWarning (tipos mistos por chunk) e preserva zeros à esquerda em dados bancários.
_CSV_ID_COLS = {
    "AGENCIA_RECEBEDOR": str,
    "CONTA_RECEBEDOR": str,
    "VALOR_CHAVE_PIX": str,
    "CNPJ": str,
}

# Acima deste tamanho (bytes), lê em chunks para reduzir pico de memória no tokenizer C do pandas.
_CSV_CHUNK_THRESHOLD_BYTES = int(os.getenv("ZIG_CSV_CHUNK_THRESHOLD_MB", "25")) * 1024 * 1024
_CSV_CHUNKSIZE = max(5_000, int(os.getenv("ZIG_CSV_CHUNKSIZE", "80000")))


def _read_saques_csv(path_or_buf: Union[str, Path, BinaryIO]) -> pd.DataFrame:
    """
    Lê CSV de saques com pico de memória mais baixo que ``low_memory=False``.
    - ``memory_map`` em disco quando possível.
    - Ficheiros grandes: leitura em chunks + concat.
    - Em falha OOM/tokenize: tenta chunks menores ou engine Python.
    """
    kw: dict = {
        "dtype": _CSV_ID_COLS,
        "encoding": "utf-8",
        "encoding_errors": "replace",
        "low_memory": True,
    }

    path_obj: Path | None = None
    if isinstance(path_or_buf, Path):
        path_obj = path_or_buf
    elif isinstance(path_or_buf, str):
        path_obj = Path(path_or_buf)

    def _read_chunked(p: Path, chunksize: int) -> pd.DataFrame:
        parts: list[pd.DataFrame] = []
        # kw já pode conter memory_map (definido antes); não duplicar no read_csv
        chunk_kw = {k: v for k, v in kw.items() if k != "memory_map"}
        for ch in pd.read_csv(p, chunksize=chunksize, memory_map=True, **chunk_kw):
            parts.append(ch)
        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, ignore_index=True, copy=False)

    if path_obj is not None and path_obj.is_file():
        kw["memory_map"] = True
        try:
            fsize = path_obj.stat().st_size
        except OSError:
            fsize = 0
        if fsize >= _CSV_CHUNK_THRESHOLD_BYTES:
            return _read_chunked(path_obj, _CSV_CHUNKSIZE)
        try:
            return pd.read_csv(path_obj, **kw)
        except (MemoryError, pd.errors.ParserError) as e:
            msg = str(e).lower()
            if "memory" in msg or "tokenizing" in msg:
                try:
                    return _read_chunked(path_obj, _CSV_CHUNKSIZE)
                except (MemoryError, pd.errors.ParserError):
                    return _read_chunked(path_obj, max(10_000, _CSV_CHUNKSIZE // 2))
            raise
        except OSError:
            return pd.read_csv(
                path_obj,
                **{k: v for k, v in kw.items() if k != "memory_map"},
            )

    # Buffer / stdin: sem memory_map
    try:
        return pd.read_csv(path_or_buf, **{k: v for k, v in kw.items() if k != "memory_map"})
    except (MemoryError, pd.errors.ParserError) as e:
        msg = str(e).lower()
        if "memory" not in msg and "tokenizing" not in msg:
            raise
        if path_obj is None or not path_obj.is_file():
            return pd.read_csv(
                path_or_buf,
                engine="python",
                **{k: v for k, v in kw.items() if k != "memory_map"},
            )
        return _read_chunked(path_obj, max(10_000, _CSV_CHUNKSIZE // 4))
# Período padrão do histórico: 01/01/2025 até a data atual. Configurável via .env:
# DATA_INICIO_HISTORICO=YYYY-MM-DD  e  DATA_FIM_HISTORICO=YYYY-MM-DD (vazio = hoje)
DEFAULT_DATA_INICIO = "2025-01-01"
PROCESSED_PATH = Path("data/processed/saques_processado.csv")

# (st_mtime, DataFrame) — evita reler ~100k+ linhas a cada rerun do Streamlit; invalida em save_processed_data.
_LOAD_PROCESSED_MEMO: tuple[float, pd.DataFrame] | None = None

REQUIRED_COLUMNS = [
    "ID_SAQUE",
    "DATA_SOLICITACAO",
    "DATA_PAGAMENTO",
    "NOME_PLACE",
    "CNPJ",
    "NOME_ORGANIZACAO",
    "ID_ORGANIZACAO",
    "CIDADE",
    "METODO_PAGAMENTO_ESPECIFICO",
    "VALOR_RETIRADA",
    "STATUS",
    "RECEIVER",
    "MOTIVO",
    "INFO_BOLETO",
    "TIPO_CHAVE_PIX",
    "VALOR_CHAVE_PIX",
    "BANCO_RECEBEDOR",
    "AGENCIA_RECEBEDOR",
    "CONTA_RECEBEDOR",
    "DATA_CRIACAO_PLACE_E_ORG",
]


def _load_places_lookup() -> pd.DataFrame:
    """
    Carrega planilha local `query_de_pesquisa_places_com_nome*.xlsx` (export manual).
    Origem Metabase da consulta: pergunta 7461 —
    https://metabase.zigpay.com.br/question/7461-query-de-pesquisa-places-com-nome
    (independe de METABASE_CARD_ID dos saques, ex. 7973).
    """
    base_dir = Path(__file__).resolve().parents[2]
    matches = sorted(base_dir.glob("query_de_pesquisa_places_com_nome*.xlsx"), reverse=True)
    if not matches:
        return pd.DataFrame()
    try:
        lookup = pd.read_excel(matches[0])
    except Exception:
        return pd.DataFrame()
    lookup.columns = [str(c).strip().upper() for c in lookup.columns]
    return lookup


def _enrich_place_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "ID_PLACE" not in out.columns:
        out["ID_PLACE"] = out.get("ID_ORGANIZACAO")
    if "NOME_PLACE" not in out.columns:
        out["NOME_PLACE"] = out.get("NOME_ORGANIZACAO")

    lookup = _load_places_lookup()
    if lookup.empty:
        return out

    id_lookup_col = "ID_PLACE" if "ID_PLACE" in lookup.columns else ("ID_ORGANIZACAO" if "ID_ORGANIZACAO" in lookup.columns else None)
    name_lookup_col = "NOME_PLACE" if "NOME_PLACE" in lookup.columns else ("NOME_ORGANIZACAO" if "NOME_ORGANIZACAO" in lookup.columns else None)
    if not id_lookup_col or not name_lookup_col:
        return out

    aux = lookup[[id_lookup_col, name_lookup_col]].dropna().drop_duplicates()
    aux.columns = ["ID_PLACE_AUX", "NOME_PLACE_AUX"]
    out["ID_PLACE"] = out["ID_PLACE"].astype(str)
    aux["ID_PLACE_AUX"] = aux["ID_PLACE_AUX"].astype(str)
    out = out.merge(aux, left_on="ID_PLACE", right_on="ID_PLACE_AUX", how="left")
    out["NOME_PLACE"] = out["NOME_PLACE_AUX"].fillna(out["NOME_PLACE"])
    return out.drop(columns=["ID_PLACE_AUX", "NOME_PLACE_AUX"], errors="ignore")


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [col.strip().upper() for col in df.columns]
    return df


def _parse_valor_retirada_series(col: pd.Series) -> pd.Series:
    """
    Converte VALOR_RETIRADA para float. Roda antes de features/score (_coerce_types).

    - Já numérico: ``pd.to_numeric(..., errors="coerce")``.
    - Texto com vírgula (formato BR, ex. ``1.549,11``): remove ``.`` (milhar), troca ``,`` por ``.``,
      depois ``to_numeric`` — equivalente à sequência pedida para esse caso.
    - Texto sem vírgula (ex. ``1549.11`` US): ``to_numeric`` direto — não remove todos os pontos
      (isso quebraria decimal americano).

    Valores inválidos permanecem NaN.
    """
    if col.empty:
        return pd.Series(dtype="float64")

    if pd.api.types.is_numeric_dtype(col):
        return pd.to_numeric(col, errors="coerce")

    ss = col.astype(str).str.strip()
    ss = ss.replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})
    empty = ss.eq("")
    has_comma = ss.str.contains(",", na=False)

    out = pd.Series(np.nan, index=col.index, dtype="float64")
    br_mask = has_comma & ~empty
    us_mask = ~has_comma & ~empty

    if br_mask.any():
        br_clean = (
            ss.loc[br_mask]
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        out.loc[br_mask] = pd.to_numeric(br_clean, errors="coerce")

    if us_mask.any():
        out.loc[us_mask] = pd.to_numeric(ss.loc[us_mask], errors="coerce")

    return out


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("DATA_SOLICITACAO", "DATA_PAGAMENTO", "DATA_CRIACAO_PLACE_E_ORG"):
        if col in df.columns:
            df[col] = _parse_timestamp_column(df[col])
    if "VALOR_RETIRADA" in df.columns:
        df["VALOR_RETIRADA"] = _parse_valor_retirada_series(df["VALOR_RETIRADA"])
    fill_data_criacao_place_e_org(df)
    return df


def date_column_for_period_filter(df: pd.DataFrame) -> str:
    """
    Coluna civil para filtros de período, recorte do dashboard e janela de histórico na ingestão:
    **DATA_SOLICITACAO** (data do pedido) sempre que existir na base — inclusive se houver
    células vazias (o recorte usa só linhas com data válida).

    Fallback **DATA_PAGAMENTO** só quando não houver coluna de solicitação.

    Legado / exceção: `ZIG_PERIOD_DATE_COLUMN=DATA_PAGAMENTO` no .env força pagamento.
    """
    import os

    override = (os.getenv("ZIG_PERIOD_DATE_COLUMN") or "").strip().upper().replace(" ", "_")
    if override in ("DATA_SOLICITACAO", "DATA_PAGAMENTO") and override in df.columns:
        return override
    if "DATA_SOLICITACAO" in df.columns:
        return "DATA_SOLICITACAO"
    return "DATA_PAGAMENTO" if "DATA_PAGAMENTO" in df.columns else "DATA_SOLICITACAO"


def filter_by_brazil_calendar_dates(
    df: pd.DataFrame,
    date_col: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """
    Mantém linhas cujo dia civil em America/Sao_Paulo da coluna informada está
    em [start, end]. Evita subcontagem quando os instantes estão em UTC no
    DataFrame e o analista filtra por dia no Brasil.
    """
    if date_col not in df.columns or df.empty:
        return df.copy()
    col = df[date_col]
    if pd.api.types.is_datetime64_any_dtype(col):
        if getattr(col.dt, "tz", None) is not None:
            s = col.dt.tz_convert("UTC")
        else:
            s = _parse_timestamp_column(col)
    else:
        s = _parse_timestamp_column(col)
    br_dates = s.dt.tz_convert("America/Sao_Paulo").dt.date
    mask = s.notna() & (br_dates >= start) & (br_dates <= end)
    return df.loc[mask].copy()


def validate_schema(df: pd.DataFrame) -> tuple[bool, list[str]]:
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    return (len(missing) == 0, missing)


def _get_historico_date_range() -> tuple[pd.Timestamp, pd.Timestamp]:
    """Retorna (data_inicio, data_fim) do período de histórico (lido do .env ou padrão). Data fim vazia = hoje."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    import os
    inicio = os.getenv("DATA_INICIO_HISTORICO", DEFAULT_DATA_INICIO).strip()
    fim = os.getenv("DATA_FIM_HISTORICO", "").strip()
    if not fim:
        fim_ts = pd.Timestamp.now().normalize()
    else:
        fim_ts = pd.to_datetime(fim).normalize()
    return pd.to_datetime(inicio).normalize(), fim_ts


def _dedupe_by_id_saque(df: pd.DataFrame, log_prefix: str) -> pd.DataFrame:
    """Uma linha por ID_SAQUE (keep=first), com log opcional."""
    if "ID_SAQUE" not in df.columns:
        return df
    print(f"\n{log_prefix} Removendo duplicidade...")
    antes = len(df)
    out = df.drop_duplicates(subset=["ID_SAQUE"], keep="first")
    depois = len(out)
    print(f"{log_prefix} Antes: {antes} | Depois: {depois}")
    return out


def _apply_historico_filter(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filtra o DataFrame pelo período de histórico (mesma coluna que o dashboard — em geral
    DATA_SOLICITACAO; ver date_column_for_period_filter). Dia civil em America/Sao_Paulo.
    """
    date_key = date_column_for_period_filter(df)
    if date_key not in df.columns or df.empty:
        return df
    data_inicio, data_fim = _get_historico_date_range()
    start_d = data_inicio.date()
    end_d = data_fim.date()
    return filter_by_brazil_calendar_dates(df, date_key, start_d, end_d)


def save_uploaded_file(uploaded_file) -> tuple[bool, str]:
    UPLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)
    filename = uploaded_file.name.lower()

    if filename.endswith(".csv"):
        df = _read_saques_csv(uploaded_file)
    elif filename.endswith(".xlsx"):
        df = pd.read_excel(uploaded_file)
    else:
        return False, "Formato invalido. Envie CSV ou XLSX."

    df = _normalize_columns(df)
    ok, missing = validate_schema(df)
    if not ok:
        return False, f"Colunas ausentes: {', '.join(missing)}"

    df = _coerce_types(df)
    df.to_csv(UPLOAD_PATH, index=False, encoding="utf-8")
    return True, f"Arquivo salvo em {UPLOAD_PATH.as_posix()}"


def _load_env_paths() -> None:
    """Carrega .env de vários caminhos para garantir que seja encontrado."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
        base = Path(__file__).resolve()
        for dir_path in [base.parents[1], base.parents[2] / "zig_risk_monitor", Path.cwd(), Path.cwd() / "zig_risk_monitor"]:
            env_file = dir_path / ".env"
            if env_file.exists():
                load_dotenv(env_file)
                break
    except ImportError:
        pass


def _read_env_file() -> dict[str, str]:
    """Lê o .env da pasta zig_risk_monitor e retorna dict com chaves em UPPER (fallback se load_dotenv falhar)."""
    out: dict[str, str] = {}
    base = Path(__file__).resolve()
    for dir_path in [base.parents[1], base.parents[2] / "zig_risk_monitor", Path.cwd(), Path.cwd() / "zig_risk_monitor"]:
        env_file = dir_path / ".env"
        if not env_file.exists():
            continue
        try:
            text = env_file.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip().upper()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
        break
    return out


def is_metabase_configured() -> bool:
    """Retorna True se METABASE_URL e METABASE_CARD_ID estiverem definidos no .env."""
    import os
    _load_env_paths()
    url = (os.getenv("METABASE_URL") or "").strip()
    card = (os.getenv("METABASE_CARD_ID") or "").strip()
    if url and card:
        return True
    env = _read_env_file()
    return bool(env.get("METABASE_URL") and env.get("METABASE_CARD_ID"))


def load_raw_data(path: Optional[Path] = None, use_metabase: bool = True) -> pd.DataFrame:
    """
    Carrega dados: se use_metabase=True e Metabase estiver configurado no .env,
    busca em tempo real do Metabase; caso contrário usa CSV de data/uploaded/saques.csv.

    Se no .env existir ZIG_LOAD_FROM_CSV_ONLY=1 (ou true/yes/on), o Metabase é ignorado
    e só se usa o CSV (útil quando a API do Metabase limita linhas ~2000 e você colocou
    a base completa em saques.csv).
    """
    import os

    if (os.getenv("ZIG_LOAD_FROM_CSV_ONLY") or "").strip().lower() in ("1", "true", "yes", "on"):
        use_metabase = False

    if use_metabase and is_metabase_configured():
        df, err = load_from_metabase()
        if err:
            # Metabase falhou; fallback para CSV se existir
            pass
        elif not df.empty:
            df = _normalize_columns(df)
            df = _coerce_types(df)
            df = _apply_historico_filter(df)
            df = _enrich_place_columns(df)
            return _dedupe_by_id_saque(df, "[LOAD]")
    source = path or UPLOAD_PATH
    if not source.exists():
        return pd.DataFrame()
    df = _read_saques_csv(source)
    df = _normalize_columns(df)
    df = _coerce_types(df)
    df = _apply_historico_filter(df)
    df = _enrich_place_columns(df)
    return _dedupe_by_id_saque(df, "[LOAD]")


def read_processed_from_disk(path: Optional[Path] = None) -> pd.DataFrame:
    """
    Lê e normaliza o CSV processado (sem memo global).
    Usado pelo dashboard com @st.cache_data e por load_processed_data().
    """
    p = path or PROCESSED_PATH
    if not p.exists():
        return pd.DataFrame()

    df = _read_saques_csv(p)
    df = _normalize_columns(df)
    df = _coerce_types(df)
    df = _enrich_place_columns(df)

    if "ID_SAQUE" in df.columns:
        antes = len(df)
        df = df.drop_duplicates(subset=["ID_SAQUE"], keep="first")
        if antes != len(df):
            print(f"[PROCESSED] Unicidade ID_SAQUE: {antes} → {len(df)}")

    if not df.empty:
        from modules.risk_engine import sync_suspeita_from_flags

        df = sync_suspeita_from_flags(df)

    return df


def save_processed_data(df: pd.DataFrame) -> None:
    import os

    global _LOAD_PROCESSED_MEMO
    _LOAD_PROCESSED_MEMO = None

    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROCESSED_PATH.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(str(tmp), str(PROCESSED_PATH))


def load_processed_data() -> pd.DataFrame:
    global _LOAD_PROCESSED_MEMO

    if not PROCESSED_PATH.exists():
        return pd.DataFrame()

    try:
        mtime = PROCESSED_PATH.stat().st_mtime
    except OSError:
        return pd.DataFrame()

    if _LOAD_PROCESSED_MEMO is not None and _LOAD_PROCESSED_MEMO[0] == mtime:
        return _LOAD_PROCESSED_MEMO[1].copy()

    print("\nCARREGANDO BASE PROCESSADA...")
    df = read_processed_from_disk(PROCESSED_PATH)
    _LOAD_PROCESSED_MEMO = (mtime, df)
    return df.copy()


def run_metabase_ingestion() -> tuple[bool, int, str]:
    from modules.feature_engineering import add_features
    from modules.risk_engine import calcular_score

    print("=" * 100)
    print("INICIANDO INGESTÃO METABASE")
    print("=" * 100)

    df, err = load_from_metabase()

    if err:
        print(f"ERRO AO BUSCAR METABASE: {err}")
        return False, 0, err

    if df.empty:
        print("ERRO: DataFrame vazio")
        return False, 0, "Nenhum dado retornado pelo Metabase."

    print(f"TOTAL LINHAS BRUTAS: {len(df)}")

    if "ID_SAQUE" in df.columns:
        print(f"SAQUES ÚNICOS (ANTES): {df['ID_SAQUE'].nunique()}")

    df = _normalize_columns(df)
    df = _coerce_types(df)
    df = _apply_historico_filter(df)
    df = _enrich_place_columns(df)

    if "ID_SAQUE" in df.columns:
        print("\nREMOVENDO DUPLICIDADES POR ID_SAQUE...")
        sort_col = "DATA_SOLICITACAO" if "DATA_SOLICITACAO" in df.columns else date_column_for_period_filter(df)
        if sort_col in df.columns:
            df = df.sort_values(sort_col, ascending=False, na_position="last")
        antes = len(df)
        df = df.drop_duplicates(subset=["ID_SAQUE"], keep="first")
        depois = len(df)
        print(f"ANTES: {antes}")
        print(f"DEPOIS: {depois}")
        print(f"REMOVIDOS: {antes - depois}")
        print(f"SAQUES ÚNICOS (DEPOIS): {df['ID_SAQUE'].nunique()}")
    else:
        print("ATENÇÃO: coluna ID_SAQUE não encontrada!")

    print("\nAPLICANDO FEATURE ENGINEERING...")
    df = add_features(df)

    print("CALCULANDO SCORE DE RISCO...")
    df = calcular_score(df)

    if "ID_SAQUE" in df.columns:
        print("\nVALIDAÇÃO FINAL:")
        print(f"LINHAS FINAIS: {len(df)}")
        print(f"SAQUES ÚNICOS FINAL: {df['ID_SAQUE'].nunique()}")

    save_processed_data(df)

    print("\nINGESTÃO FINALIZADA COM SUCESSO")
    print("=" * 100)

    return True, len(df), "Ingestão executada com sucesso."
