import streamlit as st
import json
import os

USERS_FILE = "usuarios.json"

def carregar_usuarios():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def salvar_usuarios(usuarios):
    with open(USERS_FILE, "w") as f:
        json.dump(usuarios, f)

def criar_usuario(nome, email, senha):
    usuarios = carregar_usuarios()
    if email in usuarios:
        return False
    usuarios[email] = {"nome": nome, "senha": senha}
    salvar_usuarios(usuarios)
    return True

def autenticar(email, senha):
    usuarios = carregar_usuarios()
    return email in usuarios and usuarios[email]["senha"] == senha


def ensure_authenticated():

    if "logado" not in st.session_state:
        st.session_state.logado = False

    if st.session_state.logado:
        return True

    opcao = st.radio("Acesso", ["Login", "Criar conta"])

    if opcao == "Login":
        st.title("Login")

        email = st.text_input("Email")
        senha = st.text_input("Senha", type="password")

        if st.button("Entrar"):
            if autenticar(email, senha):
                st.session_state.logado = True
                st.rerun()
            else:
                st.error("Email ou senha inválidos")

    else:
        st.title("Criar conta")

        nome = st.text_input("Nome")
        email = st.text_input("Email")
        senha = st.text_input("Senha", type="password")

        if st.button("Cadastrar"):
            if criar_usuario(nome, email, senha):
                st.success("Conta criada com sucesso")
            else:
                st.error("Email já cadastrado")

    return False
