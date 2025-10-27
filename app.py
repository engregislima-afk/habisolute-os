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
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Definir nova senha</div>', unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password")
    p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha"):
        if len(p1) < 4:
            banner_warn("Use ao menos 4 caracteres.")
        elif p1 != p2:
            banner_error("As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec['password'] = hash_password_simple(p1)
            rec['mustchange'] = False
            user_set(username, rec)
            log_event("password_changed", username=username)
            flash_success("Senha atualizada! Faça login novamente se necessário.")
            st.session_state['mustchange'] = False
            st.experimental_rerun()
            st.stop()  # PARA execução imediatamente após rerun
    st.markdown('</div>', unsafe_allow_html=True)
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
    # =============================================================================
# Permissões
# =============================================================================
DEFAULT_PERMS = {
    "roles": {
        "usuario":   ["dashboard_view"],
        "gestor":    ["dashboard_view","os_create","os_edit","os_view"],
        "diretoria": ["dashboard_view","os_view","auditoria_view","relatorios_export"],
        "admin":     ["*"]
    },
    "overrides": {}
}

def _load_perms() -> Dict[str, Any]:
    if PERMS_DB.exists():
        try:
            data = json.loads(PERMS_DB.read_text(encoding="utf-8"))
            for k,v in DEFAULT_PERMS.items():
                data.setdefault(k, v)
            return data
        except Exception:
            pass
    PERMS_DB.write_text(json.dumps(DEFAULT_PERMS, ensure_ascii=False, indent=2), encoding="utf-8")
    return json.loads(PERMS_DB.read_text(encoding="utf-8"))

def _save_perms(data: Dict[str, Any]) -> None:
    tmp = PERMS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(PERMS_DB)

def user_permissions(username: str, role: str) -> List[str]:
    perms = _load_perms()
    allowed = set(perms.get("roles", {}).get(role or "usuario", []))
    for item in perms.get("overrides", {}).get(username, []):
        item = str(item).strip()
        if not item: continue
        if item.startswith("-"):
            allowed.discard(item[1:])
        else:
            allowed.add(item)
    return sorted(allowed)

def has_perm(username: str, role: str, perm: str) -> bool:
    ps = user_permissions(username, role)
    return ("*" in ps) or (perm in ps)

def require_perm(perm: str):
    def _wrap(func):
        def _inner(*args, **kwargs):
            u = st.session_state.get("username") or ""
            r = st.session_state.get("role") or "usuario"
            if not has_perm(u, r, perm):
                banner("error", "Você não possui autorização para acessar este recurso.")
                log_event("perm_denied", {"perm": perm, "role": r}, level="WARN")
                st.stop()
            return func(*args, **kwargs)
        return _inner
    return _wrap

# =============================================================================
# CSS — Windows 11 / Fluent (com acentos laranja)
# =============================================================================
def _inject_css(theme: str | None = None):
    mode = (theme or st.session_state.get("theme_mode") or "Claro").strip().lower()

    if mode == "claro":
        # Paleta Clara
        HB_BG      = "#f7f8fb"
        HB_CARD    = "#ffffff"
        HB_BORDER  = "#e6e9f2"
        HB_TEXT    = "#0f1116"
        HB_MUTED   = "#475069"
        HB_GLASS   = "rgba(0,0,0,.04)"
    else:
        # Paleta Escura (padrão anterior)
        HB_BG      = "#0f1116"
        HB_CARD    = "#141821"
        HB_BORDER  = "#2a3142"
        HB_TEXT    = "#f5f7fb"
        HB_MUTED   = "#c9d2e4"
        HB_GLASS   = "rgba(255,255,255,.06)"

    st.markdown(f"""
<style>
:root {{
  --hb-bg: {HB_BG};
  --hb-card: {HB_CARD};
  --hb-border: {HB_BORDER};
  --hb-text: {HB_TEXT};
  --hb-muted: {HB_MUTED};
  --hb-accent: {BRAND_COLOR};
  --hb-accent2: #ffb267;
  --hb-glass: {HB_GLASS};
}}

html, body, [data-testid="stAppViewContainer"] {{
  background: var(--hb-bg)!important; color: var(--hb-text)!important;
}}

/* ---------- SIDEBAR ---------- */
[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02)) !important;
  border-right: 1px solid var(--hb-border);
  backdrop-filter: blur(10px);
}}
[data-testid="stSidebar"] .sidebar-content, 
[data-testid="stSidebar"] * {{
  color: var(--hb-text) !important;
}}
[data-testid="stSidebar"] h3, [data-testid="stSidebar"] h2 {{
  font-weight: 800; letter-spacing: .2px;
}}
.hb-side-title {{
  display:flex; align-items:center; gap:.5rem;
  margin: .25rem 0 1rem 0;
  font-weight:800;
}}
.hb-dot {{
  width:10px; height:10px; border-radius:999px;
  background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2));
  box-shadow: 0 0 10px rgba(249,115,22,.55);
}}

[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label {{
  position: relative;
  display: flex; align-items: center; gap:.6rem;
  padding: .55rem .75rem;
  border-radius: 14px;
  border: 1px solid transparent;
  background: rgba(255,255,255,.03);
  transition: all .15s ease;
  margin: .15rem 0;
  cursor: pointer;
}}
[data-testid="stSidebar"] .stRadio input[type="radio"] {{ opacity: 0; position: absolute; left: -9999px; }}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label::before {{
  content: "";
  width: 10px; height: 10px; border-radius: 999px;
  background: rgba(255,255,255,.22);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.15);
  flex: 0 0 auto;
}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:hover {{
  background: rgba(255,255,255,.07);
  border-color: rgba(255,255,255,.10);
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div {{
  color: #0b0e14 !important;
  background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2));
  border: 0 !important;
  box-shadow: 0 6px 26px rgba(249, 115, 22, .28);
  font-weight: 800;
  border-radius: 14px;
  padding: .55rem .75rem;
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div::before {{
  content: "";
  width: 10px; height: 10px; border-radius: 999px;
  background: #0b0e14;
  box-shadow: 0 0 0 3px rgba(0,0,0,.15);
  margin-right: .1rem;
}}

/* ---------- Cards / inputs ---------- */
.card{{ 
  background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
  border:1px solid var(--hb-border); border-radius:18px; padding:16px; margin-bottom:14px;
  box-shadow: 0 6px 28px rgba(0,0,0,.10), inset 0 1px 0 rgba(255,255,255,.03);
}}
.section-title {{
  background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2));
  color:#111; font-weight:800; text-align:center; padding:.6rem .8rem; border-radius:12px; margin:0 0 12px 0;
}}
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input {{
  color:var(--hb-text)!important; background:transparent!important; border:1px solid var(--hb-border)!important; border-radius:12px!important;
}}
div[data-baseweb="select"] input, div[data-baseweb="select"] span {{ color:var(--hb-text)!important; }}
label, .stMarkdown p, .block-label {{ color: var(--hb-text)!important; }}
.stButton>button, .stDownloadButton>button {{
  background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2)); 
  color:#111!important; font-weight:800; border:0; border-radius:12px; padding:.55rem 1rem;
}}
.stButton>button:hover, .stDownloadButton>button:hover {{ filter: brightness(1.05); }}
.hb-banner {{ display:flex; gap:10px; align-items:center; padding:.75rem 1rem; border-radius:14px; border:1px solid var(--hb-border); margin:.25rem 0 .75rem 0; background: var(--hb-glass); }}
.hb-banner .title {{ font-weight:800; }}
.hb-banner.info    {{ border-left:6px solid #60a5fa; }}
.hb-banner.warn    {{ border-left:6px solid #facc15; }}
.hb-banner.success {{ border-left:6px solid #22c55e; }}
.hb-banner.error   {{ border-left:6px solid #ef4444; }}
.dataframe thead tr th{{ background:{"#1b2230" if (theme or "escuro")!="claro" else "#eef2ff"}!important; color:{"#fff" if (theme or "escuro")!="claro" else "#0f1116"}!important; border-bottom:1px solid var(--hb-border)!important; }}
.hb-topbar {{ height:6px; background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2)); border-radius:6px; margin:4px 0 10px 0; }}
</style>
""", unsafe_allow_html=True)

_inject_css()

# =============================================================================
# BANNERS + FLASH
# =============================================================================
def banner(kind: str, text: str, button: dict | None = None):
    kind = (kind or "info").lower()
    icon = {"success":"✅", "error":"⛔", "warn":"⚠️", "info":"ℹ️"}.get(kind, "ℹ️")
    c = st.container()
    with c:
        st.markdown(
            f"""<div class="hb-banner {kind}">
                    <div class="title">{icon}</div>
                    <div style="flex:1">{text}</div>
                </div>""",
            unsafe_allow_html=True,
        )
        if isinstance(button, dict) and button.get("label"):
            st.button(
                button["label"],
                key=button.get("key", f"bn_{kind}_{abs(hash(text))%10_000}"),
                on_click=button.get("on_click"),
                use_container_width=True,
            )

# ----- CORREÇÃO AQUI -----
def flash(kind: str, text: str, button: dict | None = None):
    """Empilha mensagens para renderização posterior por flash_render()."""
    ss = st.session_state
    ss.setdefault("_flash", [])
    ss["_flash"].append({"k": (kind or "info"), "t": text or "", "b": button})
# -------------------------

def flash_render(clear: bool = True):
    q = st.session_state.get("_flash") or []
    for m in q:
        banner(m.get("k","info"), m.get("t",""), m.get("b"))
    if clear:
        st.session_state["_flash"] = []

# =============================================================================
# Header
# =============================================================================
def _render_header():
    st.markdown("<div class='hb-topbar'></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='card' style='padding:.8rem 1rem;'><b>🏗️ {SYSTEM_NAME}</b></div>", unsafe_allow_html=True)

# =============================================================================
# Login UI
# =============================================================================
def _recover_admin():
    db = _load_users()
    db = _bootstrap_admin(db)
    _save_users(db)
    log_event("admin_recovered", {"where": str(USERS_DB)})
    flash("success", "Admin resetado para <b>admin / 1234</b> (troca obrigatória).")

def _auth_login_ui():
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>🔐 Entrar</div>", unsafe_allow_html=True)
    c1,c2,c3 = st.columns([1.3,1.3,0.7])
    with c1:
        user = st.text_input("Usuário", key="login_user", label_visibility="collapsed", placeholder="Usuário")
    with c2:
        pwd = st.text_input("Senha", key="login_pass", type="password",
                            label_visibility="collapsed", placeholder="Senha")
    with c3:
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        if st.button("Acessar", use_container_width=True, key="btn_login"):
            rec = user_get((user or "").strip())
            if not rec or not rec.get("active", True):
                flash("error", "Usuário inexistente ou inativo.")
                log_event("login_fail", {"username": user, "reason": "not_found_or_inactive"}, level="WARN")
            elif not _verify_password_simple(pwd, rec.get("password","")):
                flash("error", "Senha incorreta.")
                log_event("login_fail", {"username": user, "reason": "bad_password"}, level="WARN")
            else:
                s["logged_in"]=True
                s["username"]=(user or "").strip()
                s["is_admin"]=bool(rec.get("is_admin",False))
                s["role"]=rec.get("role","usuario")
                s["must_change"]=bool(rec.get("must_change",False))
                prefs = load_user_prefs(); prefs["last_user"]=s["username"]; save_user_prefs(prefs)
                log_event("login_success", {"username": s["username"], "role": s["role"]})
                flash("success", f"Bem-vindo, <b>{s['username']}</b>!")
                _rerun()

    st.caption("Primeiro acesso: <b>admin / 1234</b> (será exigida troca de senha).")
    rec1, rec2 = st.columns([1,1])
    with rec1:
        if st.button("Recuperar acesso (admin)", use_container_width=True):
            _recover_admin(); _rerun()
    with rec2:
        st.markdown(f"<div class='hb-banner info'>📁 Base local: <code>{PREFS_DIR}</code></div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

def _force_change_password_ui(username: str):
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>🔑 Definir nova senha</div>", unsafe_allow_html=True)
    p1 = st.text_input("Nova senha", type="password")
    p2 = st.text_input("Confirmar nova senha", type="password")
    if st.button("Salvar nova senha", use_container_width=True, key="btn_setpwd"):
        if len(p1)<4:
            banner("warn", "Use ao menos 4 caracteres.")
        elif p1!=p2:
            banner("error", "As senhas não conferem.")
        else:
            rec = user_get(username) or {}
            rec["password"]=_hash_password_simple(p1); rec["must_change"]=False
            user_set(username, rec)
            log_event("password_changed", {"username": username})
            flash("success", "Senha atualizada! Faça login novamente se necessário.")
            s["must_change"]=False; _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# Gate inicial
if not s["logged_in"]:
    _auth_login_ui()
    flash_render()
    st.stop()

if s.get("must_change", False):
    _force_change_password_ui(s["username"])
    flash_render()
    st.stop()

# Header/topbar
_render_header()
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
papel = "Admin" if s.get("is_admin") else s.get("role","usuário").capitalize()
st.markdown(
    f"<div class='card'>👋 Olá, <b>{nome_login}</b> — <span style='opacity:.9'>{papel}</span></div>",
    unsafe_allow_html=True
)

# Toolbar topo (tema + sair)
tb1,tb2,tb3 = st.columns([1,1,1])
with tb1:
    s["theme_mode"] = st.radio("Tema", ["Claro","Escuro"], horizontal=True,
                               index=0 if s.get("theme_mode")=="Claro" else 1, key="theme_sel_main")
with tb2:
    st.write("")
with tb3:
    st.write("")
    if st.button("Sair", use_container_width=True, key="btn_logout_main"):
        log_event("logout", {"username": s.get("username")})
        s["logged_in"] = False
        flash("info", "Sessão encerrada.")
        _rerun()
        # Após o radio:
if "theme_prev" not in s:
    s["theme_prev"] = s["theme_mode"]

if s["theme_mode"] != s["theme_prev"]:
    # persiste preferência
    prefs = load_user_prefs()
    prefs["theme_mode"] = s["theme_mode"]
    save_user_prefs(prefs)
    s["theme_prev"] = s["theme_mode"]
    _inject_css(s["theme_mode"])  # re-injeta CSS no novo modo
    _rerun()

# =============================================================================
# Painel Admin + Autorizações + Auditoria
# =============================================================================
CAN_ADMIN      = bool(s.get("is_admin", False))
ROLE           = s.get("role","usuario")
CAN_VIEW_AUDIT = CAN_ADMIN or has_perm(s.get("username",""), ROLE, "auditoria_view")

if CAN_ADMIN:
    with st.expander("👤 Painel de Usuários (Admin)", expanded=False):
        st.markdown("Cadastre, ative/desative, defina papéis e redefina senhas.")
        tab1, tab2, tab3 = st.tabs(["Usuários", "Novo usuário", "Autorizações"])

        # Usuários
        with tab1:
            users = user_list()
            if not users:
                banner("info", "Nenhum usuário cadastrado.")
            else:
                for u in users:
                    colA,colB,colC,colD,colE,colF = st.columns([2,1.1,1.0,1.4,1.4,2])
                    colA.write(f"**{u['username']}**")
                    colB.write("👑 Admin" if u.get("is_admin") else u.get("role","usuario").capitalize())
                    colC.write("✅ Ativo" if u.get("active", True) else "❌ Inativo")
                    colD.write(("Exige troca" if u.get("must_change") else "Senha OK"))
                    with colE:
                        if u["username"] != "admin":
                            if st.button(("Desativar" if u.get("active", True) else "Reativar"), key=f"act_{u['username']}"):
                                rec = user_get(u["username"]) or {}
                                rec["active"] = not rec.get("active", True)
                                user_set(u["username"], rec)
                                log_event("user_status_toggle", {"user": u["username"], "active": rec["active"]})
                                flash("success", "Status atualizado.")
                                _rerun()
                            if st.button("Redefinir", key=f"rst_{u['username']}"):
                                rec = user_get(u["username"]) or {}
                                rec["password"] = _hash_password_simple("1234")
                                rec["must_change"] = True
                                user_set(u["username"], rec)
                                log_event("user_password_reset", {"user": u["username"]})
                                flash("success", "Senha redefinida para 1234 (troca obrigatória).")
                                _rerun()
                    with colF:
                        if u["username"] != "admin":
                            new_role = st.selectbox("Papel", ["usuario","gestor","diretoria","admin"],
                                                    index=["usuario","gestor","diretoria","admin"].index(u.get("role","usuario")),
                                                    key=f"role_{u['username']}")
                            if st.button("Salvar papel", key=f"save_role_{u['username']}"):
                                rec = user_get(u["username"]) or {}
                                rec["role"] = new_role
                                rec["is_admin"] = (new_role == "admin")
                                user_set(u["username"], rec)
                                log_event("user_role_changed", {"user": u["username"], "role": new_role})
                                flash("success", "Papel atualizado.")
                                _rerun()

        # Novo usuário
        with tab2:
            new_u = st.text_input("Usuário (login)", key="new_user_login")
            new_role = st.selectbox("Papel inicial", ["usuario","gestor","diretoria","admin"], index=0, key="new_user_role")
            if st.button("Criar usuário", key="btn_new_user"):
                if not new_u.strip():
                    banner("error", "Informe o nome do usuário.")
                elif user_exists(new_u.strip()):
                    banner("warn", "Usuário já existe.")
                else:
                    user_set(new_u.strip(), {
                        "password": _hash_password_simple("1234"),
                        "is_admin": (new_role == "admin"), "active": True, "must_change": True,
                        "role": new_role, "created_at": datetime.now().isoformat(timespec="seconds")
                    })
                    log_event("user_created", {"created_user": new_u.strip(), "role": new_role})
                    flash("success", "Usuário criado com senha 1234 (troca obrigatória).")
                    _rerun()

        # Autorização
        with tab3:
            perms = _load_perms()
            st.caption("Papel → Permissões (use '*' para todas). Overrides por usuário aceitam prefixo '-' para remover permissão herdada.")
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Papéis")
                roles = list(perms.get("roles", {}).keys())
                for role in roles:
                    perms_txt = st.text_area(f"{role}", "\n".join(perms["roles"][role]), height=100, key=f"role_{role}_txt")
                    perms["roles"][role] = [p.strip() for p in perms_txt.splitlines() if p.strip()]
            with c2:
                st.subheader("Overrides por usuário")
                ov = perms.get("overrides", {})
                all_users = [u["username"] for u in user_list()]
                who = st.selectbox("Usuário", ["(selecione)"] + all_users, index=0)
                if who and who != "(selecione)":
                    cur = ov.get(who, [])
                    ov_txt = st.text_area(f"Overrides de {who}", "\n".join(cur), height=120, key=f"ov_{who}_txt")
                    ov[who] = [p.strip() for p in ov_txt.splitlines() if p.strip()]
                perms["overrides"] = ov
            if st.button("💾 Salvar permissões", type="primary", key="btn_save_perms"):
                _save_perms(perms)
                log_event("perms_updated", {"by": s.get("username")})
                flash("success", "Permissões atualizadas.")

# =============================================================================
# Auditoria
# =============================================================================
if CAN_VIEW_AUDIT:
    with st.expander("🧾 Auditoria do Sistema (Log de Diretoria)", expanded=False):
        df_log = read_audit_df()
        if df_log.empty:
            banner("info", "Sem eventos de auditoria ainda.")
        else:
            c1, c2, c3, c4 = st.columns([1.4, 1.2, 1.2, 1.0])
            with c1:
                users_opt = ["(Todos)"] + sorted([u for u in df_log["user"].dropna().unique().tolist()])
                f_user = st.selectbox("Usuário", users_opt, index=0, key="flt_user_aud")
            with c2:
                f_action = st.text_input("Ação contém...", "", key="flt_action_aud")
            with c3:
                lv_opts = ["(Todos)", "INFO", "WARN", "ERROR"]
                f_level = st.selectbox("Nível", lv_opts, index=0, key="flt_level_aud")
            with c4:
                page_size = st.selectbox("Linhas", [100, 300, 1000], index=1, key="flt_page_aud")

            logv = df_log.copy()
            if f_user and f_user != "(Todos)":
                logv = logv[logv["user"] == f_user]
            if f_action:
                logv = logv[logv["action"].str.contains(f_action, case=False, na=False)]
            if f_level and f_level != "(Todos)":
                logv = logv[logv["level"] == f_level]

            total = len(logv)
            if total > 0:
                pcols = st.columns([1, 3, 1])
                with pcols[0]:
                    page = st.number_input("Página", min_value=1, max_value=max(1, (total - 1) // int(page_size) + 1), value=1, step=1, key="aud_page")
                start = (int(page) - 1) * int(page_size)
                end = start + int(page_size)
                view = logv.iloc[start:end].copy()
            else:
                view = logv.copy()

            st.dataframe(view, use_container_width=True)

            cdl1, cdl2 = st.columns([1, 1])
            with cdl1:
                st.download_button(
                    "CSV (filtro aplicado)",
                    data=view.to_csv(index=False).encode("utf-8"),
                    file_name=f"audit_{SYSTEM_CODE}_{datetime.utcnow().strftime('%Y-%m-%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                    key="dl_aud_csv",
                )
            with cdl2:
                st.download_button(
                    "JSONL (completo)",
                    data=AUDIT_LOG.read_bytes() if AUDIT_LOG.exists() else b"",
                    file_name=f"audit_full_{SYSTEM_CODE}.jsonl",
                    mime="application/json",
                    use_container_width=True,
                    key="dl_aud_jsonl",
                )

# =============================================================================
# DB (SQLite) para OS/Clientes/Obras/Serviços
# =============================================================================
Base = declarative_base()

# Usuários do DB (futuro)
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    salt = Column(String, nullable=True)
    pw_hash = Column(String, nullable=True)
    is_active = Column(Integer, default=1)

def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt.hex(), h.hex()

def verify_password(password: str, salt_hex: str, pw_hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000).hex()
    return hmac.compare_digest(h, pw_hash_hex)

class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False, unique=True)
    documento = Column(String)
    contato = Column(String)
    email = Column(String)
    telefone = Column(String)
    ativo = Column(Integer, default=1)
    bloqueado = Column(Integer, default=0)
    bloqueado_motivo = Column(Text)
    bloqueado_desde = Column(Date)
    obras = relationship("Obra", back_populates="cliente_ref")

class Obra(Base):
    __tablename__ = "obras"
    id = Column(Integer, primary_key=True)
    nome = Column(String, nullable=False)
    endereco = Column(String, nullable=False)
    cliente = Column(String)  # legado
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    ativo = Column(Integer, default=1)
    bloqueada = Column(Integer, default=0)
    bloqueada_motivo = Column(Text)
    bloqueada_desde = Column(Date)
    anexo_proposta = Column(String)
    anexo_contrato = Column(String)
    anexo_cnpj     = Column(String)
    cliente_ref = relationship("Cliente", back_populates="obras")
    os_list = relationship("OS", back_populates="obra", cascade="all, delete")

class Servico(Base):
    __tablename__ = "servicos"
    id = Column(Integer, primary_key=True)
    codigo = Column(String, nullable=False, unique=True)
    descricao = Column(String, nullable=False)
    unidade = Column(String, nullable=False, default="un")
    preco_unit = Column(Float)
    ativo = Column(Integer, default=1)
    itens = relationship("OSItem", back_populates="servico", cascade="all, delete")

# >>>>>>>>>>>>>>>>>>>>>>>>> NOVO: Serviços por Obra <<<<<<<<<<<<<<<<<<<<<<<<<
class ObraServico(Base):
    __tablename__ = "obra_servicos"
    id = Column(Integer, primary_key=True)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False, index=True)
    servico_id = Column(Integer, ForeignKey("servicos.id"), nullable=False, index=True)
    preco_unit = Column(Float)   # preço específico para esta obra
    ativo = Column(Integer, default=1)
    servico = relationship("Servico")

class OS(Base):
    __tablename__ = "os"
    id = Column(Integer, primary_key=True)
    numero = Column(String, nullable=False, unique=True)  # HAB-AAAA-####
    data_emissao = Column(Date, default=date.today)
    obra_id = Column(Integer, ForeignKey("obras.id"))
    status = Column(String, default="Aberta")
    observacoes = Column(Text)
    obra = relationship("Obra", back_populates="os_list")
    itens = relationship("OSItem", back_populates="os", cascade="all, delete")

class OSItem(Base):
    __tablename__ = "os_itens"
    id = Column(Integer, primary_key=True)
    os_id = Column(Integer, ForeignKey("os.id"))
    servico_id = Column(Integer, ForeignKey("servicos.id"))
    quantidade_prevista = Column(Float)
    # >>> snapshot de preço no momento da emissão:
    preco_unit = Column(Float)
    os = relationship("OS", back_populates="itens")
    servico = relationship("Servico", back_populates="itens")

class Medicao(Base):
    __tablename__ = "medicoes"
    id = Column(Integer, primary_key=True)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False)
    numero = Column(Integer, nullable=False)
    periodo_ini = Column(Date, nullable=False)
    periodo_fim = Column(Date, nullable=False)
    criado_em = Column(Date, default=date.today)

DB_PATH = Path(__file__).with_name("os_habisolute.db")
engine = create_engine(f"sqlite:///{DB_PATH}", future=True, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base.metadata.create_all(engine)

# Índices/PRAGMA

def _safe_create_index(conn, idx_name: str, table: str, cols: str):
    try:
        # check table exists
        t = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not t:
            return
        # check index exists
        row = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index' AND name=?", (idx_name,)).fetchone()
        if row:
            return
        conn.exec_driver_sql(f"CREATE INDEX {idx_name} ON {table}({cols})")
    except Exception:
        pass

with engine.begin() as conn:
    try:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    _safe_create_index(conn, "ix_os_obra_data", "os", "obra_id, data_emissao")
    _safe_create_index(conn, "ix_os_status", "os", "status")
    _safe_create_index(conn, "ix_os_numero", "os", "numero")
    _safe_create_index(conn, "ix_ositem_osid", "os_itens", "os_id")
    _safe_create_index(conn, "ix_medicoes_obra", "medicoes", "obra_id")

def _ensure_medicoes_schema(engine):
    with engine.begin() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "medicoes" not in tables:
            conn.exec_driver_sql("""
                CREATE TABLE medicoes (
                    id INTEGER PRIMARY KEY,
                    obra_id INTEGER NOT NULL,
                    numero INTEGER NOT NULL,
                    periodo_ini DATE NOT NULL,
                    periodo_fim DATE NOT NULL,
                    criado_em DATE,
                    FOREIGN KEY(obra_id) REFERENCES obras(id)
                )
            """)

def _ensure_clientes_schema_and_backfill(engine):
    with engine.begin() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "clientes" not in tables:
            conn.exec_driver_sql("""
                CREATE TABLE clientes (
                    id INTEGER PRIMARY KEY,
                    nome TEXT UNIQUE NOT NULL,
                    documento TEXT, contato TEXT, email TEXT, telefone TEXT,
                    ativo INTEGER DEFAULT 1,
                    bloqueado INTEGER DEFAULT 0,
                    bloqueado_motivo TEXT,
                    bloqueado_desde DATE
                )
            """)
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('obras')").fetchall()}
        if "cliente_id" not in cols: conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN cliente_id INTEGER")
        if "bloqueada" not in cols: conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada INTEGER DEFAULT 0")
        if "bloqueada_motivo" not in cols: conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada_motivo TEXT")
        if "bloqueada_desde" not in cols: conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada_desde DATE")

        obras = conn.exec_driver_sql(
            "SELECT id, cliente FROM obras WHERE cliente IS NOT NULL AND TRIM(cliente)<>'' "
            "AND (cliente_id IS NULL OR cliente_id='')"
        ).fetchall()
        for obra_id, nome_cli in obras:
            nm = (nome_cli or "").strip()
            if not nm: continue
            row = conn.exec_driver_sql("SELECT id FROM clientes WHERE nome = ?", (nm,)).fetchone()
            if row is None:
                conn.exec_driver_sql("INSERT INTO clientes (nome, ativo) VALUES (?, 1)", (nm,))
                row = conn.exec_driver_sql("SELECT id FROM clientes WHERE nome = ?", (nm,)).fetchone()
            conn.exec_driver_sql("UPDATE obras SET cliente_id=? WHERE id=?", (row[0], obra_id))

def _ensure_obras_attachments(engine):
    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('obras')").fetchall()}
        if "anexo_proposta" not in cols: conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_proposta TEXT")
        if "anexo_contrato" not in cols: conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_contrato TEXT")
        if "anexo_cnpj" not in cols: conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN anexo_cnpj TEXT")

def _ensure_users_schema_and_default(engine):
    with engine.begin() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "users" not in tables:
            conn.exec_driver_sql("""
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    salt TEXT,
                    pw_hash TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
        else:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('users')").fetchall()}
            if "salt" not in cols: conn.exec_driver_sql("ALTER TABLE users ADD COLUMN salt TEXT")
            if "pw_hash" not in cols: conn.exec_driver_sql("ALTER TABLE users ADD COLUMN pw_hash TEXT")
            if "is_active" not in cols: conn.exec_driver_sql("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")

        row = conn.exec_driver_sql("SELECT id, salt, pw_hash FROM users WHERE username='admin'").fetchone()
        if row is None:
            salt_hex, h_hex = _hash_password("admin")
            conn.exec_driver_sql(
                "INSERT INTO users (username, salt, pw_hash, is_active) VALUES (?, ?, ?, 1)",
                ("admin", salt_hex, h_hex)
            )
        else:
            uid, salt_hex, pw_hex = row
            if not salt_hex or not pw_hex:
                salt_hex, h_hex = _hash_password("admin")
                conn.exec_driver_sql(
                    "UPDATE users SET salt=?, pw_hash=?, is_active=1 WHERE id=?",
                    (salt_hex, h_hex, uid)
                )
        orphan_ids = conn.exec_driver_sql(
            "SELECT id FROM users WHERE (salt IS NULL OR TRIM(COALESCE(salt,''))='') "
            "OR (pw_hash IS NULL OR TRIM(COALESCE(pw_hash,''))='')"
        ).fetchall()
        if orphan_ids:
            conn.exec_driver_sql(
                "UPDATE users SET is_active=0 WHERE id IN (%s)" %
                ",".join(str(r[0]) for r in orphan_ids)
            )

# >>>>>>>>>>>>>>>>>>>>>>>>> NOVO: Garantir obra_servicos + snapshot em os_itens <<<<<<<<<<<<<<<<<<<<<<<<<
def _ensure_obra_servicos_schema_and_indexes(engine):
    with engine.begin() as conn:
        tables = {r[0] for r in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "obra_servicos" not in tables:
            conn.exec_driver_sql("""
                CREATE TABLE obra_servicos (
                    id INTEGER PRIMARY KEY,
                    obra_id INTEGER NOT NULL,
                    servico_id INTEGER NOT NULL,
                    preco_unit REAL,
                    ativo INTEGER DEFAULT 1,
                    FOREIGN KEY(obra_id) REFERENCES obras(id),
                    FOREIGN KEY(servico_id) REFERENCES servicos(id)
                )
            """)
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_obraserv_obra ON obra_servicos(obra_id)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_obraserv_srv  ON obra_servicos(servico_id)")

        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('os_itens')").fetchall()}
        if "preco_unit" not in cols:
            conn.exec_driver_sql("ALTER TABLE os_itens ADD COLUMN preco_unit REAL")
            conn.exec_driver_sql("""
                UPDATE os_itens
                SET preco_unit = (
                    SELECT preco_unit FROM servicos s WHERE s.id = os_itens.servico_id
                )
                WHERE preco_unit IS NULL
            """)

_ensure_medicoes_schema(engine)
_ensure_clientes_schema_and_backfill(engine)
_ensure_obras_attachments(engine)
_ensure_users_schema_and_default(engine)
_ensure_obra_servicos_schema_and_indexes(engine)

# =============================================================================
# Helpers
# =============================================================================
STATUS_OPTIONS = ["Aberta", "Em Execução", "Medido em Aberto", "Medido", "Concluída", "Cancelada"]

def to_df(sess: Session, table) -> pd.DataFrame:
    rows = sess.execute(select(table)).scalars().all()
    if not rows: return pd.DataFrame()
    recs = [{c.name: getattr(r, c.name) for r in r.__table__.columns} for r in rows]
    return pd.DataFrame(recs)

def gerar_numero_os(sess: Session) -> str:
    ano = datetime.now().year
    prefix = f"HAB-{ano}-"
    ultimo = sess.execute(select(OS).where(OS.numero.like(f"{prefix}%")).order_by(OS.id.desc())).scalars().first()
    if not ultimo:
        seq = 1
    else:
        try: seq = int(ultimo.numero.split("-")[-1]) + 1
        except: seq = ultimo.id + 1
    return f"{prefix}{seq:04d}"

def format_brl(v: float) -> str:
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception: return "R$ 0,00"

# Backup (DB + anexos)
BACKUPS_DIR = (BASE_DIR / "backups"); BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
def make_full_backup() -> Path:
    base_dir = BASE_DIR
    db_path = DB_PATH
    anexos_root = (base_dir / "anexos")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = BACKUPS_DIR / f"backup_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if db_path.exists(): zf.write(db_path, arcname=f"database/{db_path.name}")
        if anexos_root.exists():
            for p in anexos_root.rglob("*"):
                if p.is_file(): zf.write(p, arcname=str(p.relative_to(base_dir)))
    return zip_path

# Anexos de Obras
ANEXOS_DIR = BASE_DIR / "anexos" / "obras"; ANEXOS_DIR.mkdir(parents=True, exist_ok=True)
_VALID_KINDS = {"cnpj", "proposta", "contrato"}

def _save_anexo(uploaded_file, obra_id: int, kind: str) -> str | None:
    if uploaded_file is None: return None
    kind = (kind or "").lower().strip()
    if kind not in _VALID_KINDS: raise ValueError(f"Tipo de anexo inválido: {kind}")
    ext = Path(uploaded_file.name or f"{kind}.bin").suffix.lower() or ".bin"
    obra_dir = ANEXOS_DIR / f"obra_{int(obra_id)}"; obra_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = obra_dir / f"{kind}_tmp{ext}"
    try: data = uploaded_file.getvalue()
    except Exception:
        try: data = uploaded_file.getbuffer()
        except Exception: data = uploaded_file.read()
    tmp_path.write_bytes(bytes(data))
    final_path = obra_dir / f"{kind}{ext}"
    if final_path.exists(): final_path.unlink()
    tmp_path.replace(final_path)
    return final_path.relative_to(BASE_DIR).as_posix()

def _download_btn_if_exists(label: str, path_str: str | None) -> None:
    if not path_str: return
    p = Path(path_str)
    if not p.is_absolute(): p = BASE_DIR / p
    if p.exists() and p.is_file():
        st.download_button(label=label, data=p.read_bytes(), file_name=p.name, mime="application/octet-stream")

def _abs_ok(path_str: str | None) -> tuple[bool, str]:
    if not path_str: return (False, "")
    p = Path(path_str)
    if not p.is_absolute(): p = BASE_DIR / p
    return (p.exists() and p.is_file(), p.name)

# =============================================================================
# PDFs (OS, Medição, Fechamento)
# =============================================================================
styles = getSampleStyleSheet()
styleN = styles["BodyText"]
styleSmall = ParagraphStyle("small", parent=styleN, fontSize=9, leading=11)
styleTiny  = ParagraphStyle("tiny",  parent=styleN, fontSize=8, leading=10)
HB_ORANGE = colors.HexColor("#FF7A00")
FORM_CODE = "FORM.H.012.00"

def _header_vertical_centralizado() -> list:
    p1 = Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>",
                   ParagraphStyle("hdr1", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))
    p2 = Paragraph("contato@habisoluteengenharia.com.br",
                   ParagraphStyle("hdr2", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    p3 = Paragraph("(16) 3877-9480",
                   ParagraphStyle("hdr3", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    p4 = Paragraph(FORM_CODE,
                   ParagraphStyle("hdr4", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    box = Table([[p1],[p2],[p3],[p4]], colWidths=[180*mm])
    box.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    return [KeepTogether([box]), Spacer(1, 8)]

def _on_page(canvas, doc, _titulo_meta: str = ""):
    from reportlab.lib.colors import black
    canvas.saveState()
    w, h = doc.pagesize
    canvas.setFillColor(HB_ORANGE); canvas.setStrokeColor(HB_ORANGE)
    canvas.rect(0, h-10, w, 10, fill=1, stroke=0)
    footer_y = 18
    canvas.setFillColor(HB_ORANGE); canvas.setStrokeColor(HB_ORANGE)
    canvas.rect(0, footer_y + 10, w, 2, fill=1, stroke=0)
    pagina = canvas.getPageNumber(); agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    meta_txt = f"Habisolute Engenharia e Controle Tecnológico — {FORM_CODE}  {agora}  pág. {pagina}"
    canvas.setFont("Helvetica", 8.5); canvas.setFillColor(black)
    text_width = canvas.stringWidth(meta_txt, "Helvetica", 8.5)
    canvas.drawString((w - text_width)/2.0, footer_y, meta_txt)
    canvas.restoreState()

def gerar_pdf_os(os_row, obra_row, itens: list[dict], show_prices: bool, logo_bytes: bytes | None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()

    with SessionLocal() as sss:
        cli = sss.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None

    info_tbl = Table([
        [Paragraph(f"<b>Status:</b> {os_row.status}", styleSmall)],
        [Paragraph(f"<b>Obra:</b> {obra_row.nome}", styleSmall)],
        [Paragraph(f"<b>Endereço:</b> {obra_row.endereco}", styleSmall)],
        [Paragraph(f"<b>Cliente:</b> {cli.nome if cli else (obra_row.cliente or '-')}", styleSmall)],
    ], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    titulo_os = f"ORDEM DE SERVIÇO Nº {os_row.numero}    DATA: {os_row.data_emissao.strftime('%d/%m/%Y')}"
    tit_tbl = Table([[Paragraph(
        f"<b>{titulo_os}</b>",
        ParagraphStyle('titOS', parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER)
    )]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    story += [tit_tbl, Spacer(1, 8)]

    headers = ["Código", "Descrição", "Un", "Qtd"]
    if show_prices:
        headers += ["Preço Unit", "Sub Total"]
    data_rows = [headers]

    for it in itens:
        row = [it["codigo"], it["descricao"], it["unidade"], f"{it['qtd_prev']:.2f}"]
        if show_prices:
            row += [format_brl(it["preco_unit"]), format_brl(it["subtotal"])]
        data_rows.append(row)

    W = doc.width
    if not show_prices:
        col_widths = [0.18*W, 0.56*W, 0.08*W, 0.18*W]
    else:
        col_widths = [0.16*W, 0.44*W, 0.06*W, 0.10*W, 0.12*W, 0.12*W]

    total_val = sum(it["subtotal"] for it in itens) if (show_prices and itens) else 0.0
    total_row_index = None
    if show_prices:
        fillers = [""] * (len(headers) - 2)
        data_rows.append(fillers + ["Total:", format_brl(total_val)])
        total_row_index = len(data_rows) - 1

    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.black),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN", (0,1), (1,-1), "LEFT"),
        ("ALIGN", (2,1), (2,-1), "CENTER"),
        ("ALIGN", (3,1), (3,-1), "RIGHT"),
    ]))
    if show_prices:
        tbl.setStyle(TableStyle([("ALIGN", (-2,1), (-1,-1), "RIGHT")]))
    if show_prices and total_row_index is not None:
        last_label_col = len(headers) - 2
        last_value_col = len(headers) - 1
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,total_row_index), (-1,total_row_index), colors.HexColor("#f5f5f5")),
            ("FONTNAME", (last_label_col,total_row_index), (last_label_col,total_row_index), "Helvetica-Bold"),
            ("FONTNAME", (last_value_col,total_row_index), (last_value_col,total_row_index), "Helvetica-Bold"),
            ("ALIGN", (last_value_col,total_row_index), (last_value_col,total_row_index), "RIGHT"),
            ("SPAN", (0,total_row_index), (last_label_col-1,total_row_index)),
        ]))
    story.append(tbl)

    story += [Spacer(1, 24)]
    story.append(Paragraph("Data: ____/____/______", ParagraphStyle("dt", parent=styleN, fontSize=10, alignment=TA_CENTER)))
    story.append(Spacer(1, 22))

    ass_tbl = Table(
        [["", "_______________________________", "", "_______________________________", ""],
         ["", "Assinatura Cliente", "", "Assinatura Laboratorista", ""]],
        colWidths=[10*mm, 70*mm, 15*mm, 70*mm, 10*mm]
    )
    ass_tbl.setStyle(TableStyle([
        ("ALIGN",(1,0),(1,0),"CENTER"), ("ALIGN",(3,0),(3,0),"CENTER"),
        ("ALIGN",(1,1),(1,1),"CENTER"), ("ALIGN",(3,1),(3,1),"CENTER"),
        ("TOPPADDING",(0,1),(-1,1),2), ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(ass_tbl)

    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,""),
                     onLaterPages=lambda c,d:_on_page(c,d,""))
    return buf.getvalue()

def gerar_pdf_medicao(obra_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None, medicao_num: int) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()

    info_tbl = Table([[Paragraph(f"<b>Obra:</b> {obra_nome}", styleSmall)],
                      [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    titulo = f"RELATÓRIO DE MEDIÇÃO — Medição nº {medicao_num}"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle(
        "titMED", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
        ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    story += [tit_tbl, Spacer(1, 8)]

    headers = ["Data", "OS", "Código", "Descrição", "Un", "Qtd", "Preço", "Subtotal"]
    data_rows = [headers]
    for r in linhas:
        data_rows.append([
            r["data"].strftime("%d/%m/%Y") if isinstance(r["data"], date) else r["data"],
            r["os_num"], r["codigo"], r["descricao"], r["un"],
            f"{r['qtd']:.2f}", format_brl(r["preco"]), format_brl(r["subtotal"])
        ])
    W = doc.width
    col_widths = [0.09*W, 0.14*W, 0.12*W, 0.31*W, 0.06*W, 0.08*W, 0.10*W, 0.10*W]
    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.black), ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN", (0,1), (3,-1), "LEFT"), ("ALIGN", (4,1), (4,-1), "CENTER"), ("ALIGN", (5,1), (7,-1), "RIGHT"),
    ]))
    story.append(tbl)

    # Resumo
    resumo = {}
    for r in linhas:
        key = (r["codigo"], r["descricao"], r["un"])
        acc = resumo.setdefault(key, {"qtd": 0.0, "val": 0.0})
        acc["qtd"] += float(r.get("qtd", 0.0) or 0.0)
        acc["val"] += float(r.get("subtotal", 0.0) or 0.0)

    story.append(Spacer(1, 10))
    resumo_title = Table([[Paragraph("<b>RESUMO DO PERÍODO</b>", ParagraphStyle(
        "titRES", parent=styleN, fontSize=10.5, leading=12, alignment=TA_CENTER))]], colWidths=[doc.width])
    resumo_title.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")), ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story.append(resumo_title)

    res_headers = ["Código", "Descrição", "Un", "Qtd", "Valor Total"]
    res_rows = [res_headers]
    for (cod, desc, un), acc in sorted(resumo.items(), key=lambda x: (x[0][0], x[0][1])):
        res_rows.append([cod, desc, un, f"{acc['qtd']:.2f}", format_brl(acc['val'])])

    rW = doc.width
    res_tbl = Table(res_rows, colWidths=[0.14*rW, 0.46*rW, 0.07*rW, 0.13*rW, 0.20*rW], repeatRows=1)
    res_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.black), ("TEXTCOLOR",(0,0),(-1,0), colors.white),
        ("FONTNAME",(0,0),(-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.25, colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN", (0,1), (1,-1), "LEFT"), ("ALIGN", (2,1), (2,-1), "CENTER"),
        ("ALIGN", (3,1), (3,-1), "RIGHT"), ("ALIGN", (4,1), (4,-1), "RIGHT"),
    ]))
    story.append(res_tbl)

    # Total geral
    story.append(Spacer(1, 10))
    total_val = sum(r["subtotal"] for r in linhas) if linhas else 0.0
    total_box = Table([[Paragraph("<b>Total:</b>", styleN), Paragraph(f"<b>{format_brl(total_val)}</b>", styleN)]],
                      colWidths=[28*mm, 38*mm])
    total_box.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.75, colors.black), ("ALIGN", (0,0), (0,0), "RIGHT"),
        ("ALIGN", (1,0), (1,0), "RIGHT"), ("LEFTPADDING", (0,0), (-1,-1), 8), ("RIGHTPADDING",(0,0), (-1,-1), 10),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("BACKGROUND", (0,0), (0,0), colors.HexColor("#f5f5f5")),
    ]))
    wrapper = Table([[None, total_box]], colWidths=[doc.width - (28*mm + 38*mm), (28*mm + 38*mm)])
    wrapper.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                                 ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(wrapper)

    doc.build(story, onFirstPage=lambda c, d: _on_page(c, d, titulo),
                    onLaterPages=lambda c, d: _on_page(c, d, titulo))
    return buf.getvalue()

def gerar_pdf_fechamento(cliente_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()

    info_tbl = Table([[Paragraph(f"<b>Cliente:</b> {cliente_nome}", styleSmall)],
                      [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
        ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story += [info_tbl, Spacer(1, 6)]

    titulo = "FECHAMENTO POR CLIENTE"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle(
        "titFEC", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")), ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
        ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story += [tit_tbl, Spacer(1, 8)]

    agreg = {}
    for r in linhas:
        key = (r.get("obra") or "-", r["codigo"], r["descricao"], r["un"])
        acc = agreg.setdefault(key, {"qtd":0.0, "val":0.0})
        acc["qtd"] += float(r.get("qtd", 0.0) or 0.0)
        acc["val"] += float(r.get("subtotal", 0.0) or 0.0)

    headers = ["Obra", "Código", "Descrição", "Un", "Qtd", "Subtotal"]
    rows = [headers]; total = 0.0
    for (obra, cod, desc, un), acc in sorted(agreg.items(), key=lambda x: (x[0][0], x[0][1])):
        rows.append([obra, cod, desc, un, f"{acc['qtd']:.2f}", format_brl(acc["val"])])
        total += acc["val"]

    W = doc.width
    col_widths = [0.28*W, 0.10*W, 0.34*W, 0.06*W, 0.10*W, 0.12*W]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0), colors.black), ("TEXTCOLOR",(0,0),(-1,0), colors.white),
        ("FONTNAME",(0,0),(-1,0), "Helvetica-Bold"), ("GRID",(0,0),(-1,-1),0.25,colors.black),
        ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("ALIGN",(0,1),(2,-1),"LEFT"), ("ALIGN",(3,1),(3,-1),"CENTER"), ("ALIGN",(4,1),(5,-1),"RIGHT"),
    ]))
    story.append(tbl)

    story.append(Spacer(1, 10))
    total_box = Table([[Paragraph("<b>Total geral:</b>", styleN), Paragraph(f"<b>{format_brl(total)}</b>", styleN)]],
                      colWidths=[36*mm, 42*mm])
    total_box.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.75, colors.black), ("ALIGN", (0,0), (0,0), "RIGHT"),
        ("ALIGN", (1,0), (1,0), "RIGHT"), ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
        ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),10),
    ]))
    wrapper = Table([[None, total_box]], colWidths=[doc.width-(36*mm+42*mm), (36*mm+42*mm)])
    wrapper.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                                 ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(wrapper)

    doc.build(story,
              onFirstPage=lambda c, d: _on_page(c, d, titulo),
              onLaterPages=lambda c, d: _on_page(c, d, titulo))
    return buf.getvalue()

# ===================== PÁGINAS: Cadastros =====================
@require_perm("relatorios_export")
def page_clientes():
    st.markdown('<div class="section-title">Cadastro de Clientes</div>', unsafe_allow_html=True)
    col_new, col_list = st.columns([1, 2])

    with col_new:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Novo Cliente")
        nome = st.text_input("Nome do Cliente *", key="cli_new_nome")
        documento = st.text_input("Documento (CNPJ/CPF) — opcional", key="cli_new_doc")
        contato = st.text_input("Contato — opcional", key="cli_new_contato")
        email = st.text_input("E-mail — opcional", key="cli_new_email")
        telefone = st.text_input("Telefone — opcional", key="cli_new_tel")
        ativo = st.checkbox("Ativo", value=True, key="cli_new_ativo")

        if st.button("Cadastrar Cliente", use_container_width=True, key="btn_cli_add"):
            if not nome.strip():
                banner("error", "Informe o nome do cliente.")
            else:
                with SessionLocal() as sess:
                    ja = sess.execute(select(Cliente).where(Cliente.nome == nome.strip())).scalars().first()
                    if ja:
                        banner("warn", "Já existe cliente com esse nome.")
                    else:
                        sess.add(Cliente(
                            nome=nome.strip(), documento=(documento or None),
                            contato=(contato or None), email=(email or None),
                            telefone=(telefone or None), ativo=1 if ativo else 0
                        ))
                        sess.commit()
                        flash("success", "Cliente cadastrado.")
                        _rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with col_list:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Clientes")
        with SessionLocal() as sess:
            clientes = sess.execute(select(Cliente).order_by(Cliente.nome.asc())).scalars().all()

        if not clientes:
            banner("info", "Nenhum cliente ainda.")
            st.markdown('</div>', unsafe_allow_html=True)
            return

        df = pd.DataFrame([{
            "id": c.id, "nome": c.nome, "documento": c.documento, "contato": c.contato,
            "email": c.email, "telefone": c.telefone, "ativo": c.ativo,
            "bloqueado": c.bloqueado, "motivo": c.bloqueado_motivo, "desde": c.bloqueado_desde
        } for c in clientes])
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("##### Editar / Excluir")
        cli_sel = st.selectbox(
            "Selecione um cliente",
            options=clientes,
            format_func=lambda c: f"{c.nome} (ID {c.id})",
            key="cli_edit_sel"
        )
        if cli_sel:
            with SessionLocal() as sess:
                c = sess.get(Cliente, cli_sel.id)

                e1, e2 = st.columns(2)
                with e1:
                    c.nome = st.text_input("Nome", value=c.nome or "", key=f"cli_edit_nome_{c.id}")
                    c.documento = st.text_input("Documento", value=c.documento or "", key=f"cli_edit_doc_{c.id}")
                    c.contato = st.text_input("Contato", value=c.contato or "", key=f"cli_edit_ctt_{c.id}")
                with e2:
                    c.email = st.text_input("E-mail", value=c.email or "", key=f"cli_edit_email_{c.id}")
                    c.telefone = st.text_input("Telefone", value=c.telefone or "", key=f"cli_edit_tel_{c.id}")
                    c.ativo = 1 if st.checkbox("Ativo", value=bool(c.ativo), key=f"cli_edit_ativo_{c.id}") else 0

                st.markdown("##### Bloqueio do cliente")
                col_b1, col_b2 = st.columns([1, 2])
                bloqueado_atual = bool(c.bloqueado)
                novo_bloq = col_b1.checkbox("Cliente bloqueado", value=bloqueado_atual, key=f"cli_edit_bloq_{c.id}")
                novo_motivo = col_b2.text_input("Motivo (opcional)", value=c.bloqueado_motivo or "", key=f"cli_edit_bloqmot_{c.id}")

                bcol1, bcol2 = st.columns([1, 1])
                if bcol1.button("Salvar alterações", use_container_width=True, key=f"cli_save_{c.id}"):
                    dup = sess.execute(select(Cliente).where(Cliente.nome == c.nome, Cliente.id != c.id)).scalars().first()
                    if dup:
                        banner("error", "Já existe outro cliente com esse nome.")
                    else:
                        if novo_bloq and not bloqueado_atual:
                            c.bloqueado = 1; c.bloqueado_desde = date.today(); c.bloqueado_motivo = (novo_motivo or "Bloqueado")
                        elif not novo_bloq and bloqueado_atual:
                            c.bloqueado = 0; c.bloqueado_desde = None; c.bloqueado_motivo = None
                        else:
                            c.bloqueado_motivo = (novo_motivo or None)
                        sess.commit(); flash("success", "Cliente atualizado."); _rerun()

                with SessionLocal() as s2:
                    obras_vinc = s2.query(Obra).filter(
                        (Obra.cliente_id == c.id) | (func.trim(func.coalesce(Obra.cliente, "")) == c.nome)
                    ).count()
                if obras_vinc > 0:
                    bcol2.button("Excluir (bloqueado — possui obras)", disabled=True, use_container_width=True, key=f"cli_del_btn_{c.id}")
                    banner("warn", f"Este cliente possui {obras_vinc} obra(s) vinculada(s).")
                else:
                    conf = st.checkbox("Confirmo a exclusão deste cliente", key=f"cli_del_conf_{c.id}")
                    if bcol2.button("Excluir cliente", use_container_width=True, disabled=not conf, key=f"cli_del_{c.id}"):
                        sess.delete(c); sess.commit(); flash("success", "Cliente excluído."); _rerun()
        st.markdown('</div>', unsafe_allow_html=True)

@require_perm("relatorios_export")
def page_obras():
    st.markdown('<div class="section-title">Cadastro de Obras</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2])

    with c1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Nova Obra")
        with SessionLocal() as sess:
            clientes = (sess.execute(select(Cliente).where(Cliente.ativo == 1).order_by(Cliente.nome.asc()))
                        .scalars().all())
        nome = st.text_input("Nome da Obra *", key="obra_new_nome")

