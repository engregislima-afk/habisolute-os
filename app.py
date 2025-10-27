import io
import json
import secrets
import hashlib
import hmac
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

import streamlit as st

# Paths
BASEDIR = Path(__file__).resolve().parent
PREFSDIR = BASEDIR / ".habos"
PREFSDIR.mkdir(parents=True, exist_ok=True)
USERSDB = PREFSDIR / "users.json"
PERMSDB = PREFSDIR / "perms.json"
PREFSPATH = PREFSDIR / "prefs.json"
AUDITLOG = PREFSDIR / "audit.jsonl"

SYSTEMCODE = "habos"

# -- UTILS: Password hashing with PBKDF2-HMAC-SHA256 --

def hash_password(password: str, salt_hex: Optional[str] = None) -> (str, str):
    """Generate salted PBKDF2-HMAC-SHA256 password hash."""
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 120000)
    return salt.hex(), pw_hash.hex()

def verify_password(password: str, salt_hex: str, pw_hash_hex: str) -> bool:
    """Verify password against stored salt and hash securely."""
    salt = bytes.fromhex(salt_hex)
    expected_hash = bytes.fromhex(pw_hash_hex)
    test_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 120000)
    return hmac.compare_digest(test_hash, expected_hash)

# -- USER MANAGEMENT --

def load_users() -> Dict[str, Dict]:
    """Load user database from JSON or initialize admin user."""
    if USERSDB.exists():
        try:
            data = json.loads(USERSDB.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or 'users' not in data:
                data = bootstrap_admin_users()
                save_users(data)
            return data
        except Exception:
            # Fail-safe: create default admin user
            data = bootstrap_admin_users()
            save_users(data)
            return data
    else:
        data = bootstrap_admin_users()
        save_users(data)
        return data

def save_users(data: Dict[str, Dict]) -> None:
    tmp = USERSDB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(USERSDB)

def bootstrap_admin_users() -> Dict[str, Dict]:
    """Create default admin user with mandatory password change."""
    admin_salt, admin_hash = hash_password("1234")
    db = {'users': {
        'admin': {
            'password': admin_hash,
            'salt': admin_salt,
            'isadmin': True,
            'active': True,
            'mustchange': True,
            'role': 'admin',
            'createdat': datetime.now().isoformat()
        }
    }}
    return db

def user_exists(username: str) -> bool:
    users = load_users()
    return username in users.get('users', {})

def get_user(username: str) -> Optional[Dict]:
    users = load_users()
    return users.get('users', {}).get(username)

def set_user(username: str, record: Dict) -> None:
    db = load_users()
    db.setdefault('users', {})[username] = record
    save_users(db)

def verify_user_password(username: str, password: str) -> bool:
    user = get_user(username)
    if user and user.get('active'):
        return verify_password(password, user['salt'], user['password'])
    return False

# -- SESSION STATE INIT --

def init_session_state():
    st.session_state.setdefault('loggedin', False)
    st.session_state.setdefault('username', None)
    st.session_state.setdefault('isadmin', False)
    st.session_state.setdefault('role', 'usuario')
    st.session_state.setdefault('mustchange', False)
    st.session_state.setdefault('thememode', 'Claro')
    st.session_state.setdefault('flash', [])

# -- FLASH MESSAGING --

def flash_message(kind: str, text: str):
    st.session_state['flash'].append({'kind': kind, 'text': text})

def render_flashes():
    for m in st.session_state.get('flash', []):
        if m['kind'] == 'error':
            st.error(m['text'])
        elif m['kind'] == 'warn':
            st.warning(m['text'])
        elif m['kind'] == 'success':
            st.success(m['text'])
        else:
            st.info(m['text'])
    st.session_state['flash'] = []

# -- AUTHENTICATION UI --

def login_ui():
    st.header("Login")
    user = st.text_input("Usuário")
    pwd = st.text_input("Senha", type="password")
    if st.button("Acessar"):
        rec = get_user(user)
        if not rec or not rec.get('active', False):
            flash_message('error', "Usuário inexistente ou inativo.")
        elif not verify_user_password(user, pwd):
            flash_message('error', "Senha incorreta.")
        else:
            st.session_state.loggedin = True
            st.session_state.username = user
            st.session_state.isadmin = bool(rec.get('isadmin', False))
            st.session_state.role = rec.get('role', 'usuario')
            st.session_state.mustchange = rec.get('mustchange', False)
            flash_message('success', f"Bem-vindo, {user}!")
            st.experimental_rerun()
    render_flashes()

def force_change_password_ui(username: str):
    st.header("Trocar senha")
    new_pwd = st.text_input("Nova senha", type="password")
    confirm_pwd = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha"):
        if len(new_pwd) < 4:
            flash_message('warn', "Use ao menos 4 caracteres.")
        elif new_pwd != confirm_pwd:
            flash_message('error', "As senhas não conferem.")
        else:
            rec = get_user(username)
            salt, pw_hash = hash_password(new_pwd)
            rec['salt'] = salt
            rec['password'] = pw_hash
            rec['mustchange'] = False
            set_user(username, rec)
            flash_message('success', "Senha atualizada! Faça login novamente.")
            st.session_state.mustchange = False
            st.session_state.loggedin = False
            st.experimental_rerun()
    render_flashes()

# -- MAIN APP --

def main():
    init_session_state()

    if not st.session_state.loggedin:
        login_ui()
        st.stop()

    if st.session_state.mustchange:
        force_change_password_ui(st.session_state.username)
        st.stop()

    st.sidebar.title("Menu")
    st.sidebar.write(f"Olá, {st.session_state.username} - {st.session_state.role.capitalize()}")

    if st.sidebar.button("Sair"):
        st.session_state.loggedin = False
        flash_message('info', "Sessão encerrada.")
        st.experimental_rerun()

    st.write("Conteúdo principal do sistema aqui...")

if __name__ == "__main__":
    main()
