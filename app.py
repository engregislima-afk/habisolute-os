# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)
# Visual Fluent/Windows 11 + banners + avisos + medição em dias

import io, re, os, json, base64, tempfile, zipfile, hashlib, hmac, secrets, calendar
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import requests  # <--- NOVO


import streamlit as st
import pandas as pd

# SQLAlchemy
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, ForeignKey, Text,
    select, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session, selectinload

# ReportLab (PDF)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
try:
    from reportlab.platypus import KeepTogether
except Exception:
    from reportlab.platypus.flowables import KeepTogether

# =============================================================================
# Identidade / Config
# =============================================================================
SYSTEM_NAME = "Habisolute — Sistema de OS"
SYSTEM_CODE = "hab_os"      # pasta local .hab_os na raiz do projeto
BRAND_COLOR = "#f97316"     # laranja base

st.set_page_config(page_title=SYSTEM_NAME, layout="wide")

BASE_DIR   = Path(__file__).resolve().parent
PREFS_DIR  = BASE_DIR / f".{SYSTEM_CODE}"; PREFS_DIR.mkdir(parents=True, exist_ok=True)
USERS_DB   = PREFS_DIR / "users.json"
AUDIT_LOG  = PREFS_DIR / "audit.jsonl"
PERMS_DB   = PREFS_DIR / "perms.json"
PREFS_PATH = PREFS_DIR / "prefs.json"

# =============================================================================
# Preferências simples
# =============================================================================
def _save_all_prefs(data: Dict[str, Any]) -> None:
    tmp = PREFS_DIR / "prefs.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(PREFS_PATH)

def _load_all_prefs() -> Dict[str, Any]:
    try:
        if PREFS_PATH.exists(): return json.loads(PREFS_PATH.read_text(encoding="utf-8")) or {}
    except Exception: pass
    return {}

def load_user_prefs(key: str="default")->Dict[str,Any]: 
    return _load_all_prefs().get(key,{})

def save_user_prefs(prefs: Dict[str,Any], key: str="default")->None:
    data=_load_all_prefs(); data[key]=prefs; _save_all_prefs(data)

# =============================================================================
# Auditoria
# =============================================================================
def _now_iso():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def log_event(action: str, meta: Dict[str, Any] | None = None, level: str = "INFO"):
    try:
        rec = {
            "ts": _now_iso(),
            "user": st.session_state.get("username") or "anon",
            "level": level, "action": action, "meta": meta or {}, "system": SYSTEM_CODE,
        }
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def read_audit_df() -> pd.DataFrame:
    if not AUDIT_LOG.exists(): 
        return pd.DataFrame(columns=["ts","user","level","action","meta","system"])
    rows = []
    with AUDIT_LOG.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                rows.append({
                    "ts": rec.get("ts"), "user": rec.get("user"),
                    "level": rec.get("level"), "action": rec.get("action"),
                    "meta": json.dumps(rec.get("meta") or {}, ensure_ascii=False),
                    "system": rec.get("system", ""),
                })
            except Exception:
                continue
    df = pd.DataFrame(rows, columns=["ts","user","level","action","meta","system"])
    if not df.empty:
        df = df.sort_values("ts", ascending=False, kind="stable").reset_index(drop=True)
    return df

# =============================================================================
# Estado
# =============================================================================
s = st.session_state
s.setdefault("logged_in", False)
s.setdefault("username", None)
s.setdefault("is_admin", False)
s.setdefault("role", "usuario")     # usuario | gestor | diretoria | admin
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("_flash", [])

def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# =============================================================================
# Auth (JSON local)
# =============================================================================
def _hash_password_simple(pw: str) -> str:
    return hashlib.sha256((f"{SYSTEM_CODE}|" + pw).encode("utf-8")).hexdigest()

def _verify_password_simple(pw: str, hashed: str) -> bool:
    try:
        return _hash_password_simple(pw) == hashed
    except Exception:
        return False

def _save_users(data: Dict[str, Any]) -> None:
    tmp = USERS_DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); tmp.replace(USERS_DB)

def _bootstrap_admin(db: Dict[str, Any]) -> Dict[str, Any]:
    db.setdefault("users", {})
    if "admin" not in db["users"]:
        db["users"]["admin"] = {
            "password": _hash_password_simple("1234"),
            "is_admin": True, "active": True, "must_change": True,
            "role": "admin", "created_at": datetime.now().isoformat(timespec="seconds")
        }
    return db

def _load_users() -> Dict[str, Any]:
    try:
        if USERS_DB.exists():
            raw = USERS_DB.read_text(encoding="utf-8").strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and isinstance(data.get("users"), dict):
                    fixed = _bootstrap_admin(data)
                    if fixed is not data: _save_users(fixed)
                    return fixed
                if isinstance(data, dict):
                    fixed = _bootstrap_admin({"users": data}); _save_users(fixed); return fixed
                if isinstance(data, list):
                    users_map: Dict[str, Any] = {}
                    for item in data:
                        if isinstance(item, str):
                            uname = item.strip()
                            if not uname: continue
                            users_map[uname] = {
                                "password": _hash_password_simple("1234"),
                                "is_admin": (uname=="admin"), "active": True, "must_change": True,
                                "role": "admin" if uname=="admin" else "usuario",
                                "created_at": datetime.now().isoformat(timespec="seconds")
                            }
                        elif isinstance(item, dict) and item.get("username"):
                            uname = str(item["username"]).strip()
                            if not uname: continue
                            users_map[uname] = {
                                "password": _hash_password_simple("1234"),
                                "is_admin": bool(item.get("is_admin", uname=="admin")),
                                "active": bool(item.get("active", True)),
                                "must_change": True,
                                "role": item.get("role", "usuario"),
                                "created_at": item.get("created_at", datetime.now().isoformat(timespec="seconds"))
                            }
                    fixed = _bootstrap_admin({"users": users_map}); _save_users(fixed); return fixed
    except Exception:
        pass
    default = _bootstrap_admin({"users": {}})
    _save_users(default)
    return default

def user_get(username: str) -> Optional[Dict[str, Any]]:
    return _load_users().get("users", {}).get(username)

def user_set(username: str, record: Dict[str, Any]) -> None:
    db = _load_users(); db.setdefault("users", {})[username] = record; _save_users(db)

def user_exists(username: str) -> bool:
    return user_get(username) is not None

def user_list() -> List[Dict[str, Any]]:
    db = _load_users(); out=[]
    for uname, rec in db.get("users", {}).items():
        r = dict(rec); r["username"]=uname; out.append(r)
    out.sort(key=lambda r:(not r.get("is_admin",False), r["username"]))
    return out

def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin": return
        db["users"].pop(username, None); _save_users(db)

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
.dataframe thead tr th{{ background:{"#1b2230" if mode!="claro" else "#eef2ff"}!important; color:{"#fff" if mode!="claro" else "#0f1116"}!important; border-bottom:1px solid var(--hb-border)!important; }}
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

def flash(kind: str, text: str, button: dict | None = None):
    q = st.session_state_inject_css(s.get("theme_mode"))
    
    q.append({"k": kind, "t": text, "b": button})

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
with engine.begin() as conn:
    conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
    conn.exec_driver_sql("PRAGMA synchronous=NORMAL;")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_obra_data ON os(obra_id, data_emissao);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_status ON os(status);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_os_numero ON os(numero);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_ositem_osid ON os_itens(os_id);")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_medicoes_obra ON medicoes(obra_id);")
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
    recs = [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]
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
    def _get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)  # type: ignore
    except Exception:
        return os.environ.get(name, default)

def _clean_cnpj(cnpj: str) -> str:
    return re.sub(r"\D+", "", str(cnpj or ""))

@st.cache_data(show_spinner=False, ttl=3600)
def fetch_cnpj_apibrasil(cnpj: str) -> dict | None:
    """
    Consulta CNPJ na APIBrasil e retorna um dicionário normalizado.
    Retorna None em caso de erro ou CNPJ inválido.
    """
    cnpj_num = _clean_cnpj(cnpj)
    if len(cnpj_num) != 14:
        return None

    base_url = _get_secret("APIBRASIL_BASE_URL", "https://api.apibrasil.com.br").rstrip("/")
    path     = _get_secret("APIBRASIL_CNPJ_PATH", "/v2/cnpj").strip("/")
    token    = _get_secret("APIBRASIL_TOKEN", "").strip()
    if not token:
        # Sem token configurado
        return None

    url = f"{base_url}/{path}/{cnpj_num}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": f"{SYSTEM_CODE}/1.0"
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json() if r.headers.get("content-type","").startswith("application/json") else None
        if not isinstance(data, dict):
            return None

        # ---- Normalização (mapeie conforme seu provedor/planos) ----
        # Exemplos comuns em provedores de CNPJ:
        #   nome, razao_social, fantasia, logradouro, numero, complemento, bairro, municipio, uf, cep,
        #   telefone, email, atividade_principal, situacao
        nome = data.get("razao_social") or data.get("nome") or data.get("razaoSocial")
        fantasia = data.get("nome_fantasia") or data.get("fantasia") or data.get("nomeFantasia")
        end_logr = data.get("logradouro") or data.get("endereco", {}).get("logradouro")
        end_num  = data.get("numero") or data.get("endereco", {}).get("numero")
        end_comp = data.get("complemento") or data.get("endereco", {}).get("complemento")
        bairro   = data.get("bairro") or data.get("endereco", {}).get("bairro")
        cidade   = data.get("municipio") or data.get("cidade") or data.get("endereco", {}).get("municipio")
        uf       = data.get("uf") or data.get("estado") or data.get("endereco", {}).get("uf")
        cep      = data.get("cep") or data.get("endereco", {}).get("cep")
        telefone = data.get("telefone") or data.get("telefone1") or data.get("contato", {}).get("telefone")
        email    = data.get("email") or data.get("contato", {}).get("email")

        endereco_fmt = " ".join(
            [str(x) for x in [end_logr, end_num, end_comp, "-", bairro, "-", cidade, "/", uf, "-", cep] if x]
        ).replace(" - - ", " - ").replace(" / -", " / ").strip(" -/")

        return {
            "razao": (nome or "").strip(),
            "fantasia": (fantasia or "").strip(),
            "endereco": endereco_fmt.strip(),
            "telefone": (telefone or "").strip(),
            "email": (email or "").strip(),
            "cnpj": cnpj_num,
        }
    except Exception:
        return None

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
        # Botão: buscar dados por CNPJ
if st.button("🔎 Buscar CNPJ (APIBrasil)", use_container_width=True, key="cli_new_busca_cnpj"):
    if not (documento or "").strip():
        banner("warn", "Informe o CNPJ para buscar.")
    else:
        info = fetch_cnpj_apibrasil(documento)
        if not info:
            banner("error", "Não foi possível obter dados deste CNPJ (verifique token, plano e o número informado).")
        else:
            # Preenche os campos se estiverem vazios
            if not nome.strip():
                st.session_state["cli_new_nome"] = info["razao"] or info["fantasia"] or ""
            if not email.strip():
                st.session_state["cli_new_email"] = info["email"] or ""
            if not telefone.strip():
                st.session_state["cli_new_tel"] = info["telefone"] or ""
            # Endereço não existe no Cliente; você decide onde guardar.
            # Se quiser, pode abrir um text_input extra:
            if "cli_new_end_aux" not in st.session_state:
                st.session_state["cli_new_end_aux"] = info["endereco"]
            banner("success", "Dados carregados pelo CNPJ.")

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
        endereco = st.text_input("Endereço *", key="obra_new_end")
        cliente_opt = ["(Sem cliente)"] + [c.nome for c in clientes]
        cliente_sel_nome = st.selectbox("Cliente", cliente_opt, key="obra_new_cli")

        if st.button("Cadastrar Obra", use_container_width=True, key="btn_obra_add"):
            if not nome.strip() or not endereco.strip():
                banner("error", "Preencha Nome e Endereço.")
            else:
                with SessionLocal() as sess:
                    cid = None
                    if cliente_sel_nome != "(Sem cliente)":
                        cobj = sess.execute(select(Cliente).where(Cliente.nome == cliente_sel_nome)).scalars().first()
                        cid = cobj.id if cobj else None
                    sess.add(Obra(nome=nome.strip(), endereco=endereco.strip(), cliente_id=cid, ativo=1))
                    sess.commit()
                flash("success", "Obra cadastrada.")
                _rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Obras")
        with SessionLocal() as sess:
            obras = (sess.execute(select(Obra).options(selectinload(Obra.cliente_ref)).order_by(Obra.nome.asc()))
                     .scalars().all())

        if not obras:
            banner("info", "Nenhuma obra cadastrada.")
            st.markdown("</div>", unsafe_allow_html=True); return

        df = pd.DataFrame([{
            "id": o.id, "nome": o.nome, "endereco": o.endereco,
            "cliente": (o.cliente_ref.nome if getattr(o, "cliente_ref", None) else None),
            "ativo": o.ativo, "bloqueada": o.bloqueada,
            "motivo": o.bloqueada_motivo, "desde": o.bloqueada_desde,
        } for o in obras])
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("##### Editar / Excluir")
        obra_sel = st.selectbox(
            "Selecione uma obra",
            options=obras,
            format_func=lambda o: f"{o.nome} — {o.endereco} (ID {o.id})",
            key="obra_edit_sel",
        )
        if obra_sel:
            with SessionLocal() as sess:
                o = sess.get(Obra, obra_sel.id)
                clientes = sess.execute(select(Cliente).order_by(Cliente.nome.asc())).scalars().all()

                e1, e2 = st.columns(2)
                with e1:
                    o.nome = st.text_input("Nome", value=o.nome or "", key=f"obra_edit_nome_{o.id}")
                    o.endereco = st.text_input("Endereço", value=o.endereco or "", key=f"obra_edit_end_{o.id}")
                with e2:
                    cli_opts = ["(Sem cliente)"] + [c.nome for c in clientes]
                    cli_current = o.cliente_ref.nome if o.cliente_ref else "(Sem cliente)"
                    cli_new_nome = st.selectbox("Cliente", cli_opts, index=cli_opts.index(cli_current), key=f"obra_edit_cli_{o.id}")
                    o.ativo = 1 if st.checkbox("Ativo", value=bool(o.ativo), key=f"obra_edit_ativo_{o.id}") else 0
                    if cli_new_nome == "(Sem cliente)": o.cliente_id = None
                    else:
                        novo_cli = sess.execute(select(Cliente).where(Cliente.nome == cli_new_nome)).scalars().first()
                        o.cliente_id = novo_cli.id if novo_cli else None

                st.markdown("##### Bloqueio da obra")
                ob_b1, ob_b2 = st.columns([1, 2])
                bloqueada_atual = bool(o.bloqueada)
                nova_bloq = ob_b1.checkbox("Obra bloqueada", value=bloqueada_atual, key=f"obra_edit_bloq_{o.id}")
                novo_motivo_obra = ob_b2.text_input("Motivo (opcional)", value=o.bloqueada_motivo or "", key=f"obra_edit_bloqmot_{o.id}")

                # Anexos
                st.markdown("##### Anexos da obra")
                ac1, ac2, ac3 = st.columns(3)
                up_cnpj = ac1.file_uploader("Cartão CNPJ (PDF/JPG/PNG)", type=["pdf","jpg","jpeg","png"], key=f"up_cnpj_{o.id}")
                up_proposta = ac2.file_uploader("Proposta (PDF/JPG/PNG)", type=["pdf","jpg","jpeg","png"], key=f"up_prop_{o.id}")
                up_contrato = ac3.file_uploader("Contrato (PDF/JPG/PNG)", type=["pdf","jpg","jpeg","png"], key=f"up_cont_{o.id}")

                dc1, dc2, dc3 = st.columns(3)
                with dc1: _download_btn_if_exists("Baixar CNPJ", o.anexo_cnpj)
                with dc2: _download_btn_if_exists("Baixar Proposta", o.anexo_proposta)
                with dc3: _download_btn_if_exists("Baixar Contrato", o.anexo_contrato)

                ok_cnpj, nm_cnpj = _abs_ok(o.anexo_cnpj)
                ok_prop, nm_prop = _abs_ok(o.anexo_proposta)
                ok_cont, nm_cont = _abs_ok(o.anexo_contrato)

                faltando = []
                if not ok_cnpj: faltando.append("Cartão CNPJ")
                if not ok_prop: faltando.append("Proposta")
                if not ok_cont: faltando.append("Contrato")
                if faltando:
                    banner("warn", f"Falta anexar: <b>{', '.join(faltando)}</b>.")

                b1, b2 = st.columns([1, 1])
                if b1.button("Salvar alterações", use_container_width=True, key=f"obra_save_{o.id}"):
                    if nova_bloq and not bloqueada_atual:
                        o.bloqueada = 1; o.bloqueada_desde = date.today(); o.bloqueada_motivo = novo_motivo_obra or "Obra bloqueada"
                    elif not nova_bloq and bloqueada_atual:
                        o.bloqueada = 0; o.bloqueada_desde = None; o.bloqueada_motivo = None
                    else:
                        o.bloqueada_motivo = novo_motivo_obra or None
                    try:
                        if up_cnpj is not None:     p_rel = _save_anexo(up_cnpj, o.id, "cnpj");     o.anexo_cnpj = p_rel or o.anexo_cnpj
                        if up_proposta is not None: p_rel = _save_anexo(up_proposta, o.id, "proposta"); o.anexo_proposta = p_rel or o.anexo_proposta
                        if up_contrato is not None: p_rel = _save_anexo(up_contrato, o.id, "contrato");  o.anexo_contrato = p_rel or o.anexo_contrato
                    except Exception as e:
                        banner("error", f"Falha ao salvar anexos: {e}")
                    sess.commit(); flash("success", "Obra atualizada."); _rerun()

                os_count = sess.query(OS).filter(OS.obra_id == o.id).count()
                if os_count > 0:
                    banner("warn", f"Ao excluir esta obra, {os_count} OS serão removidas.")
                conf = st.checkbox("Confirmo a exclusão desta obra (e suas OS)", key=f"obra_del_conf_{o.id}")
                if b2.button("Excluir obra", use_container_width=True, disabled=not conf, key=f"obra_del_{o.id}"):
                    sess.delete(o); sess.commit(); flash("success", "Obra excluída."); _rerun()

                # ================== NOVO BLOCO: Serviços por Obra ==================
                st.markdown("##### Serviços desta obra (preços específicos)")
                with SessionLocal() as sess_osv:
                    catalogo = sess_osv.execute(select(Servico).order_by(Servico.codigo.asc())).scalars().all()
                    vinculos = (sess_osv.query(ObraServico, Servico)
                                .join(Servico, Servico.id == ObraServico.servico_id)
                                .filter(ObraServico.obra_id == o.id)
                                .order_by(Servico.codigo.asc())
                                .all())

                    if vinculos:
                        df_osv = pd.DataFrame([{
                            "id": osv.id, "codigo": srv.codigo, "descricao": srv.descricao,
                            "un": srv.unidade, "preco_unit": osv.preco_unit, "ativo": osv.ativo
                        } for (osv, srv) in vinculos])
                        st.dataframe(df_osv, use_container_width=True, hide_index=True)
                    else:
                        banner("info", "Nenhum serviço vinculado a esta obra ainda.")

                    st.markdown("###### Adicionar/editar vínculo")
                    cadd1, cadd2, cadd3, cadd4 = st.columns([2, 1, 1, 1])
                    srv_add = cadd1.selectbox("Serviço (catálogo)", catalogo,
                                              format_func=lambda s: f"{s.codigo} — {s.descricao}")
                    preco_add = cadd2.number_input("Preço p/ esta obra", min_value=0.0, step=1.0, value=float(srv_add.preco_unit or 0.0))
                    ativo_add = cadd3.checkbox("Ativo", value=True)
                    if cadd4.button("Salvar vínculo/atualizar", key=f"btn_save_vinc_{o.id}"):
                        existente = (sess_osv.query(ObraServico)
                                     .filter(ObraServico.obra_id == o.id, ObraServico.servico_id == srv_add.id)
                                     .one_or_none())
                        if existente is None:
                            sess_osv.add(ObraServico(obra_id=o.id, servico_id=srv_add.id,
                                                     preco_unit=preco_add, ativo=1 if ativo_add else 0))
                        else:
                            existente.preco_unit = preco_add
                            existente.ativo = 1 if ativo_add else 0
                        sess_osv.commit()
                        flash("success", "Vínculo de serviço atualizado nesta obra.")
                        _rerun()

                    if vinculos:
                        st.markdown("###### Ativar/Desativar/Remover")
                        alvo = st.selectbox("Vínculo", vinculos,
                                            format_func=lambda t: f"{t[1].codigo} — {t[1].descricao}")
                        if alvo:
                            osv, srv = alvo
                            cedit1, cedit2, cedit3, cedit4 = st.columns([1,1,1,1])
                            novo_preco = cedit1.number_input("Preço", min_value=0.0, step=1.0,
                                                             value=float(osv.preco_unit or 0.0), key=f"preco_edit_{osv.id}")
                            novo_ativo = cedit2.checkbox("Ativo", value=bool(osv.ativo), key=f"ativo_edit_{osv.id}")
                            if cedit3.button("Salvar", key=f"save_edit_{osv.id}"):
                                osv.preco_unit = novo_preco; osv.ativo = 1 if novo_ativo else 0
                                sess_osv.commit(); flash("success","Vínculo salvo."); _rerun()
                            if cedit4.button("Remover vínculo", key=f"del_edit_{osv.id}"):
                                sess_osv.delete(osv); sess_osv.commit()
                                flash("success","Vínculo removido."); _rerun()
        st.markdown("</div>", unsafe_allow_html=True)

@require_perm("relatorios_export")
def page_servicos():
    st.markdown('<div class="section-title">Cadastro de Serviços</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2])

    with c1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        codigo = st.text_input("Código *", placeholder="Ex.: CP28, SLUMP, MOLD").strip().upper()
        descricao = st.text_input("Descrição *", placeholder="Ex.: Rompimento de Corpo de Prova 28 dias")
        unidade = st.text_input("Unidade *", value="un")
        preco = st.number_input("Preço unitário (interno) — opcional", min_value=0.0, step=1.0, value=0.0)
        ativo = st.checkbox("Ativo", value=True, key="srv_new_ativo")

        if st.button("Cadastrar Serviço", use_container_width=True, key="srv_add_btn"):
            if not codigo or not descricao or not unidade:
                banner("error", "Preencha Código, Descrição e Unidade.")
            else:
                with SessionLocal() as sess:
                    ja = sess.execute(select(Servico).where(Servico.codigo == codigo)).scalars().first()
                    if ja:
                        banner("warn", "Já existe serviço com esse código.")
                    else:
                        sess.add(Servico(
                            codigo=codigo, descricao=descricao, unidade=unidade,
                            preco_unit=(preco or None), ativo=1 if ativo else 0
                        ))
                        sess.commit(); flash("success", "Serviço cadastrado."); _rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        with SessionLocal() as sess:
            servs = sess.execute(select(Servico).order_by(Servico.codigo.asc())).scalars().all()

        if not servs:
            banner("info", "Nenhum serviço cadastrado ainda.")
            st.markdown('</div>', unsafe_allow_html=True); return

        st.dataframe(pd.DataFrame([{
            "id": s.id, "codigo": s.codigo, "descricao": s.descricao,
            "unidade": s.unidade, "preco_unit": s.preco_unit, "ativo": s.ativo
        } for s in servs]), use_container_width=True, hide_index=True)

        st.markdown("##### Editar / Excluir")
        srv_sel = st.selectbox("Selecione um serviço", options=servs,
                               format_func=lambda s: f"{s.codigo} — {s.descricao} (ID {s.id})", key="srv_edit_sel")
        if srv_sel:
            with SessionLocal() as sess:
                sdb = sess.get(Servico, srv_sel.id)
                e1, e2 = st.columns(2)
                with e1:
                    novo_codigo = st.text_input("Código", value=sdb.codigo or "", key=f"srv_edit_cod_{sdb.id}").strip().upper()
                    sdb.descricao = st.text_input("Descrição", value=sdb.descricao or "", key=f"srv_edit_desc_{sdb.id}")
                    sdb.unidade = st.text_input("Unidade", value=sdb.unidade or "un", key=f"srv_edit_un_{sdb.id}")
                with e2:
                    sdb.preco_unit = st.number_input("Preço unitário", min_value=0.0, step=1.0,
                                                   value=float(sdb.preco_unit or 0.0), key=f"srv_edit_preco_{sdb.id}")
                    sdb.ativo = 1 if st.checkbox("Ativo", value=bool(sdb.ativo), key=f"srv_edit_ativo_{sdb.id}") else 0
                b1, b2 = st.columns([1, 1])
                if b1.button("Salvar alterações", use_container_width=True, key=f"srv_save_{sdb.id}"):
                    dup = sess.execute(select(Servico).where(Servico.codigo == novo_codigo, Servico.id != sdb.id)).scalars().first()
                    if dup: banner("error", "Já existe outro serviço com esse código.")
                    else: sdb.codigo = novo_codigo; sess.commit(); flash("success", "Serviço atualizado."); _rerun()

                with SessionLocal() as sess2:
                    itens_count = sess2.query(OSItem).filter(OSItem.servico_id == sdb.id).count()
                if itens_count > 0:
                    banner("warn", f"Ao excluir este serviço, {itens_count} item(ns) de OS serão removidos.")
                conf = st.checkbox("Confirmo a exclusão deste serviço", key=f"srv_del_conf_{sdb.id}")
                if b2.button("Excluir serviço", use_container_width=True, disabled=not conf, key=f"srv_del_{sdb.id}"):
                    sess.delete(sdb); sess.commit(); flash("success", "Serviço excluído."); _rerun()
        st.markdown('</div>', unsafe_allow_html=True)
# ===================== PÁGINAS: Emissão e Impressão =====================
def get_servicos_da_obra(sess: Session, obra_id: int) -> List[tuple[ObraServico, Servico]]:
    q = (sess.query(ObraServico, Servico)
         .join(Servico, Servico.id == ObraServico.servico_id)
         .filter(ObraServico.obra_id == obra_id, ObraServico.ativo == 1)
         .order_by(Servico.codigo.asc()))
    return q.all()

def get_preco_obra_or_cat(sess: Session, obra_id: int, servico_id: int) -> float:
    p = (sess.query(ObraServico.preco_unit)
           .filter(ObraServico.obra_id == obra_id, ObraServico.servico_id == servico_id)
           .scalar())
    if p is not None:
        return float(p or 0.0)
    base = sess.get(Servico, servico_id)
    return float(base.preco_unit or 0.0)

def obter_os_com_itens(sess: Session, os_id: int):
    os_row = sess.query(OS).options(selectinload(OS.itens).selectinload(OSItem.servico)).filter(OS.id == os_id).first()
    obra_row = sess.get(Obra, os_row.obra_id)
    itens = []
    for it in os_row.itens:
        sv = it.servico
        preco = it.preco_unit if getattr(it, "preco_unit", None) is not None else (sv.preco_unit or 0.0)
        itens.append({
            "codigo": sv.codigo, "descricao": sv.descricao, "unidade": sv.unidade,
            "qtd_prev": it.quantidade_prevista or 0.0, "preco_unit": preco,
            "subtotal": preco * (it.quantidade_prevista or 0.0)
        })
    return os_row, obra_row, itens

@require_perm("os_create")
def page_emitir_os():
    st.markdown('<div class="section-title">Emitir OS</div>', unsafe_allow_html=True)
    flash_render(clear=True)

    with SessionLocal() as sess:
        obras = sess.execute(
            select(Obra).options(selectinload(Obra.cliente_ref)).where(Obra.ativo == 1).order_by(Obra.nome.asc())
        ).scalars().all()

    if not obras:
        banner("warn", "Cadastre ao menos 1 obra para emitir OS.")
        return

    termo = st.text_input("Pesquisar obra", placeholder="Digite parte do nome/endereço/cliente", key="q_obra_emit").strip().lower()

    def _match(o: Obra) -> bool:
        cli_nome = (o.cliente_ref.nome if o.cliente_ref else (o.cliente or "")) or ""
        blob = f"{o.nome} {o.endereco} {cli_nome}".lower()
        return termo in blob

    obras_filtradas = [o for o in obras if _match(o)] if termo else obras
    opt_pairs = []
    for o in obras_filtradas:
        cli = getattr(o, "cliente_ref", None)
        cliente_nome = (cli.nome if cli else (o.cliente or "Sem cliente"))
        cliente_bloq = bool(getattr(cli, "bloqueado", 0)) if cli else False
        obra_bloq = bool(getattr(o, "bloqueada", 0))
        tags = []
        if cliente_bloq: tags.append("CLIENTE BLOQUEADO")
        if obra_bloq: tags.append("OBRA BLOQUEADA")
        tag = f" [{' & '.join(tags)}]" if tags else ""
        label = f"{o.nome} — {o.endereco} [{cliente_nome}]{tag}"
        opt_pairs.append((o, label, cliente_bloq, obra_bloq))

    if not opt_pairs: 
        banner("info", "Nenhuma obra encontrada para o termo informado.")
        return

    idx_escolhido = st.selectbox("Obra *", list(range(len(opt_pairs))), format_func=lambda i: opt_pairs[i][1], key="obra_emit_sel")
    obra_sel, _lbl, cliente_bloqueado, obra_bloqueada = opt_pairs[idx_escolhido]

    # Avisos de documentos pendentes
    with SessionLocal() as _s_docs:
        _obra_docs = _s_docs.get(Obra, obra_sel.id)
    docs_ok = {
        "Cartão CNPJ": bool(getattr(_obra_docs, "anexo_cnpj", None)),
        "Proposta":    bool(getattr(_obra_docs, "anexo_proposta", None)),
        "Contrato":    bool(getattr(_obra_docs, "anexo_contrato", None)),
    }
    faltando = [nome for nome, ok in docs_ok.items() if not ok]
    if faltando:
        banner("warn", f"Documentos pendentes desta obra: <b>{', '.join(faltando)}</b>. "
                       f"Anexe em <b>Cadastro → Obras → Anexos</b>.")

    # Indicador de dias em aberto para medição
    try:
        with SessionLocal() as sess:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao))
                                  .filter(OS.obra_id == obra_sel.id, OS.status == "Medido")
                                  .scalar())
            if ultima_medida_dt:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id, OS.status == "Aberta", OS.data_emissao > ultima_medida_dt)
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())
            else:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id, OS.status == "Aberta")
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())
        if os_ref and os_ref.data_emissao:
            dias = (date.today() - os_ref.data_emissao).days
            msg = ("Medição em atraso" if dias >= 30 else "Medição em dia")
            banner("info", f"{msg}: OS <b>{os_ref.numero}</b> em Aberto há <b>{dias}</b> dias.")
    except Exception:
        pass

    if cliente_bloqueado:
        with SessionLocal() as sess: cli = sess.get(Cliente, obra_sel.cliente_id) if obra_sel.cliente_id else None
        motivo = cli.bloqueado_motivo if cli else "Cliente bloqueado."
        desde = cli.bloqueado_desde.strftime("%d/%m/%Y") if (cli and cli.bloqueado_desde) else "-"
        banner("error", f"Cliente bloqueado desde {desde}. Motivo: {motivo}. Emissão desabilitada.")
    if obra_bloqueada:
        motivo_o = obra_sel.bloqueada_motivo or "Obra bloqueada."
        desde_o = obra_sel.bloqueada_desde.strftime("%d/%m/%Y") if obra_sel.bloqueada_desde else "-"
        banner("error", f"Obra bloqueada desde {desde_o}. Motivo: {motivo_o}. Emissão desabilitada.")
    bloqueio_ativo = (cliente_bloqueado or obra_bloqueada)

    data_emissao = st.date_input("Data de Emissão", value=date.today(), key="dt_emissao_os")
    observ = st.text_area("Observações (opcional)", key="obs_os")

    # ================== NOVO: lista de serviços da OBRA com preço da obra ==================
    with SessionLocal() as sess:
        servs_pairs = get_servicos_da_obra(sess, obra_sel.id)  # [(ObraServico, Servico)]
        if not servs_pairs:
            banner("warn", "Esta obra não possui serviços vinculados. Cadastre em Cadastro → Obras → 'Serviços desta obra'.")
            return
        _servs_exib = [{
            "srv_id": srv.id,
            "codigo": srv.codigo,
            "descricao": srv.descricao,
            "un": srv.unidade,
            "preco": float(osv.preco_unit or 0.0)
        } for (osv, srv) in servs_pairs]

    st.markdown("##### Itens da OS")
    c1, c2, c3, c4, c5 = st.columns([2, 3, 1, 1.2, 1.3])

    q_srv = c2.text_input("Buscar serviço (código/descrição)", placeholder="ex.: CP28 ou rompimento",
                          key="q_srv_os").strip().lower()

    servs_filtrados = [s for s in _servs_exib if q_srv in f"{s['codigo']} {s['descricao']}".lower()] if q_srv else _servs_exib
    serv_sel = c1.selectbox("Serviço da obra", servs_filtrados,
                            format_func=lambda sv: f"{sv['codigo']} — {sv['descricao']} (R$ {sv['preco']:.2f}/{sv['un']})",
                            key="srv_sel_os")

    qtd_prev = c3.number_input("Qtd.", min_value=0.0, step=1.0, value=0.0, key="qtd_prev_os")
    preco_vinc = c4.number_input("Preço (obra)", min_value=0.0, step=1.0, value=float(serv_sel["preco"]), key="preco_sel_os")
    subtotal_prev = qtd_prev * preco_vinc
    c5.markdown(f"<div class='card'><b>Subtotal</b><div style='font-size:1.2rem'>{format_brl(subtotal_prev)}</div></div>", unsafe_allow_html=True)

    st.session_state.setdefault("itens_os_tmp", [])
    if st.button("Adicionar", disabled=bloqueio_ativo, key="btn_add_item_os"):
        if qtd_prev <= 0:
            banner("error", "Informe uma quantidade > 0.")
        else:
            st.session_state["itens_os_tmp"].append((
                serv_sel["srv_id"], serv_sel["codigo"], serv_sel["descricao"],
                serv_sel["un"], float(qtd_prev), float(preco_vinc)
            ))
            flash("success", "Item adicionado.")

    if st.session_state["itens_os_tmp"]:
        df_it = pd.DataFrame(st.session_state["itens_os_tmp"],
                             columns=["servico_id", "Código", "Descrição", "Un", "Qtd Prevista", "Preço Unit. (obra)"])
        df_it["Subtotal"] = df_it["Qtd Prevista"] * df_it["Preço Unit. (obra)"]
        st.dataframe(df_it[["Código","Descrição","Un","Qtd Prevista","Preço Unit. (obra)","Subtotal"]], use_container_width=True)
        colA, colB = st.columns([1, 3])
        if colA.button("Limpar itens", key="btn_clear_itens_os"):
            st.session_state["itens_os_tmp"] = []
            flash("info", "Itens limpos.")
            _rerun()
        if colB.button("Gerar OS", disabled=bloqueio_ativo or not st.session_state["itens_os_tmp"], key="btn_emit_os"):
            if bloqueio_ativo:
                banner("error", "Cliente/Obra bloqueado — libere antes de emitir novas OS.")
            else:
                ok = False; sess = SessionLocal()
                try:
                    numero = gerar_numero_os(sess)
                    nova = OS(numero=numero, data_emissao=data_emissao, obra_id=obra_sel.id,
                              observacoes=(observ or None), status="Aberta")
                    sess.add(nova); sess.flush()
                    for (sid, _cod, _desc, _un, qtd, preco_snap) in st.session_state["itens_os_tmp"]:
                        sess.add(OSItem(os_id=nova.id, servico_id=sid,
                                        quantidade_prevista=(qtd or None),
                                        preco_unit=float(preco_snap)))  # snapshot
                    sess.commit(); ok = True
                except Exception:
                    sess.rollback(); ok = False
                finally:
                    sess.close()
                st.session_state["itens_os_tmp"] = []
                if ok:
                    flash("success", f"OS <b>{numero}</b> gerada com sucesso!")
                else:
                    flash("error", "OS não gerada por erro inesperado.")
                _rerun()
    else:
        banner("info", "Adicione itens para gerar a OS.")

@require_perm("os_view")
def page_visualizar_imprimir():
    st.markdown('<div class="section-title">Visualizar / Imprimir</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)

    with SessionLocal() as sess:
        os_df_full = to_df(sess, OS)
        if os_df_full.empty:
            banner("info", "Nenhuma OS emitida para visualizar.")
            st.markdown("</div>", unsafe_allow_html=True)
            return
        os_df_full["data_emissao"] = pd.to_datetime(os_df_full["data_emissao"], errors="coerce").dt.date
        obras_map = {o.id: f"{o.nome} — {o.endereco}" for o in sess.query(Obra).all()}
        os_df_full["obra_nome"] = os_df_full["obra_id"].map(lambda oid: obras_map.get(oid, f"Obra {oid}"))
        os_df_full["data_str"] = os_df_full["data_emissao"].apply(
            lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else ""
        )

    f1, f2, f3 = st.columns([2, 1, 1])
    obra_opcoes = ["(Todas)"] + sorted(os_df_full["obra_nome"].dropna().unique().tolist())
    obra_filtro = f1.selectbox("Filtrar por obra", obra_opcoes, key="flt_obra_print")
    status_opcoes = ["(Todos)"] + STATUS_OPTIONS
    status_filtro = f2.selectbox("Status", status_opcoes, key="flt_status_print")

    min_dt = os_df_full["data_emissao"].min()
    max_dt = os_df_full["data_emissao"].max()
    hoje = date.today()
    ini_default = min_dt or hoje
    fim_default = max_dt or hoje
    if ini_default > fim_default:
        ini_default, fim_default = fim_default, ini_default
    periodo = f3.date_input("Período", value=(ini_default, fim_default), key="flt_periodo_print")

    df_view = os_df_full.copy()
    if obra_filtro != "(Todas)":
        df_view = df_view[df_view["obra_nome"] == obra_filtro]
    if status_filtro != "(Todos)":
        df_view = df_view[df_view["status"] == status_filtro]

    ini, fim = (
        periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo)
    )
    df_view = df_view[(df_view["data_emissao"] >= ini) & (df_view["data_emissao"] <= fim)]
    df_view = df_view.sort_values(["data_emissao", "id"], ascending=[False, False]).reset_index(drop=True)

    q = st.text_input("Buscar por número da OS", placeholder="ex.: HAB-2025-0012", key="q_os_print").strip().upper()
    df_filt = df_view if not q else df_view[df_view["numero"].str.contains(q, case=False, na=False)]

    if df_filt.empty:
        banner("warn", "Nenhuma OS encontrada com os filtros/busca.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    df_filt["label"] = df_filt.apply(
        lambda r: f"{r['numero']} — {r['obra_nome']} — {r['data_str']} [{r['status']}]", axis=1
    )
    labels = df_filt["label"].tolist()

    if "os_idx" not in st.session_state or st.session_state.get("q_os_last") != q:
        st.session_state["os_idx"] = 0
    st.session_state["q_os_last"] = q

    cnav1, csel, cnav2 = st.columns([1, 4, 1])
    with cnav1:
        if st.button("Anterior", use_container_width=True, key="btn_prev_os"):
            st.session_state["os_idx"] = (st.session_state["os_idx"] - 1) % len(labels)
    with cnav2:
        if st.button("Próxima", use_container_width=True, key="btn_next_os"):
            st.session_state["os_idx"] = (st.session_state["os_idx"] + 1) % len(labels)

    escolha = csel.selectbox(
        "Selecione a OS para impressão",
        labels,
        index=min(st.session_state["os_idx"], len(labels) - 1),
        key="os_select_print",
    )
    st.session_state["os_idx"] = labels.index(escolha)

    row = df_filt.iloc[st.session_state["os_idx"]]
    with SessionLocal() as sess:
        os_row_db = sess.query(OS).filter(OS.id == int(row["id"])).first()
        if not os_row_db:
            banner("error", "OS não encontrada.")
            st.markdown("</div>", unsafe_allow_html=True)
            return
        os_row, obra_row, itens = obter_os_com_itens(sess, os_row_db.id)

    cH1, cH2 = st.columns([2, 1])
    with cH1:
        st.write(f"**OS:** {os_row.numero}")
        st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
        st.write(f"**Status:** {os_row.status}")
        st.write(f"**Obra:** {obra_row.nome}")
        st.write(f"**Endereço:** {obra_row.endereco}")
        with SessionLocal() as s2:
            cli = s2.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None
        st.write(f"**Cliente:** {(cli.nome if cli else (obra_row.cliente or '-'))}")
        if os_row.observacoes:
            st.write(f"**Observações:** {os_row.observacoes}")

    total = sum(it["subtotal"] for it in itens)
    with cH2:
        st.markdown(
            f'<div class="card"><b>Total estimado</b>'
            f'<div style="font-size:1.6rem;margin-top:.35rem">{format_brl(total)}</div></div>',
            unsafe_allow_html=True,
        )

    if itens:
        df_itens = pd.DataFrame(itens).rename(
            columns={
                "codigo": "Código",
                "descricao": "Descrição",
                "unidade": "Un",
                "qtd_prev": "Qtd Prevista",
                "preco_unit": "Preço Unit.",
                "subtotal": "Subtotal",
            }
        )
        st.dataframe(
            df_itens[["Código", "Descrição", "Un", "Qtd Prevista", "Preço Unit.", "Subtotal"]],
            use_container_width=True,
        )
    else:
        banner("info", "Esta OS ainda não possui itens.")

    logo_b = None
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, logo_bytes=logo_b)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, logo_bytes=logo_b)

    b1, b2 = st.columns(2)
    with b1:
        st.download_button(
            "Baixar PDF (interno — com preços)",
            data=pdf_interno,
            file_name=f"{os_row.numero}_interno.pdf",
            mime="application/pdf",
            key="dl_pdf_interno",
        )
    with b2:
        st.download_button(
            "Baixar PDF (cliente — sem preços)",
            data=pdf_cliente,
            file_name=f"{os_row.numero}_cliente.pdf",
            mime="application/pdf",
            key="dl_pdf_cliente",
        )

    st.markdown("</div>", unsafe_allow_html=True)

@require_perm("relatorios_export")
def page_medicao():
    st.markdown('<div class="section-title">Medição Mensal</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.execute(select(Obra).where(Obra.ativo == 1).order_by(Obra.nome.asc())).scalars().all()
    if not obras:
        banner("info", "Cadastre obras para usar a medição mensal."); return

    obra_sel = st.selectbox("Obra", obras, format_func=lambda o: f"{o.nome} — {o.endereco}", key="obra_medicao_sel")

    try:
        with SessionLocal() as sess:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao))
                                  .filter(OS.obra_id == obra_sel.id, OS.status == "Medido")
                                  .scalar())
            if ultima_medida_dt:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id, OS.status == "Aberta", OS.data_emissao > ultima_medida_dt)
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())
            else:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == obra_sel.id, OS.status == "Aberta")
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())
        if os_ref and os_ref.data_emissao:
            dias = (date.today() - os_ref.data_emissao).days
            msg = ("Medição em atraso" if dias >= 30 else "Medição em dia")
            banner("info", f"{msg}: OS <b>{os_ref.numero}</b> em Aberto há <b>{dias}</b> dias.")
    except Exception:
        pass

    cliente_bloqueado = False
    with SessionLocal() as scli:
        ob = scli.get(Obra, obra_sel.id)
        cli = scli.get(Cliente, ob.cliente_id) if ob and ob.cliente_id else None
        if cli and cli.bloqueado:
            cliente_bloqueado = True
            motivo = cli.bloqueado_motivo or "Sem motivo informado"
            desde = cli.bloqueado_desde.strftime("%d/%m/%Y") if cli.bloqueado_desde else "-"
            banner("warn", f"Cliente bloqueado desde {desde}. Pode gerar PDF, mas não gravar status. Motivo: {motivo}")

    try:
        with SessionLocal() as sess:
            ultimo_num = sess.query(func.max(Medicao.numero)).filter(Medicao.obra_id == obra_sel.id).scalar()
    except Exception:
        ultimo_num = 0
    medicao_num = st.number_input("Número da medição", min_value=1, step=1, value=int((ultimo_num or 0) + 1), key="med_num")

    hoje = date.today()
    primeiro_dia = date(hoje.year, hoje.month, 1)
    ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    periodo = st.date_input("Período da medição", value=(primeiro_dia, ultimo_dia), key="med_periodo")
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))

    st.markdown("#### Filtros")
    col_fs1, col_fs2 = st.columns([1, 1])
    with col_fs1:
        status_listagem = st.selectbox("Status das OS a listar", ["(Todos)"] + STATUS_OPTIONS, index=0, key="med_status_list")
    with col_fs2:
        status_aplicar = st.selectbox("Status para aplicar em massa", STATUS_OPTIONS,
                                      index=STATUS_OPTIONS.index("Medido") if "Medido" in STATUS_OPTIONS else 0, key="med_status_apply")

    with SessionLocal() as sess:
        q = (sess.query(OS, OSItem, Servico, Obra)
             .join(OSItem, OSItem.os_id == OS.id)
             .join(Servico, Servico.id == OSItem.servico_id)
             .join(Obra, Obra.id == OS.obra_id)
             .filter(OS.obra_id == obra_sel.id)
             .filter(OS.data_emissao >= ini, OS.data_emissao <= fim))
        if status_listagem != "(Todos)":
            q = q.filter(OS.status == status_listagem)
        q = q.order_by(OS.data_emissao.asc(), OS.numero.asc(), Servico.codigo.asc())
        rows = q.all()

    linhas = []
    for os_row, it, sv, ob in rows:
        preco_snap = (it.preco_unit if getattr(it, "preco_unit", None) is not None else (sv.preco_unit or 0.0))
        linhas.append({
            "data": os_row.data_emissao, "os_num": os_row.numero, "status": os_row.status,
            "codigo": sv.codigo, "descricao": sv.descricao, "un": sv.unidade,
            "qtd": (it.quantidade_prevista or 0.0), "preco": preco_snap,
            "subtotal": preco_snap * (it.quantidade_prevista or 0.0),
        })

    st.markdown("#### Itens do período (após filtros)")
    if not linhas:
        banner("info", "Não há itens para as condições selecionadas.")
    else:
        df = pd.DataFrame(linhas); total = df["subtotal"].sum()
        col_tbl, col_total = st.columns([4,1])
        with col_tbl:
            st.dataframe(
                df.assign(
                    data=df["data"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else d),
                    preco=df["preco"].apply(format_brl),
                    subtotal=df["subtotal"].apply(format_brl)
                ),
                use_container_width=True
            )
        with col_total:
            st.markdown('<div class="card"><b>Total do período</b>'
                        f'<div style="font-size:1.6rem;margin-top:.35rem">{format_brl(total)}</div></div>', unsafe_allow_html=True)

        period_text = f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"

        c1, c2, _ = st.columns([1,1,2])
        with c1:
            pdf = gerar_pdf_medicao(obra_sel.nome, period_text, linhas, logo_bytes=None, medicao_num=int(medicao_num))
            st.download_button("Gerar PDF da Medição", data=pdf,
                               file_name=f"medicao_{obra_sel.id}_{ini}_{fim}.pdf", mime="application/pdf",
                               key="dl_pdf_medicao")
        with c2:
            btn_label = f"Aplicar status '{status_aplicar}' a todas as OS do período"
            if st.button(btn_label, disabled=cliente_bloqueado, key="btn_apply_status_med"):
                if cliente_bloqueado:
                    banner("error", "Cliente bloqueado — libere antes de atualizar o status.")
                else:
                    with SessionLocal() as sess:
                        sess.query(OS).filter(
                            OS.obra_id == obra_sel.id,
                            OS.data_emissao >= ini, OS.data_emissao <= fim
                        ).update({OS.status: status_aplicar}, synchronize_session="fetch")
                        sess.add(Medicao(obra_id=obra_sel.id, numero=int(medicao_num),
                                         periodo_ini=ini, periodo_fim=fim, criado_em=date.today()))
                        sess.commit()
                    flash("success", f"Todas as OS do período foram marcadas como '{status_aplicar}'.")
                    _rerun()

@require_perm("relatorios_export")
def page_relatorios():
    st.markdown('<div class="section-title">Relatórios por Cliente</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.execute(select(Cliente).where(Cliente.ativo == 1).order_by(Cliente.nome.asc())).scalars().all()
    if not clientes:
        banner("info", "Cadastre clientes para usar os relatórios."); return

    cliente_sel = st.selectbox("Cliente", clientes, format_func=lambda c: c.nome, key="rel_cli_sel")
    if bool(getattr(cliente_sel, "bloqueado", 0)):
        motivo = cliente_sel.bloqueado_motivo or "Sem motivo informado"
        desde = cliente_sel.bloqueado_desde.strftime("%d/%m/%Y") if cliente_sel.bloqueado_desde else "-"
        banner("warn", f"Cliente bloqueado desde {desde}. Relatórios continuam disponíveis. Motivo: {motivo}")

    hoje = date.today()
    primeiro_dia = date(hoje.year, hoje.month, 1)
    ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    periodo = st.date_input("Período", value=(primeiro_dia, ultimo_dia), key="rel_periodo")
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))

    status_opt = ["(Todos)"] + STATUS_OPTIONS
    status_filtro = st.selectbox("Filtrar por status das OS", status_opt, index=0, key="rel_status")

    with SessionLocal() as sess:
        obras_cliente = sess.execute(
            select(Obra).where(
                (Obra.cliente_id == cliente_sel.id) | (func.trim(func.coalesce(Obra.cliente, "")) == cliente_sel.nome)
            ).order_by(Obra.nome.asc())
        ).scalars().all()

    if not obras_cliente:
        banner("warn", "Não há obras vinculadas a este cliente."); return

    resumo_status = []
    with SessionLocal() as sess:
        for ob in obras_cliente:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao))
                                  .filter(OS.obra_id == ob.id, OS.status == "Medido")
                                  .scalar())
            if ultima_medida_dt:
                os_ref = (sess.query(OS)
                            .filter(OS.obra_id == ob.id, OS.status == "Aberta", OS.data_emissao > ultima_medida_dt)
                            .order_by(OS.data_emissao.asc(), OS.id.asc())
                            .first())
            else:
                os_ref = (sess.query(OS).filter(OS.obra_id == ob.id, OS.status == "Aberta")
                          .order_by(OS.data_emissao.asc(), OS.id.asc()).first())
            if os_ref and os_ref.data_emissao:
                dias = (date.today() - os_ref.data_emissao).days
                status_txt = "Medição em atraso" if dias >= 30 else "Medição em dia"
                resumo_status.append({
                    "Obra": ob.nome, "Endereço": ob.endereco,
                    "OS (referência)": os_ref.numero, "Emissão": os_ref.data_emissao.strftime("%d/%m/%Y"),
                    "Dias": dias, "Status de Medição": status_txt
                })
            else:
                resumo_status.append({
                    "Obra": ob.nome, "Endereço": ob.endereco,
                    "OS (referência)": "-", "Emissão": "-", "Dias": "-",
                    "Status de Medição": "Medição em dia"
                })
    st.markdown("#### Status de Medição por Obra")
    df_status = pd.DataFrame(resumo_status)
    if not df_status.empty:
        def _ord(v): return 0 if v == "Medição em atraso" else 1
        df_status = df_status.sort_values(
            ["Status de Medição", "Obra"],
            key=lambda s: s.map(_ord) if s.name == "Status de Medição" else s
        )
        st.dataframe(df_status, use_container_width=True, hide_index=True)
    else:
        banner("info", "Sem obras vinculadas ao cliente.")

    with SessionLocal() as sess:
        obra_ids = [o.id for o in obras_cliente]
        if not obra_ids:
            banner("warn", "Não há obras vinculadas a este cliente."); return
        q = (sess.query(OS, OSItem, Servico, Obra)
             .join(OSItem, OSItem.os_id == OS.id)
             .join(Servico, Servico.id == OSItem.servico_id)
             .join(Obra, Obra.id == OS.obra_id)
             .filter(OS.obra_id.in_(obra_ids))
             .filter(OS.data_emissao >= ini, OS.data_emissao <= fim))
        if status_filtro != "(Todos)":
            q = q.filter(OS.status == status_filtro)
        q = q.order_by(OS.data_emissao.asc(), Obra.nome.asc(), OS.numero.asc(), Servico.codigo.asc())
        rows = q.all()

    linhas = []
    for os_row, it, sv, ob in rows:
        preco_snap = (it.preco_unit if getattr(it, "preco_unit", None) is not None else (sv.preco_unit or 0.0))
        linhas.append({
            "data": os_row.data_emissao, "obra": ob.nome, "os_num": os_row.numero,
            "codigo": sv.codigo, "descricao": sv.descricao, "un": sv.unidade,
            "qtd": (it.quantidade_prevista or 0.0), "preco": preco_snap,
            "subtotal": preco_snap * (it.quantidade_prevista or 0.0),
        })

    st.markdown("#### Fechamento detalhado")
    if not linhas:
        banner("info", "Nenhum item encontrado para os filtros informados.")
    else:
        df = pd.DataFrame(linhas); total = df["subtotal"].sum()
        col_tbl, col_total = st.columns([4, 1])
        with col_tbl:
            st.dataframe(df.assign(
                data=df["data"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else d),
                preco=df["preco"].apply(format_brl),
                subtotal=df["subtotal"].apply(format_brl)
            ), use_container_width=True)
        with col_total:
            st.markdown('<div class="card"><b>Total geral</b>'
                        f'<div style="font-size:1.6rem;margin-top:.35rem">{format_brl(total)}</div></div>', unsafe_allow_html=True)

        periodo_texto = f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
        pdf = gerar_pdf_fechamento(cliente_sel.nome, periodo_texto, linhas, logo_bytes=None)
        st.download_button("Imprimir fechamento (PDF)", data=pdf,
                           file_name=f"fechamento_{cliente_sel.nome}_{ini}_{fim}.pdf",
                           mime="application/pdf", key="dl_pdf_fechamento")

@require_perm("relatorios_export")
def page_export():
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)
    with st.expander("Backup (DB + anexos)", expanded=False):
        if st.button("Gerar backup ZIP", key="btn_backup_zip"):
            p = make_full_backup()
            with p.open("rb") as f:
                st.download_button("Baixar backup", data=f.read(), file_name=p.name, mime="application/zip", key="dl_backup_zip")

# ===================== MENU / ROUTER =====================
st.sidebar.markdown("###  Sistema OS")
st.sidebar.markdown(
    """
<div class="hb-side-title">
  <span class="hb-dot"></span>
  <span>Navegação</span>
</div>
""",
    unsafe_allow_html=True,
)
MENU = [
    "Emitir OS",
    "Cadastro: Clientes",
    "Cadastro: Obras",
    "Cadastro: Serviços",
    "Visualizar / Imprimir",
    "Medição Mensal",
    "Relatórios",
    "Exportações",
]
page = st.sidebar.radio("Ir para", MENU, index=0, label_visibility="collapsed", key="router_menu")

def _has(perm: str)->bool:
    return has_perm(s.get("username",""), s.get("role","usuario"), perm) or s.get("is_admin", False)

def main_router():
    flash_render()
    if page == "Cadastro: Clientes":
        if _has("relatorios_export"): page_clientes()
        else: banner("error", "Sem permissão (relatorios_export).")
    elif page == "Cadastro: Obras":
        if _has("relatorios_export"): page_obras()
        else: banner("error", "Sem permissão (relatorios_export).")
    elif page == "Cadastro: Serviços":
        if _has("relatorios_export"): page_servicos()
        else: banner("error", "Sem permissão (relatorios_export).")
    elif page == "Visualizar / Imprimir":
        if _has("os_view"): page_visualizar_imprimir()
        else: banner("error", "Sem permissão (os_view).")
    elif page == "Medição Mensal":
        if _has("relatorios_export"): page_medicao()
        else: banner("error", "Sem permissão (relatorios_export).")
    elif page == "Relatórios":
        if _has("relatorios_export"): page_relatorios()
        else: banner("error", "Sem permissão (relatorios_export).")
    elif page == "Exportações":
        if _has("relatorios_export"): page_export()
        else: banner("error", "Sem permissão (relatorios_export).")
    else:
        if _has("dashboard_view") or _has("os_create"):
            page_emitir_os()
        else:
            banner("error", "Sem permissão (dashboard_view).")

# ====== Entry point ======
main_router()
