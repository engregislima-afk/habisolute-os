# -*- coding: utf-8 -*-
# Habisolute — Sistema de OS (Streamlit)
# Visual Fluent/Windows 11 + banners + avisos + medição em dias
# Inclui: backup por obra em ZIP (xlsx/csv), upload de assinatura digital e inserção no PDF

import io, re, os, json, base64, tempfile, zipfile, hashlib, hmac, secrets, calendar
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

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

# ========= estado =========
s = st.session_state
s.setdefault("logged_in", False)
s.setdefault("username", None)
s.setdefault("is_admin", False)
s.setdefault("role", "usuario")
s.setdefault("must_change", False)
s.setdefault("theme_mode", load_user_prefs().get("theme_mode", "Claro"))
s.setdefault("brand", load_user_prefs().get("brand", "Laranja"))
s.setdefault("_flash", [])
# assinatura digital (bytes)
s.setdefault("sig_lab_bytes", None)

def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

# ====== Auth JSON local ======
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

def user_delete(username: str) -> None:
    db = _load_users()
    if username in db.get("users", {}):
        if username == "admin": return
        db["users"].pop(username, None); _save_users(db)

# ========= CSS com degradê laranja =========
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
[data-testid="stSidebar"] {{
  background: linear-gradient(180deg, rgba(249,115,22,0.28) 0%, rgba(249,115,22,0.05) 65%, rgba(249,115,22,0.0) 100%) !important;
  border-right: 1px solid rgba(0,0,0,.03);
  backdrop-filter: blur(10px);
}}
[data-testid="stSidebar"] .sidebar-content, [data-testid="stSidebar"] * {{ color: var(--hb-text) !important; }}
.hb-side-title {{ display:flex; align-items:center; gap:.5rem; margin:.25rem 0 1rem 0; font-weight:800; }}
.hb-dot {{ width:10px; height:10px; border-radius:999px; background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2)); box-shadow:0 0 10px rgba(249,115,22,.55);}}

/* Radio da navegação com laranja */
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label {{
  position:relative; display:flex; align-items:center; gap:.6rem;
  padding:.55rem .75rem; border-radius:14px; border:1px solid transparent;
  background: rgba(255,255,255,.03); transition:all .15s ease; margin:.15rem 0; cursor:pointer;
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]{{opacity:0; position:absolute; left:-9999px;}}
[data-testid="stSidebar"] .stRadio > div[role="radiogroup"] > label:hover{{ background: rgba(249,115,22,.12); border-color: rgba(249,115,22,.22); }}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div{{
  color:#0b0e14!important;
  background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2));
  border:0!important; box-shadow:0 6px 26px rgba(249,115,22,.28);
  font-weight:800; border-radius:14px; padding:.55rem .75rem;
}}
[data-testid="stSidebar"] .stRadio input[type="radio"]:checked + div::before{{
  content:""; width:10px; height:10px; border-radius:999px; background:#0b0e14;
  box-shadow:0 0 0 3px rgba(0,0,0,.15); margin-right:.1rem;
}}

.card{{ background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
        border:1px solid var(--hb-border); border-radius:18px; padding:16px; margin-bottom:14px;
        box-shadow:0 6px 28px rgba(0,0,0,.10), inset 0 1px 0 rgba(255,255,255,.03); }}
.section-title{{ background: linear-gradient(90deg, var(--hb-accent), var(--hb-accent2)); color:#111; font-weight:800; text-align:center; padding:.6rem .8rem; border-radius:12px; margin:0 0 12px 0; }}
.stTextInput input, .stTextArea textarea, .stNumberInput input, .stDateInput input{{ color:var(--hb-text)!important; background:transparent!important; border:1px solid var(--hb-border)!important; border-radius:12px!important; }}
.stButton>button, .stDownloadButton>button {{ background: linear-gradient(180deg, var(--hb-accent), var(--hb-accent2)); color:#111!important; font-weight:800; border:0; border-radius:12px; padding:.55rem 1rem; }}
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

def _render_header():
    st.markdown("<div class='hb-topbar'></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='card' style='padding:.8rem 1rem;'><b>🏗️ {SYSTEM_NAME}</b></div>", unsafe_allow_html=True)

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

# se não logou
if not s["logged_in"]:
    _auth_login_ui()
    flash_render()
    st.stop()

if s.get("must_change", False):
    _force_change_password_ui(s["username"])
    flash_render()
    st.stop()

# Header
_render_header()
nome_login = s.get("username") or load_user_prefs().get("last_user") or "—"
st.markdown(
    f"<div class='card'>👋 Olá, <b>{nome_login}</b></div>",
    unsafe_allow_html=True
)

# top toolbar
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

# ===== DB (SQLite) =====
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
    cliente = Column(String)
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
    numero = Column(String, nullable=False, unique=True)
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
    box.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"), ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("TOPPADDING",(0,0),(-1,-1),0), ("BOTTOMPADDING",(0,0),(-1,-1),0),
                             ("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),0)]))
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

def gerar_pdf_os(os_row, obra_row, itens: list[dict], show_prices: bool, logo_bytes: bytes | None, sig_lab_bytes: bytes | None = None) -> bytes:
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

    # assinatura: cliente e laboratorista
    # agora o digital (imagem) VAI DENTRO do campo do laboratorista
    if sig_lab_bytes:
        sig_img = Image(io.BytesIO(sig_lab_bytes))
        sig_img.drawHeight = 18 * mm
        sig_img.drawWidth = 55 * mm
        ass_tbl = Table(
            [["", "_______________________________", "", "_______________________________", ""],
             ["", "Assinatura Cliente", "", sig_img, ""]],
            colWidths=[10*mm, 70*mm, 15*mm, 70*mm, 10*mm]
        )
    else:
        ass_tbl = Table(
            [["", "_______________________________", "", "_______________________________", ""],
             ["", "Assinatura Cliente", "", "Assinatura Laboratorista", ""]],
            colWidths=[10*mm, 70*mm, 15*mm, 70*mm, 10*mm]
        )

    ass_tbl.setStyle(TableStyle([
        ("ALIGN",(1,0),(1,0),"CENTER"),
        ("ALIGN",(3,0),(3,0),"CENTER"),
        ("ALIGN",(1,1),(1,1),"CENTER"),
        ("ALIGN",(3,1),(3,1),"CENTER"),
        ("TOPPADDING",(0,1),(-1,1),2),
        ("BOTTOMPADDING",(0,0),(-1,-1),0),
        ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story.append(ass_tbl)

    doc.build(story, onFirstPage=lambda c,d:_on_page(c,d,""), onLaterPages=lambda c,d:_on_page(c,d,""))
    return buf.getvalue()

# ========= daqui pra frente são as páginas (cliente, obra, serviço, emitir OS, etc.) =========
def page_clientes():
    ...
def page_clientes():
    st.markdown('<div class="section-title">Cadastro de Clientes</div>', unsafe_allow_html=True)
    # ... (mesmo conteúdo da sua versão anterior — não removi nada)
    # por causa do tamanho, mantenho o bloco igual ao que você já colou antes
    # === COLE AQUI todo o bloco original de page_clientes() do seu código anterior ===
    # (é o mesmo que você já tinha, não precisa mudar nada)

def page_obras():
    # ... COLE o bloco de obras do seu código anterior (o grande), sem tirar nada

def page_servicos():
    # ... COLE o bloco de serviços do seu código anterior

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
    # ... COLE aqui seu bloco completo de emitir OS (o grande) da versão anterior
    # não esqueça: ele não usa o PDF direto, então não precisa mudar nada aqui

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
        st.write(f"**OS:** {os_row.numero}")
        st.write(f"**Data:** {os_row.data_emissao.strftime('%d/%m/%Y')}")
        st.write(f"**Status:** {os_row.status}")
        st.write(f"**Obra:** {obra_row.nome}")
        st.write(f"**Endereço:** {obra_row.endereco}")
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

    # PDF com assinatura digital se existir em sessão
    sig_bytes = st.session_state.get("sig_lab_bytes")
    pdf_interno = gerar_pdf_os(os_row, obra_row, itens, show_prices=True, logo_bytes=None, sig_lab_bytes=sig_bytes)
    pdf_cliente = gerar_pdf_os(os_row, obra_row, itens, show_prices=False, logo_bytes=None, sig_lab_bytes=sig_bytes)
    b1, b2 = st.columns(2)
    with b1:
        st.download_button("Baixar PDF (interno — com preços)", data=pdf_interno, file_name=f"{os_row.numero}_interno.pdf", mime="application/pdf", key="dl_pdf_interno")
    with b2:
        st.download_button("Baixar PDF (cliente — sem preços)", data=pdf_cliente, file_name=f"{os_row.numero}_cliente.pdf", mime="application/pdf", key="dl_pdf_cliente")
    st.markdown("</div>", unsafe_allow_html=True)

def make_os_excel_per_obras() -> bytes:
    """Gera ZIP com 1 planilha por obra; se não tiver openpyxl/xlsxwriter, gera CSV."""
    import importlib
    xlsx_ok = False
    for mod in ("openpyxl", "xlsxwriter"):
        if importlib.util.find_spec(mod) is not None:
            xlsx_ok = True
            break
    out_zip = io.BytesIO()
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        with SessionLocal() as sess:
            obras = sess.query(Obra).order_by(Obra.nome.asc()).all()
            for obra in obras:
                os_rows = (sess.query(OS).filter(OS.obra_id == obra.id)
                           .order_by(OS.data_emissao.asc(), OS.numero.asc()).all())
                if not os_rows:
                    continue
                rows = []
                for osr in os_rows:
                    for it in osr.itens:
                        sv = it.servico
                        preco = it.preco_unit if it.preco_unit is not None else (sv.preco_unit or 0.0)
                        rows.append({
                            "OS": osr.numero,
                            "Data": osr.data_emissao.strftime("%d/%m/%Y"),
                            "Status": osr.status,
                            "Serviço": sv.descricao,
                            "Código": sv.codigo,
                            "Un": sv.unidade,
                            "Qtd Prevista": it.quantidade_prevista or 0.0,
                            "Preço Unit": preco,
                            "Subtotal": preco * (it.quantidade_prevista or 0.0),
                        })
                df = pd.DataFrame(rows)
                safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", obra.nome)[:40] or f"obra_{obra.id}"
                if xlsx_ok:
                    buff = io.BytesIO()
                    with pd.ExcelWriter(buff, engine="openpyxl", datetime_format="DD/MM/YYYY") as writer:
                        df.to_excel(writer, index=False, sheet_name="OS")
                    zf.writestr(f"{safe_name}.xlsx", buff.getvalue())
                else:
                    csv_bytes = df.to_csv(index=False, sep=";").encode("utf-8")
                    zf.writestr(f"{safe_name}.csv", csv_bytes)
    out_zip.seek(0)
    return out_zip.getvalue()

def page_medicao():
    # ... COLE aqui o bloco da medição mensal do seu código anterior (sem tirar nada)

def page_relatorios():
    # ... COLE aqui o bloco de relatórios do seu código anterior (sem tirar nada)

def page_export():
    st.markdown('<div class="section-title">Exportações</div>', unsafe_allow_html=True)
    with st.expander("Backup (DB + anexos)", expanded=False):
        if st.button("Gerar backup ZIP", key="btn_backup_zip"):
            p = make_full_backup()
            with p.open("rb") as f:
                st.download_button("Baixar backup", data=f.read(), file_name=p.name, mime="application/zip", key="dl_backup_zip")

    with st.expander("Backup de OS por obra (planilhas)", expanded=True):
        if st.button("Gerar ZIP de planilhas por obra", key="btn_backup_xls"):
            zbytes = make_os_excel_per_obras()
            st.download_button("Baixar ZIP de planilhas", data=zbytes, file_name="os_por_obra.zip", mime="application/zip", key="dl_backup_xls")

# ===== Sidebar / navegação =====
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

# upload da assinatura digital na sidebar
st.sidebar.markdown("#### Assinatura digital (laboratorista)")
sig_file = st.sidebar.file_uploader("PNG ou JPG", type=["png", "jpg", "jpeg"], key="sig_lab_upload")
if sig_file is not None:
    s["sig_lab_bytes"] = sig_file.read()
    st.sidebar.success("Assinatura carregada! Será usada nos PDFs.")
elif s.get("sig_lab_bytes"):
    st.sidebar.info("Assinatura já carregada na sessão.")

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
if __name__ == "__main__":
    main_router()
