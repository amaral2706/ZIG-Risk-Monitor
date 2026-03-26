import streamlit as st

ADMIN_USERS = {
    "bruna",
    "bruna pinto",
    "bruna amaral",
    "bruna.amaral",
    "brunaamaral",
}

def normalize_username(username: str) -> str:
    return " ".join(username.strip().lower().split())

def is_admin(username: str) -> bool:
    normalized = normalize_username(username)
    if normalized in ADMIN_USERS:
        return True
    return normalized.startswith("bruna ") or normalized.startswith("bruna.")

def login_form() -> None:
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Usuario", placeholder="Digite seu usuario (ex.: bruna)")
        submitted = st.form_submit_button("Entrar")
        if submitted:
            if not username.strip():
                st.error("Informe um usuario valido.")
                return
            st.session_state["username"] = normalize_username(username)
            st.session_state["username_display"] = username.strip()
            st.rerun()

def ensure_authenticated():
    if "username" not in st.session_state:
        st.warning("Faça login para acessar esta página.")
        st.stop()

