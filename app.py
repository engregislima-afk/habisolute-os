# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)
# Visual Fluent/Windows 11 + banners + avisos + medição em dias

import io, re, os, json, base64, tempfile, zipfile, hashlib, hmac, secrets, calendar
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
import xml.sax.saxutils as saxutils  # <- para montar o XLSX sem libs externas

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
# CSS — Windows 11 / Fluent (com acentos laranja)
# =============================================================================
def _inject_css(theme: str | None = None):
    mode = (theme or st.session_state.get("theme_mode") or "Claro").strip().lower()

    if mode == "claro":
        HB_BG, HB_CARD, HB_BORDER, HB_TEXT, HB_MUTED, HB_GLASS = "#f7f8fb", "#ffffff", "#e6e9f2", "#0f1116", "#475069", "rgba(0,0,0,.04)"
    else:
        HB_BG, HB_CARD, HB_BORDER, HB_TEXT, HB_MUTED, HB_GLASS = "#0f1116", "#141821", "#2a3142", "#f5f7fb", "#c9d2e4", "rgba(255,255,255,.06)"

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
html, body, [data-testid="stAppViewContainer"] {{ background: var(--hb-bg)!important; color: var(--hb-text)!important; }}
[data-testid="stSidebar"] {{ background: linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02)) !important; border-right: 1px solid var(--hb-border); backdrop-filter: blur(10px);}}
[data-testid="stSidebar"] .sidebar-content, [data-testid="stSidebar"] * {{ color: var(--hb-text) !important; }}
.hb-side-title {{ display:flex; align-items:center; gap:.5rem; margin:.25rem 0 1rem 0; font-weight:800; }}
.hb-dot {{ width:10px; height:10px; border-radius:999px; background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2)); box-shadow:0 0 10px rgba(249,115,22,.55);}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label {{ position:relative; display:flex; align-items:center; gap:.6rem; padding:.55rem .75rem; border-radius:14px; border:1px solid transparent; background: rgba(255,255,255,.03); transition:all .15s ease; margin:.15rem 0; cursor:pointer;}}
[data-testid="stSidebar"] .stRadio input[type="radio"]{{opacity:0; position:absolute; left:-9999px;}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label::before{{ content:""; width:10px; height:10px; border-radius:999px; background:rgba(255,255,255,.22); box-shadow: inset 0 0 0 1px rgba(255,255,255,.15); flex: 0 0 auto; }}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:hover{{ background: rgba(255,255,255,.07); border-color: rgba(255,255,255,.10);}}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div{{ color:#0b0e14!important; background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2)); border:0!important; box-shadow:0 6px 26px rgba(249,115,22,.28); font-weight:800; border-radius:14px; padding:.55rem .75rem;}}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div::before{{ content:""; width:10px; height:10px; border-radius:999px; background:#0b0e14; box-shadow:0 0 0 3px rgba(0,0,0,.15); margin-right:.1rem;}}
.card{{ background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)); border:1px solid var(--hb-border); border-radius:18px; padding:16px; margin-bottom:14px; box-shadow:0 6px 28px rgba(0,0,0,.10), inset 0 1px 0 rgba(255,255,255,.03);}}
.section-title{{ background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2)); color:#111; font-weight:800; text-align:center; padding:.6rem .8rem; border-radius:12px; margin:0 0 12px 0;}}
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input{{ color:var(--hb-text)!important; background:transparent!important; border:1px solid var(--hb-border)!important; border-radius:12px!important;}}
div[data-baseweb="select"] input, div[data-baseweb="select"] span {{ color:var(--hb-text)!important; }}
label, .stMarkdown p, .block-label {{ color: var(--hb-text)!important; }}
.stButton>button, .stDownloadButton>button {{ background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2)); color:#111!important; font-weight:800; border:0; border-radius:12px; padding:.55rem 1rem;}}
.stButton>button:hover, .stDownloadButton>button:hover {{ filter: brightness(1.05); }}
.hb-banner {{ display:flex; gap:10px; align-items:center; padding:.75rem 1rem; border-radius:14px; border:1px solid var(--hb-border); margin:.25rem 0 .75rem 0; background: var(--hb-glass);}}
.hb-banner .title {{ font-weight:800; }}
.hb-banner.info    {{ border-left:6px solid #60a5fa; }}
.hb-banner.warn    {{ border-left:6px solid #facc15; }}
.hb-banner.success {{ border-left:6px solid #22c55e; }}
.hb-banner.error   {{ border-left:6px solid #ef4444; }}
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
    q = st.session_state.get("_flash", [])
    q.append({"k": (kind or "info"), "t": text or "", "b": button})
    st.session_state["_flash"] = q

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

# Gate inicial de login
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
papel = "Usuário"
st.markdown(
    f"<div class='card'>👋 Olá, <b>{nome_login}</b> — <span style='opacity:.9'>{papel}</span></div>",
    unsafe_allow_html=True
)

# Toolbar topo
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

if "theme_prev" not in s:
    s["theme_prev"] = s["theme_mode"]

if s["theme_mode"] != s["theme_prev"]:
    prefs = load_user_prefs()
    prefs["theme_mode"] = s["theme_mode"]
    save_user_prefs(prefs)
    s["theme_prev"] = s["theme_mode"]
    _rerun()

# =============================================================================
# DB (SQLite)
# =============================================================================
Base = declarative_base()

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

class ObraServico(Base):
    __tablename__ = "obra_servicos"
    id = Column(Integer, primary_key=True)
    obra_id = Column(Integer, ForeignKey("obras.id"), nullable=False, index=True)
    servico_id = Column(Integer, ForeignKey("servicos.id"), nullable=False, index=True)
    preco_unit = Column(Float)
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
    preco_unit = Column(Float)  # snapshot
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

def _create_index_safe(conn, stmt: str):
    try:
        conn.exec_driver_sql(stmt)
    except Exception as e:
        print(f"[WARN] Index creation skipped: {stmt} -> {e}")

with engine.begin() as conn:
    _create_index_safe(conn, "PRAGMA journal_mode=WAL;")
    _create_index_safe(conn, "PRAGMA synchronous=NORMAL;")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_os_obra_data ON os(obra_id, data_emissao);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_os_status ON os(status);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_os_numero ON os(numero);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_ositem_osid ON os_itens(os_id);")
    _create_index_safe(conn, "CREATE INDEX IF NOT EXISTS ix_medicoes_obra ON medicoes(obra_id);")
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
            conn.exec_driver_sql("""CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, salt TEXT, pw_hash TEXT, is_active INTEGER DEFAULT 1)""")
        else:
            cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('users')").fetchall()}
            if "salt" not in cols: conn.exec_driver_sql("ALTER TABLE users ADD COLUMN salt TEXT")
            if "pw_hash" not in cols: conn.exec_driver_sql("ALTER TABLE users ADD COLUMN pw_hash TEXT")
            if "is_active" not in cols: conn.exec_driver_sql("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1")

        row = conn.exec_driver_sql("SELECT id, salt, pw_hash FROM users WHERE username='admin'").fetchone()
        if row is None:
            salt_hex, h_hex = _hash_password("admin")
            conn.exec_driver_sql("INSERT INTO users (username, salt, pw_hash, is_active) VALUES (?, ?, ?, 1)", ("admin", salt_hex, h_hex))
        else:
            uid, salt_hex, pw_hex = row
            if not salt_hex or not pw_hex:
                salt_hex, h_hex = _hash_password("admin")
                conn.exec_driver_sql("UPDATE users SET salt=?, pw_hash=?, is_active=1 WHERE id=?", (salt_hex, h_hex, uid))
        orphan_ids = conn.exec_driver_sql(
            "SELECT id FROM users WHERE (salt IS NULL OR TRIM(COALESCE(salt,''))='') OR (pw_hash IS NULL OR TRIM(COALESCE(pw_hash,''))='')"
        ).fetchall()
        if orphan_ids:
            conn.exec_driver_sql("UPDATE users SET is_active=0 WHERE id IN (%s)" % ",".join(str(r[0]) for r in orphan_ids))

def _ensure_obra_servicos_schema_and_indexes(engine):
    with engine.begin() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "obra_servicos" not in tables:
            conn.exec_driver_sql("""CREATE TABLE obra_servicos (id INTEGER PRIMARY KEY, obra_id INTEGER NOT NULL, servico_id INTEGER NOT NULL, preco_unit REAL, ativo INTEGER DEFAULT 1, FOREIGN KEY(obra_id) REFERENCES obras(id), FOREIGN KEY(servico_id) REFERENCES servicos(id))""")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_obraserv_obra ON obra_servicos(obra_id)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_obraserv_srv  ON obra_servicos(servico_id)")
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('os_itens')").fetchall()}
        if "preco_unit" not in cols:
            conn.exec_driver_sql("ALTER TABLE os_itens ADD COLUMN preco_unit REAL")
            conn.exec_driver_sql("""UPDATE os_itens SET preco_unit = (SELECT preco_unit FROM servicos s WHERE s.id = os_itens.servico_id) WHERE preco_unit IS NULL""")

def _ensure_obras_core_columns(engine):
    with engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info('obras')").fetchall()}
        if "ativo" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN ativo INTEGER DEFAULT 1")
        if "cliente_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN cliente_id INTEGER")
        if "bloqueada" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada INTEGER DEFAULT 0")
        if "bloqueada_motivo" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada_motivo TEXT")
        if "bloqueada_desde" not in cols:
            conn.exec_driver_sql("ALTER TABLE obras ADD COLUMN bloqueada_desde DATE")

_ensure_medicoes_schema(engine)
_ensure_clientes_schema_and_backfill(engine)
_ensure_obras_attachments(engine)
_ensure_users_schema_and_default(engine)
_ensure_obra_servicos_schema_and_indexes(engine)
_ensure_obras_core_columns(engine)

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
    ultimo = (sess.execute(select(OS).where(OS.numero.like(f"{prefix}%")).order_by(OS.id.desc())).scalars().first())
    seq = 1
    if ultimo:
        try: seq = int(str(ultimo.numero).split("-")[-1]) + 1
        except: seq = (ultimo.id or 0) + 1
    return f"{prefix}{seq:04d}"

def format_brl(v: float) -> str:
    try: return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception: return "R$ 0,00"

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

# ========= NOVO: geração de Excel sem depender de openpyxl/xlsxwriter =========
def _colnum_to_xlsx_col(n: int) -> str:
    """1 -> A, 2 -> B, 27 -> AA ..."""
    res = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        res = chr(65 + rem) + res
    return res

def _xlsx_from_frames(frames: Dict[str, pd.DataFrame]) -> bytes:
    """
    Gera um .xlsx mínimo com 1 aba por frame usando só stdlib.
    """
    from zipfile import ZipFile, ZIP_DEFLATED
    import datetime as _dt
    out = io.BytesIO()
    sheet_names = list(frames.keys())

    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        # [Content_Types].xml
        content_types = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
            '  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
            '  <Default Extension="xml" ContentType="application/xml"/>',
            '  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
            '  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        ]
        for i, _ in enumerate(sheet_names, start=1):
            content_types.append(
                f'  <Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        content_types.append("</Types>")
        z.writestr("[Content_Types].xml", "\n".join(content_types))

        # _rels/.rels
        rels = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            '  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>',
            "</Relationships>",
        ]
        z.writestr("_rels/.rels", "\n".join(rels))

        # xl/workbook.xml
        sheets_xml = []
        for i, name in enumerate(sheet_names, start=1):
            safe_name = saxutils.escape(name[:31] or f"Sheet{i}")
            sheets_xml.append(f'    <sheet name="{safe_name}" sheetId="{i}" r:id="rId{i}"/>')

        workbook = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
            "  <sheets>",
            *sheets_xml,
            "  </sheets>",
            "</workbook>",
        ]
        z.writestr("xl/workbook.xml", "\n".join(workbook))

        # xl/_rels/workbook.xml.rels
        wb_rels = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        ]
        for i, _ in enumerate(sheet_names, start=1):
            wb_rels.append(
                f'  <Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            )
        wb_rels.append("</Relationships>")
        z.writestr("xl/_rels/workbook.xml.rels", "\n".join(wb_rels))

        # xl/styles.xml mínimo
        styles = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
            "  <fonts count=\"1\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts>",
            "  <fills count=\"1\"><fill><patternFill patternType=\"none\"/></fill></fills>",
            "  <borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders>",
            "  <cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>",
            "  <cellXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyNumberFormat=\"0\"/></cellXfs>",
            "  <cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles>",
            "</styleSheet>",
        ]
        z.writestr("xl/styles.xml", "\n".join(styles))

        # Worksheets
        for idx, name in enumerate(sheet_names, start=1):
            df = frames[name]
            cols = list(df.columns)
            rows_xml = []

            # cabeçalho
            header_cells = []
            for col_i, col_name in enumerate(cols, start=1):
                col_letter = _colnum_to_xlsx_col(col_i)
                header_cells.append(
                    f'<c r="{col_letter}1" t="inlineStr"><is><t>{saxutils.escape(str(col_name))}</t></is></c>'
                )
            rows_xml.append(f'<row r="1">{"".join(header_cells)}</row>')

            # dados
            for r_i, (_, row) in enumerate(df.iterrows(), start=2):
                cells = []
                for c_i, col_name in enumerate(cols, start=1):
                    col_letter = _colnum_to_xlsx_col(c_i)
                    val = row[col_name]
                    if val is None:
                        text = ""
                    elif isinstance(val, (_dt.date, _dt.datetime)):
                        text = val.strftime("%d/%m/%Y")
                    else:
                        text = str(val)
                    cells.append(
                        f'<c r="{col_letter}{r_i}" t="inlineStr"><is><t>{saxutils.escape(text)}</t></is></c>'
                    )
                rows_xml.append(f'<row r="{r_i}">{"".join(cells)}</row>')

            sheet_xml = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
                "  <sheetData>",
                *rows_xml,
                "  </sheetData>",
                "</worksheet>",
            ]
            z.writestr(f"xl/worksheets/sheet{idx}.xml", "\n".join(sheet_xml))

    out.seek(0)
    return out.getvalue()

def make_os_excel_per_obras() -> bytes:
    """
    Monta 1 arquivo Excel com 1 aba por obra, usando apenas stdlib.
    Cada linha: OS + data + status + item.
    """
    with SessionLocal() as sess:
        obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
        frames: Dict[str, pd.DataFrame] = {}

        for obra in obras:
            os_list = (
                sess.query(OS)
                .filter(OS.obra_id == obra.id)
                .order_by(OS.data_emissao.asc(), OS.id.asc())
                .all()
            )

            rows = []
            for os_row in os_list:
                itens = (
                    sess.query(OSItem)
                    .join(Servico, Servico.id == OSItem.servico_id)
                    .filter(OSItem.os_id == os_row.id)
                    .order_by(Servico.codigo.asc())
                    .all()
                )
                if itens:
                    for it in itens:
                        srv = it.servico
                        preco = (
                            it.preco_unit
                            if getattr(it, "preco_unit", None) is not None
                            else (srv.preco_unit or 0.0)
                        )
                        rows.append(
                            {
                                "OS": os_row.numero,
                                "Data emissão": os_row.data_emissao,
                                "Status": os_row.status,
                                "Código serviço": srv.codigo,
                                "Descrição serviço": srv.descricao,
                                "Un": srv.unidade,
                                "Qtd prevista": it.quantidade_prevista or 0.0,
                                "Preço unit. (snapshot)": preco,
                                "Subtotal": (it.quantidade_prevista or 0.0) * float(preco or 0.0),
                                "Observações OS": os_row.observacoes or "",
                            }
                        )
                else:
                    rows.append(
                        {
                            "OS": os_row.numero,
                            "Data emissão": os_row.data_emissao,
                            "Status": os_row.status,
                            "Código serviço": "",
                            "Descrição serviço": "",
                            "Un": "",
                            "Qtd prevista": 0,
                            "Preço unit. (snapshot)": 0,
                            "Subtotal": 0,
                            "Observações OS": os_row.observacoes or "",
                        }
                    )

            df_obra = pd.DataFrame(rows)
            if df_obra.empty:
                df_obra = pd.DataFrame({"msg": ["sem OS para esta obra"]})

            sheet_name = (obra.nome or f"obra_{obra.id}").strip()[:31]
            if not sheet_name:
                sheet_name = f"obra_{obra.id}"
            sheet_name = sheet_name.replace("/", "_").replace("\\", "_").replace(":", "_")

            frames[sheet_name] = df_obra

    return _xlsx_from_frames(frames)

# =============================================================================
# Anexos de obras
# =============================================================================
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
    if not p.is_absolute(): p = (BASE_DIR / p).resolve()
    return (p.exists() and p.is_file(), p.name)
# =============================================================================
# PDFs
# =============================================================================
styles = getSampleStyleSheet()
styleN = styles["BodyText"]
styleSmall = ParagraphStyle("small", parent=styleN, fontSize=9, leading=11)
styleTiny  = ParagraphStyle("tiny",  parent=styleN, fontSize=8, leading=10)
HB_ORANGE = colors.HexColor("#FF7A00")
FORM_CODE = "FORM.H.012.00"

def _header_vertical_centralizado() -> list:
    p1 = Paragraph("<b>Habisolute Engenharia e Controle Tecnológico</b>", ParagraphStyle("hdr1", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))
    p2 = Paragraph("contato@habisoluteengenharia.com.br", ParagraphStyle("hdr2", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    p3 = Paragraph("(16) 3877-9480", ParagraphStyle("hdr3", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    p4 = Paragraph(FORM_CODE, ParagraphStyle("hdr4", parent=styleN, fontSize=9, leading=11, alignment=TA_CENTER))
    box = Table([[p1],[p2],[p3],[p4]], colWidths=[180*mm])
    box.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0), ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0)]))
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

    info_tbl = Table([[Paragraph(f"<b>Status:</b> {os_row.status}", styleSmall)],
                      [Paragraph(f"<b>Obra:</b> {obra_row.nome}", styleSmall)],
                      [Paragraph(f"<b>Endereço:</b> {obra_row.endereco}", styleSmall)],
                      [Paragraph(f"<b>Cliente:</b> {cli.nome if cli else (obra_row.cliente or '-')}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                  ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
                                  ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story += [info_tbl, Spacer(1, 6)]

    titulo_os = f"ORDEM DE SERVIÇO Nº {os_row.numero}    DATA: {os_row.data_emissao.strftime('%d/%m/%Y')}"
    tit_tbl = Table([[Paragraph(f"<b>{titulo_os}</b>", ParagraphStyle('titOS', parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")),
                                 ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
                                 ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                 ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
                                 ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8)]))
    story += [tit_tbl, Spacer(1, 8)]

    headers = ["Código", "Descrição", "Un", "Qtd"]
    if show_prices: headers += ["Preço Unit", "Sub Total"]
    data_rows = [headers]

    for it in itens:
        row = [it["codigo"], it["descricao"], it["unidade"], f"{it['qtd_prev']:.2f}"]
        if show_prices: row += [format_brl(it["preco_unit"]), format_brl(it["subtotal"])]
        data_rows.append(row)

    W = doc.width
    col_widths = [0.16*W, 0.44*W, 0.06*W, 0.10*W, 0.12*W, 0.12*W] if show_prices else [0.18*W, 0.56*W, 0.08*W, 0.18*W]

    total_val = sum(it["subtotal"] for it in itens) if (show_prices and itens) else 0.0
    total_row_index = None
    if show_prices:
        fillers = [""] * (len(headers) - 2)
        data_rows.append(fillers + ["Total:", format_brl(total_val)])
        total_row_index = len(data_rows) - 1

    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.black), ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                             ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.25, colors.black),
                             ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
                             ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
                             ("ALIGN", (0,1), (1,-1), "LEFT"), ("ALIGN", (2,1), (2,-1), "CENTER"), ("ALIGN", (3,1), (3,-1), "RIGHT")]))
    if show_prices: tbl.setStyle(TableStyle([("ALIGN", (-2,1), (-1,-1), "RIGHT")]))
    if show_prices and total_row_index is not None:
        last_label_col = len(headers) - 2
        last_value_col = len(headers) - 1
        tbl.setStyle(TableStyle([("BACKGROUND", (0,total_row_index), (-1,total_row_index), colors.HexColor("#f5f5f5")),
                                 ("FONTNAME", (last_label_col,total_row_index), (last_label_col,total_row_index), "Helvetica-Bold"),
                                 ("FONTNAME", (last_value_col,total_row_index), (last_value_col,total_row_index), "Helvetica-Bold"),
                                 ("ALIGN", (last_value_col,total_row_index), (last_value_col,total_row_index), "RIGHT"),
                                 ("SPAN", (0,total_row_index), (last_label_col-1,total_row_index))]))
    story.append(tbl)
    story += [Spacer(1, 24), Paragraph("Data: ____/____/______", ParagraphStyle("dt", parent=styleN, fontSize=10, alignment=TA_CENTER)), Spacer(1, 22)]
    ass_tbl = Table([["", "_______________________________", "", "_______________________________", ""],
                     ["", "Assinatura Cliente", "", "Assinatura Laboratorista", ""]], colWidths=[10*mm, 70*mm, 15*mm, 70*mm, 10*mm])
    ass_tbl.setStyle(TableStyle([("ALIGN",(1,0),(1,0),"CENTER"), ("ALIGN",(3,0),(3,0),"CENTER"),
                                 ("ALIGN",(1,1),(1,1),"CENTER"), ("ALIGN",(3,1),(3,1),"CENTER"),
                                 ("TOPPADDING",(0,1),(-1,1),2), ("BOTTOMPADDING",(0,0),(-1,-1),0),
                                 ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story.append(ass_tbl)
    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,""), onLaterPages=lambda c,d:_on_page(c,d,""))
    return buf.getvalue()

def gerar_pdf_medicao(obra_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None, medicao_num: int) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()
    info_tbl = Table([[Paragraph(f"<b>Obra:</b> {obra_nome}", styleSmall)],
                      [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                  ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
                                  ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story += [info_tbl, Spacer(1, 6)]
    titulo = f"RELATÓRIO DE MEDIÇÃO — Medição nº {medicao_num}"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle("titMED", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")), ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
                                 ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                 ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
                                 ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),8)]))
    story += [tit_tbl, Spacer(1, 8)]
    headers = ["Data", "OS", "Código", "Descrição", "Un", "Qtd", "Preço", "Subtotal"]
    data_rows = [headers]
    for r in linhas:
        data_rows.append([r["data"].strftime("%d/%m/%Y") if isinstance(r["data"], date) else r["data"],
                          r["os_num"], r["codigo"], r["descricao"], r["un"],
                          f"{r['qtd']:.2f}", format_brl(r["preco"]), format_brl(r["subtotal"])])


    W = doc.width
    col_widths = [0.09*W, 0.14*W, 0.12*W, 0.31*W, 0.06*W, 0.08*W, 0.10*W, 0.10*W]
    tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.black), ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
                             ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.25, colors.black),
                             ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
                             ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
                             ("ALIGN", (0,1), (3,-1), "LEFT"), ("ALIGN", (4,1), (4,-1), "CENTER"), ("ALIGN", (5,1), (7,-1), "RIGHT")]))
    story.append(tbl)

    # resumo
    resumo = {}
    for r in linhas:
        key = (r["codigo"], r["descricao"], r["un"])
        acc = resumo.setdefault(key, {"qtd": 0.0, "val": 0.0})
        acc["qtd"] += float(r.get("qtd", 0.0) or 0.0)
        acc["val"] += float(r.get("subtotal", 0.0) or 0.0)
    story.append(Spacer(1, 10))
    resumo_title = Table([[Paragraph("<b>RESUMO DO PERÍODO</b>", ParagraphStyle("titRES", parent=styleN, fontSize=10.5, leading=12, alignment=TA_CENTER))]], colWidths=[doc.width])
    resumo_title.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")), ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
                                      ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6)]))
    story.append(resumo_title)
    res_headers = ["Código", "Descrição", "Un", "Qtd", "Valor Total"]
    res_rows = [res_headers]
    for (cod, desc, un), acc in sorted(resumo.items(), key=lambda x: (x[0][0], x[0][1])):
        res_rows.append([cod, desc, un, f"{acc['qtd']:.2f}", format_brl(acc['val'])])
    rW = doc.width
    res_tbl = Table(res_rows, colWidths=[0.14*rW, 0.46*rW, 0.07*rW, 0.13*rW, 0.20*rW], repeatRows=1)
    res_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0), colors.black), ("TEXTCOLOR",(0,0),(-1,0), colors.white),
                                 ("FONTNAME",(0,0),(-1,0), "Helvetica-Bold"), ("GRID", (0,0), (-1,-1), 0.25, colors.black),
                                 ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
                                 ("TOPPADDING",(0,0),(-1,-1),3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
                                 ("ALIGN", (0,1), (1,-1), "LEFT"), ("ALIGN", (2,1), (2,-1), "CENTER"),
                                 ("ALIGN", (3,1), (3,-1), "RIGHT"), ("ALIGN", (4,1), (4,-1), "RIGHT")]))

    story.append(res_tbl)
    story.append(Spacer(1, 10))
    total_val = sum(r["subtotal"] for r in linhas) if linhas else 0.0
    total_box = Table([[Paragraph("<b>Total:</b>", styleN), Paragraph(f"<b>{format_brl(total_val)}</b>", styleN)]], colWidths=[28*mm, 38*mm])
    total_box.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 0.75, colors.black), ("ALIGN", (0,0), (0,0), "RIGHT"),
                                   ("ALIGN", (1,0), (1,0), "RIGHT"), ("LEFTPADDING", (0,0), (-1,-1), 8), ("RIGHTPADDING",(0,0), (-1,-1), 10),
                                   ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
                                   ("BACKGROUND", (0,0), (0,0), colors.HexColor("#f5f5f5"))]))
    wrapper = Table([[None, total_box]], colWidths=[doc.width - (28*mm + 38*mm), (28*mm + 38*mm)])
    wrapper.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                                 ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(wrapper)
    doc.build(story, onFirstPage=lambda c, d: _on_page(c, d, titulo), onLaterPages=lambda c, d: _on_page(c, d, titulo))
    return buf.getvalue()

def gerar_pdf_fechamento(cliente_nome: str, periodo_str: str, linhas: list[dict], logo_bytes: bytes | None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=28, bottomMargin=36, leftMargin=14, rightMargin=14)
    story = []
    story += _header_vertical_centralizado()
    info_tbl = Table([[Paragraph(f"<b>Cliente:</b> {cliente_nome}", styleSmall)],
                      [Paragraph(f"<b>Período:</b> {periodo_str}", styleSmall)]], colWidths=[doc.width])
    info_tbl.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                  ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),2),
                                  ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story += [info_tbl, Spacer(1, 6)]
    titulo = "FECHAMENTO POR CLIENTE"
    tit_tbl = Table([[Paragraph(f"<b>{titulo}</b>", ParagraphStyle("titFEC", parent=styleN, fontSize=11, leading=13, alignment=TA_CENTER))]], colWidths=[doc.width])
    tit_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1), colors.HexColor("#e6e6e6")), ("TEXTCOLOR",(0,0),(-1,-1), colors.black),
                                 ("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                 ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6)]))
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
        rows.append([obra, cod, desc, un, f"{acc['qtd']:.2f}", format_brl(acc["val"])]); total += acc["val"]
    W = doc.width
    col_widths = [0.28*W, 0.10*W, 0.34*W, 0.06*W, 0.10*W, 0.12*W]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0), colors.black), ("TEXTCOLOR",(0,0),(-1,0), colors.white),
                             ("FONTNAME",(0,0),(-1,0), "Helvetica-Bold"), ("GRID",(0,0),(-1,-1),0.25,colors.black),
                             ("LEFTPADDING",(0,0),(-1,-1),6), ("RIGHTPADDING",(0,0),(-1,-1),6),
                             ("TOPPADDING",(0,0),(-1,-1),3),  ("BOTTOMPADDING",(0,0),(-1,-1),3),
                             ("ALIGN",(0,1),(2,-1),"LEFT"), ("ALIGN",(3,1),(3,-1),"CENTER"), ("ALIGN",(4,1),(5,-1),"RIGHT")]))
    story.append(tbl)
    story.append(Spacer(1, 10))
    total_box = Table([[Paragraph("<b>Total geral:</b>", styleN), Paragraph(f"<b>{format_brl(total)}</b>", styleN)]], colWidths=[36*mm, 42*mm])
    total_box.setStyle(TableStyle([("GRID", (0,0), (-1,-1), 0.75, colors.black), ("ALIGN", (0,0), (0,0), "RIGHT"),
                                   ("ALIGN", (1,0), (1,0), "RIGHT"), ("TOPPADDING",(0,0),(-1,-1),6), ("BOTTOMPADDING",(0,0),(-1,-1),6),
                                   ("LEFTPADDING",(0,0),(-1,-1),8), ("RIGHTPADDING",(0,0),(-1,-1),10)]))
    wrapper = Table([[None, total_box]], colWidths=[doc.width-(36*mm+42*mm), (36*mm+42*mm)])
    wrapper.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                                 ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0)]))
    story.append(wrapper)
    doc.build(story, onFirstPage=lambda c, d: _on_page(c, d, titulo), onLaterPages=lambda c, d: _on_page(c, d, titulo))
    return buf.getvalue()

# ===================== PÁGINAS CADASTROS =====================
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
                    if ja: banner("warn", "Já existe cliente com esse nome.")
                    else:
                        sess.add(Cliente(nome=nome.strip(), documento=(documento or None), contato=(contato or None),
                                         email=(email or None), telefone=(telefone or None), ativo=1 if ativo else 0))
                        sess.commit(); flash("success", "Cliente cadastrado."); _rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with col_list:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Clientes")
        with SessionLocal() as sess:
            clientes = sess.execute(select(Cliente).order_by(Cliente.nome.asc())).scalars().all()
        if not clientes:
            banner("info", "Nenhum cliente ainda."); st.markdown('</div>', unsafe_allow_html=True); return
        df = pd.DataFrame([{"id": c.id, "nome": c.nome, "documento": c.documento, "contato": c.contato,
                            "email": c.email, "telefone": c.telefone, "ativo": c.ativo,
                            "bloqueado": c.bloqueado, "motivo": c.bloqueado_motivo, "desde": c.bloqueado_desde} for c in clientes])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown("##### Editar / Excluir")
        cli_sel = st.selectbox("Selecione um cliente", options=clientes,
                               format_func=lambda c: f"{c.nome} (ID {c.id})", key="cli_edit_sel")
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
                    if dup: banner("error", "Já existe outro cliente com esse nome.")
                    else:
                        if novo_bloq and not bloqueado_atual:
                            c.bloqueado = 1; c.bloqueado_desde = date.today(); c.bloqueado_motivo = (novo_motivo or "Bloqueado")
                        elif not novo_bloq and bloqueado_atual:
                            c.bloqueado = 0; c.bloqueado_desde = None; c.bloqueado_motivo = None
                        else:
                            c.bloqueado_motivo = (novo_motivo or None)
                        sess.commit(); flash("success", "Cliente atualizado."); _rerun()
                with SessionLocal() as s2:
                    obras_vinc = s2.query(Obra).filter((Obra.cliente_id == c.id) | (func.trim(func.coalesce(Obra.cliente, "")) == c.nome)).count()
                if obras_vinc > 0:
                    bcol2.button("Excluir (bloqueado — possui obras)", disabled=True, use_container_width=True, key=f"cli_del_btn_{c.id}")
                    banner("warn", f"Este cliente possui {obras_vinc} obra(s) vinculada(s).")
                else:
                    conf = st.checkbox("Confirmo a exclusão deste cliente", key=f"cli_del_conf_{c.id}")
                    if bcol2.button("Excluir cliente", use_container_width=True, disabled=not conf, key=f"cli_del_{c.id}"):
                        sess.delete(c); sess.commit(); flash("success", "Cliente excluído."); _rerun()
        st.markdown('</div>', unsafe_allow_html=True)

def page_obras():
    st.markdown('<div class="section-title">Cadastro de Obras</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2])
    with c1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Nova Obra")
        with SessionLocal() as sess:
            clientes = (sess.execute(select(Cliente).where(Cliente.ativo == 1).order_by(Cliente.nome.asc())).scalars().all())
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
                flash("success", "Obra cadastrada."); _rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown("#### Obras")
        with SessionLocal() as sess:
            obras = (sess.execute(select(Obra).options(selectinload(Obra.cliente_ref)).order_by(Obra.nome.asc())).scalars().all())
        if not obras:
            banner("info", "Nenhuma obra cadastrada."); st.markdown("</div>", unsafe_allow_html=True); return
        df = pd.DataFrame([{"id": o.id, "nome": o.nome, "endereco": o.endereco,
                            "cliente": (o.cliente_ref.nome if getattr(o, "cliente_ref", None) else None),
                            "ativo": o.ativo, "bloqueada": o.bloqueada,
                            "motivo": o.bloqueada_motivo, "desde": o.bloqueada_desde} for o in obras])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown("##### Editar / Excluir")
        obra_sel = st.selectbox("Selecione uma obra", options=obras,
                                format_func=lambda o: f"{o.nome} — {o.endereco} (ID {o.id})", key="obra_edit_sel")
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
                if faltando: banner("warn", f"Falta anexar: <b>{', '.join(faltando)}</b>.")
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
                    sess.commit(); flash("success", "Obra atualizado."); _rerun()
                os_count = sess.query(OS).filter(OS.obra_id == o.id).count()
                if os_count > 0: banner("warn", f"Ao excluir esta obra, {os_count} OS serão removidas.")
                conf = st.checkbox("Confirmo a exclusão desta obra (e suas OS)", key=f"obra_del_conf_{o.id}")
                if b2.button("Excluir obra", use_container_width=True, disabled=not conf, key=f"obra_del_{o.id}"):
                    sess.delete(o); sess.commit(); flash("success", "Obra excluída."); _rerun()
                st.markdown("##### Serviços desta obra (preços específicos)")
                with SessionLocal() as sess_osv:
                    catalogo = sess_osv.execute(select(Servico).order_by(Servico.codigo.asc())).scalars().all()
                    vinculos = (sess_osv.query(ObraServico, Servico).join(Servico, Servico.id == ObraServico.servico_id)
                                .filter(ObraServico.obra_id == o.id).order_by(Servico.codigo.asc()).all())
                    if vinculos:
                        df_osv = pd.DataFrame([{"id": osv.id, "codigo": srv.codigo, "descricao": srv.descricao,
                                                "un": srv.unidade, "preco_unit": osv.preco_unit, "ativo": osv.ativo} for (osv, srv) in vinculos])
                        st.dataframe(df_osv, use_container_width=True, hide_index=True)
                    else:
                        banner("info", "Nenhum serviço vinculado a esta obra ainda.")
                    st.markdown("###### Adicionar/editar vínculo")
                    cadd1, cadd2, cadd3, cadd4 = st.columns([2, 1, 1, 1])
                    srv_add = cadd1.selectbox("Serviço (catálogo)", catalogo, format_func=lambda s: f"{s.codigo} — {s.descricao}")
                    preco_add = cadd2.number_input("Preço p/ esta obra", min_value=0.0, step=1.0, value=float(srv_add.preco_unit or 0.0))
                    ativo_add = cadd3.checkbox("Ativo", value=True)
                    if cadd4.button("Salvar vínculo/atualizar", key=f"btn_save_vinc_{o.id}"):
                        existente = (sess_osv.query(ObraServico).filter(ObraServico.obra_id == o.id, ObraServico.servico_id == srv_add.id).one_or_none())
                        if existente is None:
                            sess_osv.add(ObraServico(obra_id=o.id, servico_id=srv_add.id, preco_unit=preco_add, ativo=1 if ativo_add else 0))
                        else:
                            existente.preco_unit = preco_add; existente.ativo = 1 if ativo_add else 0
                        sess_osv.commit(); flash("success", "Vínculo de serviço atualizado nesta obra."); _rerun()
                    if vinculos:
                        st.markdown("###### Ativar/Desativar/Remover")
                        alvo = st.selectbox("Vínculo", vinculos, format_func=lambda t: f"{t[1].codigo} — {t[1].descricao}")
                        if alvo:
                            osv, srv = alvo
                            cedit1, cedit2, cedit3, cedit4 = st.columns([1,1,1,1])
                            novo_preco = cedit1.number_input("Preço", min_value=0.0, step=1.0, value=float(osv.preco_unit or 0.0), key=f"preco_edit_{osv.id}")
                            novo_ativo = cedit2.checkbox("Ativo", value=bool(osv.ativo), key=f"ativo_edit_{osv.id}")
                            if cedit3.button("Salvar", key=f"save_edit_{osv.id}"):
                                osv.preco_unit = novo_preco; osv.ativo = 1 if novo_ativo else 0
                                sess_osv.commit(); flash("success","Vínculo salvo."); _rerun()
                            if cedit4.button("Remover vínculo", key=f"del_edit_{osv.id}"):
                                sess_osv.delete(osv); sess_osv.commit(); flash("success","Vínculo removido."); _rerun()
        st.markdown("</div>", unsafe_allow_html=True)

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
            if not codigo or not descricao or not unidade: banner("error", "Preencha Código, Descrição e Unidade.")
            else:
                with SessionLocal() as sess:
                    ja = sess.execute(select(Servico).where(Servico.codigo == codigo)).scalars().first()
                    if ja: banner("warn", "Já existe serviço com esse código.")
                    else:
                        sess.add(Servico(codigo=codigo, descricao=descricao, unidade=unidade,
                                         preco_unit=(preco or None), ativo=1 if ativo else 0))
                        sess.commit(); flash("success", "Serviço cadastrado."); _rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        with SessionLocal() as sess:
            servs = sess.execute(select(Servico).order_by(Servico.codigo.asc())).scalars().all()
        if not servs:
            banner("info", "Nenhum serviço cadastrado ainda."); st.markdown('</div>', unsafe_allow_html=True); return
        st.dataframe(pd.DataFrame([{"id": s.id, "codigo": s.codigo, "descricao": s.descricao,
                                    "unidade": s.unidade, "preco_unit": s.preco_unit, "ativo": s.ativo} for s in servs]),
                     use_container_width=True, hide_index=True)
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
                if itens_count > 0: banner("warn", f"Ao excluir este serviço, {itens_count} item(ns) de OS serão removidos.")
                conf = st.checkbox("Confirmo a exclusão deste serviço", key=f"srv_del_conf_{sdb.id}")
                if b2.button("Excluir serviço", use_container_width=True, disabled=not conf, key=f"srv_del_{sdb.id}"):
                    sess.delete(sdb); sess.commit(); flash("success", "Serviço excluído."); _rerun()
        st.markdown('</div>', unsafe_allow_html=True)

def get_servicos_da_obra(sess: Session, obra_id: int) -> List[tuple[ObraServico, Servico]]:
    q = (sess.query(ObraServico, Servico).join(Servico, Servico.id == ObraServico.servico_id)
         .filter(ObraServico.obra_id == obra_id, ObraServico.ativo == 1).order_by(Servico.codigo.asc()))
    return q.all()

def obter_os_com_itens(sess: Session, os_id: int):
    os_row = sess.query(OS).options(selectinload(OS.itens).selectinload(OSItem.servico)).filter(OS.id == os_id).first()
    obra_row = sess.get(Obra, os_row.obra_id)
    itens = []
    for it in os_row.itens:
        sv = it.servico
        preco = it.preco_unit if getattr(it, "preco_unit", None) is not None else (sv.preco_unit or 0.0)
        itens.append({"codigo": sv.codigo, "descricao": sv.descricao, "unidade": sv.unidade,
                      "qtd_prev": it.quantidade_prevista or 0.0, "preco_unit": preco, "subtotal": preco * (it.quantidade_prevista or 0.0)})
    return os_row, obra_row, itens

def page_emitir_os():
    st.markdown('<div class="section-title">Emitir OS</div>', unsafe_allow_html=True)
    flash_render(clear=True)
    with SessionLocal() as sess:
        obras = sess.execute(select(Obra).options(selectinload(Obra.cliente_ref)).where(Obra.ativo == 1).order_by(Obra.nome.asc())).scalars().all()
    if not obras:
        banner("warn", "Cadastre ao menos 1 obra para emitir OS."); return
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
        banner("info", "Nenhuma obra encontrada para o termo informado."); return
    idx_escolhido = st.selectbox("Obra *", list(range(len(opt_pairs))), format_func=lambda i: opt_pairs[i][1], key="obra_emit_sel")
    obra_sel, _lbl, cliente_bloqueado, obra_bloqueada = opt_pairs[idx_escolhido]
    with SessionLocal() as _s_docs: _obra_docs = _s_docs.get(Obra, obra_sel.id)
    docs_ok = {"Cartão CNPJ": bool(getattr(_obra_docs, "anexo_cnpj", None)),
               "Proposta":    bool(getattr(_obra_docs, "anexo_proposta", None)),
               "Contrato":    bool(getattr(_obra_docs, "anexo_contrato", None))}
    faltando = [nome for nome, ok in docs_ok.items() if not ok]
    if faltando: banner("warn", f"Documentos pendentes desta obra: <b>{', '.join(faltando)}</b>. Anexe em <b>Cadastro → Obras → Anexos</b>.")
    try:
        with SessionLocal() as sess:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao)).filter(OS.obra_id == obra_sel.id, OS.status == "Medido").scalar())
            if ultima_medida_dt:
                os_ref = (sess.query(OS).filter(OS.obra_id == obra_sel.id, OS.status == "Aberta", OS.data_emissao > ultima_medida_dt)
                          .order_by(OS.data_emissao.asc(), OS.id.asc()).first())
            else:
                os_ref = (sess.query(OS).filter(OS.obra_id == obra_sel.id, OS.status == "Aberta")
                          .order_by(OS.data_emissao.asc(), OS.id.asc()).first())
        if os_ref and os_ref.data_emissao:
            dias = (date.today() - os_ref.data_emissao).days
            msg = ("Medição em atraso" if dias >= 30 else "Medição em dia")
            banner("info", f"{msg}: OS <b>{os_ref.numero}</b> em Aberto há <b>{dias}</b> dias.")
    except Exception: pass
    if cliente_bloqueado:
        with SessionLocal() as sess: cli = sess.get(Cliente, obra_sel.cliente_id) if obra_sel.cliente_id else None
        motivo = cli.bloqueado_motivo if cli else "Cliente bloqueado."; desde = cli.bloqueado_desde.strftime("%d/%m/%Y") if (cli and cli.bloqueado_desde) else "-"
        banner("error", f"Cliente bloqueado desde {desde}. Motivo: {motivo}. Emissão desabilitada.")
    if obra_bloqueada:
        motivo_o = obra_sel.bloqueada_motivo or "Obra bloqueada."; desde_o = obra_sel.bloqueada_desde.strftime("%d/%m/%Y") if obra_sel.bloqueada_desde else "-"
        banner("error", f"Obra bloqueada desde {desde_o}. Motivo: {motivo_o}. Emissão desabilitada.")
    bloqueio_ativo = (cliente_bloqueado or obra_bloqueada)
    data_emissao = st.date_input("Data de Emissão", value=date.today(), key="dt_emissao_os")
    observ = st.text_area("Observações (opcional)", key="obs_os")
    with SessionLocal() as sess:
        servs_pairs = get_servicos_da_obra(sess, obra_sel.id)
        if not servs_pairs:
            banner("warn", "Esta obra não possui serviços vinculados. Cadastre em Cadastro → Obras → 'Serviços desta obra'."); return
        _servs_exib = [{"srv_id": srv.id, "codigo": srv.codigo, "descricao": srv.descricao, "un": srv.unidade, "preco": float(osv.preco_unit or 0.0)} for (osv, srv) in servs_pairs]
    st.markdown("##### Itens da OS")
    c1, c2, c3, c4, c5 = st.columns([2, 3, 1, 1.2, 1.3])
    q_srv = c2.text_input("Buscar serviço (código/descrição)", placeholder="ex.: CP28 ou rompimento", key="q_srv_os").strip().lower()
    servs_filtrados = [s for s in _servs_exib if q_srv in f"{s['codigo']} {s['descricao']}".lower()] if q_srv else _servs_exib
    serv_sel = c1.selectbox("Serviço da obra", servs_filtrados, format_func=lambda sv: f"{sv['codigo']} — {sv['descricao']} (R$ {sv['preco']:.2f}/{sv['un']})", key="srv_sel_os")
    qtd_prev = c3.number_input("Qtd.", min_value=0.0, step=1.0, value=0.0, key="qtd_prev_os")
    preco_vinc = c4.number_input("Preço (obra)", min_value=0.0, step=1.0, value=float(serv_sel["preco"]), key="preco_sel_os")
    subtotal_prev = qtd_prev * preco_vinc
    c5.markdown(f"<div class='card'><b>Subtotal</b><div style='font-size:1.2rem'>{format_brl(subtotal_prev)}</div></div>", unsafe_allow_html=True)
    st.session_state.setdefault("itens_os_tmp", [])
    if st.button("Adicionar", disabled=bloqueio_ativo, key="btn_add_item_os"):
        if qtd_prev <= 0: banner("error", "Informe uma quantidade > 0.")
        else:
            st.session_state["itens_os_tmp"].append((serv_sel["srv_id"], serv_sel["codigo"], serv_sel["descricao"], serv_sel["un"], float(qtd_prev), float(preco_vinc)))
            flash("success", "Item adicionado.")
    if st.session_state["itens_os_tmp"]:
        df_it = pd.DataFrame(st.session_state["itens_os_tmp"], columns=["servico_id", "Código", "Descrição", "Un", "Qtd Prevista", "Preço Unit. (obra)"])
        df_it["Subtotal"] = df_it["Qtd Prevista"] * df_it["Preço Unit. (obra)"]
        st.dataframe(df_it[["Código","Descrição","Un","Qtd Prevista","Preço Unit. (obra)","Subtotal"]], use_container_width=True)
        colA, colB = st.columns([1, 3])
        if colA.button("Limpar itens", key="btn_clear_itens_os"):
            st.session_state["itens_os_tmp"] = []; flash("info", "Itens limpos."); _rerun()
        if colB.button("Gerar OS", disabled=bloqueio_ativo or not st.session_state["itens_os_tmp"], key="btn_emit_os"):
            if bloqueio_ativo: banner("error", "Cliente/Obra bloqueado — libere antes de emitir novas OS.")
            else:
                ok = False; sess = SessionLocal()
                try:
                    numero = gerar_numero_os(sess)
                    nova = OS(numero=numero, data_emissao=data_emissao, obra_id=obra_sel.id, observacoes=(observ or None), status="Aberta")
                    sess.add(nova); sess.flush()
                    for (sid, _cod, _desc, _un, qtd, preco_snap) in st.session_state["itens_os_tmp"]:
                        sess.add(OSItem(os_id=nova.id, servico_id=sid, quantidade_prevista=(qtd or None), preco_unit=float(preco_snap)))
                    sess.commit(); ok = True
                except Exception:
                    sess.rollback(); ok = False
                finally: sess.close()
                st.session_state["itens_os_tmp"] = []
                if ok: flash("success", f"OS <b>{numero}</b> gerada com sucesso!")
                else: flash("error", "OS não gerada por erro inesperado.")
                _rerun()
    else:
        banner("info", "Adicione itens para gerar a OS.")

def page_visualizar_imprimir():
    st.markdown('<div class="section-title">Visualizar / Imprimir</div>', unsafe_allow_html=True)
    st.markdown('<div class="card">', unsafe_allow_html=True)
    with SessionLocal() as sess:
        os_df_full = to_df(sess, OS)
        if os_df_full.empty:
            banner("info", "Nenhuma OS emitida para visualizar."); st.markdown("</div>", unsafe_allow_html=True); return
        os_df_full["data_emissao"] = pd.to_datetime(os_df_full["data_emissao"], errors="coerce").dt.date
        obras_map = {o.id: f"{o.nome} — {o.endereco}" for o in sess.query(Obra).all()}
        os_df_full["obra_nome"] = os_df_full["obra_id"].map(lambda oid: obras_map.get(oid, f"Obra {oid}"))
        os_df_full["data_str"] = os_df_full["data_emissao"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else "")
    f1, f2, f3 = st.columns([2, 1, 1])
    obra_opcoes = ["(Todas)"] + sorted(os_df_full["obra_nome"].dropna().unique().tolist())
    obra_filtro = f1.selectbox("Filtrar por obra", obra_opcoes, key="flt_obra_print")
    status_opcoes = ["(Todos)"] + STATUS_OPTIONS
    status_filtro = f2.selectbox("Status", status_opcoes, key="flt_status_print")
    min_dt = os_df_full["data_emissao"].min(); max_dt = os_df_full["data_emissao"].max(); hoje = date.today()
    ini_default, fim_default = (min_dt or hoje), (max_dt or hoje)
    if ini_default > fim_default: ini_default, fim_default = fim_default, ini_default
    periodo = st.date_input("Período", value=(ini_default, fim_default), key="print_periodo")
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))
    df_view = os_df_full.copy()
    if obra_filtro != "(Todas)": df_view = df_view[df_view["obra_nome"] == obra_filtro]
    if status_filtro != "(Todos)": df_view = df_view[df_view["status"] == status_filtro]
    df_view = df_view[(df_view["data_emissao"] >= ini) & (df_view["data_emissao"] <= fim)].sort_values(["data_emissao", "id"], ascending=[False, False]).reset_index(drop=True)
    q = st.text_input("Buscar por número da OS", placeholder="ex.: HAB-2025-0012", key="q_os_print").strip().upper()
    df_filt = df_view if not q else df_view[df_view["numero"].str.contains(q, case=False, na=False)]
    if df_filt.empty:
        banner("warn", "Nenhuma OS encontrada com os filtros/busca."); st.markdown("</div>", unsafe_allow_html=True); return
    df_filt["label"] = df_filt.apply(lambda r: f"{r['numero']} — {r['obra_nome']} — {r['data_str']} [{r['status']}]", axis=1)
    labels = df_filt["label"].tolist()
    if "os_idx" not in st.session_state or st.session_state.get("q_os_last") != q: st.session_state["os_idx"] = 0
    st.session_state["q_os_last"] = q
    cnav1, csel, cnav2 = st.columns([1, 4, 1])
    with cnav1:
        if st.button("Anterior", use_container_width=True, key="btn_prev_os"):
            st.session_state["os_idx"] = (st.session_state["os_idx"] - 1) % len(labels)
    with cnav2:
        if st.button("Próxima", use_container_width=True, key="btn_next_os"):
            st.session_state["os_idx"] = (st.session_state["os_idx"] + 1) % len(labels)
    escolha = csel.selectbox("Selecione a OS para impressão", labels, index=min(st.session_state["os_idx"], len(labels) - 1), key="os_select_print")
    st.session_state["os_idx"] = labels.index(escolha)
    row = df_filt.iloc[st.session_state["os_idx"]]
    with SessionLocal() as sess:
        os_row_db = sess.query(OS).filter(OS.id == int(row["id"])).first()
        if not os_row_db: banner("error", "OS não encontrada."); st.markdown("</div>", unsafe_allow_html=True); return
        os_row, obra_row, itens = obter_os_com_itens(sess, os_row_db.id)
    cH1, cH2 = st.columns([2, 1])
    with cH1:
        st.write(f"**OS:** {os_row.numero}"); st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
        st.write(f"**Status:** {os_row.status}"); st.write(f"**Obra:** {obra_row.nome}"); st.write(f"**Endereço:** {obra_row.endereco}")
        with SessionLocal() as s2: cli = s2.get(Cliente, obra_row.cliente_id) if obra_row.cliente_id else None
        st.write(f"**Cliente:** {(cli.nome if cli else (obra_row.cliente or '-'))}")
        if os_row.observacoes: st.write(f"**Observações:** {os_row.observacoes}")
    total = sum(it["subtotal"] for it in itens)
    with cH2:
        st.markdown(f'<div class="card"><b>Total estimado</b><div style="font-size:1.6rem;margin-top:.35rem">{format_brl(total)}</div></div>', unsafe_allow_html=True)
    if itens:
        df_itens = pd.DataFrame(itens).rename(columns={"codigo": "Código","descricao": "Descrição","unidade": "Un","qtd_prev": "Qtd Prevista","preco_unit": "Preço Unit.","subtotal": "Subtotal"})
        st.dataframe(df_itens[["Código","Descrição","Un","Qtd Prevista","Preço Unit.","Subtotal"]], use_container_width=True)
    else:
        banner("info", "Esta OS ainda não possui itens.")
    logo_b = None
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, logo_bytes=logo_b)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, logo_bytes=logo_b)
    b1, b2 = st.columns(2)
    with b1:
        st.download_button("Baixar PDF (interno — com preços)", data=pdf_interno, file_name=f"{os_row.numero}_interno.pdf", mime="application/pdf", key="dl_pdf_interno")
    with b2:
        st.download_button("Baixar PDF (cliente — sem preços)", data=pdf_cliente, file_name=f"{os_row.numero}_cliente.pdf", mime="application/pdf", key="dl_pdf_cliente")
    st.markdown("</div>", unsafe_allow_html=True)

def page_medicao():
    st.markdown('<div class="section-title">Medição Mensal</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        obras = sess.execute(select(Obra).where(Obra.ativo == 1).order_by(Obra.nome.asc())).scalars().all()
    if not obras: banner("info", "Cadastre obras para usar a medição mensal."); return
    obra_sel = st.selectbox("Obra", obras, format_func=lambda o: f"{o.nome} — {o.endereco}", key="obra_medicao_sel")
    try:
        with SessionLocal() as sess:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao)).filter(OS.obra_id == obra_sel.id, OS.status == "Medido").scalar())
            if ultima_medida_dt:
                os_ref = (sess.query(OS).filter(OS.obra_id == obra_sel.id, OS.status == "Aberta", OS.data_emissao > ultima_medida_dt).order_by(OS.data_emissao.asc(), OS.id.asc()).first())
            else:
                os_ref = (sess.query(OS).filter(OS.obra_id == obra_sel.id, OS.status == "Aberta").order_by(OS.data_emissao.asc(), OS.id.asc()).first())
        if os_ref and os_ref.data_emissao:
            dias = (date.today() - os_ref.data_emissao).days; msg = ("Medição em atraso" if dias >= 30 else "Medição em dia")
            banner("info", f"{msg}: OS <b>{os_ref.numero}</b> em Aberto há <b>{dias}</b> dias.")
    except Exception: pass
    cliente_bloqueado = False
    with SessionLocal() as scli:
        ob = scli.get(Obra, obra_sel.id); cli = scli.get(Cliente, ob.cliente_id) if ob and ob.cliente_id else None
        if cli and cli.bloqueado:
            cliente_bloqueado = True; motivo = cli.bloqueado_motivo or "Sem motivo informado"; desde = cli.bloqueado_desde.strftime("%d/%m/%Y") if cli.bloqueado_desde else "-"
            banner("warn", f"Cliente bloqueado desde {desde}. Pode gerar PDF, mas não gravar status. Motivo: {motivo}")
    try:
        with SessionLocal() as sess: ultimo_num = sess.query(func.max(Medicao.numero)).filter(Medicao.obra_id == obra_sel.id).scalar()
    except Exception: ultimo_num = 0
    medicao_num = st.number_input("Número da medição", min_value=1, step=1, value=int((ultimo_num or 0) + 1), key="med_num")
    hoje = date.today(); primeiro_dia = date(hoje.year, hoje.month, 1); ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    periodo = st.date_input("Período da medição", value=(primeiro_dia, ultimo_dia), key="med_periodo")
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))
    st.markdown("#### Filtros")
    col_fs1, col_fs2 = st.columns([1, 1])
    with col_fs1: status_listagem = st.selectbox("Status das OS a listar", ["(Todos)"] + STATUS_OPTIONS, index=0, key="med_status_list")
    with col_fs2: status_aplicar = st.selectbox("Status para aplicar em massa", STATUS_OPTIONS, index=STATUS_OPTIONS.index("Medido") if "Medido" in STATUS_OPTIONS else 0, key="med_status_apply")
    with SessionLocal() as sess:
        q = (sess.query(OS, OSItem, Servico, Obra).join(OSItem, OSItem.os_id == OS.id).join(Servico, Servico.id == OSItem.servico_id).join(Obra, Obra.id == OS.obra_id)
             .filter(OS.obra_id == obra_sel.id).filter(OS.data_emissao >= ini, OS.data_emissao <= fim))
        if status_listagem != "(Todos)": q = q.filter(OS.status == status_listagem)
        q = q.order_by(OS.data_emissao.asc(), OS.numero.asc(), Servico.codigo.asc()); rows = q.all()
    linhas = []
    for os_row, it, sv, ob in rows:
        preco_snap = (it.preco_unit if getattr(it, "preco_unit", None) is not None else (sv.preco_unit or 0.0))
        linhas.append({"data": os_row.data_emissao, "os_num": os_row.numero, "status": os_row.status, "codigo": sv.codigo, "descricao": sv.descricao, "un": sv.unidade,
                       "qtd": (it.quantidade_prevista or 0.0), "preco": preco_snap, "subtotal": preco_snap * (it.quantidade_prevista or 0.0)})
    st.markdown("#### Itens do período (após filtros)")
    if not linhas:
        banner("info", "Não há itens para as condições selecionadas.")
    else:
        df = pd.DataFrame(linhas); total = df["subtotal"].sum()
        col_tbl, col_total = st.columns([4,1])
        with col_tbl:
            st.dataframe(df.assign(data=df["data"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else d),
                                   preco=df["preco"].apply(format_brl), subtotal=df["subtotal"].apply(format_brl)), use_container_width=True)
        with col_total:
            st.markdown('<div class="card"><b>Total do período</b>' f'<div style="font-size:1.6rem;margin-top:.35rem">{format_brl(total)}</div></div>', unsafe_allow_html=True)
        period_text = f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
        c1, c2, _ = st.columns([1,1,2])
        with c1:
            pdf = gerar_pdf_medicao(obra_sel.nome, period_text, linhas, logo_bytes=None, medicao_num=int(medicao_num))
            st.download_button("Gerar PDF da Medição", data=pdf, file_name=f"medicao_{obra_sel.id}_{ini}_{fim}.pdf", mime="application/pdf", key="dl_pdf_medicao")
        with c2:
            btn_label = f"Aplicar status '{status_aplicar}' a todas as OS do período"
            if st.button(btn_label, disabled=cliente_bloqueado, key="btn_apply_status_med"):
                if cliente_bloqueado: banner("error", "Cliente bloqueado — libere antes de atualizar o status.")
                else:
                    with SessionLocal() as sess:
                        sess.query(OS).filter(OS.obra_id == obra_sel.id, OS.data_emissao >= ini, OS.data_emissao <= fim).update({OS.status: status_aplicar}, synchronize_session="fetch")
                        sess.add(Medicao(obra_id=obra_sel.id, numero=int(medicao_num), periodo_ini=ini, periodo_fim=fim, criado_em=date.today()))
                        sess.commit()
                    flash("success", f"Todas as OS do período foram marcadas como '{status_aplicar}'."); _rerun()
def page_relatorios():
    st.markdown('<div class="section-title">Relatórios por Cliente</div>', unsafe_allow_html=True)
    with SessionLocal() as sess:
        clientes = sess.execute(select(Cliente).where(Cliente.ativo == 1).order_by(Cliente.nome.asc())).scalars().all()
    if not clientes: banner("info", "Cadastre clientes para usar os relatórios."); return
    cliente_sel = st.selectbox("Cliente", clientes, format_func=lambda c: c.nome, key="rel_cli_sel")
    if bool(getattr(cliente_sel, "bloqueado", 0)):
        motivo = cliente_sel.bloqueado_motivo or "Sem motivo informado"; desde = cliente_sel.bloqueado_desde.strftime("%d/%m/%Y") if cliente_sel.bloqueado_desde else "-"
        banner("warn", f"Cliente bloqueado desde {desde}. Relatórios continuam disponíveis. Motivo: {motivo}")
    hoje = date.today(); primeiro_dia = date(hoje.year, hoje.month, 1); ultimo_dia = date(hoje.year, hoje.month, calendar.monthrange(hoje.year, hoje.month)[1])
    periodo = st.date_input("Período", value=(primeiro_dia, ultimo_dia), key="rel_periodo")
    ini, fim = (periodo if isinstance(periodo, (list, tuple)) and len(periodo) == 2 else (periodo, periodo))
    status_opt = ["(Todos)"] + STATUS_OPTIONS; status_filtro = st.selectbox("Filtrar por status das OS", status_opt, index=0, key="rel_status")
    with SessionLocal() as sess:
        obras_cliente = sess.execute(select(Obra).where((Obra.cliente_id == cliente_sel.id) | (func.trim(func.coalesce(Obra.cliente, "")) == cliente_sel.nome)).order_by(Obra.nome.asc())).scalars().all()
    if not obras_cliente: banner("warn", "Não há obras vinculadas a este cliente."); return
    resumo_status = []
    with SessionLocal() as sess:
        for ob in obras_cliente:
            ultima_medida_dt = (sess.query(func.max(OS.data_emissao)).filter(OS.obra_id == ob.id, OS.status == "Medido").scalar())
            if ultima_medida_dt:
                os_ref = (sess.query(OS).filter(OS.obra_id == ob.id, OS.status == "Aberta", OS.data_emissao > ultima_medida_dt)
                          .order_by(OS.data_emissao.asc(), OS.id.asc()).first())
            else:
                os_ref = (sess.query(OS).filter(OS.obra_id == ob.id, OS.status == "Aberta").order_by(OS.data_emissao.asc(), OS.id.asc()).first())
            if os_ref and os_ref.data_emissao:
                dias = (date.today() - os_ref.data_emissao).days; status_txt = "Medição em atraso" if dias >= 30 else "Medição em dia"
                resumo_status.append({"Obra": ob.nome, "Endereço": ob.endereco, "OS (referência)": os_ref.numero, "Emissão": os_ref.data_emissao.strftime("%d/%m/%Y"), "Dias": dias, "Status de Medição": status_txt})
            else:
                resumo_status.append({"Obra": ob.nome, "Endereço": ob.endereco, "OS (referência)": "-", "Emissão": "-", "Dias": "-", "Status de Medição": "Medição em dia"})
    st.markdown("#### Status de Medição por Obra")
    df_status = pd.DataFrame(resumo_status)
    if not df_status.empty:
        def _ord(v): return 0 if v == "Medição em atraso" else 1
        df_status = df_status.sort_values(["Status de Medição", "Obra"], key=lambda s: s.map(_ord) if s.name == "Status de Medição" else s)
        st.dataframe(df_status, use_container_width=True, hide_index=True)
    else: banner("info", "Sem obras vinculadas ao cliente.")
    with SessionLocal() as sess:
        obra_ids = [o.id for o in obras_cliente]
        if not obra_ids: banner("warn", "Não há obras vinculadas a este cliente."); return
        q = (sess.query(OS, OSItem, Servico, Obra).join(OSItem, OSItem.os_id == OS.id).join(Servico, Servico.id == OSItem.servico_id).join(Obra, Obra.id == OS.obra_id)
             .filter(OS.obra_id.in_(obra_ids)).filter(OS.data_emissao >= ini, OS.data_emissao <= fim))
        if status_filtro != "(Todos)": q = q.filter(OS.status == status_filtro)
        q = q.order_by(OS.data_emissao.asc(), Obra.nome.asc(), OS.numero.asc(), Servico.codigo.asc()); rows = q.all()
    linhas = []
    for os_row, it, sv, ob in rows:
        preco_snap = (it.preco_unit if getattr(it, "preco_unit", None) is not None else (sv.preco_unit or 0.0))
        linhas.append({"data": os_row.data_emissao, "obra": ob.nome, "os_num": os_row.numero, "codigo": sv.codigo, "descricao": sv.descricao, "un": sv.unidade,
                       "qtd": (it.quantidade_prevista or 0.0), "preco": preco_snap, "subtotal": preco_snap * (it.quantidade_prevista or 0.0)})
    st.markdown("#### Fechamento detalhado")
    if not linhas:
        banner("info", "Nenhum item encontrado para os filtros informados.")
    else:
        df = pd.DataFrame(linhas); total = df["subtotal"].sum()
        col_tbl, col_total = st.columns([4, 1])
        with col_tbl:
            st.dataframe(df.assign(data=df["data"].apply(lambda d: d.strftime("%d/%m/%Y") if isinstance(d, date) else d),
                                   preco=df["preco"].apply(format_brl), subtotal=df["subtotal"].apply(format_brl)), use_container_width=True)
        with col_total:
            st.markdown('<div class="card"><b>Total geral</b>' f'<div style="font-size:1.6rem;margin-top:.35rem">{format_brl(total)}</div></div>', unsafe_allow_html=True)
        periodo_texto = f"{ini.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
        pdf = gerar_pdf_fechamento(cliente_sel.nome, periodo_texto, linhas, logo_bytes=None)
        st.download_button("Imprimir fechamento (PDF)", data=pdf, file_name=f"fechamento_{cliente_sel.nome}_{ini}_{fim}.pdf", mime="application/pdf", key="dl_pdf_fechamento")

def page_export():
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)
    with st.expander("Backup (DB + anexos)", expanded=False):
        if st.button("Gerar backup ZIP", key="btn_backup_zip"):
            p = make_full_backup()
            with p.open("rb") as f:
                st.download_button("Baixar backup", data=f.read(), file_name=p.name, mime="application/zip", key="dl_backup_zip")
    st.markdown("#### Exportar OS por obras (Excel)")
    if st.button("Gerar Excel de OS por obra", key="btn_os_xls"):
        xls_bytes = make_os_excel_per_obras()
        st.download_button(
            "Baixar Excel",
            data=xls_bytes,
            file_name=f"os_por_obras_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_os_xls",
        )

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

def main_router():
    flash_render()
    if page == "Cadastro: Clientes":
        page_clientes()
    elif page == "Cadastro: Obras":
        page_obras()
    elif page == "Cadastro: Serviços":
        page_servicos()
    elif page == "Visualizar / Imprimir":
        page_visualizar_imprimir()
    elif page == "Medição Mensal":
        page_medicao()
    elif page == "Relatórios":
        page_relatorios()
    elif page == "Exportações":
        page_export()
    else:
        page_emitir_os()

# ====== Entry point ======
main_router()
