"""
Cliente Metabase para extração de dados em tempo real.
Usa sessão (username/password) ou API key quando configurada.
"""
import io
import logging
import unicodedata
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

# Timeouts padrão (segundos). Sobrescreva no .env: METABASE_QUERY_TIMEOUT, METABASE_CSV_TIMEOUT
# Consultas pesadas (Snowflake via Metabase) costumam passar de 120s.
_DEFAULT_QUERY = 300
_DEFAULT_CSV = 120
_DEFAULT_REQUEST = 30


def _query_timeout() -> float:
    import os
    try:
        v = float((os.getenv("METABASE_QUERY_TIMEOUT") or str(_DEFAULT_QUERY)).strip())
        return max(15.0, min(v, 1200.0))
    except ValueError:
        return float(_DEFAULT_QUERY)


def _csv_timeout() -> float:
    import os
    try:
        v = float((os.getenv("METABASE_CSV_TIMEOUT") or str(_DEFAULT_CSV)).strip())
        return max(10.0, min(v, 1200.0))
    except ValueError:
        return float(_DEFAULT_CSV)


def _request_timeout() -> float:
    import os
    try:
        v = float((os.getenv("METABASE_REQUEST_TIMEOUT") or str(_DEFAULT_REQUEST)).strip())
        return max(5.0, min(v, 120.0))
    except ValueError:
        return float(_DEFAULT_REQUEST)


def _timeout_connect_read(read_seconds: float) -> tuple[float, float]:
    """(connect, read) para o requests não ficar preso em handshake/TCP."""
    return (10.0, float(read_seconds))

# Mapeamento: nome retornado pelo Metabase -> nome normalizado (uppercase, underscores)
# Inclui colunas da query RFO (card 7973) e RFD
COLUMN_ALIASES = {
    "id_saque": "ID_SAQUE",
    "id_request": "ID_SAQUE",
    "id_place": "ID_PLACE",
    "data_solicitacao": "DATA_SOLICITACAO",
    "data_pagamento": "DATA_PAGAMENTO",
    "nome_place": "NOME_PLACE",
    "cnpj": "CNPJ",
    "nome_organizacao": "NOME_ORGANIZACAO",
    "id_organizacao": "ID_ORGANIZACAO",
    "cidade": "CIDADE",
    "metodo_pagamento_especifico": "METODO_PAGAMENTO_ESPECIFICO",
    "metodo_pagamento_solicita": "METODO_PAGAMENTO_ESPECIFICO",
    "valor_retirada": "VALOR_RETIRADA",
    "status": "STATUS",
    "receiver": "RECEIVER",
    "motivo": "MOTIVO",
    "info_boleto": "INFO_BOLETO",
    "tipo_chave_pix": "TIPO_CHAVE_PIX",
    "valor_chave_pix": "VALOR_CHAVE_PIX",
    "banco_recebedor": "BANCO_RECEBEDOR",
    "agencia_recebedor": "AGENCIA_RECEBEDOR",
    "conta_recebedor": "CONTA_RECEBEDOR",
    "data_criacao_place_e_org": "DATA_CRIACAO_PLACE_E_ORG",
    # Metabase às vezes exibe só "data_criacao" no cabeçalho
    "data_criacao": "DATA_CRIACAO_PLACE_E_ORG",
}


def _col_lookup_key(name: str) -> str:
    """Chave estável para alias: minúsculas, underscores, sem acentos."""
    s = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _normalize_col_name(name: str) -> str:
    """Normaliza nome de coluna para o padrão do sistema (UPPER, underscores)."""
    if not name:
        return name
    s = str(name).strip()
    key = _col_lookup_key(s)
    return COLUMN_ALIASES.get(key, key.upper())


def _make_unique_column_names(names: list[str]) -> list[str]:
    """Evita colunas duplicadas após normalização (pandas / Excel)."""
    counts: dict[str, int] = {}
    out: list[str] = []
    for c in names:
        n = counts.get(c, 0)
        counts[c] = n + 1
        out.append(c if n == 0 else f"{c}__{n}")
    return out


def _read_env_file() -> dict[str, str]:
    """Lê .env da pasta do app e retorna dict (fallback quando load_dotenv não carrega no Streamlit)."""
    out: dict[str, str] = {}
    base = Path(__file__).resolve()
    for d in [base.parents[1], base.parents[2] / "zig_risk_monitor", Path.cwd(), Path.cwd() / "zig_risk_monitor"]:
        f = d / ".env"
        if not f.exists():
            continue
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip().upper()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
        break
    return out


def _get_env() -> dict[str, str]:
    """Carrega variáveis do .env (load_dotenv + leitura direta do arquivo como fallback)."""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv()
        base = Path(__file__).resolve()
        for d in [base.parents[1], base.parents[2] / "zig_risk_monitor", Path.cwd(), Path.cwd() / "zig_risk_monitor"]:
            f = d / ".env"
            if f.exists():
                load_dotenv(f)
                break
    except ImportError:
        pass
    env_file = _read_env_file()
    def _get(key: str, default: str = "") -> str:
        v = (os.getenv(key) or env_file.get(key, default) or "").strip()
        return v if v else default
    return {
        "url": _get("METABASE_URL").rstrip("/"),
        "api_key": _get("METABASE_API_KEY"),
        "username": _get("METABASE_USERNAME"),
        "password": _get("METABASE_PASSWORD"),
        "database_id": _get("METABASE_DATABASE_ID"),
        "card_id": _get("METABASE_CARD_ID"),
        "query_path": _get("METABASE_QUERY_PATH"),
    }


def _session_login(base_url: str, username: str, password: str) -> Optional[str]:
    """Faz login no Metabase e retorna o session ID."""
    if not base_url or not username or not password:
        return None
    try:
        r = requests.post(
            f"{base_url}/api/session",
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json"},
            timeout=_timeout_connect_read(_request_timeout()),
        )
        r.raise_for_status()
        data = r.json()
        return data.get("id")
    except Exception:
        return None


def _get_card_parameters(
    base_url: str,
    card_id: str,
    *,
    api_key: Optional[str] = None,
    session_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Obtém os parâmetros da card (GET /api/card/:id) e retorna uma lista
    com valores: text/outros vazios; date_start/date_end com intervalo 01/01/2025 até hoje
    para a query (ex. card 7973 com [[date_start]] e [[date_end]]) retornar dados.
    """
    import os
    from datetime import date
    if not base_url or not card_id:
        return []
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    elif session_id:
        headers["X-Metabase-Session"] = session_id
    else:
        return []
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    start_str = os.getenv("DATA_INICIO_HISTORICO", "2025-01-01").strip()
    end_date = date.today()
    end_str = end_date.strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{base_url}/api/card/{card_id}",
            headers=headers,
            timeout=_timeout_connect_read(_request_timeout()),
        )
        r.raise_for_status()
        card = r.json()
        params = card.get("parameters") or []
        date_params = [p for p in params if p.get("id") and "date" in ((p.get("type") or "text").lower())]
        out = []
        for p in params:
            param_id = p.get("id")
            param_type = (p.get("type") or "text").lower()
            slug = (p.get("slug") or p.get("name") or "").lower()
            if param_id is None:
                continue
            if "date" in param_type:
                # Card 7973: date_start e date_end separados, ou um único date/range
                if "range" in param_type or ("data" in slug and len(date_params) <= 1):
                    value = f"{start_str}~{end_str}"
                elif "end" in slug or "fim" in slug:
                    value = end_str
                else:
                    value = start_str
                out.append({"id": param_id, "type": p.get("type"), "value": value})
            else:
                # Text/string vazio: enviar null para não quebrar Snowflake ("Boolean value '' is not recognized")
                out.append({"id": param_id, "type": p.get("type"), "value": None})
        return out
    except Exception:
        return []


def fetch_card_data(
    base_url: str,
    card_id: str,
    *,
    api_key: Optional[str] = None,
    session_id: Optional[str] = None,
    parameters: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """
    Executa a card no Metabase e retorna o payload bruto (data.cols + data.rows).
    Autenticação: api_key (header x-api-key) ou session_id (header X-Metabase-Session).
    parameters: lista opcional para filtros da card (ex.: [{"type": "text", "value": ""}, {"type": "date", "value": null}]).
    """
    if not base_url or not card_id:
        return None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    elif session_id:
        headers["X-Metabase-Session"] = session_id
    else:
        return None
    body: dict[str, Any] = {}
    if parameters is not None:
        body["parameters"] = parameters
    try:
        # POST /api/card/:id/query — executa a pergunta salva e retorna o dataset
        r = requests.post(
            f"{base_url}/api/card/{card_id}/query",
            json=body,
            headers=headers,
            timeout=_timeout_connect_read(_query_timeout()),
        )
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        # Extrair mensagem de erro do corpo da resposta (400/500 costumam trazer detalhe)
        err_msg = f"Metabase HTTP {e.response.status_code}"
        try:
            body = e.response.json()
            if isinstance(body, dict):
                # Metabase e APIs comuns: message, error, errors, detail, cause
                detail = (
                    body.get("message")
                    or body.get("error")
                    or body.get("detail")
                    or body.get("cause")
                )
                if isinstance(body.get("errors"), dict):
                    detail = detail or str(body["errors"])[:500]
                if isinstance(body.get("errors"), list) and body["errors"]:
                    detail = detail or str(body["errors"][0])[:500]
                if detail:
                    err_msg += f": {detail}" if isinstance(detail, str) else f": {str(detail)[:300]}"
                log.warning("Metabase API error %s: %s", e.response.status_code, body)
            else:
                text = (e.response.text or "")[:500]
                if text:
                    err_msg += f": {text}"
                    log.warning("Metabase API error %s (body): %s", e.response.status_code, text)
        except Exception:
            if e.response.text:
                err_msg += f": {e.response.text[:200]}"
                log.warning("Metabase API error %s (raw): %s", e.response.status_code, e.response.text[:300])
        return {"_error": err_msg}
    except requests.exceptions.Timeout as e:
        qt = _query_timeout()
        return {
            "_error": (
                f"Tempo esgotado aguardando o Metabase (limite atual: {qt:.0f}s). "
                "A consulta no banco está lenta ou a rede está instável. "
                "No .env do zig_risk_monitor aumente METABASE_QUERY_TIMEOUT (ex.: 420 ou 600). "
                f"Detalhe técnico: {e}"
            )
        }
    except Exception as e:
        return {"_error": str(e)}


def fetch_card_query_csv(
    base_url: str,
    card_id: str,
    *,
    api_key: Optional[str] = None,
    session_id: Optional[str] = None,
    parameters: Optional[list[dict[str, Any]]] = None,
    timeout: Optional[float] = None,
) -> Optional[pd.DataFrame]:
    """
    POST /api/card/:id/query/csv — exportação costuma permitir mais linhas que o JSON da query.
    """
    if not base_url or not card_id:
        return None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    elif session_id:
        headers["X-Metabase-Session"] = session_id
    else:
        return None
    body: dict[str, Any] = {}
    if parameters is not None:
        body["parameters"] = parameters
    t = timeout if timeout is not None else _csv_timeout()
    try:
        r = requests.post(
            f"{base_url}/api/card/{card_id}/query/csv",
            json=body,
            headers=headers,
            timeout=_timeout_connect_read(t),
        )
        r.raise_for_status()
        text = r.content.decode("utf-8-sig", errors="replace")
        return pd.read_csv(io.StringIO(text), low_memory=False)
    except Exception as e:
        log.warning("Metabase CSV export falhou: %s", e)
        return None


def normalize_exported_csv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica o mesmo mapeamento de nomes usado na resposta JSON da card."""
    if df.empty:
        return df
    out = df.copy()
    out.columns = [_normalize_col_name(str(c)) for c in out.columns]
    return out


def metabase_to_dataframe(payload: dict[str, Any]) -> pd.DataFrame:
    """Converte a resposta da API Metabase (card query) em DataFrame."""
    data = payload.get("data") or {}
    cols = data.get("cols") or []
    rows = data.get("rows") or []
    if not cols or not rows:
        return pd.DataFrame()

    # Nomes das colunas: display_name ou name, normalizados
    names = []
    for c in cols:
        raw = (c.get("display_name") or c.get("name") or "").strip()
        names.append(_normalize_col_name(raw) if raw else f"COL_{len(names)}")
    names = _make_unique_column_names(names)

    ncols = len(names)
    fixed_rows: list[list[Any]] = []
    for row in rows:
        r = list(row) if row is not None else []
        if len(r) < ncols:
            r = r + [None] * (ncols - len(r))
        elif len(r) > ncols:
            r = r[:ncols]
        fixed_rows.append(r)

    return pd.DataFrame(fixed_rows, columns=names)


def load_from_metabase() -> tuple[pd.DataFrame, Optional[str]]:
    """
    Carrega dados em tempo real do Metabase (card configurada no .env).
    Retorna (DataFrame, None) em sucesso ou (DataFrame vazio, mensagem de erro).
    """
    env = _get_env()
    base_url = env["url"]
    card_id = env["card_id"]
    api_key = env["api_key"]
    username = env["username"]
    password = env["password"]

    if not base_url:
        return pd.DataFrame(), "METABASE_URL não configurado no .env"
    if not card_id:
        return pd.DataFrame(), "METABASE_CARD_ID não configurado no .env"

    session_id = None
    if not api_key and username and password:
        session_id = _session_login(base_url, username, password)
        if not session_id:
            return pd.DataFrame(), "Falha ao autenticar no Metabase (verifique METABASE_USERNAME e METABASE_PASSWORD)"
    elif not api_key:
        return pd.DataFrame(), "Configure METABASE_API_KEY ou METABASE_USERNAME/METABASE_PASSWORD no .env"

    # Tenta com parâmetros (date_start/date_end etc.) para a card retornar dados
    parameters = _get_card_parameters(base_url, card_id, api_key=api_key or None, session_id=session_id)
    payload = fetch_card_data(
        base_url, card_id,
        api_key=api_key or None,
        session_id=session_id,
        parameters=parameters if parameters else None,
    )
    if not payload:
        return pd.DataFrame(), "Falha ao executar a card no Metabase (verifique URL, card ID e rede)"
    if payload.get("_error"):
        # Se deu 400 (bad request), pode ser formato dos parâmetros; tenta sem parâmetros
        err = payload["_error"]
        if parameters and "400" in err:
            log.info("Metabase retornou 400 com parâmetros; tentando executar a card sem parâmetros.")
            payload2 = fetch_card_data(
                base_url, card_id,
                api_key=api_key or None,
                session_id=session_id,
                parameters=None,
            )
            if payload2 and not payload2.get("_error"):
                df2 = metabase_to_dataframe(payload2)
                if not df2.empty:
                    return df2, None
        return pd.DataFrame(), err

    df = metabase_to_dataframe(payload)
    # Se veio 0 linhas com parâmetros, tenta sem parâmetros (filtros opcionais omitidos)
    if df.empty and parameters:
        payload2 = fetch_card_data(
            base_url, card_id,
            api_key=api_key or None,
            session_id=session_id,
            parameters=None,
        )
        if payload2 and not payload2.get("_error"):
            df = metabase_to_dataframe(payload2)
    if df.empty:
        return df, "Nenhum dado retornado pelo Metabase. Confira a pergunta 7973 no Metabase (filtros em branco) e se date_start/date_end estão corretos."

    # O endpoint JSON costuma limitar linhas (~2000). O export CSV costuma trazer o resultado completo.
    # Por padrão: sempre tenta CSV após o JSON e fica com o DataFrame que tiver mais linhas.
    # METABASE_SKIP_CSV_EXPORT=1 desliga a chamada ao CSV.
    # METABASE_CSV_IF_ROWS_GTE=N (opcional): só tenta CSV quando len(df) >= N (evita 2º request em cards pequenas).
    import os

    skip_csv = (os.getenv("METABASE_SKIP_CSV_EXPORT") or "").strip().lower() in ("1", "true", "yes", "on")
    csv_min_rows: Optional[int] = None
    _csv_gte = (os.getenv("METABASE_CSV_IF_ROWS_GTE") or "").strip()
    if _csv_gte:
        try:
            csv_min_rows = int(_csv_gte)
        except ValueError:
            csv_min_rows = None
    if csv_min_rows is not None and csv_min_rows < 0:
        csv_min_rows = 0

    try_csv = not skip_csv
    if try_csv and csv_min_rows is not None:
        try_csv = len(df) >= csv_min_rows

    if try_csv:
        # Com parâmetros e, se houver, sem (algumas instalações respondem melhor a um dos dois).
        param_attempts: list[Optional[list[dict[str, Any]]]] = (
            [parameters, None] if parameters else [None]
        )
        best_csv: Optional[pd.DataFrame] = None
        best_n = -1
        try:
            for params in param_attempts:
                df_csv = fetch_card_query_csv(
                    base_url,
                    card_id,
                    api_key=api_key or None,
                    session_id=session_id,
                    parameters=params,
                )
                if df_csv is None or df_csv.empty:
                    continue
                df_csv = normalize_exported_csv_columns(df_csv)
                if len(df_csv) > best_n:
                    best_n = len(df_csv)
                    best_csv = df_csv
            if best_csv is not None and best_n > len(df):
                log.info(
                    "Metabase: JSON retornou %d linhas; CSV export retornou %d — usando CSV.",
                    len(df),
                    best_n,
                )
                df = best_csv
        except Exception as e:
            log.warning("Metabase CSV export: %s — mantendo dados do JSON.", e)

    return df, None
