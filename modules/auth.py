import streamlit as st
from supabase import create_client

# =========================
# CONFIG SUPABASE
# =========================

SUPABASE_URL = "https://ocftulnrxkclqnwvvied.supabase.co"
SUPABASE_KEY = "sb_secret_hSGW5AH-85S4wYsTCM3Xpg_4ifxf5Dp"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =========================
# LOGIN / CADASTRO
# =========================

def ensure_authenticated():

    if "user" not in st.session_state:
        st.session_state.user = None

    # Se já estiver logado
    if st.session_state.user:
        return True

    opcao = st.radio("Acesso", ["Login", "Criar conta"])

    # ================= LOGIN =================
    if opcao == "Login":

        st.title("Login")

        email = st.text_input("Email")
        senha = st.text_input("Senha", type="password")

        if st.button("Entrar"):

            try:
                res = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": senha
                })

                if res.user:
                    st.session_state.user = res.user
                    st.success("Login realizado com sucesso")
                    st.rerun()

            except Exception as e:
                st.error("Email ou senha inválidos")

    # ================= CADASTRO =================
    else:

        st.title("Criar conta")

        email = st.text_input("Email")
        senha = st.text_input("Senha", type="password")

        if st.button("Cadastrar"):

            try:
                res = supabase.auth.sign_up({
                    "email": email,
                    "password": senha
                })

                if res.user:
                    st.success("Conta criada! Verifique seu email")

            except Exception as e:
                st.error("Erro ao criar conta")

    return False
