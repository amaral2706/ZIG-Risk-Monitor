import streamlit as st
from supabase import create_client

SUPABASE_URL = "https://ocftulnrxkclqnwvvied.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9jZnR1bG5yeGtjbHFud3Z2aWVkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQ1NDcwNTYsImV4cCI6MjA5MDEyMzA1Nn0.f3icJTRPw-04dCHnbn0vVFmoJf6CKdtAXrAJx61j2_A"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def ensure_authenticated():

    if "user" not in st.session_state:
        st.session_state.user = None

    if st.session_state.user:
        return True

    st.title("🔐 Acesso ao Sistema")

    opcao = st.radio("Escolha uma opção:", ["Login", "Criar conta"])

    email = st.text_input("Email")
    senha = st.text_input("Senha", type="password")

    if opcao == "Login":

        if st.button("Entrar"):

            try:
                res = supabase.auth.sign_in_with_password({
                    "email": email,
                    "password": senha
                })

                if res.user:
                    st.session_state.user = res.user
                    st.rerun()
                else:
                    st.error("Email ou senha inválidos")

            except Exception as e:
                st.error(f"Erro no login: {e}")

    else:

        if st.button("Criar conta"):

            try:
                supabase.auth.sign_up({
                    "email": email,
                    "password": senha
                })
                st.success("Conta criada! Verifique seu email.")

            except Exception as e:
                st.error(f"Erro ao cadastrar: {e}")

    return False
