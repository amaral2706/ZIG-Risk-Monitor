from pathlib import Path
import sys

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from modules.auth import is_admin, login_form
from modules.ui import apply_enterprise_theme, aplicar_estilo_global


def _ensure_runtime_folders() -> None:
    for directory in [Path("data/uploaded"), Path("data/processed"), Path("logs")]:
        directory.mkdir(parents=True, exist_ok=True)


def _section_title(title: str) -> None:
    st.markdown(f'<p class="rn-section-title">{title}</p>', unsafe_allow_html=True)


def _card(content: str) -> None:
    st.markdown(f'<div class="rn-card">{content}</div>', unsafe_allow_html=True)


st.set_page_config(
    page_title="Regra de Negócio | ZIG Risk Monitor",
    page_icon=":shield:",
    layout="wide",
    initial_sidebar_state="expanded",
)

_ensure_runtime_folders()
apply_enterprise_theme()
aplicar_estilo_global()
st.markdown(
    """
    <style>
    .rn-header-title { font-size: 28px; font-weight: 700; color: #0047BB; margin-bottom: 0.2rem; }
    .rn-header-sub { color: #5F6A72; font-size: 0.98rem; margin-bottom: 1.2rem; }
    .rn-section-title { font-size: 1.15rem; font-weight: 700; color: #0047BB; margin: 1.25rem 0 0.5rem 0; padding-bottom: 4px; border-bottom: 2px solid #E3E8EE; }
    .rn-card { background: #FFFFFF; border: 1px solid #E3E8EE; border-radius: 10px; padding: 18px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0, 71, 187, 0.08); }
    .rn-card h4 { margin: 0 0 8px 0; font-size: 18px; font-weight: 600; color: #0047BB; }
    .rn-card ul { margin: 0; padding-left: 1.1rem; }
    .rn-access-block { background: #FFFFFF; border: 1px solid #E3E8EE; border-radius: 10px; padding: 16px; margin-bottom: 12px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="rn-header-title">Regra de Negócio</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="rn-header-sub">Regras utilizadas pelo modelo de detecção de fraude para análise de risco. Linguagem objetiva para uso pelos analistas.</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 1. Regras de Score de Risco
# ---------------------------------------------------------------------------
_section_title("1. Regras de Score de Risco")

_card(
    """
    <p style="margin:0 0 8px 0;">Cada transação recebe pontos conforme critérios comportamentais baseados no padrão da base:</p>
    <ul>
        <li><strong>Valor elevado</strong> → Valor acima da média do place × 3 ou acima do percentil 90 (+30)</li>
        <li><strong>Saque noturno</strong> → Transações entre 22h e 06h (+25)</li>
        <li><strong>Destinatário recorrente</strong> → Mesma conta recebendo múltiplas transações (+20)</li>
        <li><strong>Alta frequência</strong> → Mais de 3 transações em curto intervalo (+15)</li>
        <li><strong>Conta concentradora</strong> → Conta recebendo valores de múltiplos remetentes (+20)</li>
        <li><strong>Concentração financeira</strong> → Conta recebendo mais de 20% do volume total (+25)</li>
    </ul>
    """
)

# ---------------------------------------------------------------------------
# 2. Classificação de Risco
# ---------------------------------------------------------------------------
_section_title("2. Classificação de Risco")

_card(
    """
    <h4>Faixas do score</h4>
    <p style="margin:0 0 8px 0;">O nível de risco é definido pela faixa de score:</p>
    <ul>
        <li><strong>0–29</strong> → Baixo risco (Liberar)</li>
        <li><strong>30–59</strong> → Médio risco (Monitorar)</li>
        <li><strong>60–79</strong> → Alto risco (Revisão manual)</li>
        <li><strong>80+</strong> → Risco crítico (Bloqueio automático)</li>
    </ul>
    """
)

# ---------------------------------------------------------------------------
# 3. Regra de Transação Suspeita
# ---------------------------------------------------------------------------
_section_title("3. Regra de Transação Suspeita")

_card(
    """
    <h4>Quando uma transação é considerada suspeita</h4>
    <p style="margin:0 0 8px 0;">Uma transação é considerada suspeita quando:</p>
    <ul>
        <li><strong>Valor elevado</strong></li>
        <li><strong>E</strong></li>
        <li><strong>Saque noturno</strong> OU <strong>destinatário recorrente</strong></li>
    </ul>
    <p style="margin:8px 0 0 0;">Essas transações devem ser priorizadas para bloqueio ou investigação imediata.</p>
    <p style="margin:10px 0 0 0; font-size:0.95rem; color:#5F6A72;">
    <strong>Implementação no motor:</strong> <code>VALOR_ALTO</code> (valor &gt; R$ 3.000),
    <code>SAQUE_NOTURNO</code> (hora 0h–5h), <code>DESTINATARIO_REPETIDO</code> (conta recebedora com mais de um saque no recorte).
    A flag <code>SUSPEITA</code> não usa mais apenas o score ≥ 61.</p>
    """
)

# ---------------------------------------------------------------------------
# 4. Regras de Monitoramento Comportamental
# ---------------------------------------------------------------------------
_section_title("4. Regras de Monitoramento Comportamental")

_card(
    """
    <h4>Indicadores analisados</h4>
    <ul>
        <li><strong>Saque noturno</strong> → maior exposição a fraude</li>
        <li>Valores acima do padrão do place</li>
        <li>Repetição de destinatários</li>
        <li>Alta frequência de transações</li>
        <li>Concentração de valores em poucas contas</li>
    </ul>
    """
)

# ---------------------------------------------------------------------------
# 5. Regras de Rede de Transações
# ---------------------------------------------------------------------------
_section_title("5. Regras de Rede de Transações")

_card(
    """
    <h4>Classificação das contas</h4>
    <ul>
        <li><strong>Normal</strong> → comportamento distribuído</li>
        <li><strong>Intermediário</strong> → múltiplos remetentes</li>
        <li><strong>Conta concentradora</strong> → alto volume e concentração</li>
    </ul>
    <p style="margin:8px 0 0 0;">Contas concentradoras devem ser priorizadas para análise.</p>
    """
)

# ---------------------------------------------------------------------------
# 6. Regras de Clusters Suspeitos
# ---------------------------------------------------------------------------
_section_title("6. Regras de Clusters Suspeitos")

_card(
    """
    <h4>Classificação dos clusters</h4>
    <ul>
        <li><strong>Alto risco</strong> → múltiplas contas interligadas com alto volume</li>
        <li><strong>Médio risco</strong> → padrão intermediário</li>
        <li><strong>Baixo risco</strong> → comportamento distribuído</li>
    </ul>
    <p style="margin:8px 0 0 0;">Clusters indicam possíveis redes de fraude.</p>
    """
)

# ---------------------------------------------------------------------------
# 7. Alertas de Risco
# ---------------------------------------------------------------------------
_section_title("7. Alertas de Risco")

_card(
    """
    <h4>Gatilhos automáticos</h4>
    <ul>
        <li>Aumento abrupto de volume</li>
        <li>Concentração financeira em uma conta</li>
        <li>Atividade intensa em horário noturno</li>
        <li>Alta frequência em curto período</li>
    </ul>
    """
)

# ---------------------------------------------------------------------------
# Access block
# ---------------------------------------------------------------------------
_section_title("Acesso ao sistema")
if "username" not in st.session_state:
    login_form()
else:
    st.markdown('<div class="rn-access-block">', unsafe_allow_html=True)
    st.markdown("**Usuário autenticado**")
    role = "Administrador" if is_admin(st.session_state["username"]) else "Usuário"
    shown_user = st.session_state.get("username_display", st.session_state["username"])
    col_user, col_btn = st.columns([4, 1])
    with col_user:
        st.markdown(f"**Usuário:** {shown_user}")
        st.markdown(f"**Perfil:** {role}")
    with col_btn:
        if st.button("Sair", width="stretch", key="rn_logout"):
            st.session_state.pop("username", None)
            st.session_state.pop("username_display", None)
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
